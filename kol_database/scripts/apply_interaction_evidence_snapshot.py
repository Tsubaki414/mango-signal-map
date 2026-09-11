"""Apply the locally-checked data/interaction_evidence_snapshot.json (real,
per-tweet auditable X interaction evidence -- see
scripts/check_bridge_interaction_evidence.py) onto whatever DB this runs
against. Idempotent, matches IntroBridge rows by (company_id,
operator.x_handle, bridge_handle) -- NOT raw id, since auto-increment ids
can drift between the local DB the snapshot was taken from and the live
DB (same lesson as apply_intro_bridges_snapshot.py). Snapshot checks are
inserted only when their exact directional query is missing. Existing checks
and tweet evidence are never deleted or rewritten, so a redeploy cannot erase
newer production/human evidence. The original collection time comes from the
paired bridge-interaction snapshot; startup time is never presented as an
evidence timestamp.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import InteractionCheck, InteractionEvidence, IntroBridge, Operator  # noqa: E402

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "interaction_evidence_snapshot.json"
CHECKED_AT_SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "bridge_interaction_snapshot.json"


def _parse(iso: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(iso) if iso else None


def _check_identity(from_handle: str, to_handle: str, query_string: str) -> tuple[str, str, str]:
    return (
        (from_handle or "").strip().casefold(),
        (to_handle or "").strip().casefold(),
        (query_string or "").strip(),
    )


def _checked_at_by_bridge(rows: list[dict]) -> dict[tuple[str, str, str], dt.datetime]:
    result: dict[tuple[str, str, str], dt.datetime] = {}
    for row in rows:
        checked_at = _parse(row.get("interaction_checked_at"))
        if checked_at is None:
            continue
        result[(row["company_id"], row["target_handle"], row["bridge_handle"])] = checked_at
    return result


def apply_interaction_snapshot(
    session,
    snapshot: dict,
    checked_at_by_bridge: dict[tuple[str, str, str], dt.datetime],
) -> tuple[int, int, int]:
    """Insert missing checks; return (checks, bridges, skipped_no_timestamp)."""

    by_bridge: dict[tuple[str, str, str], list[dict]] = {}
    for row in snapshot.get("checks", []):
        key = (row["company_id"], row["target_handle"], row["bridge_handle"])
        by_bridge.setdefault(key, []).append(row)

    inserted_checks = 0
    touched_bridges = 0
    skipped_no_timestamp = 0
    for (company_id, target_handle, bridge_handle), rows in by_bridge.items():
        operator = (
            session.query(Operator)
            .filter(Operator.company_id == company_id, Operator.x_handle == target_handle)
            .one_or_none()
        )
        if operator is None:
            continue
        bridge = (
            session.query(IntroBridge)
            .filter(IntroBridge.operator_id == operator.id, IntroBridge.bridge_handle == bridge_handle)
            .one_or_none()
        )
        if bridge is None:
            continue

        checked_at = checked_at_by_bridge.get((company_id, target_handle, bridge_handle))
        if checked_at is None:
            skipped_no_timestamp += len(rows)
            continue
        existing = {
            _check_identity(check.from_handle, check.to_handle, check.query_string)
            for check in bridge.interaction_checks
        }
        inserted_for_bridge = 0
        for row in rows:
            identity = _check_identity(row["from_handle"], row["to_handle"], row["query_string"])
            if identity in existing:
                continue
            check = InteractionCheck(
                bridge_id=bridge.id,
                from_handle=row["from_handle"],
                to_handle=row["to_handle"],
                query_string=row["query_string"],
                checked_at=checked_at,
                coverage_note=row["coverage_note"],
            )
            for evidence in row.get("evidence", []):
                check.evidence.append(
                    InteractionEvidence(
                        tweet_id=evidence["tweet_id"],
                        tweet_url=evidence["tweet_url"],
                        posted_at=_parse(evidence["posted_at"]),
                        author_handle=evidence["author_handle"],
                        recipient_handle=evidence["recipient_handle"],
                        context_text=evidence["context_text"],
                        interaction_type=evidence["interaction_type"],
                        is_auditable=evidence["is_auditable"],
                    )
                )
            session.add(check)
            existing.add(identity)
            inserted_checks += 1
            inserted_for_bridge += 1
        touched_bridges += int(inserted_for_bridge > 0)
    return inserted_checks, touched_bridges, skipped_no_timestamp


def main():
    if not SNAPSHOT_PATH.exists():
        print("no interaction evidence snapshot file, skipping")
        return

    init_db()
    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    checked_snapshot = (
        json.loads(CHECKED_AT_SNAPSHOT_PATH.read_text())
        if CHECKED_AT_SNAPSHOT_PATH.exists()
        else {"rows": []}
    )
    session = get_session()
    try:
        n_checks, n_bridges, skipped = apply_interaction_snapshot(
            session,
            snapshot,
            _checked_at_by_bridge(checked_snapshot.get("rows", [])),
        )
        session.commit()
        print(
            f"inserted {n_checks} missing interaction checks across {n_bridges} bridges; "
            f"skipped {skipped} rows without an auditable collection timestamp"
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
