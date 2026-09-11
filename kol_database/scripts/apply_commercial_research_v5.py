"""Apply the company-first commercial research layer and build handoff artifacts.

This script is intentionally independent of X reachability.  It scores and
selects companies from spend mechanisms, repeated campaigns, timing, ICP fit,
and operator/route executability.  Existing network, connector, and outreach
records are preserved and remain available as optional contact-route context.

Safe to re-run: all new records upsert on stable company/evidence/route keys.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import (  # noqa: E402
    Company,
    CompanyCommercialEvidence,
    CompanyContactRoute,
    CompanyLonglistAssessment,
    CompanyResearchDossier,
    CompanySource,
    Creator,
    Operator,
    RateCard,
    SocialAccount,
    SponsorshipEvidence,
)

APP_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_DATA_ROOT = APP_ROOT.parent / "data"
BUNDLED_DATA_ROOT = APP_ROOT / "data"
DATA_ROOT = (
    Path(os.environ["COMMERCIAL_RESEARCH_DATA_ROOT"])
    if os.environ.get("COMMERCIAL_RESEARCH_DATA_ROOT")
    else WORKSPACE_DATA_ROOT
    if (WORKSPACE_DATA_ROOT / "pilot_v5").is_dir()
    else BUNDLED_DATA_ROOT
)
DATA_DIR = DATA_ROOT / "pilot_v5"
V4_DIR = DATA_ROOT / "pilot_v4"
SNAPSHOT_DATE = dt.datetime(2026, 8, 29, 12, 0, 0)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _source(session, company_id: str, url: str | None) -> None:
    if not url:
        return
    exists = (
        session.query(CompanySource)
        .filter(CompanySource.company_id == company_id, CompanySource.source_url == url)
        .one_or_none()
    )
    if not exists:
        session.add(CompanySource(company_id=company_id, source_url=url))


def _resolve_creator(session, name: str, platform: str, handle: str | None) -> Creator:
    account = None
    if handle:
        account = (
            session.query(SocialAccount)
            .filter(SocialAccount.platform == platform, SocialAccount.handle.ilike(handle))
            .first()
        )
    if account:
        return account.creator
    creator = session.query(Creator).filter(Creator.display_name == name).first()
    if not creator:
        creator = Creator(
            display_name=name,
            primary_handle=handle,
            creator_class="Unknown",
            creator_class_source="unset",
            internal_notes="Discovered through v5 creator-first sponsorship research; classification pending.",
            source_files="data/pilot_v5/expanded_longlist_seed.json",
        )
        session.add(creator)
        session.flush()
    if handle and not any(
        (a.platform or "").casefold() == platform.casefold() and (a.handle or "").casefold() == handle.casefold()
        for a in creator.social_accounts
    ):
        session.add(SocialAccount(creator_id=creator.id, platform=platform, handle=handle))
    return creator


def _upsert_expanded_companies(session, rows: list[dict]) -> dict:
    stats = {"created": 0, "updated": 0, "sponsorships_created": 0}
    for row in rows:
        company = session.get(Company, row["company_id"])
        if not company:
            company = Company(
                company_id=row["company_id"],
                name=row["name"],
                category=row.get("category"),
                geography=row.get("geography"),
                stage="commercial_discovery_v5",
                spend_evidence_level=row.get("spend_evidence_level"),
                spend_mechanism_level=row.get("spend_mechanism_level"),
                why_now=row.get("why_now"),
                budget_evidence=row.get("budget_evidence"),
                website=row.get("website"),
                internal_notes="Added through company-first/creator-first commercial discovery on 2026-08-29.",
                last_verified_at=SNAPSHOT_DATE,
            )
            session.add(company)
            stats["created"] += 1
        else:
            stats["updated"] += 1
            company.why_now = company.why_now or row.get("why_now")
            company.budget_evidence = company.budget_evidence or row.get("budget_evidence")
            company.website = company.website or row.get("website")
        for evidence in row.get("sources", []):
            _source(session, company.company_id, evidence.get("url"))
        session.flush()
        for sponsorship in row.get("sponsorships", []):
            existing = (
                session.query(SponsorshipEvidence)
                .filter(SponsorshipEvidence.source_id == sponsorship["source_id"])
                .one_or_none()
            )
            if existing:
                continue
            creator = _resolve_creator(
                session,
                sponsorship["creator_name"],
                sponsorship["platform"],
                sponsorship.get("creator_handle"),
            )
            session.add(
                SponsorshipEvidence(
                    source_id=sponsorship["source_id"],
                    company_id=company.company_id,
                    creator_id=creator.id,
                    creator_name_raw=sponsorship["creator_name"],
                    creator_handle_raw=sponsorship.get("creator_handle"),
                    platform=sponsorship["platform"],
                    content_url=sponsorship["content_url"],
                    content_title=sponsorship.get("content_title"),
                    published_at=sponsorship.get("published_at"),
                    disclosure_type=sponsorship["disclosure_type"],
                    evidence_text=sponsorship.get("evidence_text"),
                    confidence=sponsorship.get("confidence"),
                    review_status="unreviewed",
                    created_at=SNAPSHOT_DATE,
                )
            )
            stats["sponsorships_created"] += 1
    session.flush()
    return stats


def _icp_type(company: Company) -> str:
    text = f"{company.category or ''} {company.name}".casefold()
    if company.stage == "new_crypto_ai_v4" or any(token in text for token in ("blockchain", "on-chain", "defi", "tokenized")):
        return "AI × crypto"
    if any(token in text for token in ("developer", "coding", "api", "infrastructure", "gpu", "mlops", "agent orchestration", "terminal")):
        return "AI developer tools/API"
    if any(token in text for token in ("productivity", "automation", "meeting", "website", "presentation", "workspace", "marketing", "saas")):
        return "AI productivity/SaaS"
    if "enterprise" in text and not company.sponsorships:
        return "Enterprise-only AI"
    return "Consumer AI products"


def _commercial_score(company: Company, icp_type: str) -> float:
    """Commercial-only score.  No relationship, fame, funding, or followers."""
    spend = {
        "active_creator_or_partner_budget": 35,
        "formal_paid_program": 25,
        "affiliate_or_referral_only": 16,
        "distribution_need_unverified_budget": 8,
        "funding_or_token_value_only": 0,
        "legacy_budget_score_preserved": 0,
    }.get(company.spend_mechanism_level or "", 8 if company.spend_evidence_level == "L1" else 0)
    live = [s for s in company.sponsorships if s.review_status != "rejected"]
    paid = [s for s in live if s.disclosure_type == "paid_sponsorship"]
    sponsor_points = min(25, len(paid) * 5)
    repeat_points = 8 if len(paid) >= 2 else 3 if live else 0
    timing_points = 12 if company.why_now else 5 if company.last_verified_at else 0
    operator_points = 8 if any(o.name for o in company.operators) else 0
    fit_points = 3 if icp_type == "Enterprise-only AI" else 8
    return float(min(100, spend + sponsor_points + repeat_points + timing_points + operator_points + fit_points))


def _discovery_paths(company: Company) -> list[str]:
    paths = []
    if any(s.review_status != "rejected" for s in company.sponsorships):
        paths.extend(["campaign_first", "creator_first"])
    if company.why_now or company.stage == "commercial_discovery_v5":
        paths.append("growth_signal_first")
    if company.stage in {"new_longtail_ai_v4", "new_crypto_ai_v4", "agent_discovered", "commercial_discovery_v5"}:
        paths.append("ecosystem_first")
    return list(dict.fromkeys(paths or ["legacy_revalidation_queue"]))


def _upsert_operator(session, company: Company, row: dict) -> Operator:
    data = row["operator"]
    operator = (
        session.query(Operator)
        .filter(Operator.company_id == company.company_id, Operator.name == data["name"])
        .first()
    )
    if not operator:
        operator = Operator(
            company_id=company.company_id,
            source_id=f"v5:operator:{company.company_id.split(':', 1)[-1]}",
            name=data["name"],
            role=data["role"],
            identity_confirmed=False,
            identity_status="public_role_found_x_exact_profile_not_revalidated",
            budget_authority_confirmed=False,
            evidence_urls=data["evidence_url"],
            last_verified_at=SNAPSHOT_DATE,
        )
        session.add(operator)
        session.flush()
    else:
        operator.role = operator.role or data["role"]
        urls = set(filter(None, (operator.evidence_urls or "").splitlines()))
        urls.add(data["evidence_url"])
        operator.evidence_urls = "\n".join(sorted(urls))
        operator.last_verified_at = operator.last_verified_at or SNAPSHOT_DATE
    _source(session, company.company_id, data["evidence_url"])
    return operator


def _confidence_from_float(value: float | None) -> str:
    if value is None:
        return "medium"
    return "high" if value >= 0.9 else "medium" if value >= 0.6 else "low"


def _campaign_payload(company: Company, program_record: dict | None, gtm_record: dict | None) -> tuple[list[dict], list[dict]]:
    campaigns = []
    creators = []
    seen_creators = set()
    for sponsorship in sorted(company.sponsorships, key=lambda item: item.published_at or ""):
        if sponsorship.review_status == "rejected":
            continue
        campaigns.append(
            {
                "name": sponsorship.content_title or f"{sponsorship.creator_name_raw} content",
                "date": sponsorship.published_at,
                "platform": sponsorship.platform,
                "format": sponsorship.disclosure_type,
                "creator": sponsorship.creator_name_raw,
                "source_url": sponsorship.content_url,
                "fact_status": "confirmed" if sponsorship.review_status == "confirmed" else "observed_unreviewed",
            }
        )
        creator_key = sponsorship.creator_id or sponsorship.creator_name_raw.casefold()
        if creator_key in seen_creators:
            continue
        seen_creators.add(creator_key)
        rate_rows = sponsorship.creator.rate_cards if sponsorship.creator else []
        creators.append(
            {
                "name": sponsorship.creator_name_raw,
                "platform": sponsorship.platform,
                "relationship_type": sponsorship.disclosure_type,
                "mango_relationship": "rate_on_file" if rate_rows else "unknown",
                "rate": (
                    {
                        "currency": rate_rows[0].quote_currency,
                        "amount": rate_rows[0].quote_amount,
                        "amount_min": rate_rows[0].quote_amount_min,
                        "amount_max": rate_rows[0].quote_amount_max,
                        "deliverable": rate_rows[0].deliverable,
                    }
                    if rate_rows
                    else None
                ),
                "commercial_relevance": "Has direct historical campaign evidence; this does not imply willingness to introduce.",
            }
        )
    if program_record:
        for name in program_record.get("programs_or_campaigns", []):
            campaigns.append(
                {
                    "name": name,
                    "date": program_record.get("last_verified_at"),
                    "platform": "multi-channel",
                    "format": "formal_program",
                    "creator": None,
                    "source_url": (program_record.get("sources") or [{}])[0].get("url"),
                    "fact_status": "confirmed",
                }
            )
    if gtm_record:
        for program in gtm_record.get("campaigns_programs", []):
            campaigns.append(
                {
                    "name": program.get("name"),
                    "date": gtm_record.get("last_verified_at"),
                    "platform": "multi-channel",
                    "format": program.get("spend_type"),
                    "creator": None,
                    "source_url": (gtm_record.get("evidence") or [{}])[0].get("url"),
                    "fact_status": "confirmed",
                }
            )
    return campaigns, creators


def _upsert_atomic_evidence(session, company: Company, row: dict, program_record: dict | None, gtm_record: dict | None) -> None:
    records = list(row.get("evidence", []))
    for source in (program_record or {}).get("sources", []):
        records.append(
            {
                "evidence_id": source.get("evidence_id") or f"v4:{company.company_id}:{len(records)}",
                "claim": source.get("claim"),
                "source_url": source.get("url"),
                "evidence_date": source.get("date"),
                "date_basis": source.get("date_basis") or "published_or_accessed",
                "evidence_type": source.get("evidence_type") or "official",
                "confidence": source.get("confidence") or "medium",
                "fact_status": source.get("fact_status") or "confirmed",
            }
        )
    for source in (gtm_record or {}).get("evidence", []):
        records.append(
            {
                "evidence_id": f"gtm:{company.company_id}:{len(records)}",
                "claim": source.get("claim"),
                "source_url": source.get("url"),
                "evidence_date": source.get("date"),
                "date_basis": source.get("date_basis"),
                "evidence_type": source.get("source_type") or "official",
                "confidence": source.get("confidence") or "medium",
                "fact_status": source.get("fact_status") or "confirmed",
            }
        )
    for sponsorship in company.sponsorships:
        if sponsorship.review_status == "rejected":
            continue
        records.append(
            {
                "evidence_id": f"sponsorship:{sponsorship.source_id or sponsorship.id}",
                "claim": sponsorship.evidence_text or f"{sponsorship.creator_name_raw} content observed as {sponsorship.disclosure_type}.",
                "source_url": sponsorship.content_url,
                "evidence_date": sponsorship.published_at,
                "date_basis": "published",
                "evidence_type": f"creator_{sponsorship.disclosure_type}",
                "confidence": _confidence_from_float(sponsorship.confidence),
                "fact_status": "confirmed" if sponsorship.review_status == "confirmed" else "observed_unreviewed",
            }
        )
    for record in records:
        if not record.get("evidence_id") or not record.get("source_url"):
            continue
        evidence = (
            session.query(CompanyCommercialEvidence)
            .filter(CompanyCommercialEvidence.evidence_id == record["evidence_id"])
            .one_or_none()
        )
        if not evidence:
            evidence = CompanyCommercialEvidence(evidence_id=record["evidence_id"], company_id=company.company_id)
            session.add(evidence)
        evidence.claim = record.get("claim") or "Claim not recorded"
        evidence.source_url = record["source_url"]
        evidence.evidence_date = record.get("evidence_date")
        evidence.date_basis = record.get("date_basis")
        evidence.evidence_type = record.get("evidence_type") or "public_web"
        evidence.confidence = record.get("confidence") or "medium"
        evidence.fact_status = record.get("fact_status") or "unverified"
        evidence.last_verified_at = SNAPSHOT_DATE
        _source(session, company.company_id, record["source_url"])


def _upsert_priority_dossiers(session, seeds: list[dict], longtail: list[dict], gtm_cases: list[dict]) -> None:
    program_by_name = {row["company"].casefold(): row for row in longtail}
    gtm_by_name = {row["company"].casefold(): row for row in gtm_cases}
    for row in seeds:
        company = session.get(Company, row["company_id"])
        if not company:
            raise RuntimeError(f"Priority dossier company not found: {row['company_id']}")
        program_record = program_by_name.get(company.name.casefold())
        gtm_record = gtm_by_name.get(company.name.casefold())
        operator = _upsert_operator(session, company, row)
        campaigns, creators = _campaign_payload(company, program_record, gtm_record)
        dossier = session.get(CompanyResearchDossier, company.company_id)
        if not dossier:
            dossier = CompanyResearchDossier(company_id=company.company_id)
            session.add(dossier)
        for field in (
            "priority_rank",
            "commercial_priority_score",
            "icp_type",
            "business_model",
            "target_customer",
            "why_company",
            "why_now",
            "commercialization_evidence",
            "budget_spend_signals",
            "gtm_summary",
            "operator_role_relevance",
            "public_contact_method",
            "mango_service_fit",
            "mango_offer",
            "opening_angle",
            "confidence",
            "fact_status",
        ):
            setattr(dossier, field, row.get(field))
        dossier.marketing_channels_json = _json(row.get("marketing_channels", []))
        dossier.historical_campaigns_json = _json(campaigns)
        dossier.creators_media_communities_json = _json(creators)
        dossier.repeated_activity = bool(row.get("repeated_activity") or sum(1 for s in company.sponsorships if s.disclosure_type == "paid_sponsorship" and s.review_status != "rejected") >= 2)
        dossier.operator_id = operator.id
        dossier.key_unknowns_json = _json(row.get("key_unknowns", []))
        dossier.last_verified_at = SNAPSHOT_DATE
        _upsert_atomic_evidence(session, company, row, program_record, gtm_record)
        for route_row in row.get("routes", []):
            route = (
                session.query(CompanyContactRoute)
                .filter(
                    CompanyContactRoute.company_id == company.company_id,
                    CompanyContactRoute.route_key == route_row["route_key"],
                )
                .one_or_none()
            )
            if not route:
                route = CompanyContactRoute(company_id=company.company_id, route_key=route_row["route_key"])
                session.add(route)
            for field in (
                "route_type",
                "label",
                "route_detail",
                "why_this_route",
                "required_first_step",
                "supporting_evidence",
                "evidence_url",
                "confidence",
            ):
                setattr(route, field, route_row.get(field))
            route.is_best = bool(route_row.get("is_best"))
            route.fallback_order = route_row.get("fallback_order")
            route.last_verified_at = SNAPSHOT_DATE


def _upsert_longlist(session, expanded_rows: list[dict], priority_rows: list[dict]) -> None:
    expanded_by_id = {row["company_id"]: row for row in expanded_rows}
    priority_by_id = {row["company_id"]: row for row in priority_rows}
    for company in session.query(Company).all():
        expanded = expanded_by_id.get(company.company_id)
        priority = priority_by_id.get(company.company_id)
        icp_type = priority.get("icp_type") if priority else expanded.get("icp_type") if expanded else _icp_type(company)
        score = priority.get("commercial_priority_score") if priority else expanded.get("commercial_priority_score") if expanded else _commercial_score(company, icp_type)
        if priority:
            disposition = "priority"
            keep_reason = priority["why_company"]
            downgrade_reason = None
            confidence = priority["confidence"]
            fact_status = priority["fact_status"]
        elif expanded:
            disposition = expanded["disposition"]
            keep_reason = expanded.get("keep_reason")
            downgrade_reason = expanded.get("downgrade_reason")
            confidence = expanded["confidence"]
            fact_status = expanded["fact_status"]
        else:
            has_paid = any(s.disclosure_type == "paid_sponsorship" and s.review_status != "rejected" for s in company.sponsorships)
            reusable = company.spend_evidence_level in {"L2", "L3"} or company.spend_mechanism_level in {
                "formal_paid_program",
                "active_creator_or_partner_budget",
            }
            if icp_type == "Enterprise-only AI" and not has_paid and not reusable:
                disposition = "deprioritize"
                downgrade_reason = "Enterprise-led with no verified creator, community, DevRel, or regional-distribution mechanism."
            elif reusable or has_paid:
                disposition = "keep_longlist"
                downgrade_reason = None
            else:
                disposition = "watch"
                downgrade_reason = "Current evidence does not yet prove a creator/community budget or repeat distribution need."
            keep_reason = (
                "Verified paid observation or structured creator/partner mechanism is on file."
                if reusable or has_paid
                else "Retained for commercial revalidation; legacy relationship score is not used."
            )
            confidence = "medium" if reusable or has_paid else "low"
            fact_status = "mixed"
        assessment = session.get(CompanyLonglistAssessment, company.company_id)
        if not assessment:
            assessment = CompanyLonglistAssessment(company_id=company.company_id)
            session.add(assessment)
        assessment.disposition = disposition
        assessment.icp_type = icp_type
        assessment.commercial_priority_score = float(score)
        assessment.discovery_paths_json = _json(expanded.get("discovery_paths") if expanded else _discovery_paths(company))
        assessment.keep_reason = keep_reason
        assessment.downgrade_reason = downgrade_reason
        assessment.confidence = confidence
        assessment.fact_status = fact_status
        assessment.last_verified_at = SNAPSHOT_DATE


def _dossier_json(dossier: CompanyResearchDossier) -> dict:
    company = dossier.company
    routes = sorted(company.contact_routes, key=lambda route: (0 if route.is_best else 1, route.fallback_order or 99))
    return {
        "priority_rank": dossier.priority_rank,
        "commercial_priority_score": dossier.commercial_priority_score,
        "company_id": company.company_id,
        "company": company.name,
        "product_category": company.category,
        "geography": company.geography,
        "icp_type": dossier.icp_type,
        "business_model": dossier.business_model,
        "target_customer": dossier.target_customer,
        "why_company": dossier.why_company,
        "why_now": dossier.why_now,
        "commercialization_evidence": dossier.commercialization_evidence,
        "budget_spend_signals": dossier.budget_spend_signals,
        "gtm_summary": dossier.gtm_summary,
        "marketing_channels": json.loads(dossier.marketing_channels_json),
        "historical_campaigns": json.loads(dossier.historical_campaigns_json),
        "creators_media_communities": json.loads(dossier.creators_media_communities_json),
        "repeated_activity": dossier.repeated_activity,
        "operator": {
            "name": dossier.operator.name if dossier.operator else None,
            "role": dossier.operator.role if dossier.operator else None,
            "identity_status": dossier.operator.identity_status if dossier.operator else None,
        },
        "operator_role_relevance": dossier.operator_role_relevance,
        "public_contact_method": dossier.public_contact_method,
        "best_contact_route": next((route.label for route in routes if route.is_best), None),
        "fallback_routes": [route.label for route in routes if not route.is_best],
        "routes": [
            {
                "route_type": route.route_type,
                "label": route.label,
                "why_this_route": route.why_this_route,
                "required_first_step": route.required_first_step,
                "supporting_evidence": route.supporting_evidence,
                "evidence_url": route.evidence_url,
                "confidence": route.confidence,
                "is_best": route.is_best,
                "fallback_order": route.fallback_order,
            }
            for route in routes
        ],
        "mango_service_fit": dossier.mango_service_fit,
        "mango_offer": dossier.mango_offer,
        "opening_angle": dossier.opening_angle,
        "confidence": dossier.confidence,
        "fact_status": dossier.fact_status,
        "key_unknowns": json.loads(dossier.key_unknowns_json),
        "evidence": [
            {
                "evidence_id": evidence.evidence_id,
                "claim": evidence.claim,
                "url": evidence.source_url,
                "date": evidence.evidence_date,
                "date_basis": evidence.date_basis,
                "evidence_type": evidence.evidence_type,
                "confidence": evidence.confidence,
                "fact_status": evidence.fact_status,
                "last_verified_at": evidence.last_verified_at.isoformat(),
            }
            for evidence in sorted(company.commercial_evidence, key=lambda evidence: evidence.evidence_id)
        ],
        "last_verified_at": dossier.last_verified_at.isoformat(),
    }


def _creator_sponsor_graph(session) -> list[dict]:
    creators = (
        session.query(Creator)
        .filter(Creator.sponsorships.any())
        .all()
    )
    result = []
    for creator in creators:
        live = [s for s in creator.sponsorships if s.review_status != "rejected"]
        if not live:
            continue
        sponsors = {}
        for sponsorship in live:
            entry = sponsors.setdefault(
                sponsorship.company_id,
                {"company_id": sponsorship.company_id, "company": sponsorship.company.name if sponsorship.company else sponsorship.company_id, "observations": []},
            )
            entry["observations"].append(
                {
                    "type": sponsorship.disclosure_type,
                    "platform": sponsorship.platform,
                    "published_at": sponsorship.published_at,
                    "url": sponsorship.content_url,
                    "fact_status": "confirmed" if sponsorship.review_status == "confirmed" else "observed_unreviewed",
                }
            )
        rates = [
            {
                "platform": rate.platform,
                "deliverable": rate.deliverable,
                "currency": rate.quote_currency,
                "amount": rate.quote_amount,
                "amount_min": rate.quote_amount_min,
                "amount_max": rate.quote_amount_max,
                "is_confident": rate.is_confident,
            }
            for rate in creator.rate_cards
        ]
        result.append(
            {
                "creator_id": creator.id,
                "creator": creator.display_name,
                "creator_class": creator.creator_class,
                "sponsor_count": len(sponsors),
                "observation_count": len(live),
                "sponsors": list(sponsors.values()),
                "repeated_cooperation": any(len(sponsor["observations"]) >= 2 for sponsor in sponsors.values()),
                "mango_relationship": "rate_on_file" if rates else "unknown",
                "rates": rates,
                "possible_commercial_relevance": "Can provide campaign-process context where historical evidence exists; no willingness to introduce is inferred.",
            }
        )
    return sorted(result, key=lambda row: (-row["sponsor_count"], -row["observation_count"], row["creator"].casefold()))


def main() -> None:
    init_db()
    priority = _read(DATA_DIR / "priority_dossiers_seed.json")
    expanded = _read(DATA_DIR / "expanded_longlist_seed.json")
    longtail = _read(V4_DIR / "longtail_ai_candidates.json")
    gtm_payload = _read(V4_DIR / "gtm_case_studies_v4.json")
    gtm_cases = gtm_payload["case_studies"]
    session = get_session()
    try:
        expanded_stats = _upsert_expanded_companies(session, expanded)
        _upsert_priority_dossiers(session, priority, longtail, gtm_cases)
        _upsert_longlist(session, expanded, priority)
        session.commit()

        dossiers = [
            _dossier_json(dossier)
            for dossier in session.query(CompanyResearchDossier).order_by(CompanyResearchDossier.priority_rank).all()
        ]
        longlist_rows = [
            {
                "company_id": company.company_id,
                "company": company.name,
                "category": company.category,
                "geography": company.geography,
                "icp_type": company.longlist_assessment.icp_type,
                "disposition": company.longlist_assessment.disposition,
                "commercial_priority_score": company.longlist_assessment.commercial_priority_score,
                "discovery_paths": json.loads(company.longlist_assessment.discovery_paths_json),
                "keep_reason": company.longlist_assessment.keep_reason,
                "downgrade_reason": company.longlist_assessment.downgrade_reason,
                "spend_evidence_level": company.spend_evidence_level,
                "spend_mechanism_level": company.spend_mechanism_level,
                "paid_sponsorship_observations": sum(
                    1 for sponsorship in company.sponsorships if sponsorship.disclosure_type == "paid_sponsorship" and sponsorship.review_status != "rejected"
                ),
                "operator_count": sum(1 for operator in company.operators if operator.name),
                "confidence": company.longlist_assessment.confidence,
                "fact_status": company.longlist_assessment.fact_status,
                "last_verified_at": company.longlist_assessment.last_verified_at.isoformat(),
            }
            for company in session.query(Company).order_by(Company.name).all()
        ]
        creator_graph = _creator_sponsor_graph(session)
        _write(DATA_DIR / "priority_company_dossiers_v5.json", dossiers)
        _write(DATA_DIR / "ai_company_longlist_v5.json", longlist_rows)
        _write(DATA_DIR / "creator_sponsor_graph_v5.json", creator_graph)
        _write(
            DATA_DIR / "gtm_case_studies_v5.json",
            {
                "as_of": "2026-08-29",
                "case_count": len(gtm_cases),
                "case_studies": gtm_cases,
                "note": "Preserved mature-company GTM cases; relationship graph is not used as a selection or scoring input.",
            },
        )
        print(
            json.dumps(
                {
                    "companies": session.query(Company).count(),
                    "longlist_assessments": session.query(CompanyLonglistAssessment).count(),
                    "priority_dossiers": len(dossiers),
                    "priority_with_operator": sum(1 for dossier in dossiers if dossier["operator"]["name"]),
                    "priority_with_three_routes": sum(1 for dossier in dossiers if len(dossier["routes"]) >= 3),
                    "commercial_evidence": session.query(CompanyCommercialEvidence).count(),
                    "creator_sponsor_nodes": len(creator_graph),
                    "gtm_cases": len(gtm_cases),
                    "expanded": expanded_stats,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
