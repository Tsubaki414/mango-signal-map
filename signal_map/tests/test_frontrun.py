"""AI FrontRun 的守卫测试。

这个模块只有一件事会错，而且错了会很难看：**把「我们第一次去看」报成
「他刚刚开始关注」**。``first_seen_at`` 的含义是 Mango 首次观察到，X 不暴露
关注的发生时间。没有守卫的话，首采会输出「Elon 刚刚关注了 1,405 个账号」。

所以这里测的几乎全是「什么时候**不该**说话」。
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from signal_map.backend import app as app_module
from signal_map.backend import frontrun
from signal_map.backend.bd_models import BDCandidate
from signal_map.backend.models import Base
from signal_map.backend.observation_models import FollowEdge, FollowSnapshot, XAccount

INTERNAL = "internal-test-token"


@pytest.fixture
def env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        session = Local()
        try:
            yield session
        finally:
            session.close()

    app_module.app.dependency_overrides[app_module.db] = override
    monkeypatch.setenv("SIGNAL_MAP_INTERNAL_TOKEN", INTERNAL)
    yield TestClient(app_module.app), Local
    app_module.app.dependency_overrides.clear()


@pytest.fixture
def session(env):
    _, Local = env
    with Local() as s:
        yield s


def _watcher(session: Session, handle: str, domain: str = "ai") -> XAccount:
    account = XAccount(
        rest_id=f"w-{handle}", handle=handle, display_name=handle.title(),
        followers=500_000, roles="target",
    )
    session.add(account)
    session.add(BDCandidate(
        platform="X", handle=handle, object_kind="target_person",
        domain_group=domain, source_name="test",
    ))
    session.flush()
    return account


def _seen(session: Session, handle: str) -> XAccount:
    """一个被关注的账号，资料已拉过 —— 否则 ``resolved`` 为 False。"""
    account = XAccount(
        rest_id=f"s-{handle}", handle=handle, display_name=handle,
        bio=f"{handle} builds things", followers=20_000, roles="observed",
    )
    session.add(account)
    session.flush()
    return account


def _snapshot(
    session: Session, observer: XAccount, day: int, *,
    complete: bool = True, error: str | None = None,
) -> FollowSnapshot:
    snapshot = FollowSnapshot(
        observer_id=observer.id, kind="following",
        collected_at=dt.datetime(2026, 9, day),
        collected_date=dt.date(2026, 9, day),
        edge_count=1, pages_fetched=1, is_complete=complete, error=error,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def _edge(session: Session, src: XAccount, dst: XAccount, snapshot: FollowSnapshot):
    session.add(FollowEdge(
        source_id=src.id, target_id=dst.id,
        first_seen_at=snapshot.collected_at, last_seen_at=snapshot.collected_at,
        first_seen_snapshot_id=snapshot.id, last_seen_snapshot_id=snapshot.id,
    ))
    session.flush()


# =============================================================================
# 守卫 —— 什么时候不该说话
# =============================================================================


def test_first_collection_reports_no_new_follows(session):
    """首采时**每一条边都是「首次观察到」，但没有一条是「新发生」**。

    这是这个模块存在的全部理由。不加守卫的话，第一次采集就会输出
    「Elon 刚刚关注了 1,405 个账号」—— 那是假消息，而且很难看。
    """
    watcher = _watcher(session, "karpathy")
    seen = _seen(session, "newthing")
    snap = _snapshot(session, watcher, 9)
    _edge(session, watcher, seen, snap)
    session.commit()

    report = frontrun.report(session)
    assert report.items == []
    assert report.comparable == 0
    assert report.status_counts["first_collection"] == 1
    # 空列表不能读成「最近没动静」，必须说清是「还没法比」。
    assert "需要再采一次" in report.as_dict()["watchlist"]["note"]


def test_a_second_complete_snapshot_makes_the_delta_real(session):
    """有了更早的完整基线，新边才算数。"""
    watcher = _watcher(session, "simonw")
    old = _seen(session, "already")
    fresh = _seen(session, "justnow")
    first = _snapshot(session, watcher, 9)
    _edge(session, watcher, old, first)
    second = _snapshot(session, watcher, 11)
    _edge(session, watcher, fresh, second)
    session.commit()

    report = frontrun.report(session)
    assert report.comparable == 1
    assert [i.account.handle for i in report.items] == ["justnow"]
    assert report.items[0].follows[0].interval_days == 2


def test_a_truncated_baseline_is_not_a_baseline(session):
    """被页数截断的采集不能当基线。

    它没看全，据它判断「新增」会把当时没翻到的页数算成新关注 —— 一次凭空
    制造出来的情报。
    """
    watcher = _watcher(session, "swyx")
    fresh = _seen(session, "maybenew")
    _snapshot(session, watcher, 9, complete=False)
    second = _snapshot(session, watcher, 11)
    _edge(session, watcher, fresh, second)
    session.commit()

    report = frontrun.report(session)
    assert report.comparable == 0
    assert report.items == []


def test_an_errored_snapshot_is_not_a_baseline(session):
    watcher = _watcher(session, "rasbt")
    fresh = _seen(session, "whoknows")
    _snapshot(session, watcher, 9, error="rate limited")
    second = _snapshot(session, watcher, 11)
    _edge(session, watcher, fresh, second)
    session.commit()
    assert frontrun.report(session).items == []


def test_same_day_recollect_is_not_a_window(session):
    """同一天重跑一次不构成观察间隔。"""
    watcher = _watcher(session, "emollick")
    fresh = _seen(session, "sameday")
    _snapshot(session, watcher, 11)
    second = _snapshot(session, watcher, 11)
    _edge(session, watcher, fresh, second)
    session.commit()

    report = frontrun.report(session)
    assert report.status_counts.get("same_day_recollect") == 1
    assert report.items == []


# =============================================================================
# 信号
# =============================================================================


def test_convergence_needs_two_independent_watchers(session):
    """一个人开始关注某个账号是日常；**两位重要人物在同一窗口都开始关注**
    才是值得看一眼的事。"""
    a = _watcher(session, "karpathy")
    b = _watcher(session, "rasbt")
    solo = _seen(session, "onewatcher")
    hot = _seen(session, "twowatchers")
    for watcher in (a, b):
        _snapshot(session, watcher, 9)
    later_a = _snapshot(session, a, 11)
    later_b = _snapshot(session, b, 11)
    _edge(session, a, solo, later_a)
    _edge(session, a, hot, later_a)
    _edge(session, b, hot, later_b)
    session.commit()

    report = frontrun.report(session)
    converged = [i for i in report.items if i.converged]
    assert [i.account.handle for i in converged] == ["twowatchers"]
    # 聚集的排在前面 —— 那是这个产品要人先看到的东西。
    assert report.items[0].account.handle == "twowatchers"


def test_every_item_carries_its_claim_limit(session):
    """限制跟着每一条走，不是页脚的一句免责声明。"""
    watcher = _watcher(session, "hardmaru")
    fresh = _seen(session, "someone")
    _snapshot(session, watcher, 9)
    later = _snapshot(session, watcher, 11)
    _edge(session, watcher, fresh, later)
    session.commit()

    item = frontrun.report(session).as_dict()["newFollows"][0]
    assert "不等于「刚刚开始关注」" in item["claimLimit"]


def test_unprofiled_accounts_are_flagged_not_hidden(session):
    """「Karpathy 刚关注了谁」答不出「谁」就等于没有答案。"""
    watcher = _watcher(session, "karpathy")
    ghost = XAccount(rest_id="ghost-1", roles="observed")
    session.add(ghost)
    session.flush()
    _snapshot(session, watcher, 9)
    later = _snapshot(session, watcher, 11)
    _edge(session, watcher, ghost, later)
    session.commit()

    payload = frontrun.report(session).as_dict()
    assert payload["newFollows"][0]["resolved"] is False
    assert payload["unresolvedIds"] == ["ghost-1"]
    assert "尚未拉取公开资料" in payload["unresolvedNote"]


def test_domain_scopes_the_watchlist(session):
    """观察名单按领域分套 —— 和 Signal Map 的 Root 库同一批人。"""
    _watcher(session, "karpathy", domain="ai")
    _watcher(session, "vitalikbuterin", domain="crypto")
    session.commit()
    assert frontrun.report(session, domain="crypto").watchlist_size == 1
    assert frontrun.report(session).watchlist_size == 2


# =============================================================================
# 权限
# =============================================================================


def test_frontrun_is_internal_only(env):
    """共用数据层，**不共用客户界面**（CLAUDE.md §11）。"""
    client, _ = env
    for path in ("/api/internal/bd/frontrun/deltas", "/api/internal/bd/frontrun/coverage"):
        assert client.get(path).status_code == 401
        assert client.get(
            path, headers={"Authorization": f"Bearer {INTERNAL}"}
        ).status_code == 200
    # 客户命名空间里不该存在任何 frontrun 路径。
    assert not [
        p for p in app_module.app.openapi()["paths"]
        if "frontrun" in p and not p.startswith("/api/internal")
    ]
