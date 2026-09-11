#!/usr/bin/env python3
"""Advisory-only GPT review for the Mango BD cockpit.

This is a development/UAT command, not a production request path. It reads
the current application state, prepares representative UI-state packets and
optionally submits them to the OpenAI Responses API with Structured Outputs.
It never writes to the application database and never applies model copy to
the frontend.

Run from the repository root:

    PYTHONPATH=kol_database .venv/bin/python \
      kol_database/scripts/review_ui_content.py --prepare-only

    PYTHONPATH=kol_database .venv/bin/python \
      kol_database/scripts/review_ui_content.py

The second form requires OPENAI_API_KEY in the ignored repository .env.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import ssl
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi


KOL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = KOL_ROOT.parent
if str(KOL_ROOT) not in sys.path:
    sys.path.insert(0, str(KOL_ROOT))

from backend.app import (  # noqa: E402
    _CreatorFilters,
    get_creator,
    list_creators,
    list_shortlists,
)
from backend.bd_api import (  # noqa: E402
    get_company,
    home_summary,
    list_companies,
    network_bridges,
    network_interview_queue,
)
from backend.env import get_env  # noqa: E402
from backend.db import init_db  # noqa: E402


ISSUE_TYPES = [
    "contradiction",
    "ambiguous_status",
    "jargon",
    "redundancy",
    "weak_information_hierarchy",
    "missing_next_action",
    "unsupported_claim",
    "fact_inference_blending",
    "stale_content",
    "empty_state_problem",
    "cross_page_inconsistency",
    "visual_overload",
]
SEVERITIES = ["P0", "P1", "P2", "P3"]

SYSTEM_PROMPT = """You are an exacting UX content and information-architecture reviewer for Mango Labs' internal Growth & BD Intelligence Cockpit.

Your scope is limited to clarity, actionability, information hierarchy, terminology, contradictions visible in the supplied packet, and whether a first-time internal user can tell what to trust and what to do next.

