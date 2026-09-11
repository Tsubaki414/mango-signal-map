"""v3 客户接口：形状、方向纪律与泄露扫描。

这一层**没有 per-client token**（发给项目方的公开链接），所以泄露扫描比
``/api/client/*`` 更要紧，也更要求精确 —— 见 ``test_no_internal_values_leak``
里关于「按叶子比对，不要按子串搜」的说明。
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from signal_map.backend.app import app
from signal_map.backend.db import SessionLocal
from signal_map.backend.models import ContactMethod, Quote

client = TestClient(app)

MONEY = re.compile(r"\$\s?[\d,]+(?:\.\d+)?")

#: ``/api/taxonomy`` 的预算档是客户用来选预算的，本来就该可见。它的
#: 3000/10000/30000/80000 偶尔等于某位创作者的成本，那是巧合不是泄露。
ALLOWED_VALUE_PATHS = (".budgets",)

#: 计数类字段不参与金额比对。``counts.discovered`` 会等于 60，而库里恰好有一条
#: 成本是 60.0 —— 把计数当成金额来查，得到的是假阳性，真泄露反而被淹没。
#: 这里按**字段语义**排除，而不是调高金额下限：下限会把 20 美元这种真实低价
#: 报价放过去，而那才是真要防的东西。
COUNT_FIELDS = ("counts", "coverage", "followers", "count", "total", "found", "kept")


def leaves(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from leaves(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from leaves(v, f"{path}[{i}]")
    else:
        yield path, node


def all_payloads() -> dict[str, object]:
    """每个客户可达的 v3 接口都要进扫描。

    新增客户端点必须加进这里 —— 上一代产品的泄露正是从一个没被扫到的新端点
    出去的。
    """
    out = {
        p: client.get(p).json()
        for p in (
            "/api/meta",
            "/api/taxonomy",
            "/api/groups",
            "/api/targets?group=ai",
            "/api/candidates?group=ai&limit=60",
            "/api/candidates?group=crypto&limit=40",
        )
    }
    out["/api/discover"] = client.post("/api/discover", json={"group": "ai"}).json()
    return out


def test_endpoints_answer():
    for path, body in all_payloads().items():
        assert body is not None, path


def test_candidates_return_both_sources():
    body = client.get("/api/candidates?group=ai&limit=60").json()
    counts = body["counts"]
    assert counts["priced"] > 0
    # discovered 是产品最值钱的部分；为 0 说明发现管线断了，不是"暂时没有"。
    assert counts["discovered"] > 0
    assert counts["total"] == counts["priced"] + counts["discovered"]


def test_discovered_carry_discovery_path():
    body = client.get("/api/candidates?group=ai&limit=60").json()
    for item in body["items"]:
        if item["source"] == "discovered":
            assert item["discoveryPath"], item["handle"]


def test_no_price_amounts_only_tiers():
    body = client.get("/api/candidates?group=ai&limit=60").json()
    for item in body["items"]:
        assert "internal_cost_usd" not in item
        assert "amount_usd" not in item
        if item["source"] == "priced" and item.get("tier") is not None:
            assert item["tier"] in (1, 2, 3, 4)


def test_taxonomy_hides_tier_thresholds():
    """档位只下发 ``mark``/``label``。``lo``/``hi`` 是成本区间，给了就能反推成本。"""
    for tier in client.get("/api/taxonomy").json()["tiers"]:
        assert "lo" not in tier and "hi" not in tier


def test_targets_are_not_claimed_verified():
    """0/282,877 个账号经过人工复核，接口不得把任何目标人物说成已核验。"""
    for t in client.get("/api/targets?group=ai").json():
        assert t["reviewed"] is False


def test_edges_are_not_claimed_verified():
    body = client.get("/api/candidates?group=ai&limit=60").json()
    for item in body["items"]:
        for edge in item["edges"]:
            assert edge["verified"] is False
            if edge["type"] == "cofollow":
                # 纯关注只证明关注存在，不能升级成 medium/strong。
                assert edge["strength"] == "weak"


def test_focus_never_filters_candidates():
    """标记重点圈层只重排，数量永不下降 —— 客户越参与结果越少是上一版的教训。"""
    base = client.get("/api/candidates?group=ai&limit=60").json()["counts"]["total"]
    narrowed = client.post(
        "/api/discover", json={"group": "ai", "circles": ["founders"]}
    ).json()
    assert narrowed["found"] >= 0
    assert client.get("/api/candidates?group=ai&limit=60").json()["counts"]["total"] == base


def test_no_internal_field_names():
    banned = (
        "internal_cost", "client_price", "amount_usd", "raw_quote", "contact",
        "supplier", "negotiat", "介绍人", "底价", "谈判",
    )
    for path, body in all_payloads().items():
        for loc, _ in leaves(body):
            assert not any(b in loc.lower() for b in banned), f"{path} {loc}"


def test_no_internal_values_leak():
    """按**叶子**比对，不按子串搜整个 JSON。

    子串搜法会把 ``100``、``200`` 这类三位数匹配进粉丝数和时间戳里，制造出
    几百条假阳性，真的泄露反而被淹没。同时保留对**句子里嵌的金额**的检查 ——
    字段白名单挡不住文案，上一代产品的成本正是这样进了生成的句子。
    """
    with SessionLocal() as session:
        costs = {
            q.internal_cost_usd
            for q in session.scalars(
                select(Quote).where(Quote.internal_cost_usd.is_not(None))
            ).all()
        }
        contacts = {
            cm.value
            for cm in session.scalars(select(ContactMethod)).all()
            if cm.value and len(cm.value) >= 6
        }

    problems = []
    for path, body in all_payloads().items():
        for loc, val in leaves(body):
            if any(loc.startswith(a) for a in ALLOWED_VALUE_PATHS):
                continue
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                segment = loc.rsplit(".", 1)[-1].split("[")[0]
                is_tally = any(f in loc for f in COUNT_FIELDS) or segment in COUNT_FIELDS
                if float(val) in costs and not is_tally:
                    problems.append(f"cost {val} at {path}{loc}")
            elif isinstance(val, str):
                # bio 是候选自己的公开简介，原样透传。里面出现 "$250K on X"
                # 这类自述金额是别人的文案，不是 Mango 的成本 —— 金额检查针对的
                # 是**我们生成的句子**（上一代产品的成本正是这样进了推荐理由）。
                if loc.endswith(".bio"):
                    continue
                for match in MONEY.findall(val):
                    if float(match.lstrip("$ ").replace(",", "")) in costs:
                        problems.append(f"cost in prose {match!r} at {path}{loc}")
                for contact in contacts:
                    if contact in val:
                        problems.append(f"contact {contact!r} at {path}{loc}")
    assert not problems, problems


@pytest.mark.parametrize("path", ["/api/targets?group=nope", "/api/candidates?group=nope"])
def test_unknown_group_404(path):
    assert client.get(path).status_code == 404


# ---------------------------------------------------------------------------
# 回归：以下每一条都对应一个真实出现过的 bug
# ---------------------------------------------------------------------------


def test_session_id_is_server_issued():
    """会话 id 不能由调用方指定。

    曾经 ``PUT /api/sessions/{任意字符串}`` 会顺手创建，于是 ``acme-2026``
    这种能猜的 id 谁都能读、能覆盖 —— 而这一层没有 per-client token，
    链接本身就是凭证，凭证不能让调用方自己起名。
    """
    assert client.put("/api/sessions/guessable-name", json={}).status_code == 404
    assert client.get("/api/sessions/guessable-name").status_code == 404

    created = client.post("/api/sessions")
    assert created.status_code == 201
    sid = created.json()["id"]
    assert len(sid) >= 12
    assert client.put(f"/api/sessions/{sid}", json={"prefs": {"group": "ai"}}).status_code == 200


def test_submitted_session_is_immutable():
    sid = client.post("/api/sessions").json()["id"]
    client.put(f"/api/sessions/{sid}", json={"prefs": {"group": "ai"}})
    client.post(f"/api/sessions/{sid}/submit")
    assert client.put(f"/api/sessions/{sid}", json={"prefs": {"group": "crypto"}}).status_code == 409


def test_focus_capped_at_three():
    sid = client.post("/api/sessions").json()["id"]
    body = client.put(
        f"/api/sessions/{sid}", json={"focus": ["a", "b", "c", "d"]}
    ).json()
    assert len(body["focus"]) == 3
    assert body["focus"] == ["b", "c", "d"]  # 挤掉最早的
    assert "notice" in body


def test_filters_reach_discovered_side():
    """筛选必须对**两侧**生效。

    发现候选一度完全不参与筛选：客户换市场、换方向，一百多位新发现纹丝不动。
    画像由公开简介规则化推导（catalog.profile_from_bio），有值就参与匹配。
    """
    base = client.get("/api/candidates?group=ai&limit=300").json()["counts"]
    narrowed = client.get("/api/candidates?group=ai&limit=300&markets=cn").json()["counts"]
    assert narrowed["discovered"] < base["discovered"]


def test_unknown_profile_is_kept_but_counted():
    """判不出画像的候选保留，但必须**数出来**。

    否则筛了方向、名单里却留着一批方向未知的人，界面上一个字不说 ——
    那是用沉默冒充匹配。
    """
    body = client.get("/api/candidates?group=ai&limit=300&audiences=developers").json()
    # 人群刻意不做推断，所以发现侧全部属于"该维度待确认"
    assert body["counts"]["keptAsUnknown"] > 0


def test_discover_returns_full_records():
    """``/api/discover`` 曾经传 account=None，158 条全部 followers/bio 为空、
    name 退化成 handle。接口 200、条数对，内容是空壳。"""
    items = client.post("/api/discover", json={"group": "ai"}).json()["items"]
    assert items
    assert sum(1 for i in items if i["followers"] is not None) > len(items) // 2


def test_brief_prefills_real_columns():
    """``/api/brief`` 曾经读 market_regions / languages / audience_types ——
    三个都不是 Brief 的列，配合 getattr 兜底静默返回 None，预填永远是空的。"""
    body = client.get("/api/brief/demo")
    if body.status_code == 404:
        pytest.skip("库里没有 Brief 记录")
    prefs = body.json()["prefs"]
    assert set(prefs) >= {"group", "domains", "markets", "languages", "audiences", "budget"}


def test_vocabulary_is_translated():
    """前端词表（us / ai_agent）和库内词表（europe_america / ai）不重叠，
    不翻译就是拿两个不相交的集合求交集，任何筛选恒等于 0。"""
    us = client.get("/api/candidates?group=ai&limit=300&markets=us").json()["counts"]
    assert us["priced"] > 0
    agent = client.get("/api/candidates?group=ai&limit=300&domains=ai_agent").json()["counts"]
    assert agent["priced"] > 0


def test_brief_codes_are_not_enumerable():
    """Brief 码不能是自增主键。

    ``/api/brief/1`` 一度可用，于是从 1 数上去就能读到别的客户的项目名、方向、
    市场与预算档 —— 这个接口没有鉴权，码本身就是凭证。
    """
    for guess in ("1", "2", "3", "10"):
        assert client.get(f"/api/brief/{guess}").status_code == 404
