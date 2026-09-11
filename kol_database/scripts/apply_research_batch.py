"""Apply the three agent-researched JSON batches (new company candidates,
operator candidates, sponsorship-evidence candidates) into the live DB.

This is a one-time, human-reviewed application step, not an automated
pipeline: the JSON files are written by research agents (WebSearch/WebFetch,
not the specialized Codex multi-gate methodology used for the original
migration), so everything lands in a state a human can still audit and
correct -- new operators are never marked identity_confirmed, new evidence
is never pre-confirmed, and every row keeps its source URL.

Safe to re-run: matches by (company_id, source_url) / (platform, handle)
so re-running after fixing a JSON file does not duplicate rows.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import (  # noqa: E402
    Company,
    CompanySource,
    Creator,
    Operator,
    SocialAccount,
    SponsorshipEvidence,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

PLATFORM_NORMALIZE = {"youtube": "YouTube", "x": "X", "twitter": "X", "instagram": "Instagram", "tiktok": "TikTok"}


def _slug(name: str) -> str:
    return "company:" + re.sub(r"[^a-z0-9]", "", name.lower())


def _find_company(session, name: str) -> Company | None:
    name_norm = re.sub(r"[^a-z0-9]", "", name.lower())
    for c in session.query(Company).all():
        if re.sub(r"[^a-z0-9]", "", c.name.lower()) == name_norm:
            return c
    return None


def _resolve_creator(session, platform: str, handle: str | None, display_name: str) -> Creator | None:
    platform = PLATFORM_NORMALIZE.get((platform or "").strip().lower(), platform)
    if not handle:
        return None
    account = (
        session.query(SocialAccount)
        .filter(SocialAccount.platform == platform, SocialAccount.handle.ilike(handle))
        .one_or_none()
    )
    if account:
        return account.creator
    creator = Creator(display_name=display_name or handle, creator_class="Unknown", creator_class_source="unset")
    session.add(creator)
    session.flush()
    session.add(SocialAccount(creator_id=creator.id, platform=platform, handle=handle))
    return creator


def _spend_classification(row: dict) -> tuple[str, str, str]:
    """Derive (spend_evidence_level, spend_mechanism_level, budget_evidence)
    from what was actually found. An atomic paid_sponsorship observation is
    real confirmed cash spend (L3); a disclosed affiliate/ambassador program
    page is formal-program evidence (L2) even with no atomic creator example
    yet -- these are exactly the same two tiers already used for the
    original 77 companies (see Photoroom/Tripo/Retell vs. PIN AI/Hyperbolic)."""
    has_paid = any(e.get("disclosure_type") == "paid_sponsorship" for e in row.get("evidence", []))
    program = row.get("program_evidence")
    if has_paid:
        level, mechanism = "L3", "active_creator_or_partner_budget"
    elif row.get("evidence") or program:
        level, mechanism = "L2", "formal_paid_program"
    else:
        level, mechanism = "legacy_unmapped", "legacy_budget_score_preserved"

    parts = []
    for e in row.get("evidence", []):
        if e.get("disclosure_type") == "paid_sponsorship":
            parts.append(f"{e.get('creator_or_source')}: {e.get('evidence_text', '')}")
    if program:
        parts.append(f"{program.get('source_url')}: {program.get('description', '')}")
    return level, mechanism, " | ".join(parts)


def apply_new_companies() -> dict:
    path = DATA_DIR / "new_company_candidates.json"
    if not path.exists():
        return {"skipped": "no file"}
    rows = json.loads(path.read_text())
    session = get_session()
    stats = {"companies_created": 0, "companies_skipped_dupe": 0, "evidence_created": 0, "creators_created": 0, "creators_matched": 0}
    for row in rows:
        existing = _find_company(session, row["name"])
        if existing:
            stats["companies_skipped_dupe"] += 1
            company = existing
        else:
            company_id = _slug(row["name"])
            spend_level, spend_mechanism, budget_evidence = _spend_classification(row)
            company = Company(
                company_id=company_id,
                name=row["name"],
                category=row.get("category"),
                geography=row.get("geography"),
                x_handle=(row.get("x_handle") or "").lstrip("@") or None,
                website=row.get("website"),
                stage="agent_discovered",
                spend_evidence_level=spend_level,
                spend_mechanism_level=spend_mechanism,
                budget_evidence=budget_evidence or None,
                internal_notes=f"[research sprint] {row.get('confidence_note', '')}",
            )
            session.add(company)
            session.flush()
            stats["companies_created"] += 1

            program = row.get("program_evidence")
            if program and program.get("source_url"):
                if not session.query(CompanySource).filter(CompanySource.company_id == company.company_id, CompanySource.source_url == program["source_url"]).count():
                    session.add(CompanySource(company_id=company.company_id, source_url=program["source_url"]))

        for ev in row.get("evidence", []):
            url = ev.get("content_url")
            if url and not session.query(CompanySource).filter(CompanySource.company_id == company.company_id, CompanySource.source_url == url).count():
                session.add(CompanySource(company_id=company.company_id, source_url=url))

            existing_ev = None
            if url:
                existing_ev = session.query(SponsorshipEvidence).filter(SponsorshipEvidence.company_id == company.company_id, SponsorshipEvidence.content_url == url).one_or_none()
            if existing_ev:
                continue

            handle = None
            m = re.search(r"@(\w+)", ev.get("creator_or_source", ""))
            platform_guess = "YouTube" if "youtube" in (url or "").lower() else ("X" if ("x.com" in (url or "") or "twitter.com" in (url or "")) else "Other")
            creator = None
            if m and platform_guess in ("YouTube", "X"):
                handle = m.group(1)
                before = session.query(Creator).count()
                creator = _resolve_creator(session, platform_guess, handle, ev.get("creator_or_source", ""))
                after = session.query(Creator).count()
                stats["creators_created" if after > before else "creators_matched"] += 1

            session.add(
                SponsorshipEvidence(
                    company_id=company.company_id,
                    creator_id=creator.id if creator else None,
                    creator_name_raw=ev.get("creator_or_source", "")[:255],
                    creator_handle_raw=handle,
                    platform=platform_guess,
                    content_url=url or "",
                    content_title=ev.get("content_title_or_description"),
                    published_at=ev.get("published_at"),
                    disclosure_type=ev.get("disclosure_type", "unverified"),
                    evidence_text=ev.get("evidence_text"),
                    confidence=0.6,
                    review_status="unreviewed",
                )
            )
            stats["evidence_created"] += 1

    session.commit()
    session.close()
    return stats


def apply_operators() -> dict:
    path = DATA_DIR / "operator_candidates.json"
    if not path.exists():
        return {"skipped": "no file"}
    rows = json.loads(path.read_text())
    session = get_session()
    stats = {"operators_created": 0, "unmatched_companies": []}
    for row in rows:
        company = _find_company(session, row["company_name"])
        if not company:
            stats["unmatched_companies"].append(row["company_name"])
            continue
        already = session.query(Operator).filter(Operator.company_id == company.company_id, Operator.name == row["name"]).one_or_none()
        if already:
            continue
        session.add(
            Operator(
                company_id=company.company_id,
                name=row["name"],
                role=row.get("role"),
                x_handle=(row.get("x_handle") or "").lstrip("@") or None,
                identity_confirmed=False,  # research-agent-sourced, not Rapid-X-verified -- stays a candidate
                identity_status="candidate_needs_x_verification",
                budget_authority_confirmed=False,
                evidence_urls=row.get("source_url"),
            )
        )
        stats["operators_created"] += 1
    session.commit()
    session.close()
    return stats


def apply_sponsorship_evidence() -> dict:
    path = DATA_DIR / "sponsorship_evidence_candidates.json"
    if not path.exists():
        return {"skipped": "no file"}
    rows = json.loads(path.read_text())
    session = get_session()
    stats = {"evidence_created": 0, "evidence_skipped_dupe": 0, "creators_created": 0, "creators_matched": 0, "unmatched_companies": []}
    for row in rows:
        company = _find_company(session, row["company_name"])
        if not company:
            stats["unmatched_companies"].append(row["company_name"])
            continue
        url = row.get("content_url", "")
        if url and session.query(SponsorshipEvidence).filter(SponsorshipEvidence.company_id == company.company_id, SponsorshipEvidence.content_url == url).count():
            stats["evidence_skipped_dupe"] += 1
            continue

        platform = PLATFORM_NORMALIZE.get((row.get("platform") or "").strip().lower(), row.get("platform"))
        creator = None
        if row.get("creator_handle") and platform in ("YouTube", "X", "Instagram", "TikTok"):
            before = session.query(Creator).count()
            creator = _resolve_creator(session, platform, row["creator_handle"], row.get("creator_name", ""))
            after = session.query(Creator).count()
            stats["creators_created" if after > before else "creators_matched"] += 1

        session.add(
            SponsorshipEvidence(
                company_id=company.company_id,
                creator_id=creator.id if creator else None,
                creator_name_raw=row.get("creator_name", "")[:255],
                creator_handle_raw=row.get("creator_handle"),
                platform=platform or "Other",
                content_url=url,
                content_title=row.get("content_title"),
                published_at=row.get("published_at"),
                disclosure_type=row.get("disclosure_type", "unverified"),
                evidence_text=row.get("evidence_text"),
                confidence=0.6,
                review_status="unreviewed",
            )
        )
        stats["evidence_created"] += 1

    session.commit()
    session.close()
    return stats


def main():
    init_db()
    print("=== New companies ===")
    print(json.dumps(apply_new_companies(), indent=2))
    print("\n=== Operators ===")
    print(json.dumps(apply_operators(), indent=2))
    print("\n=== Sponsorship evidence (existing companies) ===")
    print(json.dumps(apply_sponsorship_evidence(), indent=2))


if __name__ == "__main__":
    main()
