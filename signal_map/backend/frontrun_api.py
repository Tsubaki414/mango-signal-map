"""FrontRun 的内部接口。

整体挂在 ``/api/internal`` 下：这是 Mango 自己的情报面，**不卖给任何一个客户**
（CLAUDE.md §11：共用数据层，不共用客户界面）。挂错命名空间的后果不是权限
问题，是把「我们在盯谁」这件事告诉了客户。

为什么响应里一定带 ``status``
-----------------------------
``first_collection`` 和「最近没有新关注」在数据上都是「没有条目」，含义却相反：
一个是我们还没有基线，另一个是确实没动静。把前者显示成后者，读的人会得出
「这批人最近很安静」的结论 —— 而真相是我们根本还没看过第二眼。

所以 ``status_counts`` 和 ``nextStep`` 是响应的一部分，不是调试信息。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from . import frontrun
from .db import session_dependency

router = APIRouter(prefix="/api/internal/frontrun", tags=["frontrun"])


def _account(acc: Any) -> dict[str, Any]:
    return {
        "handle": acc.handle,
        "name": acc.display_name,
        "followers": acc.followers,
        "bio": (acc.bio or "")[:200] or None,
    }


@router.get("/report")
def report(
    domain: str | None = Query(None),
    window_days: int | None = Query(None, description="只看最近 N 天观察到的变化"),
    min_watchers: int = Query(1, ge=1, description="至少几位观察者新关注了同一个账号"),
    session: Session = Depends(session_dependency),
) -> dict[str, Any]:
    """观察名单最近开始关注了谁。

    ``min_watchers`` 是这份报告唯一的强度旋钮：一个人关注了某账号只是个人口味，
    多位在 24 小时内关注同一个账号才是信号 —— frontrun.vc 把这个现象叫
    「the social graph lit up」。默认 1 是为了先看清全貌，真正用时应该调高。
    """
    rep = frontrun.report(
        session, domain=domain, window_days=window_days, min_watchers=min_watchers
    )
    return {
        "watchlistSize": rep.watchlist_size,
        # 能比对的人数 —— 报告的真实分母。它远小于观察名单时，结论不可推广。
        "comparable": rep.comparable,
        "statusCounts": rep.status_counts,
        "windowDays": rep.window_days,
        "items": [
            {
                "account": _account(it.account),
                "watcherCount": len(it.follows),
                "watchers": [
                    {
                        **_account(f.watcher),
                        "observedAt": f.observed_at.isoformat() if f.observed_at else None,
                        # 两次采集间隔。间隔越大，「最近」这个词越不精确 ——
                        # 我们只知道这条边出现在两次观察之间的某一刻。
                        "intervalDays": f.interval_days,
                    }
                    for f in it.follows
                ],
            }
            for it in rep.items
        ],
        "dropped": rep.dropped,
        "unresolved": rep.unresolved,
    }


@router.get("/coverage")
def coverage(
    domain: str | None = Query(None),
    session: Session = Depends(session_dependency),
) -> dict[str, Any]:
    """观察名单的采集健康度：谁该重采了。

    一次采集只是快照，两次才是信号。这个接口的存在是为了让「该重采了」变成
    一件看得见的事，而不是等到有人问「为什么报告总是空的」。
    """
    return frontrun.coverage(session, domain=domain)
