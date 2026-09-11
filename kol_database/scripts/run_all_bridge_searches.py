"""Batch-run find_intro_bridges.py across every Operator that has an
x_handle. Meant to be re-run periodically (e.g. from the recurring
research cron) as new operators get identified -- each call is cheap to
skip if nothing changed (find_intro_bridges.py itself just re-verifies and
overwrites that operator's bridge rows, so re-running is safe and just
refreshes can_dm/bridge status rather than accumulating duplicates).

After running, dumps the current state to data/intro_bridges_snapshot.json
so scripts/apply_intro_bridges_snapshot.py can push it to the deployed
Railway volume (a plain redeploy does not carry local data changes across
-- see project memory on this).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.db import get_session  # noqa: E402
from backend.models import IntroBridge, Operator  # noqa: E402
from find_intro_bridges import find_bridges_for_operator  # noqa: E402

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "intro_bridges_snapshot.json"


def main():
    session = get_session()
    operators = session.query(Operator).filter(Operator.x_handle.isnot(None)).all()
    targets = [(o.company_id, o.id, o.x_handle) for o in operators]
    session.close()

    print(f"running bridge search for {len(targets)} operators...")
    for company_id, operator_id, x_handle in targets:
        try:
            result = find_bridges_for_operator(company_id, operator_id, x_handle)
            print(f"  {company_id:25s} @{x_handle:20s} can_dm={result['can_dm']}  bridges={len(result['bridges'])}")
        except Exception as exc:  # noqa: BLE001 -- one bad handle should not kill the whole batch
            print(f"  {company_id:25s} @{x_handle:20s} ERROR: {exc}")
        time.sleep(0.3)

    # Refresh the deployable snapshot from current DB state.
    session = get_session()
    snapshot = {"operators": [], "bridges": []}
    for o in session.query(Operator).filter(Operator.can_dm.isnot(None)).all():
        snapshot["operators"].append(
            {
                "id": o.id,
                "company_id": o.company_id,
                "x_handle": o.x_handle,
                "can_dm": o.can_dm,
                "can_dm_checked_at": o.can_dm_checked_at.isoformat() if o.can_dm_checked_at else None,
            }
        )
    for b in session.query(IntroBridge).all():
        snapshot["bridges"].append(
            {
                "company_id": b.company_id,
                "operator_id": b.operator_id,
                "bridge_handle": b.bridge_handle,
                "bridge_name": b.bridge_name,
                "bridge_bio": b.bridge_bio,
                "bridge_followers_count": b.bridge_followers_count,
                "mango_side_handle": b.mango_side_handle,
                "mutual_with_mango_side": b.mutual_with_mango_side,
                "mutual_with_target": b.mutual_with_target,
                "confidence_note": b.confidence_note,
            }
        )
    session.close()
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))
    print(f"\nsnapshot refreshed: {len(snapshot['operators'])} operators, {len(snapshot['bridges'])} bridges")


if __name__ == "__main__":
    main()