Hard constraints:
- Use only facts present in the supplied packet. If a needed fact is absent, write the literal string `missing_data`; never infer or complete it.
- Never decide whether a relationship is real, create a contact, change E0-E6, classify sponsorship as paid, alter commercial priority, or invent a budget, role, channel, interaction, or outcome.
- Public X follows/interactions are research evidence, not proof of a private relationship or willingness to introduce.
- Contactability, identity matching, human verification, budget influence, and Mango relationship are independent facts.
- Model suggestions are advisory and require human review; do not phrase them as production facts.
- Review the page/state supplied, not all companies in the database.
- Respond in concise Chinese. Preserve unavoidable product field names where useful.
- For every issue, evidence_required must name the missing/needed input, or be exactly `none` when the issue is purely presentational.
"""


def _string_array_schema(max_items: int = 5) -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "maxItems": max_items}


REVIEW_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "page": {"type": "string"},
        "state": {"type": "string"},
        "primary_user_question": {"type": "string"},
        "is_primary_question_answered": {"type": "boolean"},
        "clarity_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "actionability_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "trustworthiness_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "information_hierarchy_score": {"type": "integer", "minimum": 1, "maximum": 5},
        "issues": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "location": {"type": "string"},
                    "issue_type": {"type": "string", "enum": ISSUE_TYPES},
                    "observed_copy": {"type": "string"},
                    "why_confusing": {"type": "string"},
                    "proposed_copy": {"type": "string"},
                    "proposed_information_structure": {"type": "string"},
                    "evidence_required": {"type": "string"},
                },
                "required": [
                    "severity",
                    "location",
                    "issue_type",
                    "observed_copy",
                    "why_confusing",
                    "proposed_copy",
                    "proposed_information_structure",
                    "evidence_required",
                ],
            },
        },
        "redundant_content": _string_array_schema(),
        "missing_content": _string_array_schema(),
        "recommended_primary_action": {"type": "string"},
        "recommended_secondary_actions": _string_array_schema(3),
        "terms_requiring_definition": _string_array_schema(),
        "contradictions": _string_array_schema(),
        "human_review_required": {"type": "boolean"},
    },
    "required": [
        "page",
        "state",
        "primary_user_question",
        "is_primary_question_answered",
        "clarity_score",
        "actionability_score",
        "trustworthiness_score",
        "information_hierarchy_score",
        "issues",
        "redundant_content",
        "missing_content",
        "recommended_primary_action",
        "recommended_secondary_actions",
        "terms_requiring_definition",
        "contradictions",
        "human_review_required",
    ],
}


def response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reviews": {"type": "array", "items": REVIEW_ITEM_SCHEMA},
        },
        "required": ["reviews"],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _screenshot_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(REPO_ROOT)),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _creator_filters(tab: str) -> _CreatorFilters:
    # FastAPI resolves Query defaults during HTTP dependency injection. A
    # direct development-script call must pass the real values explicitly.
    return _CreatorFilters(
        tab=tab,
        search=None,
        creator_class=None,
        platform=None,
        region=None,
        language=None,
        category=None,
        followers_min=None,
        followers_max=None,
        avg_views_min=None,
        avg_views_max=None,
        engagement_min=None,
        engagement_max=None,
        quote_min=None,
        quote_max=None,
        promotion_level=None,
        contact=None,
        sort="engagement_rate",
        order="desc",
    )


def _operator_compact(operator: dict[str, Any]) -> dict[str, Any]:
    return {
        key: operator.get(key)
        for key in (
            "name",
            "role",
            "x_handle",
            "identity_source_matched",
            "identity_human_verified",
            "identity_human_verified_at",
            "role_human_verified",
            "role_human_verified_at",
            "x_account_verified",
            "dm_status",
            "public_contact_status",
            "budget_influence_status",
            "mango_relationship_verified",
            "last_verified_at",
        )
    } | {
        "bridge_candidates": [
            {
                "handle": bridge.get("bridge_handle"),
                "mango_side_handle": bridge.get("mango_side_handle"),
                "interaction_count_recent": bridge.get("interaction_count_recent"),
                "most_recent_interaction_at": bridge.get("most_recent_interaction_at"),
            }
            for bridge in (operator.get("bridges") or [])[:3]
        ]
    }


def _company_compact(detail: dict[str, Any]) -> dict[str, Any]:
    plan = detail.get("suggested_bridge_action") or {}
    return {
        "company_id": detail.get("company_id"),
        "name": detail.get("name"),
        "business_priority": detail.get("priority"),
        "business_priority_reasons": detail.get("priority_reasons"),
        "execution_status": detail.get("execution_priority"),
        "execution_status_reasons": detail.get("execution_priority_reasons"),
        "relationship_stage": detail.get("relationship_stage_code"),
        "relationship_detail": detail.get("relationship_stage_detail"),
        "reachability_label": detail.get("reachability_label"),
        "spend_evidence_level": detail.get("spend_evidence_level"),
        "budget_evidence": detail.get("budget_evidence"),
        "paid_sponsorship_confirmed_count": detail.get("paid_sponsorship_confirmed_count"),
        "paid_sponsorship_unreviewed_count": detail.get("paid_sponsorship_unreviewed_count"),
        "why_now": detail.get("why_now"),
        "operators": [_operator_compact(o) for o in (detail.get("operators") or [])[:3]],
        "recommended_action": {
            "kind": plan.get("kind"),
            "text": plan.get("text"),
            "steps": plan.get("steps"),
        },
        "owner": detail.get("owner"),
        "due_date": detail.get("due_date"),
        "current_outreach_log_count": len(detail.get("outreach_logs") or []),
        "archived_action_count": len(detail.get("action_items") or []),
        "research_path_count": len(detail.get("all_intro_paths") or []),
        "human_review": detail.get("solomon_review"),
    }


def _creator_compact(creator: dict[str, Any]) -> dict[str, Any]:
    sponsorship_history = creator.get("sponsorship_history") or []
    review_counts: dict[str, int] = {}
    for row in sponsorship_history:
        status = row.get("review_status") or "missing_data"
        review_counts[status] = review_counts.get(status, 0) + 1
    return {
        key: creator.get(key)
        for key in (
            "id",
            "display_name",
            "creator_class",
            "classification_confidence",
            "promotion_level",
            "categories",
            "region",
            "language",
            "followers",
            "avg_views",
            "engagement_rate",
            "internal_notes",
        )
    } | {
        "contact_count": len(creator.get("contacts") or []),
        "rate_card_count": len(creator.get("rate_cards") or []),
        "sponsorship_observation_count": len(sponsorship_history),
        "sponsorship_review_counts": review_counts,
        "repeat_confirmed_paid_sponsor_companies": creator.get("repeat_confirmed_paid_sponsor_companies") or [],
        "sponsorship_observation_samples": sponsorship_history[:5],
    }


def _page(
    page: str,
    state: str,
    task: str,
    data: Any,
    visible_copy: list[str],
    actions: list[str],
    screenshot: str | None = None,
    *,
    state_origin: str = "current_application_data",
) -> dict[str, Any]:
    return {
        "page": page,
        "state": state,
        "state_origin": state_origin,
        "user_task": task,
        "structured_data": data,
        "visible_copy": visible_copy,
        "accessibility_snapshot": {
            "document_language": "zh-CN",
            "navigation": ["首页", "商机", "人脉网络", "创作者"],
            "interaction_model": "tables + keyboard-activatable rows + labelled modal/drawer dialogs",
            "live_status": "toast uses role=status and aria-live=polite",
            "note": "Compact semantic inventory from current deterministic templates; screenshot is the visual source of truth.",
        },
        "available_actions": actions,
        "screenshot": screenshot or "missing_data",
    }


def collect_packet(
    snapshot_date: str,
    *,
    screenshot_dir: Path | None = None,
) -> dict[str, Any]:
    # The advisory packet is also used against older local development
    # databases.  Keep its schema compatible with the current ORM before the
    # eager-loaded company query runs; create_all is additive and does not
    # alter any research, review, outreach, or campaign records.
    init_db()
    public_request = SimpleNamespace(state=SimpleNamespace(write_access=False))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        home = home_summary()
        company_rows = list_companies(
            search=None,
            category=None,
            geography=None,
            priority=None,
            spend_evidence_level=None,
            commercial_disposition=None,
            icp_type=None,
            opportunity_value=None,
            execution_readiness=None,
            decision_bucket=None,
            signal=None,
            has_sponsorship_evidence=None,
            sort="execution_priority",
            order="desc",
            page=1,
            page_size=100,
        )
        company_by_name = {row["name"]: row for row in company_rows["results"]}
        detail_by_name = {
            name: _company_compact(get_company(company_by_name[name]["company_id"], public_request))
            for name in ("Gamma", "Heurist", "Runway", "Replit", "Cursor", "Olas", "Sapien", "GAIB")
            if name in company_by_name
        }
        queue = network_interview_queue()
        bridges = network_bridges()
        creator_rows = list_creators(_creator_filters("strategic"), page=1, page_size=100)
        creator_detail = None
        fallback_creator_detail = None
        # Sponsorship-linked accounts are not guaranteed to be in the
        # Strategic tab (many correctly live under Distribution or Needs
        # Review). Search all three deterministic tabs so the representative
        # Creator-detail state always contains real reviewable evidence.
        for tab in ("strategic", "distribution", "needs_review"):
            page = 1
            while creator_detail is None:
                tab_rows = list_creators(_creator_filters(tab), page=page, page_size=100)
                for creator_row in tab_rows["results"]:
                    candidate = get_creator(creator_row["id"], public_request)
                    fallback_creator_detail = fallback_creator_detail or candidate
                    if candidate.get("sponsorship_history"):
                        creator_detail = candidate
                        break
                if creator_detail is not None or page * 100 >= tab_rows["total"]:
                    break
                page += 1
            if creator_detail is not None:
                break
        creator_detail = creator_detail or fallback_creator_detail
        shortlists = list_shortlists()

    screenshot_dir = (
        screenshot_dir
        or (KOL_ROOT / "reports" / "uat_screenshots" / snapshot_date)
    ).resolve()
    screenshot_names = {
        "home": "home-desktop.png",
        "opportunities": "opportunities-desktop.png",
        "gamma": "gamma-e0-desktop.png",
        "runway": "runway-desktop.png",
        "cursor": "cursor-desktop.png",
        "network": "network-desktop.png",
        "creators": "creators-desktop.png",
        "creator_detail": "creator-sponsored-detail-desktop.png",
        "shortlists_empty": "shortlists-empty-desktop.png",
    }
    screenshots: dict[str, dict[str, Any]] = {}
    for key, name in screenshot_names.items():
        path = screenshot_dir / name
        if path.exists():
            screenshots[key] = _screenshot_record(path)

    def shot(key: str) -> str | None:
        return (screenshots.get(key) or {}).get("path")

    top_actions = [
        {
            key: action.get(key)
            for key in (
                "company_name",
                "execution_status",
                "business_priority",
                "relationship_stage",
                "primary_next_action",
                "contact_name",
                "contact_handle",
                "direct_channel",
                "fallback",
                "owner",
                "due_date",
            )
        }
        for action in home.get("top_actions", [])[:5]
    ]
    connector_checks = [
        {
            "asker": check.get("asker"),
            "company_name": check.get("company_name"),
            "candidates": check.get("candidates"),
        }
        for check in home.get("connector_checks", [])[:5]
    ]
    opportunities = [
        {
            key: row.get(key)
            for key in (
                "name",
                "priority",
                "relationship_stage_code",
                "execution_priority",
                "spend_evidence_level",
                "reachability_label",
                "next_action",
                "owner",
                "due_date",
            )
        }
        for row in company_rows["results"][:12]
    ]

    pages: list[dict[str, Any]] = [
        _page(
            "首页",
            "默认首屏",
            "两分钟内判断今天最该推进什么、找谁、下一步与备选方案是什么。",
            {"totals": home.get("totals"), "top_actions": top_actions, "connector_checks": connector_checks},
            ["今天最值得做的", "按执行状态排序；商业价值不等于今天可执行", "内部关系核实队列", "互关只代表候选，不代表认识或愿意引荐"],
            ["打开公司详情", "执行直接联系", "向指定 Mango 内部人员核实候选"],
            shot("home"),
        ),
        _page(
            "商机列表",
            "按执行状态排序",
            "比较商业价值、关系阶段、可执行性、缺口与下一步。",
            {"total": company_rows["total"], "representative_rows": opportunities},
            ["商业优先级", "关系阶段", "执行状态", "预算证据", "最佳可用路径", "下一步行动", "按执行状态排序"],
            ["筛选", "排序", "打开公司详情"],
            shot("opportunities"),
        ),
    ]

    company_states = [
        ("E0 公司详情", "Gamma", "判断没有关系线索时应先补什么。", shot("gamma")),
        ("E1 单向关注公司详情", "Runway", "区分单向关注研究线索与独立私信渠道。", shot("runway")),
        ("E1 互关候选公司详情", "Heurist", "比较互关候选人，同时看清真实关系尚未核实。", None),
        ("E2 单次互动公司详情", "Olas", "把一次公开互动理解为历史熟悉度信号，而非真实关系。", None),
        ("Direct Contact、无 warm path", "Cursor", "找到 Lee Robinson、直接联系渠道与备选方案，且不暗示暖路径。", shot("cursor")),
        ("Runway", "Runway", "确认 Jennie 只是单向关注研究线索；Cristóbal 私信是独立冷启动渠道。", shot("runway")),
        ("Replit", "Replit", "识别 Tala 的身份/X/私信/预算/Mango 关系为不同核实状态。", None),
        ("Cursor", "Cursor", "确认负责人、直接渠道和行动计划跨页面一致。", shot("cursor")),
        ("Olas", "Olas", "理解 E2 证据、引荐候选与直接渠道的并行差异。", None),
        ("Sapien", "Sapien", "并列比较候选人的互动证据，不按粉丝量自动选人。", None),
        ("GAIB", "GAIB", "分开显示候选负责人、互动证据与仍需人工核实的关系。", None),
    ]
    for state, name, task, screenshot in company_states:
        detail = detail_by_name.get(name, {"status": "missing_data"})
        pages.append(
            _page(
                "公司详情",
                state,
                task,
                detail,
                [
                    "执行摘要",
                    "商业优先级",
                    "关系阶段",
                    "预算与投放证据",
                    "已识别目标联系人",
                    "公开身份匹配",
                    "身份人工复核",
                    "当前职位人工复核",
                    "Mango 关系",
                    "行动方案（即时生成，与下方历史行动项独立）",
                    "早期研究材料可能已过期，不参与当前关系阶段与执行优先级计算",
                ],
                ["记录真实执行结果", "编辑联系人核实字段", "创建合作候选名单", "展开证据/历史材料"],
                screenshot,
            )
        )

    # No current company exactly represents this branch. Keep it as an
    # explicit template fixture, not a fake company fact.
    warm_fixture = json.loads(json.dumps(detail_by_name.get("Heurist", {}), ensure_ascii=False))
    warm_fixture["fixture_change"] = "For template review only: direct-channel step suppressed; all company facts remain advisory input and this object is not persisted."
    if isinstance(warm_fixture.get("recommended_action"), dict):
        fixture_action = warm_fixture["recommended_action"]
        fixture_action["kind"] = "connector_only"
        fixture_action["steps"] = [
            step for step in (warm_fixture["recommended_action"].get("steps") or []) if step.get("kind") != "direct_contact"
        ]
        fixture_action["text"] = "；".join(
            f"{index}. {step.get('detail', '')}" for index, step in enumerate(fixture_action["steps"], start=1)
        )
    warm_fixture["execution_status"] = "等待内部核实"
    warm_fixture["execution_status_reasons"] = ["模板夹具：仅保留待核实引荐候选人，不存在直接联系渠道"]
    pages.append(
        _page(
            "公司详情",
            "有 warm candidate、无直接渠道（模板夹具）",
            "验证只有待核实引荐候选人时的行动结构和备选方案。",
            warm_fixture,
            ["互关引荐候选人", "真实关系与引荐意愿尚未核实", "1. 核实引荐候选人", "2. 执行备选方案"],
            ["向 Mango 内部人员核实", "候选被证伪后使用官方渠道"],
            None,
            state_origin="non_persistent_template_fixture",
        )
    )

    pages.extend(
        [
            _page(
                "人脉网络",
                "Connector Interview Queue",
                "判断 Mango 应先问谁、一次核实哪些公司、问题怎么问。",
                {"queue": (queue.get("queue") if isinstance(queue, dict) else queue), "archived_bridge_count": len(bridges.get("results", []) if isinstance(bridges, dict) else bridges)},
                ["本轮内部关系核实", "本轮优先 4", "互关只是引荐候选，只有真实执行记录才能升级到 E4+", "早期研究材料，可能已过期，不参与当前关系阶段与执行优先级计算"],
                ["打开公司详情", "向指定内部人员提出短问题", "折叠/展开早期资料"],
                shot("network"),
            ),
            _page(
                "Creator 列表",
                "核心 KOL、按互动率排序",
                "区分 Strategic Influence 与 Distribution，并看联系方式、报价和适配信号。",
                {"total": creator_rows["total"], "rows": creator_rows["results"][:8]},
                ["核心 KOL", "KOC / 分发号", "待复核", "按互动率排序", "交付物与报价", "联系方式", "加入候选名单"],
                ["筛选", "排序", "打开创作者详情", "加入候选名单", "导出列表"],
                shot("creators"),
            ),
            _page(
                "Creator 详情",
                "代表性核心 KOL",
                "判断创作者类型、真实联系方式/报价、相似推广证据与 campaign 适配。",
                _creator_compact(creator_detail) if creator_detail else {"status": "missing_data"},
                ["创作者分类", "分类依据", "联系方式", "报价", "合作 / 推广证据", "付费观察 · 待审核", "加入候选名单"],
                ["编辑分类", "刷新公开数据", "加入候选名单"],
                shot("creator_detail"),
            ),
            _page(
                "Campaign Builder",
                "空 Campaign / 当前生产数据",
                "空数据时知道如何从公司或 Creator 库开始，不误以为存在正式 campaign。",
                {"saved_shortlists": shortlists},
                ["还没有候选名单", "请从公司详情填写合作目标，或从创作者页新建候选名单"],
                ["返回公司详情", "浏览 Creator 库"],
                shot("shortlists_empty"),
            ),
            _page(
                "Campaign Builder",
                "有候选人的非持久化模板夹具",
                "检查目标、地区、平台、预算、候选理由、余额与下一步是否清楚。",
                {
                    "fixture_only": True,
                    "company": "missing_data",
                    "objective": "missing_data",
                    "region": "missing_data",
                    "language": "missing_data",
                    "platforms": "missing_data",
                    "budget_usd": "missing_data",
                    "timing": "missing_data",
                    "candidate": _creator_compact(creator_detail) if creator_detail else "missing_data",
                    "persistence": "not_saved",
                },
                ["活动目标", "目标受众", "地区", "语言", "平台", "预算", "时间安排", "已选创作者", "剩余预算", "内部 review"],
                ["保存活动信息", "替换候选人", "导出 proposal"],
                None,
                state_origin="non_persistent_template_fixture",
            ),
            _page(
                "全局状态",
                "Loading / API Error / Empty State",
                "在加载、失败和空结果时知道发生了什么以及下一步。",
                {
                    "loading": "加载中 / skeleton rows",
                    "api_error": "暂时无法加载…；请检查网络后重试；技术详情折叠",
                    "empty_filtered": "没有符合条件的…；清除筛选或放宽范围",
                    "empty_unfiltered": "暂无数据；说明导入/开始路径",
                },
                ["加载中", "暂时无法加载", "请检查网络后重试", "技术详情", "重试", "没有符合条件的创作者", "试试清除筛选条件或放宽范围"],
                ["重试", "清除筛选", "返回可开始工作的页面"],
                None,
                state_origin="deterministic_state_templates",
            ),
        ]
    )

    return {
        "metadata": {
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
            "snapshot_date": snapshot_date,
            "advisory_only": True,
            "database_writes": False,
            "production_copy_auto_apply": False,
            "facts_source": "current local API serializers and deterministic frontend templates",
            "language_guide": "kol_database/docs/product-language.md",
            "page_state_count": len(pages),
        },
        "screenshots": screenshots,
        "pages": pages,
    }


def _image_content(paths: list[Path]) -> list[dict[str, str]]:
    content: list[dict[str, str]] = []
    for path in paths:
        if not path.exists():
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "input_text", "text": f"Screenshot file: {path.name}"})
        content.append({"type": "input_image", "image_url": f"data:image/png;base64,{encoded}", "detail": "low"})
    return content


def _extract_output_text(raw: dict[str, Any]) -> str:
    for item in raw.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text" and part.get("text"):
                return part["text"]
            if part.get("type") == "refusal":
                raise RuntimeError(f"OpenAI review refused: {part.get('refusal', 'unspecified refusal')}")
    raise RuntimeError("OpenAI response contained no output_text message")


def call_openai_review(
    pages: list[dict[str, Any]],
    *,
    model: str,
    image_paths: list[Path],
    timeout: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    api_key = get_env("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Run --prepare-only or add it to the ignored repository .env.")

    user_text = json.dumps(
        {
            "instruction": "Return one review for every page/state below, preserving each page and state string exactly.",
            "pages": pages,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    content: list[dict[str, Any]] = [{"type": "input_text", "text": user_text}]
    content.extend(_image_content(image_paths))
    payload = {
        "model": model,
        "store": False,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": SYSTEM_PROMPT}]},
            {"role": "user", "content": content},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "mango_ui_content_review",
                "strict": True,
                "schema": response_schema(),
            }
        },
        "max_output_tokens": 16000,
    }
    request = Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urlopen(request, timeout=timeout, context=ssl_context) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"OpenAI request failed: {exc}") from exc

    status = raw.get("status")
    if status not in (None, "completed"):
        raise RuntimeError(f"OpenAI response status was {status!r}: {raw.get('incomplete_details')}")
    result = json.loads(_extract_output_text(raw))
    reviews = result.get("reviews")
    if not isinstance(reviews, list):
        raise RuntimeError("Structured output did not contain a reviews array")
    return reviews, raw


def _md(value: Any) -> str:
    return str(value or "missing_data").replace("\n", " ").strip()


def _report_markdown(audit: dict[str, Any]) -> str:
    reviews = audit.get("reviews", [])
    severity_rank = {value: index for index, value in enumerate(SEVERITIES)}
    issues = sorted(
        [dict(issue, page=review.get("page"), state=review.get("state")) for review in reviews for issue in review.get("issues", [])],
        key=lambda issue: (severity_rank.get(issue.get("severity"), 99), _md(issue.get("page")), _md(issue.get("state"))),
    )
    score_fields = ("clarity_score", "actionability_score", "trustworthiness_score", "information_hierarchy_score")
    score_summary = {
        field: round(sum(review.get(field, 0) for review in reviews) / len(reviews), 2) if reviews else None
        for field in score_fields
    }
    lines = [
        "# Mango Cockpit GPT UI Content Review",
        "",
        "> Advisory only. This model output has not been auto-applied to the product or database; every proposed change requires human review against evidence.",
        "",
        f"- Generated: `{audit['metadata']['generated_at']}`",
        f"- Model: `{audit['metadata']['model']}`",
        f"- Reviewed states: **{len(reviews)}**",
        f"- Issues: **{len(issues)}**",
        f"- Average scores (1–5): clarity {score_summary['clarity_score']}, actionability {score_summary['actionability_score']}, trust {score_summary['trustworthiness_score']}, hierarchy {score_summary['information_hierarchy_score']}",
        "",
        "## Issues by severity",
        "",
    ]
    if not issues:
        lines.append("No issues returned.")
    for issue in issues:
        lines.extend(
            [
                f"### {issue.get('severity')} · {_md(issue.get('page'))} / {_md(issue.get('state'))}",
                "",
                f"- Location: {_md(issue.get('location'))}",
                f"- Type: `{_md(issue.get('issue_type'))}`",
                f"- Observed: {_md(issue.get('observed_copy'))}",
                f"- Why: {_md(issue.get('why_confusing'))}",
                f"- Proposed copy: {_md(issue.get('proposed_copy'))}",
                f"- Proposed structure: {_md(issue.get('proposed_information_structure'))}",
                f"- Evidence required: {_md(issue.get('evidence_required'))}",
                "",
            ]
        )
    lines.extend(["## Per-state scores", "", "| Page | State | Clear | Actionable | Trust | Hierarchy | Answered? |", "|---|---|---:|---:|---:|---:|---|"])
    for review in reviews:
        lines.append(
            f"| {_md(review.get('page'))} | {_md(review.get('state'))} | {review.get('clarity_score')} | {review.get('actionability_score')} | {review.get('trustworthiness_score')} | {review.get('information_hierarchy_score')} | {'yes' if review.get('is_primary_question_answered') else 'no'} |"
        )
    lines.append("")
    return "\n".join(lines)


def _copy_diff_markdown(reviews: list[dict[str, Any]]) -> str:
    lines = [
        "# Proposed Copy Diff (Advisory)",
        "",
        "> None of these model suggestions has been applied. Validate each item against current evidence and `kol_database/docs/product-language.md`.",
        "",
    ]
    count = 0
    for review in reviews:
        for issue in review.get("issues", []):
            before, after = _md(issue.get("observed_copy")), _md(issue.get("proposed_copy"))
            if before == "missing_data" and after == "missing_data":
                continue
            count += 1
            lines.extend(
                [
                    f"## {count}. {_md(review.get('page'))} / {_md(review.get('state'))} · {issue.get('severity')}",
                    "",
                    f"```diff\n- {before}\n+ {after}\n```",
                    "",
                    f"Evidence required: {_md(issue.get('evidence_required'))}",
                    "",
                ]
            )
    if count == 0:
        lines.append("No copy replacements returned.\n")
    return "\n".join(lines)


def _ia_markdown(reviews: list[dict[str, Any]]) -> str:
    lines = [
        "# Information Architecture Suggestions (Advisory)",
        "",
        "> Model-generated review material only. It does not change facts, scoring, relationship stages or production UI.",
        "",
    ]
    for review in reviews:
        structures = [
            issue.get("proposed_information_structure")
            for issue in review.get("issues", [])
            if issue.get("proposed_information_structure") not in (None, "", "none", "missing_data")
        ]
        if not structures and not review.get("missing_content") and not review.get("redundant_content"):
            continue
        lines.extend([f"## {_md(review.get('page'))} / {_md(review.get('state'))}", ""])
        lines.append(f"Primary action: {_md(review.get('recommended_primary_action'))}")
        lines.append("")
        for structure in structures:
            lines.append(f"- Structure: {_md(structure)}")
        for item in review.get("missing_content", []):
            lines.append(f"- Missing: {_md(item)}")
        for item in review.get("redundant_content", []):
            lines.append(f"- Redundant: {_md(item)}")
        lines.append("")
    return "\n".join(lines)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _chunked(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Generate input_packet.json without calling OpenAI")
    parser.add_argument("--snapshot-date", default=dt.date.today().isoformat(), help="Screenshot/report date (YYYY-MM-DD)")
    parser.add_argument("--output-dir", type=Path, help="Override reports/ui_content_review/YYYY-MM-DD")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        help="Override the screenshot source directory (for example, the final local_after capture).",
    )
    parser.add_argument("--model", help="Override OPENAI_REVIEW_MODEL / OPENAI_MODEL")
    parser.add_argument("--batch-size", type=int, default=10, choices=range(1, 21), metavar="1-20")
    parser.add_argument("--timeout", type=int, default=180, help="Seconds per Responses API batch")
    parser.add_argument("--no-images", action="store_true", help="Do not attach available screenshots to the review request")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir or KOL_ROOT / "reports" / "ui_content_review" / args.snapshot_date
    output_dir.mkdir(parents=True, exist_ok=True)
    packet = collect_packet(
        args.snapshot_date,
        screenshot_dir=args.screenshot_dir,
    )
    _write_json(output_dir / "input_packet.json", packet)
    print(f"Prepared {packet['metadata']['page_state_count']} representative states: {output_dir / 'input_packet.json'}")

    if args.prepare_only:
        print("Prepare-only mode: no OpenAI request was made and no database rows were changed.")
        return 0

    model = args.model or get_env("OPENAI_REVIEW_MODEL", "OPENAI_MODEL") or "gpt-4o-mini"
    screenshot_dir = (
        args.screenshot_dir
        or (KOL_ROOT / "reports" / "uat_screenshots" / args.snapshot_date)
    ).resolve()
    screenshot_by_name = {
        "首页": screenshot_dir / "home-desktop.png",
        "商机列表": screenshot_dir / "opportunities-desktop.png",
        "E0 公司详情": screenshot_dir / "gamma-e0-desktop.png",
        "人脉网络": screenshot_dir / "network-desktop.png",
        "Creator 列表": screenshot_dir / "creators-desktop.png",
        "Creator 详情": screenshot_dir / "creator-sponsored-detail-desktop.png",
        "空 Campaign / 当前生产数据": screenshot_dir / "shortlists-empty-desktop.png",
        "Runway": screenshot_dir / "runway-desktop.png",
        "Cursor": screenshot_dir / "cursor-desktop.png",
    }

    reviews: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(_chunked(packet["pages"], args.batch_size), start=1):
        image_paths: list[Path] = []
        if not args.no_images:
            for page in batch:
                candidates = [page.get("page"), page.get("state")]
                for candidate in candidates:
                    path = screenshot_by_name.get(candidate)
                    if path and path.exists() and path not in image_paths:
                        image_paths.append(path)
        print(f"Reviewing batch {batch_index}: {len(batch)} states, {len(image_paths)} screenshot(s), model={model}")
        batch_reviews, raw = call_openai_review(batch, model=model, image_paths=image_paths, timeout=args.timeout)
        reviews.extend(batch_reviews)
        # Raw API responses are useful for audit/usage inspection and never
        # contain the Authorization header or API key.
        _write_json(output_dir / f"raw_response_batch_{batch_index:02d}.json", raw)

    expected = {(page["page"], page["state"]) for page in packet["pages"]}
    received = {(review.get("page"), review.get("state")) for review in reviews}
    audit = {
        "metadata": {
            "generated_at": dt.datetime.now(dt.UTC).isoformat(),
            "model": model,
            "advisory_only": True,
            "human_reviewed": False,
            "database_writes": False,
            "production_copy_auto_applied": False,
            "expected_state_count": len(expected),
            "received_state_count": len(received),
            "missing_states": sorted([list(item) for item in expected - received]),
            "unexpected_states": sorted([list(item) for item in received - expected]),
        },
        "reviews": reviews,
    }
    _write_json(output_dir / "audit.json", audit)
    (output_dir / "REPORT.md").write_text(_report_markdown(audit), encoding="utf-8")
    (output_dir / "copy_diff.md").write_text(_copy_diff_markdown(reviews), encoding="utf-8")
    (output_dir / "information_architecture.md").write_text(_ia_markdown(reviews), encoding="utf-8")

    print(f"Saved {len(reviews)} advisory reviews to {output_dir}")
    if audit["metadata"]["missing_states"]:
        print(f"WARNING: {len(audit['metadata']['missing_states'])} expected state(s) were missing from model output.")
        return 2
    print("No database rows or production templates were changed by this command.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
