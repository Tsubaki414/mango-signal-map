"""Apply data/gap_operator_candidates.json (new operator records for
companies that previously had zero contacts) to whatever DB this runs
against. Bootstrap-only: a company that already has any operator is never
modified, so a redeploy cannot re-add a candidate a human replaced or removed.
When a truly empty company has multiple baked candidates (GAIB), the whole
group is inserted together. Must run before apply_intro_bridges_snapshot.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import Company, Operator  # noqa: E402

CANDIDATES_PATH = Path(__file__).resolve().parent.parent / "data" / "gap_operator_candidates.json"

NAME_TO_COMPANY_ID = {
    "Notion": "company:notion",
    "Pika": "company:pika",
    "MyShell": "company:myshell",
    "GAIB": "company:gaib",
    "Hyperbolic": "company:hyperbolic",
    "Heurist": "company:heurist",
}


def apply_gap_operator_rows(session, rows: list[dict]) -> int:
    """Insert baked candidates only for companies empty at transaction start."""

    existing_company_ids = {
        company_id
        for (company_id,) in session.query(Operator.company_id).distinct().all()
    }
    inserted_keys: set[tuple[str, str]] = set()
    inserted = 0
    for row in rows:
        company_id = NAME_TO_COMPANY_ID.get(row["company_name"])
        if company_id is None or session.get(Company, company_id) is None:
            continue
        if company_id in existing_company_ids:
            continue
        key = (company_id, row["name"])
        if key in inserted_keys:
            continue
        session.add(
            Operator(
                company_id=company_id,
                name=row["name"],
                role=row["role"],
                x_handle=row["x_handle"],
                identity_confirmed=False,
                identity_status="candidate_needs_x_verification",
                budget_authority_confirmed=False,
                evidence_urls=row["source_url"],
            )
        )
        inserted_keys.add(key)
        inserted += 1
    return inserted


def main():
    if not CANDIDATES_PATH.exists():
        print("no gap_operator_candidates.json, skipping")
        return

    init_db()
    rows = json.loads(CANDIDATES_PATH.read_text())
    session = get_session()

    n = apply_gap_operator_rows(session, rows)

    session.commit()
    print(f"created {n} new operator(s)")


if __name__ == "__main__":
    main()
