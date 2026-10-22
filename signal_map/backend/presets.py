"""目标圈层快速选择（preset）与跨圈层综合排名。

两件事，都长在原有的「领域 → 圈层 → 目标人物」结构上，不另立一套数据：

1. **快速选择**。预设定义在 ``config/signal_map_circles.json`` 的 ``presets``：
   一个预设是同一领域内的一组圈层（海外一线 VC、行业领袖、交易所生态、
   券商 / 零售交易平台），成员就是这些圈层里的目标人物。客户面上它等于一次
   把这几个圈层「设为本轮重点」；内部面与 ``/api/personas`` 仍可按预设展开
   成员、再逐个勾掉。旧的预设 id 作为 ``aliases`` 继续被接受。
2. **聚合排名**。对每个候选账号，数「所选目标人物里有几位关注它」—— 总数、
   按预设（或圈层）分开的数，以及一个综合分。

综合分为什么要按预设 / 圈层归一
------------------------------
直接数关注人数，人多的圈子会压倒一切：海外一线 VC 有十几人、交易所生态里某一
家只有 4 人，一个只被 VC 圈 5 人关注的账号会排在被另一圈 4 人**全部**关注的
账号前面。所以每个圈子先算「本圈已采集目标里关注它的比例」（0–1），综合分是
**各圈比例的平均**。一个账号同时进入多个圈子才能拿高分，这正是「跨圈层」的意思。

分母只用**已采集关注网络**的目标人物。没采过的人不是「没关注」，是「没看过」——
把他们算进分母会把整个圈子的比例压低，而那是采集缺口，不是关系事实。一个圈子
一个都没采过时，它不参与平均，并在 ``collected == 0`` 里如实报出。

口径
----
* 一条关注边只证明关注存在（CLAUDE.md §2 路径 3）。综合分是**关注信号的覆盖
  度**，不是曝光概率，不是背书。
* 只用 ``follow_edges`` 里 ``disappeared_at IS NULL`` 的边，与发现池同一口径。
* 目标人物本身不进排名 —— 他们是要影响的人，不是要买的人。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import v3_catalog as catalog
from .observation_models import FollowEdge, XAccount

MEMBER_SOURCES = catalog.TARGET_SOURCES
MEMBER_SOURCE_LABELS_ZH = catalog.TARGET_SOURCE_LABELS_ZH

#: 客户手动加的、不属于任何预设的目标人物，归到这个虚拟预设。
CUSTOM_PERSONA_ID = "custom"
CUSTOM_PERSONA_LABEL = "自选目标人物"


@dataclass(frozen=True)
class PersonaMember:
    handle: str
    name: str
    role: str
    source: str
    needs_review: bool
    basis: str | None = None
    circle: str | None = None


@dataclass(frozen=True)
class Persona:
    """一个预设展开后的样子：预设本身 + 它那几个圈层里的目标人物。"""

    id: str
    group: str
    label: str
    desc: str
    why: str
    circles: tuple[str, ...]
    aliases: tuple[str, ...]
    members: tuple[PersonaMember, ...]


def _expand_preset(p: catalog.PresetDef) -> Persona:
    return Persona(
        id=p.id, group=p.group, label=p.label, desc=p.desc, why=p.why,
        circles=p.circles, aliases=p.aliases,
        members=tuple(
            PersonaMember(
                handle=t.handle.lstrip("@"), name=t.name, role=t.role, source=t.source,
                needs_review=t.needs_review, basis=t.basis, circle=t.circle,
            )
            for t in catalog.preset_members(p)
        ),
    )


def load_personas() -> tuple[Persona, ...]:
    return tuple(_expand_preset(p) for p in catalog.load_presets())


def persona_by_id(persona_id: str) -> Persona | None:
    """当前 id 或旧 id 都能查到；返回的 ``id`` 永远是当前 id。"""
    preset = catalog.preset_by_id(persona_id)
    return _expand_preset(preset) if preset else None


@dataclass
class Selection:
    """客户的最终选择：哪些预设（当前 id）、每位目标人物归哪几个预设。"""

    personas: list[str]
    #: handle(lower) -> 原始大小写 handle
    handles: dict[str, str]
    #: handle(lower) -> 所属预设 id 列表（可多个）
    membership: dict[str, list[str]]
    unknown_personas: list[str] = field(default_factory=list)

    @property
    def target_handles(self) -> list[str]:
        return list(self.handles.values())


def expand(
    personas: Iterable[str] = (),
    target_handles: Iterable[str] = (),
    exclude_handles: Iterable[str] = (),
) -> Selection:
    """预设展开 + 手动加的 handle 合并 − 手动勾掉的 handle。

    顺序稳定：先按预设顺序展开成员，再追加手动 handle。同一个人在多个预设里
    出现时只算一位目标人物，但几个预设都记上 —— 综合排名需要知道他同时代表
    哪几个圈子。旧 id 解析到当前 id，两个旧 id 指向同一个预设时只展开一次。
    """
    excluded = {h.lstrip("@").lower() for h in exclude_handles if h}
    handles: dict[str, str] = {}
    membership: dict[str, list[str]] = {}
    chosen: list[str] = []
    unknown: list[str] = []
    for requested in dict.fromkeys(personas):
        persona = persona_by_id(requested)
        if persona is None:
            unknown.append(requested)
            continue
        pid = persona.id
        if pid in chosen:
            continue
        chosen.append(pid)
        for m in persona.members:
            key = m.handle.lower()
            if key in excluded:
                continue
            handles.setdefault(key, m.handle)
            membership.setdefault(key, [])
            if pid not in membership[key]:
                membership[key].append(pid)
    for h in target_handles:
        clean = (h or "").strip().lstrip("@")
        key = clean.lower()
        if not clean or key in excluded:
            continue
        if key not in handles:
            handles[key] = clean
        if not membership.get(key):
            membership[key] = [CUSTOM_PERSONA_ID]
    if any(v == [CUSTOM_PERSONA_ID] for v in membership.values()):
        chosen.append(CUSTOM_PERSONA_ID)
    return Selection(personas=chosen, handles=handles, membership=membership,
                     unknown_personas=unknown)


def persona_label(pid: str) -> str:
    if pid == CUSTOM_PERSONA_ID:
        return CUSTOM_PERSONA_LABEL
    p = persona_by_id(pid)
    return p.label if p else pid


def member_status(session: Session, handles: Iterable[str]) -> dict[str, tuple[XAccount | None, int]]:
    """handle(lower) -> (观察层账号, 已采集的关注边数)。0 条边 = 还没采过。"""
    wanted = {h.lower() for h in handles}
    if not wanted:
        return {}
    accounts = session.scalars(
        select(XAccount).where(func.lower(XAccount.handle).in_(wanted))
    ).all()
    by_id = {a.id: a for a in accounts}
    counts = dict(
        session.execute(
            select(FollowEdge.source_id, func.count())
            .where(FollowEdge.source_id.in_(list(by_id)), FollowEdge.disappeared_at.is_(None))
            .group_by(FollowEdge.source_id)
        ).all()
    ) if by_id else {}
    out: dict[str, tuple[XAccount | None, int]] = {h: (None, 0) for h in wanted}
    for a in accounts:
        out[(a.handle or "").lower()] = (a, counts.get(a.id, 0))
    return out


# =============================================================================
# 综合排名
# =============================================================================


@dataclass
class PersonaReach:
    persona: str
    label: str
    followed_by: list[str]
    collected: int

    @property
    def share(self) -> float:
        return len(self.followed_by) / self.collected if self.collected else 0.0

    def as_dict(self) -> dict:
        return {
            "persona": self.persona,
            "label": self.label,
            "count": len(self.followed_by),
            "collected": self.collected,
            "share": round(self.share, 4),
            "followed_by": list(self.followed_by),
        }


@dataclass
class RankedAccount:
    account: XAccount
    followed_by: list[str]
    reach: list[PersonaReach]
    score: float

    @property
    def circles_reached(self) -> int:
        return sum(1 for r in self.reach if r.followed_by)


@dataclass
class AggregateResult:
    selection: Selection
    #: 预设 id -> (目标人物数, 已采集数)
    coverage: dict[str, tuple[int, int]]
    #: 选中但观察层里根本没有的 handle
    unresolved: list[str]
    #: 已收录但还没采关注网络的 handle
    uncollected: list[str]
    items: list[RankedAccount]
    pool_size: int


def aggregate(
    session: Session,
    selection: Selection,
    *,
    min_targets: int = 1,
    limit: int = 100,
    exclude_handles: Iterable[str] = (),
    keep: Callable[[XAccount], bool] | None = None,
) -> AggregateResult:
    """对所选目标人物关注的账号做跨圈层综合排名。

    ``keep(account) -> bool`` 由调用方决定池子边界：内部面看全部，客户面只看
    已判定可投放的账号。排名逻辑本身与池子边界无关。
    """
    status = member_status(session, selection.handles.keys())
    unresolved = sorted(selection.handles[h] for h, (a, _) in status.items() if a is None)
    uncollected = sorted(
        selection.handles[h] for h, (a, n) in status.items() if a is not None and n == 0
    )
    collected = {h: a for h, (a, n) in status.items() if a is not None and n > 0}

    coverage: dict[str, tuple[int, int]] = {}
    for pid in selection.personas:
        members = [h for h, ps in selection.membership.items() if pid in ps]
        coverage[pid] = (len(members), sum(1 for h in members if h in collected))
    active = [pid for pid in selection.personas if coverage[pid][1] > 0]

    source_handle = {a.id: selection.handles[h] for h, a in collected.items()}
    source_personas = {a.id: selection.membership.get(h, []) for h, a in collected.items()}
    if not source_handle:
        return AggregateResult(selection, coverage, unresolved, uncollected, [], 0)

    edges = session.execute(
        select(FollowEdge.target_id, FollowEdge.source_id).where(
            FollowEdge.source_id.in_(list(source_handle)),
            FollowEdge.disappeared_at.is_(None),
        )
    ).all()
    followers_of: dict[int, list[int]] = {}
    for tgt, src in edges:
        followers_of.setdefault(tgt, []).append(src)

    eligible = {t: s for t, s in followers_of.items() if len(set(s)) >= max(min_targets, 1)}
    accounts = {
        a.id: a
        for a in session.scalars(select(XAccount).where(XAccount.id.in_(list(eligible)))).all()
    } if eligible else {}

    # 目标人物本身（含未采集的）与人工排除名单不进排名。
    blocked = {h.lower() for h in selection.handles} | {
        h.lstrip("@").lower() for h in exclude_handles
    }

    ranked: list[RankedAccount] = []
    for tid, srcs in eligible.items():
        account = accounts.get(tid)
        if account is None or (account.handle or "").lower() in blocked:
            continue
        if keep is not None and not keep(account):
            continue
        srcs = sorted(set(srcs), key=lambda s: source_handle[s].lower())
        reach = []
        for pid in selection.personas:
            by = [source_handle[s] for s in srcs if pid in source_personas[s]]
            reach.append(PersonaReach(pid, persona_label(pid), by, coverage[pid][1]))
        score = (
            sum(r.share for r in reach if r.persona in active) / len(active) if active else 0.0
        )
        ranked.append(RankedAccount(account, [source_handle[s] for s in srcs], reach, score))

    ranked.sort(
        key=lambda r: (
            -round(r.score, 6),
            -r.circles_reached,
            -len(r.followed_by),
            -(r.account.followers or 0),
            (r.account.handle or "").lower(),
        )
    )
    return AggregateResult(
        selection, coverage, unresolved, uncollected,
        ranked[: max(limit, 0)], pool_size=len(ranked),
    )


def coverage_dicts(result: AggregateResult) -> list[dict]:
    return [
        {
            "id": pid,
            "label": persona_label(pid),
            "targets": result.coverage[pid][0],
            "collected": result.coverage[pid][1],
            # 0 已采集 = 这个圈子在综合分里不参与平均，不是「没人关注」。
            "in_score": result.coverage[pid][1] > 0,
        }
        for pid in result.selection.personas
    ]


AGGREGATE_NOTE_ZH = (
    "综合分 = 各预设内「已采集目标人物中关注该账号的比例」的平均值，"
    "每个预设权重相同，避免人多的圈子压倒人少的圈子。这是关注信号的覆盖度，"
    "不是曝光概率，也不代表背书。未采集关注网络的目标人物不计入分母。"
)


# =============================================================================
# 推荐名单里的同一套口径：按圈层归一
# =============================================================================

COVERAGE_NOTE_ZH = (
    "跨圈层覆盖 = 各圈层内「已采集目标人物中关注该账号的比例」的平均值，"
    "每个圈层权重相同，避免人多的圈层压倒人少的圈层。这是关注信号的覆盖度，"
    "不是曝光概率，也不代表背书。未采集关注网络的目标人物不计入分母。"
)


@dataclass
class CircleReach:
    circle: str
    count: int
    collected: int

    @property
    def share(self) -> float:
        return self.count / self.collected if self.collected else 0.0


def circle_reach(
    target_ids: Iterable[str],
    circle_of_target: dict[str, str],
    collected_targets: set[str],
    circles: Iterable[str],
) -> tuple[list[CircleReach], float]:
    """候选在每个圈层的覆盖，以及它们的平均（综合分）。

    与 ``aggregate`` 同一规则，只是「圈子」从预设换成了领域里的圈层。分子与
    分母都只数**已采集关注网络**的目标人物：互动信号可能来自一位还没采关注
    列表的人，把他算进分子却不算进分母，比例会超过 1。没有已采集目标人物的
    圈层照常列出，但不进平均。
    """
    seen = set(target_ids) & collected_targets
    reach = []
    for cid in circles:
        n = sum(1 for t in seen if circle_of_target.get(t) == cid)
        total = sum(1 for t in collected_targets if circle_of_target.get(t) == cid)
        reach.append(CircleReach(cid, n, total))
    active = [r for r in reach if r.collected]
    score = sum(r.share for r in active) / len(active) if active else 0.0
    return reach, score
