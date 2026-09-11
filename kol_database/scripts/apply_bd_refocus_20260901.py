#!/usr/bin/env python3
"""Apply reviewed first-party BD-route corrections from the 2026-09-01 pass.

This migration changes only three decision records whose prior state is now
superseded by official pages: Gamma Japan intent, Perplexity Japan/channel
expansion, and Wispr Flow's public creator/partnership intake. LinkedIn names
remain public current-role candidates; no budget authority or warm relation is
confirmed here.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = ROOT / "kol_database" / "deploy_seed.db"


def timestamp() -> str:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0).isoformat(sep=" ")


def apply(connection: sqlite3.Connection) -> dict[str, int]:
    now = timestamp()
    stats = {"asia_profiles_updated": 0, "asia_evidence_upserted": 0, "decisions_updated": 0, "routes_updated": 0}

    gamma_profile = {
        "markets": ["Japan"],
        "signals": ["regional_hiring", "local_creator_activation", "localized_learning", "community_events"],
    }
    cursor = connection.execute(
        """
        UPDATE company_asia_profiles SET
            asia_interest_status='strong_signal',
            market_context='overseas_company_building_local_japan_community',
            asia_target_markets_json=?, asia_signal_types_json=?,
            asia_signal_summary=?, confidence='high', asia_operator=?,
            localization_status=?, regional_creator_activity=?, mango_asia_fit=?,
            recommended_market_entry_angle=?, review_scope=?, last_verified_at=?
        WHERE company_id='company:gamma'
        """,
        (
            json.dumps(gamma_profile["markets"], ensure_ascii=False),
            json.dumps(gamma_profile["signals"], ensure_ascii=False),
            "Gamma 当前 Japan Regional Community 岗位明确负责本地 creator、workshop、localized learning、Gamma 101 与 community activation；这是可回查的日本市场 intent，不等于已批准外包预算。",
            "Regional Community Manager - Japan（公开招聘角色，尚未具名）",
            "岗位明确要求日语本地化教育内容、Gamma 101 与本地活动。",
            "岗位明确要求与本地 creators 建立关系并运营 workshop/community activation。",
            "很高：Mango 可把日本/APAC creator education、community activation 与可归因教程分发合并成一个小型试点。",
            "以日本 creator education + workshop/community activation 切入；先确认 owner、目标城市、成功指标与预算机制。",
            "人工回查 Gamma 官方 Japan Regional Community 与 Growth Marketing 招聘页；职位存在证明 intent，不证明预算审批或供应商选择。",
            now,
        ),
    )
    stats["asia_profiles_updated"] += cursor.rowcount

    perplexity_profile = {
        "markets": ["India", "Japan", "South Korea"],
        "signals": ["official_local_partnership", "regional_distribution", "channel_partner_expansion"],
    }
    cursor = connection.execute(
        """
        UPDATE company_asia_profiles SET
            asia_interest_status='confirmed_expansion',
            market_context='overseas_company_with_active_asia_distribution',
            asia_target_markets_json=?, asia_signal_types_json=?,
            asia_signal_summary=?, confidence='high', asia_operator=?,
            localization_status=?, local_partner_or_customer=?, regional_creator_activity=?,
            mango_asia_fit=?, recommended_market_entry_angle=?, review_scope=?, last_verified_at=?
        WHERE company_id='company:perplexity'
        """,
        (
            json.dumps(perplexity_profile["markets"], ensure_ascii=False),
            json.dumps(perplexity_profile["signals"], ensure_ascii=False),
            "Perplexity 已通过 Airtel 在印度大规模分发，并通过 SoftBank 在日本推出 Enterprise Pro；官方材料也把韩国 SK Telecom 列为全球扩张路径的一部分。",
            "Partnerships / Publisher Partnerships / Regional channel owner（具体预算人待核实）",
            "日本页面与企业分销已经本地化；更广 creator localization 仍待确认。",
            "Airtel、SoftBank；官方页面另提到 SK Telecom。",
            "已有重复 creator paid observations，但尚未按日本/印度受众完成归因。",
            "很高：可补充电信/企业渠道之外的本地 creator education、use-case content 与转化归因。",
            "引用日本与印度已落地渠道，提出补齐 adoption education 的小型本地 creator 测试；不把 channel partnership 当作 creator 预算。",
            "人工回查 Perplexity 官方 Japan/SoftBank、Publisher Program、Channel Partner 与 Campus Partner 页面，并保留 Airtel 官方证据。",
            now,
        ),
    )
    stats["asia_profiles_updated"] += cursor.rowcount

    evidence_rows = [
        (
            "asia:company:gamma:japan-community-role", "company:gamma", json.dumps(["Japan"], ensure_ascii=False),
            "regional_hiring", "Gamma 官方 Japan Regional Community 招聘明确要求本地 creator、workshop、localized learning、Gamma 101 与 community activation。",
            "https://careers.gamma.app/regional-community-manager-japan", "2026-09-01", now, "high", "confirmed", now,
        ),
        (
            "asia:company:perplexity:softbank-japan", "company:perplexity", json.dumps(["Japan", "South Korea"], ensure_ascii=False),
            "official_local_partnership", "Perplexity 官方宣布通过 SoftBank 在日本推出 Enterprise Pro，并将其列为继韩国 SK Telecom 后的全球扩张路径。",
            "https://www.perplexity.ai/ja/hub/blog/perplexity-expands-partnership-with-softbank-to-launch-enterprise-pro-japan", "2026-09-01", now, "high", "confirmed", now,
        ),
    ]
    for row in evidence_rows:
        connection.execute(
            """
            INSERT INTO company_asia_evidence (
                evidence_id, company_id, target_markets_json, signal_type, summary,
                source_url, evidence_date, collected_at, confidence, fact_status, last_verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
                target_markets_json=excluded.target_markets_json,
                signal_type=excluded.signal_type, summary=excluded.summary,
                source_url=excluded.source_url, evidence_date=excluded.evidence_date,
                confidence=excluded.confidence, fact_status=excluded.fact_status,
                last_verified_at=excluded.last_verified_at
            """,
            row,
        )
        stats["asia_evidence_upserted"] += 1

    decisions = {
        "company:gamma": {
            "why_now": "Gamma 的日本社区招聘明确要求本地创作者、工作坊、本地化教学与社区激活；增长岗位同时负责拉新实验和合作伙伴生态。",
            "sell": "日本与亚太创作者教育及社区激活试点，配套可归因的生产力与演示文稿教程分发。",
            "buyer": "Conor Irvine（官网确认 Partnerships）；Fiona Turko / Amanda Rothbard 为公开当前职位候选，身份与预算权仍待双重确认。",
            "route": "先向 Conor / Partnerships 提交一页日本市场激活简报，并同时使用官方社区与联盟入口验证路由。",
            "action": "发送一页式日本市场简报：目标受众、创作者类型、工作坊形式、激活指标和六周继续投入门槛；先确认谁拥有区域预算。",
            "fallback_1": "通过 Gamma Community 核验日本活动负责人，并提出一场本地 Gamma 101 工作坊。",
            "fallback_2": "通过 PartnerStack 联盟入口验证创作者激活流程，再申请升级为付费区域试点。",
            "unknowns": ["日本区域预算负责人及审批机制", "Fiona / Amanda 的当前身份和对外合作权限", "工作坊、联盟与付费创作者的预算边界"],
        },
        "company:perplexity": {
            "why_now": "Perplexity 已在印度通过 Airtel 大规模分发，并通过 SoftBank 在日本推出 Enterprise Pro；同时存在 Publisher、Channel 与 Campus Partner 公开机制。",
            "sell": "日本/印度 adoption education + 双语 answer-engine 工作流 creator 测试，补充现有 channel distribution，并按激活与留存衡量。",
            "buyer": "Daniela Miranda-Smith（Head of Partnerships）/ Michelle Dao（Creator Partnerships）为公开当前职位候选；身份与预算权待首方确认。",
            "route": "优先使用官方 partnerships@perplexity.ai / Publisher Partnerships 路线，并在邮件中请求转给 Japan/creator activation owner。",
            "action": "向 partnerships@perplexity.ai 发送一页 Japan/India adoption brief，引用现有 channel distribution，询问负责 creator education 与区域激活的 owner。",
            "fallback_1": "通过 publishers@perplexity.ai 询问日本 publisher/creator co-marketing 的采购与 owner。",
            "fallback_2": "从 Campus Partners 或近期付费 creator 询问 brief 与付款流程，不默认请求引荐。",
            "unknowns": ["Creator / regional activation 的经济买方", "日本与印度 creator budget", "Daniela / Michelle 的当前身份与具体权限"],
        },
        "company:wisprflow": {
            "why_now": "Wispr Flow 已有 2026 年重复 creator sponsorship 观察，并公开 creator/partnership intake，明确覆盖 sponsorship、affiliate、co-marketing、community 与 brand integration。",
            "sell": "按 founder、developer 与 productivity use case 分层的区域 creator activation，并以 trial activation 与 30 天 retained usage 衡量。",
            "buyer": "Jacob Shwirtz（Cultural Partnerships）/ Rohan Kochhar（Growth & Partnerships, India）为公开当前职位候选；身份与预算权待首方确认。",
            "route": "使用官方 partnership/creator inquiry form 直达 Partnerships team，并把具体 audience、timing、collaboration type 与指标写全。",
            "action": "通过官方表单提交 30 天 APAC/India cohort brief，明确 creator 样本、目标用例、激活与留存指标，并请求转给区域 owner。",
            "fallback_1": "通过 enterprise@wisprflow.ai 做明确 routing ask，附同一页 brief。",
            "fallback_2": "向 2026 年已观察到的 sponsored creators 询问采购流程，不默认请求引荐。",
            "unknowns": ["India/APAC 是否为当前优先市场", "Jacob / Rohan 的当前身份与预算权限", "creator 合作费、affiliate 与 co-marketing 的预算边界"],
        },
    }
    for company_id, values in decisions.items():
        cursor = connection.execute(
            """
            UPDATE company_decisions SET
                why_now_zh=?, what_to_sell_zh=?, buyer_summary_zh=?,
                primary_route_summary_zh=?, first_action_zh=?, fallback_1_zh=?,
                fallback_2_zh=?, key_unknowns_json=?, last_verified_at=?
            WHERE company_id=?
            """,
            (
                values["why_now"], values["sell"], values["buyer"], values["route"],
                values["action"], values["fallback_1"], values["fallback_2"],
                json.dumps(values["unknowns"], ensure_ascii=False), now, company_id,
            ),
        )
        stats["decisions_updated"] += cursor.rowcount

    cursor = connection.execute(
        """
        UPDATE company_contact_routes SET
            route_detail=?, why_this_route=?, required_first_step=?,
            supporting_evidence=?, evidence_url=?, last_verified_at=?
        WHERE company_id='company:wisprflow' AND route_key='creator-form'
        """,
        (
            "Use the official partnership and creator inquiry form, which routes to the Partnerships team and covers sponsorship, affiliate, co-marketing, community and brand integrations.",
            "This is the company's explicit public commercial collaboration route, not a guessed contact.",
            "Submit audience, channel links, collaboration type, timing, sample creators and activation/retention metrics.",
            "Official Wispr Flow Help Center partnership and creator inquiry page.",
            "https://docs.wisprflow.ai/articles/7890073756-partnership-and-creator-collaboration-inquiries",
            now,
        ),
    )
    stats["routes_updated"] += cursor.rowcount

    connection.execute(
        "UPDATE company_contact_routes SET is_best=0, fallback_order=1, last_verified_at=? WHERE company_id='company:perplexity' AND route_key='operator-growth'",
        (now,),
    )
    cursor = connection.execute(
        """
        UPDATE company_contact_routes SET
            is_best=1, fallback_order=NULL, label='Perplexity Partnerships',
            route_detail=?, why_this_route=?, required_first_step=?, supporting_evidence=?,
            evidence_url=?, confidence='high', last_verified_at=?
        WHERE company_id='company:perplexity' AND route_key='business-contact'
        """,
        (
            "Use partnerships@perplexity.ai and request routing to the Japan/creator activation owner.",
            "Perplexity's official Channel Partner page explicitly publishes this partnerships route.",
            "Send a one-page Japan/India adoption brief with creator types, activation metric and a routing question.",
            "Official Perplexity Channel Partner announcement.",
            "https://www.perplexity.ai/hub/blog/meet-our-first-channel-partners-data-integrators",
            now,
        ),
    )
    stats["routes_updated"] += cursor.rowcount + 1
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    connection = sqlite3.connect(args.db)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        stats = apply(connection)
        if args.apply:
            connection.commit()
        else:
            connection.rollback()
        print(json.dumps({"mode": "applied" if args.apply else "dry_run", "db": str(args.db), **stats}, ensure_ascii=False, indent=2))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
