"""可 BD 筛选的实时接口 —— 一次调用同时返回「有报价的推荐」和「可建联的人」。

前端的形状决定了这里的形状：上面是偏好选择（领域 / 市场 / 圈层 / 具体目标
人物），下面两块列表。改一个偏好，两块都要跟着变。所以主入口是一个 **无状态
的现算接口**，不是一张预先算好的名单表 —— 名单表意味着偏好一变就过期。

两块列表回答的是两个不同的问题，刻意分开返回：

* ``priced_recommendations`` —— 已有报价、现在就能报给客户的对象。走的是既有
  的 13 轴引擎（``recommend.assess``），偏好被包装成一份**不落库的** Brief。
  不落库是有意的：临时试筛不该在客户的项目历史里留下一份简报。
* ``bd_candidates`` —— 还没有报价、需要 Mango 去建联的人。走
  ``bd_discovery`` + ``bd_screening``，四份判断分开返回。

两块共用同一组偏好和同一份目标人物关系数据，所以同一个人不会在两边得到互相
矛盾的说法。

整个路由挂在 ``/api/internal`` 下：这里有成本、联系方式、供应商身份和内部
备注，客户接口永远不引用本模块。
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import bd_discovery, bd_screening, recommend
from .bd_models import (
    BD_PRIORITIES,
    BD_PRIORITY_LABELS_ZH,
    BD_STATUS_LABELS_ZH,
    BD_STATUSES,
    COMMERCIAL_SIGNAL_LABELS_ZH,
    COMMERCIAL_SIGNAL_TYPES,
    NEXT_STEP_KINDS,
    NEXT_STEP_LABELS_ZH,
    OBJECT_KIND_LABELS_ZH,
    OBJECT_KINDS,
    REQUIRES_HUMAN_VERIFICATION,
    STANCE_BY_SIGNAL,
    BDCandidate,
    CommercialSignal,
)
from .bd_screening import CandidateView, DomainPreferences
from .db import session_dependency
from .models import (
    Brief,
    ContactMethod,
    Creator,
    InternalTask,
    Quote,
    QuoteMessage,
)
from .normalize import (
    AUDIENCE_TYPE_LABELS_ZH,
    AUDIENCE_TYPES,
    MARKET_REGION_LABELS_ZH,
    MARKET_REGIONS,
    VERTICALS,
)
from .observation_models import XAccount
from .quotes import FX_ASOF, price_band, to_usd

router = APIRouter(prefix="/api/internal/bd", tags=["internal-bd"])

db = session_dependency

#: 一次返回多少候选。默认压得比较低：这是给人看的建联队列，不是导出接口。
DEFAULT_LIMIT = 60

#: 本地新建行的 id 起点。BD 归档的 id 占 1..357 这一段，``migrate_from_bd``
#: 每次重建都会把它们原样带回来。本地新建的行如果用自增 id，某次重建就可能把
#: 同一个 id 分给一条 BD 行，而被保留下来的本地行会在恢复时把它覆盖掉 ——
#: 「id 恰好没撞上」不是外键。分段隔开就再也不会撞。
_LOCAL_ID_BASE = 1_000_000


def _next_local_id(session: Session, model) -> int:
    current = session.scalar(
        select(func.max(model.id)).where(model.id >= _LOCAL_ID_BASE)
    )
    return (current or _LOCAL_ID_BASE - 1) + 1


# =============================================================================
# 请求模型
# =============================================================================


class ScreenRequest(BaseModel):
    """前端上半部分选的东西。每一项都可以不填 —— 不填就是「没问」。"""

    verticals: list[str] = Field(default_factory=list)
    market_regions: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    audience_types: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    #: 具体目标人物的 X handle。关系证据只对这些人算。
    target_handles: list[str] = Field(default_factory=list)
    label: str | None = None

    #: 只影响已有报价那一块，用于预算可执行性轴。
    total_budget_usd: float | None = None
    per_creator_budget_max_usd: float | None = None
    objectives: list[str] = Field(default_factory=list)
    content_formats: list[str] = Field(default_factory=list)

    #: 可 BD 那一块的取数范围。
    priorities: list[str] = Field(default_factory=list)
    #: 至少被几位所选目标人物关注才**显示这个标记**。它不再决定谁进池子 ——
    #: 池子由「能不能买」定义，共同关注只是打上去的资格标记。
    min_targets: int = 1
    #: 只保留被至少一位所选目标人物关注的候选。默认 False：一个能买、内容对口
    #: 但暂时没被任何目标关注的人，仍然是有效的建联对象，不该被删掉。
    require_target_follow: bool = False
    #: roster centrality（我们自己的创作者关注了谁）。默认关闭，它排出来的是
    #: 同圈互推账号，回答的不是「谁能买」。
    include_roster_centrality: bool = False
    #: 观察层里简介带商务入口的账号 —— 池子的主要扩充面。
    include_buyable: bool = True
    include_creators: bool = True
    include_ingested: bool = True
    min_roster_followers: int = bd_discovery.MIN_ROSTER_FOLLOWERS
    limit: int = DEFAULT_LIMIT
    priced_limit: int = 30

    def preferences(self) -> DomainPreferences:
        return DomainPreferences.from_dict(self.model_dump())


class CandidateUpdate(BaseModel):
    """更新一个候选的 BD 状态。第一次写入会把观察层账号提升为被跟踪的候选。"""

    actor: str = Field(min_length=1)
    bd_status: str | None = None
    bd_status_note: str | None = None
    owner: str | None = None
    object_kind: str | None = None
    object_kind_basis: str | None = None
    next_step_kind: str | None = None
    next_step: str | None = None
    mark_contacted: bool = False


class SignalCreate(BaseModel):
    """记录一条商业合作证据。

    ``evidence_url`` 或 ``evidence_quote`` 至少要有一个：没有出处的证据不是
    证据，规格要求联系方式保留原始来源和核验时间。
    """

    actor: str = Field(min_length=1)
    signal_type: str
    value: str | None = None
    evidence_url: str | None = None
    evidence_quote: str | None = None
    occurred_at: dt.date | None = None
    notes: str | None = None
    #: 只有人核验过才置 True。默认 False —— 导入即未核验。
    verified: bool = False


class ContactCreate(BaseModel):
    actor: str = Field(min_length=1)
    method_type: str
    value: str = Field(min_length=1)
    source_url: str | None = None
    source_note: str | None = None
    verified: bool = False


class QuoteCreate(BaseModel):
    """取得新报价后写回。**永远追加，不覆盖历史。**

    ``raw_text`` 必填：报价的原文是这套系统里最不能丢的东西，解析器还会继续
    改进，改进后要能重新解析。
    """

    actor: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    amount: float | None = None
    currency: str = "USD"
    platform: str = "Unknown"
    content_format: str = "unknown"
    deliverable_raw: str | None = None
    quantity: int = 1
    is_package: bool = False
    quote_source: str = "kol_direct"
    #: 供应商 / 经纪人名字，当报价经由他们给出时。
    source_detail: str | None = None
    quoted_at: dt.date | None = None
    valid_until: dt.date | None = None
    minimum_spend_usd: float | None = None
    usage_rights: str | None = None
    exclusivity: str | None = None
    turnaround: str | None = None


# =============================================================================
# 选项 —— 前端上半部分的可选值
# =============================================================================


@router.get("/options")
def options(session: Session = Depends(db)):
    """偏好选择器要用的全部词表，加上可选的目标人物。

    目标人物列表分两段：**已人工确认的 Root** 和 **尚未确认的候选**。分开是
    必要的 —— 未确认的候选里有相当比例是同圈互关账号，把两者混在一个下拉框里
    等于让人在不知情的情况下选中一个假目标。
    """
    accounts = session.scalars(
        select(XAccount)
        .where(XAccount.root_review_status != "rejected", XAccount.handle.is_not(None))
        .order_by(XAccount.followers.desc())
        .limit(400)
    ).all()

    def row(account: XAccount) -> dict:
        return {
            "handle": account.handle,
            "rest_id": account.rest_id,
            "display_name": account.display_name,
            "followers": account.followers,
            "root_type": account.root_type,
            "review_status": account.root_review_status,
        }

    accepted = [row(a) for a in accounts if a.root_review_status == "accepted"]
    pending = [row(a) for a in accounts if a.root_review_status == "pending"]
    return {
        "verticals": list(VERTICALS),
        "market_regions": [
            {"key": k, "label_zh": MARKET_REGION_LABELS_ZH.get(k, k)} for k in MARKET_REGIONS
        ],
        "audience_types": [
            {"key": k, "label_zh": AUDIENCE_TYPE_LABELS_ZH.get(k, k)} for k in AUDIENCE_TYPES
        ],
        "platforms": ["X", "YouTube", "Instagram", "TikTok", "LinkedIn", "Threads"],
        "priorities": [
            {"key": k, "label_zh": BD_PRIORITY_LABELS_ZH[k]} for k in BD_PRIORITIES
        ],
        "object_kinds": [
            {"key": k, "label_zh": OBJECT_KIND_LABELS_ZH[k]} for k in OBJECT_KINDS
        ],
        "bd_statuses": [{"key": k, "label_zh": BD_STATUS_LABELS_ZH[k]} for k in BD_STATUSES],
        "commercial_signal_types": [
            {
                "key": k,
                "label_zh": COMMERCIAL_SIGNAL_LABELS_ZH[k],
                "stance": STANCE_BY_SIGNAL[k],
                "requires_verification": k in REQUIRES_HUMAN_VERIFICATION,
            }
            for k in COMMERCIAL_SIGNAL_TYPES
        ],
        "next_step_kinds": [
            {"key": k, "label_zh": NEXT_STEP_LABELS_ZH[k]} for k in NEXT_STEP_KINDS
        ],
        "target_people": {
            "accepted": accepted,
            "pending_review": pending,
            "note": (
                "未审核候选不是 Root：其关注信号只能作研究线索。"
                "选中它们做筛选是允许的，但结果里会如实标注。"
            ),
        },
    }


# =============================================================================
# 主入口 —— 实时演算
# =============================================================================


@router.post("/screen")
def screen(payload: ScreenRequest, session: Session = Depends(db)):
    """一次算出两块：有报价的推荐 + 可建联的人。"""
    prefs = payload.preferences()

    # 目标人物与采集覆盖，两块共用，只查一次。
    target_accounts = bd_screening.resolve_target_nodes(session, prefs.target_handles)
    collected = bd_screening.collected_target_nodes(session, set(target_accounts))
    relation_by_creator = bd_screening.signals_by_creator(
        session, set(target_accounts) if target_accounts else None
    )

    priced = _priced_section(session, payload, prefs, relation_by_creator)
    bd = _bd_section(session, payload, prefs, target_accounts, collected, relation_by_creator)

    return {
        "preferences": prefs.as_dict(),
        "computed_at": dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(),
        "rule_versions": {
            "recommendation": recommend.RULE_VERSION,
            "bd_screening": bd_screening.SCREENING_VERSION,
        },
        "priced_recommendations": priced,
        "bd_candidates": bd,
        "collection_gaps": bd_discovery.collection_gaps(
            session, prefs, target_accounts, min_targets=payload.min_targets,
        ),
        "reading_note": (
            "两块列表回答不同问题：上面是现在就能报价执行的对象，下面是需要 Mango "
            "先建联的人。可 BD 列表的排序是**建联队列顺序**，不是匹配度排名 ——"
            "没有项目简报时不生成统一的项目匹配排名。"
        ),
    }


def _ephemeral_brief(payload: ScreenRequest, prefs: DomainPreferences) -> Brief:
    """把偏好包装成一份**不入库**的 Brief，喂给既有的 13 轴引擎。

    不落库是刻意的：随手试筛不应该在客户的项目历史里留下一份简报，也不应该
    产生一个 ``RecommendationRun`` —— 那是客户真正提交需求时才存在的东西。
    """
    return Brief(
        id=None,
        client_id=None,
        name=prefs.label or "内部实时筛选（未落库）",
        verticals=",".join(prefs.verticals) or None,
        target_markets=",".join(prefs.market_regions) or None,
        content_languages=",".join(prefs.languages) or None,
        target_audiences=",".join(prefs.audience_types) or None,
        platforms=",".join(prefs.platforms) or None,
        content_formats=",".join(payload.content_formats) or None,
        objectives=",".join(payload.objectives) or None,
        total_budget_usd=payload.total_budget_usd,
        per_creator_budget_max_usd=payload.per_creator_budget_max_usd,
    )


def _priced_section(
    session: Session,
    payload: ScreenRequest,
    prefs: DomainPreferences,
    relation_by_creator: dict[int, list],
) -> dict:
    """已有报价的对象，按偏好实时重跑 13 轴。"""
    brief = _ephemeral_brief(payload, prefs)
    pool = recommend.candidate_pool(session)

    assessments = [
        recommend.assess(creator, brief, relation_by_creator.get(creator.id, []))
        for creator in pool
    ]
    # 和客户接口一致：mismatch 不淘汰，只排后面并把未满足条件写清楚。
    assessments.sort(key=lambda a: a.rank_score, reverse=True)
    shown = assessments[: max(payload.priced_limit, 0)]

    return {
        "pool_size": len(pool),
        "returned": len(shown),
        "items": [_assessment_dict(a) for a in shown],
        "supply_gaps": _supply_gaps(assessments),
        "note": (
            "来源为已有可用报价的对象，可直接进入客户筛选池。"
            "rank_score 仅用于排序，任何界面都不得单独展示。"
        ),
    }


#: 会被当作「供给里到底有没有这种人」来检查的轴。风险、数据可信度一类不在
#: 其中：它们描述的是某个对象的状态，不是库存的一个维度。
_GAP_AXES = ("content_vertical", "market_language", "platform", "content_format", "audience")


def _supply_gaps(assessments: list) -> list[dict]:
    """整个已报价库存里都没有的东西，明说出来。

    这是从 BD 时代的 ``campaign_matching`` 继承的一条规则：**报缺口，不要拿
    通用账号填上去**。实测里立刻用上了 —— 选「中文圈」时 276 个已报价对象
    全部 mismatch，因为库里一个中文圈对象都没有。按设计 mismatch 不淘汰，
    于是界面会照常给出一串人；不说破的话，看的人会以为这就是中文圈的推荐，
    而事实是这批人一个都不面向中文圈。
    """
    gaps: list[dict] = []
    for key in _GAP_AXES:
        asked = False
        usable = 0
        label = key
        for assessment in assessments:
            axis = assessment.axis(key)
            if axis is None or axis.verdict == "not_asked":
                continue
            asked = True
            label = axis.label_zh
            if axis.verdict in ("match", "partial"):
                usable += 1
        if asked and usable == 0 and assessments:
            gaps.append({
                "axis": key,
                "label_zh": label,
                "detail": (
                    f"已有报价的 {len(assessments)} 个对象中，没有一个符合所选「{label}」条件"
                ),
                "next_step": "这是供给缺口，需要新建联，而不是从现有名单里挑一个近似的",
            })
    return gaps


def _assessment_dict(assessment) -> dict:
    quote = assessment.quote
    return {
        "creator_id": assessment.creator_id,
        "creator_name": assessment.creator_name,
        "axes": [
            {
                "key": a.key, "label_zh": a.label_zh, "verdict": a.verdict,
                "detail": a.detail, "evidence": a.evidence,
            }
            for a in assessment.axes
        ],
        "matched": assessment.matched,
        "unmet": assessment.unmet,
        "missing": assessment.missing,
        "reasons": assessment.reasons,
        "risks": assessment.risks,
        "next_steps": assessment.next_steps,
        "quote": (
            {
                "id": quote.id,
                "platform": quote.platform,
                "content_format": quote.content_format,
                "deliverable_raw": quote.deliverable_raw,
                "currency": quote.currency,
                # 内部面，成本可见。客户序列化层读不到本模块。
                "internal_cost_usd": quote.internal_cost_usd,
                "client_price_band": quote.client_price_band,
                "client_visible": quote.client_visible,
                "status": quote.status,
                "needs_review": quote.needs_review,
            }
            if quote else None
        ),
        "rank_score": assessment.rank_score,
    }


def _bd_section(
    session: Session,
    payload: ScreenRequest,
    prefs: DomainPreferences,
    target_accounts: dict[str, XAccount],
    collected: set[str],
    relation_by_creator: dict[int, list],
) -> dict:
    views = bd_discovery.build_views(
        session,
        target_accounts=target_accounts,
        min_targets=payload.min_targets,
        include_creators=payload.include_creators,
        include_buyable=payload.include_buyable,
        include_observed=payload.include_roster_centrality,
        include_ingested=payload.include_ingested,
        min_roster_followers=payload.min_roster_followers,
    )
    stored = bd_discovery.stored_signals_by_key(session, views)

    screenings = [
        bd_screening.screen(
            view, prefs,
            stored_signals=stored.get(view.key, []),
            relation_signals=(
                relation_by_creator.get(view.creator.id, []) if view.creator else []
            ),
            target_accounts=target_accounts,
            collected_nodes=collected,
        )
        for view in views
    ]

    wanted = set(payload.priorities) or set(BD_PRIORITIES)
    kept = [s for s in screenings if s.priority in wanted]
    if payload.require_target_follow:
        kept = [s for s in kept if s.relation.observed]

    # 排序 = 建联队列顺序，不是匹配度。
    #
    # 主排序是**商务可执行性**，不是「多少名人关注他」—— 后者排出来的是名人榜，
    # 审计里已经验证过（前 20 名全是 CEO 和研究大牛，一个能买的都没有）。关系
    # 在这里只作为同档内的次序：都能买、都能联系上时，能进目标信息流的排前面。
    # 而且先看**互动**再看关注条数，因为一条关注远弱于一条公开互动。
    order = {key: index for index, key in enumerate(BD_PRIORITIES)}
    level_rank = {
        "confirmed": 0, "proven": 1, "stated": 2, "contact_only": 3, "none": 4,
    }
    strength_rank = {"interaction": 0, "follow_only": 1, "none": 2}
    kept.sort(
        key=lambda s: (
            order.get(s.priority, 99),
            level_rank.get(s.commercial.level, 9),
            strength_rank.get(s.relation.strength, 9),
            -s.relation.distinct_targets,
            -len(s.completeness.known),
            -(s.view.followers or 0),
        )
    )

    by_priority: dict[str, int] = {key: 0 for key in BD_PRIORITIES}
    by_source: dict[str, int] = {}
    for item in screenings:
        by_priority[item.priority] = by_priority.get(item.priority, 0) + 1
        by_source[item.view.source_kind] = by_source.get(item.view.source_kind, 0) + 1

    shown = kept[: max(payload.limit, 0)]
    return {
        "pool_size": len(screenings),
        "matched": len(kept),
        "returned": len(shown),
        "by_priority": {
            key: {"count": count, "label_zh": BD_PRIORITY_LABELS_ZH[key]}
            for key, count in by_priority.items()
        },
        "by_source_kind": {
            key: {"count": count, "label_zh": bd_screening.SOURCE_KIND_LABELS_ZH.get(key, key)}
            for key, count in sorted(by_source.items())
        },
        "items": [bd_screening.screening_dict(s) for s in shown],
        "note": (
            "排序为建联队列顺序，不是匹配度排名。适配程度、关系证据、"
            "商务成熟度与证据完整度分开展示，任何一项都不能抵消另一项。"
        ),
    }


# =============================================================================
# 单个候选
# =============================================================================


def _find_view(
    session: Session, platform: str, handle: str, prefs: DomainPreferences,
) -> CandidateView:
    key = f"{platform}:{handle.lstrip('@').lower()}"
    views = bd_discovery.build_views(session, min_roster_followers=1)
    for view in views:
        if view.key.lower() == key.lower():
            return view
    raise HTTPException(status_code=404, detail="candidate not found")


@router.get("/candidates/{platform}/{handle}")
def candidate_detail(
    platform: str,
    handle: str,
    verticals: str = Query(default=""),
    market_regions: str = Query(default=""),
    languages: str = Query(default=""),
    audience_types: str = Query(default=""),
    platforms: str = Query(default=""),
    target_handles: str = Query(default=""),
    session: Session = Depends(db),
):
    """一个候选的完整卡片，按同一组偏好现算。

    偏好走 query string 而不是 body：详情页要能被直接分享成一个链接，链接里
    必须带着「当时是按什么条件筛的」，否则两个人看到同一个 URL 会得到不同的
    结论。
    """
    prefs = DomainPreferences.from_dict({
        "verticals": verticals, "market_regions": market_regions,
        "languages": languages, "audience_types": audience_types,
        "platforms": platforms, "target_handles": target_handles,
    })
    return _candidate_payload(session, platform, handle, prefs)


def _candidate_payload(
    session: Session, platform: str, handle: str, prefs: DomainPreferences,
) -> dict:
    """卡片本体，和路由分开。

    写操作的 handler 要返回更新后的卡片，但**不能**直接调用上面那个路由函数：
    它的默认值是 FastAPI 的 ``Query(...)`` 哨兵对象，只有经过请求解析才会变成
    字符串。直接调用会把哨兵当成参数值传下去 —— 一个只在内部调用路径上出现、
    在 HTTP 上完全看不出来的错误。
    """
    view = _find_view(session, platform, handle, prefs)

    target_accounts = bd_screening.resolve_target_nodes(session, prefs.target_handles)
    collected = bd_screening.collected_target_nodes(session, set(target_accounts))
    relation = bd_screening.signals_by_creator(
        session, set(target_accounts) if target_accounts else None
    )
    stored = bd_discovery.stored_signals_by_key(session, [view])

    screening = bd_screening.screen(
        view, prefs,
        stored_signals=stored.get(view.key, []),
        relation_signals=relation.get(view.creator.id, []) if view.creator else [],
        target_accounts=target_accounts,
        collected_nodes=collected,
    )
    payload = bd_screening.screening_dict(screening)
    payload["open_tasks"] = _open_tasks(session, view)
    return payload


def _open_tasks(session: Session, view: CandidateView) -> list[dict]:
    if view.creator is None:
        return []
    tasks = session.scalars(
        select(InternalTask).where(
            InternalTask.creator_id == view.creator.id, InternalTask.status == "open"
        )
    ).all()
    return [
        {"id": t.id, "kind": t.kind, "title": t.title, "owner": t.owner} for t in tasks
    ]


def _ensure_candidate(session: Session, view: CandidateView, actor: str) -> BDCandidate:
    """把一个候选提升为被跟踪的行 —— 第一次有人对它做动作时才创建。

    观察层里有一万多个账号，全部预先建行只会得到一张一万行的空表。行在**有人
    真的开始跟进它**的那一刻出现，所以这张表里的每一行都对应一次真实的判断。
    """
    if view.candidate is not None:
        return view.candidate
    candidate = BDCandidate(
        creator_id=view.creator.id if view.creator else None,
        platform=view.platform,
        handle=view.handle.lstrip("@").lower(),
        display_name=view.display_name,
        profile_url=view.profile_url,
        object_kind=view.object_kind,
        object_kind_basis=view.object_kind_basis,
        target_group=view.target_group,
        source_name="internal_bd_surface",
        source_ref=f"promoted_by:{actor}",
        source_claims_status="unverified",
    )
    session.add(candidate)
    session.flush()
    view.candidate = candidate
    return candidate


def _payload_after_write(session: Session, platform: str, handle: str) -> dict:
    """写入之后重新渲染这张卡片。

    ``expire_on_commit=False``（这套系统全局如此，为的是提交后对象仍可读）意味着
    已经加载过的关系集合**不会**在 commit 后刷新。写完报价再直接渲染，读到的
    还是写之前那份 ``creator.quotes``，卡片会显示「尚无报价」—— 一个只在写完
    紧接着读的路径上出现、单看数据库完全正常的错误。所以这里显式过期一次。
    """
    session.expire_all()
    return _candidate_payload(session, platform, handle, DomainPreferences())


@router.patch("/candidates/{platform}/{handle}")
def update_candidate(
    platform: str, handle: str, payload: CandidateUpdate, session: Session = Depends(db),
):
    """更新跟进状态。状态是一句事实陈述，所以必须由人写，带 actor。"""
    view = _find_view(session, platform, handle, DomainPreferences())
    candidate = _ensure_candidate(session, view, payload.actor)

    if payload.bd_status is not None:
        if payload.bd_status not in BD_STATUSES:
            raise HTTPException(status_code=422, detail=f"bd_status must be in {BD_STATUSES}")
        candidate.bd_status = payload.bd_status
    if payload.object_kind is not None:
        if payload.object_kind not in OBJECT_KINDS:
            raise HTTPException(status_code=422, detail=f"object_kind must be in {OBJECT_KINDS}")
        candidate.object_kind = payload.object_kind
        candidate.object_kind_basis = payload.object_kind_basis or f"人工判定（{payload.actor}）"
    if payload.next_step_kind is not None:
        if payload.next_step_kind not in NEXT_STEP_KINDS:
            raise HTTPException(
                status_code=422, detail=f"next_step_kind must be in {NEXT_STEP_KINDS}"
            )
        candidate.next_step_kind = payload.next_step_kind
        candidate.next_step = payload.next_step or NEXT_STEP_LABELS_ZH[payload.next_step_kind]
        # 记住这是人写的，否则下一次重新筛选会用规则结论把它冲掉。
        candidate.next_step_is_manual = True
    if payload.bd_status_note is not None:
        candidate.bd_status_note = payload.bd_status_note
    if payload.owner is not None:
        candidate.owner = payload.owner
    if payload.mark_contacted:
        candidate.last_contact_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)

    session.commit()
    return _payload_after_write(session, platform, handle)


@router.post("/candidates/{platform}/{handle}/commercial-signals", status_code=201)
def add_signal(
    platform: str, handle: str, payload: SignalCreate, session: Session = Depends(db),
):
    """记录一条商业合作证据。

    两条硬约束：证据必须有出处；「已确认愿意承接本项目」必须由人核验。后者是
    三件事里最强的一件，只能来自真实回复，不能由任何自动流程产生。
    """
    if payload.signal_type not in COMMERCIAL_SIGNAL_TYPES:
        raise HTTPException(
            status_code=422, detail=f"signal_type must be in {COMMERCIAL_SIGNAL_TYPES}"
        )
    if not (payload.evidence_url or payload.evidence_quote):
        raise HTTPException(
            status_code=422,
            detail="evidence_url or evidence_quote is required; a signal with no source is not evidence",
        )
    if payload.signal_type in REQUIRES_HUMAN_VERIFICATION and not payload.verified:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{payload.signal_type} records that someone replied and agreed; "
                "it must be recorded as verified by a person"
            ),
        )

    view = _find_view(session, platform, handle, DomainPreferences())
    candidate = _ensure_candidate(session, view, payload.actor)
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)

    signal = CommercialSignal(
        # 事实挂在人身上；只有机构候选才退回挂在候选行上。
        creator_id=view.creator.id if view.creator else None,
        bd_candidate_id=None if view.creator else candidate.id,
        signal_type=payload.signal_type,
        value=payload.value,
        evidence_url=payload.evidence_url,
        evidence_quote=payload.evidence_quote,
        observed_at=now.date(),
        occurred_at=payload.occurred_at,
        verified_at=now if payload.verified else None,
        verified_by=payload.actor if payload.verified else None,
        fact_status="verified_fact" if payload.verified else "research_lead",
        notes=payload.notes,
        source_system="internal_bd_surface",
        source_ref=payload.actor,
    )
    session.add(signal)

    if payload.signal_type == "confirmed_willing_reply":
        candidate.bd_status = "replied"
    session.commit()
    return _payload_after_write(session, platform, handle)


@router.post("/candidates/{platform}/{handle}/contacts", status_code=201)
def add_contact(
    platform: str, handle: str, payload: ContactCreate, session: Session = Depends(db),
):
    """记录一个联系方式，带原始来源和核验时间。

    没有 Creator 就没有地方放联系方式 —— 一个联系方式属于某个人，不属于某份
    名单。所以观察层账号要先建立供给记录，这个动作由 ``_ensure_creator`` 做，
    并且明确标为「因建联而创建」。
    """
    view = _find_view(session, platform, handle, DomainPreferences())
    creator = _ensure_creator(session, view, payload.actor)
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)

    contact = ContactMethod(
        id=_next_local_id(session, ContactMethod),
        creator_id=creator.id,
        method_type=payload.method_type,
        value=payload.value,
        verified_at=now if payload.verified else None,
    )
    # 附加列由 add_bd_tables.py 补上；老库跑起来也不该炸，所以用 setattr。
    for attr, value in (
        ("source_url", payload.source_url),
        ("source_note", payload.source_note),
        ("observed_at", now.date()),
        ("verified_by", payload.actor if payload.verified else None),
        ("is_inferred", False),
    ):
        if hasattr(ContactMethod, attr):
            setattr(contact, attr, value)
    session.add(contact)
    session.commit()
    return _payload_after_write(session, platform, handle)


def _ensure_creator(session: Session, view: CandidateView, actor: str) -> Creator:
    """观察层账号在被真正建联时，才升级成一条供给记录。

    这是「同一账号复用身份记录」的另一半：如果这个账号已经有 Creator，直接
    用它，绝不新建第二条。
    """
    if view.creator is not None:
        return view.creator

    from .models import SocialAccount

    creator = Creator(
        id=_next_local_id(session, Creator),
        display_name=view.display_name or view.handle,
        primary_handle=view.handle,
        verticals=",".join(view.verticals) or None,
        market_region=view.market_region,
        market_region_basis=view.market_region_basis,
        audience_types=",".join(view.audience_types) or None,
        audience_types_basis=view.audience_basis,
        audience_types_evidence=view.audience_evidence,
        creator_class="Unknown",
        creator_tier="unknown",
        source_system="signal_map_bd_intake",
        source_ref=f"{view.key} via {actor}",
    )
    session.add(creator)
    session.flush()
    session.add(SocialAccount(
        id=_next_local_id(session, SocialAccount),
        creator_id=creator.id,
        platform=view.platform,
        handle=view.handle,
        profile_url=view.profile_url,
        is_primary=True,
        platform_uid=view.x_account.rest_id if view.x_account else None,
        bio=view.bio,
        followers=view.followers,
        metrics_observed_at=view.metrics_observed_at,
    ))
    if view.x_account is not None and view.x_account.creator_id is None:
        # 观察层账号和供给记录接上，下一次就不会又被当成新发现。
        view.x_account.creator_id = creator.id
    if view.candidate is not None:
        view.candidate.creator_id = creator.id
    view.creator = creator
    return creator


@router.post("/candidates/{platform}/{handle}/quotes", status_code=201)
def add_quote(
    platform: str, handle: str, payload: QuoteCreate, session: Session = Depends(db),
):
    """取得新报价后写回 —— 这就是「待 BD」和「已有报价」之间的那道衔接。

    三条规则：

    * **原文必存**，解析永远可以重来。
    * **追加，不覆盖**：历史报价一条不删，所以同一个人可以有多轮报价。
    * **金额进 ``internal_cost_usd``，不进 ``client_price_usd``**。这是创作者
      向 Mango 要的价，是成本；直接抄成对客价会同时泄露成本并抹掉利润。对客
      价必须由人在报价复核接口里另行确定。

    写完之后这个人就有了带金额的报价，也就自动进入客户筛选池 —— 不需要任何
    额外的搬运动作。
    """
    view = _find_view(session, platform, handle, DomainPreferences())
    creator = _ensure_creator(session, view, payload.actor)

    message = QuoteMessage(
        id=_next_local_id(session, QuoteMessage),
        creator_id=creator.id,
        raw_text=payload.raw_text,
        source=payload.quote_source,
        source_is_inferred=False,
        source_detail=payload.source_detail,
        quoted_at=payload.quoted_at,
        observed_at=dt.date.today(),
        valid_until=payload.valid_until,
        source_system="signal_map_bd_intake",
        source_ref=payload.actor,
    )
    session.add(message)
    session.flush()

    amount_usd, fx_rate = to_usd(payload.amount, payload.currency)
    quote = Quote(
        id=_next_local_id(session, Quote),
        creator_id=creator.id,
        message_id=message.id,
        deliverable_raw=payload.deliverable_raw,
        content_format=payload.content_format,
        platform=payload.platform,
        quantity=payload.quantity,
        is_package=payload.is_package,
        amount=payload.amount,
        currency=payload.currency,
        amount_usd=amount_usd,
        fx_rate_used=fx_rate,
        fx_asof=FX_ASOF if fx_rate else None,
        # 人手录入的报价来自真实沟通，解析可信度高；但对客展示仍需另行复核。
        status="confirmed_valid" if amount_usd is not None else "no_quote",
        needs_review=amount_usd is None,
        parse_confidence="high" if amount_usd is not None else "unparsed",
        parse_notes=f"由 {payload.actor} 在内部建联界面录入",
        raw_segment=payload.raw_text,
        internal_cost_usd=amount_usd,
        client_price_usd=None,
        client_price_band=price_band(amount_usd),
        client_visible=False,
        client_visibility_note="对客报价待 Mango 确定加价后开放",
        minimum_spend_usd=payload.minimum_spend_usd,
        usage_rights=payload.usage_rights,
        exclusivity=payload.exclusivity,
        turnaround=payload.turnaround,
    )
    session.add(quote)

    candidate = _ensure_candidate(session, view, payload.actor)
    if amount_usd is not None:
        candidate.bd_status = "quoted"

    # 询价任务做完了就关掉，否则队列只会越来越长。
    for task in session.scalars(
        select(InternalTask).where(
            InternalTask.creator_id == creator.id,
            InternalTask.status == "open",
            InternalTask.kind == "price_inquiry",
        )
    ):
        task.status = "done"
        task.outcome_notes = f"已取得报价（{payload.actor}）"
        task.closed_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)

    session.commit()
    return _payload_after_write(session, platform, handle)


@router.post("/candidates/{platform}/{handle}/tasks", status_code=201)
def raise_task(
    platform: str, handle: str, payload: CandidateUpdate, session: Session = Depends(db),
):
    """把这个候选的下一步落成一条内部工单，进 Mango 的作业队列。"""
    prefs = DomainPreferences()
    view = _find_view(session, platform, handle, prefs)
    candidate = _ensure_candidate(session, view, payload.actor)

    target_accounts = bd_screening.resolve_target_nodes(session, prefs.target_handles)
    stored = bd_discovery.stored_signals_by_key(session, [view])
    screening = bd_screening.screen(
        view, prefs, stored_signals=stored.get(view.key, []), relation_signals=[],
        target_accounts=target_accounts, collected_nodes=set(),
    )
    kind = payload.next_step_kind or screening.next_step_kind
    title = payload.next_step or screening.next_step

    task = InternalTask(
        kind=_TASK_KIND_BY_NEXT_STEP.get(kind, "other"),
        title=f"{view.display_name or view.handle}：{title}",
        detail=screening.priority_reason,
        creator_id=view.creator.id if view.creator else None,
        owner=payload.owner or payload.actor,
    )
    session.add(task)
    session.commit()
    return {
        "task_id": task.id, "kind": task.kind, "title": task.title,
        "candidate_id": candidate.id,
    }


#: 下一步 -> 内部工单类型。两套词表分开是有意的：下一步是 BD 视角的动作，
#: 工单类型是作业队列的分类，两者不必一一对应。
_TASK_KIND_BY_NEXT_STEP = {
    "find_contact": "contact_gap",
    "confirm_willingness": "willingness_check",
    "request_quote": "price_inquiry",
    "collect_samples": "evidence_gap",
    "research_only": "other",
    "none": "other",
}


# =============================================================================
# AI FrontRun —— 被观察的重要人物最近开始关注了谁
# =============================================================================
#
# 挂在 ``/api/internal`` 下是有意的：CLAUDE.md §11 —— **共用数据层，不共用
# 客户界面**。这是 Mango 自己的情报面。


@router.get("/frontrun/deltas")
def frontrun_deltas(
    domainGroup: str | None = Query(default=None),
    windowDays: int | None = Query(default=None, ge=1, le=365),
    minWatchers: int = Query(default=1, ge=1),
    session: Session = Depends(db),
):
    """自上一次**完整**采集以来，观察名单新关注了谁。

    没有基线时返回的是「首采，尚无基线」，**不是空列表** —— 空列表会被读成
    「最近没有动静」，而实际情况是「我们还没法比」。
    """
    from . import frontrun

    return frontrun.report(
        session, domain=domainGroup, window_days=windowDays, min_watchers=minWatchers
    ).as_dict()


@router.get("/frontrun/coverage")
def frontrun_coverage(
    domainGroup: str | None = Query(default=None),
    session: Session = Depends(db),
):
    """观察名单的采集健康度：谁该重采了，谁的采集被截断了。"""
    from . import frontrun

    return frontrun.coverage(session, domain=domainGroup)
