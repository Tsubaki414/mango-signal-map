"""Generates a connector-verification research appendix.
(section VI, 2026-08-28 spec) directly from live computed data --
bd_compute.opportunity_priority/relationship_stage/suggested_bridge_action
-- never hand-authored prose that could drift from what the system
actually knows. Every fact in the output is traceable to a real DB row or
a real Rapid X interaction check; nothing here is invented.

Scope note: the spec asked for 10 companies across 4 categories (paid
sponsorship, direct DM, unconfirmed bridge candidates, high-value/no
path). This run covers the 6 companies with COMPLETE real data already
verified this session (Replit, Runway: paid + direct; Olas, Sapien, GAIB,
Heurist: bridge candidates with real interaction-evidence checks). The
"high-value, zero operator" category (e.g. Gamma, ElevenLabs) needs new
operator-discovery research (finding a real growth/partnerships person,
not inventing one) that was not done in this pass -- see the write-up's
closing note.

Usage: python3 scripts/generate_connector_pilot.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bd_compute import execution_priority, opportunity_priority, relationship_stage, suggested_bridge_action  # noqa: E402
from backend.db import get_session  # noqa: E402
from backend.models import Company  # noqa: E402

PILOT_COMPANY_NAMES = ["Replit", "Runway", "Wispr Flow", "Olas", "Sapien", "GAIB", "Heurist"]


def _company_writeup(c: Company) -> str:
    opp = opportunity_priority(c)
    stage = relationship_stage(c)
    plan = suggested_bridge_action(c)
    exec_p = execution_priority(c)

    lines = [f"## {c.name}", ""]
    lines.append(f"**商业价值证据**：{'；'.join(opp['reasons'][:3])}")
    lines.append(f"**关系阶段**：{stage['label']}")
    lines.append(f"**执行优先级**：{exec_p['label']}（{'; '.join(exec_p['reasons'])}）")
    lines.append("")

    source_matched_operator = opp["reachability"]["confirmed_operator"]
    if source_matched_operator:
        channel = " -- X 私信似乎开放（冷启动渠道，非关系证明）" if source_matched_operator.can_dm else ""
        lines.append(
            f"**公开来源匹配的负责人候选**：{source_matched_operator.name}"
            f"（@{source_matched_operator.x_handle or '无记录账号'}）{channel}"
        )
        lines.append("身份、当前职位与预算影响力仍需 Mango 人工核实。")
    else:
        lines.append("**负责人候选**：尚未识别，需要研究")
    lines.append("")

    if stage["bridges"]:
        lines.append("**Connector 候选人（最多 3 位，按证据强弱排序，非自动首选）**：")
        for b in stage["bridges"][:3]:
            flag = " ⚠ 已证伪" if b["disproven"] else ""
            lines.append(f"- @{b['bridge_handle']}（{b['bridge_name'] or '无记录姓名'}，{fmt_num(b['bridge_followers_count'])} 粉丝）{flag}")
            lines.append(f"  - 阶段：{b['label']}")
            lines.append(f"  - 证据：{b['evidence_note']}")
            lines.append(f"  - 内部核实找：@{b['mango_side_handle']}")
            lines.append(f"  - 建议问题：{b['suggested_question']}")
    else:
        lines.append("**Connector 候选人**：暂无互关候选人")
    lines.append("")

    if plan:
        lines.append(f"**Direct path**：{'@' + plan['direct']['handle'] + '（' + plan['direct']['note'] + '）' if plan['direct'] else '无已知直接渠道'}")
        lines.append(f"**待核实引荐候选**：{('，'.join('@' + w['bridge_handle'] for w in plan['warm'])) if plan['warm'] else '无候选人'}（不代表认识或愿意引荐）")
        fallback = next(
            (step["detail"] for step in plan.get("steps", []) if step.get("kind") == "fallback"),
            "若当前路径无进展，回到官方渠道并补齐证据。",
        )
        lines.append(f"**Fallback**：{fallback}")
    else:
        lines.append("**Direct/Warm/Fallback**：商业价值不足以支撑当前无路径下的行动建议")
    lines.append("")

    paid = [s for s in c.sponsorships if s.disclosure_type == "paid_sponsorship" and s.review_status != "rejected"]
    confirmed_paid = [s for s in paid if s.review_status == "confirmed"]
    if confirmed_paid:
        lines.append(f"**Mango 可提供的具体价值**：该公司有 {len(confirmed_paid)} 条付费赞助证据已人工复核；仍需根据当前目标定制提案。")
    elif paid:
        lines.append(f"**Mango 可提供的具体价值**：该公司有 {len(paid)} 条公开付费赞助观察待人工复核；只可作为提案线索，不是已确认商业事实。")
    else:
        lines.append("**Mango 可提供的具体价值**：暂无该公司的付费赞助先例，需从同类公司的历史投放中类比论证。")
    lines.append("")
    return "\n".join(lines)


def fmt_num(n):
    return f"{n:,}" if n else "0"


def main() -> None:
    session = get_session()
    try:
        companies = (
            session.query(Company)
            .filter(Company.name.in_(PILOT_COMPANY_NAMES))
            .all()
        )
        by_name = {c.name: c for c in companies}
        ordered = [by_name[n] for n in PILOT_COMPANY_NAMES if n in by_name]

        out = ["# Connector Verification Research Appendix -- 2026-08-29", ""]
        out.append(
            "> **用途：只用于内部核实，不是引荐就绪名单或当前行动队列。** "
            "当前执行以 Cockpit Home / Network 和 OutreachLog 为准；E1–E3 公开线索不证明真实认识或愿意引荐。"
        )
        out.append("")
        out.append(
            "范围说明：本轮覆盖 7 家公司（原计划 10 家，另 3 家「高商业价值但完全无 operator/path」"
            "如 Gamma/ElevenLabs 需要新的真实 operator 搜索研究，本轮未做，避免编造负责人）。"
            "以下每一条事实均来自实时计算的真实数据，非手写猜测。"
        )
        out.append("")

        # -- Connector Interview Queue: grouped by which Mango-side person
        # to ask, so Solomon can verify multiple companies in one sitting
        # instead of being asked once per company.
        out.append("## Connector Interview Queue（按内部核实人分组）")
        out.append("")
        by_asker: dict[str, list[str]] = {}
        for c in ordered:
            stage = relationship_stage(c)
            for b in stage["bridges"][:3]:
                if b["disproven"]:
                    continue
                by_asker.setdefault(b["mango_side_handle"], []).append(
                    f"- [{c.name}] @{b['bridge_handle']}（{b['label']}）-- {b['suggested_question']}"
                )
        for asker, items in by_asker.items():
            out.append(f"### 询问 @{asker}")
            out.extend(items)
            out.append("")

        out.append("---")
        out.append("")
        for c in ordered:
            out.append(_company_writeup(c))

        report = "\n".join(out)
        out_path = Path(__file__).resolve().parent.parent / "data" / "connector_interview_queue.md"
        out_path.write_text(report)
        print(f"wrote {out_path}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
