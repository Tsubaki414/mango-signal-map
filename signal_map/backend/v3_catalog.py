"""Signal Map v3 目录层：领域 / 圈层 / 目标人物 / 候选池。

v3 的方法是 **target-backed discovery**：以目标人物（root）为起点，取其公开
关注网络，反推能把内容送进这些人视野的创作者。所以候选池 **不等于** 已有报价
库 —— 池子里相当一部分人 Mango 从未联系过。这是产品最值钱的部分，后端必须
两类都返回。

方向纪律（读错一次就全盘皆错）
------------------------------
边的语义永远是 **目标人物 关注 → 候选**，不是反过来。两个数据源方向相同：

* ``attention_signals``：``source_node``（目标账号）→ ``source_creator_id``
  （我们的创作者）。``source_creator_id`` 名字里是 source、语义上是**被关注
  方**，这是观察层沿用下来的命名。全表 5,060 行无一例外，见
  ``bd_screening`` 的同名说明。这张表只覆盖**已经是 Mango 创作者**的被关注方，
  因为该列是指向 ``creators`` 的外键 —— 所以只靠它，``discovered`` 恒为 0。
* ``follow_edges``：原始关注边，覆盖**还没有供给记录**的账号。发现管线的主线
  正是这批人。

候选池不是"目标人物关注的所有人"
--------------------------------
一个目标人物动辄关注四万个账号，全量取出来不是候选池，是通讯录。真正的信号是
**被同一领域多位目标人物共同关注**。门槛随该领域已收集到关注边的目标人物数量
浮动（见 ``cofollow_threshold``）：目标人物少的领域用低门槛，否则整个领域直接
归零；目标人物多的领域用高门槛，否则每个人都连所有圈层，客户标记重点看不出
任何变化 —— 这正是 v3 验收标准里「命中率超过 60% 说明阈值太松」要防的事。

未核验就是未核验
----------------
``x_accounts.root_review_status`` 当前 282,877 行全部 ``pending``，accepted 与
rejected 都是 0：**没有任何一个目标人物经过人工确认**。因此本模块产出的每条边
一律 ``verified=False``，纯关注边强度一律 ``weak``。客户端可以显示它，但不得
表述为已核实 —— 这既是 ``AGENTS.md`` 的证据契约，也是 v3 README 第七节的用词
约束。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .bd_models import NON_BUYABLE_OBJECT_KINDS, BDCandidate
from .models import AttentionSignal, Creator, Quote, SocialAccount
from .observation_models import FollowEdge, XAccount

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "signal_map_circles.json"

#: 纯关注边只能是 weak。互动类信号才配得上更高的强度 —— 见 ``EDGE_STRENGTH``。
FOLLOW_STRENGTH = "weak"

#: 买不到内容位的身份，复用 ``bd_models`` 的既有词表 —— 不在这里另立一套，
#: 否则两处会各自漂移。它包含 ``target_person``，这一条不能漏：Demis Hassabis、
#: Greg Brockman、cz_binance、Ray Dalio 在库里都是人工标定的 ``target_person``，
#: 只排除 LLM 那两类的话他们会原样出现在客户的可投放名单里。
#:
#: 这一层不是可选的优化。只按共同关注取池子，AI 领域前列全是这些人 —— 共同
#: 关注回答的是「重要的人都关注谁」，答案永远是「其他重要的人」。
NON_BUYABLE_KINDS = NON_BUYABLE_OBJECT_KINDS

#: 能买到内容位的两类。客户面用**白名单**：只有判定为这两类的账号才进名单。
BUYABLE_KINDS = frozenset({"kol", "media_channel"})


#: attention_signals.signal_type → (v3 edge type, strength)
#: v3 的 Edge.type 只有四种，观察层的类型要映射过去，不能直接透传。
EDGE_STRENGTH: dict[str, tuple[str, str]] = {
    "follow": ("cofollow", "weak"),
    "reply": ("reply", "medium"),
    "quote": ("quote", "medium"),
    "repost": ("quote", "medium"),
    "mention": ("reply", "weak"),
    "co_appearance": ("co_appear", "medium"),
    "co_mention": ("co_appear", "weak"),
}


@dataclass(frozen=True)
class Circle:
    id: str
    label: str
    value: str
    who: str
    pick: str


@dataclass(frozen=True)
class GroupDef:
    id: str
    label: str
    desc: str
    domains: tuple[str, ...]
    circles: tuple[Circle, ...]


@dataclass(frozen=True)
class TargetDef:
    id: str
    name: str
    handle: str
    role: str
    group: str
    circle: str
    why: str
    markets: tuple[str, ...]
    audiences: tuple[str, ...]


@dataclass
class ResolvedTarget:
    """配置里的目标人物 + 它在观察层的实际状态。"""

    definition: TargetDef
    account: XAccount | None = None
    #: 该目标人物已收集到的关注边数量。0 表示还没采过，不是"没有关注任何人"。
    follow_edges: int = 0

    @property
    def collected(self) -> bool:
        return self.account is not None and self.follow_edges > 0

    @property
    def review_status(self) -> str:
        return getattr(self.account, "root_review_status", None) or "pending"


@dataclass
class Edge:
    target_id: str
    type: str
    count: int
    last_seen: str | None
    verified: bool
    strength: str
    evidence: str | None = None


@dataclass
class PoolMember:
    """候选池成员。``creator_id`` 有值即 priced 侧，否则是 discovered 侧。"""

    account_id: int | None
    rest_id: str | None
    handle: str | None
    creator_id: int | None
    edges: list[Edge] = field(default_factory=list)

    @property
    def source(self) -> str:
        return "priced" if self.creator_id is not None else "discovered"


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> tuple[GroupDef, ...]:
    raw = json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))
    circles_by_group = {
        gid: tuple(
            Circle(c["id"], c["label"], c["value"], c["who"], c["pick"]) for c in items
        )
        for gid, items in raw["circles"].items()
    }
    return tuple(
        GroupDef(
            id=g["id"],
            label=g["label"],
            desc=g["desc"],
            domains=tuple(g["domains"]),
            circles=circles_by_group.get(g["id"], ()),
        )
        for g in raw["groups"]
    )


@lru_cache(maxsize=1)
def load_targets(path: str | None = None) -> tuple[TargetDef, ...]:
    raw = json.loads(Path(path or CONFIG_PATH).read_text(encoding="utf-8"))
    return tuple(
        TargetDef(
            id=t["id"],
            name=t["name"],
            handle=t["handle"],
            role=t.get("role", ""),
            group=t["group"],
            circle=t["circle"],
            why=t.get("why", ""),
            markets=tuple(t.get("markets", ())),
            audiences=tuple(t.get("audiences", ())),
        )
        for t in raw["targets"]
    )


def group_by_id(group_id: str) -> GroupDef | None:
    return next((g for g in load_config() if g.id == group_id), None)


def targets_for(group_id: str) -> tuple[TargetDef, ...]:
    return tuple(t for t in load_targets() if t.group == group_id)


# --------------------------------------------------------------------------
# 解析目标人物
# --------------------------------------------------------------------------


def resolve_targets(session: Session, group_id: str) -> list[ResolvedTarget]:
    """把配置里的 handle 解析成 x_accounts 记录，并统计已采集的关注边。

    解析不到的目标人物**照样返回**，``account=None`` —— 客户端显示「资料待补充」
    而不是让它从圈层里消失。一个圈层少了代表人物，客户看到的就是一个空圈层，
    那比显示一个未解析的名字更容易被误读成"这个圈层没人"。
    """
    defs = targets_for(group_id)
    if not defs:
        return []

    wanted = {t.handle.lower(): t for t in defs}
    accounts = session.scalars(
        select(XAccount).where(func.lower(XAccount.handle).in_(list(wanted)))
    ).all()
    by_handle = {(a.handle or "").lower(): a for a in accounts}

    edge_counts: dict[int, int] = {}
    ids = [a.id for a in accounts]
    if ids:
        rows = session.execute(
            select(FollowEdge.source_id, func.count())
            .where(FollowEdge.source_id.in_(ids), FollowEdge.disappeared_at.is_(None))
            .group_by(FollowEdge.source_id)
        ).all()
        edge_counts = {sid: n for sid, n in rows}

    resolved = []
    for t in defs:
        acc = by_handle.get(t.handle.lower())
        resolved.append(
            ResolvedTarget(
                definition=t,
                account=acc,
                follow_edges=edge_counts.get(acc.id, 0) if acc else 0,
            )
        )
    return resolved


def cofollow_threshold(collected_targets: int) -> int:
    """一个账号要被多少位目标人物共同关注，才算这个领域的候选。

    门槛只负责一件事：**这个连接够不够强**。它一度还兼职挡名人（Hinton、
    Ilya 那一批），所以被调到了 ≥5；身份分类上线后那个职责归 ``BUYABLE_KINDS``，
    门槛就该退回本职，否则会连真正可投放的人一起挡掉 —— 实测 AI 在 ≥5 下只剩
    53 人，而库里可投放且未建联的账号有 525 个。

    实测（叠加可投放过滤后）：
    AI（16 位目标人物）≥2 → 253 · ≥3 → 164 · ≥4 → 91 · ≥5 → 53
    加密 / 金融（各 4 位）≥2 → 54 / 69 · ≥3 → 10 / 19
    """
    if collected_targets <= 6:
        return 2
    return 3


# --------------------------------------------------------------------------
# 候选池
# --------------------------------------------------------------------------


def _priced_creator_ids(session: Session) -> set[int]:
    """有真实报价记录的创作者。没有报价 = 无法进预算，不能进候选池。"""
    return set(
        session.scalars(
            select(Quote.creator_id).where(Quote.internal_cost_usd.is_not(None))
        ).all()
    )


def priced_pool(session: Session, targets: list[ResolvedTarget]) -> list[PoolMember]:
    """priced 侧：目标人物关注了的、且我们已有报价的创作者。

    读 ``attention_signals``，注意 ``source_creator_id`` 指的是**被关注方**。
    """
    nodes = [t.account.rest_id for t in targets if t.account and t.account.rest_id]
    if not nodes:
        return []

    rest_to_target = {
        t.account.rest_id: t.definition.id for t in targets if t.account and t.account.rest_id
    }
    priced = _priced_creator_ids(session)

    signals = session.scalars(
        select(AttentionSignal).where(
            AttentionSignal.source_node.in_(nodes),
            AttentionSignal.source_creator_id.is_not(None),
        )
    ).all()

    members: dict[int, PoolMember] = {}
    for sig in signals:
        cid = sig.source_creator_id
        if cid not in priced:
            continue
        target_id = rest_to_target.get(sig.source_node)
        if target_id is None:
            continue
        edge_type, strength = EDGE_STRENGTH.get(sig.signal_type, ("cofollow", "weak"))
        member = members.setdefault(
            cid, PoolMember(account_id=None, rest_id=None, handle=None, creator_id=cid)
        )
        member.edges.append(
            Edge(
                target_id=target_id,
                type=edge_type,
                count=sig.observation_count or 1,
                last_seen=sig.occurred_at.isoformat() if sig.occurred_at else None,
                # 全库 human_confirmed 为 0；这里如实反映，不写死 False。
                verified=bool(sig.human_confirmed),
                strength=strength,
                evidence=sig.raw_evidence,
            )
        )
    return list(members.values())


def discovered_pool(
    session: Session, targets: list[ResolvedTarget], *, limit: int = 400
) -> list[PoolMember]:
    """discovered 侧：被多位目标人物共同关注、但**还不是** Mango 创作者的账号。

    这是「我们还会为你去找并建联新的人」的数据来源。门槛见 ``cofollow_threshold``。
    """
    collected = [t for t in targets if t.collected]
    if not collected:
        return []

    source_ids = [t.account.id for t in collected]
    id_to_target = {t.account.id: t.definition.id for t in collected}
    threshold = cofollow_threshold(len(collected))

    # 已经是我们创作者的账号走 priced 侧，这里排除，避免同一个人出现两次。
    known_uids = set(
        session.scalars(
            select(SocialAccount.platform_uid).where(SocialAccount.platform_uid.is_not(None))
        ).all()
    )

    # 目标人物本身必须排除，**跨领域全部排除**。
    #
    # 共同关注这个指标选出来的是"很多目标人物都关注的人"，而那恰好就是同一批
    # 目标人物和行业名人：不加这一条，AI 领域的 discovered 前五名是 sama、
    # karpathy、demishassabis —— 而 sama 本身就是 founders 圈层的目标人物。
    # 产品会因此对客户说「我们再去帮你建联 Sam Altman」，既不可执行，也把
    # "目标人物"和"可投放创作者"这两个角色混成了一个。
    excluded_handles = {t.handle.lower() for t in load_targets()}

    # 客户面只放**已判定可投放**的账号，而不是「排除已知买不到的」。
    #
    # 这一条和「缺数据从不淘汰候选」的通则相反，是有意为之，理由和
    # classify_account_roles 的取舍一致：两个方向的代价不对称 —— 错误排除一个
    # 可买的人，损失一次机会；错误放进一个 Geoffrey Hinton，是产品级别的尴尬，
    # 因为产品等于在对客户说「我们去帮你把他签下来」。
    #
    # 用排除法实测过：Paul Graham、Ilya Sutskever、Geoffrey Hinton、Nate Silver
    # 全部出现在客户的可投放名单里 —— 他们只是还没被分类，于是默认通过了。
    # 未判定的账号照常留在内部队列等人确认，只是不进客户名单。
    buyable_handles = {
        (handle or "").lower()
        for handle, kind, suggested in session.execute(
            select(
                BDCandidate.handle,
                BDCandidate.object_kind,
                BDCandidate.object_kind_suggested,
            ).where(BDCandidate.handle.is_not(None))
        ).all()
        if (kind if kind and kind != "unknown" else suggested) in BUYABLE_KINDS
    }

    counted = (
        select(FollowEdge.target_id, func.count(func.distinct(FollowEdge.source_id)).label("n"))
        .where(FollowEdge.source_id.in_(source_ids), FollowEdge.disappeared_at.is_(None))
        .group_by(FollowEdge.target_id)
        .having(func.count(func.distinct(FollowEdge.source_id)) >= threshold)
        .subquery()
    )
    # 可投放过滤必须进 SQL。先按连接数取 limit*3 行、再在 Python 里过滤，
    # 等于让过滤去吃这个窗口：AI 在门槛 ≥5 下真实有 53 个可投放候选，
    # 取前 180 行再过滤只剩 10 个，而且少掉的那些永远没机会出现。
    rows = session.execute(
        select(XAccount, counted.c.n)
        .join(counted, counted.c.target_id == XAccount.id)
        .where(
            XAccount.rest_id.is_not(None),
            func.lower(XAccount.handle).in_(list(buyable_handles)) if buyable_handles else False,
        )
        .order_by(counted.c.n.desc(), XAccount.followers.desc().nullslast())
    ).all()

    kept = []
    for account, _n in rows:
        if account.rest_id in known_uids:
            continue
        # 可投放已在 SQL 里过滤；这里只剩"目标人物本身"的排除。
        if (account.handle or "").lower() in excluded_handles:
            continue
        kept.append(account)
        if len(kept) >= limit:
            break
    if not kept:
        return []

    # 边一次取完再分组。按候选逐个查是 N+1，在 512,921 行的 follow_edges 上
    # 实测把三个领域的取池子拖到两分钟以上 —— 那个延迟直接就是接口延迟。
    edges_by_account: dict[int, list[Edge]] = {}
    edge_rows = session.execute(
        select(
            FollowEdge.target_id,
            FollowEdge.source_id,
            FollowEdge.last_seen_at,
            FollowEdge.times_seen,
        ).where(
            FollowEdge.target_id.in_([a.id for a in kept]),
            FollowEdge.source_id.in_(source_ids),
            FollowEdge.disappeared_at.is_(None),
        )
    ).all()
    for tgt, src, seen, times in edge_rows:
        if src not in id_to_target:
            continue
        edges_by_account.setdefault(tgt, []).append(
            Edge(
                target_id=id_to_target[src],
                type="cofollow",
                count=times or 1,
                last_seen=seen.date().isoformat() if seen else None,
                # 关注边从来没有人工确认过，也不该被推断为已确认。
                verified=False,
                strength=FOLLOW_STRENGTH,
                evidence=None,
            )
        )

    return [
        PoolMember(
            account_id=a.id,
            rest_id=a.rest_id,
            handle=a.handle,
            creator_id=None,
            edges=edges_by_account.get(a.id, []),
        )
        for a in kept
    ]


# --------------------------------------------------------------------------
# 发现候选的画像
# --------------------------------------------------------------------------

#: 汉字/假名/谚文，用来判断内容语言。按 CLAUDE.md 的证据阶梯，**内容语言优先于
#: 账号所在地**：一个写英文的创作者触达的是欧美受众，无论他人在新加坡还是德州。
_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")


@dataclass
class DiscoveredProfile:
    """发现候选的画像。每个值都带 ``basis``，卡片上要能写「依据：简介语言」。

    这里刻意**不判断目标人群**。用正则从简介猜受众被验证过是错的 —— 它匹配的是
    「这个人做什么」，却被当成「谁在看」（CLAUDE.md 里那个 anime-edits 账号被标成
    creators 的例子）。判不出来就是 ``unknown``，由客户端显示「人群待确认」，
    而不是编一个。
    """

    domains: tuple[str, ...] = ()
    domains_basis: str | None = None
    market: str | None = None
    market_basis: str | None = None
    language: str | None = None


def profile_from_bio(bio: str | None) -> DiscoveredProfile:
    """从公开简介推导领域与市场。规则化、可解释、不调模型。

    这是「粗但可筛 胜过 精确但为空」在发现侧的同一套做法：没有画像的候选，
    任何市场或方向筛选都只能整批放行或整批丢弃，两种都不可用。
    """
    from .bd_discovery import verticals_from_bio

    if not bio:
        return DiscoveredProfile()

    verticals, quotes = verticals_from_bio(bio)
    cjk = len(_CJK.findall(bio))
    if cjk >= 4:
        market, language, basis = "greater_china", "zh", "简介语言"
    elif cjk:
        market, language, basis = None, "bi", "简介中英混排"
    else:
        market, language, basis = "europe_america", "en", "简介语言"

    return DiscoveredProfile(
        domains=verticals,
        domains_basis=quotes,
        market=market,
        market_basis=basis,
        language=language,
    )


def creators_by_id(session: Session, ids: list[int]) -> dict[int, Creator]:
    if not ids:
        return {}
    return {
        c.id: c for c in session.scalars(select(Creator).where(Creator.id.in_(ids))).all()
    }
