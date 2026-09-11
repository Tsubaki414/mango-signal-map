"""Explainable recommendation service.

The product rule this module exists to enforce: **never return one
unexplainable score.** Twelve axes are judged independently and all twelve
travel with the result. A ``rank_score`` exists for ordering, but no surface
may show it alone, and it is derived from the axes rather than replacing them.

Three behaviours that look like bugs and are not:

* **Unknown never eliminates.** A creator with no data on an axis is ranked
  lower and labelled 待确认, not dropped. Absent data lowers confidence; it
  does not disprove fit. The same rule that governs Root Signals.
* **Mismatch does not eliminate either.** A creator who misses a stated
  preference stays in the result with the miss recorded in
  ``unmet_conditions``, because the client is entitled to see what Mango
  considered and why it ranked low. Only three things remove a creator, and
  each is counted and reported (see ``HardExclusion``).
* **Reasons are templates filled from stored fields.** No model writes
  recommendation prose. An LLM classified the audience upstream, and its
  verdict is stored with quoted evidence; here it is just another field.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .budget import BudgetLine, summarize
from .models import (
    CLIENT_VISIBLE_QUOTE_STATUSES,
    AttentionSignal,
    Brief,
    Creator,
    Quote,
    Recommendation,
    RecommendationRun,
)
from .normalize import MARKET_REGION_LABELS_ZH

RULE_VERSION = "signalmap-reco-v1"

#: The twelve judgments. Order is the display order on a client card.
AXES: tuple[tuple[str, str], ...] = (
    ("content_vertical", "内容与行业"),
    ("market_language", "市场与语言"),
    ("platform", "平台"),
    ("content_format", "内容形式"),
    ("audience", "目标人群"),
    ("objective", "传播目标"),
    ("budget", "预算可执行性"),
    ("quote_certainty", "报价确定程度"),
    ("commercial", "商业可执行性"),
    ("track_record", "历史合作"),
    ("risk", "风险"),
    ("data_confidence", "数据可信度"),
    # Added last on purpose: Root signals are a *new axis*, not a rewrite. A
    # creator with none is ranked lower and labelled 关系数据待补充 -- never
    # eliminated, exactly like any other unknown.
    ("attention_path", "注意力路径"),
)

#: match     -- meets the stated preference
#: partial   -- meets it in part
#: mismatch  -- conflicts with it (recorded, never silently dropped)
#: unknown   -- Mango has no data (lowers confidence, never eliminates)
#: not_asked -- the brief did not state a preference on this axis
VERDICTS = ("match", "partial", "mismatch", "unknown", "not_asked")

_VERDICT_SCORE = {"match": 1.0, "partial": 0.5, "mismatch": 0.0, "unknown": 0.25, "not_asked": None}

#: Relative weight per axis when composing the internal ordering value.
#: Deliberately flat-ish: a steep curve would recreate the single-score
#: problem by letting one axis dominate the others invisibly.
_AXIS_WEIGHTS = {
    "content_vertical": 1.4,
    "market_language": 1.3,
    "platform": 1.2,
    # Weighted near budget on purpose: if the client asked for a dedicated
    # video and this creator only sells Shorts, the requested deliverable is
    # not purchasable from them. That is a "can I actually buy this" question,
    # not a stylistic preference, and it was ranking above creators who did
    # offer the format when weighted at 0.8.
    "content_format": 1.3,
    "audience": 1.3,
    "objective": 1.0,
    "budget": 1.5,
    "quote_certainty": 0.9,
    "commercial": 1.0,
    "track_record": 0.7,
    "risk": 0.8,
    "data_confidence": 0.6,
    # Modest weight while the evidence is bare follows. It should rise only
    # when interaction evidence (stage 3) upgrades signals past 研究线索 --
    # weighting a follow like a relationship is the overclaim this whole
    # layer exists to avoid.
    "attention_path": 0.9,
}

#: 传播目标 -> what actually supports it in the data we hold.
_OBJECTIVE_RULES: dict[str, dict] = {
    "credibility": {  # 专业背书
        "label": "专业背书",
        "prefer_tiers": ("strategic",),
        "prefer_audiences": ("developers", "researchers", "founders", "investors", "enterprise"),
        "penalise_high_promotion": True,
    },
    "awareness": {  # 品牌认知
        "label": "品牌认知",
        "prefer_tiers": ("strategic", "distribution", "media"),
        "prefer_audiences": (),
        "penalise_high_promotion": False,
    },
    "trial": {  # 产品试用
        "label": "产品试用",
        "prefer_tiers": ("strategic", "distribution"),
        "prefer_audiences": ("developers", "creators", "marketers", "designers", "consumers"),
        "penalise_high_promotion": False,
    },
    "conversion": {  # 注册转化
        "label": "注册转化",
        "prefer_tiers": ("distribution", "strategic"),
        "prefer_audiences": ("consumers", "students", "creators"),
        "penalise_high_promotion": False,
    },
    "funding_narrative": {  # 融资叙事
        "label": "融资叙事",
        "prefer_tiers": ("strategic",),
        "prefer_audiences": ("investors", "founders", "researchers"),
        "penalise_high_promotion": True,
    },
}


@dataclass
class AxisResult:
    key: str
    label_zh: str
    verdict: str
    #: Internal wording. May name exact costs -- Mango's own surface reads this.
    detail: str
    evidence: str | None = None
    #: Client wording, when it must differ. The budget axis is the case that
    #: forces this to exist: its internal detail quotes ``amount_usd``, which
    #: is what the creator asked Mango for, so serialising it to a client
    #: would leak the cost inside a prose string -- past every field-name
    #: allowlist. ``client_safe`` reads this, never ``detail``.
    client_detail: str | None = None

    @property
    def detail_for_client(self) -> str:
        return self.client_detail if self.client_detail is not None else self.detail


@dataclass
class HardExclusion:
    creator_id: int
    creator_name: str
    reason: str


@dataclass
class CreatorAssessment:
    """One creator judged against one brief."""

    creator_id: int
    creator_name: str
    axes: list[AxisResult] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    unmet: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    quote: Quote | None = None
    rank_score: float = 0.0

    def axis(self, key: str) -> AxisResult | None:
        return next((a for a in self.axes if a.key == key), None)


# --- helpers -----------------------------------------------------------------


def _csv(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _overlap(a: Iterable[str], b: Iterable[str]) -> list[str]:
    right = {item.lower() for item in b}
    return [item for item in a if item.lower() in right]


def _cheapest_quote(creator: Creator, formats: list[str], platforms: list[str]) -> Quote | None:
    """The cheapest priced quote, preferring one that matches the brief.

    Preference order matters: recommending a creator on the basis of their
    cheapest deliverable when the client asked for a different format would
    make the budget figure meaningless.
    """
    priced = [q for q in creator.quotes if q.amount_usd is not None]
    if not priced:
        return None
    for candidates in (
        [q for q in priced if (not formats or q.content_format in formats)
         and (not platforms or q.platform in platforms)],
        [q for q in priced if not formats or q.content_format in formats],
        [q for q in priced if not platforms or q.platform in platforms],
        priced,
    ):
        if candidates:
            return min(candidates, key=lambda q: q.amount_usd)
    return None


# --- axis evaluators ---------------------------------------------------------


def _axis_content(creator: Creator, brief: Brief) -> AxisResult:
    wanted = _csv(brief.verticals)
    have = _csv(creator.verticals)
    if not wanted:
        return AxisResult("content_vertical", "内容与行业", "not_asked", "客户未指定行业")
    if not have:
        return AxisResult("content_vertical", "内容与行业", "unknown", "该创作者的内容领域待确认")
    hits = _overlap(wanted, have)
    if not hits:
        return AxisResult(
            "content_vertical", "内容与行业", "mismatch",
            f"内容领域为 {'、'.join(have)}，与需求 {'、'.join(wanted)} 不重合",
        )
    verdict = "match" if len(hits) == len(wanted) else "partial"
    return AxisResult(
        "content_vertical", "内容与行业", verdict,
        f"覆盖 {'、'.join(hits)}", evidence=f"verticals={','.join(have)}",
    )


def _axis_market(creator: Creator, brief: Brief) -> AxisResult:
    wanted_markets = _csv(brief.target_markets)
    wanted_langs = _csv(brief.content_languages)
    if not wanted_markets and not wanted_langs:
        return AxisResult("market_language", "市场与语言", "not_asked", "客户未指定市场或语言")

    bits: list[str] = []
    verdicts: list[str] = []

    if wanted_markets:
        if creator.market_region is None:
            verdicts.append("unknown")
            bits.append("市场待确认")
        elif creator.market_region in wanted_markets:
            verdicts.append("match")
            label = MARKET_REGION_LABELS_ZH.get(creator.market_region, creator.market_region)
            bits.append(f"覆盖{label}（依据：{_basis_zh(creator.market_region_basis)}）")
        else:
            verdicts.append("mismatch")
            label = MARKET_REGION_LABELS_ZH.get(creator.market_region, creator.market_region)
            bits.append(f"主要面向{label}，与目标市场不符")

    if wanted_langs:
        have = _csv(creator.languages)
        if not have:
            verdicts.append("unknown")
            bits.append("内容语言待确认")
        elif _overlap(wanted_langs, have):
            verdicts.append("match")
            bits.append(f"内容语言 {'、'.join(have)}")
        else:
            verdicts.append("mismatch")
            bits.append(f"内容语言为 {'、'.join(have)}")

    return AxisResult("market_language", "市场与语言", _combine(verdicts), "；".join(bits))


def _basis_zh(basis: str | None) -> str:
    return {
        "language": "内容语言", "country": "创作者所在地", "provenance": "来源名单",
        "bio_script": "简介语种", "content": "内容判读", "vertical": "内容领域",
        "llm": "内容判读", "unknown": "未知",
    }.get(basis or "unknown", basis or "未知")


def _combine(verdicts: list[str]) -> str:
    """Worst-but-informative combination of sub-verdicts."""
    if not verdicts:
        return "not_asked"
    if "mismatch" in verdicts:
        return "mismatch" if all(v == "mismatch" for v in verdicts) else "partial"
    if "unknown" in verdicts:
        return "unknown" if all(v == "unknown" for v in verdicts) else "partial"
    return "match" if all(v == "match" for v in verdicts) else "partial"


def _axis_platform(creator: Creator, brief: Brief) -> AxisResult:
    wanted = _csv(brief.platforms)
    if not wanted:
        return AxisResult("platform", "平台", "not_asked", "客户未指定平台")
    have = sorted({q.platform for q in creator.quotes if q.amount_usd is not None} |
                  {a.platform for a in creator.accounts})
    have = [p for p in have if p and p != "Unknown"]
    if not have:
        return AxisResult("platform", "平台", "unknown", "平台信息待确认")
    hits = _overlap(wanted, have)
    if not hits:
        return AxisResult("platform", "平台", "mismatch", f"在 {'、'.join(have)}，不含目标平台")
    return AxisResult("platform", "平台", "match" if len(hits) == len(wanted) else "partial",
                      f"可在 {'、'.join(hits)} 投放")


def _axis_format(creator: Creator, brief: Brief, quote: Quote | None) -> AxisResult:
    wanted = _csv(brief.content_formats)
    if not wanted:
        return AxisResult("content_format", "内容形式", "not_asked", "客户未指定内容形式")
    have = sorted({q.content_format for q in creator.quotes if q.amount_usd is not None})
    have = [f for f in have if f and f != "unknown"]
    if not have:
        return AxisResult("content_format", "内容形式", "unknown", "报价对应的内容形式待确认")
    hits = _overlap(wanted, have)
    if not hits:
        return AxisResult("content_format", "内容形式", "mismatch",
                          f"现有报价形式为 {'、'.join(have)}")
    return AxisResult("content_format", "内容形式", "match" if len(hits) == len(wanted) else "partial",
                      f"已有 {'、'.join(hits)} 的报价")


def _axis_audience(creator: Creator, brief: Brief) -> AxisResult:
    wanted = _csv(brief.target_audiences)
    if not wanted:
        return AxisResult("audience", "目标人群", "not_asked", "客户未指定目标人群")
    have = _csv(creator.audience_types)
    if not have:
        return AxisResult("audience", "目标人群", "unknown", "受众类型待确认")
    hits = _overlap(wanted, have)
    evidence = creator.audience_types_evidence
    if not hits:
        return AxisResult("audience", "目标人群", "mismatch",
                          f"主要触达 {'、'.join(have)}", evidence=evidence)
    return AxisResult("audience", "目标人群", "match" if len(hits) == len(wanted) else "partial",
                      f"触达 {'、'.join(hits)}（依据：{_basis_zh(creator.audience_types_basis)}）",
                      evidence=evidence)


def _axis_objective(creator: Creator, brief: Brief) -> AxisResult:
    wanted = _csv(brief.objectives)
    if not wanted:
        return AxisResult("objective", "传播目标", "not_asked", "客户未指定传播目标")
    known = [o for o in wanted if o in _OBJECTIVE_RULES]
    if not known:
        return AxisResult("objective", "传播目标", "unknown", f"未识别的传播目标：{'、'.join(wanted)}")

    bits: list[str] = []
    verdicts: list[str] = []
    audiences = _csv(creator.audience_types)
    for objective in known:
        rule = _OBJECTIVE_RULES[objective]
        tier_ok = creator.creator_tier in rule["prefer_tiers"]
        aud_ok = not rule["prefer_audiences"] or bool(_overlap(rule["prefer_audiences"], audiences))
        if creator.creator_tier == "unknown":
            verdicts.append("unknown")
            bits.append(f"{rule['label']}：账号类型待分类")
        elif tier_ok and aud_ok:
            verdicts.append("match")
            bits.append(f"适合{rule['label']}")
        elif tier_ok or aud_ok:
            verdicts.append("partial")
            bits.append(f"部分适合{rule['label']}")
        else:
            verdicts.append("mismatch")
            bits.append(f"不适合{rule['label']}")
    return AxisResult("objective", "传播目标", _combine(verdicts), "；".join(bits))


def _axis_budget(creator: Creator, brief: Brief, quote: Quote | None) -> AxisResult:
    if quote is None or quote.amount_usd is None:
        return AxisResult("budget", "预算可执行性", "unknown", "暂无可用报价，价格待 Mango 确认")

    # Name the deliverable the price is actually for. When no quote matches the
    # requested format, ``_cheapest_quote`` falls back to another one -- and a
    # bare "$200 在预算区间内" would then read as the price for what the client
    # asked for, which it is not.
    wanted_formats = _csv(brief.content_formats)
    # ``unknown`` is the parser saying it could not tell, so say that rather
    # than printing the sentinel: "$500 以下（unknown）" reads as a product bug
    # on a client card, and 缺失数据显示未知 is the standing rule.
    deliverable = quote.deliverable_raw or (
        "合作形式待确认" if quote.content_format in {None, "unknown"} else quote.content_format
    )
    off_spec = bool(wanted_formats) and quote.content_format not in wanted_formats
    suffix = f"（{deliverable}，非所需形式）" if off_spec else f"（{deliverable}）"

    low, high = brief.per_creator_budget_min_usd, brief.per_creator_budget_max_usd
    price = quote.amount_usd
    # The client never sees the figure -- only which band it falls in.
    band = quote.client_price_band or "价格待 Mango 确认"

    if low is None and high is None:
        if brief.total_budget_usd is not None and price > brief.total_budget_usd:
            return AxisResult(
                "budget", "预算可执行性", "mismatch",
                f"最低报价 ${price:,.0f}{suffix} 已超出项目总预算",
                client_detail=f"{band}{suffix}，已超出项目总预算",
            )
        return AxisResult(
            "budget", "预算可执行性", "not_asked", f"最低报价 ${price:,.0f}{suffix}",
            client_detail=f"{band}{suffix}",
        )
    if high is not None and price > high:
        return AxisResult(
            "budget", "预算可执行性", "mismatch",
            f"最低报价 ${price:,.0f}{suffix}，高于单人预算上限 ${high:,.0f}",
            client_detail=f"{band}{suffix}，高于单人预算上限 ${high:,.0f}",
        )
    if low is not None and price < low:
        return AxisResult(
            "budget", "预算可执行性", "partial",
            f"报价 ${price:,.0f}{suffix} 低于预算区间，可考虑加量",
            client_detail=f"{band}{suffix}，低于预算区间，可考虑加量",
        )
    verdict = "partial" if off_spec else "match"
    return AxisResult(
        "budget", "预算可执行性", verdict, f"报价 ${price:,.0f}{suffix} 在预算区间内",
        client_detail=f"{band}{suffix}，在预算区间内",
    )


def _axis_quote_certainty(creator: Creator, quote: Quote | None) -> AxisResult:
    if quote is None:
        return AxisResult("quote_certainty", "报价确定程度", "unknown", "无报价记录")
    if quote.status in CLIENT_VISIBLE_QUOTE_STATUSES and not quote.needs_review:
        return AxisResult("quote_certainty", "报价确定程度", "match", "报价已确认且可对客展示")
    if quote.needs_review:
        # Everything imported from Mango BD is here: BD stored no quote date,
        # source, or validity for any price.
        return AxisResult("quote_certainty", "报价确定程度", "partial",
                          "报价需复核（缺少报价时间与来源）",
                          evidence=quote.review_reason)
    return AxisResult("quote_certainty", "报价确定程度", "partial", f"报价状态：{quote.status}")


def _axis_commercial(creator: Creator) -> AxisResult:
    routes = [r for r in creator.procurement_routes]
    verified = [r for r in routes if r.verified_at]
    has_contact = bool(creator.contacts)
    if verified:
        return AxisResult("commercial", "商业可执行性", "match",
                          "已有验证过的采购路径，由 Mango 对接")
    if routes:
        return AxisResult("commercial", "商业可执行性", "partial", "已有采购路径，尚未验证")
    if has_contact:
        return AxisResult("commercial", "商业可执行性", "partial",
                          "已有联系方式，采购路径待确认")
    return AxisResult("commercial", "商业可执行性", "unknown", "商务路径待补充")


def _axis_track_record(creator: Creator) -> AxisResult:
    evidence = list(creator.sponsorships)
    if not evidence:
        return AxisResult("track_record", "历史合作", "unknown", "暂无历史合作记录")
    confirmed = [
        e for e in evidence
        if e.evidence_type == "paid_sponsorship" and e.review_status == "confirmed"
    ]
    if len(confirmed) >= 2:
        return AxisResult("track_record", "历史合作", "match",
                          f"{len(confirmed)} 条已复核付费赞助证据")
    if confirmed:
        return AxisResult("track_record", "历史合作", "partial", "1 条已复核付费赞助证据")
    # product-language.md: unreviewed paid rows are 付费观察·待审核, never
    # presented as confirmed sponsorship.
    return AxisResult("track_record", "历史合作", "partial",
                      f"{len(evidence)} 条付费观察·待审核")


def _axis_risk(creator: Creator, brief: Brief) -> AxisResult:
    risks: list[str] = []
    if creator.promotion_level == "High":
        risks.append("推广浓度高")
    if creator.creator_tier == "distribution":
        risks.append("铺量型账号，声量背书有限")
    if creator.creator_tier == "unknown":
        risks.append("账号类型未分类")
    if not risks:
        return AxisResult("risk", "风险", "match", "未发现明显风险信号")
    tolerance = (brief.risk_tolerance or "").lower()
    verdict = "partial" if tolerance in {"medium", "high"} else "mismatch"
    return AxisResult("risk", "风险", verdict, "；".join(risks))


def _axis_attention(creator: Creator, signals: list) -> AxisResult:
    """KOL–Root 注意力路径 (path 3).

    Reports *observed follow signals only*. A follow proves a follow existed at
    observation time -- not that the Root reads this creator, endorses them,
    saw any post, or that a campaign will reach them. X does not expose when a
    follow began, so recency is never claimed either.

    No signals is **当前未观察到**, rendered 关系数据待补充. It is an
    ``unknown``, not a ``mismatch``: we looked at some Roots, not all of them,
    and absence in a partial sample disproves nothing.

    Review status changes the sentence. An accepted Root is an account a person
    confirmed is worth reaching; an unreviewed candidate is an account an
    algorithm surfaced, and a third of those turn out to be peer creators in
    the roster's own mutual-follow network (see ``root_review``). Reading the
    two alike would let a growth-pod follow render as an attention path, so
    unreviewed signals say **候选账号** and say it in the client wording too.

    A **rejected** Root is dropped upstream in ``attention_signals_by_creator``
    -- a person has said that account is not a Root, and re-stating its follow
    here would be reporting evidence that was already withdrawn.

    Only accepted Roots are **named to a client**, and this is the second
    reason ``client_detail`` exists after the cost leak. Naming an unreviewed
    candidate did three wrong things at once: it presented @AlfaizAliX -- an
    account that follows 70 of Mango's creators, 9.5% of its whole follow list
    -- as an attention path; it picked which three to show by ``sorted()``, so
    the client saw the alphabetically-first accounts rather than the strongest;
    and it published which accounts Mango tracks, which is research the
    internal-only list covers. Internally the handles stay, because Mango has
    to be able to check the claim.
    """
    if not signals:
        return AxisResult(
            "attention_path", "注意力路径", "unknown",
            "关系数据待补充：当前未观察到目标圈层的关注信号",
        )

    accepted = [s for s in signals if getattr(s, "root_type", None)]
    basis = accepted or signals
    roots = sorted({s.source_handle for s in basis if s.source_handle})
    shown = "、".join(f"@{r}" for r in roots[:3])
    more = f" 等 {len(roots)} 个账号" if len(roots) > 3 else ""
    limited = any(s.coverage_limitation for s in basis)

    interactions = [s for s in basis if s.signal_type != "follow"]
    strong = [s for s in basis if s.confidence == "high_confidence_inference"]

    if accepted:
        types = sorted({s.root_type_label for s in accepted if s.root_type_label})
        who = f"已确认 Root（{'、'.join(types)}）" if types else "已确认 Root"
        # Naming them is the whole value: "Andrew Ng follows him" is the
        # sentence a client is paying for, and it is checkable.
        client_who, client_shown = who, f" {shown}{more} "
    else:
        who = "候选账号（尚未人工确认为 Root）"
        # No handle, so no spacing around one either -- the space belongs to
        # the @handle, not to the verb after it.
        client_who = f" {len(roots)} 个待确认账号"
        client_shown = ""

    # The evidence ladder from product-language.md. A follow and a reply are
    # different facts and must not read alike; only the *pattern* (2+ recent
    # interactions) earns 高概率推断, and nothing here reaches 已验证事实 --
    # that needs a human, not an API.
    def _sentence(who_text: str, names: str) -> str:
        if strong:
            return f"被{who_text}{names}近期多次互动（高概率推断）"
        if interactions:
            kinds = "、".join(sorted({s.signal_type for s in interactions}))
            return f"被{who_text}{names}公开{kinds}过（历史熟悉度信号，非已验证关系）"
        return f"被{who_text}{names}关注（关注信号，非互动证据）"

    detail = _sentence(who, f" {shown}{more} ")
    client_detail = _sentence(client_who, client_shown)
    if not accepted:
        # Say plainly why no name is given, rather than looking like an
        # omission. An unreviewed candidate is 研究线索 and nothing more.
        client_detail += "；这些账号尚未经 Mango 人工确认，仅作研究线索"
    if limited:
        detail += "；部分名单未采全"
        client_detail += "；部分名单未采全"
    if accepted and len(accepted) < len(signals):
        remainder = f"；另有 {len(signals) - len(accepted)} 条来自未审核候选，未计入"
        detail += remainder
        client_detail += remainder

    # Caps at partial regardless: every grade below 已验证事实 is an
    # inference, and only a human check may call it a relationship.
    return AxisResult(
        "attention_path", "注意力路径", "partial", detail,
        evidence=(
            f"{len(basis)} 条已观察信号"
            f"（关注 {len(basis) - len(interactions)} · 互动 {len(interactions)}）"
        ),
        client_detail=client_detail,
    )


def _axis_data_confidence(creator: Creator, quote: Quote | None) -> AxisResult:
    gaps: list[str] = []
    if creator.market_region is None:
        gaps.append("市场")
    if creator.audience_types is None:
        gaps.append("受众")
    if creator.verticals is None:
        gaps.append("内容领域")
    if quote is not None and quote.parse_confidence in {"low", "unparsed"}:
        gaps.append("报价解析")
    if not gaps:
        return AxisResult("data_confidence", "数据可信度", "match", "关键字段均有数据支撑")
    return AxisResult("data_confidence", "数据可信度", "partial", f"待确认：{'、'.join(gaps)}")


# --- assessment --------------------------------------------------------------


def assess(creator: Creator, brief: Brief, signals: list | None = None) -> CreatorAssessment:
    """Judge one creator against one brief across every axis.

    ``signals`` are this creator's observed path-3 attention signals. Defaults
    to none, which yields 关系数据待补充 rather than an error -- the Root graph
    is partial by construction and a caller without it must still get a full
    assessment.
    """
    formats = _csv(brief.content_formats)
    platforms = _csv(brief.platforms)
    quote = _cheapest_quote(creator, formats, platforms)

    axes = [
        _axis_content(creator, brief),
        _axis_market(creator, brief),
        _axis_platform(creator, brief),
        _axis_format(creator, brief, quote),
        _axis_audience(creator, brief),
        _axis_objective(creator, brief),
        _axis_budget(creator, brief, quote),
        _axis_quote_certainty(creator, quote),
        _axis_commercial(creator),
        _axis_track_record(creator),
        _axis_risk(creator, brief),
        _axis_data_confidence(creator, quote),
        _axis_attention(creator, signals or []),
    ]

    assessment = CreatorAssessment(
        creator_id=creator.id, creator_name=creator.display_name, axes=axes, quote=quote
    )

    # These three lists are serialised to clients, so they use the client
    # wording. The internal surface reads ``axes[].detail`` for exact figures.
    for axis in axes:
        if axis.verdict in {"match", "partial"} and axis.key not in {"risk", "data_confidence"}:
            assessment.matched.append(f"{axis.label_zh}：{axis.detail_for_client}")
        elif axis.verdict == "mismatch":
            assessment.unmet.append(f"{axis.label_zh}：{axis.detail_for_client}")
        elif axis.verdict == "unknown":
            assessment.missing.append(axis.label_zh)

    assessment.rank_score = _score(axes)
    assessment.reasons = _reasons(creator, axes, quote)
    assessment.risks = _risks(creator, axes, quote)
    assessment.next_steps = _next_steps(creator, quote, axes)
    return assessment


def _score(axes: list[AxisResult]) -> float:
    """Internal ordering value only. Never rendered on its own."""
    total = weight_sum = 0.0
    for axis in axes:
        value = _VERDICT_SCORE[axis.verdict]
        if value is None:  # not_asked -- excluded, not scored as zero
            continue
        weight = _AXIS_WEIGHTS.get(axis.key, 1.0)
        total += value * weight
        weight_sum += weight
    return round(total / weight_sum, 4) if weight_sum else 0.0


def _reasons(creator: Creator, axes: list[AxisResult], quote: Quote | None) -> list[str]:
    """Rule-generated, template-filled. No model writes this text."""
    by_key = {axis.key: axis for axis in axes}
    reasons: list[str] = []

    for key in ("content_vertical", "market_language", "audience", "objective"):
        axis = by_key.get(key)
        if axis and axis.verdict in {"match", "partial"}:
            reasons.append(f"{axis.label_zh}：{axis.detail}")

    if quote is not None and quote.amount_usd is not None:
        # Band, not the figure: reasons are serialised to clients, and
        # ``amount_usd`` is Mango's cost.
        band = quote.client_price_band or "价格待 Mango 确认"
        reasons.append(
            f"已有报价：{quote.deliverable_raw or '合作形式待确认'}（{quote.platform}，{band}）"
        )
    track = by_key.get("track_record")
    if track and track.verdict == "match":
        reasons.append(f"历史合作：{track.detail}")
    return reasons


def _risks(creator: Creator, axes: list[AxisResult], quote: Quote | None) -> list[str]:
    risks: list[str] = []
    risk_axis = next((a for a in axes if a.key == "risk"), None)
    if risk_axis and risk_axis.verdict != "match":
        risks.append(risk_axis.detail)
    if quote is not None and quote.needs_review:
        risks.append("报价未经复核，最终价格可能变动")
    if quote is not None and quote.is_package:
        risks.append("该报价为套餐价，不能与单条价格直接相加")
    if not creator.contacts:
        risks.append("尚无可用联系方式，需要补充商务路径")
    return risks


def _next_steps(creator: Creator, quote: Quote | None, axes: list[AxisResult]) -> list[str]:
    steps: list[str] = []
    if quote is None:
        steps.append("向该创作者询价")
    else:
        if quote.needs_review:
            steps.append("复核报价时间与来源")
        if not quote.client_visible:
            steps.append("确认可对客展示的价格")
    if not creator.contacts:
        steps.append("补充联系方式与采购路径")
    if creator.market_region is None:
        steps.append("确认受众市场")
    if creator.audience_types is None:
        steps.append("确认受众人群")
    return steps


# --- run ---------------------------------------------------------------------


def attention_signals_by_creator(
    session: Session, target_nodes: set[str] | None = None
) -> dict[int, list]:
    """``{creator_id: [signal, ...]}`` with the Root's handle and verdict resolved.

    Loaded once per run rather than per creator: the table is small and a
    per-creator query would make the axis the slowest part of a batch.

    **Signals from rejected accounts are dropped here**, not filtered later. A
    person looked at that account and said it is not a Root; leaving the row in
    and hoping every downstream reader remembers to skip it is how withdrawn
    evidence comes back. One filter, at the source -- which is also why the BD
    screening surface calls this function rather than querying the table
    itself. Two readers with two filters is two chances to forget one.

    ``target_nodes`` restricts the result to a specific set of target accounts
    (``XAccount.rest_id``), which is what "客户只选了这几位目标人物" means. It
    narrows *which* signals are reported; it never changes what a signal proves.
    """
    from types import SimpleNamespace

    from .observation_models import ROOT_TYPE_LABELS_ZH, XAccount

    accounts = {
        rest_id: (handle, status, root_type)
        for rest_id, handle, status, root_type in session.execute(
            select(
                XAccount.rest_id,
                XAccount.handle,
                XAccount.root_review_status,
                XAccount.root_type,
            )
        ).all()
    }
    query = select(AttentionSignal).where(AttentionSignal.source_creator_id.is_not(None))
    if target_nodes is not None:
        query = query.where(AttentionSignal.source_node.in_(list(target_nodes)))

    grouped: dict[int, list] = {}
    for signal in session.scalars(query):
        handle, status, root_type = accounts.get(signal.source_node, (None, "pending", None))
        if status == "rejected":
            continue
        grouped.setdefault(signal.source_creator_id, []).append(
            SimpleNamespace(
                source_handle=handle,
                signal_type=signal.signal_type,
                confidence=signal.confidence,
                occurred_at=signal.occurred_at,
                evidence_url=signal.evidence_url,
                coverage_limitation=signal.coverage_limitation,
                review_status=status,
                # Carried through for the BD screening surface, which has to
                # state the *direction* of each edge and cite its raw evidence.
                # The attention axis ignores them; they cost nothing and their
                # absence would force a second, divergent query.
                source_node=signal.source_node,
                direction=signal.direction,
                platform=signal.platform,
                observation_count=signal.observation_count,
                raw_evidence=signal.raw_evidence,
                collected_at=signal.collected_at,
                human_confirmed=signal.human_confirmed,
                conflict_of_interest=signal.conflict_of_interest,
                # Only set once a human accepted the account, so the axis can
                # tell 已确认 Root from 候选 without re-querying.
                root_type=root_type if status == "accepted" else None,
                root_type_label=(
                    ROOT_TYPE_LABELS_ZH.get(root_type) if status == "accepted" else None
                ),
            )
        )
    return grouped


def candidate_pool(session: Session) -> list[Creator]:
    """Every creator eligible to be recommended at all.

    Two hard gates, both commercial rather than preference-based:
    a creator with no priced quote cannot be budgeted, and a
    ``non_creator`` has no audience relationship to sell.
    """
    creators = session.scalars(
        select(Creator).options(
            selectinload(Creator.quotes),
            selectinload(Creator.accounts),
            selectinload(Creator.contacts),
            selectinload(Creator.sponsorships),
            selectinload(Creator.procurement_routes),
        )
    ).all()
    return [c for c in creators if c.has_priced_quote and c.is_recommendable]


def run_recommendation(
    session: Session,
    brief: Brief,
    *,
    limit: int = 30,
    persist: bool = True,
) -> tuple[RecommendationRun, list[CreatorAssessment], list[HardExclusion]]:
    """Score the pool against a brief and (optionally) persist the batch."""
    excluded_ids = {int(x) for x in _csv(brief.must_exclude_creator_ids) if x.isdigit()}
    required_ids = {int(x) for x in _csv(brief.must_include_creator_ids) if x.isdigit()}

    pool = candidate_pool(session)
    signals_by_creator = attention_signals_by_creator(session)
    exclusions: list[HardExclusion] = []
    assessments: list[CreatorAssessment] = []

    for creator in pool:
        if creator.id in excluded_ids:
            exclusions.append(
                HardExclusion(creator.id, creator.display_name, "客户指定排除")
            )
            continue
        assessments.append(assess(creator, brief, signals_by_creator.get(creator.id)))

    # must-include creators are pinned to the top with their assessment intact,
    # so the client still sees every axis -- including any that did not match.
    assessments.sort(
        key=lambda a: (a.creator_id not in required_ids, -a.rank_score, a.creator_name)
    )
    selected = assessments[:limit]

    unsupported = _unsupported_filters(brief)

    run = RecommendationRun(
        brief_id=brief.id,
        rule_version=RULE_VERSION,
        brief_snapshot_json=json.dumps(_brief_snapshot(brief), ensure_ascii=False),
        candidate_pool_size=len(pool),
        returned_count=len(selected),
        unsupported_filters=json.dumps(unsupported, ensure_ascii=False) if unsupported else None,
    )

    if persist:
        session.add(run)
        session.flush()
        for rank, assessment in enumerate(selected, start=1):
            session.add(
                Recommendation(
                    run_id=run.id,
                    creator_id=assessment.creator_id,
                    quote_id=assessment.quote.id if assessment.quote else None,
                    rank=rank,
                    rank_score=assessment.rank_score,
                    axis_scores_json=json.dumps(
                        [asdict(a) for a in assessment.axes], ensure_ascii=False
                    ),
                    matched_preferences_json=json.dumps(assessment.matched, ensure_ascii=False),
                    unmet_conditions_json=json.dumps(assessment.unmet, ensure_ascii=False),
                    missing_data_json=json.dumps(assessment.missing, ensure_ascii=False),
                    reasons_json=json.dumps(assessment.reasons, ensure_ascii=False),
                    risk_flags_json=json.dumps(assessment.risks, ensure_ascii=False),
                    next_step=" / ".join(assessment.next_steps) or None,
                )
            )
        session.commit()

    return run, selected, exclusions


def _brief_snapshot(brief: Brief) -> dict:
    """Freeze what the brief said at run time.

    A later edit to the brief must not silently rewrite the meaning of an
    earlier batch the client already reacted to.
    """
    return {
        "name": brief.name,
        "verticals": brief.verticals,
        "target_markets": brief.target_markets,
        "content_languages": brief.content_languages,
        "target_audiences": brief.target_audiences,
        "objectives": brief.objectives,
        "platforms": brief.platforms,
        "content_formats": brief.content_formats,
        "total_budget_usd": brief.total_budget_usd,
        "per_creator_budget_min_usd": brief.per_creator_budget_min_usd,
        "per_creator_budget_max_usd": brief.per_creator_budget_max_usd,
        "risk_tolerance": brief.risk_tolerance,
    }


def _unsupported_filters(brief: Brief) -> list[str]:
    """Preferences Mango cannot answer from data it holds.

    Reported rather than silently ignored -- a filter that quietly does
    nothing is worse than one that says it could not be applied.
    """
    unsupported: list[str] = []
    if brief.creator_size_preference:
        unsupported.append("KOL 体量偏好：粉丝量已入库，但尚未接入排序")
    if brief.competitors:
        unsupported.append("竞品规避：尚无竞品合作关系数据")
    if brief.stage:
        unsupported.append("项目阶段：尚未用于筛选")
    return unsupported


def budget_for(assessments: Iterable[CreatorAssessment], brief: Brief):
    """Roll a set of assessments into a budget summary."""
    lines: list[BudgetLine] = []
    for assessment in assessments:
        quote = assessment.quote
        if quote is None:
            lines.append(
                BudgetLine(
                    creator_id=assessment.creator_id,
                    creator_name=assessment.creator_name,
                    quote_id=None,
                    deliverable=None,
                )
            )
            continue
        confirmed = quote.status in CLIENT_VISIBLE_QUOTE_STATUSES and not quote.needs_review
        lines.append(
            BudgetLine(
                creator_id=assessment.creator_id,
                creator_name=assessment.creator_name,
                quote_id=quote.id,
                deliverable=quote.deliverable_raw,
                unit_amount=quote.amount,
                unit_amount_usd=quote.amount_usd,
                currency=quote.currency or "USD",
                is_package=bool(quote.is_package),
                is_confirmed=confirmed,
                client_visible_price=quote.client_display_price,
            )
        )
    return summarize(lines, brief.total_budget_usd)
