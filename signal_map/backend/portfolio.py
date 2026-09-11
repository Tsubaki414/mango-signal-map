"""组合方案与预算比较 — several defensible portfolios from one brief.

The product point: a client comparing options is what "有掌控感" actually
means. One ranked list forces them to trust the ranking; several portfolios
with stated trade-offs lets them choose, and makes Mango's judgment legible.

Every scenario is built from the **same** assessments — the axes never change
between scenarios. What changes is the *selection strategy*: which axes it
prioritises and which dimension it spreads across. So two scenarios disagreeing
about a creator is a difference of purpose, not of fact.

Selection is greedy on **marginal coverage per dollar**, not on rank. Picking
the top-N by score produces five near-identical creators covering one audience
segment; the second developer-focused creator adds far less than the first
designer-focused one. What a portfolio is worth is what it *collectively*
reaches, so each pick is scored on what it adds to the set so far:

* audience types not yet covered
* market regions not yet covered
* content formats not yet covered
* **Roots not yet reached** — the attention layer, used here for the first time

Honesty rules carry over unchanged. Budget totals obey ``budget.py``: packages,
unconfirmed quotes and mixed currencies all force 需要 Mango 确认 rather than a
confident number. Coverage is only ever claimed for data that exists — a
creator with no Root signal contributes nothing to Root coverage and is not
penalised for it either.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .budget import BudgetLine, BudgetSummary, summarize
from .normalize import AUDIENCE_TYPE_LABELS_ZH, MARKET_REGION_LABELS_ZH

#: ``cost_sensitivity`` is how much cheapness should influence selection:
#: 0.0 ignores price entirely, 1.0 strongly prefers cheap. It exists because a
#: single global setting made every scenario pick the same cheap creators and
#: spend 1% of the budget -- which also made 专业可信度方案 contradict its own
#: "单价高" thesis. A credibility portfolio should buy the right people; a
#: conversion portfolio should buy volume.
#:
#: ``head_count`` seeds the portfolio with N largest-reach creators before the
#: greedy step, which is what "头部+垂直" actually means.
SCENARIOS: dict[str, dict] = {
    "credibility": {
        "label": "专业可信度方案",
        "thesis": "少而精：优先有专业受众和可验证注意力信号的战略型 KOL，接受更高单价",
        "prefer": ("attention_path", "objective", "audience", "track_record"),
        "tiers": ("strategic",),
        "max_picks": 6,
        "cost_sensitivity": 0.0,
        "head_count": 0,
        "tradeoff": "覆盖面较窄，单价高，适合背书与融资叙事，不适合铺量",
    },
    "coverage": {
        "label": "圈层覆盖方案",
        "thesis": "最大化不重复的受众圈层与市场，接受单个账号量级较小",
        "prefer": ("audience", "market_language", "attention_path"),
        "tiers": ("strategic", "distribution", "media"),
        "max_picks": 12,
        "cost_sensitivity": 0.35,
        "head_count": 0,
        "tradeoff": "单点声量弱，胜在广度；专业背书强度不如可信度方案",
    },
    "conversion": {
        "label": "产品转化方案",
        "thesis": "面向消费者与学习者的高频次投放，单价低、条数多",
        "prefer": ("audience", "budget", "content_format"),
        "tiers": ("strategic", "distribution"),
        "max_picks": 16,
        "cost_sensitivity": 0.7,
        "head_count": 0,
        "tradeoff": "偏铺量，品牌调性控制较弱，需要更强的内容审核",
    },
    "cross_border": {
        "label": "中英文跨境方案",
        "thesis": "覆盖多个语言与市场，用于跨境同步传播",
        "prefer": ("market_language", "audience"),
        "tiers": ("strategic", "distribution", "media"),
        "max_picks": 10,
        "cost_sensitivity": 0.3,
        "head_count": 0,
        "tradeoff": "各市场深度有限；非目标市场的支出需要客户确认是否值得",
    },
    "head_and_vertical": {
        "label": "头部与垂直混合方案",
        "thesis": "1–2 个量级头部账号带声量，其余垂直账号承接细分人群",
        "prefer": ("attention_path", "audience", "content_vertical"),
        "tiers": ("strategic", "distribution"),
        "max_picks": 8,
        "cost_sensitivity": 0.25,
        "head_count": 2,
        "tradeoff": "头部账号占用大部分预算，若头部表现不佳则整体风险集中",
    },
}

#: Stop adding once this much of the budget is committed. A portfolio that
#: spends 1% of the budget is not a plan -- the client asked how to deploy a
#: number, and leaving it unallocated silently answers a different question.
BUDGET_TARGET = 0.85

#: How many un-selected runners-up to surface per scenario. The spec asks for
#: 备选对象 explicitly -- a portfolio without alternates cannot be adjusted.
ALTERNATE_COUNT = 3


@dataclass
class Pick:
    """One creator inside a scenario, with what it added to the set."""

    creator_id: int
    creator_name: str
    role: str
    adds_audiences: list[str] = field(default_factory=list)
    adds_markets: list[str] = field(default_factory=list)
    adds_formats: list[str] = field(default_factory=list)
    adds_roots: int = 0
    marginal_score: float = 0.0


@dataclass
class Scenario:
    key: str
    label: str
    thesis: str
    tradeoff: str
    picks: list[Pick] = field(default_factory=list)
    alternates: list[str] = field(default_factory=list)
    budget: BudgetSummary | None = None
    audiences: list[str] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)
    roots_reached: int = 0
    #: 0.0 = every pick covers a distinct audience, 1.0 = all identical.
    audience_overlap: float = 0.0
    risks: list[str] = field(default_factory=list)
    #: Internal wording. May quote exact spend -- Mango's own surface reads it.
    notes: list[str] = field(default_factory=list)
    #: Client wording. Must never let the internal spend be recovered, including
    #: **by subtraction**: a note saying "仅用掉 7%（剩余 $37,125）" beside a
    #: visible budget of $40,000 hands over the exact cost. Percentages leak it
    #: just as surely as the remainder does.
    client_notes: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.picks)


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _creator_role(assessment, creator) -> str:
    """The 传播角色 this creator plays, from what the data actually supports.

    Assigned from tier + audience, never invented: a role with no supporting
    field would be exactly the kind of unfounded claim this product avoids.
    """
    audiences = set(_csv(creator.audience_types))
    if creator.creator_tier == "media":
        return "媒体节点"
    if creator.creator_tier == "distribution":
        return "回声节点"
    if audiences & {"developers", "researchers", "enterprise"}:
        return "专业验证者"
    if audiences & {"investors", "founders"}:
        return "圈层桥梁"
    if audiences & {"consumers", "students"}:
        return "转化推动者"
    if audiences & {"creators", "designers", "marketers"}:
        return "解释者"
    return "待定角色"


def _axis_value(assessment, key: str) -> float:
    axis = assessment.axis(key)
    if axis is None:
        return 0.0
    return {"match": 1.0, "partial": 0.5, "unknown": 0.2, "mismatch": 0.0}.get(
        axis.verdict, 0.3
    )


def _marginal(
    assessment,
    creator,
    signals: list,
    covered_audiences: set,
    covered_markets: set,
    covered_formats: set,
    covered_roots: set,
    config: dict,
) -> tuple[float, Pick]:
    """Score a candidate by what it adds to the set built so far."""
    audiences = set(_csv(creator.audience_types))
    markets = {creator.market_region} - {None}
    formats = {q.content_format for q in creator.quotes if q.amount_usd is not None}
    roots = {s.source_handle for s in signals if s.source_handle}

    new_audiences = sorted(audiences - covered_audiences)
    new_markets = sorted(markets - covered_markets)
    new_formats = sorted(formats - covered_formats)
    new_roots = roots - covered_roots

    novelty = (
        len(new_audiences) * 1.0
        + len(new_markets) * 1.2
        + len(new_formats) * 0.5
        # Root novelty is capped: reaching 40 new Roots is not 40x reaching
        # one, and an uncapped term would let a single well-connected account
        # dominate every scenario.
        + min(len(new_roots), 6) * 0.6
    )
    fit = sum(_axis_value(assessment, key) for key in config["prefer"]) / len(config["prefer"])

    # A portfolio that adds nothing new still has value if the creator is a
    # strong fit -- so novelty is a bonus on fit, not a gate.
    score = fit * 1.0 + novelty * 0.5

    return score, Pick(
        creator_id=assessment.creator_id,
        creator_name=assessment.creator_name,
        role=_creator_role(assessment, creator),
        adds_audiences=[AUDIENCE_TYPE_LABELS_ZH.get(a, a) for a in new_audiences],
        adds_markets=[MARKET_REGION_LABELS_ZH.get(m, m) for m in new_markets],
        adds_formats=new_formats,
        adds_roots=len(new_roots),
        marginal_score=round(score, 4),
    )


def build_scenario(
    key: str,
    assessments: list,
    creators_by_id: dict,
    signals_by_creator: dict,
    budget_usd: float | None,
) -> Scenario:
    """Greedily assemble one portfolio under the scenario's thesis."""
    config = SCENARIOS[key]
    scenario = Scenario(
        key=key, label=config["label"], thesis=config["thesis"], tradeoff=config["tradeoff"]
    )

    pool = [
        a
        for a in assessments
        if creators_by_id[a.creator_id].creator_tier in config["tiers"]
        and a.quote is not None
        and a.quote.amount_usd is not None
    ]
    if not pool:
        scenario.notes.append("当前候选池中没有符合该方案类型的对象")
        scenario.budget = summarize([], budget_usd)
        return scenario

    covered_a: set = set()
    covered_m: set = set()
    covered_f: set = set()
    covered_r: set = set()
    chosen: list = []
    spent = 0.0
    ceiling = budget_usd * BUDGET_TARGET if budget_usd else None

    def commit(assessment, creator, pick) -> None:
        nonlocal spent
        chosen.append(pick)
        spent += assessment.quote.amount_usd or 0.0
        covered_a.update(_csv(creator.audience_types))
        covered_m.update({creator.market_region} - {None})
        covered_f.update(q.content_format for q in creator.quotes if q.amount_usd is not None)
        covered_r.update(
            s.source_handle
            for s in signals_by_creator.get(assessment.creator_id, [])
            if s.source_handle
        )

    # Seed the largest-reach creators first where the thesis calls for it.
    # Greedy marginal-coverage alone never picks a head account: it is
    # expensive and covers audiences a cheaper creator also covers.
    if config["head_count"]:
        def reach(assessment) -> int:
            creator = creators_by_id[assessment.creator_id]
            return max((a.followers or 0) for a in creator.accounts) if creator.accounts else 0

        for assessment in sorted(pool, key=reach, reverse=True)[: config["head_count"]]:
            cost = assessment.quote.amount_usd or 0.0
            if budget_usd is not None and spent + cost > budget_usd:
                continue
            creator = creators_by_id[assessment.creator_id]
            _, pick = _marginal(
                assessment, creator, signals_by_creator.get(assessment.creator_id, []),
                covered_a, covered_m, covered_f, covered_r, config,
            )
            pick.role = "引爆者"
            commit(assessment, creator, pick)

    sensitivity = config["cost_sensitivity"]
    while len(chosen) < config["max_picks"]:
        if ceiling is not None and spent >= ceiling:
            break
        scored = []
        for assessment in pool:
            if any(c.creator_id == assessment.creator_id for c in chosen):
                continue
            creator = creators_by_id[assessment.creator_id]
            cost = assessment.quote.amount_usd or 0.0
            if budget_usd is not None and spent + cost > budget_usd:
                continue
            score, pick = _marginal(
                assessment, creator, signals_by_creator.get(assessment.creator_id, []),
                covered_a, covered_m, covered_f, covered_r, config,
            )
            # Cost divides the score only as far as the thesis wants it to. At
            # sensitivity 0 price is ignored entirely; at 0.7 a creator twice
            # the price needs ~1.6x the value to win.
            efficiency = score / max(cost, 1.0) ** sensitivity if sensitivity else score
            scored.append((efficiency, pick, assessment, creator))

        if not scored:
            break
        scored.sort(key=lambda row: -row[0])
        _, pick, assessment, creator = scored[0]
        commit(assessment, creator, pick)

    scenario.picks = chosen
    scenario.audiences = sorted(AUDIENCE_TYPE_LABELS_ZH.get(a, a) for a in covered_a)
    scenario.markets = sorted(MARKET_REGION_LABELS_ZH.get(m, m) for m in covered_m)
    scenario.formats = sorted(covered_f)
    scenario.roots_reached = len(covered_r)

    chosen_ids = {p.creator_id for p in chosen}
    scenario.alternates = [
        a.creator_name for a in pool if a.creator_id not in chosen_ids
    ][:ALTERNATE_COUNT]

    by_id = {a.creator_id: a for a in assessments}
    lines = [
        BudgetLine(
            creator_id=p.creator_id,
            creator_name=p.creator_name,
            quote_id=by_id[p.creator_id].quote.id if by_id[p.creator_id].quote else None,
            deliverable=by_id[p.creator_id].quote.deliverable_raw
            if by_id[p.creator_id].quote
            else None,
            unit_amount_usd=by_id[p.creator_id].quote.amount_usd
            if by_id[p.creator_id].quote
            else None,
            currency=(by_id[p.creator_id].quote.currency if by_id[p.creator_id].quote else "USD"),
            is_package=bool(by_id[p.creator_id].quote.is_package)
            if by_id[p.creator_id].quote
            else False,
            is_confirmed=False,
            client_visible_price=by_id[p.creator_id].quote.client_display_price
            if by_id[p.creator_id].quote
            else None,
        )
        for p in chosen
    ]
    scenario.budget = summarize(lines, budget_usd)
    scenario.audience_overlap = _overlap_ratio(chosen, creators_by_id)
    scenario.risks = _scenario_risks(scenario, chosen, creators_by_id)
    internal, client_facing = _utilisation_notes(scenario, chosen, budget_usd, config)
    scenario.notes.extend(internal)
    scenario.client_notes.extend(client_facing)
    return scenario


