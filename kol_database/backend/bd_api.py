"""Opportunities / Network / Campaign-builder API -- the BD-intelligence
half of the cockpit. Mounted into the same FastAPI app as the Creator
directory (app.py) so both share one process, one DB, one auth-free
internal-tool trust boundary. See bd_compute.py for the scoring logic and
bd_serializers.py for the JSON shapes.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import joinedload, selectinload

from .campaign_matching import campaign_profile_for, profile_public_json, quote_scope_fit
from .bd_compute import (
    canonical_decision,
    canonical_sponsorship_type,
    deduplicate_bridge_stages,
    interaction_audit_summary,
    opportunity_priority,
    rank_suggested_creators,
    sponsor_side_intelligence_prospects,
    suggested_bridge_action,
)
from .bd_serializers import company_detail, company_summary, connector_brief_json, sponsorship_json
from .db import get_session
from .models import (
    ACTION_ITEM_STATUSES,
    ActionItem,
    Company,
    CompanyBuyerMapEntry,
    CompanyLonglistAssessment,
    CompanyResearchDossier,
    CompanySalesPacket,
    ConnectorBrief,
    Creator,
    CreatorExclusion,
    GtmCase,
    CONTACT_CHANNELS,
    IntroBridge,
    IntroPath,
    Operator,
    OUTREACH_STAGES,
    OutreachLog,
    Shortlist,
    SocialAccount,
    SolomonReview,
    SolomonReviewHistory,
    SponsorshipEvidence,
)

router = APIRouter(prefix="/api")

APIFY_COMPANY_INTELLIGENCE_PATH = Path(
    os.environ.get(
        "APIFY_COMPANY_INTELLIGENCE_PATH",
        Path(__file__).resolve().parent.parent / "data" / "apify_company_intelligence_v1.json",
    )
)


def _public_internal_person_copy(value: str | None) -> str:
    """Keep relationship evidence while removing a personal-name UI root."""

    text = str(value or "")
    text = re.sub(
        r"Solomon\s*\(@Solomon_Nahhh\)",
        "Mango 内部联系人",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"@Solomon_Nahhh", "Mango 内部联系人", text, flags=re.IGNORECASE)
    return re.sub(r"\bSolomon\b", "Mango 内部联系人", text, flags=re.IGNORECASE)


def _load_public_apify_intelligence(company_id: str) -> dict | None:
    """Load the deploy-safe Apify research layer for one company.

    The API deliberately reconstructs a small allow-listed response instead
    of returning the package wholesale. This prevents later collection code
    from accidentally exposing raw Actor payloads, cache paths or credentials
    through the public read-only site.
    """

    try:
        package = json.loads(APIFY_COMPANY_INTELLIGENCE_PATH.read_text(encoding="utf-8"))
        source = (package.get("companies") or {}).get(company_id)
    except (OSError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(source, dict):
        return None

    website_signals = []
    for row in source.get("website_signals") or []:
        if not isinstance(row, dict) or not row.get("source_url"):
            continue
        signal_types = sorted({
            str(signal.get("signal_type"))
            for signal in row.get("candidate_signals") or []
            if isinstance(signal, dict) and signal.get("signal_type")
        })
        website_signals.append({
            "source_title": row.get("source_title"),
            "source_url": row.get("source_url"),
            "source_validity": row.get("source_validity"),
            "signal_types": signal_types,
            "fact_status": "observed_unreviewed",
        })

    operators = []
    for row in source.get("operator_candidates") or []:
        if not isinstance(row, dict):
            continue
        operators.append({
            key: row.get(key)
            for key in (
                "name", "current_role", "headline", "location", "role_relevance",
                "operator_candidate_status", "public_profile_url", "fact_status",
                "review_status", "last_observed_at",
            )
        })

    public_contact_routes = []
    for row in source.get("public_contact_routes") or []:
        if not isinstance(row, dict) or not row.get("route_url"):
            continue
        public_contact_routes.append({
            key: row.get(key)
            for key in (
                "label", "route_type", "route_url", "evidence_url", "why_this_route",
                "required_first_step", "confidence", "fact_status",
            )
        })

    campaign_source = source.get("campaign") or {}
    cohorts = []
    for cohort in campaign_source.get("cohorts") or []:
        if not isinstance(cohort, dict):
            continue
        candidates = []
        for row in cohort.get("creator_candidates") or []:
            if not isinstance(row, dict):
                continue
            candidates.append({
                key: row.get(key)
                for key in (
                    "creator", "why_fit", "youtube_channel_url", "evidence_url",
                    "evidence_date", "commercial_type_candidate", "youtube_subscribers",
                    "observed_video_views", "existing_creator_id", "rate_on_file",
                    "region_evidence", "fact_status",
                )
            })
        cohorts.append({
            "cohort": cohort.get("cohort"),
            "objective": cohort.get("objective"),
            "creator_candidates": candidates,
            "pricing_status": cohort.get("pricing_status"),
        })

    return {
        "generated_at": package.get("generated_at"),
        "fact_status": source.get("fact_status"),
        "review_status": source.get("review_status"),
        "source_counts": source.get("source_counts") or {},
        "decision_ready_counts": source.get("decision_ready_counts") or {},
        "why_now_observed": source.get("why_now_observed") or [],
        "mango_offer_hypothesis": source.get("mango_offer_hypothesis"),
        "best_route_hypothesis": source.get("best_route_hypothesis"),
        "contact_gap": source.get("contact_gap"),
        "public_contact_routes": public_contact_routes,
        "website_signals": website_signals,
        "operator_candidates": operators,
        "campaign": {
            "campaign_thesis": campaign_source.get("campaign_thesis"),
            "company_specific_evidence": campaign_source.get("company_specific_evidence") or [],
            "cohorts": cohorts,
            "apac_gap": campaign_source.get("apac_gap") or {},
            "pricing_guardrail": campaign_source.get("pricing_guardrail"),
            "fact_status": campaign_source.get("fact_status"),
        },
    }


def _edge_direction_list(path: IntroPath) -> list[str]:
    try:
        value = json.loads(path.edge_directions or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _cross_company_connector_candidates(session) -> dict[str, list[dict]]:
    """Rank entities that touch multiple Priority-15 companies.

    This is connector *research*, not a claim that anyone will introduce
    Mango. Creator rows require commercial evidence at two or more targets;
    X rows require observed paths to two or more targets and keep their
    direction / human-intro boundary explicit.
    """

    priority_rows = session.query(CompanyResearchDossier.company_id).all()
    priority_ids = {row[0] for row in priority_rows}
    company_names = {
        row.company_id: row.name
        for row in session.query(Company).filter(Company.company_id.in_(priority_ids)).all()
    }

    commercial_types = {
        "paid_sponsorship", "affiliate", "ambassador_long_term_partner",
        "event_podcast_appearance",
    }
    creator_groups: dict[int, dict] = {}
    evidence_rows = (
        session.query(SponsorshipEvidence)
        .options(
            joinedload(SponsorshipEvidence.creator).selectinload(Creator.rate_cards),
            joinedload(SponsorshipEvidence.creator).selectinload(Creator.contacts),
            joinedload(SponsorshipEvidence.creator).selectinload(Creator.social_accounts),
        )
        .filter(
            SponsorshipEvidence.company_id.in_(priority_ids),
            SponsorshipEvidence.review_status != "rejected",
        )
        .all()
    )
    for evidence in evidence_rows:
        creator = evidence.creator
        disclosure = canonical_sponsorship_type(evidence.disclosure_type)
        if creator is None or disclosure not in commercial_types:
            continue
        group = creator_groups.setdefault(creator.id, {
            "connector_kind": "creator_commercial_bridge",
            "connector_id": f"creator:{creator.id}",
            "name": creator.display_name,
            "creator_class": creator.creator_class,
            "target_company_ids": set(),
            "target_companies": set(),
            "evidence_urls": set(),
            "commercial_types": set(),
            "review_statuses": set(),
            "observation_count": 0,
            "confirmed_observation_count": 0,
            "rate_on_file": bool(creator.rate_cards),
            "public_contact_on_file": bool(creator.contacts),
            "profile_url": next(
                (row.profile_url for row in creator.social_accounts if row.profile_url),
                None,
            ),
        })
        group["target_company_ids"].add(evidence.company_id)
        group["target_companies"].add(company_names.get(evidence.company_id, evidence.company_id))
        group["commercial_types"].add(disclosure)
        group["review_statuses"].add(evidence.review_status)
        if evidence.content_url:
            group["evidence_urls"].add(evidence.content_url)
        group["observation_count"] += 1
        group["confirmed_observation_count"] += int(evidence.review_status == "confirmed")

    creator_connectors = []
    for group in creator_groups.values():
        company_count = len(group["target_company_ids"])
        if company_count < 2:
            continue
        score = (
            company_count * 30
            + group["observation_count"] * 6
            + group["confirmed_observation_count"] * 8
            + int(group["rate_on_file"]) * 12
            + int(group["public_contact_on_file"]) * 6
        )
        creator_connectors.append({
            **group,
            "target_company_ids": sorted(group["target_company_ids"]),
            "target_companies": sorted(group["target_companies"]),
            "target_company_count": company_count,
            "evidence_urls": sorted(group["evidence_urls"])[:5],
            "commercial_types": sorted(group["commercial_types"]),
            "review_statuses": sorted(group["review_statuses"]),
            "rank_score": score,
            "relationship_truth": "只证明跨公司商业内容观察；不证明愿意引荐、当前可采购或与 Mango 有私人关系。",
            "commercial_value": "可一次询问多个目标公司的 brief、采购流程、付款方式和实际 operator。",
            "required_first_step": "先核对各条商业披露，再询问跨公司采购流程情报；不要直接要求引荐。",
        })
    creator_connectors.sort(key=lambda row: (-row["rank_score"], row["name"].casefold()))

    briefs = {
        (row.connector_x_handle or "").lstrip("@").casefold(): row
        for row in session.query(ConnectorBrief).all()
        if row.connector_x_handle
    }
    path_groups: dict[str, dict] = {}
    path_rows = (
        session.query(IntroPath)
        .filter(
            IntroPath.company_id.in_(priority_ids),
            IntroPath.graph_reachable.is_(True),
            IntroPath.connector_handle.is_not(None),
            IntroPath.connector_handle != "",
        )
        .all()
    )
    for path in path_rows:
        handle = (path.connector_handle or "").strip()
        key = handle.lstrip("@").casefold()
        if not key or key == "mangolabs_":
            continue
        directions = _edge_direction_list(path)
        group = path_groups.setdefault(key, {
            "connector_kind": "x_research_bridge",
            "connector_id": f"x:{key}",
            "name": (briefs.get(key).connector_name if briefs.get(key) else handle),
            "handle": handle if handle.startswith("@") else f"@{handle}",
            "target_company_ids": set(),
            "target_companies": set(),
            "source_artifacts": set(),
            "path_count": 0,
            "direction_complete_path_count": 0,
            "mutual_edge_path_count": 0,
            "primary_path_count": 0,
        })
        group["target_company_ids"].add(path.company_id)
        group["target_companies"].add(company_names.get(path.company_id, path.company_id))
        if path.source_artifact:
            group["source_artifacts"].add(path.source_artifact)
        group["path_count"] += 1
        group["direction_complete_path_count"] += int(
            bool(directions) and len(directions) >= int(path.hop_count or 0)
        )
        group["mutual_edge_path_count"] += int("mutual_follow" in directions)
        group["primary_path_count"] += int(bool(path.is_primary))

    x_connectors = []
    for key, group in path_groups.items():
        company_count = len(group["target_company_ids"])
        brief = briefs.get(key)
        if company_count < 2 or (brief is None and company_count < 4):
            continue
        score = (
            company_count * 20
            + group["mutual_edge_path_count"] * 5
            + group["direction_complete_path_count"] * 2
            + int(brief is not None) * 25
        )
        x_connectors.append({
            **group,
            "target_company_ids": sorted(group["target_company_ids"]),
            "target_companies": sorted(group["target_companies"]),
            "target_company_count": company_count,
            "source_artifacts": sorted(group["source_artifacts"]),
            "rank_score": score,
            "relationship_truth": "X 可达性研究线索；逐边方向保留，但不证明真实认识、回复意愿或引荐意愿。",
            "commercial_value": "一次关系核验可覆盖多家 Priority 15，但必须先确认认识的是哪个真实员工。",
            "required_first_step": (
                brief.best_current_action
                if brief else
                "询问与每家公司的真实联系人、关系性质、最近互动和是否适合做具体引荐。"
            ),
        })
    x_connectors.sort(key=lambda row: (-row["rank_score"], row["name"].casefold()))
    return {
        "creator_connectors": creator_connectors,
        "x_research_connectors": x_connectors,
    }


def _load_company(session, company_id: str) -> Company:
    company = (
        session.query(Company)
        .populate_existing()
        .options(
            selectinload(Company.aliases),
            selectinload(Company.sources),
            selectinload(Company.operators),
            selectinload(Company.intro_paths),
            selectinload(Company.action_items),
            selectinload(Company.outreach_logs),
            selectinload(Company.sponsorships).joinedload(SponsorshipEvidence.creator),
            joinedload(Company.gtm_case),
            joinedload(Company.research_dossier).joinedload(CompanyResearchDossier.operator),
            selectinload(Company.commercial_evidence),
            selectinload(Company.contact_routes),
            joinedload(Company.longlist_assessment),
            joinedload(Company.canonical_decision),
            joinedload(Company.asia_profile),
            selectinload(Company.asia_evidence),
            joinedload(Company.sales_packet),
            selectinload(Company.buyer_map_entries),
            joinedload(Company.solomon_review),
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
            selectinload(Company.operators),
            selectinload(Company.intro_paths),
            selectinload(Company.action_items),
            selectinload(Company.sponsorships),
            joinedload(Company.research_dossier).joinedload(CompanyResearchDossier.operator),
            selectinload(Company.contact_routes),
            joinedload(Company.longlist_assessment),
            joinedload(Company.canonical_decision),
            joinedload(Company.asia_profile),
            selectinload(Company.asia_evidence),
            joinedload(Company.sales_packet),
            selectinload(Company.buyer_map_entries),
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
    priority: list[str] | None = Query(None),  # High | Medium | Low (pure business value -- see opportunity_priority)
    spend_evidence_level: list[str] | None = Query(None),
    commercial_disposition: list[str] | None = Query(None),
    icp_type: list[str] | None = Query(None),
    opportunity_value: list[str] | None = Query(None),
    execution_readiness: list[str] | None = Query(None),
    decision_bucket: list[str] | None = Query(None),
    signal: list[str] | None = Query(None),
    has_sponsorship_evidence: bool | None = None,
    sort: str = "canonical_decision",
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
        if commercial_disposition:
            rows = [
                r
                for r in rows
                if (r.get("commercial_research") or {}).get("disposition") in commercial_disposition
            ]
        if icp_type:
            rows = [
                r
                for r in rows
                if (r.get("commercial_research") or {}).get("icp_type") in icp_type
            ]
        if opportunity_value:
            rows = [
                row for row in rows
                if row["canonical_decision"]["opportunity_value"] in opportunity_value
            ]
        if execution_readiness:
            rows = [
                row for row in rows
                if row["canonical_decision"]["execution_readiness"] in execution_readiness
            ]
        if decision_bucket:
            rows = [
                row for row in rows
                if row["canonical_decision"]["decision_bucket"] in decision_bucket
            ]
        if signal:
            rows = [
                row for row in rows
                if all(row["canonical_decision"]["flags"].get(flag, False) for flag in signal)
            ]
        if has_sponsorship_evidence:
            rows = [r for r in rows if r["sponsorship_count"] > 0]

        priority_rank = {"High": 3, "Medium": 2, "Low": 1}
        exec_rank = {
            "需要跟进": 6,
            "现在联系": 5,
            "等待回复": 4,
            "等待内部核实": 3,
            "本周准备": 2,
            "暂缓": 1,
            "放弃": 0,
        }
        sort_keys = {
            "canonical_decision": lambda r: (
                {"pursue_now": 4, "prepare": 3, "watch": 2, "archive": 1}.get(
                    r["canonical_decision"]["decision_bucket"], 0
                ),
                {"tier_a": 4, "tier_b": 3, "tier_c": 2, "archive": 1}.get(
                    r["canonical_decision"]["opportunity_value"], 0
                ),
                r["canonical_decision"]["internal_sort_score"],
            ),
            "priority": lambda r: (priority_rank.get(r["priority"], -1), r["reachability_level"]),
            "reachability": lambda r: r["reachability_level"],
            "execution_priority": lambda r: (exec_rank.get(r["execution_priority"], 0), r["execution_priority_score"]),
            "sponsorship_count": lambda r: r["sponsorship_count"],
            "commercial_priority": lambda r: (
                (r.get("commercial_research") or {}).get("commercial_priority_score") or -1,
                -((r.get("commercial_research") or {}).get("priority_rank") or 999),
            ),
            "name": lambda r: r["name"].lower(),
        }
        key_fn = sort_keys.get(sort, sort_keys["canonical_decision"])
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
        companies = _load_all_companies(session)
        # Built from the Chinese display value so these match what
        # company_summary() actually returns in the "category"/"geography"
        # fields -- the filter checkbox values and the filtered rows must
        # agree on which language they're comparing.
        categories = sorted({c.category_zh or c.category for c in companies if c.category_zh or c.category})
        geographies = sorted({c.geography_zh or c.geography for c in companies if c.geography_zh or c.geography})
        priority_counts: dict[str, int] = {}
        opportunity_counts: dict[str, int] = {}
        readiness_counts: dict[str, int] = {}
        bucket_counts: dict[str, int] = {}
        for c in companies:
            label = opportunity_priority(c)["label"]
            priority_counts[label] = priority_counts.get(label, 0) + 1
            decision = canonical_decision(c)
            opportunity_counts[decision["opportunity_value"]] = opportunity_counts.get(decision["opportunity_value"], 0) + 1
            readiness_counts[decision["execution_readiness"]] = readiness_counts.get(decision["execution_readiness"], 0) + 1
            bucket_counts[decision["decision_bucket"]] = bucket_counts.get(decision["decision_bucket"], 0) + 1
        assessments = [c.longlist_assessment for c in companies if c.longlist_assessment]
        return {
            "categories": categories,
            "geographies": geographies,
            "spend_evidence_levels": sorted({c.spend_evidence_level for c in companies if c.spend_evidence_level}),
            "priority_counts": priority_counts,
            "commercial_dispositions": sorted({row.disposition for row in assessments}),
            "icp_types": sorted({row.icp_type for row in assessments}),
            "opportunity_values": opportunity_counts,
            "execution_readiness": readiness_counts,
            "decision_buckets": bucket_counts,
            "signal_filters": [
                {"value": "needs_buyer", "label": "需要补查买方"},
                {"value": "needs_route", "label": "需要补查路径"},
                {"value": "has_confirmed_spend_gtm", "label": "有已确认投放/GTM 证据"},
                {"value": "has_asia_signal", "label": "有亚洲市场信号"},
                {"value": "has_mango_employee_path", "label": "有 Mango 员工路径"},
                {"value": "has_official_direct_channel", "label": "有正式直达渠道"},
                {"value": "has_historical_creator_partnership", "label": "有历史 creator 合作"},
            ],
            "total": len(companies),
        }
    finally:
        session.close()


@router.get("/companies/{company_id}")
def get_company(company_id: str, request: Request):
    session = get_session()
    try:
        detail = company_detail(
            _load_company(session, company_id),
            include_internal=bool(getattr(request.state, "write_access", False)),
        )
        apify_intelligence = _load_public_apify_intelligence(company_id)
        if apify_intelligence:
            detail["apify_intelligence"] = apify_intelligence
        connector_candidates = _cross_company_connector_candidates(session)
        detail["cross_company_connectors"] = {
            key: [
                row for row in rows
                if company_id in row.get("target_company_ids", [])
            ][:5]
            for key, rows in connector_candidates.items()
        }
        return detail
    finally:
        session.close()


@router.get("/companies/{company_id}/suggested-creators")
def suggested_creators(company_id: str, limit: int = 20):
    """Candidate creators for this company's campaign, from the existing
    quote library -- category/region fit, historical sponsorship of this
    company or similar ones, existing relationship. Every result carries
    its reasons; there is no single fit score.

    Filtering rules (data-integrity sprint): Non-creator/Irrelevant entities
    never appear here, full stop. Media/Community Account entities are real
    and useful but are not *creator* recommendations -- they come back in a
    separate `media_channels` list instead of mixed into `results`. Needs
    Review (Unknown) entities can still surface (e.g. a prior sponsor with
    no classification yet), but always sort after every confidently-classified
    result, and carry an explicit `needs_review` flag rather than silently
    blending in. Rejected sponsorship evidence never counts as a
    "prior relationship". Explicit CreatorExclusion rows (global or
    company-scoped) remove a creator from suggestions entirely.
    """
    session = get_session()
    try:
        company = _load_company(session, company_id)
        # An organic mention/event appearance is evidence, but not a prior
        # commercial relationship and therefore must not receive the
        # historical-sponsor boost in creator recommendations.
        prior_creator_ids = {
            s.creator_id
            for s in company.sponsorships
            if s.creator_id
            and s.review_status == "confirmed"
            and canonical_sponsorship_type(s.disclosure_type)
            in {"paid_sponsorship", "affiliate", "ambassador_long_term_partner"}
        }
        excluded_creator_ids = {
            e.creator_id
            for e in session.query(CreatorExclusion).filter(
                (CreatorExclusion.company_id == company_id) | (CreatorExclusion.company_id.is_(None))
            )
        }
        creators = (
            session.query(Creator)
            .options(
                selectinload(Creator.social_accounts).selectinload(SocialAccount.recent_content),
                selectinload(Creator.rate_cards),
                selectinload(Creator.sponsorships),
            )
            .all()
        )
        results, media_channels = rank_suggested_creators(
            creators, company, prior_creator_ids=prior_creator_ids, excluded_creator_ids=excluded_creator_ids
        )
        return {"results": results[:limit], "media_channels": media_channels}
    finally:
        session.close()


def _campaign_role(row: dict) -> str:
    capability = (row.get("company_fit") or {}).get("best_capability")
    if capability:
        return capability["label"]
    creator_class = row.get("creator_class") or ""
    text = " ".join(row.get("reasons") or []).casefold()
    if creator_class in {"Top KOL", "Community Leader"}:
        return "Strategic KOL"
    if any(term in text for term in ("developer", "technical", "api", "开发", "技术")):
        return "Technical educator"
    if creator_class in {"KOC", "Marketing Account"}:
        return "KOC"
    return "Regional/community distribution" if row.get("platform") in {"X", "YouTube"} else "Creator distribution"


def _select_campaign_tier(rows: list[dict], target_count: int, *, lean: bool = False) -> list[dict]:
    """Build a genuinely different creator mix without inventing prices.

    Lean chooses the lowest quoted candidate in each available role. The
    larger tiers prioritize role coverage, then preserve recommendation rank.
    """
    if target_count <= 0:
        return []
    selected: list[dict] = []
    selected_ids: set[int] = set()
    if lean:
        best_by_role: dict[str, dict] = {}
        for row in rows:
            role = row["campaign_role"]
            current = best_by_role.get(role)
            if current is None or row["quote"]["representative"] < current["quote"]["representative"]:
                best_by_role[role] = row
        role_order = [
            "Strategic KOL", "Technical educator", "Regional/community distribution",
            "KOC", "Creator distribution", "Media/community channel",
        ]
        candidates = [best_by_role[role] for role in role_order if role in best_by_role]
        candidates.extend(row for role, row in best_by_role.items() if role not in role_order)
    else:
        seen_roles: set[str] = set()
        candidates = []
        for row in rows:
            if row["campaign_role"] not in seen_roles:
                candidates.append(row)
                seen_roles.add(row["campaign_role"])
        candidates.extend(rows)

    for row in candidates:
        if row["id"] in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(row["id"])
        if len(selected) >= target_count:
            break
    return selected


def _select_company_campaign_tier(rows: list[dict], target_count: int, *, tier_key: str) -> list[dict]:
    """Choose a capability-balanced tier without pretending every role is priced.

    A specialist with only an X-post quote can still be the right creative
    director or Japan node, but that quote must not be counted as if it priced
    the proposed work.  The tier keeps that person with an explicit
    ``separate_quote_required`` status and totals only aligned rate cards.
    """
    eligible = [row for row in rows if row.get("campaign_role")]
    if not eligible or target_count <= 0:
        return []

    if tier_key == "lean":
        ordered = sorted(
            eligible,
            key=lambda row: (
                0 if row.get("price_included_in_tier") else 1,
                (row.get("quote") or {}).get("representative", float("inf")),
                -row.get("fit_score", 0),
                row["id"],
            ),
        )
    elif tier_key == "premium":
        ordered = sorted(
            eligible,
            key=lambda row: (
                -row.get("fit_score", 0),
                -(row.get("followers") or 0),
                0 if row.get("price_included_in_tier") else 1,
                -(row.get("quote") or {}).get("representative", 0),
                row["id"],
            ),
        )
    else:
        ordered = sorted(
            eligible,
            key=lambda row: (
                -row.get("fit_score", 0),
                -(row.get("followers") or 0),
                0 if row.get("price_included_in_tier") else 1,
                (row.get("quote") or {}).get("representative", float("inf")),
                row["id"],
            ),
        )

    selected: list[dict] = []
    selected_ids: set[int] = set()
    seen_roles: set[str] = set()
    for row in ordered:
        if row["campaign_role"] in seen_roles:
            continue
        selected.append(row)
        selected_ids.add(row["id"])
        seen_roles.add(row["campaign_role"])
        if len(selected) >= target_count:
            return selected
    for row in ordered:
        if row["id"] in selected_ids:
            continue
        selected.append(row)
        selected_ids.add(row["id"])
        if len(selected) >= target_count:
            break
    return selected


def _price_campaign_tier(
    key: str,
    label: str,
    rows: list[dict],
    *,
    service_fee_pct: float | None,
    contingency_pct: float | None,
    localization_fee_usd: float | None,
    purpose: str | None = None,
    unpriced_scope: list[str] | None = None,
) -> dict:
    priced_rows = [row for row in rows if row.get("price_included_in_tier", True)]
    creator_media = round(sum(row["quote"]["representative"] for row in priced_rows), 2)
    service_fee = round(creator_media * service_fee_pct / 100, 2) if service_fee_pct is not None else None
    contingency = round(creator_media * contingency_pct / 100, 2) if contingency_pct is not None else None
    localization = round(localization_fee_usd, 2) if localization_fee_usd is not None else None
    total = round(creator_media + (service_fee or 0) + (contingency or 0) + (localization or 0), 2)
    return {
        "key": key,
        "label": label,
        "creator_count": len(rows),
        "priced_creator_count": len(priced_rows),
        "creator_ids": [row["id"] for row in rows],
        "creator_media_cost_usd": creator_media,
        "mango_service_fee_usd": service_fee,
        "contingency_usd": contingency,
        "localization_fee_usd": localization,
        "total_client_budget_usd": total,
        "commercial_assumptions_complete": service_fee_pct is not None,
        "program_budget_complete": not bool(unpriced_scope) and service_fee_pct is not None,
        "purpose": purpose,
        "unpriced_scope": unpriced_scope or [],
        "price_scope_label": "已知且交付范围匹配的 creator media 成本" if unpriced_scope else "已配置成本",
    }


@router.get("/companies/{company_id}/campaign-preview")
def campaign_preview(
    company_id: str,
    limit: int = Query(default=8, ge=3, le=12),
    service_fee_pct: float | None = Query(default=None, ge=0, le=100),
    contingency_pct: float | None = Query(default=None, ge=0, le=100),
    localization_fee_usd: float | None = Query(default=None, ge=0),
):
    """Read-only, quote-grounded proposal preview. No Shortlist is created."""
    session = get_session()
    try:
        company = _load_company(session, company_id)
        decision = canonical_decision(company)
        campaign_profile = campaign_profile_for(company)
        campaign_profile_json = profile_public_json(campaign_profile)
        apify_intelligence = _load_public_apify_intelligence(company_id)
        research_cohorts = ((apify_intelligence or {}).get("campaign") or {}).get("cohorts") or []
        prior_creator_ids = {
            evidence.creator_id
            for evidence in company.sponsorships
            if evidence.creator_id
            and evidence.review_status == "confirmed"
            and canonical_sponsorship_type(evidence.disclosure_type)
            in {"paid_sponsorship", "affiliate", "ambassador_long_term_partner"}
        }
        excluded_creator_ids = {
            row.creator_id
            for row in session.query(CreatorExclusion).filter(
                (CreatorExclusion.company_id == company_id) | (CreatorExclusion.company_id.is_(None))
            )
        }
        creators = (
            session.query(Creator)
            .options(
                selectinload(Creator.social_accounts).selectinload(SocialAccount.recent_content),
                selectinload(Creator.rate_cards),
                selectinload(Creator.sponsorships).joinedload(SponsorshipEvidence.company),
            )
            .all()
        )
        ranked, media = rank_suggested_creators(
            creators,
            company,
            prior_creator_ids=prior_creator_ids,
            excluded_creator_ids=excluded_creator_ids,
        )
        creators_by_id = {creator.id: creator for creator in creators}
        campaign_candidates = []

        def candidate_row(row: dict, *, is_media: bool = False) -> dict:
            creator = creators_by_id[row["id"]]
            capability = (row.get("company_fit") or {}).get("best_capability")
            role = _campaign_role(row)
            confident_rates = [
                rate for rate in creator.rate_cards
                if rate.is_confident and rate.quote_amount_usd is not None
            ]
            rate_options = [
                (rate, quote_scope_fit(rate, capability)) for rate in confident_rates
            ]
            aligned = [item for item in rate_options if item[1]["price_included"]]
            chosen = min(
                aligned or rate_options,
                key=lambda item: item[0].quote_amount_usd or float("inf"),
                default=(None, {
                    "status": "quote_required",
                    "price_included": False,
                    "reason": "当前没有可解析的数值报价，需先确认交付物、档期和价格。",
                }),
            )
            rate, scope = chosen
            prior_sponsors = sorted({
                evidence.company.name
                for evidence in creator.sponsorships
                if evidence.company and evidence.review_status != "rejected"
            })
            profile_account = next(
                (account for account in creator.social_accounts if account.profile_url),
                creator.social_accounts[0] if creator.social_accounts else None,
            )
            return {
                **row,
                "campaign_role": role,
                "audience": ", ".join(filter(None, (creator.region, creator.language, creator.categories))) or "受众待核实",
                "recommended_deliverable": (
                    capability.get("proposed_deliverable") if capability else "交付能力待人工确认"
                ),
                "priced_deliverable": rate.deliverable if rate else None,
                "rate_card_id": rate.id if rate else None,
                "quote": ({
                    "currency": "USD",
                    "representative": rate.quote_amount_usd,
                    "min": rate.quote_amount_min if rate.quote_currency == "USD" else rate.quote_amount_usd,
                    "max": rate.quote_amount_max if rate.quote_currency == "USD" else rate.quote_amount_usd,
                    "source_currency": rate.quote_currency,
                    "raw_quote_text": rate.raw_quote_text,
                } if rate else None),
                "quote_scope": scope,
                "price_included_in_tier": bool(scope["price_included"]),
                "estimated_cpm": row.get("cpm_min"),
                "historical_relevant_sponsors": prior_sponsors,
                "fit_evidence": capability.get("evidence", []) if capability else [],
                "matched_capabilities": row.get("matched_capabilities", []),
                "profile_url": profile_account.profile_url if profile_account else None,
                "followers": max((account.followers or 0 for account in creator.social_accounts), default=0) or None,
                "is_media_channel": is_media,
                "risk_or_unknown": (
                    "分类尚待人工审核；入选方案前需确认身份与受众。"
                    if row.get("needs_review")
                    else "报价有效期未单独记录；发送 proposal 前需要复价和排期。"
                ),
                "quote_last_record_update": creator.updated_at.date().isoformat() if creator.updated_at else None,
            }

        for row in ranked:
            if (row.get("company_fit") or {}).get("generic_only"):
                continue
            campaign_candidates.append(candidate_row(row))
        for row in media:
            if (row.get("company_fit") or {}).get("generic_only"):
                continue
            campaign_candidates.append(candidate_row(row, is_media=True))

        # A company-specific research layer must replace generic category
        # matching, not merely decorate it. These candidates have explicit
        # Gamma content/commercial observations, but they remain unreviewed
        # and therefore never become a confirmed historical relationship.
        if research_cohorts:
            research_candidates: list[dict] = []
            seen_research_creator_ids: set[int] = set()
            cohort_roles = {
                "旗舰工作流教育": "工作流教育者",
                "演示文稿垂类教程": "工作流教育者",
                "评测与 affiliate 长尾": "评测 / affiliate 分发",
            }
            role_overrides = {
                "Jeff Su": "业务 / 增长 operator",
                "Tiago Forte": "创业者 / 教育 creator",
            }
            deliverables = {
                "旗舰工作流教育": "用真实知识工作流制作一支 Gamma 教程，并使用独立追踪链接衡量激活。",
                "演示文稿垂类教程": "制作强 presentation intent 的 Gamma 对比或教程内容，并跟踪模板使用与激活。",
                "评测与 affiliate 长尾": "使用可追踪链接测试常青评测、教程搜索流量和转化。",
            }
            scope_terms = {
                "旗舰工作流教育": ("video", "youtube", "tutorial", "min", "视频"),
                "演示文稿垂类教程": ("video", "youtube", "tutorial", "min", "视频"),
                "评测与 affiliate 长尾": ("video", "youtube", "review", "newsletter", "post", "视频"),
            }
            research_rank = 0
            for cohort in research_cohorts:
                cohort_name = str(cohort.get("cohort") or "")
                if cohort_name not in cohort_roles:
                    continue
                for source_row in cohort.get("creator_candidates") or []:
                    creator_id = source_row.get("existing_creator_id")
                    if not isinstance(creator_id, int) or creator_id in seen_research_creator_ids:
                        continue
                    if creator_id in excluded_creator_ids or creator_id not in creators_by_id:
                        continue
                    creator = creators_by_id[creator_id]
                    research_rank += 1
                    seen_research_creator_ids.add(creator_id)
                    role = role_overrides.get(source_row.get("creator"), cohort_roles[cohort_name])
                    capability = {
                        "label": role,
                        "proposed_deliverable": deliverables[cohort_name],
                        "priced_scope_terms": list(scope_terms[cohort_name]),
                        "evidence": [source_row.get("why_fit") or "已找到该公司相关内容证据，待人工复核。"],
                    }
                    confident_rates = [
                        rate for rate in creator.rate_cards
                        if rate.is_confident and rate.quote_amount_usd is not None
                    ]
                    rate_options = [(rate, quote_scope_fit(rate, capability)) for rate in confident_rates]
                    aligned = [item for item in rate_options if item[1]["price_included"]]
                    rate, scope = min(
                        aligned or rate_options,
                        key=lambda item: item[0].quote_amount_usd or float("inf"),
                        default=(None, {
                            "status": "quote_required",
                            "price_included": False,
                            "reason": "公司特定内容适配已观察到，但本方案交付、档期和报价仍需逐项确认。",
                        }),
                    )
                    fit_score = 140 if rate and scope["price_included"] else 120 - research_rank
                    profile_account = next(
                        (account for account in creator.social_accounts if account.profile_url),
                        creator.social_accounts[0] if creator.social_accounts else None,
                    )
                    research_candidates.append({
                        "id": creator.id,
                        "display_name": creator.display_name,
                        "handle": profile_account.handle if profile_account else None,
                        "platform": profile_account.platform if profile_account else "YouTube",
                        "followers": source_row.get("youtube_subscribers") or (
                            max((account.followers or 0 for account in creator.social_accounts), default=0) or None
                        ),
                        "creator_class": creator.creator_class,
                        "prior_relationship": False,
                        "prior_evidence_types": [source_row.get("commercial_type_candidate")],
                        "prior_evidence_review_statuses": ["unreviewed"],
                        "has_confirmed_paid_evidence": False,
                        "needs_review": True,
                        "reasons": [source_row.get("why_fit")],
                        "company_fit": {
                            "profile_key": campaign_profile.key,
                            "profile_thesis": campaign_profile.thesis_zh,
                            "matched_capabilities": [capability],
                            "best_capability": capability,
                            "fit_score": fit_score,
                            "fit_level": "company_specific_observation_unreviewed",
                            "generic_only": False,
                        },
                        "fit_level": "company_specific_observation_unreviewed",
                        "fit_score": fit_score,
                        "matched_capabilities": [capability],
                        "quote_min_usd": rate.quote_amount_usd if rate else None,
                        "quote_max_usd": rate.quote_amount_usd if rate else None,
                        "cpm_min": None,
                        "campaign_role": role,
                        "audience": source_row.get("region_evidence") or "受众地域待核实",
                        "recommended_deliverable": deliverables[cohort_name],
                        "priced_deliverable": rate.deliverable if rate else None,
                        "rate_card_id": rate.id if rate else None,
                        "quote": ({
                            "currency": "USD",
                            "representative": rate.quote_amount_usd,
                            "min": rate.quote_amount_min if rate.quote_currency == "USD" else rate.quote_amount_usd,
                            "max": rate.quote_amount_max if rate.quote_currency == "USD" else rate.quote_amount_usd,
                            "source_currency": rate.quote_currency,
                            "raw_quote_text": rate.raw_quote_text,
                        } if rate else None),
                        "quote_scope": scope,
                        "price_included_in_tier": bool(scope["price_included"]),
                        "estimated_cpm": None,
                        "historical_relevant_sponsors": [f"{company.name}（公开商业候选，待复核）"],
                        "fit_evidence": capability["evidence"],
                        "profile_url": source_row.get("youtube_channel_url") or (
                            profile_account.profile_url if profile_account else None
                        ),
                        "is_media_channel": False,
                        "risk_or_unknown": "公司与 creator 的内容/商业观察尚未人工确认；发送 proposal 前必须复核证据、受众、档期和报价。",
                        "quote_last_record_update": creator.updated_at.date().isoformat() if creator.updated_at else None,
                        "evidence_url": source_row.get("evidence_url"),
                    })
            campaign_candidates = research_candidates

        qualification_candidates = [row for row in campaign_candidates if not row["price_included_in_tier"]]
        target_counts = campaign_profile_json["tier_target_counts"]
        lean_mix = _select_company_campaign_tier(
            campaign_candidates,
            min(target_counts["lean"], len(campaign_candidates)),
            tier_key="lean",
        )
        recommended_mix = _select_company_campaign_tier(
            campaign_candidates,
            min(target_counts["recommended"], limit, len(campaign_candidates)),
            tier_key="recommended",
        )
        premium_mix = _select_company_campaign_tier(
            campaign_candidates,
            min(target_counts["premium"], len(campaign_candidates)),
            tier_key="premium",
        )
        selected = recommended_mix
        tier_purposes = campaign_profile_json["tier_purposes"]
        tier_unpriced = campaign_profile_json["unpriced_scope_by_tier"]
        pricing_tiers = [
            _price_campaign_tier(
                "lean", "Lean", lean_mix, service_fee_pct=service_fee_pct,
                contingency_pct=contingency_pct, localization_fee_usd=localization_fee_usd,
                purpose=tier_purposes["lean"], unpriced_scope=tier_unpriced["lean"],
            ),
            _price_campaign_tier(
                "recommended", "Recommended", recommended_mix, service_fee_pct=service_fee_pct,
                contingency_pct=contingency_pct, localization_fee_usd=localization_fee_usd,
                purpose=tier_purposes["recommended"], unpriced_scope=tier_unpriced["recommended"],
            ),
            _price_campaign_tier(
                "premium", "Premium", premium_mix, service_fee_pct=service_fee_pct,
                contingency_pct=contingency_pct, localization_fee_usd=localization_fee_usd,
                purpose=tier_purposes["premium"], unpriced_scope=tier_unpriced["premium"],
            ),
        ]
        known_creator_media_totals = {
            tier["creator_media_cost_usd"] for tier in pricing_tiers
        }
        pricing_display_mode = (
            "concept_only_pricing_incomplete"
            if research_cohorts
            and len(known_creator_media_totals) == 1
            and all(not tier["program_budget_complete"] for tier in pricing_tiers)
            else "tier_scenarios"
        )
        budget = {
            "minimum_usd": pricing_tiers[0]["total_client_budget_usd"],
            "recommended_usd": pricing_tiers[1]["total_client_budget_usd"],
            "maximum_usd": pricing_tiers[2]["total_client_budget_usd"],
            "basis": "显示的是已知且与建议交付物匹配的 creator media 成本。Mango 服务费、项目运营、制作 grant、活动或场地等未报价范围不会被伪装进总额。",
            "is_full_program_budget": False,
        }
        expected_roles = {row["label"] for row in campaign_profile_json["required_capabilities"]}
        missing_roles = sorted(expected_roles - {row["campaign_role"] for row in selected})
        asia = company.asia_profile
        asia_angle = (
            asia.recommended_market_entry_angle
            if asia and asia.asia_interest_status in {"confirmed_expansion", "active_market", "strong_signal"}
            else "本轮未发现足够亚洲市场意图证据，不把 Asia 作为默认销售角度。"
        )
        best_route = next((route for route in company.contact_routes if route.is_best), None)
        qualification_markdown = [
            f"- {row['campaign_role']} | {row['display_name']} | {row['quote_scope']['reason']}"
            for row in qualification_candidates[:8]
        ] or ["- 暂无额外待询价 specialist 候选。"]

        def proposal_creator_line(row: dict) -> str:
            price = (
                f"USD {row['quote']['representative']:,.0f}"
                if row.get("price_included_in_tier") and row.get("quote")
                else "需按本方案重新询价"
            )
            priced_deliverable = row.get("priced_deliverable") or "现有报价不覆盖本方案交付"
            return (
                f"- {row['campaign_role']} | {row['display_name']} | "
                f"建议：{row['recommended_deliverable']} | 已有记录：{priced_deliverable} | {price}"
            )

        pricing_summary = (
            "已有活动概念，但报价未完整。"
            f"当前已知创作者费用小计：USD {budget['recommended_usd']:,.0f}；"
            "在成本形成真实差异前，不得把三档方案当作客户报价。"
            if pricing_display_mode == "concept_only_pricing_incomplete"
            else f"Recommended 已知 creator media 成本：USD {budget['recommended_usd']:,.0f}（不是完整 program budget）"
        )
        proposal_markdown = "\n".join([
            f"# {company.name} × Mango 初步 Campaign Proposal",
            "",
            f"**Campaign 核心命题**：{campaign_profile.thesis_zh}",
            f"**为什么现在**：{decision['why_now']}",
            f"**市场角度**：{asia_angle}",
            f"**目标受众**：{company.research_dossier.target_customer if company.research_dossier else '待与买方确认'}",
            f"**定价状态**：{pricing_summary}",
            f"**联系路径**：{best_route.label if best_route else decision['primary_route']}",
            f"**下一商业动作**：{decision['first_action']}",
            "",
            "## 已报价且交付范围匹配的 creator 组合",
            *[proposal_creator_line(row) for row in selected],
            "",
            "## 能力匹配但需重新询价",
            *qualification_markdown,
            "",
            "## 当前未计价范围",
            *[f"- {item}" for item in tier_unpriced["recommended"]],
            "",
            "## 衡量方案",
            "- 按 creator / deliverable 使用独立追踪链接或 code。",
            "- 记录触达、观看、合格访问、注册/激活与每个转化成本。",
            "- 发布后 7/14/30 天复盘，并把自然扩散与付费交付分开。",
        ])
        return {
            "company_id": company.company_id,
            "company": company.name,
            "campaign_thesis": campaign_profile.thesis_zh,
            "why_now": decision["why_now"],
            "asia_market_angle": asia_angle,
            "target_audience": company.research_dossier.target_customer if company.research_dossier else None,
            "creator_mix": selected,
            "creator_mix_by_tier": {
                "lean": lean_mix,
                "recommended": recommended_mix,
                "premium": premium_mix,
            },
            "capability_roster": [
                row for row in qualification_candidates
                if row["id"] not in {item["id"] for item in selected}
            ][:12],
            "research_creator_cohorts": research_cohorts,
            "research_pricing_guardrail": ((apify_intelligence or {}).get("campaign") or {}).get("pricing_guardrail"),
            "campaign_profile": campaign_profile_json,
            "unfilled_roles": missing_roles,
            "budget_allocation": budget,
            "pricing_tiers": pricing_tiers,
            "pricing_display_mode": pricing_display_mode,
            "commercial_cost_inputs": {
                "service_fee_pct": service_fee_pct,
                "contingency_pct": contingency_pct,
                "localization_fee_usd": localization_fee_usd,
                "status": "configured" if service_fee_pct is not None else "mango_fee_required_before_client_send",
            },
            "measurement_plan": [
                "独立追踪链接/code", "触达与观看", "合格访问", "注册/激活", "CPA/CAC", "7/14/30 天复盘",
            ],
            "contact_route": best_route.label if best_route else decision["primary_route"],
            "next_commercial_step": decision["first_action"],
            "proposal_markdown": proposal_markdown,
            "generated_from": (
                "company-specific public research candidates + existing rate records"
                if research_cohorts
                else "company delivery profile + creator public fields + confirmed rate and sponsorship records"
            ),
            "writes_performed": False,
        }
    finally:
        session.close()


def _json_array(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


@router.get("/companies/{company_id}/sales-packet")
def company_sales_packet(
    company_id: str,
    service_fee_pct: float | None = Query(default=None, ge=0, le=100),
    contingency_pct: float | None = Query(default=None, ge=0, le=100),
    localization_fee_usd: float | None = Query(default=None, ge=0),
):
    """Top-5 buyer map, outreach copy and quote-grounded offer in one view."""
    preview = campaign_preview(
        company_id,
        limit=8,
        service_fee_pct=service_fee_pct,
        contingency_pct=contingency_pct,
        localization_fee_usd=localization_fee_usd,
    )
    session = get_session()
    try:
        company = (
            session.query(Company)
            .options(joinedload(Company.sales_packet), selectinload(Company.buyer_map_entries))
            .filter(Company.company_id == company_id)
            .one_or_none()
        )
        if company is None:
            raise HTTPException(404, f"Company {company_id!r} not found")
        packet = company.sales_packet
        if packet is None:
            raise HTTPException(404, "This company does not yet have a send-ready sales packet")
        buyer_entries = sorted(
            company.buyer_map_entries,
            key=lambda row: (row.fallback_order is None, row.fallback_order or 0, row.id),
        )
        buyer_map = [
            {
                "entry_key": row.entry_key,
                "buyer_type": row.buyer_type,
                "name": row.name,
                "role": row.role,
                "relevance": row.relevance_zh,
                "verification_status": row.verification_status,
                "contact_method": row.contact_method,
                "route_type": row.route_type,
                "source_url": row.source_url,
                "evidence_date": row.evidence_date,
                "confidence": row.confidence,
                "next_step": row.next_step_zh,
                "fallback_order": row.fallback_order,
            }
            for row in buyer_entries
        ]
        official_named = [
            row for row in buyer_map
            if row["name"] and row["verification_status"] == "official_named"
        ]
        official_routes = [
            row for row in buyer_map
            if row["source_url"] and row["verification_status"].startswith("official")
        ]
        economic_buyer = next((row for row in buyer_map if row["buyer_type"] == "economic_buyer"), None)
        send_status = (
            "person_and_route_ready" if official_named and official_routes
            else "official_team_route_ready" if official_routes
            else "research_required"
        )
        if preview["pricing_display_mode"] == "concept_only_pricing_incomplete":
            known_subtotal = preview["pricing_tiers"][1]["creator_media_cost_usd"]
            pricing_lines = [
                "- 已有活动概念，但报价未完整。",
                f"- 当前已知创作者费用小计：USD {known_subtotal:,.0f}。",
                "- 在创作者报价、Mango 服务费、本地化和项目成本形成真实差异前，不展示三档报价。",
            ]
        else:
            pricing_lines = []
            for tier in preview["pricing_tiers"]:
                fee_text = (
                    f"USD {tier['mango_service_fee_usd']:,.0f}"
                    if tier["mango_service_fee_usd"] is not None else "REQUIRED BEFORE CLIENT SEND"
                )
                pricing_lines.append(
                    f"- {tier['label']}: {tier['creator_count']} creators, creator media USD "
                    f"{tier['creator_media_cost_usd']:,.0f}, Mango fee {fee_text}, scenario total USD "
                    f"{tier['total_client_budget_usd']:,.0f}"
                )
        packet_markdown = "\n".join([
            f"# {company.name} x Mango Sales Packet",
            "",
            f"**Offer**: {packet.offer_name_zh}",
            f"**Commercial thesis**: {packet.commercial_thesis_zh}",
            f"**Audience**: {packet.audience_zh}",
            f"**Contact readiness**: {send_status}",
            f"**Economic buyer**: {(economic_buyer or {}).get('name') or (economic_buyer or {}).get('role') or 'Unknown'}",
            "",
            "## Pricing scenarios",
            *pricing_lines,
            "",
            "Pricing note: creator media uses existing confident quotes. Mango fees and other assumptions must be configured before client send.",
            "",
            "## Deliverables",
            *[f"- {item}" for item in _json_array(packet.deliverables_json)],
            "",
            "## Measurement",
            *[f"- {item}" for item in _json_array(packet.measurement_json)],
            "",
            "## Qualification questions",
            *[f"- {item}" for item in _json_array(packet.qualification_questions_json)],
            "",
            "## Buyer and route map",
            *[
                f"- {row['buyer_type']}: {row['name'] or 'Unknown'} | {row['role']} | "
                f"{row['verification_status']} | {row['next_step']}"
                for row in buyer_map
            ],
            "",
            "## Email",
            f"Subject: {packet.email_subject_en}",
            "",
            packet.email_body_en,
            "",
            "## X DM",
            packet.x_dm_en,
            "",
            "## Evidence boundary",
            packet.evidence_note_zh,
        ])
        return {
            "company_id": company.company_id,
            "company": company.name,
            "packet_version": packet.packet_version,
            "offer_name": packet.offer_name_zh,
            "commercial_thesis": packet.commercial_thesis_zh,
            "audience": packet.audience_zh,
            "deliverables": _json_array(packet.deliverables_json),
            "measurement": _json_array(packet.measurement_json),
            "qualification_questions": _json_array(packet.qualification_questions_json),
            "switch_rules": _json_array(packet.switch_rules_json),
            "outreach": {
                "email_subject": packet.email_subject_en,
                "email_body": packet.email_body_en,
                "x_dm": packet.x_dm_en,
            },
            "buyer_map": buyer_map,
            "send_readiness": {
                "status": send_status,
                "official_named_people": len(official_named),
                "official_routes": len(official_routes),
                "economic_buyer_status": economic_buyer["verification_status"] if economic_buyer else "unknown",
            },
            "evidence_note": packet.evidence_note_zh,
            "confidence": packet.confidence,
            "last_verified_at": packet.last_verified_at.isoformat(),
            "campaign": preview,
            "packet_markdown": packet_markdown,
            "writes_performed": False,
        }
    finally:
        session.close()


class BuildCampaignRequest(BaseModel):
    name: str | None = None
    objective: str
    target_audience: str | None = None
    budget_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    region_pref: str | None = None
    language_pref: str | None = None
    platforms_pref: str | None = None
    timing: str | None = None
    pricing_assumptions: dict[str, float | None] | None = None

    @field_validator("objective")
    @classmethod
    def validate_objective(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("objective must not be blank")
        return value

    @field_validator("name")
    @classmethod
    def normalize_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("pricing_assumptions")
    @classmethod
    def validate_pricing_assumptions(cls, value: dict[str, float | None] | None):
        if value is None:
            return None
        allowed = {"service_fee_pct", "contingency_pct", "localization_fee_usd"}
        if set(value) - allowed:
            raise ValueError("pricing_assumptions contains unsupported keys")
        for key in ("service_fee_pct", "contingency_pct"):
            number = value.get(key)
            if number is not None and not 0 <= number <= 100:
                raise ValueError(f"{key} must be between 0 and 100")
        localization = value.get("localization_fee_usd")
        if localization is not None and localization < 0:
            raise ValueError("localization_fee_usd must be non-negative")
        return value


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
            pricing_assumptions_json=(
                json.dumps(body.pricing_assumptions, ensure_ascii=False, sort_keys=True)
                if body.pricing_assumptions is not None else None
            ),
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


@router.get("/network/route-portfolio")
def network_route_portfolio():
    """Company-opening routes, grouped by commercial usefulness.

    Legacy connector rows remain queryable in the archive endpoint.  This
    view never upgrades a follow edge into employment or a warm introduction.
    """
    session = get_session()
    try:
        companies = _load_all_companies(session)
        summaries = {company.company_id: company_summary(company) for company in companies}

        def company_ref(company: Company) -> dict:
            decision = summaries[company.company_id]["canonical_decision"]
            return {
                "company_id": company.company_id,
                "company": company.name,
                "opportunity_value": decision["opportunity_value"],
                "opportunity_value_label": decision["opportunity_value_label"],
                "execution_readiness": decision["execution_readiness_label"],
            }

        official_channels = []
        partner_programs = []
        secondary_paths = []
        verified_person_paths = []
        creator_intelligence = []
        confirmed_warm = []

        for company in companies:
            decision = summaries[company.company_id]["canonical_decision"]
            if decision["opportunity_value"] == "archive":
                continue
            for route in sorted(
                company.contact_routes,
                key=lambda row: (0 if row.is_best else 1, row.fallback_order or 99, row.id),
            ):
                row = {
                    **company_ref(company),
                    "route_type": route.route_type,
                    "route": route.label,
                    "why": route.why_this_route,
                    "first_step": route.required_first_step,
                    "evidence_url": route.evidence_url,
                    "confidence": route.confidence,
                }
                route_text = f"{route.label} {route.route_detail}".casefold()
                if any(term in route_text for term in ("partner", "affiliate", "community", "developer program")):
                    partner_programs.append(row)
                elif route.route_type in {"Direct", "Cold"} and route.evidence_url:
                    official_channels.append(row)

            stage_code = summaries[company.company_id].get("relationship_stage_code") or "E0"
            try:
                stage_level = int(stage_code[1:])
            except (ValueError, TypeError):
                stage_level = 0
            if stage_level >= 4:
                confirmed_warm.append({
                    **company_ref(company),
                    "route": summaries[company.company_id].get("reachability_label"),
                    "confidence": "human_execution_record",
                })

            seen_secondary = set()
            for path in company.intro_paths:
                if decision["decision_source"] != "priority_15_review":
                    continue
                if not getattr(path, "graph_reachable", False) or (path.hop_count or 0) < 2:
                    continue
                route_key = (path.target_type, path.connector_handle, path.path_labels, path.edge_directions)
                if route_key in seen_secondary:
                    continue
                seen_secondary.add(route_key)
                directions = _edge_direction_list(path)
                if path.target_type == "operator_person":
                    labels = (path.path_labels or "").casefold()
                    operator = next(
                        (
                            item for item in company.operators
                            if item.name
                            and item.identity_confirmed
                            and (
                                item.name.casefold() in labels
                                or (
                                    item.x_handle
                                    and item.x_handle.lstrip("@").casefold() in labels
                                )
                            )
                        ),
                        None,
                    )
                    # Every hop must carry direction data. A visible person
                    # path also needs at least one relationship-strength
                    # signal; a chain of one-way follows stays in research
                    # storage but is not promoted into this UI block.
                    complete_directions = bool(directions) and len(directions) >= (path.hop_count or 0)
                    has_relationship_signal = "mutual_follow" in directions
                    if operator and complete_directions and has_relationship_signal:
                        role_text = (operator.role or "").casefold()
                        buyer_terms = (
                            "growth", "marketing", "partnership", "community",
                            "developer relations", "devrel", "ecosystem",
                            "affiliate", "creator",
                        )
                        buyer_relevant = bool(
                            operator.budget_authority_confirmed
                            or any(term in role_text for term in buyer_terms)
                        )
                        verified_person_paths.append({
                            **company_ref(company),
                            "route": _public_internal_person_copy(path.path_labels or "人对人研究路径"),
                            "route_type": "Verified person-to-person research path",
                            "edge_directions": directions,
                            "endpoint_name": operator.name,
                            "endpoint_role": operator.role,
                            "endpoint_is_relevant_buyer": buyer_relevant,
                            "endpoint_buyer_status": (
                                "相关买方 / 预算影响者"
                                if buyer_relevant
                                else "公司真实员工，但尚未证明是相关买方"
                            ),
                            "relationship_basis": "路径中至少一跳为已观察到的 X 互关；其余跳按逐边方向展示。",
                            "why": "可作为定向联系人研究路径；仍不等于愿意引荐。",
                            "confidence": "research_only",
                            "supporting_evidence": path.source_artifact or path.evidence_scope,
                        })
                    continue
                secondary_paths.append({
                    **company_ref(company),
                    "route": _public_internal_person_copy(path.path_labels or path.connector_handle or "二级研究路径"),
                    "route_type": "Weak secondary research path",
                    "edge_directions": directions,
                    "why": "仅作为联系人研究线索；单向关注不代表认识、可引荐或可私信。",
                    "confidence": "research_only",
                })

            if decision["flags"]["has_historical_creator_partnership"]:
                creator_intelligence.append({
                    **company_ref(company),
                    "route": "历史 creator 商业情报",
                    "why": "可用于询问采购流程或复盘 campaign；不自动描述为愿意引荐。",
                    "evidence_count": summaries[company.company_id]["sponsorship_count"],
                })

        sort_key = lambda row: (
            {"tier_a": 0, "tier_b": 1, "tier_c": 2}.get(row["opportunity_value"], 9),
            row["company"].casefold(),
        )
        for rows in (
            official_channels, partner_programs, verified_person_paths,
            secondary_paths, creator_intelligence, confirmed_warm,
        ):
            rows.sort(key=sort_key)
        secondary_paths = secondary_paths[:20]

        cross_company_connectors = _cross_company_connector_candidates(session)
        for row in cross_company_connectors["x_research_connectors"]:
            row["required_first_step"] = _public_internal_person_copy(
                row.get("required_first_step")
            )
        return {
            "confirmed_warm_routes": confirmed_warm,
            # Empty by design until the database carries attributed employment
            # evidence on every edge. Follow paths are never put in this group.
            "company_employee_routing_paths": [],
            "verified_person_to_person_paths": verified_person_paths,
            "official_commercial_channels": official_channels,
            "partnership_community_programs": partner_programs,
            "creator_side_commercial_intelligence": creator_intelligence,
            "secondary_research_paths": secondary_paths,
            "cross_company_connectors": {
                "creator_connectors": cross_company_connectors["creator_connectors"][:12],
                "x_research_connectors": cross_company_connectors["x_research_connectors"][:12],
                "contract": (
                    "跨公司覆盖用于提高一次研究或沟通的复用价值；"
                    "任何历史合作、X 路径或现成报价都不等于愿意引荐。"
                ),
            },
            "archive": {
                "connector_brief_count": session.query(ConnectorBrief).count(),
                "note": "旧 connector 核实队列和泛 crypto 候选保留在研究归档，不参与目标公司排序。",
            },
        }
    finally:
        session.close()


@router.get("/network/interview-queue")
def network_interview_queue():
    """Section V (2026-08-28): the Network page's actual first question is
    "who should Mango ask, and how many companies does one conversation
    cover" -- not a bridge-person leaderboard. Groups every live (not
    disproven) bridge candidate by which Mango-side account it's mutual
    with, i.e. who has to be the one to actually ask. Mirrors
    scripts/generate_connector_pilot.py's logic but live, across all
    companies, not just the 7-company pilot set."""
    session = get_session()
    try:
        companies = _load_all_companies(session)
        by_asker: dict[str, list[dict]] = {}
        people: dict[str, dict] = {}
        for c in companies:
            summary = company_summary(c)
            outreach_logs = list(c.outreach_logs or [])
            bridges = [b for o in c.operators for b in getattr(o, "bridges", [])]
            for stage in deduplicate_bridge_stages(bridges, outreach_logs):
                if stage["disproven"]:
                    continue
                item = {
                        "company_id": c.company_id,
                        "company_name": c.name,
                        "execution_priority": summary["execution_priority"],
                        "business_priority": summary["priority"],
                        "bridge_handle": stage["bridge_handle"],
                        "bridge_name": stage["bridge_name"],
                        "code": stage["code"],
                        "label": stage["label"],
                        "evidence_note": stage["evidence_note"],
                        "verification_status": stage["interaction_verification_status"],
                        "askability": stage["askability"],
                        "social_cost": stage["social_cost"],
                        "suggested_question": stage["suggested_question"],
                    }
                for asker in stage["mango_side_handles"]:
                    by_asker.setdefault(asker, []).append(dict(item))
                person = people.setdefault(
                    stage["bridge_handle"].casefold(),
                    {
                        "bridge_handle": stage["bridge_handle"],
                        "bridge_name": stage["bridge_name"],
                        "mango_owner_candidates": set(),
                        "companies": [],
                        "best_askability_rank": -1,
                        "best_stage_level": 0,
                        "interaction_checked": False,
                        "interaction_found": False,
                        "exact_first_ask": stage["exact_first_ask"],
                        "fallback": stage["fallback"],
                    },
                )
                person["mango_owner_candidates"].update(stage["mango_side_handles"])
                person["companies"].append(item)
                person["best_askability_rank"] = max(person["best_askability_rank"], stage["askability_rank"])
                person["best_stage_level"] = max(person["best_stage_level"], stage["level"])
                person["interaction_checked"] = person["interaction_checked"] or stage["interaction_checked"]
                person["interaction_found"] = person["interaction_found"] or stage["interaction_evidence_count"] > 0
        exec_rank = {"需要跟进": 6, "现在联系": 5, "等待回复": 4, "等待内部核实": 3, "本周准备": 2, "暂缓": 1, "放弃": 0}
        priority_rank = {"High": 3, "Medium": 2, "Low": 1}
        queue = []
        for asker, items in sorted(by_asker.items(), key=lambda kv: -len(kv[1])):
            items.sort(
                key=lambda item: (
                    -exec_rank.get(item["execution_priority"], 0),
                    -priority_rank.get(item["business_priority"], 0),
                    -int(item["code"][1:]),
                    item["company_name"].lower(),
                    item["bridge_handle"].lower(),
                )
            )
            queue.append({"ask": asker, "count": len(items), "items": items})
        person_rows = []
        for person in people.values():
            person["mango_owner_candidates"] = sorted(person["mango_owner_candidates"])
            person["companies"].sort(
                key=lambda item: (
                    -exec_rank.get(item["execution_priority"], 0),
                    -priority_rank.get(item["business_priority"], 0),
                    item["company_name"].casefold(),
                )
            )
            person["companies_potentially_unlocked"] = [item["company_name"] for item in person["companies"]]
            person["current_status"] = (
                "relationship_candidate" if person["best_stage_level"] >= 2 else "verification_queue"
            )
            person_rows.append(person)
        person_rows.sort(
            key=lambda person: (
                -person["best_askability_rank"],
                -person["best_stage_level"],
                person["bridge_handle"].casefold(),
            )
        )
        return {"queue": queue, "people": person_rows, "interaction_audit": interaction_audit_summary(companies)}
    finally:
        session.close()


@router.get("/network/bridges")
def network_bridges():
    """Mutual-follow introduction candidates (see scripts/find_intro_bridges.py),
    grouped by person and sorted by how many companies they can plausibly
    unlock -- a bridge person mutual with a Mango-side connector AND with a
    source-matched operator at more than one target company is a useful
    research lead for a "one conversation checks several doors" case.
    Mutual follows alone never establish a personal relationship or an
    intent to introduce."""
    session = get_session()
    try:
        companies = _load_all_companies(session)
        by_handle: dict[str, dict] = {}
        for company in companies:
            bridges = [bridge for operator in company.operators for bridge in (operator.bridges or [])]
            for stage in deduplicate_bridge_stages(bridges, list(company.outreach_logs or [])):
                key = stage["bridge_handle"].casefold()
                entry = by_handle.setdefault(
                    key,
                    {
                        "bridge_handle": stage["bridge_handle"],
                        "bridge_name": stage["bridge_name"],
                        "bridge_bio": next((bridge.bridge_bio for bridge in bridges if bridge.bridge_handle.casefold() == key), None),
                        "bridge_followers_count": stage["bridge_followers_count"],
                        "mango_owner_candidates": set(),
                        "companies": {},
                        "best_askability_rank": -1,
                        "best_stage_level": 0,
                        "exact_first_ask": stage["exact_first_ask"],
                        "fallback": stage["fallback"],
                    },
                )
                entry["mango_owner_candidates"].update(stage["mango_side_handles"])
                entry["best_askability_rank"] = max(entry["best_askability_rank"], stage["askability_rank"])
                entry["best_stage_level"] = max(entry["best_stage_level"], stage["level"])
                entry["companies"][company.company_id] = {
                    "company_id": company.company_id,
                    "company_name": company.name,
                    "operator_name": next((o.name for o in company.operators if o.name), None),
                    "stage": stage["code"],
                    "relationship_evidence": stage["evidence_note"],
                    "evidence_recency": stage["evidence_recency"],
                    "askability": stage["askability"],
                    "verification_status": stage["interaction_verification_status"],
                    "what_is_verified": "mutual follow edges" + (" and public interaction URL(s)" if stage["interaction_evidence_count"] else ""),
                    "what_remains_unverified": "real-world relationship, current contact, motivation and willingness to introduce",
                }
        results = []
        for entry in by_handle.values():
            target_companies = sorted(entry["companies"].values(), key=lambda c: c["company_name"])
            results.append(
                {
                    "bridge_handle": entry["bridge_handle"],
                    "bridge_name": entry["bridge_name"],
                    "bridge_bio": entry["bridge_bio"],
                    "bridge_followers_count": entry["bridge_followers_count"],
                    "mango_owner_candidates": sorted(entry["mango_owner_candidates"]),
                    "companies": target_companies,
                    "company_count": len(target_companies),
                    "askability_rank": entry["best_askability_rank"],
                    "relationship_stage_level": entry["best_stage_level"],
                    "current_status": "relationship_candidate" if entry["best_stage_level"] >= 2 else "verification_queue",
                    "exact_first_ask": entry["exact_first_ask"],
                    "social_cost_risk": "Do not request an introduction before Mango ownership and relationship are confirmed",
                    "fallback": entry["fallback"],
                }
            )
        # Relationship/askability first. Company count and follower count
        # are displayed context only and never sorting signals.
        results.sort(
            key=lambda row: (-row["askability_rank"], -row["relationship_stage_level"], row["bridge_handle"].casefold())
        )
        return {"results": results, "interaction_audit": interaction_audit_summary(companies)}
    finally:
        session.close()


@router.get("/network/operators")
def network_operators(confirmed_only: bool = False):
    """Default changed to False (2026-08-28, section III): hiding every
    operator whose identity isn't yet human-verified hid real, useful
    contacts (e.g. Cursor's Lee Robinson, can_dm=True but
    identity_confirmed=False) instead of showing them with an honest
    "待人工核实" status. Identity verification is a displayed fact per
    row now, not a filter on whether the row appears."""
    session = get_session()
    try:
        q = session.query(Operator).options(joinedload(Operator.company))
        if confirmed_only:
            q = q.filter(Operator.identity_confirmed.is_(True))
        operators = [o for o in q.all() if o.name]
        return {
            "results": [
                {
                    "id": o.id,
                    "name": o.name,
                    "role": o.role,
                    "x_handle": o.x_handle,
                    "company_id": o.company_id,
                    "company_name": o.company.name,
                    "identity_confirmed": o.identity_confirmed,
                    "identity_source_matched": bool(o.identity_confirmed),
                    "identity_human_verified": bool(o.identity_human_verified_at),
                    "human_identity_verified": bool(o.identity_human_verified_at),
                    "identity_human_verified_at": o.identity_human_verified_at.isoformat() if o.identity_human_verified_at else None,
                    "identity_confirmed_metadata": {
                        "deprecated": True,
                        "semantic": "official_role_and_rapid_x_exact_profile_source_match",
                        "does_not_mean": "mango_human_identity_verification",
                        "replacement_field": "identity_source_matched",
                    },
                    "role_human_verified": bool(o.role_human_verified_at),
                    "role_human_verified_at": o.role_human_verified_at.isoformat() if o.role_human_verified_at else None,
                    "x_account_verified": "rapid_x_exact_profile" in (o.identity_status or ""),
                    "can_dm": o.can_dm,
                    "dm_status": (
                        "verified_open"
                        if o.can_dm and o.can_dm_checked_at
                        else "checked_not_open"
                        if o.can_dm_checked_at
                        else "unknown"
                    ),
                    "budget_authority_confirmed": o.budget_authority_confirmed,
                    "budget_authority_verified_at": o.budget_authority_verified_at.isoformat() if o.budget_authority_verified_at else None,
                    "last_verified_at": o.last_verified_at.isoformat() if o.last_verified_at else None,
                }
                for o in operators
            ]
        }
    finally:
        session.close()


@router.get("/network/sponsor-intelligence-prospects")
def network_sponsor_intelligence_prospects():
    """Person-centred sponsor-side context leads, explicitly not intro-ready."""
    session = get_session()
    try:
        creators = (
            session.query(Creator)
            .options(
                joinedload(Creator.contacts),
                joinedload(Creator.rate_cards),
                joinedload(Creator.sponsorships).joinedload(SponsorshipEvidence.company),
            )
            .all()
        )
        prospects, media_channels = sponsor_side_intelligence_prospects(creators)
        return {
            "results": prospects,
            "media_channels": media_channels,
            "semantic_note": (
                "Historical sponsorship may justify a low-cost information ask. "
                "It does not prove sponsor-side operator access or willingness to introduce."
            ),
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Home / action map
# ---------------------------------------------------------------------------


_HOME_EXECUTION_RANK = {
    "需要跟进": 6,
    "现在联系": 5,
    "等待回复": 4,
    "等待内部核实": 3,
    "本周准备": 2,
    "暂缓": 1,
    "放弃": 0,
}
_HOME_BUSINESS_RANK = {"High": 3, "Medium": 2, "Low": 1}
_TERMINAL_ACTION_STATUSES = frozenset({"done", "blocked"})


def _business_today(now: dt.datetime | None = None) -> dt.date:
    """Return Mango's operating date, independent of the host's UTC day."""

    timezone_name = (os.environ.get("MANGO_BUSINESS_TIMEZONE") or "Asia/Shanghai").strip()
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name != "Asia/Shanghai":
            raise RuntimeError(f"Unknown MANGO_BUSINESS_TIMEZONE: {timezone_name}")
        # Some slim images omit IANA tzdata. Shanghai has no current DST, so
        # preserve the intended default boundary without a network dependency.
        timezone = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")
    instant = now or dt.datetime.now(dt.timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=dt.timezone.utc)
    return instant.astimezone(timezone).date()


def _is_home_operational_summary(row: dict) -> bool:
    """Do not resurrect terminal ActionItems through a computed score.

    A real non-void outreach continuation may legitimately outlive the
    ActionItem it was linked to.  That persisted event is the only exception;
    ``execution_priority`` is recommendation/ranking output, never workflow
    state and therefore deliberately does not participate in this predicate.
    """

    return bool(
        row.get("action_status") not in _TERMINAL_ACTION_STATUSES
        or row.get("has_active_outreach_continuation")
    )


def _home_action_sort_key(row: dict, *, today: dt.date | None = None) -> tuple:
    """Put overdue/today work ahead of future/unscheduled research.

    A release planning date is operational state, not business evidence.  It
    therefore decides *when* a card is surfaced; execution and business
    priority decide ordering only within the same time bucket.
    """

    today = today or _business_today()
    try:
        due = dt.date.fromisoformat(row.get("due_date") or "")
    except (TypeError, ValueError):
        due = None
    due_bucket = 0 if due is not None and due <= today else 1 if due is not None else 2
    return (
        due_bucket,
        -_HOME_EXECUTION_RANK.get(row.get("execution_priority"), 0),
        -_HOME_BUSINESS_RANK.get(row.get("priority"), -1),
        due or dt.date.max,
        (row.get("name") or "").casefold(),
    )


@router.get("/commercial/priority")
def commercial_priority_companies():
    """The company-first priority set, ranked without relationship inputs."""
    session = get_session()
    try:
        companies = [
            company
            for company in _load_all_companies(session)
            if company.research_dossier is not None
        ]
        companies.sort(key=lambda company: company.research_dossier.priority_rank)
        return {
            "count": len(companies),
            "scoring_contract": "Commercial priority excludes X reachability, mutual follows, follower count, fame, and fundraising amount.",
            "companies": [company_summary(company) for company in companies],
        }
    finally:
        session.close()


@router.get("/commercial/longlist")
def commercial_company_longlist(
    disposition: list[str] | None = Query(None),
    icp_type: list[str] | None = Query(None),
):
    session = get_session()
    try:
        companies = [company for company in _load_all_companies(session) if company.longlist_assessment]
        if disposition:
            allowed = {value.casefold() for value in disposition}
            companies = [company for company in companies if company.longlist_assessment.disposition.casefold() in allowed]
        if icp_type:
            allowed = {value.casefold() for value in icp_type}
            companies = [company for company in companies if company.longlist_assessment.icp_type.casefold() in allowed]
        companies.sort(
            key=lambda company: (
                -(company.longlist_assessment.commercial_priority_score or 0),
                company.name.casefold(),
            )
        )
        return {
            "count": len(companies),
            "companies": [company_summary(company) for company in companies],
        }
    finally:
        session.close()


@router.get("/commercial/creator-sponsor-graph")
def commercial_creator_sponsor_graph():
    session = get_session()
    try:
        rows = (
            session.query(SponsorshipEvidence)
            .options(
                joinedload(SponsorshipEvidence.company),
                joinedload(SponsorshipEvidence.creator).joinedload(Creator.rate_cards),
            )
            .filter(SponsorshipEvidence.review_status != "rejected")
            .all()
        )
        grouped: dict[str, dict] = {}
        for evidence in rows:
            key = f"creator:{evidence.creator_id}" if evidence.creator_id else f"raw:{evidence.creator_name_raw.casefold()}"
            creator = evidence.creator
            item = grouped.setdefault(
                key,
                {
                    "creator_id": evidence.creator_id,
                    "creator": creator.display_name if creator else evidence.creator_name_raw,
                    "creator_class": creator.creator_class if creator else "Unknown",
                    "sponsors": {},
                    "rates": [
                        {
                            "platform": rate.platform,
                            "deliverable": rate.deliverable,
                            "currency": rate.quote_currency,
                            "amount": rate.quote_amount,
                            "amount_min": rate.quote_amount_min,
                            "amount_max": rate.quote_amount_max,
                            "is_confident": rate.is_confident,
                        }
                        for rate in (creator.rate_cards if creator else [])
                    ],
                },
            )
            sponsor = item["sponsors"].setdefault(
                evidence.company_id,
                {
                    "company_id": evidence.company_id,
                    "company": evidence.company.name if evidence.company else evidence.company_id,
                    "observations": [],
                },
            )
            sponsor["observations"].append(sponsorship_json(evidence))

        graph = []
        for item in grouped.values():
            sponsors = list(item.pop("sponsors").values())
            observation_count = sum(len(sponsor["observations"]) for sponsor in sponsors)
            graph.append(
                {
                    **item,
                    "sponsor_count": len(sponsors),
                    "observation_count": observation_count,
                    "sponsors": sponsors,
                    "repeated_cooperation": any(len(sponsor["observations"]) >= 2 for sponsor in sponsors),
                    "mango_relationship": "rate_on_file" if item["rates"] else "unknown",
                    "possible_commercial_relevance": "Historical campaign evidence may support a process-intelligence ask; willingness to introduce is not inferred.",
                }
            )
        graph.sort(key=lambda item: (-item["sponsor_count"], -item["observation_count"], item["creator"].casefold()))
        return {"count": len(graph), "creators": graph}
    finally:
        session.close()


@router.get("/commercial/gtm-cases")
def commercial_gtm_cases():
    session = get_session()
    try:
        rows = session.query(GtmCase).order_by(GtmCase.id).all()
        return {
            "count": len(rows),
            "cases": [
                {
                    "company_id": row.company_id,
                    "company": row.company_name,
                    "category": row.category,
                    "maturity_stage": row.maturity_stage,
                    "gtm_motion": row.gtm_motion,
                    "spend_classification": row.spend_classification,
                    "what_mango_should_copy": row.what_mango_should_copy,
                    "what_not_to_copy": row.what_not_to_copy,
                }
                for row in rows
            ],
        }
    finally:
        session.close()


@router.get("/home/summary")
def home_summary():
    session = get_session()
    try:
        companies = _load_all_companies(session)
        summaries = [company_summary(c) for c in companies]
        companies_by_id = {c.company_id: c for c in companies}

        commercial_priority = sorted(
            (row for row in summaries if (row.get("commercial_research") or {}).get("is_priority")),
            key=lambda row: (row["commercial_research"]["priority_rank"], row["name"].casefold()),
        )

        # The Home module is an operational queue.  Due/overdue work must be
        # visible before tomorrow's cards; execution and business value then
        # break ties.  This keeps the three 2026-08-29 internal checks ahead
        # of future-dated cold-contact/research work.
        all_ordered_summaries = sorted(summaries, key=_home_action_sort_key)
        ordered_summaries = [row for row in all_ordered_summaries if _is_home_operational_summary(row)]
        top_actions = []
        connector_checks = []
        for row in ordered_summaries:
            if len(top_actions) >= 13 and len(connector_checks) >= 5:
                break
            company = companies_by_id[row["company_id"]]
            suggested = suggested_bridge_action(company)
            if len(connector_checks) < 5:
                candidates = deduplicate_bridge_stages(
                    [bridge for operator in company.operators for bridge in (operator.bridges or [])],
                    list(company.outreach_logs or []),
                )
                candidates = [candidate for candidate in candidates if not candidate["disproven"]]
                # One card is one actual internal conversation: the same
                # target company can correctly appear twice when Solomon
                # and the Mango account each need to check a different set
                # of mutual-follow candidates. Candidates remain a side-by-
                # side comparison; no single person is auto-selected.
                for asker in sorted(
                    {handle for candidate in candidates for handle in candidate.get("mango_side_handles", [])}
                ):
                    asker_candidates = [
                        candidate for candidate in candidates if asker in candidate.get("mango_side_handles", [])
                    ]
                    connector_checks.append(
                        {
                            "company_id": company.company_id,
                            "company_name": company.name,
                            "execution_priority": row["execution_priority"],
                            "business_priority": row["priority"],
                            "asker": asker,
                            "candidates": [
                                {
                                    "handle": candidate["bridge_handle"],
                                    "name": candidate["bridge_name"],
                                    "stage": candidate["code"],
                                    "evidence_note": candidate["evidence_note"],
                                    "suggested_question": candidate["suggested_question"],
                                }
                                for candidate in asker_candidates[:3]
                            ],
                        }
                    )
                    if len(connector_checks) >= 5:
                        break
            if (suggested or row.get("action_id") is not None or row.get("has_active_outreach_continuation")) and len(top_actions) < 13:
                direct = (suggested or {}).get("direct")
                identified = row.get("identified_operator")
                top_actions.append(
                    {
                        "company_id": company.company_id,
                        "company_name": company.name,
                        # Workflow state is the persisted ActionItem status;
                        # the computed recommendation keeps its own explicit
                        # execution_priority name and never masquerades as a
                        # status field.
                        "action_id": row.get("action_id"),
                        "action_status": row.get("action_status"),
                        "current_work_kind": row.get("current_work_kind"),
                        "current_workflow_status": row.get("current_workflow_status"),
                        "execution_status": row.get("current_workflow_status"),
                        "execution_priority": row["execution_priority"],
                        "business_priority": row["priority"],
                        "relationship_stage": row["relationship_stage_code"],
                        "execution_wave": (
                            row.get("action_wave")
                            if row.get("current_work_kind") in {"action_item", "release_internal_check"}
                            else None
                        ),
                        # A release-day internal verification task was
                        # scheduled against the stored ActionItem.  A cold
                        # DM discovered by the live graph remains an
                        # independent channel/fallback and cannot silently
                        # replace the exact internal ask.
                        "primary_next_action": row["next_action"],
                        "next_action_source": row.get("next_action_source"),
                        "action_plan": suggested,
                        "contact_name": (direct or {}).get("name") or (identified or {}).get("name"),
                        "contact_handle": (direct or {}).get("handle") or (identified or {}).get("x_handle"),
                        "direct_channel": (direct or {}).get("channel"),
                        "channel_access": row.get("channel_access"),
                        "fallback": row.get("current_fallback"),
                        # company_summary already reconciles mutable execution
                        # state: the latest non-void outreach log wins when it
                        # carries an owner/follow-up date, otherwise the
                        # historical ActionItem remains the fallback.
                        "owner": row["owner"],
                        "due_date": row["due_date"],
                        "due_date_source": row.get("due_date_source"),
                        "planned_work_type": row.get("planned_work_type"),
                        "created_at": row.get("current_work_created_at"),
                        "is_fresh": row.get("next_action_is_fresh", False),
                    }
                )

        # A campaign proposal doesn't require a warm relationship to exist
        # first -- it's frequently the door-opener itself. Requiring
        # priority=="High" here used to mean this list needed both real
        # spend evidence AND a verified bridge/DM at the same time, which
        # zeroed the list out entirely on 2026-08-28 (the four "High"
        # companies had no paid sponsorship history; the three with real
        # paid history had been demoted to Medium by the reachability
        # gate). Campaign readiness is just: real proof this company pays
        # for creator content, so Mango has something concrete to pitch.
        campaign_ready = [r for r in all_ordered_summaries if r["paid_sponsorship_count"] > 0][:8]
        recent_paid = (
            session.query(SponsorshipEvidence)
            .options(joinedload(SponsorshipEvidence.company), joinedload(SponsorshipEvidence.creator))
            .filter(
                SponsorshipEvidence.disclosure_type == "paid_sponsorship",
                SponsorshipEvidence.review_status != "rejected",
            )
            .order_by(SponsorshipEvidence.published_at.desc())
            .limit(8)
            .all()
        )

        # Solomon-facing portfolio. Every row is already serialized from the
        # same canonical decision used by Opportunities and Company detail.
        canonical_ordered = sorted(
            summaries,
            key=lambda row: (
                -{"pursue_now": 4, "prepare": 3, "watch": 2, "archive": 1}.get(
                    row["canonical_decision"]["decision_bucket"], 0
                ),
                -row["canonical_decision"]["internal_sort_score"],
                row["name"].casefold(),
            ),
        )
        pursue_candidates = [
            row for row in canonical_ordered
            if row["canonical_decision"]["decision_bucket"] == "pursue_now"
        ]
        pursue_now = sorted(
            pursue_candidates,
            key=lambda row: (
                -{"contact_now": 3, "prepare_proposal": 2, "research_buyer_route": 1}.get(
                    row["canonical_decision"]["execution_readiness"], 0
                ),
                -row["canonical_decision"]["internal_sort_score"],
                row["name"].casefold(),
            ),
        )[:5]
        prepare_next = [
            row for row in canonical_ordered
            if row["canonical_decision"]["decision_bucket"] == "prepare"
        ][:8]
        watch_investigate = [
            row for row in canonical_ordered
            if row["canonical_decision"]["decision_bucket"] == "watch"
        ][:8]
        asia_opportunities = [
            row for row in canonical_ordered
            if row["canonical_decision"]["flags"]["has_asia_signal"]
        ]

        return {
            "decision_portfolio": {
                "pursue_now": pursue_now,
                "today_top_three": pursue_now[:3],
                "pursue_queue": pursue_now[3:5],
                "prepare_next": prepare_next,
                "watch_investigate": watch_investigate,
                "asia_opportunities": asia_opportunities,
            },
            "commercial_priority": commercial_priority,
            "top_priority_companies": ordered_summaries[:13],
            "top_actions": top_actions,
            "connector_checks": connector_checks,
            "campaign_ready": campaign_ready,
            "recent_paid_evidence": [sponsorship_json(s) for s in recent_paid],
            "totals": {
                "companies": len(companies),
                "commercial_priority_companies": len(commercial_priority),
                "commercial_longlist_companies": sum(1 for c in companies if c.longlist_assessment),
                # "Identified" (a named target contact on file) is a
                # different, broader question than "identity verified" --
                # see Operator card's per-field verification status
                # (2026-08-28, section III). This counts anyone with a
                # name, not just identity_confirmed rows.
                "identified_operators": sum(1 for c in companies for o in c.operators if o.name),
                "paid_sponsorships": sum(
                    1
                    for c in companies
                    for s in c.sponsorships
                    if s.disclosure_type == "paid_sponsorship" and s.review_status != "rejected"
                ),
                "confirmed_paid_sponsorships": sum(
                    1
                    for c in companies
                    for s in c.sponsorships
                    if s.disclosure_type == "paid_sponsorship" and s.review_status == "confirmed"
                ),
                "unreviewed_paid_sponsorships": sum(
                    1
                    for c in companies
                    for s in c.sponsorships
                    if s.disclosure_type == "paid_sponsorship" and s.review_status == "unreviewed"
                ),
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


# ---------------------------------------------------------------------------
# Manual correction / execution tracking (data-integrity sprint)
#
# Deliberately minimal: edit-in-place on existing rows plus the handful of
# "add one more" actions Solomon actually needs (a new operator, a new intro
# path, a new action item). No approval workflow, no audit-log UI, no
# generic admin CRUD -- every write here is a direct, explained persistence
# of one specific correction, verified against the real DB below.
# ---------------------------------------------------------------------------


class CompanyEdit(BaseModel):
    category: str | None = None
    geography: str | None = None
    why_now: str | None = None
    budget_evidence: str | None = None
    buyer_or_route: str | None = None
    internal_notes: str | None = None


@router.patch("/companies/{company_id}")
def edit_company(company_id: str, body: CompanyEdit):
    """Editing a company here always sets last_verified_at -- a human
    looking at this record and confirming/correcting it is exactly what
    "verified" means."""
    session = get_session()
    try:
        company = _load_company(session, company_id)
        # category/geography edits from the UI are Chinese text (that's what
        # the form displays) -- write them to the _zh display column, not
        # the English column bd_compute.creator_campaign_fit() matches on.
        field_targets = {"category": "category_zh", "geography": "geography_zh"}
        for field in ("category", "geography", "why_now", "budget_evidence", "buyer_or_route", "internal_notes"):
            value = getattr(body, field)
            if value is not None:
                setattr(company, field_targets.get(field, field), value or None)
        company.last_verified_at = dt.datetime.utcnow()
        session.commit()
        return company_detail(_load_company(session, company_id))
    finally:
        session.close()


@router.post("/companies/{company_id}/verify")
def verify_company(company_id: str):
    """Nothing needed correcting -- just record that a human checked this
    company's data and it's still accurate."""
    session = get_session()
    try:
        company = _load_company(session, company_id)
        company.last_verified_at = dt.datetime.utcnow()
        session.commit()
        return {"company_id": company_id, "last_verified_at": company.last_verified_at.isoformat()}
    finally:
        session.close()


class OperatorEdit(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = None
    role: str | None = None
    x_handle: str | None = None
    identity_human_verified: bool | None = None
    role_human_verified: bool | None = None
    budget_authority_confirmed: bool | None = None
    evidence_urls: str | None = None


class OperatorCreate(OperatorEdit):
    name: str


def _operator_evidence_urls(raw: str | None) -> list[str]:
    return [
        line.strip()
        for line in (raw or "").splitlines()
        if line.strip().lower().startswith(("https://", "http://"))
    ]


def _apply_operator_edit(operator: Operator, body: OperatorEdit, *, verified_at: dt.datetime | None = None) -> None:
    """Apply an operator edit without turning an ordinary edit into proof.

    Source matching (`identity_confirmed`) remains pipeline-owned and is never
    writable here. Human identity/role/budget checks are independent explicit
    facts, require cited evidence, and keep separate timestamps.
    """
    checked_at = verified_at or dt.datetime.utcnow()
    prospective_name = body.name.strip() if body.name is not None else operator.name
    prospective_role = body.role.strip() if body.role is not None else operator.role
    prospective_handle = body.x_handle.strip().lstrip("@") if body.x_handle is not None else operator.x_handle
    prospective_evidence = body.evidence_urls if body.evidence_urls is not None else operator.evidence_urls
    evidence_urls = _operator_evidence_urls(prospective_evidence)

    name_changed = body.name is not None and prospective_name != operator.name
    role_changed = body.role is not None and prospective_role != operator.role
    handle_changed = body.x_handle is not None and prospective_handle != operator.x_handle
    evidence_changed = body.evidence_urls is not None and prospective_evidence != operator.evidence_urls
    identity_fact_changed = name_changed or handle_changed or evidence_changed
    role_fact_changed = role_changed or evidence_changed
    budget_fact_changed = name_changed or role_changed or handle_changed or evidence_changed

    if body.identity_human_verified:
        if not prospective_name or not prospective_handle or not evidence_urls:
            raise HTTPException(422, "人工核实身份必须填写姓名、X 账号和至少一条公开证据 URL")
        # "Human verified identity" is the final review of a two-source
        # identity, not a way to bypass exact-profile resolution. A new or
        # edited handle/name/evidence invalidates the pipeline match below;
        # require the research pipeline to re-match the official source and
        # exact Rapid X profile before a reviewer can attest the identity.
        exact_x_matched = "rapid_x_exact_profile" in (operator.identity_status or "")
        if identity_fact_changed or not operator.identity_confirmed or not exact_x_matched:
            raise HTTPException(
                422,
                "人工核实身份前，必须先完成官网/公司来源与 Rapid X exact profile 双源匹配；修改姓名、X 账号或证据后请先重新解析",
            )
    if body.role_human_verified:
        if not prospective_role or not evidence_urls:
            raise HTTPException(422, "人工核实当前职位必须填写职位和至少一条公开证据 URL")
    if body.budget_authority_confirmed:
        if not evidence_urls:
            raise HTTPException(422, "确认预算影响力必须填写至少一条公开或内部可回查证据 URL")

    operator.name = prospective_name or None
    operator.role = prospective_role or None
    operator.x_handle = prospective_handle or None
    if body.evidence_urls is not None:
        operator.evidence_urls = "\n".join(evidence_urls) or None

    # X profile facts belong to the exact handle that was queried.  Keeping
    # them after a handle edit would make the new account inherit the old
    # account's rest id / DM permission and could create a false executable
    # outreach channel.  The enrichment pipeline must re-resolve the new
    # exact profile before any of these facts become usable again.
    if handle_changed:
        operator.x_rest_id = None
        operator.can_dm = None
        operator.can_dm_checked_at = None

    # A human edit to any source-matched identity component invalidates the
    # old automated double-source match until the research pipeline rechecks
    # the new values. The UI cannot write this flag back to True.
    if name_changed or role_changed or handle_changed or evidence_changed:
        operator.identity_confirmed = False
        operator.identity_status = "human_edited_needs_source_rematch"

    if body.identity_human_verified is not None:
        operator.identity_human_verified_at = (
            checked_at
            if body.identity_human_verified and (operator.identity_human_verified_at is None or identity_fact_changed)
            else operator.identity_human_verified_at
            if body.identity_human_verified
            else None
        )
    elif identity_fact_changed:
        operator.identity_human_verified_at = None

    if body.role_human_verified is not None:
        operator.role_human_verified_at = (
            checked_at
            if body.role_human_verified and (operator.role_human_verified_at is None or role_fact_changed)
            else operator.role_human_verified_at
            if body.role_human_verified
            else None
        )
    elif role_fact_changed:
        operator.role_human_verified_at = None

    if body.budget_authority_confirmed is not None:
        operator.budget_authority_confirmed = body.budget_authority_confirmed
        operator.budget_authority_verified_at = checked_at if body.budget_authority_confirmed else None
    elif budget_fact_changed:
        operator.budget_authority_confirmed = False
        operator.budget_authority_verified_at = None

    verification_times = [
        value
        for value in (
            operator.identity_human_verified_at,
            operator.role_human_verified_at,
            operator.budget_authority_verified_at,
        )
        if value is not None
    ]
    operator.last_verified_at = max(verification_times) if verification_times else None


@router.post("/companies/{company_id}/operators")
def create_operator(company_id: str, body: OperatorCreate):
    session = get_session()
    try:
        _load_company(session, company_id)  # 404s if the company doesn't exist
        operator = Operator(
            company_id=company_id,
            name=body.name,
            role=body.role,
            x_handle=body.x_handle,
            identity_confirmed=False,
            identity_status="human_added_needs_source_match",
            budget_authority_confirmed=False,
            evidence_urls=body.evidence_urls,
        )
        _apply_operator_edit(operator, body)
        session.add(operator)
        session.commit()
        return company_detail(_load_company(session, company_id))
    finally:
        session.close()


@router.patch("/operators/{operator_id}")
def edit_operator(operator_id: int, body: OperatorEdit):
    session = get_session()
    try:
        operator = session.query(Operator).filter(Operator.id == operator_id).one_or_none()
        if operator is None:
            raise HTTPException(404, f"Operator {operator_id} not found")
        _apply_operator_edit(operator, body)
        session.commit()
        return company_detail(_load_company(session, operator.company_id))
    finally:
        session.close()


class IntroPathEdit(BaseModel):
    target_type: str | None = None  # company_account | operator_person
    degree_label: str | None = None  # direct | secondary | third
    connector_handle: str | None = None
    path_labels: str | None = None
    graph_reachable: bool | None = None
    human_intro_status: str | None = None


class IntroPathCreate(IntroPathEdit):
    target_type: str
    root: str = "mango"


@router.post("/companies/{company_id}/intro-paths")
def create_intro_path(company_id: str, body: IntroPathCreate):
    # IntroPath is an observed X-graph artifact.  Letting a form/API caller
    # invent labels or set graph_reachable would immediately manufacture an
    # E1 relationship signal without Rapid X evidence or edge direction.
    # Real human relationship progress belongs in attributed OutreachLog;
    # public graph corrections must be re-imported through the evidence
    # pipeline, which preserves source id, cache provenance and direction.
    raise HTTPException(
        409,
        "X 图谱路径为只读研究证据：请通过 Rapid X 研究管线重新采集；真实认识/引荐进展请写入带归因的 OutreachLog",
    )


@router.patch("/intro-paths/{path_id}")
def edit_intro_path(path_id: int, body: IntroPathEdit):
    raise HTTPException(
        409,
        "X 图谱路径为只读研究证据：不得在 API 中改写可达性、节点或关注方向；请重新采集并保留证据 provenance",
    )


class ActionItemEdit(BaseModel):
    owner: str | None = None
    status: str | None = None  # open | in_progress | done | blocked
    due_date: str | None = None
    primary_next_action: str | None = None
    fallback: str | None = None
    success_condition: str | None = None
    outcome_notes: str | None = None

    @model_validator(mode="after")
    def validate_operational_invariants(self):
        fields = self.model_fields_set
        for field in ("owner", "fallback", "primary_next_action"):
            if field not in fields:
                continue
            value = getattr(self, field)
            if value is None or not value.strip():
                raise ValueError(f"{field} must not be blank")
            setattr(self, field, value.strip())
        if "status" in fields:
            if self.status not in ACTION_ITEM_STATUSES:
                raise ValueError(f"status must be one of {ACTION_ITEM_STATUSES}")
        if "due_date" in fields and self.due_date is not None:
            due = self.due_date.strip()
            try:
                parsed = dt.date.fromisoformat(due)
            except ValueError as exc:
                raise ValueError("due_date must be YYYY-MM-DD") from exc
            if parsed.isoformat() != due:
                raise ValueError("due_date must be YYYY-MM-DD")
            self.due_date = due
        return self


class ActionItemCreate(ActionItemEdit):
    owner: str
    primary_next_action: str
    fallback: str
    due_date: str
    execution_wave: int | None = None


@router.post("/companies/{company_id}/action-items")
def create_action_item(company_id: str, body: ActionItemCreate):
    session = get_session()
    try:
        _load_company(session, company_id)
        item = ActionItem(
            company_id=company_id,
            execution_wave=body.execution_wave,
            owner=body.owner,
            primary_next_action=body.primary_next_action,
            fallback=body.fallback,
            success_condition=body.success_condition,
            status=body.status or "open",
            due_date=body.due_date,
            outcome_notes=body.outcome_notes,
        )
        session.add(item)
        session.commit()
        return company_detail(_load_company(session, company_id))
    finally:
        session.close()


@router.patch("/action-items/{item_id}")
def edit_action_item(item_id: int, body: ActionItemEdit):
    session = get_session()
    try:
        item = session.query(ActionItem).filter(ActionItem.id == item_id).one_or_none()
        if item is None:
            raise HTTPException(404, f"ActionItem {item_id} not found")
        for field in ("owner", "status", "due_date", "primary_next_action", "fallback", "success_condition", "outcome_notes"):
            value = getattr(body, field)
            if value is not None:
                setattr(item, field, value)
        session.commit()
        return company_detail(_load_company(session, item.company_id))
    finally:
        session.close()


class OutreachLogCreate(BaseModel):
    stage: str  # one of OUTREACH_STAGES
    notes: str | None = None
    operator_id: int | None = None  # target operator this event is about
    bridge_id: int | None = None  # connector/bridge candidate used, if any
    intro_path_id: int | None = None
    linked_action_item_id: int | None = None
    owner: str | None = None  # who at Mango did this
    contacted_who: str | None = None  # free text: name/handle actually reached
    contact_channel: str | None = None  # one of CONTACT_CHANNELS
    evidence_url: str | None = None
    next_follow_up_date: str | None = None


_BRIDGE_REQUIRED_OUTREACH_STAGES = {
    "connector_replied",
    "relationship_rejected",
    "intro_accepted",
    "intro_made",
}


def _validate_outreach_context(session, company_id: str, body: OutreachLogCreate) -> dict[str, int | None]:
    """Validate every optional FK and the E4-E6 attribution contract.

    The public endpoint accepts integer ids, so relying on SQLite foreign-key
    existence alone is insufficient: a valid id from another company would
    otherwise attach an event to the wrong relationship graph. This helper is
    pure validation/inference; it does not add or modify rows.
    """
    operator = session.get(Operator, body.operator_id) if body.operator_id is not None else None
    if body.operator_id is not None and (operator is None or operator.company_id != company_id):
        raise HTTPException(422, "operator_id 不存在或不属于当前公司")

    bridge = session.get(IntroBridge, body.bridge_id) if body.bridge_id is not None else None
    if body.bridge_id is not None and (bridge is None or bridge.company_id != company_id):
        raise HTTPException(422, "bridge_id 不存在或不属于当前公司")
    if bridge is not None:
        if operator is not None and operator.id != bridge.operator_id:
            raise HTTPException(422, "operator_id 与 bridge_id 指向的目标联系人不一致")
        if operator is None:
            operator = session.get(Operator, bridge.operator_id)

    intro_path = session.get(IntroPath, body.intro_path_id) if body.intro_path_id is not None else None
    if body.intro_path_id is not None and (intro_path is None or intro_path.company_id != company_id):
        raise HTTPException(422, "intro_path_id 不存在或不属于当前公司")

    action = session.get(ActionItem, body.linked_action_item_id) if body.linked_action_item_id is not None else None
    if body.linked_action_item_id is not None and (action is None or action.company_id != company_id):
        raise HTTPException(422, "linked_action_item_id 不存在或不属于当前公司")

    if not (body.owner or "").strip():
        raise HTTPException(422, "执行结果必须填写 Mango 执行人，才能知道由谁完成了这次动作")
    if not body.contact_channel:
        raise HTTPException(422, "执行结果必须填写联系渠道")
    if operator is None and bridge is None and not (body.contacted_who or "").strip():
        raise HTTPException(422, "执行结果必须关联目标联系人/引荐候选人，或填写实际联系的公司账号/对象")

    if body.stage in _BRIDGE_REQUIRED_OUTREACH_STAGES and bridge is None:
        raise HTTPException(422, f"{body.stage} 必须关联具体 bridge_id，不能记录成公司级结论")
    if body.stage in {"relationship_confirmed", "target_replied"} and operator is None:
        raise HTTPException(422, f"{body.stage} 必须关联具体 operator_id 或 bridge_id")

    # A reply to a direct DM is still a valid target_replied funnel event,
    # but it must not become E6. When a bridge is explicitly selected, require
    # the prior intro_made record that makes "intro happened + target replied"
    # true for that exact bridge.
    if body.stage == "target_replied" and bridge is not None:
        prior_intro = (
            session.query(OutreachLog)
            .filter(
                OutreachLog.company_id == company_id,
                OutreachLog.bridge_id == bridge.id,
                OutreachLog.stage == "intro_made",
                OutreachLog.voided.is_(False),
            )
            .first()
        )
        if prior_intro is None:
            raise HTTPException(422, "该 bridge 尚无 intro_made 记录；先记录引荐已发出，目标回复后才能进入 E6")

    return {
        "operator_id": operator.id if operator is not None else None,
        "bridge_id": bridge.id if bridge is not None else None,
        "intro_path_id": intro_path.id if intro_path is not None else None,
        "linked_action_item_id": action.id if action is not None else None,
    }


@router.post("/companies/{company_id}/outreach-logs")
def create_outreach_log(company_id: str, body: OutreachLogCreate):
    """Records one real-world outreach event -- see OutreachLog's
    docstring. This is the only way any funnel-outcome data enters the
    system; nothing here is inferred, only what a human logs. Answers
    "who contacted whom, through which path, with what result" -- not
    just a company-level note (section IV, 2026-08-28)."""
    if body.stage not in OUTREACH_STAGES:
        raise HTTPException(422, f"stage must be one of {OUTREACH_STAGES}")
    if body.contact_channel and body.contact_channel not in CONTACT_CHANNELS:
        raise HTTPException(422, f"contact_channel must be one of {CONTACT_CHANNELS}")
    session = get_session()
    try:
        _load_company(session, company_id)
        context = _validate_outreach_context(session, company_id, body)
        log = OutreachLog(
            company_id=company_id,
            operator_id=context["operator_id"],
            bridge_id=context["bridge_id"],
            intro_path_id=context["intro_path_id"],
            linked_action_item_id=context["linked_action_item_id"],
            owner=body.owner.strip(),
            contacted_who=(body.contacted_who or "").strip() or None,
            contact_channel=body.contact_channel,
            evidence_url=body.evidence_url,
            stage=body.stage,
            notes=body.notes,
            next_follow_up_date=body.next_follow_up_date,
        )
        session.add(log)
        session.commit()
        return company_detail(_load_company(session, company_id))
    finally:
        session.close()


class OutreachLogVoid(BaseModel):
    voided_reason: str


class OutreachLogEdit(BaseModel):
    """Operational corrections that do not rewrite event provenance.

    Company, operator, bridge, path, action, stage, channel, contacted party,
    evidence URL and occurrence time are deliberately absent.  A mistaken
    attribution must be voided and re-entered, preserving the audit trail.
    """

    model_config = {"extra": "forbid"}

    notes: str | None = None
    next_follow_up_date: str | None = None
    owner: str | None = None


def _apply_outreach_log_edit(log: OutreachLog, body: OutreachLogEdit) -> None:
    """Apply a bounded edit to a live outreach row, or raise HTTP 422."""

    if log.voided:
        raise HTTPException(422, "已作废的 OutreachLog 不能编辑；请新建一条更正记录")
    fields = set(body.model_fields_set)
    if not fields:
        raise HTTPException(422, "至少提供 notes、next_follow_up_date 或 owner 之一")

    if "owner" in fields:
        owner = (body.owner or "").strip()
        if not owner:
            raise HTTPException(422, "owner 不能为空；这是执行归因字段")
        log.owner = owner

    if "next_follow_up_date" in fields:
        if body.next_follow_up_date is None:
            log.next_follow_up_date = None
        else:
            due = body.next_follow_up_date.strip()
            try:
                parsed = dt.date.fromisoformat(due)
            except ValueError as exc:
                raise HTTPException(422, "next_follow_up_date 必须是 YYYY-MM-DD") from exc
            if parsed.isoformat() != due:
                raise HTTPException(422, "next_follow_up_date 必须是 YYYY-MM-DD")
            log.next_follow_up_date = due

    if "notes" in fields:
        log.notes = body.notes


@router.patch("/outreach-logs/{log_id}/void")
def void_outreach_log(log_id: int, body: OutreachLogVoid):
    """Corrects a mis-logged entry -- e.g. logged against the wrong
    company, or a test/mistaken entry -- WITHOUT deleting it. A voided row
    stays visible with its reason, is excluded from relationship-stage
    auto-progression (see bd_compute._apply_outreach_logs) and funnel
    learning, but the audit trail is never destroyed (2026-08-28, section
    IV: "删除改成可审计的纠错/作废机制，避免丢失历史")."""
    session = get_session()
    try:
        log = session.get(OutreachLog, log_id)
        if log is None:
            raise HTTPException(404, f"OutreachLog {log_id} not found")
        company_id = log.company_id
        log.voided = True
        log.voided_reason = body.voided_reason
        session.commit()
        return company_detail(_load_company(session, company_id))
    finally:
        session.close()


@router.patch("/outreach-logs/{log_id}")
def edit_outreach_log(log_id: int, body: OutreachLogEdit):
    """Edit only owner, notes or next follow-up on a non-void event.

    There is intentionally no hard-delete endpoint.  Use ``/void`` for a
    wrong event, company, target, bridge, evidence attribution, stage or
    occurrence time, then create the corrected event.  This preserves what
    changed without allowing a PATCH to rewrite historical evidence.
    """

    session = get_session()
    try:
        log = session.get(OutreachLog, log_id)
        if log is None:
            raise HTTPException(404, f"OutreachLog {log_id} not found")
        company_id = log.company_id
        _apply_outreach_log_edit(log, body)
        session.commit()
        return company_detail(_load_company(session, company_id))
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class SponsorshipReviewEdit(BaseModel):
    review_status: str  # confirmed | rejected
    reviewed_note: str | None = None


@router.patch("/sponsorship-evidence/{evidence_id}")
def review_sponsorship_evidence(evidence_id: int, body: SponsorshipReviewEdit):
    if body.review_status not in ("confirmed", "rejected"):
        raise HTTPException(422, "review_status must be 'confirmed' or 'rejected'")
    session = get_session()
    try:
        row = session.query(SponsorshipEvidence).filter(SponsorshipEvidence.id == evidence_id).one_or_none()
        if row is None:
            raise HTTPException(404, f"SponsorshipEvidence {evidence_id} not found")
        row.review_status = body.review_status
        row.reviewed_note = body.reviewed_note
        row.reviewed_at = dt.datetime.utcnow()
        session.commit()
        return sponsorship_json(row)
    finally:
        session.close()


class CreatorExclusionCreate(BaseModel):
    company_id: str | None = None  # None = global/category-wide exclusion
    reason: str


@router.post("/creators/{creator_id}/exclusions")
def create_creator_exclusion(creator_id: int, body: CreatorExclusionCreate):
    session = get_session()
    try:
        creator = session.query(Creator).filter(Creator.id == creator_id).one_or_none()
        if creator is None:
            raise HTTPException(404, f"Creator {creator_id} not found")
        exclusion = CreatorExclusion(creator_id=creator_id, company_id=body.company_id, reason=body.reason)
        session.add(exclusion)
        session.commit()
        return {"id": exclusion.id, "creator_id": creator_id, "company_id": body.company_id, "reason": body.reason}
    finally:
        session.close()


@router.get("/creators/{creator_id}/exclusions")
def list_creator_exclusions(creator_id: int):
    session = get_session()
    try:
        rows = session.query(CreatorExclusion).options(joinedload(CreatorExclusion.company)).filter(CreatorExclusion.creator_id == creator_id).all()
        return {
            "results": [
                {"id": r.id, "company_id": r.company_id, "company_name": r.company.name if r.company else None, "reason": r.reason}
                for r in rows
            ]
        }
    finally:
        session.close()


@router.delete("/creators/{creator_id}/exclusions/{exclusion_id}")
def delete_creator_exclusion(creator_id: int, exclusion_id: int):
    session = get_session()
    try:
        row = session.query(CreatorExclusion).filter(CreatorExclusion.id == exclusion_id, CreatorExclusion.creator_id == creator_id).one_or_none()
        if row is None:
            raise HTTPException(404, f"Exclusion {exclusion_id} not found for creator {creator_id}")
        session.delete(row)
        session.commit()
        return {"deleted": exclusion_id}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Solomon UAT review
# ---------------------------------------------------------------------------


def _solomon_review_json(r: SolomonReview) -> dict:
    return {
        "company_id": r.company_id,
        "company_name": r.company.name,
        "decision": r.decision,
        "intro_path_confirmed_real": r.intro_path_confirmed_real,
        "creator_suggestions_sellable": r.creator_suggestions_sellable,
        "missing_info": r.missing_info,
        "solomon_notes": r.solomon_notes,
        "next_action": r.next_action,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("/solomon/pilot-candidates")
def solomon_pilot_candidates():
    """Suggests real companies for the 4 UAT buckets the review pilot needs
    -- reachable+high-spend, high-spend-but-weak-reach, has real sponsorship
    evidence, and a control case the system says not to act on yet. Purely
    a picker aid built from already-computed scoring; selecting a company
    into the pilot happens by creating its SolomonReview row, not here."""
    session = get_session()
    try:
        companies = _load_all_companies(session)
        scored = [(c, opportunity_priority(c)) for c in companies]

        reachable_high_spend = [c for c, p in scored if p["label"] == "High" and p["reachability"]["level"] >= 3]
        # Opportunity and reachability are independent axes now (see
        # bd_compute.opportunity_priority) -- "Watchlist" no longer exists
        # as a blended label, so this bucket is reconstructed directly
        # from the two axes: strong business case, weak/no known path in.
        weak_reach_high_spend = [c for c, p in scored if p["label"] in ("High", "Medium") and p["reachability"]["level"] <= 2]
        has_sponsorship_evidence = [c for c, p in scored if len(c.sponsorships) > 0]
        control_dont_act_yet = [c for c, p in scored if p["label"] == "Low"]

        def brief(c):
            return {"company_id": c.company_id, "name": c.name, "priority": next(p["label"] for cc, p in scored if cc.company_id == c.company_id)}

        return {
            "reachable_high_spend": [brief(c) for c in reachable_high_spend[:10]],
            "weak_reach_high_spend": [brief(c) for c in weak_reach_high_spend[:10]],
            "has_sponsorship_evidence": [brief(c) for c in has_sponsorship_evidence[:10]],
            "control_dont_act_yet": [brief(c) for c in control_dont_act_yet[:10]],
        }
    finally:
        session.close()


@router.get("/solomon/reviews")
def list_solomon_reviews():
    session = get_session()
    try:
        rows = session.query(SolomonReview).options(joinedload(SolomonReview.company)).all()
        return {"results": [_solomon_review_json(r) for r in rows]}
    finally:
        session.close()


@router.get("/solomon/reviews/{company_id}")
def get_solomon_review(company_id: str):
    session = get_session()
    try:
        row = session.query(SolomonReview).options(joinedload(SolomonReview.company)).filter(SolomonReview.company_id == company_id).one_or_none()
        if row is None:
            raise HTTPException(404, f"No Solomon review for {company_id} yet")
        return _solomon_review_json(row)
    finally:
        session.close()


class SolomonReviewUpsert(BaseModel):
    decision: str | None = None  # proceed | watch | reject
    intro_path_confirmed_real: bool | None = None
    creator_suggestions_sellable: bool | None = None
    missing_info: str | None = None
    solomon_notes: str | None = None
    next_action: str | None = None


@router.put("/solomon/reviews/{company_id}")
def upsert_solomon_review(company_id: str, body: SolomonReviewUpsert):
    if body.decision is not None and body.decision not in ("proceed", "watch", "reject"):
        raise HTTPException(422, "decision must be 'proceed', 'watch', or 'reject'")
    session = get_session()
    try:
        _load_company(session, company_id)  # 404s if the company doesn't exist -- selecting a company into
        # the pilot always requires it to be a real Opportunity, never a fabricated stand-in.
        row = session.query(SolomonReview).filter(SolomonReview.company_id == company_id).one_or_none()
        if row is None:
            row = SolomonReview(company_id=company_id)
            session.add(row)
        for field in ("decision", "intro_path_confirmed_real", "creator_suggestions_sellable", "missing_info", "solomon_notes", "next_action"):
            value = getattr(body, field)
            if value is not None:
                setattr(row, field, value)
        row.reviewed_at = dt.datetime.utcnow()
        # SolomonReview only ever holds current state (this save just
        # overwrote it) -- snapshot it into the append-only history table so
        # a past decision and its reasoning aren't lost the next time
        # someone changes their mind about this company.
        session.add(
            SolomonReviewHistory(
                company_id=company_id,
                decision=row.decision,
                intro_path_confirmed_real=row.intro_path_confirmed_real,
                creator_suggestions_sellable=row.creator_suggestions_sellable,
                missing_info=row.missing_info,
                solomon_notes=row.solomon_notes,
                next_action=row.next_action,
            )
        )
        session.commit()
        return _solomon_review_json(session.query(SolomonReview).options(joinedload(SolomonReview.company)).filter(SolomonReview.company_id == company_id).one())
    finally:
        session.close()


@router.get("/solomon/reviews/{company_id}/history")
def get_solomon_review_history(company_id: str):
    session = get_session()
    try:
        rows = (
            session.query(SolomonReviewHistory)
            .filter(SolomonReviewHistory.company_id == company_id)
            .order_by(SolomonReviewHistory.recorded_at.desc())
            .all()
        )
        return {
            "results": [
                {
                    "id": r.id,
                    "decision": r.decision,
                    "intro_path_confirmed_real": r.intro_path_confirmed_real,
                    "creator_suggestions_sellable": r.creator_suggestions_sellable,
                    "missing_info": r.missing_info,
                    "solomon_notes": r.solomon_notes,
                    "next_action": r.next_action,
                    "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
                }
                for r in rows
            ]
        }
    finally:
        session.close()
