"""Apply the locally-verified data/intro_bridges_snapshot.json (Operator
can_dm status + verified IntroBridge rows) to whatever DB this runs
against. Idempotent, and matches operators by (company_id, x_handle) --
NOT raw id -- since auto-increment ids can drift between the local DB the
snapshot was taken from and the live DB (e.g. if new operators were
inserted in a different order). Bridges are matched by (operator_id,
normalized bridge_handle, mango_side_handle) using the *resolved* live
operator id. The Mango-side root is provenance: the same connector found
from Solomon and Mango is two atomic rows, not a duplicate. This is what gets
scripts/find_intro_bridges.py's expensive, already-verified API results
onto the live Railway volume -- a plain redeploy does not touch existing
data, see project memory. Must run after apply_gap_operators.py, which
creates the operator rows this script attaches to. Existing operator DM
facts are updated only when the baked observation is strictly newer; bridge
rows are insert-only. A redeploy therefore cannot roll back newer production
or human-reviewed state.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import IntroBridge, Operator  # noqa: E402

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "intro_bridges_snapshot.json"


def _parse(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def apply_operator_dm_snapshot(operator: Operator, row: dict) -> bool:
    """Apply a DM observation only when its timestamp advances the record."""

    source_checked_at = _parse(row.get("can_dm_checked_at"))
    if source_checked_at is None:
        return False
    if operator.can_dm_checked_at is not None and source_checked_at <= operator.can_dm_checked_at:
        return False
    operator.can_dm = bool(row.get("can_dm"))
    operator.can_dm_checked_at = source_checked_at
    return True


def insert_missing_bridges(session, rows: list[dict], id_map: dict[int, Operator]) -> int:
    """Insert missing atomic root-provenance rows without changing existing."""

    operator_ids = {operator.id for operator in id_map.values() if operator.id is not None}
    existing_keys = {
        (
            bridge.operator_id,
            (bridge.bridge_handle or "").strip().casefold(),
            (bridge.mango_side_handle or "").strip().casefold(),
        )
        for bridge in session.query(IntroBridge).filter(IntroBridge.operator_id.in_(operator_ids)).all()
    } if operator_ids else set()
    inserted = 0
    for row in rows:
        operator = id_map.get(row["operator_id"])
        if operator is None:
            continue
        key = (
            operator.id,
            (row.get("bridge_handle") or "").strip().casefold(),
            (row.get("mango_side_handle") or "").strip().casefold(),
        )
        if key in existing_keys:
            continue
        bridge_fields = {key: value for key, value in row.items() if key != "operator_id"}
        session.add(IntroBridge(operator_id=operator.id, **bridge_fields))
        existing_keys.add(key)
        inserted += 1
    return inserted


def main():
    if not SNAPSHOT_PATH.exists():
        print("no snapshot file, skipping")
        return

    init_db()
    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    session = get_session()

    # snapshot operator id -> resolved live Operator, keyed by (company_id, x_handle)
    id_map: dict[int, Operator] = {}
    n_ops = 0
    for row in snapshot.get("operators", []):
        op = (
            session.query(Operator)
            .filter(Operator.company_id == row["company_id"], Operator.x_handle == row["x_handle"])
            .one_or_none()
        )
        if op is None:
            continue  # operator doesn't exist here yet (apply_gap_operators.py should run first)
        changed = apply_operator_dm_snapshot(op, row)
        id_map[row["id"]] = op
        n_ops += int(changed)

    n_bridges = insert_missing_bridges(session, snapshot.get("bridges", []), id_map)

    session.commit()
    print(f"advanced can_dm evidence on {n_ops} operators, inserted {n_bridges} missing bridge rows")


if __name__ == "__main__":
    main()