def _utilisation_notes(
    scenario: Scenario, chosen: list, budget_usd: float | None, config: dict
) -> tuple[list[str], list[str]]:
    """Explain leftover budget instead of quietly spending or ignoring it.

    Under-spend here is usually not a selection failure -- this roster is
    genuinely inexpensive, so a large budget cannot be absorbed by a
    sensibly-sized portfolio. Padding the list to hit the number would be
    worse than saying so, and the client's real options (more deliverables per
    creator, or a wider list) are decisions only they can make.
    """
    spent = scenario.budget.total_usd if scenario.budget else None
    if budget_usd is None or spent is None or not chosen:
        return [], []

    used = spent / budget_usd
    if used >= 0.5:
        return [], []

    exhausted = len(chosen) >= config["max_picks"]
    reason = (
        f"该方案已达 {config['max_picks']} 人上限"
        if exhausted
        else "候选池中已无更多符合该方案类型的对象"
    )
    advice = "可考虑增加单人投放条数、放宽方案类型，或扩大名单。"

    internal = [
        f"本方案内部成本 ${spent:,.0f}，占预算 {used:.0%}（剩余 ${budget_usd - spent:,.0f}）：{reason}。"
    ]
    # No figure and no percentage: with the budget visible in the same payload,
    # either one lets the client subtract their way to Mango's cost.
    client_facing = [f"本方案未用满预算：{reason}，且候选池单价偏低。{advice}"]
    return internal, client_facing


