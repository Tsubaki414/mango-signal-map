"""客户项目面 —— target-backed KOL discovery 的对外接口。

定位纠正（这一版存在的全部理由）
--------------------------------
之前的实现把候选池等同于**报价库**：客户从 Mango 已经谈过价的人里挑。那把产品
做成了一个带筛选器的目录，而且把最有价值的部分砍掉了。

正确的方法是 **target-backed KOL discovery**：

    客户说出想影响谁（目标人物 / root）
      → 取这些人的关注与互动网络，求共同子集
      → 得到候选池，其中一部分在报价库里，一部分从没建联过
      → 过三道过滤：真实流量 / 有没有可合作路径 / 有没有内容表达面
      → 客户可见的执行池

**候选池不等于报价库。** 池子里相当一部分人 Mango 既没联系过也没有报价 —— 他们
是顺着客户想影响的对象找出来的。这既是对客户的核心卖点，也是 Mango 资源库自我
扩张的入口：客户的选择直接驱动 BD 优先级。

所以 ``source`` 有两类，同一份名单里一起返回：

* ``priced``     —— 已在报价库，可立即确认档位与档期
* ``discovered`` —— 共同关注算出来的，``price`` 为 null，需要 Mango 去建联

排序的主依据是**连接**，不是综合分
----------------------------------
排序按「连接数 × 连接强度」。内容方向、人群、市场只作次要修正。把所有维度揉成
一个综合分再排序，会把「这个人能进入你想影响的人的信息流」这个唯一清晰的信号
稀释掉 —— 而那正是客户买的东西。

**与所选目标人物没有任何公开连接记录的人，不进入客户可见名单。** 内部建联队列
仍然保留他们（见 ``bd_api``），那是另一个问题。

客户端可见性
------------
这个模块的序列化是**逐字段白名单**。联系方式原文、供应商底价与原始金额、Mango
内部人名与介绍人、内部触达等级、谈判记录，一律不出现。价格只以档位
``$/$$/$$$/$$$$`` 下发；``discovered`` 的价格是 ``None``，前端显示「报价待询」。

预算分两段返回 ``{known:{lo,hi}, pendingCount}``。**未报价的人不折算成任何数字**
—— 用估算值填补未知，会让客户以为已经报过价，而 Mango 得为客户看到的每个数字
负责。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .bd_models import NON_BUYABLE_OBJECT_KINDS, BDCandidate
from .bd_screening import CandidateView, signals_from_bio
import datetime as dt

from .models import (
    FEEDBACK_ACTIONS,
    AttentionSignal,
    CandidateItem,
    Creator,
    FeedbackEvent,
    InternalTask,
    Quote,
)
from .normalize import AUDIENCE_TYPE_LABELS_ZH, MARKET_REGION_LABELS_ZH
from .observation_models import FollowEdge, XAccount

# =============================================================================
# 词表
# =============================================================================

#: 候选是怎么来的。前端按这个分组：可立即确认在上，新发现待建联在下。
CANDIDATE_SOURCES = ("priced", "discovered")

#: 商务状态是三态，不是布尔。每一态都必须附 ``bizEvidence`` 说明依据。
BIZ_STATES = ("ready", "open_channel", "needs_bd")

BIZ_STATE_LABELS_ZH = {
    "ready": "可立即确认档位与档期",
    "open_channel": "有公开合作入口，待接洽",
    "needs_bd": "需 Mango 主动建联",
}

#: 连接强度三级。**不输出 H0–H3 这类内部代号**，也不在数据不足时伪造精度。
LINK_STRENGTHS = ("strong", "medium", "weak")

LINK_STRENGTH_LABELS_ZH = {
    "strong": "多次公开互动，主题相关",
    "medium": "单次公开互动，或双向关注",
    "weak": "单向关注",
}

#: 价格档位。按单条主内容的 USD 分档 —— 原始金额永不下发。
#: 档界与前端 ``adapter.js`` 保持一致，否则同一个人在两边显示不同档位。
_TIER_BOUNDS = ((500, 1), (2000, 2), (8000, 3))

TIER_LABELS = {1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}

#: 各档位的隐藏区间，用于预算汇总。区间刻意宽 —— 窄到能反推出成本就失去意义。
TIER_RANGES = {1: (150, 500), 2: (500, 2000), 3: (2000, 8000), 4: (8000, 25000)}

#: 发现候选的过滤门槛。三道过滤对应「真实流量 / 可合作路径 / 内容表达面」。
MIN_FOLLOWERS = 5_000


def tier_for(amount_usd: float | None) -> int | None:
    if amount_usd is None:
        return None
    for ceiling, tier in _TIER_BOUNDS:
        if amount_usd < ceiling:
            return tier
    return 4


# =============================================================================
# 连接
# =============================================================================


@dataclass
class Link:
    """一条 ``目标人物 → 候选`` 的公开连接记录。

    方向固定：是**目标人物**关注/互动了候选，不是反过来。反过来那条边说明的是
    候选在看谁，跟客户想被谁看到无关。
    """

    target_id: str
    target_handle: str | None
    #: follow | reply | quote | repost | co_appear | mention | shared_circle
    type: str
    count: int
    last_seen: str | None
    verified: bool
    mutual: bool = False
    #: ``shared_circle`` 专用：这条间接关系经由哪几位创作者。没有它，卡片上
    #: 就成了一句无法追查的「同圈层」。
    via: tuple[str, ...] = ()

    @property
    def strength(self) -> str:
        """strong / medium / weak，规则写死在这里，不做加权。

        * ``strong``  多次公开互动
        * ``medium``  单次互动，或双向关注
        * ``weak``    仅单向关注 —— 而单向关注**只说明内容有机会出现在他的
          时间线上**。@pmarca 关注 32,758 个账号，那条时间线上出现过什么，跟他
          看没看过是两回事。
        """
        if self.type == "shared_circle":
            # 二跳永远是 weak。它说明的是「他在你目标所关注的创作者的视野里」，
            # 不是「你的目标关注他」—— 后者才是直接关注，前者连那个都不是。
            return "weak"
        if self.type != "follow":
            return "strong" if self.count >= 2 else "medium"
        return "medium" if self.mutual else "weak"

    def as_dict(self) -> dict:
        return {
            "targetId": self.target_id,
            "targetHandle": self.target_handle,
            "type": self.type,
            "count": self.count,
            "lastSeen": self.last_seen,
            "verified": self.verified,
            "strength": self.strength,
            "strengthLabel": (
                _SHARED_CIRCLE_LABEL if self.type == "shared_circle"
                else LINK_STRENGTH_LABELS_ZH[self.strength]
            ),
            "via": list(self.via),
        }


#: 间接关系的措辞。**不能出现「关注了本候选」这类直接关系的说法。**
_SHARED_CIRCLE_LABEL = "同圈层：你的目标关注的创作者也在关注他（间接关系）"

#: 排序权重。**只有连接参与主排序。**
_STRENGTH_WEIGHT = {"strong": 5.0, "medium": 2.0, "weak": 1.0}

#: 二跳的折扣。一条间接关系不该和一条直接关注等价 —— 直接关注至少说明这个
#: 候选的内容会出现在目标本人的时间线上，二跳连这个都不成立。
_SHARED_CIRCLE_WEIGHT = 0.35


def link_score(links: list[Link]) -> float:
    """连接数 × 连接强度。这是主排序，也是唯一的主排序。

    刻意不把内容、人群、市场揉进来 —— 那些是**次要修正**，在
    ``_secondary_rank`` 里单独作为次级键，所以任何时候都能回答「他排在前面是
    因为连接，还是因为内容」。
    """
    return sum(
        (_SHARED_CIRCLE_WEIGHT if link.type == "shared_circle"
         else _STRENGTH_WEIGHT[link.strength])
        * min(link.count, 5)
        for link in links
    )


def shared_circle_links(
    session: Session,
    target_accounts: dict[str, XAccount],
    *,
    min_bridges: int = 2,
) -> dict[int, list[Link]]:
    """二跳：``目标人物 → 关注 → 我方创作者 → 关注 → 候选``。

    直接关注不是唯一有意义的关系。一个候选被「你的目标所关注的那几位创作者」
    共同关注，说明他在同一个注意力圈层里 —— 这比什么都没有强，也确实能带来
    可执行的候选（实测扩出 1,062 位一跳够不到的人）。

    **但它比直接关注弱一档，措辞和权重都必须体现。** 直接关注至少说明这个人的
    内容会出现在目标本人的时间线上；二跳连这个都不成立，它只说明「他在你目标
    看的人的视野里」。所以强度永远是 ``weak``，权重再打 0.35 折，标签里带
    「间接关系」四个字。

    中间人只取**目标人物真的关注了的**那些我方创作者（实测 188 位里只有 25 位
    符合）。不加这层过滤就退化成 roster centrality —— 那反映的是 Mango 签了谁，
    CLAUDE.md 里记着这条。
    """
    target_ids = {a.id: a for a in target_accounts.values()}
    if not target_ids:
        return {}

    # 一跳：目标关注了谁。同时找出其中属于我方创作者的，作为中间人。
    direct: dict[int, set[int]] = defaultdict(set)
    for source_id, tgt in session.execute(
        select(FollowEdge.source_id, FollowEdge.target_id).where(
            FollowEdge.source_id.in_(list(target_ids)),
            FollowEdge.disappeared_at.is_(None),
        )
    ).all():
        direct[tgt].add(source_id)

    # 反向查：``direct`` 有 13 万个 id，拿它做 ``IN`` 会直接超出 SQLite 的参数
    # 上限。而我方创作者账号只有几百个 —— 查那一边，再在内存里取交集。
    bridges = {
        account.id: account
        for account in session.scalars(
            select(XAccount).where(XAccount.creator_id.is_not(None))
        )
        if account.id in direct
    }
    if not bridges:
        return {}

    # 二跳：中间人关注了谁。一跳已经够得到的不算 —— 那是更强的关系，不该被
    # 一条更弱的边重复计一次。
    reached: dict[int, set[int]] = defaultdict(set)
    for source_id, tgt in session.execute(
        select(FollowEdge.source_id, FollowEdge.target_id).where(
            FollowEdge.source_id.in_(list(bridges)),
            FollowEdge.disappeared_at.is_(None),
        )
    ).all():
        if tgt in direct or tgt in target_ids:
            continue
        reached[tgt].add(source_id)

    out: dict[int, list[Link]] = {}
    for account_id, bridge_ids in reached.items():
        if len(bridge_ids) < min_bridges:
            continue
        via = sorted(
            bridges[b].handle for b in bridge_ids if bridges.get(b) and bridges[b].handle
        )
        # 归到「哪位目标的圈层」：取第一位关注了这些中间人的目标。
        roots = sorted({
            target_ids[t].handle
            for b in bridge_ids for t in direct.get(b, ()) if t in target_ids
        } - {None})
        out[account_id] = [Link(
            target_id=f"circle:{roots[0]}" if roots else "circle",
            target_handle=roots[0] if roots else None,
            type="shared_circle",
            count=len(bridge_ids),
            last_seen=None,
            verified=False,
            via=tuple(via[:6]),
        )]
    return out


def collect_links(
    session: Session,
    target_accounts: dict[str, XAccount],
    account_ids: list[int],
) -> dict[int, list[Link]]:
    """``{x_account_id: [Link, ...]}``，方向都是目标人物 → 候选。

    两个来源合起来：``FollowEdge`` 给关注边，``AttentionSignal`` 给互动边。
    互动边覆盖同一对时**替换**关注边 —— 一条回复比一条关注强得多，两条都列会让
    同一段关系被数两次。
    """
    if not target_accounts or not account_ids:
        return {}

    target_ids = {a.id: a for a in target_accounts.values()}
    by_rest_id = {a.rest_id: a for a in target_accounts.values()}
    out: dict[int, list[Link]] = defaultdict(list)

    # --- 关注边 ---
    #
    # 每一处 ``IN`` 都要分块。加入间接关系之后 ``account_ids`` 从几百涨到几千，
    # SQLite 的参数上限直接把整个接口打挂 —— 这是本文件里第二次踩同一个坑，
    # 数据量小的时候永远看不到。
    edges = []
    reverse: set[tuple[int, int]] = set()
    for chunk in _chunks(account_ids):
        edges.extend(session.execute(
            select(
                FollowEdge.source_id, FollowEdge.target_id, FollowEdge.last_seen_at
            ).where(
                FollowEdge.source_id.in_(list(target_ids)),
                FollowEdge.target_id.in_(chunk),
                FollowEdge.disappeared_at.is_(None),
            )
        ).all())
        # 反向边用来判双向关注。没采过候选的关注列表时它是空的 —— 那样只会让
        # 强度停在 weak，不会误判成 medium。
        reverse.update(session.execute(
            select(FollowEdge.source_id, FollowEdge.target_id).where(
                FollowEdge.source_id.in_(chunk),
                FollowEdge.target_id.in_(list(target_ids)),
                FollowEdge.disappeared_at.is_(None),
            )
        ).all())

    follow_pairs: dict[tuple[int, int], Link] = {}
    for source_id, target_id, last_seen in edges:
        target = target_ids[source_id]
        follow_pairs[(source_id, target_id)] = Link(
            target_id=target.rest_id,
            target_handle=target.handle,
            type="follow",
            count=1,
            last_seen=last_seen.date().isoformat() if last_seen else None,
            verified=False,
            mutual=(target_id, source_id) in reverse,
        )

    # --- 互动边 ---
    account_by_creator: dict[int, int] = {}
    for chunk in _chunks(account_ids):
        for account in session.scalars(select(XAccount).where(XAccount.id.in_(chunk))):
            if account.creator_id:
                account_by_creator[account.creator_id] = account.id

    interactions: dict[tuple[int, int, str], Link] = {}
    for chunk in _chunks(list(account_by_creator)):
        for signal in session.scalars(
            select(AttentionSignal).where(
                AttentionSignal.source_creator_id.in_(chunk),
                AttentionSignal.signal_type != "follow",
            )
        ):
            target = by_rest_id.get(signal.source_node)
            account_id = account_by_creator.get(signal.source_creator_id)
            if target is None or account_id is None:
                continue
            key = (target.id, account_id, signal.signal_type)
            existing = interactions.get(key)
            if existing is None:
                interactions[key] = Link(
                    target_id=target.rest_id,
                    target_handle=target.handle,
                    type=signal.signal_type,
                    count=signal.observation_count or 1,
                    last_seen=signal.occurred_at.isoformat() if signal.occurred_at else None,
                    verified=bool(signal.human_confirmed),
                )
            else:
                existing.count += signal.observation_count or 1

    interacted_pairs = {(t, a) for t, a, _ in interactions}
    for (source_id, target_id), link in follow_pairs.items():
        # 同一对已经有互动记录了，关注那条不再单列 —— 否则一段关系被数两次。
        if (source_id, target_id) in interacted_pairs:
            continue
        out[target_id].append(link)
    for (_source_id, account_id, _kind), link in interactions.items():
        out[account_id].append(link)
    return dict(out)


# =============================================================================
# 候选
# =============================================================================


@dataclass
class ProjectCandidate:
    """一个客户可见的候选。字段名对齐前端契约（camelCase）。"""

    source: str
    account: XAccount
    creator: Creator | None
    view: CandidateView | None
    links: list[Link] = field(default_factory=list)
    quotes: list[Quote] = field(default_factory=list)
    bd_candidate: BDCandidate | None = None
    inline_signals: list = field(default_factory=list)
    #: 内容与项目方向的关系：match / mismatch / unknown。``mismatch`` 的候选
    #: 在组装时就被排除了，所以这里只会是前者或后者。
    content_fit: str = "unknown"
    content_fit_basis: str | None = None

    @property
    def tier(self) -> int | None:
        """按最便宜的一条**单条内容**报价分档。套餐价不能和单条价比。"""
        singles = [
            q.amount_usd for q in self.quotes
            if q.amount_usd is not None and not q.is_package
        ]
        return tier_for(min(singles)) if singles else None

    @property
    def package_only(self) -> bool:
        """只有套餐报价。套餐价不能和单条价比，所以档位算不出来 —— 但这个人
        **仍然是可以立即确认的**，只是要先把套餐拆成具体内容形式。"""
        return bool(self.quotes) and self.tier is None

    @property
    def biz_state(self) -> tuple[str, str]:
        """``(state, evidence)``。三态，每一态都要说清依据。"""
        if self.source == "priced":
            # 用 ``self.quotes`` 判而不是 ``self.tier``：只有套餐报价的人算不出
            # 档位，之前因此掉进了 ``needs_bd``，卡片上同时写着「已在报价库」和
            # 「需 Mango 主动建联」—— 两句话互相打脸。
            if self.package_only:
                return "ready", "已在 Mango 报价库，为套餐报价，需按内容形式拆分确认"
            return "ready", "已在 Mango 报价库，档位与档期可直接确认"

        openers = [
            s for s in self.inline_signals
            if s.signal_type in ("business_email", "booking_form", "media_kit",
                                 "public_rate_card", "partnership_page",
                                 "accepts_brand_work_statement")
        ]
        if openers:
            quoted = next((s.evidence_quote for s in openers if s.evidence_quote), None)
            return "open_channel", (
                f"公开资料里有合作入口：{public_bio(quoted)}" if quoted else "公开资料里有合作入口"
            )
        return "needs_bd", "未发现公开合作入口，需 Mango 主动建联"


#: 项目类型里，哪几项会**改变名单成员**（而不只是改变顺序）。
#:
#: 只有这三项，而且只作用在 ``discovered`` 上：发现池是无界的（几千人），不按
#: 项目收窄就等于每个项目看到同一批人；``priced`` 是 Mango 有限的自有库存，
#: 客户有权看到「我们考虑过谁」，所以那边不做内容淘汰。
_MEMBERSHIP_PREFS = ("domains", "markets", "languages")


def content_fit(view: CandidateView | None, prefs: dict) -> tuple[str, str | None]:
    """``(match | mismatch | unknown, 依据)``。

    **只有 ``mismatch`` 会把人排除，``unknown`` 不会。** 发现候选的领域是从公开
    简介现推的，只有 47% 推得出来；把推不出来的一并删掉，会在客户完全看不见的
    情况下砍掉一半候选池，而那一半里恰恰包括简介写得含蓄的真人。这条规则和
    ``bd_screening.domain_fit`` 是同一条，也和「缺失数据从不淘汰候选」一致。
    """
    if view is None:
        return "unknown", None

    checks: list[tuple[str, list[str], list[str]]] = [
        ("内容方向", list(view.verticals), prefs.get("domains") or []),
        ("市场", [view.market_region] if view.market_region else [], prefs.get("markets") or []),
        ("内容语言", list(view.languages), prefs.get("languages") or []),
    ]
    matched: list[str] = []
    mismatched: list[str] = []
    for label, have, want in checks:
        if not want:
            continue          # 没问就不判
        if not have:
            continue          # 判不出来 -> unknown，不淘汰
        lowered = {w.lower() for w in want}
        if any(h and h.lower() in lowered for h in have):
            matched.append(label)
        else:
            mismatched.append(f"{label}为 {'、'.join(h for h in have if h)}")

    if mismatched:
        return "mismatch", "；".join(mismatched)
    if matched:
        return "match", "符合 " + "、".join(matched)
    return "unknown", None


#: 传播角色。八类里目前能从数据判出六类 —— ``引爆者`` 需要量级基准，
#: ``社区节点`` 需要社群数据，两者都还没有，所以不假装能判。
#:
#: 和 ``portfolio._creator_role`` 同一套词表，但那边只吃 ``creator_tier``
#: （BD 时期的分类），新发现的账号没有这个字段，于是角色一栏全是空的。
#: 这里改成吃**任何一种可得的证据**：身份判定、领域、受众、播放中位数。
ROLE_LABELS = (
    "引爆者", "解释者", "专业验证者", "圈层桥梁",
    "转化推动者", "回声节点", "媒体节点", "待定角色",
)

#: 被判为「引爆者」的播放中位数门槛。用播放而不是粉丝：粉丝是历史积累，
#: 一个百万粉但单帖一万播放的账号引爆不了任何东西。
_AMPLIFIER_MEDIAN_VIEWS = 100_000


def creator_role(candidate: ProjectCandidate) -> tuple[str, str | None]:
    """``(角色, 依据)``。判不出来就说 ``待定角色`` —— 不编。

    顺序是有意的：先看**他是什么**（媒体/分发），再看**他触达谁**（受众），
    最后才看**他有多大**（播放）。反过来会让量级压倒性质，把一个大号一律叫
    引爆者，而那正是这个产品要避免的单维判断。
    """
    view = candidate.view
    kind = (candidate.bd_candidate.effective_object_kind
            if candidate.bd_candidate else "unknown")
    tier = candidate.creator.creator_tier if candidate.creator else None
    audiences = set(view.audience_types) if view else set()

    if kind == "media_channel" or tier == "media":
        return "媒体节点", "账号性质为媒体/栏目，按库存采购而非个人声量"
    if tier == "distribution":
        return "回声节点", "BD 分类为泛 KOC / 铺量型，适合扩散而非背书"

    median = candidate.account.median_views
    if median and median >= _AMPLIFIER_MEDIAN_VIEWS:
        return "引爆者", f"近期单帖播放中位数 {median:,.0f}，具备把话题带出小圈层的量级"

    if audiences & {"developers", "researchers", "enterprise"}:
        return "专业验证者", f"受众集中在 {'、'.join(_zh(audiences & {'developers','researchers','enterprise'}))}"
    if audiences & {"investors", "founders"}:
        return "圈层桥梁", f"受众集中在 {'、'.join(_zh(audiences & {'investors','founders'}))}"
    if audiences & {"consumers", "students"}:
        return "转化推动者", f"受众集中在 {'、'.join(_zh(audiences & {'consumers','students'}))}"
    if audiences & {"creators", "designers", "marketers"}:
        return "解释者", f"受众集中在 {'、'.join(_zh(audiences & {'creators','designers','marketers'}))}"
    return "待定角色", None


def _zh(keys) -> list[str]:
    return [AUDIENCE_TYPE_LABELS_ZH.get(k, k) for k in sorted(keys)]


def _secondary_rank(candidate: ProjectCandidate, prefs: dict) -> tuple:
    """次要修正：内容方向 / 人群 / 市场。只在连接分相同时起作用。"""
    view = candidate.view
    if view is None:
        return (0, 0, 0)
    def overlap(have, want) -> int:
        lowered = {x.lower() for x in (want or [])}
        return sum(1 for x in have if x.lower() in lowered)
    return (
        -overlap(view.verticals, prefs.get("domains")),
        -overlap(view.audience_types, prefs.get("audiences")),
        -(1 if view.market_region in (prefs.get("markets") or []) else 0),
    )


# =============================================================================
# 发现管线
# =============================================================================


@dataclass
class DiscoveryResult:
    priced: list[ProjectCandidate]
    discovered: list[ProjectCandidate]
    targets_resolved: int
    targets_with_data: int
    #: 因为没通过三道过滤而没进名单的数量，按原因分开。给内部看的诚实计数。
    filtered_out: dict


def discover(
    session: Session,
    target_accounts: dict[str, XAccount],
    prefs: dict | None = None,
    *,
    min_followers: int = MIN_FOLLOWERS,
    discovered_cap: int | None = None,
    include_shared_circle: bool = True,
    min_bridges: int = 2,
) -> DiscoveryResult:
    """从目标人物的关注与互动网络里算出候选池。

    三道过滤，对应「真实流量 / 可合作路径 / 内容表达面」：

    1. **真实流量** —— 粉丝量下限。低于这个数的账号即使被名人关注，也没有可投放
       的受众。
    2. **内容表达面** —— 必须有公开简介。读不到任何内容的账号，客户卡片上除了
       粉丝数写不出任何东西，那不是一个可以判断的对象。
    3. **可合作路径** —— 这一道**不淘汰人**，只决定 ``bizState``：有公开入口的
       是 ``open_channel``，没有的是 ``needs_bd``，两者都进名单。需要 Mango 去
       建联恰恰是这个产品要做的事。

    目标人物本人不进候选池：名人互相关注是常态，但客户想影响的人不是投放对象。
    """
    prefs = prefs or {}
    target_ids = {a.id for a in target_accounts.values()}
    if not target_ids:
        return DiscoveryResult([], [], 0, 0, {})

    # **整个 Root 库 + 已判定买不到的账号都排除**，不只是本轮勾选的那几位。
    #
    # 客户这一轮只勾了 7 位目标，于是 Andrew Ng、Sam Altman、Jeff Dean 因为被
    # 其他目标关注，作为「发现候选」进了名单 —— 而他们是 Root 库里的人，是客户
    # 想被看见的对象，永远不会接投放。勾没勾选是这一轮的事，是不是目标人物是
    # 这个人的属性，两者不能混。
    excluded = set(target_ids) | _root_library_account_ids(session)

    # --- 目标人物指向的全部账号 ---
    reached: set[int] = set()
    for source_id, target_id in session.execute(
        select(FollowEdge.source_id, FollowEdge.target_id).where(
            FollowEdge.source_id.in_(list(target_ids)),
            FollowEdge.disappeared_at.is_(None),
        )
    ).all():
        if target_id not in excluded:
            reached.add(target_id)

    filtered = {
        "no_bio": 0, "below_follower_floor": 0, "reviewed_out": 0,
        "is_target_person": len(excluded) - len(target_ids),
        "off_project_direction": 0,
        "insufficient_profile": 0,
    }
    kept: list[XAccount] = []
    for chunk in _chunks(sorted(reached)):
        for account in session.scalars(select(XAccount).where(XAccount.id.in_(chunk))):
            if account.root_review_status == "rejected":
                filtered["reviewed_out"] += 1
                continue
            if (account.followers or 0) < min_followers:
                filtered["below_follower_floor"] += 1
                continue
            if not (account.bio or "").strip():
                filtered["no_bio"] += 1
                continue
            kept.append(account)

    # 间接关系（同圈层）。默认开：直接关注够不到的人里，有相当一部分是真正
    # 可投放的对象，只要求硬关系会把名单压得过短。
    circle = (
        shared_circle_links(session, target_accounts, min_bridges=min_bridges)
        if include_shared_circle else {}
    )
    if circle:
        known = {a.id for a in kept}
        for chunk in _chunks([i for i in circle if i not in known]):
            for account in session.scalars(select(XAccount).where(XAccount.id.in_(chunk))):
                if account.root_review_status == "rejected" or account.id in excluded:
                    continue
                if (account.followers or 0) < min_followers or not (account.bio or "").strip():
                    continue
                kept.append(account)

    links_by_account = collect_links(
        session, target_accounts, [a.id for a in kept]
    )
    for account_id, rows in circle.items():
        # 直接关系存在时不再叠加间接的 —— 同一段关系不该被数两次。
        if not links_by_account.get(account_id):
            links_by_account[account_id] = rows

    # --- 与报价库 join，标记 source ---
    creator_ids = [a.creator_id for a in kept if a.creator_id]
    quotes_by_creator: dict[int, list[Quote]] = defaultdict(list)
    if creator_ids:
        for chunk in _chunks(creator_ids):
            for quote in session.scalars(
                select(Quote).where(
                    Quote.creator_id.in_(chunk), Quote.amount_usd.is_not(None)
                )
            ):
                quotes_by_creator[quote.creator_id].append(quote)

    bd_by_handle = {
        row.handle: row
        for row in session.scalars(
            select(BDCandidate).options(selectinload(BDCandidate.signals))
        )
    }

    priced: list[ProjectCandidate] = []
    discovered: list[ProjectCandidate] = []
    for account in kept:
        links = links_by_account.get(account.id, [])
        if not links:
            # 与所选目标人物没有任何公开连接记录的人，不进客户可见名单。
            continue
        creator = session.get(Creator, account.creator_id) if account.creator_id else None
        quotes = quotes_by_creator.get(account.creator_id or -1, [])
        url = f"https://x.com/{account.handle}" if account.handle else None
        candidate = ProjectCandidate(
            source="priced" if quotes else "discovered",
            account=account,
            creator=creator,
            view=_view_for(account, creator),
            links=links,
            quotes=quotes,
            bd_candidate=bd_by_handle.get((account.handle or "").lower()),
            inline_signals=signals_from_bio(account.bio, source_url=url),
        )
        # 模型判过的领域优先于简介关键词：它带引用，而且对简介写得含蓄的人
        # 能读出关键词读不出的东西。两者都没有就是真的判不出来。
        suggested = candidate.bd_candidate.verticals_suggested if candidate.bd_candidate else None
        if suggested and candidate.view is not None:
            candidate.view.verticals = tuple(
                v.strip() for v in suggested.split(",") if v.strip()
            )
            candidate.view.verticals_basis = "llm"
        # 两边都算内容契合度：卡片上要显示这个人和项目方向的关系。**只有
        # discovered 会因为 mismatch 被排除** —— 报价库是 Mango 有限的自有库存，
        # 客户有权看到我们考虑过谁，所以那边只标注、不淘汰。
        verdict, basis = content_fit(candidate.view, prefs)
        candidate.content_fit = verdict
        candidate.content_fit_basis = basis

        if quotes:
            priced.append(candidate)
            continue

        if verdict == "mismatch":
            filtered["off_project_direction"] += 1
            continue

        # **卡片必须有实质内容才能出现在客户面前。**
        #
        # 「内容表达面」这道过滤之前只要求「有简介」，太松了：3,427 个通过身份
        # 过滤的候选里，1,969 个（57%）既没有身份判定、简介也读不出领域 —— 它们
        # 的卡片上除了 handle 和粉丝数什么都没有。那不是一个客户能判断的对象，
        # 是一行占位。宁可名单短，也不交半成品。
        if not _has_substance(candidate):
            filtered["insufficient_profile"] += 1
            continue
        discovered.append(candidate)

    def order(rows: list[ProjectCandidate], by_biz_state: bool = False) -> list[ProjectCandidate]:
        # 主排序：连接数 × 连接强度。次要修正只在连接分相同时起作用。
        #
        # ``by_biz_state`` 只用于 ``discovered``，先按有没有公开合作入口分带。
        # 理由是实测出来的：纯按连接排，新发现的前三名是 HuggingFace CEO、
        # Airbnb CEO、Perplexity CEO —— 连接数最高的一批**永远是名人**，因为
        # 名人互相关注。他们不会接投放，放在第一屏就等于把这一栏浪费掉。
        # 分带之后，有 sponsor 页 / 商务邮箱的人先出现，需要 Mango 主动建联的
        # 仍在名单里，只是排在后面。带内仍然完全按连接排序。
        rank = {"open_channel": 0, "needs_bd": 1, "ready": 0}
        return sorted(
            rows,
            key=lambda c: (
                rank[c.biz_state[0]] if by_biz_state else 0,
                -link_score(c.links),
                _secondary_rank(c, prefs),
            ),
        )

    priced = order(priced)
    discovered = order(discovered, by_biz_state=True)
    # 先返回已有资源，新发现逐步补入 —— 名单以能立即执行的部分为主体。
    cap = discovered_cap if discovered_cap is not None else max(3, len(priced) // 2)
    filtered["discovered_beyond_cap"] = max(len(discovered) - cap, 0)
    discovered = discovered[:cap]

    collected = _targets_with_data(session, target_accounts)
    return DiscoveryResult(
        priced, discovered, len(target_accounts), collected, filtered
    )


def _has_substance(candidate: "ProjectCandidate") -> bool:
    """这张卡片有没有客户能据以判断的东西。

    两条任一成立即可：**身份被判定为可投放**（LLM 读出他是创作者或媒体），
    或者**简介能读出内容领域**。两者都没有，卡片上只剩 handle 和粉丝数。
    """
    view = candidate.view
    if view is None or not view.verticals:
        # **必须能说出他做什么内容。** 之前「有受众判断」也算过关，于是
        # @bestofnextdoor（简介：quality neighborhood drama）进了 AI 项目的
        # 名单 —— 领域说不出来，任何领域过滤都拦不住他。
        return False

    # **而且必须被真的看过一眼。** @BChappatta 是 Bloomberg 专栏作者，简介里
    # 能读出 finance，于是他通过了领域这关 —— 但他的身份从没被判定过，因为他的
    # 连接数低于身份扫描的门槛。一个没人看过的人不该出现在客户面前，这正是
    # 「不交付半成品」的意思。
    candidate_row = candidate.bd_candidate
    if candidate_row is None or candidate_row.object_kind_suggested_by is None:
        return False
    return candidate_row.effective_object_kind in ("kol", "media_channel")


def _root_library_account_ids(session: Session) -> set[int]:
    """买不到的账号的 X id：Root 库成员，加上被判定为公众人物/机构官方号的。

    这是**属性**查询，不是本轮选择：一个被标为目标人物的账号，无论客户这轮有没有
    勾他，都不是投放对象。

    这里会读**机器建议**（``effective_object_kind`` 在人未判定时回落到
    ``object_kind_suggested``），破了「机器建议不参与判断」的一般规则。破例只在
    **一个方向**上：建议只把人从可投放名单里拿掉，永远不用来把人放进去。两个
    方向代价不对称 —— 错误排除一个可买的人，损失一次机会；错误放进一个 SEC 委员，
    是产品级别的尴尬。而且 ``unclear`` 从不排除任何人。
    """
    handles = {
        (candidate.handle or "").lower()
        for candidate in session.scalars(select(BDCandidate))
        if candidate.handle
        and candidate.effective_object_kind in NON_BUYABLE_OBJECT_KINDS
    }
    if not handles:
        return set()
    return {
        account.id
        for account in session.scalars(select(XAccount).where(XAccount.handle.is_not(None)))
        if (account.handle or "").lower() in handles
    }


def priced_supply(session: Session, prefs: dict) -> dict:
    """报价库里符合本项目方向的对象有多少 —— 说出来，别让客户自己猜。

    真实分布是 ai 239 / crypto 8 / finance 5（共 276）。一个金融项目看到 4 位
    AI 创作者，如果不说明白，会读成「Mango 的金融资源就这些」；实际情况是
    **这个方向的报价库还很薄，而发现池正是为此存在的**。
    """
    domains = [d.lower() for d in (prefs.get("domains") or [])]
    total = session.scalar(
        select(func.count(func.distinct(Quote.creator_id))).where(
            Quote.amount_usd.is_not(None)
        )
    ) or 0
    if not domains:
        return {"matchingDomain": None, "total": total, "note": None}

    matching = 0
    for creator in session.scalars(
        select(Creator).where(Creator.verticals.is_not(None))
    ):
        if not any(q.amount_usd is not None for q in creator.quotes):
            continue
        have = {v.strip().lower() for v in (creator.verticals or "").split(",")}
        if have & set(domains):
            matching += 1
    return {
        "matchingDomain": matching,
        "total": total,
        "note": (
            f"已有报价的对象共 {total} 位，其中 {matching} 位内容方向与本项目一致；"
            "本轮名单以新发现对象为主 —— 这些人由你选定的目标人物的公开关注关系"
            "反推得出，Mango 将逐位建联并取得报价。"
            if matching * 4 < total else
            f"已有报价的对象共 {total} 位，其中 {matching} 位内容方向与本项目一致。"
        ),
    }


def _narrowed(result: "DiscoveryResult") -> dict:
    """项目方向把发现池收窄了多少 —— 说清楚，别让它看起来像「本来就只有这些」。

    用词按客户界面的约束来：不说「缺口 / 无数据」，说「下一步可拓展」。
    """
    off = result.filtered_out.get("off_project_direction", 0)
    beyond = result.filtered_out.get("discovered_beyond_cap", 0)
    return {
        "offProjectDirection": off,
        "beyondCurrentBatch": beyond,
        "note": (
            f"本批之外还有 {beyond} 位同样与你的目标人物有公开关系，可在下一批展开"
            if beyond else "本批已覆盖当前可展开的发现候选"
        ),
    }


def _chunks(values, size: int = 900):
    items = list(values)
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _targets_with_data(session: Session, target_accounts: dict[str, XAccount]) -> int:
    """有多少位目标人物的关注列表真的采过 —— 零结果的分母。"""
    if not target_accounts:
        return 0
    ids = [a.id for a in target_accounts.values()]
    return session.scalar(
        select(func.count(func.distinct(FollowEdge.source_id))).where(
            FollowEdge.source_id.in_(ids)
        )
    ) or 0


def _view_for(account: XAccount, creator: Creator | None) -> CandidateView:
    """把账号摊平成筛选用的视图，复用 ``bd_discovery`` 的派生逻辑。"""
    from . import bd_discovery

    if creator is not None:
        return bd_discovery._creator_view(creator)
    return bd_discovery._observed_view(account, None, 0, 0)


# =============================================================================
# 客户端序列化 —— 逐字段白名单
# =============================================================================

#: 客户卡片上允许出现的字段。**白名单**：新加一列在有人把它写进这里之前，对
#: 客户是不可见的。黑名单等于承诺记住未来每一个字段，而那个承诺一定会破。
CLIENT_FIELDS = (
    "id", "name", "handle", "url", "platform", "followers", "avatarUrl", "bio",
    "contentLine", "source", "tier", "price", "priceNote", "quoteStatus",
    "quoteDate", "bizState", "bizStateLabel", "bizEvidence", "links",
    "linkCount", "topLinkStrength", "discoveryPath", "markets", "languages",
    "domains", "audiences", "formats", "engagement", "risk", "nextStep",
    "dataNote", "projectFit", "projectFitBasis", "role", "roleBasis",
)


def candidate_dict(candidate: ProjectCandidate) -> dict:
    """一个候选的客户可见表示。

    这里**没有**：联系方式原文、原始金额与底价、供应商身份、Mango 内部人名与
    介绍人、内部触达等级、谈判记录。价格只有档位。
    """
    account = candidate.account
    view = candidate.view
    tier = candidate.tier
    state, evidence = candidate.biz_state
    links = sorted(
        candidate.links,
        key=lambda link: (LINK_STRENGTHS.index(link.strength), -link.count),
    )
    role, role_basis = creator_role(candidate)
    handles = [link.target_handle for link in links if link.target_handle]

    payload = {
        "id": f"x:{account.rest_id}",
        "name": account.display_name or account.handle,
        "handle": account.handle,
        "url": f"https://x.com/{account.handle}" if account.handle else None,
        "platform": "X",
        "followers": account.followers,
        "avatarUrl": _avatar(candidate),
        "bio": public_bio(account.bio),
        "contentLine": _content_line(view, candidate),
        "source": candidate.source,

        # --- 价格：只有档位 ---
        "tier": tier,
        "price": TIER_LABELS[tier] if tier else None,
        "priceNote": (
            "档位区间，非报价；以 Mango 复核为准" if tier
            else "套餐报价，需按内容形式拆分后确认档位"
            if candidate.package_only
            else "报价待询"
        ),
        "quoteStatus": _quote_status(candidate),

        # --- 商务状态：三态 + 依据 ---
        "bizState": state,
        "bizStateLabel": BIZ_STATE_LABELS_ZH[state],
        "bizEvidence": evidence,

        # --- 连接 ---
        "links": [link.as_dict() for link in links],
        "linkCount": len(links),
        "topLinkStrength": links[0].strength if links else None,
        "discoveryPath": (
            {
                "via": handles[:8],
                "count": len(handles),
                "text": (
                    "经由 " + "、".join(f"@{h}" for h in handles[:3])
                    + (f" 等 {len(handles)} 位目标人物" if len(handles) > 3 else " 的公开关注")
                    + "发现"
                ),
            }
            if candidate.source == "discovered" and handles else None
        ),

        # --- 内容面 ---
        "markets": _labels(view.market_region, MARKET_REGION_LABELS_ZH) if view else [],
        "languages": list(view.languages) if view else [],
        "domains": list(view.verticals) if view else [],
        "audiences": (
            [AUDIENCE_TYPE_LABELS_ZH.get(a, a) for a in view.audience_types] if view else []
        ),
        "formats": _formats(candidate),
        "engagement": _engagement(candidate),
        "risk": _risk(candidate),
        # 这个人和本项目方向的关系。``unknown`` 是诚实答案：发现候选的领域是
        # 从公开简介现推的，只有约一半推得出来。
        # 传播角色：这个人在组合里承担什么，不是他有多大。
        "role": role,
        "roleBasis": role_basis,
        "projectFit": candidate.content_fit,
        "projectFitBasis": candidate.content_fit_basis,
        "nextStep": _next_step(state, candidate.source, candidate.package_only),
        "dataNote": _data_note(candidate),
    }
    # 白名单在这里真的生效，不只是文档里的一句话。
    return {key: payload[key] for key in CLIENT_FIELDS if key in payload}


import re as _re  # noqa: E402

_EMAIL_IN_BIO = _re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def public_bio(bio: str | None) -> str | None:
    """简介去掉邮箱之后的版本。

    简介是公开的，但**里面的邮箱是联系方式原文**，规格明令不下发。实测里
    ``collab@linusekenstam.com`` 就是这样漏出去的 —— 它同时存在于
    ``contact_methods`` 和这个人的公开简介，字段白名单挡住了前者，却让后者从
    ``bio`` 里整段带了出去。**白名单挡字段，挡不住字段内容**，这是同一类错误的
    第三次出现（前两次是成本被写进生成的句子里）。

    只抹邮箱，其余原样保留：简介本身是客户判断这个人做什么内容的主要依据。
    """
    if not bio:
        return bio
    return _EMAIL_IN_BIO.sub("［联系方式由 Mango 对接］", bio)


def _avatar(candidate: "ProjectCandidate") -> str | None:
    """头像：观察层优先，回落到 BD 时期存下来的那份。

    两处都存过头像，但存的不是同一批人：``XAccount`` 的来自 X 接口，
    ``SocialAccount`` 的来自 BD 导入。已有报价的对象大多只有后者。
    """
    if candidate.account.avatar_url:
        return candidate.account.avatar_url
    creator = candidate.creator
    if creator is None:
        return None
    for social in creator.accounts:
        if social.avatar_url:
            return social.avatar_url
    return None


def _content_line(view: CandidateView | None, candidate: "ProjectCandidate | None" = None) -> str | None:
    """一句话说明这个人做什么内容。

    以前只在能推出 ``verticals`` 时才给，结果三分之一的卡片这一行是空的 ——
    而这是客户扫一眼名单时唯一会读的那行字。现在按可靠度依次退让：领域 →
    身份判定的引用原文 → 什么都不写。**永远不编**：写不出就留空，让前端显示
    别的，而不是造一句听起来像判断的话。
    """
    if view is None:
        return None
    if view.verticals:
        who = "、".join(AUDIENCE_TYPE_LABELS_ZH.get(a, a) for a in view.audience_types[:2])
        what = "、".join(view.verticals[:2])
        return f"面向{who}的{what}内容" if who else f"{what}方向的内容"

    # 没有领域时，用身份判定里那句引用过的原文 —— 它来自这个人自己的简介。
    basis = candidate.bd_candidate.object_kind_suggestion_basis if (
        candidate and candidate.bd_candidate
    ) else None
    if basis and "「" in basis:
        quoted = basis.split("「", 1)[1].split("」", 1)[0]
        if quoted and len(quoted) > 3:
            return f"公开简介：{quoted}"
    return None


def _labels(value: str | None, mapping: dict) -> list[str]:
    return [mapping.get(value, value)] if value else []


def _quote_status(candidate: ProjectCandidate) -> str:
    """``confirmed`` / ``historical_unverified`` / ``pending_inquiry``。

    ``pending_inquiry`` 是新增的一档，专给发现候选。规格明写：未报价对象**不能
    显示为已确认可合作**，所以它绝不能落到 ``confirmed``。
    """
    if candidate.source == "discovered":
        return "pending_inquiry"
    visible = [q for q in candidate.quotes if q.client_visible and not q.needs_review]
    return "confirmed" if visible else "historical_unverified"


def _quote_date(candidate: ProjectCandidate) -> str | None:
    dates = [
        q.message.quoted_at for q in candidate.quotes
        if q.message is not None and q.message.quoted_at
    ]
    return max(dates).isoformat() if dates else None


def _formats(candidate: ProjectCandidate) -> list[str]:
    return sorted({q.content_format for q in candidate.quotes if q.content_format})


def _engagement(candidate: ProjectCandidate) -> dict:
    """近期内容表现。**粉丝数是历史积累，播放中位数才是现在还有多少人在看。**

    实测里两者能差一个量级：@WSJ 2,219 万粉，单帖中位播放 26,176；@MKBHD
    500 万粉，666,156。名单上只给粉丝数，等于把最关键的那个数藏起来了。

    中位数而不是平均数：一条爆款会把均值抬到看不出常态。
    """
    account = candidate.account
    median = account.median_views
    counted = account.recent_posts_counted
    latest = account.latest_post_at
    observed = account.posts_observed_at

    if median is None and candidate.creator is not None:
        # 已有报价的对象走 BD 时期存下的那份。
        for social in candidate.creator.accounts:
            if social.median_views:
                median = social.median_views
                observed = social.metrics_observed_at
                break

    return {
        "medianViews": median,
        "postsCounted": counted,
        "latestPostAt": latest.isoformat() if latest else None,
        "observedAt": observed.date().isoformat() if observed else None,
        "basis": (
            f"最近 {counted} 条原创帖的播放中位数（不含转推）" if counted
            else "取自 Mango 既有记录" if median else None
        ),
        "replyQuality": None,
    }


def _risk(candidate: ProjectCandidate) -> list[str]:
    """客户需要知道的风险提示。措辞不评判人。"""
    notes: list[str] = []
    for signal in candidate.inline_signals:
        if signal.signal_type == "self_promotion_only":
            notes.append("公开资料以自有产品推广为主，第三方合作方式需先确认")
        if signal.signal_type == "work_or_press_email":
            notes.append("公开邮箱为工作/媒体联系，商务合作路径另需确认")
    return sorted(set(notes))


def _next_step(state: str, source: str, package_only: bool = False) -> str:
    """客户视角的下一步。用词受约束：不说「缺口 / 待补充 / 无数据」。"""
    if state == "ready":
        return "按内容形式拆分套餐后确认档期" if package_only else "可直接确认档位与档期"
    if state == "open_channel":
        return "Mango 将通过其公开合作入口接洽并取得报价"
    return "Mango 将主动建联并取得报价" if source == "discovered" else "下一步可拓展"


def _data_note(candidate: ProjectCandidate) -> str | None:
    if candidate.source != "discovered":
        return None
    return (
        "该对象由你选定的目标人物公开关注关系发现，Mango 尚未与其建立合作；"
        "关注关系表示内容有机会出现在对方信息流，不代表对方一定会看到或回应"
    )


# =============================================================================
# 预算
# =============================================================================


def budget_summary(candidates: list[ProjectCandidate]) -> dict:
    """分两段返回：已知区间 + 待询价人数。

    **未报价的人不折算成任何数字。** 用估算值填补未知会让客户以为已经报过价，
    而 Mango 得为客户看到的每个数字负责 —— 这是那种客户签字之后才会发现的问题。
    """
    lo = hi = 0
    pending = 0
    for candidate in candidates:
        tier = candidate.tier
        if tier is None:
            pending += 1
            continue
        low, high = TIER_RANGES[tier]
        lo += low
        hi += high
    return {
        "known": {"lo": lo, "hi": hi} if lo or hi else {"lo": 0, "hi": 0},
        "pendingCount": pending,
        "note": "档位区间之和，非报价；以 Mango 复核为准。待询价对象未计入。",
    }


# =============================================================================
# 路由
# =============================================================================

from fastapi import APIRouter, Depends, HTTPException, Query  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from .auth import current_client  # noqa: E402
from .db import session_dependency  # noqa: E402
from .models import Brief, Client  # noqa: E402

router = APIRouter(prefix="/api", tags=["client-projects"])

db = session_dependency


class DiscoverRequest(BaseModel):
    """客户选定的目标人物。换一组人，名单和排序就换一份。"""

    targetIds: list[str] = Field(default_factory=list)
    minFollowers: int = MIN_FOLLOWERS
    discoveredCap: int | None = None
    #: 是否纳入间接关系（同圈层）。默认开 —— 只要硬关系会把名单压得过短。
    includeSharedCircle: bool = True
    #: 二跳至少要经过几位中间人。1 位太噪。
    minBridges: int = 2


def _project(session: Session, project_id: int, client: Client) -> Brief:
    """取项目，并确认它属于调用方。

    不属于就返回 **404 而不是 403** —— 403 会确认这个 id 存在，等于一个可以拿来
    枚举别家项目的接口。
    """
    brief = session.get(Brief, project_id)
    if brief is None or brief.client_id != client.id:
        raise HTTPException(status_code=404, detail="project not found")
    return brief


def _resolve_targets(session: Session, handles: list[str]) -> dict[str, XAccount]:
    from .bd_screening import resolve_target_nodes

    return resolve_target_nodes(session, tuple(handles))


def _candidates_payload(session: Session, brief: Brief, prefs: dict) -> dict:
    handles = [h.strip() for h in (brief.target_root_handles or "").split(",") if h.strip()]
    targets = _resolve_targets(session, handles)
    result = discover(session, targets, prefs)

    rows = [candidate_dict(c) for c in result.priced] + [
        candidate_dict(c) for c in result.discovered
    ]
    return {
        "projectId": brief.id,
        "targetIds": handles,
        "counts": {
            "priced": len(result.priced),
            "discovered": len(result.discovered),
            "total": len(rows),
        },
        "targets": {
            "selected": len(handles),
            "resolved": result.targets_resolved,
            "withRelationshipData": result.targets_with_data,
        },
        "candidates": rows,
        "budget": budget_summary(result.priced + result.discovered),
        "narrowedByProject": _narrowed(result),
        "pricedSupply": priced_supply(session, prefs),
        "method": (
            "名单由你选定的目标人物的公开关注与互动关系反推得出，"
            "按连接数量与强度排序；内容方向、人群与市场作次要修正。"
        ),
        "limits": (
            "公开关注表示内容有机会出现在对方信息流，不代表对方一定会看到、"
            "回复或转发。未取得报价的对象标注为待询价，不计入已知预算。"
        ),
    }


@router.post("/projects/{project_id}/discover")
def run_discovery(
    project_id: int,
    payload: DiscoverRequest,
    session: Session = Depends(db),
    client: Client = Depends(current_client),
):
    """按客户选定的目标人物跑一次发现，并把选择存回项目。

    存回项目是必要的：``GET /candidates`` 要能在不带参数的情况下重现同一份
    名单，否则刷新一次页面客户看到的就是另一批人。
    """
    brief = _project(session, project_id, client)
    brief.target_root_handles = ",".join(
        h.strip().lstrip("@") for h in payload.targetIds if h.strip()
    ) or None
    session.commit()

    prefs = _prefs_from(brief)
    targets = _resolve_targets(session, payload.targetIds)
    result = discover(
        session, targets, prefs,
        min_followers=payload.minFollowers,
        discovered_cap=payload.discoveredCap,
        include_shared_circle=payload.includeSharedCircle,
        min_bridges=payload.minBridges,
    )
    return {
        "projectId": brief.id,
        "counts": {
            "priced": len(result.priced),
            "discovered": len(result.discovered),
        },
        "targets": {
            "selected": len(payload.targetIds),
            "resolved": result.targets_resolved,
            "withRelationshipData": result.targets_with_data,
        },
        "candidates": (
            [candidate_dict(c) for c in result.priced]
            + [candidate_dict(c) for c in result.discovered]
        ),
        "budget": budget_summary(result.priced + result.discovered),
        "narrowedByProject": _narrowed(result),
    }


@router.get("/projects/{project_id}/candidates")
def list_candidates(
    project_id: int,
    session: Session = Depends(db),
    client: Client = Depends(current_client),
):
    """项目的候选池：``priced`` 与 ``discovered`` 同时返回，用 ``source`` 区分。"""
    brief = _project(session, project_id, client)
    return _candidates_payload(session, brief, _prefs_from(brief))


@router.get("/projects/{project_id}/signals")
def list_signals(
    project_id: int,
    session: Session = Depends(db),
    client: Client = Depends(current_client),
):
    """关系 Signal。无数据返回 ``[]``，前端自动降级，不显示空图。"""
    brief = _project(session, project_id, client)
    handles = [h.strip() for h in (brief.target_root_handles or "").split(",") if h.strip()]
    targets = _resolve_targets(session, handles)
    result = discover(session, targets, _prefs_from(brief))

    signals: list[dict] = []
    for candidate in result.priced + result.discovered:
        for link in candidate.links:
            signals.append({
                "kolId": f"x:{candidate.account.rest_id}",
                "rootId": link.target_id,
                "type": link.type,
                "date": link.last_seen,
                "count": link.count,
                "verified": link.verified,
                "strength": link.strength,
                "evidence": (
                    f"@{link.target_handle} 的公开{'关注' if link.type == 'follow' else link.type}记录"
                    if link.target_handle else None
                ),
            })
    return signals


@router.get("/roots")
def list_roots(
    domainGroup: str = Query(default="ai"),
    session: Session = Depends(db),
    client: Client = Depends(current_client),
):
    """Root 库，按领域分套、按圈层分组。

    不同领域的客户看到不同的人。做加密的客户开屏是一列 AI 研究员，既没用也说明
    系统没在听他说话。
    """
    rows = session.scalars(
        select(BDCandidate).where(
            BDCandidate.object_kind == "target_person",
            BDCandidate.domain_group == domainGroup,
        )
    ).all()

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row.target_group or "其他"].append({
            "id": row.handle,
            "name": row.display_name or row.handle,
            "handle": f"@{row.handle}",
            # 来源名单的描述是**别人的说法**，原样透出并标注，不当成 Mango 的判断。
            "note": row.source_notes_raw,
            "noteStatus": row.source_claims_status,
        })
    return [
        {
            "id": _slug(label),
            "label": label,
            "value": len(people),
            "people": people,
        }
        for label, people in grouped.items()
    ]


def _slug(text: str) -> str:
    import re as _re

    return _re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "group"


def _prefs_from(brief: Brief) -> dict:
    def csv(value: str | None) -> list[str]:
        return [p.strip() for p in (value or "").split(",") if p.strip()]

    return {
        "domains": csv(brief.verticals),
        "markets": csv(brief.target_markets),
        "languages": csv(brief.content_languages),
        "audiences": csv(brief.target_audiences),
        "platforms": csv(brief.platforms),
    }


# =============================================================================
# 客户决策 —— 表达兴趣、加入名单、Approve / Reject / Question
# =============================================================================
#
# 这一段闭合的是之前断掉的那条线：客户能看到新发现的人，却**没法在同一个流程里
# 说「我要这个」**。而「客户的选择驱动 BD 优先级」正是这个产品对 Mango 自己的
# 价值所在 —— 没有它，发现出来的人只是一份好看的名单。
#
# ``interest`` 和 ``approve`` 是两个动作，不是一个：
#
# * ``approve``  「这个人我要了」—— 前提是价格和档期能确认。
# * ``interest`` 「这个人我感兴趣，你去谈」—— 新发现的对象**没有价格**，逼客户
#   在这种状态下做承诺是不合理的，而那恰恰是新发现对象的常态。
#
# 两者去向也不同：interest 直接变成 BD 的建联优先级。


class FeedbackIn(BaseModel):
    """客户对一个候选的动作。``candidateId`` 用卡片上的 ``id``（``x:<rest_id>``）。"""

    candidateId: str
    action: str
    reasonCode: str | None = None
    note: str | None = None
    actor: str | None = None


def _resolve_account(session: Session, candidate_id: str) -> XAccount:
    """卡片 id -> ``XAccount``。卡片上发出去的是什么，这里就认什么。"""
    rest_id = candidate_id.split(":", 1)[1] if ":" in candidate_id else candidate_id
    account = session.scalar(select(XAccount).where(XAccount.rest_id == rest_id))
    if account is None:
        account = session.scalar(
            select(XAccount).where(func.lower(XAccount.handle) == rest_id.lower())
        )
    if account is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    return account


def _supply_record(session: Session, account: XAccount) -> Creator:
    """把一个被客户选中的发现对象升级成**可复用的供给记录**。

    这是那条闭环的关键一步，也是为什么这里可以建 ``Creator``：客户表达兴趣就是
    「Mango 去建立这段关系」的触发点，而这段关系一旦建立就应该沉淀下来，
    下一个客户能直接用。规格原文：*后续取得联系方式、合作意愿和报价时，更新
    同一记录，沉淀为可复用资源*。

    已经有记录的**一律复用**，绝不新建第二条 —— 一个账号只有一条身份记录。
    """
    if account.creator_id:
        creator = session.get(Creator, account.creator_id)
        if creator is not None:
            return creator

    from .bd_api import _next_local_id
    from .models import SocialAccount

    handle = account.handle or account.rest_id
    existing = session.scalar(
        select(Creator).where(func.lower(Creator.primary_handle) == handle.lower())
    )
    if existing is not None:
        account.creator_id = existing.id
        return existing

    # 把已经判出来的东西带过去。不带的话，**升级本身会让这个人变模糊**：
    # 卡片上原本有领域、有受众、有角色，一进名单全没了，因为新建的 Creator
    # 是空的。客户会看到同一个人在两个页面上不一样 —— 那是最伤信任的一种 bug。
    candidate_row = session.scalar(
        select(BDCandidate).where(
            BDCandidate.platform == "X", func.lower(BDCandidate.handle) == handle.lower()
        )
    )
    view = _view_for(account, None)
    verticals = (
        candidate_row.verticals_suggested if candidate_row and candidate_row.verticals_suggested
        else (",".join(view.verticals) or None)
    )
    kind = candidate_row.effective_object_kind if candidate_row else "unknown"

    creator = Creator(
        id=_next_local_id(session, Creator),
        display_name=account.display_name or handle,
        primary_handle=handle,
        verticals=verticals,
        market_region=view.market_region,
        market_region_basis=view.market_region_basis,
        audience_types=",".join(view.audience_types) or None,
        audience_types_basis=view.audience_basis,
        audience_types_evidence=view.audience_evidence,
        creator_class="Media / Community Account" if kind == "media_channel" else "Unknown",
        creator_tier="media" if kind == "media_channel" else "unknown",
        source_system="signal_map_bd_intake",
        source_ref=f"client_interest:{account.rest_id}",
    )
    session.add(creator)
    session.flush()
    session.add(SocialAccount(
        id=_next_local_id(session, SocialAccount),
        creator_id=creator.id, platform="X", handle=handle,
        profile_url=f"https://x.com/{handle}", is_primary=True,
        platform_uid=account.rest_id, bio=account.bio,
        followers=account.followers, avatar_url=account.avatar_url,
        median_views=account.median_views,
        metrics_observed_at=account.posts_observed_at,
    ))
    account.creator_id = creator.id
    return creator


#: 动作 -> 内部工单。``interest`` 的工单类型单独一个，因为它的完成标准不同：
#: 询价的产出是一个数字，建联的产出是「联系上了没有」。
_TASK_FOR_ACTION = {
    "interest": ("client_interest", "客户表达兴趣，优先建联并取得报价"),
    "approve": ("price_inquiry", "客户已确认，落实报价与档期"),
    "question": ("client_question", "客户提出问题，需回复"),
}


@router.post("/projects/{project_id}/feedback", status_code=201)
def submit_feedback(
    project_id: int,
    payload: FeedbackIn,
    session: Session = Depends(db),
    client: Client = Depends(current_client),
):
    """记录客户动作，并把它变成 Mango 这边的真实工作。

    ``reject`` **只影响这个项目**，不会让这个人在供给库里降级 —— 一个客户觉得
    不合适，不等于下一个客户也不合适。
    """
    brief = _project(session, project_id, client)
    if payload.action not in FEEDBACK_ACTIONS:
        raise HTTPException(status_code=422, detail=f"action must be in {FEEDBACK_ACTIONS}")

    account = _resolve_account(session, payload.candidateId)
    # reject / question 不需要建供给记录 —— 客户说不要，不该因此在库里多一条人。
    creator = (
        _supply_record(session, account)
        if payload.action in ("interest", "approve", "shortlist")
        else (session.get(Creator, account.creator_id) if account.creator_id else None)
    )
    if creator is None:
        raise HTTPException(
            status_code=422,
            detail="this action needs a supply record; express interest first",
        )

    event = FeedbackEvent(
        brief_id=brief.id, creator_id=creator.id, action=payload.action,
        reason_code=payload.reasonCode, reason_text=payload.note,
        actor=payload.actor, actor_side="client",
    )
    session.add(event)
    session.flush()

    # 候选名单：shortlist / approve / interest 都进，reject 移出。
    if payload.action in ("interest", "approve", "shortlist"):
        item = session.scalar(
            select(CandidateItem).where(
                CandidateItem.brief_id == brief.id,
                CandidateItem.creator_id == creator.id,
            )
        )
        if item is None:
            session.add(CandidateItem(
                brief_id=brief.id, creator_id=creator.id, client_note=payload.note,
            ))
        else:
            item.removed_at = None
    elif payload.action in ("reject", "unshortlist"):
        item = session.scalar(
            select(CandidateItem).where(
                CandidateItem.brief_id == brief.id,
                CandidateItem.creator_id == creator.id,
                CandidateItem.removed_at.is_(None),
            )
        )
        if item is not None:
            item.removed_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)

    task_id = None
    spec = _TASK_FOR_ACTION.get(payload.action)
    if spec is not None:
        kind, title = spec
        # 同一个人同一种工作不重复开单 —— 客户多点两次不该让队列变长。
        existing = session.scalar(
            select(InternalTask).where(
                InternalTask.brief_id == brief.id,
                InternalTask.creator_id == creator.id,
                InternalTask.kind == kind,
                InternalTask.status == "open",
            )
        )
        if existing is None:
            task = InternalTask(
                kind=kind, brief_id=brief.id, creator_id=creator.id,
                feedback_event_id=event.id,
                title=f"{creator.display_name}：{title}",
                detail=payload.note,
            )
            session.add(task)
            session.flush()
            task_id = task.id
        else:
            task_id = existing.id

    session.commit()
    return {
        "candidateId": payload.candidateId,
        "action": payload.action,
        "recorded": True,
        # 让客户看到他的动作确实产生了后果，而不是消失在一个按钮里。
        "effect": _effect_text(payload.action),
        "internalTaskRaised": task_id is not None,
    }


def _effect_text(action: str) -> str:
    return {
        "interest": "已转为 Mango 的优先建联任务，我们会去接洽并取得报价",
        "approve": "已进入你的名单，Mango 将落实报价与档期",
        "shortlist": "已加入你的名单",
        "unshortlist": "已从你的名单移出",
        "reject": "已从本项目移出（仅影响本项目）",
        "question": "已转为 Mango 的内部问题，我们会回复",
    }.get(action, "已记录")


@router.get("/projects/{project_id}/shortlist")
def get_shortlist(
    project_id: int,
    session: Session = Depends(db),
    client: Client = Depends(current_client),
):
    """客户已选的名单，附预算两段汇总。"""
    brief = _project(session, project_id, client)
    items = session.scalars(
        select(CandidateItem)
        .where(CandidateItem.brief_id == brief.id, CandidateItem.removed_at.is_(None))
        .options(
            selectinload(CandidateItem.creator).selectinload(Creator.quotes),
            selectinload(CandidateItem.creator).selectinload(Creator.accounts),
        )
    ).all()

    rows, picks = [], []
    for item in items:
        creator = item.creator
        account = session.scalar(
            select(XAccount).where(XAccount.creator_id == creator.id)
        )
        candidate = ProjectCandidate(
            source="priced" if any(q.amount_usd for q in creator.quotes) else "discovered",
            account=account or XAccount(rest_id=str(creator.id), handle=creator.primary_handle),
            creator=creator,
            view=_view_for(account, creator) if account else None,
            quotes=[q for q in creator.quotes if q.amount_usd is not None],
        )
        picks.append(candidate)
        role, basis = creator_role(candidate)
        rows.append({
            "candidateId": f"x:{account.rest_id}" if account else f"creator:{creator.id}",
            "name": creator.display_name,
            # BD 时期有些对象没有 handle，只有名字。显示 handle 会出现一排
            # 「@?」，那是数据缺失被当成字符串打出来了。
            "handle": creator.primary_handle or (account.handle if account else None),
            "source": candidate.source,
            "role": role,
            "roleBasis": basis,
            "tier": candidate.tier,
            "price": TIER_LABELS[candidate.tier] if candidate.tier else None,
            "quoteStatus": _quote_status(candidate),
            "bizState": candidate.biz_state[0],
            "addedAt": item.added_at.isoformat() if item.added_at else None,
            "clientNote": item.client_note,
        })
    return {
        "projectId": brief.id,
        "count": len(rows),
        "items": rows,
        "budget": budget_summary(picks),
        "coverage": _role_coverage(rows),
    }


def _role_coverage(rows: list[dict]) -> dict:
    """名单的角色构成，以及少了什么。

    这是「阵容」和「一堆人」的区别：八个专业验证者不是一个组合，是同一次下注
    重复了八遍。措辞按客户界面的约束 —— 说「下一步可拓展」，不说「缺口」。
    """
    have: dict[str, int] = {}
    for row in rows:
        have[row["role"]] = have.get(row["role"], 0) + 1
    # 一个能讲清楚的组合至少要有人解释、有人背书、有人扩散。
    core = ("专业验证者", "解释者", "回声节点")
    missing = [role for role in core if not have.get(role)]
    return {
        "byRole": have,
        "expandable": missing,
        "note": (
            "当前名单已覆盖 " + "、".join(have) if have else "名单为空"
        ) + (
            f"；下一步可拓展：{'、'.join(missing)}" if missing and have else ""
        ),
    }
