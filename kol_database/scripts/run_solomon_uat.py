"""Read-only HTTP acceptance checks for the Solomon internal UAT build."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

import certifi


def verified_ssl_context() -> ssl.SSLContext:
    """Use certifi's current CA bundle; never bypass TLS verification."""

    return ssl.create_default_context(cafile=certifi.where())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8811")
    parser.add_argument(
        "--access-token",
        default=os.environ.get("INTERNAL_ACCESS_TOKEN"),
        help="Optional internal-access bearer token (defaults to INTERNAL_ACCESS_TOKEN).",
    )
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    passed: list[str] = []
    ssl_context = verified_ssl_context()

    def check(condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(label)
        passed.append(label)

    def get(path: str, *, expect_json: bool = True):
        headers = {"Accept": "application/json" if expect_json else "text/html"}
        if args.access_token:
            headers["Authorization"] = f"Bearer {args.access_token}"
        request = Request(base + path, headers=headers)
        with urlopen(request, timeout=15, context=ssl_context) as response:
            check(response.status == 200, f"HTTP 200 {path}")
            check("WWW-Authenticate" not in response.headers, f"无页面密码 challenge {path}")
            payload = response.read()
        return json.loads(payload) if expect_json else payload.decode("utf-8")

    index = get("/", expect_json=False)
    check("Mango" in index, "首页 HTML 已加载")

    home = get("/api/home/summary")
    check(len(home["top_actions"]) >= 5, "首页至少 5 个行动")
    release_day = home["top_actions"][:3]
    check(
        {row["company_name"] for row in release_day} == {"Replit", "Cursor", "Perplexity"},
        "首页前三为发布日内部核实",
    )
    check(
        all(row.get("planned_work_type") == "internal_relationship_check" for row in release_day),
        "发布日行动未被标成直接外联",
    )
    check(
        all("Jennie" in (row.get("primary_next_action") or "") for row in release_day),
        "发布日行动保留对 Jennie 的精确内部核实问句",
    )
    check(len(home["connector_checks"]) == 5, "首页 5 个内部核实问题")
    check(home["totals"]["companies"] == 89, "首页公司总数 89")
    check(home["totals"]["confirmed_paid_sponsorships"] == 0, "已复核付费证据为 0")
    check(home["totals"]["unreviewed_paid_sponsorships"] == 42, "待审核付费观察为 42")

    companies = get("/api/companies?page_size=100")
    check(companies["total"] == 89, "商机列表 89 家")
    check(all(row.get("next_action") for row in companies["results"]), "89 家均有下一步")
    check(all("@None" not in row.get("next_action", "") for row in companies["results"]), "行动文案无 @None")
    check(all(row.get("best_route_kind") for row in companies["results"]), "89 家均有路径/研究方案类型")

    def company(company_id: str) -> dict:
        return get(f"/api/companies/{quote(company_id, safe='')}")

    runway = company("company:runway")
    check(runway["relationship_stage_code"] == "E1", "Runway 为 E1")
    check(runway["suggested_bridge_action"]["kind"] == "direct_only", "Runway 为 direct-only")
    check(runway["identified_operator"]["name"] == "Cristóbal Valenzuela", "Runway 联系人正确")
    check(runway["identified_operator"]["identity_source_matched"], "Runway 公开资料双源匹配")
    check(not runway["identified_operator"]["identity_human_verified"], "Runway 身份未冒充人工核实")
    check(not runway["identified_operator"]["role_human_verified"], "Runway 职位未冒充人工核实")
    check(not runway["identified_operator"]["mango_relationship_verified"], "Runway 无伪造 Mango 关系")
    check(len(runway["outreach_logs"]) == 0, "Runway 无 UAT outreach 残留")
    runway_paths = runway.get("all_intro_paths") or []
    check(
        runway_paths and all(path.get("edge_directions") is not None for path in runway_paths),
        "Runway 路径均保留逐边关注方向 payload",
    )
    check(
        any(
            step.get("relationship") == "follows"
            and step.get("from_follows_to") is True
            and step.get("to_follows_from") is False
            for path in runway_paths
            for step in (path.get("direction_steps") or [])
            if "jennie" in (step.get("from_label") or "").lower()
            and any(name in (step.get("to_label") or "").lower() for name in ("runway", "cristóbal", "c_valenzuelab"))
        ),
        "Jennie → Runway/Cristóbal 明确为单向关注，不冒充互关",
    )

    replit = company("company:replit")
    check(replit["identified_operator"]["name"] == "Tala Awwad", "Replit 联系人正确")
    check(replit["identified_operator"]["identity_source_matched"], "Replit 公开资料双源匹配")
    check(not replit["identified_operator"]["identity_human_verified"], "Replit 身份未冒充人工核实")
    check(replit["identified_operator"]["dm_status"] == "verified_open", "Replit 私信开放独立显示")
    check(replit["suggested_bridge_action"]["kind"] == "direct_only", "Replit 为 direct-only")

    cursor = company("company:cursor")
    check(cursor["identified_operator"]["name"] == "Lee Robinson", "Cursor 联系人可见")
    check(cursor["identified_operator"]["dm_status"] == "verified_open", "Cursor 私信开放独立显示")
    check(cursor["suggested_bridge_action"]["kind"] == "direct_only", "Cursor 为 direct-only")
    check("同时" not in cursor["suggested_bridge_action"]["text"], "Cursor 无孤立‘同时’文案")

    check(company("company:olas")["relationship_stage_code"] == "E2", "Olas 为 E2 历史互动信号")
    sapien = company("company:sapien")
    check(
        len(sapien["suggested_bridge_action"].get("warm") or []) == 1
        and sapien["suggested_bridge_action"]["warm"][0]["bridge_handle"] == "calchulus",
        "Sapien 仅把有公开互动的 calchulus 留作 E2 关系候选",
    )
    check(
        len(sapien["suggested_bridge_action"].get("verification_queue") or []) == 1
        and sapien["suggested_bridge_action"]["verification_queue"][0]["bridge_handle"] == "roxinft",
        "Sapien 将无公开互动的 roxinft 留在 E1 核实队列",
    )
    check(company("company:gaib")["relationship_stage_code"] == "E2", "GAIB 为 E2")

    # Bounded negative evidence: a checked search window with zero public X
    # hits is not proof that two humans do not know one another. Heurist is the
    # deterministic release example because all three candidates were checked
    # and remained in the verification queue rather than being promoted to a
    # warm relationship.
    heurist = company("company:heurist")
    heurist_bridges = heurist["relationship_stage_detail"].get("bridges") or []
    check(heurist["relationship_stage_code"] == "E1", "Heurist 仅为 E1 关注候选")
    check(
        heurist_bridges
        and all(
            row.get("interaction_verification_status")
            == "checked_no_public_interaction_found"
            for row in heurist_bridges
        ),
        "Heurist 候选均完成公开互动查询且未命中",
    )
    check(
        all(
            "不代表两人不认识" in (row.get("evidence_note") or "")
            and any("不代表两人不认识" in note for note in row.get("coverage_notes") or [])
            for row in heurist_bridges
        ),
        "Heurist 未命中采用有边界措辞，不推断私人关系",
    )
    check(
        not (heurist["suggested_bridge_action"].get("warm") or [])
        and len(heurist["suggested_bridge_action"].get("verification_queue") or []) == 3,
        "Heurist 三位候选只进核实队列，不冒充暖路径",
    )
    check(
        all(not row.get("relationship_verified") for row in heurist_bridges),
        "Heurist 无伪造的已确认关系",
    )

    # Highest current spend-mechanism evidence tier with no credible route.
    # L3 is still a spend-intent/mechanism signal, not a confirmed campaign
    # budget; this case prevents the UI from equating value with reachability.
    akool = company("company:akool")
    check(akool["spend_evidence_level"] == "L3", "AKOOL 为当前最高 L3 投放机制信号（非确认预算）")
    check(akool["relationship_stage_code"] == "E0", "AKOOL 无关系线索")
    check(akool["suggested_bridge_action"]["kind"] == "research_only", "AKOOL 无可信路径时只进入研究方案")
    check(akool.get("identified_operator") is None, "AKOOL 未编造负责人")
    check(
        "没有任何已知关系路径或负责人" in akool["suggested_bridge_action"]["text"],
        "AKOOL 下一步明确补负责人和路径，不直接外联",
    )

    # There is no E4+ human-confirmed relationship in this snapshot. Sapien is
    # the closest real counter-case: one historical public interaction (E2)
    # paired with only L1 capacity evidence. Keep the weakness explicit.
    all_relationship_codes = {row["relationship_stage_code"] for row in companies["results"]}
    check(
        not any(code in all_relationship_codes for code in {"E4", "E5", "E6"}),
        "当前快照没有可声称为强关系的 E4+ 案例",
    )
    check(sapien["relationship_stage_code"] == "E2", "Sapien 是最接近的公开互动案例但仍未核实关系")
    check(sapien["spend_evidence_level"] == "L1", "Sapien 仅有 L1 capacity signal")
    check(
        not sapien["relationship_evidence"]["relationship_verified"],
        "Sapien 公开互动不冒充私人关系或引荐意愿",
    )

    gamma = company("company:gamma")
    check(gamma["relationship_stage_code"] == "E0", "Gamma 为 E0")
    check(len(gamma["operators"]) == 0, "Gamma 无编造联系人")
    check(gamma["paid_sponsorship_unreviewed_count"] == 7, "Gamma 7 条付费观察待审核")
    check(gamma["execution_priority"] == "本周准备", "Gamma 不是现在联系")

    network = get("/api/network/interview-queue")
    check(bool(network.get("queue")), "人脉内部核实队列非空")
    creator_meta = get("/api/meta/filters")
    check(creator_meta["tab_counts"]["media"] == 4, "媒体 / 社区独立标签页为 4 条")
    media_creators = get("/api/creators?tab=media&page_size=20")
    check(
        media_creators["total"] == 4
        and all(row["creator_class"] == "Media / Community Account" for row in media_creators["results"]),
        "媒体 / 社区账号不再混入核心 KOL",
    )
    creators = get("/api/creators?page_size=20")
    check(bool(creators.get("results")), "Creator 列表非空")
    representative_creator_id = next(
        sponsorship["creator"]["id"]
        for sponsorship in gamma["sponsorships"]
        if sponsorship.get("creator") and sponsorship.get("review_status") == "unreviewed"
    )
    creator = get(f"/api/creators/{representative_creator_id}")
    check(all("review_status" in row for row in creator["sponsorship_history"]), "Creator 合作观察均带审核状态")
    check(creator["repeat_confirmed_paid_sponsor_companies"] == [], "待审核观察不冒充多次付费合作")
    shortlists = get("/api/shortlists")
    shortlist_rows = shortlists if isinstance(shortlists, list) else shortlists.get("results", [])
    check(len(shortlist_rows) == 0, "正式候选名单为空")

    print(f"Solomon UAT PASSED -- {len(passed)} assertions")
    for label in passed:
        print(f"  PASS  {label}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Solomon UAT FAILED: {exc}", file=sys.stderr)
        raise
