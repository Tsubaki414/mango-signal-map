"""跑一遍筛选，把结果落成一份可读报告 + 原始 JSON。

给的是**这一轮的实际筛选结果**，不是示例数据。报告和接口走同一套代码，所以
纸面上看到的和前端拿到的是同一个判断 —— 两套逻辑各写一遍，迟早会分叉。

用法::

    python -m signal_map.scripts.report_bd_screening \\
        --verticals ai --platforms X --min-targets 5 \\
        --targets-from-candidates --out signal_map/reports
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from signal_map.backend import bd_discovery, bd_screening, db as sm_db, recommend  # noqa: E402
from signal_map.backend.bd_models import BD_PRIORITIES, BD_PRIORITY_LABELS_ZH, BDCandidate  # noqa: E402
from signal_map.backend.bd_screening import DomainPreferences  # noqa: E402
from signal_map.backend.models import Brief  # noqa: E402


def _csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verticals", default="ai")
    parser.add_argument("--market-regions", default="")
    parser.add_argument("--languages", default="")
    parser.add_argument("--audience-types", default="")
    parser.add_argument("--platforms", default="X")
    parser.add_argument("--targets", default="", help="comma-separated handles")
    parser.add_argument(
        "--targets-from-candidates", action="store_true",
        help="use every BDCandidate marked object_kind=target_person",
    )
    parser.add_argument("--min-targets", type=int, default=5)
    parser.add_argument("--top", type=int, default=40, help="rows per priority bucket")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "signal_map" / "reports")
    args = parser.parse_args()

    session = sm_db.get_session()
    try:
        handles = _csv(args.targets)
        if args.targets_from_candidates:
            handles += list(session.scalars(
                select(BDCandidate.handle).where(
                    BDCandidate.object_kind == "target_person",
                    BDCandidate.platform == "X",
                )
            ).all())

        prefs = DomainPreferences.from_dict({
            "verticals": args.verticals, "market_regions": args.market_regions,
            "languages": args.languages, "audience_types": args.audience_types,
            "platforms": args.platforms, "target_handles": sorted(set(handles)),
        })

        targets = bd_screening.resolve_target_nodes(session, prefs.target_handles)
        collected = bd_screening.collected_target_nodes(session, set(targets))
        relations = bd_screening.signals_by_creator(session, set(targets) or None)

        views = bd_discovery.build_views(
            session, target_accounts=targets, min_targets=args.min_targets,
            # roster centrality 默认不掺：那是「我们自己关注了谁」，不是「谁能买」。
            include_observed=False,
        )
        stored = bd_discovery.stored_signals_by_key(session, views)
        screenings = [
            bd_screening.screen(
                view, prefs, stored_signals=stored.get(view.key, []),
                relation_signals=(
                    relations.get(view.creator.id, []) if view.creator else []
                ),
                target_accounts=targets, collected_nodes=collected,
            )
            for view in views
        ]

        # 已有报价那一块，同一组偏好。
        brief = Brief(
            id=None, client_id=None, name="内部筛选报告（未落库）",
            verticals=",".join(prefs.verticals) or None,
            target_markets=",".join(prefs.market_regions) or None,
            content_languages=",".join(prefs.languages) or None,
            target_audiences=",".join(prefs.audience_types) or None,
            platforms=",".join(prefs.platforms) or None,
        )
        priced = [
            recommend.assess(creator, brief, relations.get(creator.id, []))
            for creator in recommend.candidate_pool(session)
        ]
        priced.sort(key=lambda a: a.rank_score, reverse=True)

        gaps = bd_discovery.collection_gaps(
            session, prefs, targets, min_targets=args.min_targets
        )
    finally:
        session.close()

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()

    # 每档只落 ``--top`` 条。全量导出会到 70MB+（5,196 个候选，每个都带上
    # 全部关注边），那不是给人看的东西，也不是接口的形状 —— 接口本来就分页。
    # 总数仍然如实报在 ``bucket_totals`` 里，被截掉的不会看起来像不存在。
    order = {key: index for index, key in enumerate(BD_PRIORITIES)}
    by_bucket: dict[str, list] = {key: [] for key in BD_PRIORITIES}
    for item in sorted(
        screenings,
        key=lambda s: (
            order.get(s.priority, 99),
            -s.relation.distinct_targets,
            -len(s.completeness.known),
            -(s.view.followers or 0),
        ),
    ):
        by_bucket.setdefault(item.priority, []).append(item)

    payload = {
        "generated_at": dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(),
        "preferences": prefs.as_dict(),
        "min_targets": args.min_targets,
        "rule_versions": {
            "recommendation": recommend.RULE_VERSION,
            "bd_screening": bd_screening.SCREENING_VERSION,
        },
        "targets_resolved": len(targets),
        "targets_collected": len(collected),
        "pool_size": len(screenings),
        "priced_pool_size": len(priced),
        "bucket_totals": {key: len(rows) for key, rows in by_bucket.items()},
        "rows_per_bucket": args.top,
        "bd_candidates": [
            bd_screening.screening_dict(s)
            for rows in by_bucket.values()
            for s in rows[: args.top]
        ],
        "collection_gaps": gaps,
    }
    json_path = args.out / f"bd_screening_{stamp}.json"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md_path = args.out / f"bd_screening_{stamp}.md"
    md_path.write_text(
        _render(prefs, args, screenings, priced, targets, collected, gaps),
        encoding="utf-8",
    )
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")


def _render(prefs, args, screenings, priced, targets, collected, gaps) -> str:
    order = {key: index for index, key in enumerate(BD_PRIORITIES)}
    screenings.sort(
        key=lambda s: (
            order.get(s.priority, 99),
            -s.relation.distinct_targets,
            -len(s.completeness.known),
            -(s.view.followers or 0),
        )
    )
    counts = {key: 0 for key in BD_PRIORITIES}
    for item in screenings:
        counts[item.priority] = counts.get(item.priority, 0) + 1

    lines: list[str] = [
        f"# 可 BD 筛选结果 · {dt.date.today().isoformat()}",
        "",
        "由 `signal_map.scripts.report_bd_screening` 生成，和 "
        "`POST /api/internal/bd/screen` 走同一套判断代码。",
        "",
        "## 本轮条件",
        "",
        f"- 领域：{'、'.join(prefs.verticals) or '未指定'}",
        f"- 市场：{'、'.join(prefs.market_regions) or '未指定'}",
        f"- 圈层：{'、'.join(prefs.audience_types) or '未指定'}",
        f"- 平台：{'、'.join(prefs.platforms) or '未指定'}",
        f"- 目标人物：选定 {len(prefs.target_handles)} 位，"
        f"已解析 {len(targets)} 位，已采关注列表 {len(collected)} 位",
        f"- 共同关注门槛：被 ≥ {args.min_targets} 位目标人物关注",
        "",
        "目标人物是客户希望被看见的人，**不是投放对象**。下面这份名单是"
        "「他们关注了谁」反推出来的可建联对象。",
        "",
        "## 结果概览",
        "",
        "| 优先级 | 人数 | 含义 |",
        "|---|---|---|",
    ]
    meanings = {
        "priority_inquiry": "商业合作信号明确 + 有可执行联系路径 → 直接询价",
        "confirm_willingness": "有联系方式、内容适配，但付费合作证据不足",
        "need_bd_path": "内容与关系有价值，但还没找到可执行联系人",
        "observe_only": "更适合作为目标人物、研究参考，或存在未解决的关键问题",
    }
    for key in BD_PRIORITIES:
        lines.append(
            f"| {BD_PRIORITY_LABELS_ZH[key]} | {counts.get(key, 0)} | {meanings[key]} |"
        )
    lines += [
        "",
        f"已有报价、可直接推荐的对象：{len(priced)} 个（走 13 轴引擎，不在下面这份名单里）。",
        "",
    ]

    for key in BD_PRIORITIES:
        rows = [s for s in screenings if s.priority == key]
        if not rows:
            continue
        lines += [f"## {BD_PRIORITY_LABELS_ZH[key]}（{len(rows)}）", ""]
        for item in rows[: args.top]:
            lines.extend(_card(item))
        if len(rows) > args.top:
            lines += [f"_……另有 {len(rows) - args.top} 位，见 JSON。_", ""]

    lines += ["## 采集缺口", ""]
    for gap in gaps:
        lines.append(f"- **{gap['kind']}** — {gap['detail']}")
        lines.append(f"  - 下一步：{gap['next_step']}")
    lines += [
        "",
        "零结果表示**当前未观察到**，不表示两者无关系。被页数截断的关注列表里"
        "「没有某条边」同样什么都不能说明。",
        "",
    ]
    return "\n".join(lines)


def _card(item) -> list[str]:
    view = item.view
    followers = f"{view.followers:,}" if view.followers else "待确认"
    who = [t["target_handle"] for t in item.relation.targets if t["target_handle"]]
    lines = [
        f"### @{view.handle}"
        + (f" · {view.display_name}" if view.display_name else ""),
        "",
        f"- 粉丝：{followers} · 来源：{bd_screening.SOURCE_KIND_LABELS_ZH.get(view.source_kind)}"
        f" · 身份：{item.view.object_kind}",
        f"- **为什么适合**：{item.domain_fit.summary}",
        f"- **关联目标**：{item.relation.distinct_targets} 位"
        + (f"（{'、'.join('@' + w for w in who[:8])}）" if who else "（当前未观察到）"),
        f"- **商务成熟度**：{item.commercial.LEVELS[item.commercial.level]}",
    ]
    for stance, row in item.commercial.stances.items():
        mark = "✓" if row["holds"] else "—"
        lines.append(f"  - {mark} {row['label_zh']}：{row['detail']}")
    for signal in item.commercial.signals[:4]:
        quote = signal.get("evidence_quote") or signal.get("evidence_url") or ""
        value = signal.get("value") or ""
        lines.append(
            f"  - 证据｜{signal['label_zh']}：{value} "
            + (f"「{str(quote)[:70]}」" if quote else "")
            + (f"（{signal['note']}）" if signal.get("note") else "")
        )
    for caveat in item.commercial.caveats:
        lines.append(f"  - ⚠︎ {caveat}")
    lines += [
        f"- **报价状态**：{item.quote_state['note']}",
        f"- **跟进状态**：{view.candidate.bd_status if view.candidate else 'new'}",
        f"- **证据完整度**：{item.completeness.coverage_text}"
        + (f"，缺 {'、'.join(item.completeness.missing)}" if item.completeness.missing else ""),
        f"- **下一步**：{item.next_step}",
    ]
    claims = view.candidate
    if claims is not None and (claims.source_score_raw or claims.source_tier_raw):
        lines.append(
            f"- 来源名单说法（{claims.source_claims_status}，未参与判断）："
            f"tier={claims.source_tier_raw} score={claims.source_score_raw}"
        )
    lines.append("")
    return lines


if __name__ == "__main__":
    main()
