"""待建联候选的实时筛选 —— 四份分开的判断，不合成一个分数。

规格里最硬的一条要求是：**适配程度、关系证据、商务成熟度和证据完整度分开
展示**。所以这里返回四个独立 readout，各带各的证据，谁也不能补偿谁。两条
具体后果写在代码里而不是注释里：

* ``联系方式不能抵消内容不相关`` —— 领域适配是闸门。商务邮箱齐全、历史赞助
  齐全但内容方向不符的账号，落在「仅作目标或观察」，不因好联系而上移。
* ``缺少联系方式也不要直接删除高价值候选`` —— 没有联系路径不是淘汰理由，
  是「优先补充商务路径」这一档存在的全部原因。

为什么是 CandidateView 而不是某一张表
-------------------------------------
可 BD 的人不是一份名单，是一个**随偏好实时变化的查询结果**，来源见
``bd_discovery``：被客户所选目标人物共同关注的账号、已在库但没有报价的对象、
观察层账号、外部导入的名单。它们的数据完备度差得很远（有的有归一后的领域和
报价历史，有的只有一条公开简介），但筛选问题是同一个。``CandidateView`` 把
它们摊平成同一组字段，并且**每个字段都带 basis**，所以一张卡片永远能回答
「这个结论是从哪来的」。

没有 Brief 时只判断领域适配
---------------------------
``screen()`` 接受一组 ``DomainPreferences``（领域、市场、语言、圈层、平台、
目标人物），不接受项目简报。规格原文：没有具体项目需求时，只判断领域适配，
不生成统一的项目匹配排名。所以这里 **没有任何 0–100 的匹配分**，只有逐轴
verdict 和四档优先级。带 Brief 的项目匹配是 ``recommend.py`` 的职责。

方向纪律
--------
``relation_evidence()`` 回答的是「**哪些目标人物关注了这个候选**」，不是
「这个候选关注了哪些大 V」。两个数据来源，方向相同：

* ``AttentionSignal``：``source_node``（目标账号）→ ``source_creator_id``
  （我们的候选）。``source_creator_id`` 命名上是 source、语义上是被关注方，
  这是观察层沿用下来的命名，读这张表必须记住。
* ``view.target_follow_edges``：原始关注边，覆盖**还没有供给记录**的候选 ——
  而方法的主线恰恰是发现这批人。

它 **不读** ``ContactMethod``、``ProcurementRoute`` 或任何 Mango 商务触达
数据：Mango 员工能联系到某人，和这个 KOL 能不能影响目标大 V 是两个问题，
混在一起就是把路径 1 当成路径 3。

``discovery_basis`` 里有两种来源，只有一种是关系证据：``target_followed``
（客户选的目标人物关注了它）是；``roster_centrality``（我们自己的创作者关注了
它）**不是** —— 后者反映的是 Mango 签了谁，不是行业地位。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .bd_models import (
    BD_PRIORITY_LABELS_ZH,
    COMMERCIAL_SIGNAL_LABELS_ZH,
    NEXT_STEP_LABELS_ZH,
    OBJECT_KIND_LABELS_ZH,
    STANCE_BY_SIGNAL,
    STANCE_LABELS_ZH,
    STATED_ACCEPTANCE_SIGNALS,
    BDCandidate,
    CommercialSignal,
)
from .models import NON_RECOMMENDABLE_TIERS, Creator
from .normalize import AUDIENCE_TYPE_LABELS_ZH, MARKET_REGION_LABELS_ZH
from .observation_models import ROOT_TYPE_LABELS_ZH, XAccount

#: 版本号跟着判断规则走，随结果一起返回。半年后有人问「当时为什么排进优先
#: 询价」，没有这个就答不出来。
SCREENING_VERSION = "signalmap-bd-screen-v1"

VERDICTS = ("match", "partial", "mismatch", "unknown", "not_asked")

#: 领域适配的闸门轴。只有这几条能把候选挡在门外 —— 它们回答「内容相不相关」。
#: 活跃度、原创表达、互动表现回答「证据够不够」，数据不够是完整度问题，
#: 不是不相关，所以它们不在这里。
_GATE_AXES = ("content_vertical", "market_region", "language", "platform")


# =============================================================================
# 输入
# =============================================================================


@dataclass(frozen=True)
class DomainPreferences:
    """客户在前端选的东西：领域、市场、圈层、具体目标人物。

    每一项都可以为空，空表示「没问」—— 对应的轴返回 ``not_asked``，不会被当成
    ``unknown`` 拉低任何东西。这是「上面选什么，下面就变什么」的输入。
    """

    verticals: tuple[str, ...] = ()
    market_regions: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    audience_types: tuple[str, ...] = ()
    platforms: tuple[str, ...] = ()
    #: 具体目标人物的 X handle（不带 @）或 rest_id。关系证据只对这些人计算。
    target_handles: tuple[str, ...] = ()
    label: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> DomainPreferences:
        def tup(key: str) -> tuple[str, ...]:
            value = data.get(key) or []
            if isinstance(value, str):
                value = value.split(",")
            return tuple(v for v in (str(s).strip() for s in value) if v)

        return cls(
            verticals=tup("verticals"),
            market_regions=tup("market_regions"),
            languages=tup("languages"),
            audience_types=tup("audience_types"),
            platforms=tup("platforms"),
            target_handles=tuple(h.lstrip("@") for h in tup("target_handles")),
            label=data.get("label"),
        )

    def as_dict(self) -> dict:
        return {
            "verticals": list(self.verticals),
            "market_regions": list(self.market_regions),
            "languages": list(self.languages),
            "audience_types": list(self.audience_types),
            "platforms": list(self.platforms),
            "target_handles": list(self.target_handles),
            "label": self.label,
        }

    def asked_content(self) -> bool:
        return bool(
            self.verticals or self.market_regions or self.languages
            or self.audience_types or self.platforms
        )


# =============================================================================
# 候选视图 —— 三种来源摊平成同一组字段，每个字段带 basis
# =============================================================================

#: 候选是从哪条路进入池子的。这不是分层，是**数据完备度**的诚实说明：
#: ``creator`` 有归一后的领域、受众证据和报价历史；``observed`` 只有一条公开
#: 简介；``ingested`` 是别人给的名单，附带的评分一律未核验。
SOURCE_KINDS = ("creator", "observed", "ingested")

SOURCE_KIND_LABELS_ZH = {
    "creator": "已在库对象（有完整供给记录）",
    "observed": "观察层发现（仅有公开简介）",
    "ingested": "外部名单导入（附带信息待核验）",
}


@dataclass
class CandidateView:
    """一个候选，摊平到筛选需要的字段。字段值可能来自库，也可能来自现场推导。"""

    key: str                      # 自然键，"X:handle"
    platform: str
    handle: str
    display_name: str | None
    profile_url: str | None
    source_kind: str

    creator: Creator | None = None
    candidate: BDCandidate | None = None
    x_account: XAccount | None = None

    bio: str | None = None
    followers: int | None = None
    metrics_observed_at: dt.datetime | None = None

    verticals: tuple[str, ...] = ()
    verticals_basis: str = "unknown"
    market_region: str | None = None
    market_region_basis: str = "unknown"
    languages: tuple[str, ...] = ()
    audience_types: tuple[str, ...] = ()
    audience_basis: str = "unknown"
    audience_evidence: str | None = None
    platforms: tuple[str, ...] = ()

    object_kind: str = "unknown"
    object_kind_basis: str | None = None
    target_group: str | None = None

    #: 为什么这个账号在池子里。当 ``source == "roster_centrality"`` 时它
    #: **不是**关系证据 —— 见模块 docstring。
    discovery_basis: dict | None = None
    #: ``目标人物 → 关注 → 本候选`` 的原始边，来自 ``follow_edges``。
    #: 这是尚未建立供给记录的候选唯一的关系证据来源：``AttentionSignal`` 以
    #: ``source_creator_id`` 为键，只覆盖已在库的对象，而方法的主线恰恰是发现
    #: 还不在库的人。
    target_follow_edges: list[dict] = field(default_factory=list)
    #: 给人看的一句话，措辞里必须带「关注是门槛，不是关系证据」。
    target_follow_note: str | None = None
    #: 观察层现场从简介里读出来的商业信号（未落库，带原文引用）。
    inline_signals: list[CommercialSignal] = field(default_factory=list)
    #: 来自 root_review 的旗标（同圈互关、涨粉服务…），原样带过来。
    review_flags: list[dict] = field(default_factory=list)


# =============================================================================
# 输出结构
# =============================================================================


@dataclass(frozen=True)
class Axis:
    key: str
    label_zh: str
    verdict: str
    detail: str
    evidence: str | None = None

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label_zh": self.label_zh, "verdict": self.verdict,
            "detail": self.detail, "evidence": self.evidence,
        }


@dataclass
class DomainFit:
    """适配程度。``verdict`` 四选一，**没有分数** —— 这是刻意的。"""

    verdict: str  # fit | partial | mismatch | unclear
    summary: str
    axes: list[Axis]

    LABELS = {
        "fit": "领域适配", "partial": "部分适配",
        "mismatch": "内容方向不符", "unclear": "适配性待确认",
    }

    @property
    def passed(self) -> bool:
        """能不能进建联队列。``unclear`` 不算通过：没有数据不是通过。"""
        return self.verdict in ("fit", "partial")

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "label_zh": self.LABELS[self.verdict],
            "summary": self.summary,
            "axes": [a.as_dict() for a in self.axes],
        }


@dataclass
class RelationEvidence:
    """关系证据：哪些目标人物关注了这个候选。"""

    targets: list[dict]
    coverage_note: str
    targets_collected: int
    targets_requested: int
    priority_signals: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    @property
    def distinct_targets(self) -> int:
        return len({t["target_rest_id"] for t in self.targets})

    @property
    def interaction_count(self) -> int:
        return sum(1 for t in self.targets if t["signal_type"] != "follow")

    @property
    def observed(self) -> bool:
        return bool(self.targets)

    @property
    def strength(self) -> str:
        """``interaction`` / ``follow_only`` / ``none``。

        关注和互动是两个量级的证据，合成一个数字就再也分不开。一条关注只说明
        「这个候选的内容有机会出现在他的时间线上」—— 而 @pmarca 关注 32,758 个
        账号，那条时间线上出现过什么，跟他看没看过是两回事。只有回复、引用、
        转发能证明他确实注意到了。
        """
        if self.interaction_count:
            return "interaction"
        return "follow_only" if self.targets else "none"

    STRENGTH_LABELS = {
        "interaction": "有公开互动记录（目标确实注意到过）",
        "follow_only": "仅有关注（门槛通过，尚不构成关系证据）",
        "none": "当前未观察到",
    }

    def as_dict(self) -> dict:
        return {
            "distinct_targets": self.distinct_targets,
            "interaction_count": self.interaction_count,
            # 强度是分开的一档，不是把两种证据加起来的分数。
            "strength": self.strength,
            "strength_label_zh": self.STRENGTH_LABELS[self.strength],
            "targets": self.targets,
            "priority_signals": self.priority_signals,
            "coverage_note": self.coverage_note,
            "targets_collected": self.targets_collected,
            "targets_requested": self.targets_requested,
            "limitations": self.limitations,
            "claim_limit": (
                "关注只证明观察时存在关注关系，不证明看过、认可或会转发；"
                "零结果表示当前未观察到，不表示两者无关系"
            ),
        }


@dataclass
class CommercialMaturity:
    """商务成熟度。三件事分开记，永不合并成一个「可合作」布尔值。"""

    stances: dict[str, dict]
    level: str  # none | contact_only | stated | proven | confirmed
    signals: list[dict]
    caveats: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)

    LEVELS = {
        "none": "无商业合作信号",
        "contact_only": "仅有联系方式",
        "stated": "公开表示承接（未见成交案例）",
        "proven": "有第三方合作案例",
        "confirmed": "已确认愿意承接本项目",
    }

    @property
    def contactable(self) -> bool:
        return self.stances["contactable"]["holds"]

    @property
    def has_paid_evidence(self) -> bool:
        """够不够直接询价：见过成交、见过公开承接声明，或本人确认过。"""
        return (
            self.stances["has_commercial_history"]["holds"]
            or self.stances["confirmed_willing"]["holds"]
            or self.level == "stated"
        )

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "level_label_zh": self.LEVELS[self.level],
            "stances": self.stances,
            "signals": self.signals,
            "caveats": self.caveats,
            "blocking": self.blocking,
        }


@dataclass
class EvidenceCompleteness:
    known: list[str]
    missing: list[str]

    @property
    def coverage_text(self) -> str:
        return f"{len(self.known)}/{len(self.known) + len(self.missing)}"

    def as_dict(self) -> dict:
        return {
            "known": self.known, "missing": self.missing,
            "coverage_text": self.coverage_text,
        }


@dataclass
class Screening:
    view: CandidateView
    domain_fit: DomainFit
    relation: RelationEvidence
    commercial: CommercialMaturity
    completeness: EvidenceCompleteness
    priority: str
    priority_reason: str
    next_step_kind: str
    next_step: str
    quote_state: dict
    version: str = SCREENING_VERSION


# =============================================================================
# 适配程度
# =============================================================================


def _overlap(a: tuple[str, ...] | list[str], b: tuple[str, ...]) -> list[str]:
    lowered = {x.lower() for x in b}
    return [x for x in a if x.lower() in lowered]


def domain_fit(view: CandidateView, prefs: DomainPreferences) -> DomainFit:
    """七条内容判断，逐条给结论，不求和。

    前四条是闸门（内容方向、市场、语言、平台）。后三条（活跃度、原创表达、
    互动表现）以及圈层为 ``unknown`` 时不会把人挡在外面，只进证据完整度。
    """
    if view.object_kind == "supplier":
        return DomainFit(
            "unclear", "机构候选没有内容面，按代理范围与可采购库存判断，不做内容适配",
            [Axis("content_vertical", "内容与行业", "not_asked", "机构候选不做内容适配")],
        )

    axes: list[Axis] = []
    creator = view.creator
    account = _primary_account(creator)

    # --- 闸门：内容方向 ---
    if not prefs.verticals:
        axes.append(Axis(
            "content_vertical", "内容与行业", "not_asked",
            f"未指定领域；该账号领域判断为 {'、'.join(view.verticals) or '待确认'}",
        ))
    elif not view.verticals:
        axes.append(Axis("content_vertical", "内容与行业", "unknown", "内容领域未知，需补充"))
    else:
        hit = _overlap(view.verticals, prefs.verticals)
        if hit:
            axes.append(Axis(
                "content_vertical", "内容与行业", "match", f"内容领域覆盖 {'、'.join(hit)}",
                evidence=f"依据：{_basis_zh(view.verticals_basis)}"
                         f"（全部已归一领域：{'、'.join(view.verticals)}）",
            ))
        else:
            axes.append(Axis(
                "content_vertical", "内容与行业", "mismatch",
                f"内容领域为 {'、'.join(view.verticals)}，与所选 {'、'.join(prefs.verticals)} 不重合",
            ))

    # --- 闸门：市场 ---
    if not prefs.market_regions:
        axes.append(Axis("market_region", "市场", "not_asked", "未指定市场"))
    elif view.market_region is None:
        axes.append(Axis("market_region", "市场", "unknown", "市场待确认"))
    else:
        label = MARKET_REGION_LABELS_ZH.get(view.market_region, view.market_region)
        if view.market_region in prefs.market_regions:
            axes.append(Axis(
                "market_region", "市场", "match", f"市场为{label}",
                evidence=f"依据：{_basis_zh(view.market_region_basis)}（推断，非实测受众地理）",
            ))
        else:
            axes.append(Axis("market_region", "市场", "mismatch", f"市场为{label}，不在所选范围内"))

    # --- 闸门：语言 ---
    if not prefs.languages:
        axes.append(Axis("language", "内容语言", "not_asked", "未指定语言"))
    elif not view.languages:
        axes.append(Axis("language", "内容语言", "unknown", "内容语言未知"))
    elif _overlap(view.languages, prefs.languages):
        axes.append(Axis("language", "内容语言", "match", f"内容语言 {'、'.join(view.languages)}"))
    else:
        axes.append(Axis(
            "language", "内容语言", "mismatch",
            f"内容语言为 {'、'.join(view.languages)}，与所选不符",
        ))

    # --- 闸门：平台 ---
    if not prefs.platforms:
        axes.append(Axis("platform", "平台", "not_asked", "未指定平台"))
    elif not view.platforms:
        axes.append(Axis("platform", "平台", "unknown", "未收录任何账号"))
    else:
        hit = _overlap(view.platforms, prefs.platforms)
        if hit:
            axes.append(Axis("platform", "平台", "match", f"在 {'、'.join(hit)} 有账号"))
        else:
            axes.append(Axis("platform", "平台", "mismatch", f"仅有 {'、'.join(view.platforms)} 账号"))

    # --- 非闸门：圈层 ---
    if not prefs.audience_types:
        axes.append(Axis(
            "audience", "目标圈层", "not_asked",
            f"未指定圈层；该账号受众判断为 {'、'.join(_zh_audiences(view.audience_types)) or '待确认'}",
        ))
    elif not view.audience_types:
        axes.append(Axis("audience", "目标圈层", "unknown", "受众类型待确认"))
    else:
        hit = _overlap(view.audience_types, prefs.audience_types)
        axes.append(Axis(
            "audience", "目标圈层", "match" if hit else "mismatch",
            f"受众覆盖 {'、'.join(_zh_audiences(hit))}" if hit
            else f"受众为 {'、'.join(_zh_audiences(view.audience_types))}，与所选圈层不重合",
            evidence=view.audience_evidence,
        ))

    axes.append(_axis_activity(view, account))
    axes.append(_axis_originality(view, account))
    axes.append(_axis_engagement(account))

    verdict, summary = _fit_verdict(axes)
    return DomainFit(verdict, summary, axes)


def _fit_verdict(axes: list[Axis]) -> tuple[str, str]:
    """把闸门轴收成一个 verdict —— 用规则，不用加权求和。"""
    gates = [a for a in axes if a.key in _GATE_AXES]
    asked = [a for a in gates if a.verdict != "not_asked"]

    mismatches = [a for a in asked if a.verdict == "mismatch"]
    if mismatches:
        return "mismatch", "；".join(a.detail for a in mismatches)
    if not asked:
        return "unclear", "本轮未设定领域筛选条件，未做适配判断"

    matches = [a for a in asked if a.verdict == "match"]
    unknowns = [a for a in asked if a.verdict == "unknown"]
    if not matches:
        return "unclear", "所选条件对应的数据尚未采集：" + "、".join(a.label_zh for a in unknowns)
    if unknowns:
        return "partial", (
            "符合 " + "、".join(a.label_zh for a in matches)
            + "；" + "、".join(a.label_zh for a in unknowns) + "待确认"
        )
    return "fit", "符合 " + "、".join(a.label_zh for a in matches)


def _primary_account(creator: Creator | None):
    if creator is None or not creator.accounts:
        return None
    for account in creator.accounts:
        if account.is_primary:
            return account
    return max(creator.accounts, key=lambda a: a.followers or 0)


def _axis_activity(view: CandidateView, account) -> Axis:
    if account is not None and account.posting_frequency:
        stamp = (
            f"，数据观察于 {account.metrics_observed_at:%Y-%m-%d}"
            if account.metrics_observed_at else "，观察时间未记录"
        )
        return Axis("activity", "近期活跃度", "match", f"发布频率 {account.posting_frequency}{stamp}")
    if view.source_kind == "observed":
        # 观察层只有一次 profile 抓取，没有发文频率。说清楚是「没采」而不是
        # 「不活跃」—— 后者是一个我们没有资格下的判断。
        return Axis(
            "activity", "近期活跃度", "unknown",
            "观察层仅抓取过一次公开资料，未采集发布频率；需补充近期内容后再判断",
        )
    return Axis("activity", "近期活跃度", "unknown", "发布频率未采集，需补充近期内容")


def _axis_originality(view: CandidateView, account) -> Axis:
    """原创表达 —— 用推广密度反读，并写明这是反读。

    没有「原创度」这种可测字段，硬造一个就是编数据。能测的是这个号有多少内容
    是广告：推广密度高说明可信表达空间小。措辞必须暴露这是代理指标。
    """
    ratio = account.promotional_content_ratio if account else None
    if ratio is not None:
        verdict = "mismatch" if ratio >= 0.6 else "partial" if ratio >= 0.35 else "match"
        return Axis(
            "originality", "原创表达", verdict,
            f"推广内容占比约 {ratio:.0%}（以推广密度反读表达空间，非原创度实测）",
        )
    level = view.creator.promotion_level if view.creator else None
    if level:
        verdict = {"High": "mismatch", "Medium": "partial", "Low": "match"}.get(level, "unknown")
        return Axis("originality", "原创表达", verdict, f"推广密度评级 {level}（人工分级）")
    growth = [f for f in view.review_flags if f.get("key") == "growth_service"]
    if growth:
        # 简介里在卖涨粉/代写服务：这个号本身就在售卖曝光，表达面存疑。
        return Axis("originality", "原创表达", "mismatch", growth[0]["detail"])
    return Axis("originality", "原创表达", "unknown", "推广密度未评估")


def _axis_engagement(account) -> Axis:
    if account is None:
        return Axis("engagement", "典型互动表现", "unknown", "播放与互动数据未采集")
    bits = []
    # 中位数优先于平均数：一条爆款会把均值抬到看不出常态。
    if account.median_views:
        bits.append(f"中位播放 {account.median_views:,.0f}")
    if account.avg_views:
        bits.append(f"平均播放 {account.avg_views:,.0f}")
    if account.engagement_rate is not None:
        bits.append(f"互动率 {account.engagement_rate:.2%}")
    if not bits:
        return Axis("engagement", "典型互动表现", "unknown", "播放与互动数据未采集")
    stamp = (
        f"（观察于 {account.metrics_observed_at:%Y-%m-%d}）"
        if account.metrics_observed_at else "（观察时间未记录）"
    )
    return Axis("engagement", "典型互动表现", "match", "、".join(bits) + stamp)


def _basis_zh(basis: str | None) -> str:
    return {
        "language": "内容语言", "country": "创作者所在国家",
        "provenance": "来源名单归属", "bio_script": "简介文字",
        "categories": "已归档分类", "bio": "公开简介",
        "content": "本人内容", "vertical": "所属领域",
    }.get(basis or "", "未知")


def _zh_audiences(keys) -> list[str]:
    return [AUDIENCE_TYPE_LABELS_ZH.get(k, k) for k in keys]


# =============================================================================
# 关系证据（路径 3）
# =============================================================================


def resolve_target_nodes(session: Session, handles: tuple[str, ...]) -> dict[str, XAccount]:
    """把客户选的目标人物 handle 解析成已收录的 X 账号。

    解析不到不是错误，是覆盖缺口 —— 调用方要把它报出来，而不是当成
    「这个人没关注任何候选」。
    """
    if not handles:
        return {}
    wanted = {h.lstrip("@").lower() for h in handles}
    out: dict[str, XAccount] = {}
    for account in session.scalars(select(XAccount).where(XAccount.handle.is_not(None))):
        if (account.handle or "").lower() in wanted:
            out[account.rest_id] = account
    for rest_id in wanted:
        if rest_id.isdigit() and rest_id not in out:
            account = session.scalar(select(XAccount).where(XAccount.rest_id == rest_id))
            if account is not None:
                out[account.rest_id] = account
    return out


def collected_target_nodes(session: Session, rest_ids: set[str]) -> set[str]:
    """这些目标人物里，哪些的公开关注列表我们真的采过。

    零结果的分母。没有它，「未观察到关注」和「根本没去看」长得一模一样。

    按 ``FollowSnapshot`` 判定，**不是**按 ``AttentionSignal``。一开始用的是
    后者，结果 62 位全部采完之后仍然报「28 位尚未采集」—— 因为
    ``AttentionSignal`` 只在目标关注了**我方创作者**时才产生，一位关注列表采得
    很干净、但一个我们的人都没关注的名人，会被算成没采过。那正好是把「已观察
    到零结果」误报成「没去看」，方向和这个函数存在的理由相反。
    """
    if not rest_ids:
        return set()
    from .observation_models import FollowSnapshot, XAccount

    return set(session.scalars(
        select(XAccount.rest_id)
        .join(FollowSnapshot, FollowSnapshot.observer_id == XAccount.id)
        .where(
            XAccount.rest_id.in_(list(rest_ids)),
            FollowSnapshot.kind == "following",
            # 报错的那次采集不算采过：它什么也没看到。
            FollowSnapshot.error.is_(None),
        )
        .distinct()
    ).all())


def signals_by_creator(session: Session, target_nodes: set[str] | None) -> dict[int, list]:
    """一次取完所有关注边，按候选分组。

    直接复用 ``recommend.attention_signals_by_creator``，不自己查表：**已被人工
    否决的 Root，其信号必须在同一个地方被丢弃**。如果这里再写一遍查询，就有了
    两份过滤规则和两次忘记的机会，而被撤回的证据会从没更新的那一份里回来。
    """
    from .recommend import attention_signals_by_creator

    return attention_signals_by_creator(session, target_nodes or None)


def relation_evidence(
    view: CandidateView,
    prefs: DomainPreferences,
    *,
    signals: list,
    target_accounts: dict[str, XAccount],
    collected_nodes: set[str],
) -> RelationEvidence:
    """哪些**目标人物关注了这个候选** —— 方向不可反过来读。"""
    requested = len(prefs.target_handles)
    collected = len(collected_nodes & set(target_accounts)) if target_accounts else 0

    rows: list[dict] = []
    limitations: list[str] = []
    for signal in signals:
        # handle / review 状态由 recommend.attention_signals_by_creator 解析好，
        # 这里不再自己查 XAccount —— 两处各查一次就会有两套「已否决」的判断。
        account = target_accounts.get(signal.source_node)
        reviewed = signal.review_status
        handle = signal.source_handle or (account.handle if account else None)
        rows.append({
            # 这三行合起来才是完整的方向陈述，缺一不可。
            "target_handle": handle,
            "target_rest_id": signal.source_node,
            "direction_text": (
                f"@{handle} 关注了本候选" if handle else "目标账号关注了本候选"
            ),
            "signal_type": signal.signal_type,
            "direction": signal.direction,
            "platform": signal.platform,
            "occurred_at": signal.occurred_at.isoformat() if signal.occurred_at else None,
            "observation_count": signal.observation_count,
            "evidence_url": signal.evidence_url,
            "raw_evidence": signal.raw_evidence,
            "collected_at": signal.collected_at.isoformat() if signal.collected_at else None,
            "confidence": signal.confidence,
            "human_confirmed": signal.human_confirmed,
            "conflict_of_interest": signal.conflict_of_interest,
            # 目标人物本身是否已被人工确认。未确认的目标，其关注只是研究线索。
            "target_review_status": reviewed,
            # 只有人工确认过的 Root 才有类型可报。未确认的候选留空，卡片上就会
            # 如实读成「候选账号」而不是「已确认 Root」。
            "target_root_type": signal.root_type_label,
        })
        if signal.coverage_limitation:
            limitations.append(signal.coverage_limitation)

    # 还没有供给记录的候选，关系证据只能来自原始关注边 —— 而方法的主线正是
    # 发现这批人。已经产生了 AttentionSignal 的目标不重复计入。
    seen = {r["target_rest_id"] for r in rows}
    for edge in view.target_follow_edges:
        if edge["target_rest_id"] in seen:
            continue
        account = target_accounts.get(edge["target_rest_id"])
        rows.append({
            "target_handle": edge["target_handle"],
            "target_rest_id": edge["target_rest_id"],
            "direction_text": (
                f"@{edge['target_handle']} 关注了本候选" if edge["target_handle"]
                else "目标账号关注了本候选"
            ),
            "signal_type": "follow",
            "direction": "source_to_target",
            "platform": "X",
            "occurred_at": None,  # X 不暴露关注开始时间，这里不能编一个
            "observation_count": 1,
            "evidence_url": (
                f"https://x.com/{edge['target_handle']}/following"
                if edge["target_handle"] else None
            ),
            "raw_evidence": f"@{edge['target_handle']} follows @{view.handle}",
            "collected_at": edge["last_observed_at"],
            "first_observed_at": edge["first_observed_at"],
            "confidence": "research_lead",
            "human_confirmed": False,
            "conflict_of_interest": None,
            "target_review_status": account.root_review_status if account else "pending",
            "target_root_type": (
                ROOT_TYPE_LABELS_ZH.get(account.root_type)
                if account and account.root_review_status == "accepted" and account.root_type
                else None
            ),
        })
        if edge.get("coverage_limitation"):
            limitations.append(edge["coverage_limitation"])

    priority: list[str] = []
    distinct = len({r["target_rest_id"] for r in rows})
    if distinct >= 2:
        # 规格：多位目标共同关注可作优先信号，但不要求被所有目标关注。
        priority.append(f"{distinct} 位目标人物共同关注")
    interactions = [r for r in rows if r["signal_type"] != "follow"]
    if len(interactions) >= 2:
        priority.append(f"除关注外另有 {len(interactions)} 条公开互动记录")
    repeated = [r for r in rows if (r["observation_count"] or 0) > 1]
    if repeated:
        priority.append(f"{len(repeated)} 条信号被重复观察到")

    if view.creator is None and not view.target_follow_edges:
        note = (
            "该候选来自观察层，尚未建立可关联的供给记录，"
            "目标人物的关注关系未采集（未采集，不代表无关系）"
        )
    elif requested and not target_accounts:
        note = f"所选 {requested} 位目标人物均未收录到 X 账号库，无法判断关注关系（未采集）"
    elif not requested:
        note = "本轮未指定目标人物；此处列出所有已观察到的关注方"
    elif collected == 0:
        note = (
            f"所选 {requested} 位目标人物中，0 位的公开关注列表已采集 —— "
            "当前无法判断，属未采集而非无关系"
        )
    else:
        note = (
            f"所选 {requested} 位目标人物中，{collected} 位的公开关注列表已采集；"
            f"其余 {max(len(target_accounts) - collected, 0)} 位未采集，其关注情况未知"
        )
    return RelationEvidence(rows, note, collected, requested, priority, sorted(set(limitations)))


# =============================================================================
# 商务成熟度
# =============================================================================

#: ``(?<!@)`` 是必需的：Mastodon 账号写作 ``@shakir@mas.to``，形状和邮箱一模
#: 一样。没有这个前瞻，@shakir_za 的联邦宇宙账号会被读成商务邮箱，把他推进
#: 「有公开合作入口」—— 一个既不存在的入口，卡片上还引着原文。
_EMAIL_RE = re.compile(r"(?<![@A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

#: 公开表示承接第三方品牌投放的措辞。
#:
#: 这份表被刻意收窄过一次，值得写下原因。最初它包含 "for collabs"、
#: "work with me"、"business inquiries" 这类词，结果实测出来的「优先询价」
#: 前八名全是简介写着 "DM for Collabs" 的账号 —— 而这正是同圈互推账号的招牌
#: 写法，也是 ``root_review`` 早就识别出的那一批。"work with me" 在这些人
#: 简介里的意思通常是「雇我做咨询」，"business inquiries" 只是一个联系入口。
#: 三者都不是「我收品牌的钱发内容」。
#:
#: 现在只保留**说明收品牌钱**的说法。判断某个人「大概愿意」不属于这里，也不
#: 属于任何自动规则。
_ACCEPTS_MARKERS: tuple[str, ...] = (
    "brand partnership", "brand partnerships", "brand deal", "brand deals",
    "partner with brands", "paid partnership", "sponsored content",
    "for sponsorship", "sponsorships:", "sponsor me", "media kit",
    "advertise with", "advertising inquiries",
    "商务合作", "品牌合作", "广告合作", "广告位",
)

#: 仅推广自有产品/课程/社群的措辞。**明确不构成**承接第三方投放的证据 ——
#: 一个天天卖自己课程的账号，并没有因此证明他愿意帮别人打广告。
_SELF_PROMO_MARKERS = (
    "join my", "my course", "my newsletter", "my community", "my ebook",
    "enroll now", "my cohort", "buy my", "我的课程", "我的社群", "知识星球",
)

#: 在售卖曝光或互推的账号。同样不算承接第三方品牌投放，而且比「什么都没写」
#: 更糟：它说明这个号的合作位是拿来换量的。命中它会**撤销**承接声明的读法，
#: 不是并列展示 —— 并列会让 "DM for collabs" 同时算成两件事，而它只是一件。
_GROWTH_SERVICE_MARKERS = (
    "ghostwriter", "ghostwriting", "dm for promo", "dm for collab",
    "dm for collabs", "dm to collab", "for collabs", "let's collab",
    "grow your", "growth strategist", "shoutout", "帮推", "互推",
)


def signals_from_bio(bio: str | None, *, source_url: str | None) -> list[CommercialSignal]:
    """从公开简介里读出商业信号，每条都带原文引用。

    只读**字面写着**的东西。邮箱必须真的出现在简介里 —— 规格明确「不猜邮箱」，
    所以这里没有任何拼装域名的逻辑。措辞类信号在剔除邮箱之后再匹配，否则
    ``collab@example.com`` 会被读成一句「我接合作」的公开声明，而它只是一个
    地址。

    返回的对象**不入库**：它们是这次查询现场读出来的，落库要经过
    ``/api/internal/bd/...`` 的显式写入，带上 actor。
    """
    if not bio:
        return []
    found: list[CommercialSignal] = []
    today = dt.date.today()

    emails = _EMAIL_RE.findall(bio)
    for email in dict.fromkeys(emails):
        purpose, note = _email_purpose(bio, email)
        found.append(CommercialSignal(
            # 工作/媒体邮箱不是商务合作入口。类型分开，``STANCE_BY_SIGNAL`` 里
            # ``work_or_press_email`` 不映射到任何一件事，所以它会被记录、被展示，
            # 但不会让这个人算作「有可执行联系路径」。
            signal_type="business_email" if purpose == "commercial" else "work_or_press_email",
            value=email,
            evidence_quote=_excerpt(bio, email), evidence_url=source_url,
            observed_at=today, fact_status="research_lead", is_inferred=False,
            source_system="bio_scan", notes=note,
        ))

    prose = _EMAIL_RE.sub(" ", bio).lower()

    # 换量/互推的说法先判，命中就**不再**读作承接品牌投放。顺序在这里是语义的
    # 一部分，不是优化：并列展示会让 "DM for collabs" 同时算成「他接广告」和
    # 「他在换量」，而它只说明了后者。
    growth = next((m for m in _GROWTH_SERVICE_MARKERS if m in prose), None)
    if growth is not None:
        found.append(CommercialSignal(
            signal_type="self_promotion_only",
            evidence_quote=_excerpt(bio, growth), evidence_url=source_url,
            observed_at=today, fact_status="research_lead", source_system="bio_scan",
            notes="简介出现换量/互推/代写类说法，是在售卖曝光位，不等于承接第三方品牌投放",
        ))
    else:
        marker = next((m for m in _ACCEPTS_MARKERS if m in prose), None)
        if marker is not None:
            found.append(CommercialSignal(
                signal_type="accepts_brand_work_statement",
                evidence_quote=_excerpt(bio, marker), evidence_url=source_url,
                observed_at=today, fact_status="research_lead", is_inferred=False,
                source_system="bio_scan",
                notes="公开简介中的承接声明；是本人说法，不是成交案例",
            ))

    promo = next((m for m in _SELF_PROMO_MARKERS if m in prose), None)
    if promo is not None:
        found.append(CommercialSignal(
            signal_type="self_promotion_only",
            evidence_quote=_excerpt(bio, promo), evidence_url=source_url,
            observed_at=today, fact_status="research_lead", source_system="bio_scan",
            notes="简介只提到推广自有产品/课程/社群",
        ))
    return found


#: 邮箱前面的这些词说明它是媒体/工作联系，不是接广告的入口。
_PRESS_LABELS = (
    "press", "media inquiries", "media:", "pr:", "为媒体", "媒体联系",
    # @NeurIPSConf 的 "Please send feedback to townhall@neurips.cc" —— 通用
    # 反馈信箱，而且它上一句就写着这个账号没人看。
    "feedback", "questions", "enquiries", "tips", "story ideas",
)

#: 简介里自报的职业。**记者的邮箱按定义就是工作邮箱**，不需要再去猜域名 ——
#: @CadeMetz 的 ``cade.metz@nytimes.com`` 躲过了域名规则，因为他简介里写的是
#: "New York Times" 而域名是 "nytimes"，归一之后仍然对不上。
_WORK_ROLE_MARKERS = (
    "reporter", "journalist", "correspondent", "staff writer", "editor at",
    "记者", "编辑",
)

#: 邮箱前面的这些词说明它确实是接商业合作的。
_COMMERCIAL_LABELS = (
    "brand", "collab", "sponsor", "partnership", "partnerships", "business",
    "advertis", "booking", "ads", "商务", "合作",
)


def _email_purpose(bio: str, email: str) -> tuple[str, str]:
    """这个邮箱是「接广告的」还是「工作邮箱」，以及凭什么这么说。

    判断顺序就是证据强度顺序，**顺序本身是逻辑的一部分**：

    1. **邮箱前面的显式标签**最强。写着 ``Ads:`` / ``Brand partnerships:``
       就是接广告的入口，不管这个人自称什么。@laurashin 自称 "Crypto
       journalist"、邮箱域名又是她自己播客的域名 —— 下面两条规则都会把她判成
       买不到，而她的简介里明明白白写着 ``Ads``。她卖的正是播客广告位。
    2. **邮箱域名就是他自己说的雇主**。简介写着 ``@Axios``、邮箱是
       ``Dan@axios.com`` —— 工作邮箱。这一条一次分掉了 Benioff、NYT 记者、
       Bloomberg 记者。
    3. **媒体/通用信箱标签**：``Press:``、``feedback@`` —— 不是接广告的。
    4. **自述职业**：记者、编辑 —— 按工作联系处理。

    都判不出来时返回「用途未标明」的 commercial：一个没说明用途的个人邮箱仍然
    是一条可以试的路径，只是卡片上要注明。
    """
    lowered = bio.lower()
    local, _, domain = email.lower().partition("@")
    root = domain.split(".")[0]

    index = lowered.find(email.lower())
    prefix = lowered[max(0, index - 40) : index] if index >= 0 else ""

    # 1. 显式商务标签，压过一切推断。
    if any(label in prefix for label in _COMMERCIAL_LABELS) or local in {
        "collab", "collabs", "brand", "brands", "sponsor", "sponsors",
        "partnerships", "business", "biz", "ads", "advertising",
    }:
        return "commercial", "简介明确标注为商务/品牌合作邮箱"

    # 2. 域名主体在简介里被当成机构名提到过（@Axios / Axios / axios.com）。
    #    比较前把非字母数字全去掉：机构名通常带空格或大小写不同 ——
    #    ``CEO of Useful Sensors`` 对 ``pete@usefulsensors.com``。
    stripped = re.sub(r"[^a-z0-9]", "", lowered.replace(email.lower(), " "))
    if len(root) >= 4 and root not in {"gmail", "outlook", "yahoo", "proton", "icloud"}:
        if root in stripped:
            return "work", (
                f"域名 {domain} 与简介里提到的所属机构一致，判为工作邮箱 —— "
                "能联系到人，但不等于商务合作入口"
            )

    # 3. 通用/媒体信箱。
    if local in {"feedback", "support", "help", "info", "hello", "contact", "admin"}:
        return "press", "通用联络/反馈地址，不是承接品牌投放的入口"
    if any(label in prefix for label in _PRESS_LABELS) or local in {"press", "pr", "media"}:
        return "press", "简介标注为媒体/公关联系，不是承接品牌投放的入口"

    # 4. 自述职业。
    role = next((m for m in _WORK_ROLE_MARKERS if m in lowered), None)
    if role is not None:
        return "work", f"简介自述职业为「{role}」，公开邮箱按工作联系处理"

    return "commercial", "来自账号公开简介，用途未标明，未核验是否仍可达"


def _excerpt(text: str, marker: str, width: int = 60) -> str:
    """原文片段。简介会改，不存原文这条判断日后无法复核。"""
    index = text.lower().find(marker.lower())
    if index < 0:
        return marker
    start = max(0, index - width // 2)
    snippet = re.sub(r"\s+", " ", text[start : index + len(marker) + width // 2]).strip()
    return f"…{snippet}…" if start > 0 else snippet


def commercial_maturity(
    view: CandidateView, stored_signals: list[CommercialSignal],
) -> CommercialMaturity:
    """三件事分开判断：有联系路径 / 接过第三方广告 / 确认愿意接本项目。"""
    signals = list(stored_signals) + list(view.inline_signals)
    creator = view.creator

    by_stance: dict[str, list[CommercialSignal]] = {s: [] for s in STANCE_LABELS_ZH}
    stated: list[CommercialSignal] = []
    caveats: list[str] = []
    blocking: list[str] = []

    for signal in signals:
        stance = STANCE_BY_SIGNAL.get(signal.signal_type)
        if stance:
            by_stance[stance].append(signal)
        if signal.signal_type in STATED_ACCEPTANCE_SIGNALS:
            stated.append(signal)
        if signal.signal_type == "self_promotion_only":
            caveats.append(
                (signal.notes or "仅观察到推广自有产品/课程/社群")
                + (f"；原文：{signal.evidence_quote}" if signal.evidence_quote else "")
                + " —— 不能据此判断愿意承接第三方投放"
            )
        if signal.signal_type == "declined_commercial":
            blocking.append("已明确拒绝商业合作" + (f"（{signal.notes}）" if signal.notes else ""))

    # 历史合作证据也算「接过第三方广告」，但必须带复核状态：BD 库里的
    # sponsorship_evidence 绝大多数是 observed_unreviewed，当成已确认就是过度声称。
    sponsorship_rows: list[dict] = []
    if creator is not None:
        for evidence in creator.sponsorships:
            if evidence.evidence_type not in ("paid_sponsorship", None):
                continue
            sponsorship_rows.append({
                "signal_type": "third_party_sponsorship",
                "label_zh": COMMERCIAL_SIGNAL_LABELS_ZH["third_party_sponsorship"],
                "stance": "has_commercial_history",
                "value": evidence.sponsor_name,
                "evidence_url": evidence.post_url,
                "occurred_at": evidence.published_at.isoformat() if evidence.published_at else None,
                "verified_at": None,
                "review_status": evidence.review_status,
                "fact_status": (
                    "verified_fact" if evidence.review_status == "confirmed" else "research_lead"
                ),
                "source": "sponsorship_evidence",
                "note": (
                    None if evidence.review_status == "confirmed"
                    else "付费观察·待复核"
                ),
            })

    contact_rows: list[dict] = []
    if creator is not None:
        for contact in creator.contacts:
            contact_rows.append({
                "signal_type": "business_email" if contact.method_type == "email" else "dm_intake",
                "label_zh": f"联系方式（{contact.method_type}）",
                "stance": "contactable",
                # 值是内部数据；序列化层决定给不给出去。
                "value": contact.value,
                "evidence_url": getattr(contact, "source_url", None),
                "verified_at": contact.verified_at.isoformat() if contact.verified_at else None,
                "fact_status": "research_lead",
                "source": "contact_methods",
                "note": (
                    None if contact.verified_at
                    else "未核验：尚无人确认该联系方式确实可达"
                ),
            })

    # 已确认的代理关系也是一条可执行路径 —— 但只有确认过的才算。
    route_rows: list[dict] = []
    if creator is not None:
        for route in creator.procurement_routes:
            status = getattr(route, "representation_status", None) or (
                "confirmed" if route.verified_at else "public_entry_unverified"
            )
            route_rows.append({
                "signal_type": "managed_by",
                "label_zh": COMMERCIAL_SIGNAL_LABELS_ZH["managed_by"],
                "stance": "contactable" if status == "confirmed" else None,
                "value": route.counterparty_name,
                "route_type": route.route_type,
                "representation_status": status,
                "verified_at": route.verified_at.isoformat() if route.verified_at else None,
                "fact_status": "verified_fact" if status == "confirmed" else "research_lead",
                "source": "procurement_routes",
                "note": (
                    None if status == "confirmed"
                    else "仅为公开入口，代理关系未经确认 —— 找到一家机构不等于存在代理关系"
                ),
            })

    has_history = bool(by_stance["has_commercial_history"] or sponsorship_rows)
    contactable = bool(
        by_stance["contactable"] or contact_rows
        or [r for r in route_rows if r["stance"] == "contactable"]
    )
    confirmed = bool(by_stance["confirmed_willing"])

    history_count = len(by_stance["has_commercial_history"]) + len(sponsorship_rows)
    stances = {
        "contactable": {
            "holds": contactable,
            "label_zh": STANCE_LABELS_ZH["contactable"],
            "evidence_count": len(by_stance["contactable"]) + len(contact_rows),
            "detail": "有可执行联系路径" if contactable else "尚未找到可执行联系人或入口",
        },
        "has_commercial_history": {
            "holds": has_history,
            "label_zh": STANCE_LABELS_ZH["has_commercial_history"],
            "evidence_count": history_count,
            "detail": (
                f"观察到 {history_count} 条第三方付费合作记录（多数尚未人工复核）"
                if has_history else "未观察到第三方付费合作记录"
            ),
        },
        "confirmed_willing": {
            "holds": confirmed,
            "label_zh": STANCE_LABELS_ZH["confirmed_willing"],
            "evidence_count": len(by_stance["confirmed_willing"]),
            "detail": "对方已回复确认愿意承接" if confirmed else "尚未取得本人/代理的承接确认",
        },
    }

    if confirmed:
        level = "confirmed"
    elif has_history:
        level = "proven"
    elif stated:
        level = "stated"
    elif contactable:
        level = "contact_only"
    else:
        level = "none"

    signal_rows = [
        {
            "id": s.id,
            "signal_type": s.signal_type,
            "label_zh": COMMERCIAL_SIGNAL_LABELS_ZH.get(s.signal_type, s.signal_type),
            "stance": STANCE_BY_SIGNAL.get(s.signal_type),
            "value": s.value,
            "evidence_url": s.evidence_url,
            "evidence_quote": s.evidence_quote,
            "observed_at": s.observed_at.isoformat() if s.observed_at else None,
            "occurred_at": s.occurred_at.isoformat() if s.occurred_at else None,
            "verified_at": s.verified_at.isoformat() if s.verified_at else None,
            "verified_by": s.verified_by,
            "fact_status": s.fact_status,
            "is_inferred": s.is_inferred,
            "note": s.notes,
            "source": s.source_system or "commercial_signals",
        }
        for s in signals
    ]
    return CommercialMaturity(
        stances, level, signal_rows + sponsorship_rows + contact_rows + route_rows,
        caveats, blocking,
    )


# =============================================================================
# 证据完整度
# =============================================================================


def evidence_completeness(
    view: CandidateView,
    relation: RelationEvidence,
    commercial: CommercialMaturity,
    quotes: dict,
) -> EvidenceCompleteness:
    known: list[str] = []
    missing: list[str] = []

    def record(label: str, present: bool) -> None:
        (known if present else missing).append(label)

    account = _primary_account(view.creator)
    record("内容领域", bool(view.verticals))
    record("市场归属", bool(view.market_region))
    record("受众类型证据", bool(view.audience_evidence))
    record("粉丝规模", bool(view.followers))
    record("近期活跃度", bool(account and account.posting_frequency))
    record("播放/互动数据", bool(account and (account.median_views or account.engagement_rate)))
    record("目标关注关系", relation.observed)
    record("商业合作证据", commercial.level in ("stated", "proven", "confirmed"))
    record("可执行联系方式", commercial.contactable)
    record("报价", bool(quotes.get("has_priced_quote")))
    return EvidenceCompleteness(known, missing)


# =============================================================================
# 优先级与下一步
# =============================================================================


def decide_priority(
    view: CandidateView,
    fit: DomainFit,
    relation: RelationEvidence,
    commercial: CommercialMaturity,
) -> tuple[str, str, str, str]:
    """返回 ``(priority, reason, next_step_kind, next_step)``。

    刻意写成一串 if 而不是打分：这些是并列的排除规则。写成权重就会让
    「有邮箱」去补偿「内容不相关」，而规格明确禁止这件事。
    """
    kind_label = OBJECT_KIND_LABELS_ZH.get(view.object_kind, view.object_kind)

    if commercial.blocking:
        return (
            "observe_only", "；".join(commercial.blocking),
            "research_only", NEXT_STEP_LABELS_ZH["research_only"],
        )

    if view.object_kind == "target_person":
        # 关系再强也不等于愿意接单。规格点名了投资人和研究者这一类。
        return (
            "observe_only",
            (
                f"标记为{kind_label}：作为希望被触达的对象，关系强度不能替代商业合作意愿；"
                "如需投放需另行确认是否承接商业合作"
            ),
            "research_only", NEXT_STEP_LABELS_ZH["research_only"],
        )

    if view.object_kind == "supplier":
        return (
            "confirm_willingness",
            f"标记为{kind_label}：需先确认代理范围与可采购库存，本身不作为投放对象",
            "confirm_willingness", "确认代理范围、可采购 roster 与授权条件",
        )

    if view.creator is not None and view.creator.creator_tier in NON_RECOMMENDABLE_TIERS:
        return (
            "observe_only", "已判定为非创作者账号，没有可售卖的受众关系",
            "research_only", NEXT_STEP_LABELS_ZH["research_only"],
        )

    if fit.verdict == "mismatch":
        # 「联系方式不能抵消内容不相关」在这里落地：这一支不看 commercial。
        return (
            "observe_only", f"内容与所选方向不符：{fit.summary}",
            "research_only", NEXT_STEP_LABELS_ZH["research_only"],
        )

    if not fit.passed:
        return (
            "observe_only", f"适配性无法判断：{fit.summary}",
            "collect_samples", NEXT_STEP_LABELS_ZH["collect_samples"],
        )

    note = f"；{'、'.join(relation.priority_signals)}" if relation.priority_signals else ""

    if commercial.contactable and commercial.has_paid_evidence:
        return (
            "priority_inquiry",
            f"{fit.summary}；{commercial.LEVELS[commercial.level]}；有可执行联系路径{note}",
            "request_quote", NEXT_STEP_LABELS_ZH["request_quote"],
        )
    if commercial.contactable:
        return (
            "confirm_willingness",
            (
                f"{fit.summary}；有联系方式但付费合作证据不足"
                f"（{commercial.LEVELS[commercial.level]}）{note}"
            ),
            "confirm_willingness", NEXT_STEP_LABELS_ZH["confirm_willingness"],
        )
    return (
        "need_bd_path",
        f"{fit.summary}；尚未找到可执行联系人{note}",
        "find_contact", NEXT_STEP_LABELS_ZH["find_contact"],
    )


# =============================================================================
# 报价衔接
# =============================================================================


def quote_state(creator: Creator | None) -> dict:
    """当前报价状态 —— 决定这个人在不在客户筛选池里。

    客户 MVP 的池子由 ``recommend.candidate_pool`` 定义：必须有带金额的报价。
    这里回答同一个问题、用同一个标准，避免两处判据悄悄分叉。
    """
    if creator is None:
        return {
            "has_priced_quote": False, "quote_count": 0, "priced_quote_count": 0,
            "in_client_pool": False, "quotes": [],
            "note": "尚未建立供给记录，取得报价前不进入客户筛选池",
        }
    priced = [q for q in creator.quotes if q.amount_usd is not None]
    return {
        "has_priced_quote": bool(priced),
        "quote_count": len(creator.quotes),
        "priced_quote_count": len(priced),
        "in_client_pool": bool(priced) and creator.creator_tier not in NON_RECOMMENDABLE_TIERS,
        "note": (
            "已有报价，复用原记录；新报价追加为新行，不覆盖历史" if priced
            else "尚无报价，属内部待建联名单；取得报价后自动进入客户筛选池"
        ),
        "quotes": [
            {
                "id": q.id,
                "deliverable_raw": q.deliverable_raw,
                "content_format": q.content_format,
                "platform": q.platform,
                "currency": q.currency,
                "status": q.status,
                "needs_review": q.needs_review,
                "client_visible": q.client_visible,
                "quoted_at": (
                    q.message.quoted_at.isoformat()
                    if q.message and q.message.quoted_at else None
                ),
                "valid_until": (
                    q.message.valid_until.isoformat()
                    if q.message and q.message.valid_until else None
                ),
                "quote_source": q.message.source if q.message else None,
                "quote_source_detail": q.message.source_detail if q.message else None,
                # 金额是 Mango 的成本；这套接口整体只挂在 /api/internal 下。
                "internal_cost_usd": q.internal_cost_usd,
            }
            for q in sorted(creator.quotes, key=lambda q: q.id)
        ],
    }


# =============================================================================
# 入口
# =============================================================================


def screen(
    view: CandidateView,
    prefs: DomainPreferences,
    *,
    stored_signals: list[CommercialSignal],
    relation_signals: list,
    target_accounts: dict[str, XAccount],
    collected_nodes: set[str],
) -> Screening:
    """对一个候选跑完四份判断，给出优先级和下一步。

    所有批量数据（关系边、目标账号、采集覆盖）由调用方一次取好传进来 ——
    这个函数不查库，所以既能用于实时接口，也能用于离线报告。
    """
    fit = domain_fit(view, prefs)
    relation = relation_evidence(
        view, prefs, signals=relation_signals,
        target_accounts=target_accounts, collected_nodes=collected_nodes,
    )
    commercial = commercial_maturity(view, stored_signals)
    quotes = quote_state(view.creator)
    completeness = evidence_completeness(view, relation, commercial, quotes)

    priority, reason, step_kind, step = decide_priority(view, fit, relation, commercial)
    candidate = view.candidate
    # 人写过的下一步不被规则冲掉；规则的结论仍留在 priority_reason 里可见。
    if candidate is not None and candidate.next_step_is_manual and candidate.next_step:
        step_kind, step = candidate.next_step_kind, candidate.next_step

    return Screening(
        view=view, domain_fit=fit, relation=relation, commercial=commercial,
        completeness=completeness, priority=priority, priority_reason=reason,
        next_step_kind=step_kind, next_step=step, quote_state=quotes,
    )


def priority_label(priority: str) -> str:
    return BD_PRIORITY_LABELS_ZH.get(priority, priority)


def screening_dict(screening: Screening) -> dict:
    """一个候选的完整卡片 —— 前端一张卡需要的全部字段。

    这套接口整体只挂在 ``/api/internal`` 下，所以联系方式的值原样返回。
    客户接口不引用本模块的任何东西。
    """
    view = screening.view
    candidate = view.candidate
    return {
        "key": view.key,
        "candidate_id": candidate.id if candidate else None,
        "creator_id": view.creator.id if view.creator else None,
        "platform": view.platform,
        "handle": view.handle,
        "display_name": view.display_name,
        "profile_url": view.profile_url,
        "followers": view.followers,
        "bio": view.bio,
        "source_kind": view.source_kind,
        "source_kind_label_zh": SOURCE_KIND_LABELS_ZH.get(view.source_kind),
        "object_kind": view.object_kind,
        "object_kind_label_zh": OBJECT_KIND_LABELS_ZH.get(view.object_kind),
        "object_kind_basis": view.object_kind_basis,
        "target_group": view.target_group,

        # --- 为什么在池子里。**不是**关系证据。 ---
        "discovery_basis": view.discovery_basis,
        "review_flags": view.review_flags,

        # --- 四份分开的判断 ---
        "domain_fit": screening.domain_fit.as_dict(),
        "relation_evidence": screening.relation.as_dict(),
        "commercial_maturity": screening.commercial.as_dict(),
        "evidence_completeness": screening.completeness.as_dict(),

        # --- 结论 ---
        "priority": screening.priority,
        "priority_label_zh": priority_label(screening.priority),
        "priority_reason": screening.priority_reason,
        "next_step_kind": screening.next_step_kind,
        "next_step": screening.next_step,
        "next_step_is_manual": bool(candidate and candidate.next_step_is_manual),

        # --- 报价与跟进 ---
        "quote_state": screening.quote_state,
        "bd_status": candidate.bd_status if candidate else "new",
        "bd_status_note": candidate.bd_status_note if candidate else None,
        "owner": candidate.owner if candidate else None,
        "last_contact_at": (
            candidate.last_contact_at.isoformat()
            if candidate and candidate.last_contact_at else None
        ),

        # --- 来源方原始说法：整块隔离，自带「未核验」标记 ---
        "source_claims": (
            {
                "source_name": candidate.source_name,
                "source_ref": candidate.source_ref,
                "score_raw": candidate.source_score_raw,
                "tier_raw": candidate.source_tier_raw,
                "notes_raw": candidate.source_notes_raw,
                "status": candidate.source_claims_status,
                "warning": "来源名单的评分与分层为待核验输入，未参与本页任何判断",
            }
            if candidate else None
        ),
        "screening_version": screening.version,
    }
