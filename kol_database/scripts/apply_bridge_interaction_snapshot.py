"""Apply the locally-checked data/bridge_interaction_snapshot.json (real X
interaction evidence for a subset of bridge candidates -- see
scripts/check_bridge_interaction_evidence.py) onto whatever DB this runs
against. Idempotent, and matches IntroBridge rows by (company_id,
operator.x_handle, bridge_handle) -- NOT raw id -- since auto-increment ids
can drift between the local DB the snapshot was taken from and the live
DB, same lesson as apply_intro_bridges_snapshot.py. A baked row updates the
deprecated flat summary only when its checked_at is strictly newer. Existing
undated content is preserved because the baked file cannot prove it is newer.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import Company, IntroBridge, Operator  # noqa: E402

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "bridge_interaction_snapshot.json"


def _parse(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def apply_bridge_interaction_row(bridge: IntroBridge, row: dict) -> bool:
    """Advance a legacy flat interaction summary without rolling it back."""

    source_checked_at = _parse(row.get("interaction_checked_at"))
    if source_checked_at is None:
        return False
    if bridge.interaction_checked_at is not None:
        if source_checked_at <= bridge.interaction_checked_at:
            return False
    elif any(
        value is not None
        for value in (
            bridge.interaction_count_recent,
            bridge.most_recent_interaction_at,
            bridge.interaction_sample_text,
        )
    ):
        # Content with no comparable timestamp may be human/newer. Preserve it.
        return False

    bridge.interaction_checked_at = source_checked_at
    bridge.interaction_count_recent = row.get("interaction_count_recent")
    bridge.most_recent_interaction_at = _parse(row.get("most_recent_interaction_at"))
    bridge.interaction_sample_text = row.get("interaction_sample_text")
    return True


def main():
    if not SNAPSHOT_PATH.exists():
        print("no bridge interaction snapshot file, skipping")
        return

    init_db()
    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    session = get_session()

    n_applied = 0
    for row in snapshot.get("rows", []):
        operator = (
            session.query(Operator)
            .filter(Operator.company_id == row["company_id"], Operator.x_handle == row["target_handle"])
            .one_or_none()
        )
        if operator is None:
            continue
        bridge = (
            session.query(IntroBridge)
            .filter(IntroBridge.operator_id == operator.id, IntroBridge.bridge_handle == row["bridge_handle"])
            .one_or_none()
        )
        if bridge is None:
            continue
        n_applied += int(apply_bridge_interaction_row(bridge, row))

    session.commit()
    print(f"advanced legacy interaction summaries on {n_applied} bridge rows")


if __name__ == "__main__":
    main()
