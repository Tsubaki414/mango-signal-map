"""Opportunities / Network / Campaign-builder API -- the BD-intelligence
half of the cockpit. Mounted into the same FastAPI app as the Creator
directory (app.py) so both share one process, one DB, one auth-free
internal-tool trust boundary. See bd_compute.py for the scoring logic and
bd_serializers.py for the JSON shapes.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import joinedload

from .bd_compute import creator_campaign_fit, opportunity_priority
from .bd_serializers import company_detail, company_summary, connector_brief_json, sponsorship_json
from .compute import quote_summary
from .db import get_session
from .models import (
    ActionItem,
    Company,
    ConnectorBrief,
    Creator,
    Operator,
    Shortlist,
    SponsorshipEvidence,
)

router = APIRouter(prefix="/api")


def _load_company(session, company_id: str) -> Company:
    company = (
        session.query(Company)
        .populate_existing()
        .options(
            joinedload(Company.aliases),
            joinedload(Company.sources),
            joinedload(Company.operators),
            joinedload(Company.intro_paths),
            joinedload(Company.action_items),
            joinedload(Company.sponsorships).joinedload(SponsorshipEvidence.creator),
            joinedload(Company.gtm_case),
        )
        .filter(Company.company_id == company_id)
        .one_or_none()
    )
    if company is None:
        raise HTTPException(404, f"Company {company_id!r} not found")
    return company


def _load_all_companies(session) -> list[Company]:
    return (
        session.query(Company)
        .options(
            joinedload(Company.operators),
            joinedload(Company.intro_paths),
            joinedload(Company.action_items),
            joinedload(Company.sponsorships),
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Opportunities
# ---------------------------------------------------------------------------


@router.get("/companies")
def list_companies(
    search: str | None = None,
    category: list[str] | None = Query(None),
    geography: list[str] | None = Query(None),
    priority: list[str] | None = Query(None),  # High | Medium | Low | Watchlist
    spend_evidence_level: list[str] | None = Query(None),
    has_sponsorship_evidence: bool | None = None,
    sort: str = "priority",
    order: str = "desc",
    page: int = 1,
    page_size: int = 50,
):
    session = get_session()
    try:
        companies = _load_all_companies(session)
        rows = [company_summary(c) for c in companies]

        if search:
            needle = search.strip().lower()
            rows = [r for r in rows if needle in r["name"].lower() or needle in (r["category"] or "").lower()]
        if category:
            rows = [r for r in rows if r["category"] in category]
        if geography:
            rows = [r for r in rows if r["geography"] in geography]
        if priority:
            rows = [r for r in rows if r["priority"] in priority]
        if spend_evidence_level:
            rows = [r for r in rows if r["spend_evidence_level"] in spend_evidence_level]
        if has_sponsorship_evidence:
            rows = [r for r in rows if r["sponsorship_count"] > 0]

        priority_rank = {"High": 3, "Medium": 2, "Watchlist": 1, "Low": 0}
        sort_keys = {
            "priority": lambda r: (priority_rank.get(r["priority"], -1), r["reachability_level"]),
            "reachability": lambda r: r["reachability_level"],
            "sponsorship_count": lambda r: r["sponsorship_count"],
            "name": lambda r: r["name"].lower(),
        }
        key_fn = sort_keys.get(sort, sort_keys["priority"])
        rows.sort(key=key_fn, reverse=(order == "desc"))

        total = len(rows)
        start = (page - 1) * page_size
        return {"total": total, "page": page, "page_size": page_size, "results": rows[start : start + page_size]}
    finally:
        session.close()


@router.get("/companies/meta/filters")
def company_filter_options():
    session = get_session()
    try:
        companies = session.query(Company).all()
        categories = sorted({c.category for c in companies if c.category})
        geographies = sorted({c.geography for c in companies if c.geography})
        priority_counts: dict[str, int] = {}
        for c in companies:
            label = opportunity_priority(c)["label"]
            priority_counts[label] = priority_counts.get(label, 0) + 1
        return {
            "categories": categories,
            "geographies": geographies,
            "spend_evidence_levels": sorted({c.spend_evidence_level for c in companies if c.spend_evidence_level}),
            "priority_counts": priority_counts,
            "total": len(companies),
        }
    finally:
        session.close()


@router.get("/companies/{company_id}")
def get_company(company_id: str):
    session = get_session()
    try:
        return company_detail(_load_company(session, company_id))
    finally:
        session.close()


@router.get("/companies/{company_id}/suggested-creators")
def suggested_creators(company_id: str, limit: int = 20):
    """Candidate creators for this company's campaign, from the existing
    quote library -- category/region fit, historical sponsorship of this
    company or similar ones, existing relationship. Every result carries
    its reasons; there is no single fit score."""
    session = get_session()
    try:
        company = _load_company(session, company_id)
        prior_creator_ids = {s.creator_id for s in company.sponsorships if s.creator_id}

        creators = (
            session.query(Creator)
            .options(
                joinedload(Creator.social_accounts),
                joinedload(Creator.rate_cards),
                joinedload(Creator.sponsorships),
            )
            .all()
        )

        results = []
        for creator in creators:
            quoted = quote_summary(creator)
            if quoted["confident_count"] == 0 and creator.id not in prior_creator_ids:
                continue  # no quote and no history with this company -- not campaign-ready
            fit = creator_campaign_fit(creator, company)
            account = creator.social_accounts[0] if creator.social_accounts else None
            results.append(
                {
                    "id": creator.id,
                    "display_name": creator.display_name,
                    "handle": account.handle if account else None,
                    "platform": account.platform if account else None,
                    "creator_class": creator.creator_class,
                    "prior_relationship": creator.id in prior_creator_ids,
                    "reasons": fit["reasons"],
                    "quote_min_usd": quoted["quote_min_usd"],
                    "quote_max_usd": quoted["quote_max_usd"],
                    "cpm_min": quoted["cpm_min"],
                }
            )

        results.sort(key=lambda r: (not r["prior_relationship"], r["quote_min_usd"] is None))
        return {"results": results[:limit]}
    finally:
        session.close()


class BuildCampaignRequest(BaseModel):
    name: str | None = None
    objective: str | None = None
    target_audience: str | None = None
    budget_usd: float | None = None
    region_pref: str | None = None
    language_pref: str | None = None
    platforms_pref: str | None = None
    timing: str | None = None


@router.post("/companies/{company_id}/campaigns")
def build_campaign(company_id: str, body: BuildCampaignRequest):
    """Company -> pre-populated Shortlist. Reuses the existing Shortlist
    model and all of its budget/export machinery unchanged; this just sets
    company_id and the campaign-context fields on creation."""
    session = get_session()
    try:
        company = _load_company(session, company_id)
        shortlist = Shortlist(
            name=body.name or f"{company.name} campaign",
            client_or_campaign=company.name,
            budget_usd=body.budget_usd,
            company_id=company.company_id,
            objective=body.objective,
            target_audience=body.target_audience,
            region_pref=body.region_pref,
            language_pref=body.language_pref,
            platforms_pref=body.platforms_pref,
            timing=body.timing,
        )
        session.add(shortlist)
        session.commit()
        return {"id": shortlist.id, "name": shortlist.name, "company_id": shortlist.company_id}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------


@router.get("/network/connectors")
def network_connectors():
    session = get_session()
    try:
        briefs = session.query(ConnectorBrief).order_by(ConnectorBrief.sort_order).all()
        companies_by_id = {c.company_id: c for c in session.query(Company).all()}
        return {"results": [connector_brief_json(b, companies_by_id) for b in briefs]}
    finally:
        session.close()


@router.get("/network/operators")
def network_operators(confirmed_only: bool = True):
    session = get_session()
    try:
        q = session.query(Operator).options(joinedload(Operator.company))
        if confirmed_only:
            q = q.filter(Operator.identity_confirmed.is_(True))
        operators = q.all()
        return {
            "results": [
                {
                    "id": o.id,
                    "name": o.name,
                    "role": o.role,
                    "x_handle": o.x_handle,
                    "company_id": o.company_id,
                    "company_name": o.company.name,
                    "budget_authority_confirmed": o.budget_authority_confirmed,
                }
                for o in operators
            ]
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Home / action map
# ---------------------------------------------------------------------------


@router.get("/home/summary")
def home_summary():
    session = get_session()
    try:
        companies = _load_all_companies(session)
        summaries = [company_summary(c) for c in companies]

        top_actions = (
            session.query(ActionItem)
            .options(joinedload(ActionItem.company))
            .order_by(ActionItem.execution_wave.asc())
            .limit(8)
            .all()
        )
        campaign_ready = [
            r for r in summaries if r["priority"] == "High" and r["paid_sponsorship_count"] > 0
        ][:8]
        recent_paid = (
            session.query(SponsorshipEvidence)
            .options(joinedload(SponsorshipEvidence.company), joinedload(SponsorshipEvidence.creator))
            .filter(SponsorshipEvidence.disclosure_type == "paid_sponsorship")
            .order_by(SponsorshipEvidence.published_at.desc())
            .limit(8)
            .all()
        )

        return {
            "top_priority_companies": sorted(summaries, key=lambda r: {"High": 3, "Medium": 2, "Watchlist": 1, "Low": 0}.get(r["priority"], -1), reverse=True)[:10],
            "top_actions": [
                {
                    "company_id": a.company_id,
                    "company_name": a.company.name,
                    "execution_wave": a.execution_wave,
                    "primary_next_action": a.primary_next_action,
                    "owner": a.owner,
                }
                for a in top_actions
            ],
            "campaign_ready": campaign_ready,
            "recent_paid_evidence": [sponsorship_json(s) for s in recent_paid],
            "totals": {
                "companies": len(companies),
                "confirmed_operators": sum(1 for c in companies for o in c.operators if o.identity_confirmed),
                "paid_sponsorships": sum(1 for c in companies for s in c.sponsorships if s.disclosure_type == "paid_sponsorship"),
            },
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Global search
# ---------------------------------------------------------------------------


@router.get("/search")
def global_search(q: str):
    needle = q.strip().lower()
    if len(needle) < 2:
        return {"companies": [], "operators": [], "creators": []}
    session = get_session()
    try:
        companies = (
            session.query(Company)
            .filter(Company.name.ilike(f"%{needle}%"))
            .limit(8)
            .all()
        )
        operators = (
            session.query(Operator)
            .options(joinedload(Operator.company))
            .filter(Operator.name.ilike(f"%{needle}%"))
            .limit(8)
            .all()
        )
        creators = (
            session.query(Creator)
            .filter(Creator.display_name.ilike(f"%{needle}%"))
            .limit(8)
            .all()
        )
        return {
            "companies": [{"company_id": c.company_id, "name": c.name} for c in companies],
            "operators": [{"id": o.id, "name": o.name, "company_id": o.company_id, "company_name": o.company.name} for o in operators],
            "creators": [{"id": c.id, "display_name": c.display_name} for c in creators],
        }
    finally:
        session.close()
