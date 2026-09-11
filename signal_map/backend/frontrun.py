"""AI FrontRun —— 被观察的重要人物**最近开始关注了谁**。

和 Signal Map 问的不是同一个问题
--------------------------------
* Signal Map：客户想影响这些人，应该通过哪些 KOL 和商务路径进入？
* FrontRun：Mango 长期观察的重要人物，最近开始关注和互动了哪些账号、项目与趋势？

**共用数据层，不共用客户界面**（CLAUDE.md §11）。这个模块整体挂在
``/api/internal`` 下：它是 Mango 自己的情报面，不是卖给某一个客户的东西。

这一层能存在，是因为观察层本来就是为变更检测设计的：``FollowSnapshot`` 记录
每次采集，``FollowEdge`` 记录首次/末次观察时间和消失时间。Signal Map 采集 107
位目标人物的关注列表时，顺手把 FrontRun 的底座建好了。

守卫：为什么不能直接查「新边」
------------------------------
``first_seen_at`` 的含义是 **Mango 首次观察到这条边**，不是「这个关注是那天
开始的」。X 不暴露后者。所以：

* 一个观察者**没有更早的完整快照**时，他的每一条边看起来都是「新的」——
  实际只是我们第一次去看。直接报出去就会出现「Elon 刚刚关注了 1,405 个账号」
  这种假消息。
* 一次**被截断**的采集里，「这条边不见了」什么都不能说明 —— 可能只是没翻到
  那一页。所以取关只在前后两次都完整时才认。

这两条不是保守，是这个模块唯一的正确性来源。没有基线时，答案是
「首采，尚无基线」，**不是空列表** —— 空列表会被读成「最近没有动静」。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .bd_models import BDCandidate
from .observation_models import FollowEdge, FollowSnapshot, XAccount

#: 同一天内的两次采集不构成有意义的观察间隔 —— 那通常是一次重跑。低于这个
#: 天数的 delta 仍然会算，但会标出间隔，让读的人自己判断。
MEANINGFUL_INTERVAL_DAYS = 1

#: 被几位观察者同时新关注才算「聚集信号」。一个人开始关注某个账号是日常；
#: 两个互不相关的重要人物在同一窗口里都开始关注，才是值得看一眼的事。
CONVERGENCE_MIN_WATCHERS = 2


@dataclass
class Baseline:
    """一个观察者的基线状态 —— delta 能不能算，全看这个。"""

    account: XAccount
    latest: FollowSnapshot | None = None
    baseline: FollowSnapshot | None = None

    @property
    def usable(self) -> bool:
        """有没有一次**更早的完整**快照可以对比。"""
        return self.baseline is not None and self.latest is not None

    @property
    def interval_days(self) -> int | None:
        if not self.usable:
            return None
        return (self.latest.collected_date - self.baseline.collected_date).days

    @property
    def status(self) -> str:
        if self.latest is None:
            return "never_collected"
        if self.baseline is None:
            # 首采。每条边都是「首次观察到」，但那不是「刚刚关注」。
            return "first_collection"
        if (self.interval_days or 0) < MEANINGFUL_INTERVAL_DAYS:
            return "same_day_recollect"
        return "ready"

    STATUS_LABELS = {
        "never_collected": "尚未采集",
        "first_collection": "首采，尚无基线 —— 本次所有关注均为首次观察到，不代表新发生",
        "same_day_recollect": "同日重采，间隔不足以判断变化",
        "ready": "可对比",
    }


def baselines(session: Session, account_ids: list[int]) -> dict[int, Baseline]:
    """每个观察者的最新快照和可用基线。

    基线 = 最新快照**之前**最近的一次**完整且无错误**的采集。截断的采集不能
    当基线：它没看全，据它判断「新增」会把没翻到的页数算成新关注。
    """
    if not account_ids:
        return {}

    accounts = {
        account.id: account
        for account in session.scalars(select(XAccount).where(XAccount.id.in_(account_ids)))
    }
    by_observer: dict[int, list[FollowSnapshot]] = defaultdict(list)
    for snapshot in session.scalars(
        select(FollowSnapshot)
        .where(
            FollowSnapshot.observer_id.in_(account_ids),
            FollowSnapshot.kind == "following",
        )
        .order_by(FollowSnapshot.id)
    ):
        by_observer[snapshot.observer_id].append(snapshot)

    out: dict[int, Baseline] = {}
    for account_id, account in accounts.items():
        snapshots = by_observer.get(account_id, [])
        if not snapshots:
            out[account_id] = Baseline(account=account)
            continue
        latest = snapshots[-1]
        prior = [
            s for s in snapshots[:-1]
            if s.is_complete and not s.error
        ]
        out[account_id] = Baseline(
            account=account, latest=latest, baseline=prior[-1] if prior else None
        )
    return out


@dataclass
class NewFollow:
    """一条「我们首次观察到 X 开始关注 Y」。措辞不能升级成「X 关注了 Y」。"""

    watcher: XAccount
    observed_at: dt.date
    interval_days: int | None

    def as_dict(self) -> dict:
        return {
            "watcher": self.watcher.handle,
            "watcherFollowers": self.watcher.followers,
            "observedAt": self.observed_at.isoformat(),
            "sinceDays": self.interval_days,
        }


@dataclass
class FrontRunItem:
    """一个被新关注的账号，以及是谁开始关注它的。"""

    account: XAccount
    follows: list[NewFollow] = field(default_factory=list)

    @property
    def watcher_count(self) -> int:
        return len({f.watcher.id for f in self.follows})

    @property
    def converged(self) -> bool:
        """多位重要人物在同一窗口里都开始关注 —— 这才是值得看的那种信号。"""
        return self.watcher_count >= CONVERGENCE_MIN_WATCHERS

    @property
    def resolved(self) -> bool:
        """有没有拉过公开资料。

        新关注的账号里有相当一部分我们只见过 rest_id —— 它们是作为别人关注
        列表里的一个 id 进库的，从没被单独查过。**「Karpathy 刚关注了谁」正是
        这个产品的全部问题**，答不出「谁」就等于没有答案。所以这里标出来，
        并由 ``--frontrun`` 那一路去补。
        """
        return bool(self.account.handle and self.account.bio)

    def as_dict(self) -> dict:
        return {
            "handle": self.account.handle,
            "restId": self.account.rest_id,
            "resolved": self.resolved,
            "displayName": self.account.display_name,
            "bio": self.account.bio,
            "followers": self.account.followers,
            "avatarUrl": self.account.avatar_url,
            "profileUrl": (
                f"https://x.com/{self.account.handle}" if self.account.handle else None
            ),
            "watcherCount": self.watcher_count,
            "converged": self.converged,
            "newlyFollowedBy": [f.as_dict() for f in self.follows],
            # 这句话跟着每一条走，不是页脚的免责声明。
            "claimLimit": (
                "「首次观察到」不等于「刚刚开始关注」：X 不暴露关注的发生时间，"
                "这里只能说明上一次采集时还没有这条关注"
            ),
        }


@dataclass
class FrontRunReport:
    items: list[FrontRunItem]
    dropped: list[dict]
    watchlist_size: int
    comparable: int
    status_counts: dict[str, int]
    window_days: int | None
    unresolved: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "windowDays": self.window_days,
            "watchlist": {
                "size": self.watchlist_size,
                "comparable": self.comparable,
                "byStatus": {
                    key: {"count": value, "label": Baseline.STATUS_LABELS[key]}
                    for key, value in sorted(self.status_counts.items())
                },
                # 分母说清楚。没有基线的人不是「没有动静」，是「还没法比」。
                "note": (
                    f"{self.watchlist_size} 位观察对象中，{self.comparable} 位有可对比的"
                    "历史采集；其余需要再采一次才能判断变化。"
                ),
            },
            "newFollows": [item.as_dict() for item in self.items],
            "unresolvedIds": self.unresolved,
            "unresolvedNote": (
                f"{len(self.unresolved)} 个新关注对象只见过 id，尚未拉取公开资料；"
                "运行 enrich_cofollowed_profiles --frontrun 补齐"
                if self.unresolved else None
            ),
            "converged": [item.as_dict() for item in self.items if item.converged],
            "dropped": self.dropped,
            "method": (
                "对比每位观察对象的最新一次采集与上一次**完整**采集。"
                "截断的采集不作基线 —— 那样会把没翻到的页数算成新关注。"
            ),
        }


def watchlist_accounts(session: Session, domain: str | None = None) -> list[XAccount]:
    """被观察的人 = Signal Map 里标记为目标人物的账号。

    这是 CLAUDE.md §11 说的那条：客户在 Signal Map 里选的 Root 可以喂给
    FrontRun 的观察名单。两个产品共用同一批人，但各问各的问题。
    """
    query = select(BDCandidate.handle).where(
        BDCandidate.object_kind == "target_person", BDCandidate.platform == "X"
    )
    if domain:
        query = query.where(BDCandidate.domain_group == domain)
    handles = {(h or "").lower() for h in session.scalars(query).all() if h}
    if not handles:
        return []
    return [
        account
        for account in session.scalars(select(XAccount).where(XAccount.handle.is_not(None)))
        if (account.handle or "").lower() in handles
    ]


def report(
    session: Session,
    *,
    domain: str | None = None,
    window_days: int | None = None,
    min_watchers: int = 1,
) -> FrontRunReport:
    """自上次完整采集以来，观察名单开始关注了谁。

    ``window_days`` 只过滤**观察时间**，不是关注发生的时间 —— 后者我们不知道。
    """
    watched = watchlist_accounts(session, domain)
    if not watched:
        return FrontRunReport([], [], 0, 0, {}, window_days)

    states = baselines(session, [a.id for a in watched])
    status_counts: dict[str, int] = defaultdict(int)
    for state in states.values():
        status_counts[state.status] += 1

    ready = {aid: st for aid, st in states.items() if st.status == "ready"}
    if not ready:
        return FrontRunReport(
            [], [], len(watched), 0, dict(status_counts), window_days
        )

    cutoff = (
        dt.date.today() - dt.timedelta(days=window_days) if window_days else None
    )

    # 新关注：首次观察到的那次采集正好是最新一次 —— 也就是上一次完整采集里
    # 还没有这条边。
    grouped: dict[int, FrontRunItem] = {}
    latest_ids = {st.latest.id: aid for aid, st in ready.items()}
    rows = session.execute(
        select(FollowEdge.source_id, FollowEdge.target_id, FollowEdge.first_seen_at).where(
            FollowEdge.first_seen_snapshot_id.in_(list(latest_ids)),
            FollowEdge.disappeared_at.is_(None),
        )
    ).all()

    target_ids = {tgt for _src, tgt, _seen in rows}
    accounts = _load(session, target_ids)

    for source_id, target_id, first_seen in rows:
        state = ready.get(source_id)
        account = accounts.get(target_id)
        if state is None or account is None:
            continue
        observed = first_seen.date() if first_seen else state.latest.collected_date
        if cutoff and observed < cutoff:
            continue
        item = grouped.setdefault(target_id, FrontRunItem(account=account))
        item.follows.append(
            NewFollow(state.account, observed, state.interval_days)
        )

    items = [i for i in grouped.values() if i.watcher_count >= min_watchers]
    # 聚集优先：多位重要人物同时开始关注，比一位关注一百个账号更值得看。
    items.sort(key=lambda i: (-i.watcher_count, -(i.account.followers or 0)))

    dropped = _dropped(session, ready, cutoff)
    return FrontRunReport(
        items, dropped, len(watched), len(ready), dict(status_counts), window_days,
        unresolved=[i.account.rest_id for i in items if not i.resolved],
    )


def _load(session: Session, ids) -> dict[int, XAccount]:
    out: dict[int, XAccount] = {}
    items = list(ids)
    for start in range(0, len(items), 900):
        chunk = items[start : start + 900]
        for account in session.scalars(select(XAccount).where(XAccount.id.in_(chunk))):
            out[account.id] = account
    return out


def _dropped(session: Session, ready: dict, cutoff: dt.date | None) -> list[dict]:
    """取关。**只有前后两次都完整时才认** —— 截断的采集里「边不见了」可能只是
    没翻到那一页。

    取关是真实事件：一条注意力路径退化了。但它比新增更容易误报，所以门槛更高。
    """
    rows = session.execute(
        select(FollowEdge.source_id, FollowEdge.target_id, FollowEdge.disappeared_at)
        .where(
            FollowEdge.source_id.in_(list(ready)),
            FollowEdge.disappeared_at.is_not(None),
        )
    ).all()
    if not rows:
        return []
    accounts = _load(session, {tgt for _s, tgt, _d in rows})
    out = []
    for source_id, target_id, gone in rows:
        state = ready.get(source_id)
        account = accounts.get(target_id)
        if state is None or account is None or not state.latest.is_complete:
            continue
        when = gone.date() if gone else None
        if cutoff and when and when < cutoff:
            continue
        out.append({
            "watcher": state.account.handle,
            "handle": account.handle,
            "displayName": account.display_name,
            "observedGoneAt": when.isoformat() if when else None,
            "note": "上一次完整采集里还在，这次不在了 —— 关注已取消",
        })
    return out


def coverage(session: Session, domain: str | None = None) -> dict:
    """观察名单的采集健康度：谁该重采了。"""
    watched = watchlist_accounts(session, domain)
    states = baselines(session, [a.id for a in watched])
    rows = []
    for state in states.values():
        rows.append({
            "handle": state.account.handle,
            "status": state.status,
            "statusLabel": Baseline.STATUS_LABELS[state.status],
            "lastCollected": (
                state.latest.collected_date.isoformat() if state.latest else None
            ),
            "intervalDays": state.interval_days,
            "truncated": bool(state.latest and not state.latest.is_complete),
        })
    rows.sort(key=lambda r: (r["lastCollected"] or "", r["handle"] or ""))
    return {
        "size": len(watched),
        "accounts": rows,
        "nextStep": (
            "运行 collect_target_following --from-candidates 再采一次；"
            "只有存在上一次完整采集时，变化才算得出来"
        ),
    }
