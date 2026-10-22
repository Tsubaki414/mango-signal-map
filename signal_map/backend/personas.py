"""人群预设（persona）与跨圈层综合排名。

两件事，老板（Dov）提的：

1. **批量选择社工对象**。客户不再一个个找目标人物，而是先选一个或几个人群预设
   （海外 VC、Target Coinbase、Binance/CZ & 何一圈……），每个预设展开成一组目标
   人物，客户再逐个勾掉/加回。预设定义在 ``config/signal_map_personas.json``。
2. **聚合排名**。对每个候选账号，数「所选目标人物里有几位关注它」—— 总数、按
   persona 分开的数，以及一个综合分。

综合分为什么要按 persona 归一
------------------------------
直接数关注人数，人多的圈子会压倒一切：海外 VC 选 16 人、Binance 圈只有 4 人，
一个只被 VC 圈 5 人关注的账号会排在被 Binance 圈 4 人**全部**关注的账号前面。
所以每个 persona 先算「本圈已采集目标里关注它的比例」（0–1），综合分是**各圈
比例的平均**。一个账号同时进入多个圈子才能拿高分，这正是「跨圈层」的意思。

分母只用**已采集关注网络**的目标人物。没采过的人不是「没关注」，是「没看过」——
把他们算进分母会把整个圈子的比例压低，而那是采集缺口，不是关系事实。一个
persona 一个都没采过时，它不参与平均，并在 ``personas[].collected == 0`` 里如实
报出。

口径
----
* 一条关注边只证明关注存在（CLAUDE.md §2 路径 3）。综合分是**关注信号的覆盖
  度**，不是曝光概率，不是背书。
* 只用 ``follow_edges`` 里 ``disappeared_at IS NULL`` 的边，与发现池同一口径。
* 目标人物本身不进排名 —— 他们是要影响的人，不是要买的人。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .observation_models import FollowEdge, XAccount

PERSONAS_PATH = Path(__file__).resolve().parents[2] / "config" / "signal_map_personas.json"

#: 成员来源，按证据强度从高到低。``manual`` 是编写者凭常识加入，库里没有依据。
MEMBER_SOURCES = ("circles_config", "root_seeds", "candidate_list", "observed_bio", "manual")
MEMBER_SOURCE_LABELS_ZH = {
    "circles_config": "已在目标人物配置中",
    "root_seeds": "已在 Root 种子提案中",
    "candidate_list": "已在目标人物候选名单中",
    "observed_bio": "公开简介写明所属机构",
    "manual": "人工补充，待核实",
}

#: 客户手动加的、不属于任何预设的目标人物，归到这个虚拟 persona。
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


@dataclass(frozen=True)
class Persona:
    id: str
    label: str
    desc: str
    why: str
    members: tuple[PersonaMember, ...]


def load_personas(path: str | Path | None = None) -> tuple[Persona, ...]:
    return _load(str(path or PERSONAS_PATH))


@lru_cache(maxsize=4)
def _load(path: str) -> tuple[Persona, ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for p in raw.get("personas", []):
        members = []
        for m in p.get("members", []):
            source = m.get("source", "manual")
            if source not in MEMBER_SOURCES:
                raise ValueError(f"persona {p['id']}: 未知成员来源 {source!r}")
            members.append(
                PersonaMember(
                    handle=m["handle"].lstrip("@"),
                    name=m.get("name") or m["handle"],
                    role=m.get("role", ""),
                    source=source,
                    # manual 一律待核实，配置里写 false 也不算数 —— 没有依据的
                    # 名字不能靠改一个布尔值就变成「已核实」。
                    needs_review=bool(m.get("needs_review")) or source == "manual",
                    basis=m.get("basis"),
                )
            )
        out.append(
            Persona(
                id=p["id"], label=p["label"], desc=p.get("desc", ""),
                why=p.get("why", ""), members=tuple(members),
            )
        )
    return tuple(out)


def persona_by_id(persona_id: str) -> Persona | None:
    return next((p for p in load_personas() if p.id == persona_id), None)


@dataclass
class Selection:
    """客户的最终选择：哪些 persona、每位目标人物归哪几个 persona。"""

    personas: list[str]
    #: handle(lower) -> 原始大小写 handle
    handles: dict[str, str]
    #: handle(lower) -> 所属 persona id 列表（可多个）
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
    """persona 展开 + 手动加的 handle 合并 − 手动勾掉的 handle。

    顺序稳定：先按 persona 顺序展开成员，再追加手动 handle。同一个人在多个
    persona 里出现时只算一位目标人物，但两个 persona 都记上 —— 综合排名需要知道
    他同时代表哪几个圈子。
    """
    excluded = {h.lstrip("@").lower() for h in exclude_handles if h}
    handles: dict[str, str] = {}
    membership: dict[str, list[str]] = {}
    chosen: list[str] = []
    unknown: list[str] = []
    for pid in dict.fromkeys(personas):
        persona = persona_by_id(pid)
        if persona is None:
            unknown.append(pid)
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
    #: persona id -> (目标人物数, 已采集数)
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
    "综合分 = 各人群预设内「已采集目标人物中关注该账号的比例」的平均值，"
    "每个预设权重相同，避免人多的圈子压倒人少的圈子。这是关注信号的覆盖度，"
    "不是曝光概率，也不代表背书。未采集关注网络的目标人物不计入分母。"
)