def _overlap_ratio(picks: list, creators_by_id: dict) -> float:
    """How much the picks' audiences repeat each other.

    0.0 = every pick covers something new; 1.0 = all identical. Reported rather
    than optimised away: some overlap is deliberate (repetition drives recall),
    and the client is entitled to see how much they are buying.
    """
    if len(picks) < 2:
        return 0.0
    total = 0
    distinct: set = set()
    for pick in picks:
        audiences = set(_csv(creators_by_id[pick.creator_id].audience_types))
        total += len(audiences)
        distinct |= audiences
    if total == 0:
        return 0.0
    return round(1 - (len(distinct) / total), 3)


def _scenario_risks(scenario: Scenario, picks: list, creators_by_id: dict) -> list[str]:
    risks: list[str] = []
    if not picks:
        return ["无法在当前预算与候选池内组成该方案"]
    if scenario.audience_overlap > 0.6:
        risks.append(f"受众重叠较高（{scenario.audience_overlap:.0%}），新增覆盖有限")
    if scenario.roots_reached == 0:
        risks.append("该组合暂无已观察到的注意力信号，圈层进入路径待补充")
    tiers = [creators_by_id[p.creator_id].creator_tier for p in picks]
    if tiers and all(t == "distribution" for t in tiers):
        risks.append("全部为铺量型账号，专业背书强度不足")
    if scenario.budget and scenario.budget.needs_mango_confirmation:
        risks.append("总价包含套餐或未复核报价，需要 Mango 确认后才能作为承诺")
    if len(picks) == 1:
        risks.append("仅一个投放对象，结果高度依赖单点表现")
    # A "head + vertical" plan that delivered no vertical half is not a smaller
    # version of the thesis -- it is a different plan, and saying so is the
    # difference between a portfolio and a single expensive bet.
    heads = sum(1 for p in picks if p.role == "引爆者")
    if heads and heads == len(picks):
        risks.append("仅有头部账号，垂直承接部分未能在预算内组成")
    return risks


def build_all(
    assessments: list,
    creators_by_id: dict,
    signals_by_creator: dict,
    budget_usd: float | None,
    keys: list[str] | None = None,
) -> list[Scenario]:
    """Build every scenario from one set of assessments.

    Scenarios that cannot be assembled are still returned, carrying the reason
    — a missing option is information ("我们没有足够的垂直专家做这个方案"), and
    silently omitting it would read as though it was never considered.
    """
    return [
        build_scenario(key, assessments, creators_by_id, signals_by_creator, budget_usd)
        for key in (keys or list(SCENARIOS))
    ]
