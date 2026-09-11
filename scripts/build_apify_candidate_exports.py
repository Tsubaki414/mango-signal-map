#!/usr/bin/env python3
"""Build Solomon-readable Apify review exports without confirming any claim."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE = ROOT / "data" / "pilot_v5" / "apify_research_review_queue.json"
DEFAULT_DB = ROOT / "kol_database" / "deploy_seed.db"
DEFAULT_OUTPUT = ROOT / "data" / "pilot_v5"
DEFAULT_COCKPIT_OUTPUT = ROOT / "kol_database" / "data" / "apify_company_intelligence_v1.json"
DEFAULT_REVIEW_OUTPUT = ROOT / "kol_database" / "data" / "apify_review_candidates_v1.json"
DEFAULT_DOSSIERS = ROOT / "data" / "pilot_v5" / "priority_company_dossiers_v5.json"


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clean_handle(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"youtube\.com/(?:@|c/|user/)([^/?#]+)", url, re.I)
    return match.group(1).casefold() if match else None


def canonical_social_profile(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return f"{parsed.netloc.casefold().removeprefix('www.')}{parsed.path.rstrip('/').casefold()}"


def load_creator_index(db_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], set[str]]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    creators: dict[int, dict[str, Any]] = {}
    for row in connection.execute("SELECT id, display_name, creator_class FROM creators"):
        creators[row["id"]] = {
            "creator_id": row["id"],
            "display_name": row["display_name"],
            "creator_class": row["creator_class"],
            "rates": [],
        }
    for row in connection.execute(
        "SELECT creator_id, platform, deliverable, quote_amount_usd, quote_currency FROM rate_cards"
    ):
        if row["creator_id"] in creators:
            creators[row["creator_id"]]["rates"].append(dict(row))
    by_handle: dict[str, dict[str, Any]] = {}
    for row in connection.execute(
        "SELECT creator_id, handle, profile_url FROM social_accounts WHERE platform = 'YouTube'"
    ):
        creator = creators.get(row["creator_id"])
        if not creator:
            continue
        for value in (row["handle"], clean_handle(row["profile_url"])):
            if value:
                by_handle[str(value).lstrip("@").casefold()] = creator
    by_name = {row["display_name"].strip().casefold(): row for row in creators.values()}
    existing_urls = {
        row[0]
        for row in connection.execute("SELECT content_url FROM sponsorship_evidence WHERE content_url <> ''")
    }
    connection.close()
    return by_handle, by_name, existing_urls


def creator_match(observation: dict[str, Any], by_handle: dict[str, Any], by_name: dict[str, Any]) -> dict[str, Any] | None:
    entity = observation.get("candidate_entity") or {}
    handle = clean_handle(entity.get("channel_url"))
    if handle and handle in by_handle:
        return by_handle[handle]
    name = str(entity.get("name") or "").strip().casefold()
    return by_name.get(name)


def sponsorship_type(candidate: dict[str, Any]) -> str:
    classification = candidate.get("classification_candidate") or ""
    if candidate.get("paid_brand_attributed"):
        return "paid_sponsorship"
    if "affiliate" in classification and candidate.get("affiliate_brand_attributed"):
        return "affiliate"
    return "unknown_commercial_relationship"


SIGNAL_LABELS_ZH = {
    "commercialization": "商业化",
    "creator_affiliate": "creator / affiliate",
    "growth_marketing": "增长营销",
    "partnership_ecosystem": "伙伴生态",
    "regional_expansion": "区域扩张",
    "community_events": "社区与活动",
    "public_contact": "公开申请 / 联系路径",
}


PUBLIC_ROUTE_OVERRIDES = {
    "company:perplexity": {
        "label": "Perplexity Partnerships",
        "route_type": "Direct official channel",
        "route_url": "mailto:partnerships@perplexity.ai",
        "evidence_url": "https://www.perplexity.ai/hub/blog/meet-our-first-channel-partners-data-integrators",
        "why_this_route": "官方 Channel Partner 页面公开 partnerships@perplexity.ai；可请求转给 Japan / creator activation owner。",
        "required_first_step": "发送一页 Japan/India adoption brief，并提出明确 routing question。",
        "confidence": "high",
        "fact_status": "confirmed_official_route",
    },
    "company:wisprflow": {
        "label": "Wispr Flow Partnership / Creator Inquiry",
        "route_type": "Direct official channel",
        "route_url": "https://docs.wisprflow.ai/articles/7890073756-partnership-and-creator-collaboration-inquiries",
        "evidence_url": "https://docs.wisprflow.ai/articles/7890073756-partnership-and-creator-collaboration-inquiries",
        "why_this_route": "官方说明明确覆盖 sponsorship、affiliate、co-marketing、community 与 brand integrations，并路由到 Partnerships team。",
        "required_first_step": "提交 audience、channel、collaboration type、timing、creator 样本与激活/留存指标。",
        "confidence": "high",
        "fact_status": "confirmed_official_route",
    },
}


def operator_priority_score(row: dict[str, Any]) -> int:
    title = str(row.get("current_role") or "").casefold()
    score = 0
    for value, terms in (
        (104, ("creator partnership", "influencer", "affiliate", "partner marketing")),
        (92, ("partnership", "business development", "ecosystem")),
        (88, ("community", "developer relations", "devrel")),
        (84, ("growth marketing", "performance marketing")),
        (78, ("product marketing", "field marketing")),
        (68, ("marketing", "growth")),
    ):
        if any(term in title for term in terms):
            score = max(score, value)
    if any(term in title for term in ("apac", "asia", "japan", "korea", "china", "india", "regional")):
        score += 8
    if any(term in title for term in ("public sector", "policy", "gsi partnership")):
        score -= 30
    if any(term in title for term in ("positions", "member of marketing staff")):
        score -= 20
    return score


def top_operator_candidates(rows: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (-operator_priority_score(row), str(row.get("name") or "").casefold()),
    )[:limit]


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def join_zh_clauses(*values: Any) -> str:
    clauses = [str(value).strip().rstrip("。；; ") for value in values if value]
    return f"{'；'.join(clauses)}。" if clauses else ""


def build_priority_bd_cards(
    *,
    dossiers: list[dict[str, Any]],
    cockpit_companies: dict[str, dict[str, Any]],
    db_path: Path,
) -> dict[str, Any]:
    """Build Solomon's company-first decision layer.

    Collection counts and Actor provenance stay in the research exports. This
    file intentionally starts with the commercial decision chain and keeps
    every LinkedIn/person result at candidate status until human confirmation.
    """

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    company_ids = [row["company_id"] for row in dossiers]
    placeholders = ",".join("?" for _ in company_ids)

    decisions = {
        row["company_id"]: dict(row)
        for row in connection.execute(
            f"SELECT * FROM company_decisions WHERE company_id IN ({placeholders})",
            company_ids,
        )
    }
    asia_profiles = {
        row["company_id"]: dict(row)
        for row in connection.execute(
            f"SELECT * FROM company_asia_profiles WHERE company_id IN ({placeholders})",
            company_ids,
        )
    }

    company_names = {row["company_id"]: row["company"] for row in dossiers}
    creator_groups: dict[int, dict[str, Any]] = {}
    commercial_rows = connection.execute(
        f"""
        SELECT se.creator_id, cr.display_name, se.company_id, se.content_url,
               se.disclosure_type, se.review_status
        FROM sponsorship_evidence se
        JOIN creators cr ON cr.id = se.creator_id
        WHERE se.company_id IN ({placeholders})
          AND se.review_status <> 'rejected'
          AND se.disclosure_type IN ('paid_sponsorship', 'affiliate',
                                     'ambassador_long_term_partner',
                                     'event_podcast_appearance')
        """,
        company_ids,
    ).fetchall()
    for row in commercial_rows:
        group = creator_groups.setdefault(row["creator_id"], {
            "name": row["display_name"],
            "target_company_ids": set(),
            "target_companies": set(),
            "commercial_types": set(),
            "evidence_urls": set(),
            "observation_count": 0,
        })
        group["target_company_ids"].add(row["company_id"])
        group["target_companies"].add(company_names.get(row["company_id"], row["company_id"]))
        group["commercial_types"].add(row["disclosure_type"])
        if row["content_url"]:
            group["evidence_urls"].add(row["content_url"])
        group["observation_count"] += 1

    creator_connectors = []
    for group in creator_groups.values():
        if len(group["target_company_ids"]) < 2:
            continue
        creator_connectors.append({
            "name": group["name"],
            "target_company_ids": sorted(group["target_company_ids"]),
            "target_companies": sorted(group["target_companies"]),
            "target_company_count": len(group["target_company_ids"]),
            "observation_count": group["observation_count"],
            "commercial_types": sorted(group["commercial_types"]),
            "evidence_urls": sorted(group["evidence_urls"])[:5],
            "relationship_boundary": "历史商业内容可用于询问流程，不代表愿意引荐或当前仍可采购。",
        })
    creator_connectors.sort(
        key=lambda row: (-row["target_company_count"], -row["observation_count"], row["name"].casefold())
    )

    cards = []
    for dossier in sorted(dossiers, key=lambda row: row["priority_rank"]):
        company_id = dossier["company_id"]
        decision = decisions.get(company_id) or {}
        asia = asia_profiles.get(company_id) or {}
        research = cockpit_companies.get(company_id) or {}
        operator_candidates = top_operator_candidates(research.get("operator_candidates") or [])
        cards.append({
            "priority_rank": dossier["priority_rank"],
            "commercial_priority_score": dossier.get("commercial_priority_score"),
            "company_id": company_id,
            "company": dossier["company"],
            "product_category": dossier.get("product_category"),
            "icp_type": dossier.get("icp_type"),
            "decision": {
                "opportunity_value": decision.get("opportunity_value"),
                "execution_readiness": decision.get("execution_readiness"),
                "decision_bucket": decision.get("decision_bucket"),
            },
            "intent": {
                "why_company": decision.get("opportunity_reason_zh") or dossier.get("why_company"),
                "why_now": decision.get("why_now_zh") or dossier.get("why_now"),
                "budget_spend_signal": dossier.get("budget_spend_signals"),
                "first_party_evidence": [
                    {
                        "title": row.get("source_title"),
                        "url": row.get("source_url"),
                        "signal_types": [
                            item.get("signal_type")
                            for item in row.get("candidate_signals") or []
                            if isinstance(item, dict) and item.get("signal_type")
                        ],
                        "fact_status": "observed_unreviewed",
                    }
                    for row in (research.get("website_signals") or [])[:5]
                ],
            },
            "operator": {
                "curated_primary": dossier.get("operator") or {},
                "candidate_shortlist": operator_candidates,
                "boundary": "候选仅证明公开职业资料观察；预算权、当前身份与回复意愿仍需确认。",
            },
            "route": {
                "best": decision.get("primary_route_summary_zh") or dossier.get("best_contact_route"),
                "official_routes": (research.get("public_contact_routes") or [])[:3],
                "fallbacks": [
                    value
                    for value in (
                        decision.get("fallback_1_zh"),
                        decision.get("fallback_2_zh"),
                    )
                    if value
                ] or dossier.get("fallback_routes") or [],
            },
            "connector": {
                "creator_process_intelligence": [
                    row for row in creator_connectors
                    if company_id in row["target_company_ids"]
                ][:3],
                "x_person_path_location": "Cockpit Network / Company detail（逐边方向展示）",
                "boundary": "Connector 是联系路线的支持层，不参与公司价值排序，也不等于 warm intro。",
            },
            "asia": {
                "status": asia.get("asia_interest_status") or "not_reviewed",
                "markets": _json_list(asia.get("asia_target_markets_json")),
                "signal_summary": asia.get("asia_signal_summary") or "本轮未形成可验证的亚洲市场判断。",
                "mango_fit": asia.get("mango_asia_fit"),
                "entry_angle": asia.get("recommended_market_entry_angle"),
            },
            "action": {
                "mango_offer": decision.get("what_to_sell_zh") or dossier.get("mango_offer"),
                "first_step": decision.get("first_action_zh") or dossier.get("opening_angle"),
                "key_unknowns": _json_list(decision.get("key_unknowns_json")) or dossier.get("key_unknowns") or [],
            },
            "confidence": dossier.get("confidence"),
            "fact_status": "mixed_confirmed_inferred_and_unreviewed",
            "last_verified_at": decision.get("last_verified_at") or dossier.get("last_verified_at"),
        })

    connection.close()
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "decision_chain": ["Intent", "Operator", "Route", "Connector", "Action"],
        "priority_company_count": len(cards),
        "contract": {
            "company_ranking": "由营销需求、预算/GTM 信号、时机与 Mango fit 决定；关系图不参与起始排序。",
            "operator": "LinkedIn 仅用于公开当前职位候选发现；未人工确认前不得写成已确认买方。",
            "connector": "跨公司 connector 只提高研究和沟通复用价值，不代表愿意引荐。",
        },
        "companies": cards,
    }


def build_incremental_company_digest(
    *,
    company_id: str,
    company_name: str,
    rows: list[dict[str, Any]],
    operators: list[dict[str, Any]],
    sponsorships: list[dict[str, Any]],
    dossier: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    website = [row for row in rows if row.get("source_kind") == "official_web"]
    linkedin = [row for row in rows if row.get("source_kind") == "linkedin"]
    youtube = [row for row in rows if row.get("source_kind") == "youtube_sponsorship"]
    contacts = [row for row in rows if row.get("source_kind") == "public_contact"]
    instagram = [row for row in rows if row.get("source_kind") == "campaign_creator"]
    valid_website = [row for row in website if row.get("eligible_for_evidence_review")]
    company_operators = [row for row in operators if row.get("company_id") == company_id]
    company_sponsorships = [row for row in sponsorships if row.get("company_id") == company_id]
    attributed = [row for row in company_sponsorships if row.get("attribution_status") == "brand_attributed"]
    holds = [row for row in company_sponsorships if row.get("attribution_status") != "brand_attributed"]

    website_signals = []
    public_routes = []
    why_now = []
    for row in valid_website:
        signal_types = sorted({
            signal.get("signal_type")
            for signal in row.get("candidate_signals") or []
            if isinstance(signal, dict) and signal.get("signal_type")
        })
        website_signals.append({
            "source_title": row.get("source_title"),
            "source_url": row.get("source_url"),
            "source_validity": row.get("source_validity"),
            "eligible_for_evidence_review": True,
            "candidate_signals": [
                {"signal_type": signal_type} for signal_type in signal_types
            ],
        })
        labels = [SIGNAL_LABELS_ZH.get(value, value) for value in signal_types]
        if labels:
            why_now.append(f"{row.get('source_title') or '官网页面'}：观察到{'、'.join(labels)}信号，待人工回查原文。")
        route_signals = {"partnership_ecosystem", "creator_affiliate", "public_contact", "community_events"}
        if set(signal_types).intersection(route_signals):
            public_routes.append({
                "label": row.get("source_title") or "官网合作入口",
                "route_type": "Direct official program",
                "route_url": row.get("source_url"),
                "evidence_url": row.get("source_url"),
                "why_this_route": "官网页面包含合作、creator/affiliate、社区或公开申请信号；具体申请资格仍需人工复核。",
                "required_first_step": "打开官网原文，确认当前申请条件和负责团队，再提交一个公司特定 activation brief。",
                "confidence": "medium",
                "fact_status": "observed_unreviewed",
            })

    offer_by_company = {
        "company:tavus": "面向 agency、consultant 与开发者 creator 的实时 video-agent demo / integration campaign；先验证 API 激活和合格 partner leads。",
        "company:hedra": "面向 AI character、UGC、品牌和创意团队的 ambassador/creator activation；以作品产出、激活和可归因转化衡量。",
    }
    thesis_by_company = {
        "company:tavus": "用技术 creator 和 agency operator 展示可投入真实业务流程的 Tavus video-agent demo。",
        "company:hedra": "用 AI character / UGC creator 产出可复用品牌内容，并验证 ambassador 到产品激活的路径。",
    }
    direct_operators = top_operator_candidates([
        row for row in company_operators if row.get("role_relevance") == "direct_buyer_function"
    ])
    route_names = "、".join(
        f"{row.get('name')}（{row.get('current_role') or '职位待核实'}）"
        for row in direct_operators[:4]
    )
    best_route = (
        f"优先核验 {route_names}；这些只是公开当前职位候选，不证明预算权或回复意愿。"
        if route_names
        else "本轮没有通过当前职位门槛的直接买方候选；先走官网 program/partnership 路线。"
    )
    cohorts = []
    if attributed:
        cohorts.append({
            "cohort": "品牌归因历史内容候选",
            "objective": "先复核商业披露、受众和重复合作，再决定是否进入 campaign。",
            "creator_candidates": [
                {
                    "creator": row.get("creator"),
                    "why_fit": f"已观察到与 {company_name} 相关的品牌归因商业候选；合作类型、交付与当前意愿仍待人工复核。",
                    "youtube_channel_url": row.get("channel_url"),
                    "evidence_url": row.get("content_url"),
                    "evidence_date": row.get("published_at"),
                    "commercial_type_candidate": row.get("disclosure_type_candidate"),
                    "youtube_subscribers": (row.get("metrics") or {}).get("subscribers"),
                    "observed_video_views": (row.get("metrics") or {}).get("views"),
                    "existing_creator_id": row.get("existing_creator_id"),
                    "rate_on_file": row.get("rate_on_file"),
                    "region_evidence": "unknown",
                    "fact_status": "observed_unreviewed",
                }
                for row in attributed[:12]
            ],
            "pricing_status": "unknown; verify attribution, deliverable and rate before budgeting",
        })
    if dossier:
        for route in dossier.get("routes") or []:
            route_url = route.get("evidence_url")
            if not route_url:
                continue
            public_routes.append({
                "label": route.get("label"),
                "route_type": route.get("route_type"),
                "route_url": route_url,
                "evidence_url": route_url,
                "why_this_route": route.get("why_this_route"),
                "required_first_step": route.get("required_first_step"),
                "confidence": route.get("confidence"),
                "fact_status": "mixed_existing_dossier_route",
            })
    if decision and decision.get("why_now_zh"):
        why_now.insert(0, str(decision["why_now_zh"]))
    override = PUBLIC_ROUTE_OVERRIDES.get(company_id)
    if override:
        public_routes.insert(0, override)
    deduplicated_routes = []
    seen_route_keys = set()
    for route in public_routes:
        key = (str(route.get("label") or "").casefold(), str(route.get("route_url") or ""))
        if key in seen_route_keys:
            continue
        seen_route_keys.add(key)
        deduplicated_routes.append(route)
    public_routes = deduplicated_routes[:3]

    return {
        "company_id": company_id,
        "company": company_name,
        "fact_status": "mixed_observed_unreviewed",
        "review_status": "unreviewed",
        "source_counts": {
            "official_web": len(website),
            "linkedin": len(linkedin),
            "youtube": len(youtube),
            "public_contact": len(contacts),
            "instagram_creator_posts": len(instagram),
        },
        "decision_ready_counts": {
            "valid_first_party_pages": len(valid_website),
            "operator_candidates": len(direct_operators),
            "brand_attributed_commercial_candidates": len(attributed),
            "brand_attribution_holds": len(holds),
            "creator_expansion_candidates": len({row.get("creator") for row in attributed}),
            "public_company_contacts_found": 0,
            "public_program_routes_found": len(public_routes),
        },
        "why_now_observed": why_now or ["本轮没有可进入证据复核的官网增长/合作信号。"],
        "mango_offer_hypothesis": (
            (decision or {}).get("what_to_sell_zh")
            or (dossier.get("mango_offer") if dossier else offer_by_company.get(company_id))
        ) or "需结合官网信号与现有 company dossier 进一步提炼。",
        "best_route_hypothesis": (
            join_zh_clauses(
                (decision or {}).get("buyer_summary_zh"),
                (decision or {}).get("primary_route_summary_zh"),
            )
            or best_route
        ),
        "contact_gap": "未自动公开 LinkedIn 结果中的个人联系方式；只展示官网公开 program/partnership 路线。",
        "public_contact_routes": public_routes[:3],
        "website_signals": website_signals,
        "operator_candidates": direct_operators,
        "campaign": {
            "campaign_thesis": thesis_by_company.get(company_id, "公司特定 campaign 命题待人工提炼。"),
            "company_specific_evidence": why_now,
            "cohorts": cohorts,
            "apac_gap": {},
            "pricing_guardrail": (
                "本轮没有品牌归因的 creator 商业候选；不得用搜索结果或普通 mention 生成 campaign 名单。"
                if not attributed
                else "候选尚未人工确认，且报价不完整；不得生成貌似完整的客户总价。"
            ),
            "fact_status": "strategy_hypothesis_over_unreviewed_observations",
        },
    }


def build_gamma_campaign_plan(
    sponsorships: list[dict[str, Any]],
    creators: list[dict[str, Any]],
    japan_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    sponsor_by_creator = {row["creator"]: row for row in sponsorships}
    creator_by_name = {row["creator"]: row for row in creators}
    fit_notes = {
        "Kevin Stratvert": "大型软件教程频道；Gamma 视频带 Sponsor: Gamma、paid-content 标记和 gam.link，适合旗舰工作流教程。",
        "Jeff Su": "职场生产力与大型科技生态受众；Gamma 视频带 paid-content 标记和 gam.link，适合知识工作者叙事。",
        "Tiago Forte": "知识管理与 AI workflow 受众；Gamma 视频带 paid-content 标记和 #gammapartner，适合思想领导力案例。",
        "Daniel | Tech & Data": "现有库中已有报价；本次视频明确写 Gamma 赞助并带 gam.link，适合可定价的 tutorial 执行位。",
        "One Skill PPT": "演示文稿垂类专家；本次视频明确写 Gamma 赞助，产品与受众匹配直接。",
        "Mike Dee": "演示文稿教程频道；本次视频明确写 Gamma.app 赞助并带 gam.link，适合教程型复核。",
        "Tool Finder": "软件发现与比较受众；Gamma 视频带 paid-content 标记和品牌导向链接，适合评测/affiliate 层。",
        "Marketing Island": "小型营销软件评测渠道；有 Gamma 导向 affiliate URL，适合绩效型长尾测试。",
        "Julian Weber": "小型 AI 工具教程渠道；有 Gamma 导向 affiliate URL，适合低成本转化实验。",
    }
    specs = [
        ("旗舰工作流教育", "用真实知识工作流证明 Gamma，而不是泛 AI 曝光。", ["Kevin Stratvert", "Jeff Su", "Tiago Forte"]),
        ("演示文稿垂类教程", "覆盖强 presentation intent 用户，并测试教程到激活的转化。", ["One Skill PPT", "Mike Dee", "Daniel | Tech & Data"]),
        ("评测与 affiliate 长尾", "用可追踪链接测试常青搜索与转化，不与旗舰内容混算。", ["Tool Finder", "Marketing Island", "Julian Weber"]),
    ]
    cohorts = []
    for cohort_name, objective, names in specs:
        candidates = []
        for name in names:
            sponsor = sponsor_by_creator.get(name)
            creator = creator_by_name.get(name)
            if not sponsor or sponsor.get("attribution_status") != "brand_attributed":
                continue
            metrics = sponsor.get("metrics") or {}
            candidates.append({
                "creator": name,
                "why_fit": fit_notes[name],
                "youtube_channel_url": sponsor.get("channel_url"),
                "evidence_url": sponsor.get("content_url"),
                "evidence_date": sponsor.get("published_at"),
                "commercial_type_candidate": sponsor.get("disclosure_type_candidate"),
                "youtube_subscribers": metrics.get("subscribers"),
                "observed_video_views": metrics.get("views"),
                "existing_creator_id": sponsor.get("existing_creator_id"),
                "rate_on_file": sponsor.get("rate_on_file"),
                "rates": creator.get("rates") if creator else [],
                "region_evidence": "unknown",
                "fact_status": "observed_unreviewed",
            })
        cohorts.append({
            "cohort": cohort_name,
            "objective": objective,
            "creator_candidates": candidates,
            "pricing_status": "partial_only; do not publish a cohort total until missing quotes are obtained",
        })
    japan_shortlist = sorted(
        [row for row in japan_candidates if row["max_observed_views"] >= 1_000 or row["youtube_subscribers"] >= 10_000],
        key=lambda row: (-row["max_observed_views"], -row["youtube_subscribers"]),
    )[:8]
    cohorts.append({
        "cohort": "Japan 日语内容适配候选",
        "objective": "验证日语教程、比较评测与本地教育内容的 creator fit；这一层是 discovery，不是已验证合作。",
        "creator_candidates": [
            {
                "creator": row["creator"],
                "why_fit": (
                    f"已观察到 {row['gamma_content_observation_count']} 条日语 Gamma 教程/评测；"
                    f"样本最高 {row['max_observed_views'] or 0:,} 播放，频道约 {row['youtube_subscribers'] or 0:,} 订阅。"
                    "地域、报价和商业合作意愿仍待验证。"
                ),
                "youtube_channel_url": row["youtube_channel_url"],
                "evidence_url": row["content_observations"][0]["content_url"],
                "evidence_date": row["content_observations"][0]["published_at"],
                "commercial_type_candidate": row["commercial_status"],
                "youtube_subscribers": row["youtube_subscribers"],
                "observed_video_views": row["max_observed_views"],
                "existing_creator_id": row["existing_creator_id"],
                "rate_on_file": row["rate_on_file"],
                "rates": row["rates"],
                "region_evidence": row["region_evidence"],
                "fact_status": "content_fit_observed_commercial_fit_unverified",
            }
            for row in japan_shortlist
        ],
        "pricing_status": "unknown; request rate and audience geography before campaign budgeting",
    })
    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "company_id": "company:gamma",
        "company": "Gamma",
        "campaign_thesis": "围绕 Gamma 的 presentation/productivity 工作流构建三层组合：旗舰教育、垂类教程、affiliate 长尾。",
        "company_specific_evidence": [
            "Gamma 当前 Growth Marketing 岗位强调 top-of-funnel 实验与 partner ecosystem。",
            "Gamma 当前 Japan Regional Community 岗位强调本地 creator、workshop、localized course 与 community activation。",
            "本次 YouTube 数据找到品牌归因的既往商业候选，但这些 creator 的 APAC/日语适配尚无证据。",
        ],
        "cohorts": cohorts,
        "apac_gap": {
            "status": "japanese_language_candidate_set_found_not_commercially_validated",
            "reason": f"已找到 {len(japan_candidates)} 个日语 Gamma 内容候选，但内容语言不等于创作者所在地，且绝大多数没有赞助披露或现成报价。",
            "next_collection": [
                "核验 shortlist 的所在地、受众地域、business email 和报价",
                "查找 shortlist 对其他 AI/SaaS 品牌的付费合作披露",
                "从 Gamma Japan Regional Community 公开活动、讲师与本地课程继续扩展",
            ],
        },
        "pricing_guardrail": "当前仅 Daniel | Tech & Data 有现成报价；其他候选不得用占位价拼出 Recommended/Premium 总价。",
        "fact_status": "strategy_hypothesis_over_unreviewed_observations",
    }


def build_exports(queue: dict[str, Any], db_path: Path) -> dict[str, Any]:
    rows = queue.get("observations", [])
    dossiers: list[dict[str, Any]] = []
    dossier_by_company = {}
    if DEFAULT_DOSSIERS.exists():
        dossiers = json.loads(DEFAULT_DOSSIERS.read_text(encoding="utf-8"))
        dossier_by_company = {
            row.get("company_id"): row
            for row in dossiers
            if row.get("company_id")
        }
    decision_by_company: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        decision_by_company = {
            row["company_id"]: dict(row)
            for row in connection.execute("SELECT * FROM company_decisions")
        }
    by_handle, by_name, existing_urls = load_creator_index(db_path)
    website = [row for row in rows if row.get("source_kind") == "official_web"]
    linkedin = [row for row in rows if row.get("source_kind") == "linkedin"]
    youtube = [row for row in rows if row.get("source_kind") == "youtube_sponsorship"]
    instagram = [row for row in rows if row.get("source_kind") == "campaign_creator"]
    gamma_website = [row for row in website if row.get("company_id") == "company:gamma"]
    gamma_linkedin = [row for row in linkedin if row.get("company_id") == "company:gamma"]
    gamma_youtube = [row for row in youtube if row.get("company_id") == "company:gamma"]
    gamma_instagram = [row for row in instagram if row.get("company_id") == "company:gamma"]

    japan_groups: dict[str, dict[str, Any]] = {}
    for row in gamma_youtube:
        entity = row.get("candidate_entity") or {}
        commercial = row.get("candidate_commercial_relationship") or {}
        if entity.get("content_language_observed") != "Japanese" or not commercial.get("company_mentioned"):
            continue
        name = str(entity.get("name") or "Unknown creator")
        key = str(entity.get("channel_url") or name)
        match = creator_match(row, by_handle, by_name)
        group = japan_groups.setdefault(
            key,
            {
                "creator": name,
                "youtube_channel_url": entity.get("channel_url"),
                "content_language_observed": "Japanese",
                "region_evidence": "Japanese-language content observed; creator location and audience geography unverified",
                "existing_creator_id": match.get("creator_id") if match else None,
                "creator_class": match.get("creator_class") if match else "new_candidate",
                "rates": match.get("rates") if match else [],
                "rate_on_file": bool(match and match.get("rates")),
                "public_business_emails": set(),
                "content_observations": [],
                "youtube_subscribers": 0,
                "max_observed_views": 0,
            },
        )
        metrics = row.get("candidate_metrics") or {}
        group["youtube_subscribers"] = max(group["youtube_subscribers"], metrics.get("subscribers") or 0)
        group["max_observed_views"] = max(group["max_observed_views"], metrics.get("views") or 0)
        group["public_business_emails"].update((row.get("candidate_contacts") or {}).get("emails") or [])
        group["content_observations"].append(
            {
                "content_title": row.get("source_title"),
                "content_url": row.get("source_url"),
                "published_at": row.get("published_at"),
                "views": metrics.get("views"),
                "commercial_classification_candidate": commercial.get("classification_candidate"),
                "commercial_attribution_status": commercial.get("attribution_status"),
                "fact_status": "observed_unreviewed",
            }
        )
    japan_candidates = []
    for group in japan_groups.values():
        titles = " ".join(item.get("content_title") or "" for item in group["content_observations"])
        direct = bool(re.search(r"gamma", titles, re.I))
        group["gamma_content_relevance"] = (
            "direct_gamma_tutorial_or_review" if direct else "comparative_or_contextual_gamma_mention"
        )
        group["gamma_content_observation_count"] = len(group["content_observations"])
        group["repeated_gamma_content_observed"] = len(group["content_observations"]) >= 2
        group["public_business_emails"] = sorted(group["public_business_emails"])
        group["commercial_status"] = (
            "commercial_candidate_needs_review"
            if any(
                item["commercial_classification_candidate"] != "no_commercial_disclosure_found"
                for item in group["content_observations"]
            )
            else "no_gamma_commercial_disclosure_found"
        )
        group["recommended_next_step"] = (
            "Verify audience geography, other AI sponsor history, public business contact and rate before campaign use."
        )
        group["fact_status"] = "content_fit_observed_commercial_fit_unverified"
        japan_candidates.append(group)
    japan_candidates.sort(
        key=lambda row: (
            row["gamma_content_relevance"] != "direct_gamma_tutorial_or_review",
            -row["max_observed_views"],
            -row["youtube_subscribers"],
        )
    )

    operators = []
    for row in linkedin:
        entity = row.get("candidate_entity") or {}
        if not entity.get("eligible_for_operator_review"):
            continue
        operators.append(
            {
                "observation_id": row["observation_id"],
                "company_id": row["company_id"],
                "name": entity.get("name"),
                "current_role": (entity.get("current_roles_at_company") or [None])[0],
                "headline": entity.get("headline"),
                "location": entity.get("location"),
                "role_relevance": entity.get("role_relevance"),
                "operator_candidate_status": entity.get("operator_candidate_status"),
                "public_profile_url": row.get("source_url"),
                "fact_status": row.get("fact_status"),
                "review_status": row.get("review_status"),
                "last_observed_at": row.get("collected_at"),
            }
        )

    sponsorships = []
    creator_rows: dict[str, dict[str, Any]] = {}
    for row in youtube:
        candidate = row.get("candidate_commercial_relationship") or {}
        if not candidate.get("eligible_for_sponsorship_review"):
            continue
        match = creator_match(row, by_handle, by_name)
        entity = row.get("candidate_entity") or {}
        existing = row.get("source_url") in existing_urls
        attributed = candidate.get("attribution_status") == "brand_attributed"
        import_action = (
            "already_in_database"
            if existing
            else "eligible_for_unreviewed_import"
            if attributed
            else "hold_for_brand_attribution_review"
        )
        sponsorship = {
            "observation_id": row["observation_id"],
            "company_id": row["company_id"],
            "creator": entity.get("name"),
            "channel_url": entity.get("channel_url"),
            "content_title": row.get("source_title"),
            "content_url": row.get("source_url"),
            "published_at": row.get("published_at"),
            "disclosure_type_candidate": sponsorship_type(candidate),
            "classification_candidate": candidate.get("classification_candidate"),
            "attribution_status": candidate.get("attribution_status"),
            "brand_link_candidates": candidate.get("brand_link_candidates") or [],
            "disclosure_snippets": candidate.get("disclosure_snippets") or [],
            "metrics": row.get("candidate_metrics") or {},
            "existing_creator_id": match.get("creator_id") if match else None,
            "existing_creator_class": match.get("creator_class") if match else None,
            "rate_on_file": bool(match and match.get("rates")),
            "existing_sponsorship": existing,
            "import_action": import_action,
            "fact_status": "observed_unreviewed",
            "review_status": row.get("review_status"),
        }
        sponsorships.append(sponsorship)
        key = str(entity.get("channel_url") or entity.get("name") or row["observation_id"])
        creator = creator_rows.setdefault(
            key,
            {
                "creator": entity.get("name"),
                "youtube_channel_url": entity.get("channel_url"),
                "existing_creator_id": match.get("creator_id") if match else None,
                "creator_class": match.get("creator_class") if match else "new_candidate",
                "rates": match.get("rates") if match else [],
                "commercial_observations": [],
                "public_business_emails": set(),
                "instagram_profiles": set(),
                "instagram_recent_posts": [],
                "instagram_recent_post_count": 0,
                "region_evidence": "unknown",
                "campaign_cohort": "AI productivity / presentation educator",
            },
        )
        creator["commercial_observations"].append(
            {
                "content_url": row.get("source_url"),
                "published_at": row.get("published_at"),
                "classification_candidate": candidate.get("classification_candidate"),
                "attribution_status": candidate.get("attribution_status"),
                "views": (row.get("candidate_metrics") or {}).get("views"),
            }
        )
        creator["public_business_emails"].update((row.get("candidate_contacts") or {}).get("emails") or [])
        creator["instagram_profiles"].update((row.get("candidate_contacts") or {}).get("instagram_urls") or [])

    instagram_by_profile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in instagram:
        entity = row.get("candidate_entity") or {}
        if entity.get("profile_url"):
            instagram_by_profile[canonical_social_profile(entity["profile_url"])].append(row)
    for creator in creator_rows.values():
        for profile in list(creator["instagram_profiles"]):
            matched_posts = instagram_by_profile.get(canonical_social_profile(profile), [])
            creator["instagram_recent_post_count"] += len(matched_posts)
            creator["instagram_recent_posts"].extend(
                {
                    "url": post.get("source_url"),
                    "published_at": post.get("published_at"),
                    "excerpt": post.get("raw_text_excerpt"),
                    "metrics": post.get("candidate_metrics") or {},
                    "fact_status": "observed_unreviewed",
                }
                for post in matched_posts[:6]
            )

    creators = []
    for creator in creator_rows.values():
        creator["public_business_emails"] = sorted(creator["public_business_emails"])
        creator["instagram_profiles"] = sorted(creator["instagram_profiles"])
        attributed = sum(
            item["attribution_status"] == "brand_attributed" for item in creator["commercial_observations"]
        )
        creator["commercial_relevance"] = (
            "Prior Gamma commercial observation candidate; repeat outreach must wait for review."
            if attributed
            else "Possible Gamma commercial relationship; resolve brand attribution first."
        )
        creator["campaign_use"] = (
            "Product tutorial/review distribution; APAC or language fit remains unknown and must not be inferred."
        )
        creator["fact_status"] = "observed_unreviewed"
        creators.append(creator)

    website_signals = [
        {
            "source_title": row.get("source_title"),
            "source_url": row.get("source_url"),
            "source_validity": row.get("source_validity"),
            "eligible_for_evidence_review": row.get("eligible_for_evidence_review"),
            "candidate_signals": row.get("candidate_signals") or [],
        }
        for row in gamma_website
    ]
    valid_website_urls = {
        row.get("source_url")
        for row in gamma_website
        if row.get("eligible_for_evidence_review")
    }
    public_contact_routes = []
    affiliate_help_url = "https://help.gamma.app/en/articles/11048092-how-do-i-join-the-gamma-affiliate-program"
    if affiliate_help_url in valid_website_urls:
        public_contact_routes.append({
            "label": "Gamma Affiliate 官方申请",
            "route_type": "Direct official program",
            "route_url": "https://gammaapp.partnerstack.com/?group=affiliates",
            "evidence_url": affiliate_help_url,
            "why_this_route": "Gamma 官方帮助中心明确面向 creator、educator 与 community leader，并要求通过 PartnerStack 提交申请。",
            "required_first_step": "先以 Mango 的 creator cohort、受众类型和推广方式完成申请；不要用泛合作介绍代替具体 activation 方案。",
            "confidence": "high",
            "fact_status": "observed_unreviewed",
        })
    community_url = "https://community.gamma.app/"
    if community_url in valid_website_urls:
        public_contact_routes.append({
            "label": "Gamma 官方社区",
            "route_type": "Intermediary / community",
            "route_url": community_url,
            "evidence_url": community_url,
            "why_this_route": "可观察本地活动、Gambassador 与社区 operator，但社区参与不等于买方同意合作。",
            "required_first_step": "先核验日本相关活动、主办人和 Amanda Rothbard 的公开职责，再提出具体 workshop 或 creator education 方案。",
            "confidence": "medium",
            "fact_status": "observed_unreviewed",
        })
    digest = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "company_id": "company:gamma",
        "company": "Gamma",
        "fact_status": "mixed_observed_unreviewed",
        "source_counts": {
            "official_web": len(gamma_website),
            "linkedin": len(gamma_linkedin),
            "youtube": len(gamma_youtube),
            "public_contact": len([
                row for row in rows
                if row.get("source_kind") == "public_contact" and row.get("company_id") == "company:gamma"
            ]),
            "instagram_creator_posts": len(gamma_instagram),
        },
        "decision_ready_counts": {
            "valid_first_party_pages": sum(
                row.get("eligible_for_evidence_review", False) for row in gamma_website
            ),
            "operator_candidates": sum(row.get("company_id") == "company:gamma" for row in operators),
            "brand_attributed_commercial_candidates": sum(
                row["company_id"] == "company:gamma" and row["attribution_status"] == "brand_attributed"
                for row in sponsorships
            ),
            "brand_attribution_holds": sum(
                row["company_id"] == "company:gamma" and row["attribution_status"] != "brand_attributed"
                for row in sponsorships
            ),
            "creator_expansion_candidates": len({
                row["creator"] for row in sponsorships if row["company_id"] == "company:gamma"
            }),
            "public_company_contacts_found": 0,
            "public_program_routes_found": len(public_contact_routes),
        },
        "why_now_observed": [
            "Gamma 当前 Growth Marketing 职位明确负责 top-of-funnel 实验与 partner ecosystem。",
            "Gamma 当前 Japan Regional Community 职位明确提到本地 creator、workshop、localized learning 与 community activation。",
            "以上是官网首方页面观察，仍待人工复核；本轮测试的 API/Ecosystem 职位链接已经返回 404，不能继续作为当前招聘信号。",
        ],
        "mango_offer_hypothesis": (
            "以日本/APAC creator 教育和社区激活为主线，并配套可复制的 productivity tutorial 分发。"
            "官网日本岗位支持区域需求判断；creator 结果支持教程渠道判断，但已发现 creator 的 APAC 受众匹配仍未知。"
        ),
        "best_route_hypothesis": (
            "Creator 采购优先核验 Fiona Turko（Creator Partnerships Lead）；区域社区激活优先核验 Amanda Rothbard（Community Lead）；"
            "获客实验优先核验 Ravish Agrawal（Head of Growth Marketing）。三者目前都只是未复核的公开职业资料观察。"
        ),
        "contact_gap": "本轮未找到 Gamma 的通用公开公司邮箱；已找到官方 affiliate 申请和官方社区两条可执行公开路线。",
        "public_contact_routes": public_contact_routes,
        "website_signals": website_signals,
    }
    campaign = build_gamma_campaign_plan(
        [row for row in sponsorships if row["company_id"] == "company:gamma"],
        creators,
        japan_candidates,
    )
    company_names = {
        row.get("company_id"): row.get("company_name")
        for row in rows
        if row.get("company_id") and row.get("company_name")
    }
    company_digests = {
        company_id: build_incremental_company_digest(
            company_id=company_id,
            company_name=company_names[company_id],
            rows=[row for row in rows if row.get("company_id") == company_id],
            operators=operators,
            sponsorships=sponsorships,
            dossier=dossier_by_company.get(company_id),
            decision=decision_by_company.get(company_id),
        )
        for company_id in sorted(company_names)
        if company_id != "company:gamma"
    }
    exports = {
        "operators": {"schema_version": 1, "generated_at": now_iso(), "operators": operators},
        "sponsorships": {"schema_version": 1, "generated_at": now_iso(), "sponsorships": sponsorships},
        "creators": {"schema_version": 1, "generated_at": now_iso(), "creators": sorted(creators, key=lambda row: row["creator"] or "")},
        "digest": digest,
        "campaign": campaign,
        "company_digests": {
            "schema_version": 1,
            "generated_at": now_iso(),
            "companies": company_digests,
        },
        "japan_creators": {
            "schema_version": 1,
            "generated_at": now_iso(),
            "company_id": "company:gamma",
            "creator_candidates": japan_candidates,
        },
    }
    cockpit_companies = build_cockpit_package(exports)["companies"]
    exports["priority_cards"] = build_priority_bd_cards(
        dossiers=dossiers,
        cockpit_companies=cockpit_companies,
        db_path=db_path,
    )
    return exports


def build_cockpit_package(exports: dict[str, Any]) -> dict[str, Any]:
    """Return the public-evidence-only subset rendered by the Cockpit.

    Raw Actor payloads, cache paths and authentication material stay outside
    this deployable file. Candidate status remains explicit so discovery data
    cannot be mistaken for a confirmed business claim.
    """

    digest = exports["digest"]
    campaign = exports["campaign"]
    companies = {
        digest["company_id"]: {
            "company_id": digest["company_id"],
            "company": digest["company"],
            "fact_status": digest["fact_status"],
            "review_status": "unreviewed",
            "source_counts": digest["source_counts"],
            "decision_ready_counts": digest["decision_ready_counts"],
            "why_now_observed": digest["why_now_observed"],
            "mango_offer_hypothesis": digest["mango_offer_hypothesis"],
            "best_route_hypothesis": digest["best_route_hypothesis"],
            "contact_gap": digest["contact_gap"],
            "public_contact_routes": digest["public_contact_routes"],
            "website_signals": [
                row
                for row in digest["website_signals"]
                if row.get("eligible_for_evidence_review")
            ],
            "operator_candidates": top_operator_candidates([
                row
                for row in exports["operators"]["operators"]
                if row.get("company_id") == digest["company_id"]
            ]),
            "campaign": campaign,
        }
    }
    companies.update((exports.get("company_digests") or {}).get("companies") or {})
    return {
        "schema_version": 1,
        "generated_at": digest["generated_at"],
        "companies": companies,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cockpit-output", type=Path, default=DEFAULT_COCKPIT_OUTPUT)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW_OUTPUT)
    args = parser.parse_args()
    exports = build_exports(json.loads(args.queue.read_text(encoding="utf-8")), args.db)
    paths = {
        "operators": args.output_dir / "apify_operator_candidates_v1.json",
        "sponsorships": args.output_dir / "apify_sponsorship_candidates_v1.json",
        "creators": args.output_dir / "apify_creator_expansion_candidates_v1.json",
        "digest": args.output_dir / "apify_gamma_research_digest_v1.json",
        "campaign": args.output_dir / "apify_gamma_campaign_cohorts_v1.json",
        "japan_creators": args.output_dir / "apify_gamma_japan_creator_candidates_v1.json",
        "company_digests": args.output_dir / "apify_company_digests_v1.json",
        "priority_cards": args.output_dir / "priority_company_bd_cards_v1.json",
    }
    for key, path in paths.items():
        write_json(path, exports[key])
    write_json(args.cockpit_output, build_cockpit_package(exports))
    write_json(
        args.review_output,
        {
            "schema_version": 1,
            "generated_at": exports["operators"]["generated_at"],
            "operators": exports["operators"]["operators"],
            "sponsorships": exports["sponsorships"]["sponsorships"],
            "creators": exports["creators"]["creators"],
        },
    )
    print(
        json.dumps(
            {
                "outputs": {key: str(path.relative_to(ROOT)) for key, path in paths.items()},
                "cockpit_output": str(args.cockpit_output.relative_to(ROOT)),
                "review_output": str(args.review_output.relative_to(ROOT)),
                "operator_candidates": len(exports["operators"]["operators"]),
                "sponsorship_candidates": len(exports["sponsorships"]["sponsorships"]),
                "creator_candidates": len(exports["creators"]["creators"]),
                "company_digests": len(exports["company_digests"]["companies"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
