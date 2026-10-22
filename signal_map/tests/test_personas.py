"""人群预设与跨圈层综合排名。

这几条出错时**看起来是对的**，所以单独测：

* 综合分按 persona 归一 —— 人多的圈子不能靠人数压倒人少的圈子。
* 没采过关注网络的目标人物不进分母（「没看过」不是「没关注」）。
* 手动勾掉的人真的不参与计算；目标人物本身不进排名。
* 配置里 ``manual`` 来源的人一律标待核实，改布尔值也不算数。
* 客户面只能勾掉预设成员，不能塞任意 handle；不下发内部字段。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from signal_map.backend import app as app_module
from signal_map.backend import personas, v3_catalog
from signal_map.backend.bd_models import BDCandidate
from signal_map.backend.db import session_dependency
from signal_map.backend.models import Base
from signal_map.backend.observation_models import FollowEdge, XAccount

INTERNAL = "internal-test-token"

CONFIG = {
    "personas": [
        {
            "id": "big", "label": "大圈", "desc": "", "why": "",
            "members": [
                {"handle": "a1", "name": "A1", "source": "circles_config"},
                {"handle": "a2", "name": "A2", "source": "circles_config"},
                {"handle": "a3", "name": "A3", "source": "observed_bio", "basis": "x"},
                {"handle": "a4", "name": "A4", "source": "candidate_list"},
                # 收录了但没采过关注网络：不能进分母。
                {"handle": "a5", "name": "A5", "source": "root_seeds"},
                # 观察层里根本没有。
                {"handle": "ghost", "name": "Ghost", "source": "manual", "needs_review": False},
            ],
        },
        {
            "id": "small", "label": "小圈", "desc": "", "why": "",
            "members": [{"handle": "b1", "name": "B1", "source": "observed_bio"}],
        },
    ]
}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "personas.json"
    path.write_text(json.dumps(CONFIG), encoding="utf-8")
    monkeypatch.setattr(personas, "PERSONAS_PATH", path)
    # 客户面会排除目标人物配置与人工排除名单；测试库里这些都不存在。
    monkeypatch.setattr(v3_catalog, "load_targets", lambda path=None: ())
    monkeypatch.setattr(v3_catalog, "excluded_handles", lambda: frozenset())
    personas._load.cache_clear()
    yield path
    personas._load.cache_clear()


@pytest.fixture
def env(cfg, monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        s = Local()
        try:
            yield s
        finally:
            s.close()

    app_module.app.dependency_overrides[app_module.db] = override
    app_module.app.dependency_overrides[session_dependency] = override
    monkeypatch.setenv("SIGNAL_MAP_INTERNAL_TOKEN", INTERNAL)
    with Local() as s:
        _seed(s)
    yield TestClient(app_module.app), Local
    app_module.app.dependency_overrides.clear()


def _seed(s) -> None:
    acc = {}
    for i, h in enumerate(
        ["a1", "a2", "a3", "a4", "a5", "b1", "wide", "deep", "gone", "vc_fund"], start=1
    ):
        acc[h] = XAccount(rest_id=f"r{i}", handle=h, display_name=h.upper(),
                          followers=1000 * i, root_review_status="pending")
    s.add_all(acc.values())
    s.flush()

    def follow(src, tgt, gone=False):
        import datetime as dt
        s.add(FollowEdge(source_id=acc[src].id, target_id=acc[tgt].id,
                         disappeared_at=dt.datetime(2026, 1, 1) if gone else None))

    # deep：大圈 3/4 关注，小圈没有 → (0.75 + 0) / 2 = 0.375
    for src in ("a1", "a2", "a3"):
        follow(src, "deep")
    # wide：大圈 1/4 + 小圈 1/1 → (0.25 + 1) / 2 = 0.625，人数更少但跨圈
    follow("a1", "wide")
    follow("b1", "wide")
    # 消失的边不算。
    follow("a4", "gone", gone=True)
    # 目标人物互相关注：a2 不能出现在排名里。
    follow("a1", "a2")
    # a4 只关注了另一位目标人物 —— 已采集（进分母），但不贡献任何候选。
    follow("a4", "a1")
    # 买不到的身份：内部默认不进榜，客户面也不进。
    follow("a1", "vc_fund")
    follow("b1", "vc_fund")

    for h, kind in (("deep", "kol"), ("wide", "kol"), ("vc_fund", "organization")):
        s.add(BDCandidate(platform="X", handle=h, object_kind=kind, source_name="test"))
    s.commit()


# =============================================================================
# 真实配置文件
# =============================================================================


def test_real_config_is_well_formed():
    personas._load.cache_clear()
    defs = personas.load_personas()
    ids = [p.id for p in defs]
    assert {"overseas_vc", "target_coinbase", "target_robinhood",
            "binance_circle", "industry_titans"} <= set(ids)
    assert len(ids) == len(set(ids))
    circles = json.loads(v3_catalog.CONFIG_PATH.read_text(encoding="utf-8"))
    configured = {t["handle"].lower() for t in circles["targets"]}
    for p in defs:
        handles = [m.handle.lower() for m in p.members]
        assert len(handles) == len(set(handles)), p.id
        for m in p.members:
            if m.source == "manual":
                assert m.needs_review, m.handle
            if m.source == "circles_config":
                assert m.handle.lower() in configured, m.handle
            if m.source == "observed_bio":
                assert m.basis, f"{m.handle}: observed_bio 必须引用简介原文"


def test_manual_source_always_needs_review(cfg):
    ghost = next(m for m in personas.persona_by_id("big").members if m.handle == "ghost")
    assert ghost.needs_review is True


# =============================================================================
# 展开
# =============================================================================


def test_expand_merges_excludes_and_tracks_membership(cfg):
    sel = personas.expand(["big", "small"], ["@Extra", "a1"], ["a3"])
    assert "a3" not in sel.handles
    assert sel.handles["extra"] == "Extra"
    assert sel.membership["extra"] == [personas.CUSTOM_PERSONA_ID]
    # a1 已在预设里，手动再加一次不能把它改成「自选」。
    assert sel.membership["a1"] == ["big"]
    assert sel.personas == ["big", "small", personas.CUSTOM_PERSONA_ID]


def test_expand_reports_unknown_persona(cfg):
    assert personas.expand(["nope"]).unknown_personas == ["nope"]


# =============================================================================
# 综合排名
# =============================================================================


def test_score_is_normalised_per_persona(env):
    _, Local = env
    with Local() as s:
        res = personas.aggregate(s, personas.expand(["big", "small"]))
    by = {r.account.handle: r for r in res.items}
    # 分母只算已采集的 4 位（a5 没采、ghost 不存在）。
    assert res.coverage == {"big": (6, 4), "small": (1, 1)}
    assert by["deep"].score == pytest.approx(0.375)
    assert by["wide"].score == pytest.approx(0.625)
    # 跨圈的 wide 排在人数更多的 deep 前面。
    order = [r.account.handle for r in res.items]
    assert order.index("wide") < order.index("deep")
    assert by["wide"].circles_reached == 2
    assert res.unresolved == ["ghost"]
    assert res.uncollected == ["a5"]


def test_targets_and_disappeared_edges_never_ranked(env):
    _, Local = env
    with Local() as s:
        res = personas.aggregate(s, personas.expand(["big", "small"]))
    handles = {r.account.handle for r in res.items}
    assert "a2" not in handles
    assert "gone" not in handles


def test_unticked_target_drops_out_of_counts(env):
    _, Local = env
    with Local() as s:
        res = personas.aggregate(s, personas.expand(["big"], (), ["a1"]))
    by = {r.account.handle: r for r in res.items}
    assert "wide" not in by  # 只有 a1 和 b1 关注它
    assert by["deep"].followed_by == ["a2", "a3"]
    assert by["deep"].score == pytest.approx(2 / 3)


# =============================================================================
# 接口
# =============================================================================


def test_internal_personas_requires_token(env):
    client, _ = env
    assert client.get("/api/internal/bd/personas").status_code == 401
    body = client.get(
        "/api/internal/bd/personas", headers={"Authorization": f"Bearer {INTERNAL}"}
    ).json()
    big = body["personas"][0]
    assert big["coverage"] == {"targets": 6, "collected": 4, "needs_review": 1}


def test_screen_accepts_personas(env):
    client, _ = env
    h = {"Authorization": f"Bearer {INTERNAL}"}
    res = client.post("/api/internal/bd/screen", headers=h, json={
        "personas": ["small"], "target_handles": ["a1"], "exclude_handles": [],
        "limit": 5, "priced_limit": 5,
    })
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["preferences"]["target_handles"] == ["b1", "a1"]
    agg = body["aggregate_ranking"]
    handles = [i["handle"] for i in agg["items"]]
    assert handles[0] == "wide"
    assert "vc_fund" not in handles  # 第 4 步：机构默认不进榜
    assert client.post("/api/internal/bd/screen", headers=h,
                       json={"personas": ["nope"]}).status_code == 422


def test_client_ranking_is_whitelisted_and_buyable_only(env):
    client, _ = env
    lst = client.get("/api/personas").json()
    assert set(lst[0]["members"][0]) == {
        "handle", "name", "role", "avatarUrl", "needsReview", "collected"}
    body = client.get("/api/personas/ranking?personas=big,small").json()
    handles = [i["handle"] for i in body["items"]]
    assert handles == ["@wide", "@deep"]
    item = body["items"][0]
    assert set(item) == {
        "rank", "name", "handle", "avatarUrl", "url", "followers", "source", "score",
        "targetsFollowing", "circlesReached", "followedBy", "byPersona", "verified"}
    assert item["verified"] is False
    assert client.get("/api/personas/ranking?personas=nope").status_code == 404
