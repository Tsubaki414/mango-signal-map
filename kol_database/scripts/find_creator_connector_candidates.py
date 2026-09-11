"""Real-data scan (not a migration): finds creators who are plausible
business-connector candidates per section VII (2026-08-28) -- repeat
PAID sponsorship precedent with at least one company, AND Mango already
has a usable quote/contact for them. This is a screen, not a claim: it
narrows ~300+ creators down to the handful actually worth checking for
real interaction evidence with the sponsor's own operator (the same kind
of Rapid X check already done for Group A's bridges), which was not run
in this pass. Read-only; writes nothing to the DB.

Usage: python3 scripts/find_creator_connector_candidates.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bd_compute import creator_connector_candidacy  # noqa: E402
from backend.db import get_session  # noqa: E402
from backend.models import Creator  # noqa: E402


def main() -> None:
    session = get_session()
    try:
        creators = session.query(Creator).all()
        candidates = []
        for c in creators:
            result = creator_connector_candidacy(c)
            if result["is_candidate"]:
                candidates.append((c, result))

        print(f"scanned {len(creators)} creators, found {len(candidates)} connector candidates (same-company confirmed-paid repeat + quoted)\n")
        candidates.sort(key=lambda pair: -sum(r["confirmed_paid_count"] for r in pair[1]["repeat_paid_relationships"]))
        for c, result in candidates:
            counts = "，".join(f"{r['company_name']}×{r['confirmed_paid_count']}" for r in result["repeat_paid_relationships"])
            print(f"- {c.display_name} (@{c.primary_handle or '无记录账号'}) -- 已复核重复付费合作：{counts}")

        # A separate, real, and different signal: creators paid by 2+
        # DISTINCT target companies -- not "repeat with one company" (the
        # connector-candidacy screen above), but "serially sponsored by
        # this exact market" -- a high-value creator-supply signal
        # regardless of connector potential. Found by inspection this
        # would otherwise be invisible: dansmarttutorials has 4 distinct
        # paid placements (Creatify, Gamma, PixVerse, Replit), one each,
        # so it scores zero on the same-company-repeat screen above.
        print("\n--- 跨公司已复核付费合作（不同信号：广泛的 AI 赞助供给资产，非 connector 候选） ---\n")
        cross_company = []
        for c in creators:
            companies = {
                s.company_id
                for s in c.sponsorships
                if s.review_status == "confirmed" and s.disclosure_type == "paid_sponsorship"
            }
            if len(companies) >= 2:
                cross_company.append((c, companies))
        cross_company.sort(key=lambda pair: -len(pair[1]))
        for c, companies in cross_company:
            company_names = sorted({s.company.name for s in c.sponsorships if s.company_id in companies and s.company})
            print(f"- {c.display_name} (@{c.primary_handle or '无记录账号'}) -- {len(companies)} 家不同公司已复核付费赞助：{'、'.join(company_names)}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
