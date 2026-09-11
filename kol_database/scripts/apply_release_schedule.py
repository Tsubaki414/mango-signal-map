"""Apply the versioned 2026-08-29 Solomon release work schedule.

NULL-only and idempotent: any date set by a human before or after this script
is never overwritten. Dates represent workload planning, not evidence
freshness and not proof that outreach occurred. See backend.release_schedule.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session  # noqa: E402
from backend.models import ActionItem  # noqa: E402
from backend.release_schedule import RELEASE_DEFAULT_DUE_DATES, TODAY_INTERNAL_CHECKS  # noqa: E402


def apply_release_dates(actions: list[ActionItem]) -> tuple[int, list[str]]:
    """Mutate NULL due dates in-memory; return (changed, missing ids)."""
    by_company = {action.company_id: action for action in actions}
    changed = 0
    missing: list[str] = []
    for company_id, due_date in RELEASE_DEFAULT_DUE_DATES.items():
        action = by_company.get(company_id)
        if action is None:
            missing.append(company_id)
            continue
        if action.due_date is None or not action.due_date.strip():
            action.due_date = due_date
            changed += 1
    return changed, missing


def main() -> None:
    session = get_session()
    try:
        action_rows = session.query(ActionItem).all()
        actions = {action.company_id: action for action in action_rows}
        changed, missing = apply_release_dates(action_rows)
        if missing:
            raise RuntimeError("release schedule references missing action items: " + ", ".join(missing))
        session.commit()
        today = sum(
            actions[company_id].due_date == RELEASE_DEFAULT_DUE_DATES[company_id]
            for company_id in TODAY_INTERNAL_CHECKS
        )
        print(
            f"[apply_release_schedule] assigned {changed} NULL due dates; "
            f"{today} release-day internal checks; preserved every existing date"
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
