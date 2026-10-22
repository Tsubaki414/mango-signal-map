"""目标圈层预设与跨圈层综合排名。

这几条出错时**看起来是对的**，所以单独测：

* 预设长在圈层上：成员就是它那几个圈层里的目标人物，旧 id 照收。
* 综合分按预设 / 圈层归一 —— 人多的圈子不能靠人数压倒人少的圈子。
* 没采过关注网络的目标人物不进分母（「没看过」不是「没关注」）。
* 手动勾掉的人真的不参与计算；目标人物本身不进排名。
* 配置里 ``manual`` 来源的人一律标待核实，改布尔值也不算数。
* 客户面只能勾掉预设成员，不能塞任意 handle；不下发内部字段。
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from signal_map.backend import app as app_module
from signal_map.backend import presets, v3_catalog
from signal_map.backend.bd_models import BDCandidate
from signal_map.backend.db import session_dependency
from signal_map.backend.models import Base
from signal_map.backend.observation_models import FollowEdge, XAccount

INTERNAL = "internal-test-token"


def _t(handle, circle, source="circles_config", **kw):
    return {"id": f"t_{handle}", "name": handle.upper(), "handle": handle, "role": "",
            "group": "g", "circle": circle, "why": "", "source": source, **kw}


def _circle(cid):
    return {"id": cid, "label": cid, "value": "", "who": "", "pick": ""}


CONFIG = {
    "groups": [{"id": "g", "label": "G", "desc": "", "domains": []}],
    "circles": {"g": [_circle("big"), _circle("small")]},
    "targets": [
        _t("a1", "big"),
        _t("a2", "big"),
        _t("a3", "big", "observed_bio", basis="x"),
        _t("a4", "big", "candidate_list"),
        # 收录了但没采过关注网络：不能进分母。
        _t("a5", "big", "root_seeds"),
        # 观察层里根本没有。
        _t("ghost", "big", "manual", needs_review=False),
        _t("b1", "small", "observed_bio", basis="y"),
    ],
    "presets": [
        {"id": "big", "aliases": ["old_big"], "group": "g", "label": "大圈", "circles": ["big"]},
        {"id": "small", "group": "g", "label": "小圈", "circles": ["small"]},
    ],
}


def _clear_caches():
    for fn in (v3_catalog.load_config, v3_catalog.load_targets, v3_catalog.load_presets,
               v3_catalog.excluded_handles):
        fn.cache_clear()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    path = tmp_path / "circles.json"
    path.write_text(json.dumps(CONFIG), encoding="utf-8")
    monkeypatch.setattr(v3_catalog, "CONFIG_PATH", path)
    # 客户面会排除人工排除名单；测试里不存在。
    monkeypatch.setattr(v3_catalog, "EXCLUDED_PATH", tmp_path / "none.json")
    _clear_caches()
    yield path
    _clear_caches()


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


OLD_IDS = {"overseas_vc", "target_coinbase", "target_robinhood", "binance_circle",
           "industry_titans"}

#: 预设与圈层的名字必须是中性的分类名，和既有圈层同一种口吻。
BANNED_LABEL = re.compile(r"target|社工|大佬|CZ|何一|Coinbase|Robinhood|Binance", re.I)


def test_real_config_is_well_formed():
    _clear_caches()
    defs = v3_catalog.load_presets()
    ids = [p.id for p in defs]
    aliases = [a for p in defs for a in p.aliases]
    assert len(ids + aliases) == len(set(ids + aliases))
    # 旧 id 全部还能用。
    assert OLD_IDS <= set(aliases)
    for old in OLD_IDS:
        assert presets.persona_by_id(old) is not None, old

    groups = {g.id: {c.id for c in g.circles} for g in v3_catalog.load_config()}
    for p in defs:
        assert p.group in groups, p.id
        assert p.circles and set(p.circles) <= groups[p.group], p.id
        assert len(p.circles) <= 3, f"{p.id}: 重点圈层最多 3 个"
        assert not BANNED_LABEL.search(p.label), p.label
        assert presets.persona_by_id(p.id).members, f"{p.id}: 预设没有成员"
    for g in v3_catalog.load_config():
        for c in g.circles:
            assert not BANNED_LABEL.search(c.label), c.label

    targets = v3_catalog.load_targets()
    # 「—」是中文加密圈待补充的占位，不是一个人。
    handles = [t.handle.lower() for t in targets if re.search(r"\w", t.handle)]
    assert len(handles) == len(set(handles)), "同一个人只能有一处定义"
    for t in targets:
        assert t.circle in groups[t.group], t.id
        if t.source == "manual":
            assert t.needs_review, t.handle
        if t.source == "observed_bio":
            assert t.basis, f"{t.handle}: observed_bio 必须引用简介原文"


def test_manual_source_always_needs_review(cfg):
    ghost = next(m for m in presets.persona_by_id("big").members if m.handle == "ghost")
    assert ghost.needs_review is True
    assert next(t for t in v3_catalog.load_targets() if t.handle == "ghost").needs_review


def test_members_come_from_circles(cfg):
    big = presets.persona_by_id("big")
    assert [m.handle for m in big.members] == ["a1", "a2", "a3", "a4", "a5", "ghost"]
    assert {m.circle for m in big.members} == {"big"}


# =============================================================================
# 展开
# =============================================================================


def test_expand_merges_excludes_and_tracks_membership(cfg):
    sel = presets.expand(["big", "small"], ["@Extra", "a1"], ["a3"])
    assert "a3" not in sel.handles
    assert sel.handles["extra"] == "Extra"
    assert sel.membership["extra"] == [presets.CUSTOM_PERSONA_ID]
    # a1 已在预设里，手动再加一次不能把它改成「自选」。
    assert sel.membership["a1"] == ["big"]
    assert sel.personas == ["big", "small", presets.CUSTOM_PERSONA_ID]


def test_expand_reports_unknown_persona(cfg):
    assert presets.expand(["nope"]).unknown_personas == ["nope"]


def test_old_ids_resolve_to_current_id(cfg):
    sel = presets.expand(["old_big", "big"])
    # 旧 id 与当前 id 指向同一个预设：只展开一次，回报当前 id。
    assert sel.personas == ["big"]
    assert sel.membership["a1"] == ["big"]
    assert sel.unknown_personas == []


# =============================================================================
# 综合排名
# =============================================================================


def test_score_is_normalised_per_persona(env):
    _, Local = env
    with Local() as s:
        res = presets.aggregate(s, presets.expand(["big", "small"]))
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
        res = presets.aggregate(s, presets.expand(["big", "small"]))
    handles = {r.account.handle for r in res.items}
    assert "a2" not in handles
    assert "gone" not in handles


def test_unticked_target_drops_out_of_counts(env):
    _, Local = env
    with Local() as s:
        res = presets.aggregate(s, presets.expand(["big"], (), ["a1"]))
    by = {r.account.handle: r for r in res.items}
    assert "wide" not in by  # 只有 a1 和 b1 关注它
    assert by["deep"].followed_by == ["a2", "a3"]
    assert by["deep"].score == pytest.approx(2 / 3)


# =============================================================================
# 接口
# =============================================================================


def test_circle_reach_is_normalised_and_ignores_uncollected():
    circle_of = {"a1": "big", "a2": "big", "a3": "big", "a4": "big", "b1": "small", "x": "big"}
    collected = {"a1", "a2", "a3", "a4", "b1"}
    # x 有互动信号但没采关注网络：不进分子，也不进分母。
    reach, score = presets.circle_reach(["a1", "b1", "b1", "x"], circle_of, collected,
                                        ["big", "small", "empty"])
    assert [(r.circle, r.count, r.collected) for r in reach] == [
        ("big", 1, 4), ("small", 1, 1), ("empty", 0, 0)]
    # empty 一个都没采过，不进平均：(0.25 + 1) / 2
    assert score == pytest.approx(0.625)


def test_groups_expose_presets_as_circle_shortcuts(env):
    client, _ = env
    g = client.get("/api/groups").json()[0]
    by = {p["id"]: p for p in g["presets"]}
    assert by["big"]["circles"] == ["big"]
    assert by["big"]["coverage"] == {"targets": 6, "collected": 4}
    assert set(by["big"]) == {"id", "label", "desc", "why", "circles", "coverage"}
    targets = client.get("/api/targets?group=g").json()
    assert next(t for t in targets if t["handle"] == "@ghost")["needsReview"] is True
    assert next(t for t in targets if t["handle"] == "@a1")["needsReview"] is False


def test_internal_personas_requires_token(env):
    client, _ = env
    assert client.get("/api/internal/bd/personas").status_code == 401
    body = client.get(
        "/api/internal/bd/personas", headers={"Authorization": f"Bearer {INTERNAL}"}
    ).json()
    big = body["personas"][0]
    assert big["coverage"] == {"targets": 6, "collected": 4, "needs_review": 1}
    assert big["aliases"] == ["old_big"] and big["circles"] == ["big"]


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
    old = client.post("/api/internal/bd/screen", headers=h, json={
        "personas": ["old_big"], "limit": 5, "priced_limit": 5}).json()
    assert old["persona_selection"]["personas"] == [{"id": "big", "label": "大圈"}]


def test_client_ranking_is_whitelisted_and_buyable_only(env):
    client, _ = env
    lst = client.get("/api/personas").json()
    assert set(lst[0]["members"][0]) == {
        "handle", "name", "role", "circle", "avatarUrl", "needsReview", "collected"}
    body = client.get("/api/personas/ranking?personas=old_big,small").json()
    handles = [i["handle"] for i in body["items"]]
    assert handles == ["@wide", "@deep"]
    item = body["items"][0]
    assert set(item) == {
        "rank", "name", "handle", "avatarUrl", "url", "followers", "source", "score",
        "targetsFollowing", "circlesReached", "followedBy", "byPersona", "verified"}
    assert item["verified"] is False
    assert client.get("/api/personas/ranking?personas=nope").status_code == 404
