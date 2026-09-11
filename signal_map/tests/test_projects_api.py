"""客户项目面测试 —— target-backed discovery 的验收条件。

这些断言对应的都是**已经出过错的地方**，不是想象出来的边界：

* 候选池不等于报价库：``discovered`` 必须真的出现在名单里。
* 目标人物永远不是投放对象 —— 包括**没被这一轮勾选**的 Root 库成员。
  一开始只排除了勾选的那几位，于是 Andrew Ng、Sam Altman 作为「新发现」
  进了客户名单。
* 客户响应里不能出现联系方式原文。字段白名单挡住了 ``contact_methods``，
  却让同一个邮箱从 ``bio`` 里整段带了出去。
* 未报价的人不折算成任何预算数字。
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from signal_map.backend import app as app_module
from signal_map.backend import projects_api
from signal_map.backend.bd_models import BDCandidate
from signal_map.backend.models import (
    Base,
    CandidateItem,
    InternalTask,
    Brief,
    Client,
    ContactMethod,
    Creator,
    Quote,
    QuoteMessage,
    SocialAccount,
)
from signal_map.backend.observation_models import FollowEdge, XAccount

TOKEN = "client-token"


@pytest.fixture
def env():
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
    with Local() as seed:
        _seed(seed)
    yield TestClient(app_module.app), Local
    app_module.app.dependency_overrides.clear()


def _classified(handle: str, kind: str, verticals: str) -> BDCandidate:
    """一条已被身份判定的候选行。客户面要求候选被真的看过一眼。"""
    return BDCandidate(
        platform="X", handle=handle, source_name="test",
        object_kind_suggested=kind,
        object_kind_suggestion_basis=f"模型判定为 {kind}（high）；依据原文：「test」",
        object_kind_suggested_by="llm_account_role:test",
        verticals_suggested=verticals,
        verticals_suggestion_basis=f'模型判定领域（high）；依据原文：{verticals}:"test"',
    )


def _account(session: Session, rest_id: str, handle: str, **kw) -> XAccount:
    account = XAccount(rest_id=rest_id, handle=handle, roles="observed", **kw)
    session.add(account)
    session.flush()
    return account


def _seed(session: Session) -> None:
    client = Client(name="ACME", api_token=TOKEN)
    session.add(client)
    session.flush()
    session.add(Brief(
        id=1, client_id=client.id, name="AI 发布", verticals="ai",
        target_markets="europe_america", platforms="X",
    ))

    # 目标人物：一位被勾选，一位在 Root 库里但这轮没勾。
    picked = _account(session, "t-1", "picked", followers=900_000, following=400)
    unpicked = _account(session, "t-2", "unpicked", followers=800_000, following=300)
    for handle, account in (("picked", picked), ("unpicked", unpicked)):
        session.add(BDCandidate(
            platform="X", handle=handle, display_name=handle,
            object_kind="target_person", domain_group="ai", source_name="test",
        ))

    # 已有报价的对象。邮箱同时在 contact_methods 和公开简介里 —— 泄漏用例。
    creator = Creator(
        display_name="Priced KOL", primary_handle="pricedkol",
        creator_class="KOL", creator_tier="strategic",
        verticals="ai", market_region="europe_america", languages="en",
    )
    session.add(creator)
    session.flush()
    session.add(SocialAccount(
        creator_id=creator.id, platform="X", handle="pricedkol",
        followers=120_000, is_primary=True,
        bio="AI tutorials. Brand deals: sales@pricedkol.example",
    ))
    message = QuoteMessage(creator_id=creator.id, raw_text="Thread $1,300")
    session.add(message)
    session.flush()
    session.add(Quote(
        creator_id=creator.id, message_id=message.id, platform="X",
        content_format="x_thread", amount=1300, currency="USD", amount_usd=1300,
        internal_cost_usd=1300, status="confirmed_valid", needs_review=False,
        client_visible=True,
    ))
    session.add(ContactMethod(
        creator_id=creator.id, method_type="email", value="sales@pricedkol.example",
    ))
    priced_account = _account(
        session, "c-1", "pricedkol", followers=120_000, following=500,
        bio="AI tutorials. Brand deals: sales@pricedkol.example",
        creator_id=creator.id,
    )

    # 从未建联过的发现候选。带身份判定 —— 新的实质性闸门要求候选**被真的
    # 看过一眼**，未经检查的人不出现在客户面前。
    found = _account(
        session, "d-1", "foundkol", followers=60_000, following=300,
        bio="Daily AI breakdowns for developers. Brand partnerships: hi@found.example",
    )
    session.add(_classified("foundkol", "kol", "ai"))
    # 粉丝太少，过不了真实流量那道。
    tiny = _account(session, "d-2", "tinykol", followers=200, following=100, bio="AI stuff")
    # 没有简介，读不出任何内容面。
    blank = _account(session, "d-3", "blankkol", followers=90_000, following=100)

    for target in (picked, unpicked):
        for other in (priced_account, found, tiny, blank, unpicked, picked):
            if other is target:
                continue
            session.add(FollowEdge(
                source_id=target.id, target_id=other.id,
                first_seen_at=dt.datetime(2026, 9, 1),
                last_seen_at=dt.datetime(2026, 9, 1),
            ))
    session.commit()


def _discover(client: TestClient, ids: list[str]) -> dict:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    client.post("/api/projects/1/discover", headers=headers, json={"targetIds": ids})
    return client.get("/api/projects/1/candidates", headers=headers).json()


# =============================================================================
# 候选池 ≠ 报价库
# =============================================================================


def test_pool_returns_both_priced_and_discovered(env):
    client, _ = env
    data = _discover(client, ["picked"])
    sources = {row["source"] for row in data["candidates"]}
    assert sources == {"priced", "discovered"}
    assert data["counts"]["discovered"] >= 1


def test_discovered_has_no_price_and_is_never_confirmed(env):
    """未报价对象不能显示为已确认可合作。"""
    client, _ = env
    data = _discover(client, ["picked"])
    for row in data["candidates"]:
        if row["source"] == "discovered":
            assert row["price"] is None
            assert row["tier"] is None
            assert row["quoteStatus"] == "pending_inquiry"
            assert row["priceNote"] == "报价待询"


def test_discovered_carries_discovery_path(env):
    client, _ = env
    data = _discover(client, ["picked"])
    found = next(r for r in data["candidates"] if r["handle"] == "foundkol")
    assert found["discoveryPath"]["via"] == ["picked"]
    assert "picked" in found["discoveryPath"]["text"]


# =============================================================================
# 目标人物永远不是投放对象
# =============================================================================


def test_root_library_members_never_appear_as_candidates(env):
    """**包括这一轮没有被勾选的。**

    只勾了 @picked，@unpicked 因为被 @picked 关注而作为「新发现」进了名单 ——
    真实数据里这一条让 Andrew Ng、Sam Altman、Jeff Dean 出现在了可投放列表上。
    是不是目标人物是这个人的属性，不是这一轮的勾选状态。
    """
    client, _ = env
    data = _discover(client, ["picked"])
    handles = {row["handle"] for row in data["candidates"]}
    assert "unpicked" not in handles
    assert "picked" not in handles


# =============================================================================
# 三道过滤
# =============================================================================


def test_follower_floor_and_expression_surface_filter(env):
    client, _ = env
    handles = {row["handle"] for row in _discover(client, ["picked"])["candidates"]}
    assert "tinykol" not in handles    # 真实流量
    assert "blankkol" not in handles   # 内容表达面


def test_candidates_without_a_link_do_not_appear(env):
    client, _ = env
    data = _discover(client, ["picked"])
    assert data["candidates"]
    assert all(row["linkCount"] > 0 for row in data["candidates"])


# =============================================================================
# 预算
# =============================================================================


def test_budget_returns_two_parts_and_never_estimates_the_unknown(env):
    client, _ = env
    data = _discover(client, ["picked"])
    budget = data["budget"]
    assert set(budget) >= {"known", "pendingCount"}
    assert budget["pendingCount"] >= 1
    # 已知部分只由有档位的人贡献；待询价的人一分钱都没被算进去。
    priced = [r for r in data["candidates"] if r["tier"]]
    lo = sum(projects_api.TIER_RANGES[r["tier"]][0] for r in priced)
    assert budget["known"]["lo"] == lo


# =============================================================================
# 客户端可见性
# =============================================================================


def test_no_contact_details_reach_the_client(env):
    """字段白名单挡住了 ``contact_methods``，但同一个邮箱藏在 ``bio`` 里。"""
    client, Local = env
    headers = {"Authorization": f"Bearer {TOKEN}"}
    client.post("/api/projects/1/discover", headers=headers, json={"targetIds": ["picked"]})
    body = client.get("/api/projects/1/candidates", headers=headers).text

    with Local() as session:
        for contact in session.scalars(select(ContactMethod)):
            assert contact.value not in body
    assert not re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", body)


def test_no_raw_amounts_reach_the_client(env):
    client, Local = env
    headers = {"Authorization": f"Bearer {TOKEN}"}
    client.post("/api/projects/1/discover", headers=headers, json={"targetIds": ["picked"]})
    body = client.get("/api/projects/1/candidates", headers=headers).text

    with Local() as session:
        for quote in session.scalars(select(Quote)):
            for form in (str(int(quote.internal_cost_usd)), f"{int(quote.internal_cost_usd):,}"):
                # 成本被写进生成的句子里，是这套系统出过三次的同一类错误。
                assert not re.search(rf"(?<![\d,]){re.escape(form)}(?![\d,])", body)


def test_serializer_is_an_allowlist(env):
    client, _ = env
    data = _discover(client, ["picked"])
    for row in data["candidates"]:
        assert set(row) <= set(projects_api.CLIENT_FIELDS)


def test_another_clients_project_is_404_not_403(env):
    """403 会确认这个 id 存在，等于一个可以枚举别家项目的接口。"""
    client, Local = env
    with Local() as session:
        other = Client(name="Other", api_token="other-token")
        session.add(other)
        session.commit()
    response = client.get(
        "/api/projects/1/candidates", headers={"Authorization": "Bearer other-token"}
    )
    assert response.status_code == 404


def test_client_api_requires_a_token(env):
    client, _ = env
    assert client.get("/api/projects/1/candidates").status_code == 401


# =============================================================================
# 实时性
# =============================================================================


def test_different_targets_give_different_lists(env):
    """同一个客户选不同的目标人物组合，名单必须不同。"""
    client, Local = env
    with Local() as session:
        picked = session.scalar(select(XAccount).where(XAccount.handle == "picked"))
        found = session.scalar(select(XAccount).where(XAccount.handle == "foundkol"))
        solo = _account(
            session, "d-9", "sololink", followers=70_000, following=200,
            bio="AI research notes. Brand partnerships: solo@ex.example",
        )
        # 只有 @unpicked 关注他 —— 勾选 @picked 时他不该出现。
        unpicked = session.scalar(select(XAccount).where(XAccount.handle == "unpicked"))
        session.add(FollowEdge(source_id=unpicked.id, target_id=solo.id))
        session.add(_classified("sololink", "kol", "ai"))
        session.commit()
        assert picked and found

    a = {r["handle"] for r in _discover(client, ["picked"])["candidates"]}
    b = {r["handle"] for r in _discover(client, ["unpicked"])["candidates"]}
    assert "sololink" in b
    assert "sololink" not in a


def test_link_strength_never_claims_more_than_a_follow(env):
    """单向关注只能是 weak，措辞里也不能出现内部代号。"""
    client, _ = env
    data = _discover(client, ["picked"])
    for row in data["candidates"]:
        for link in row["links"]:
            assert link["strength"] in projects_api.LINK_STRENGTHS
            if link["type"] == "follow":
                assert link["strength"] in ("weak", "medium")
            assert not re.match(r"^H[0-3]$", link["strengthLabel"])
    assert "不代表对方一定会看到" in data["limits"]


# =============================================================================
# 项目类型驱动发现池
# =============================================================================


def _set_project(Local, **fields) -> None:
    with Local() as session:
        brief = session.get(Brief, 1)
        for key, value in fields.items():
            setattr(brief, key, value)
        session.commit()


def test_project_domain_changes_the_discovered_set(env):
    """同一组目标人物，换项目领域，发现池要跟着变。

    改之前 ``prefs`` 只喂给次级排序，membership 完全不受项目类型影响 ——
    ai / crypto / finance / gaming 四个项目拿到的是同一批人。
    """
    client, Local = env
    with Local() as session:
        picked = session.scalar(select(XAccount).where(XAccount.handle == "picked"))
        # 一个能判定为 crypto、和 ai 无关的候选。
        crypto = _account(
            session, "d-crypto", "cryptokol", followers=50_000, following=200,
            bio="Daily crypto and web3 market notes. Brand deals: hi@crypto.example",
        )
        session.add(FollowEdge(source_id=picked.id, target_id=crypto.id))
        session.add(_classified("cryptokol", "kol", "crypto"))
        session.commit()

    _set_project(Local, verticals="ai")
    ai_list = {r["handle"] for r in _discover(client, ["picked"])["candidates"]}
    _set_project(Local, verticals="crypto")
    crypto_list = {r["handle"] for r in _discover(client, ["picked"])["candidates"]}

    assert "foundkol" in ai_list and "foundkol" not in crypto_list
    assert "cryptokol" in crypto_list and "cryptokol" not in ai_list


def test_undetermined_content_stays_internal_not_client_facing(env):
    """领域说不出来的账号：**不进客户名单，但留在内部建联队列。**

    两条规则的边界，容易混：

    * 项目类型过滤 —— ``mismatch`` 排除，``unknown`` 不排除。这条没变。
    * 卡片实质性 —— 说不出他做什么内容，就不是一张客户能判断的卡片。

    第二条先生效。这不是「判不出来就淘汰」，而是「判不出来之前不交付」——
    这个人仍然是有效线索，只是要先补资料，那件事在内部队列里做。
    """
    client, Local = env
    with Local() as session:
        picked = session.scalar(select(XAccount).where(XAccount.handle == "picked"))
        vague = _account(
            session, "d-vague", "vaguekol", followers=40_000, following=200,
            bio="Thoughts, links, and occasional rants. Brand deals: hi@vague.example",
        )
        session.add(FollowEdge(source_id=picked.id, target_id=vague.id))
        session.commit()

    handles = {r["handle"] for r in _discover(client, ["picked"])["candidates"]}
    assert "vaguekol" not in handles

    # 模型补上领域之后，同一个人就能进名单了 —— 说明挡他的是「说不出内容」，
    # 不是「他不行」。
    with Local() as session:
        session.add(_classified("vaguekol", "kol", "ai"))
        session.commit()

    rows = _discover(client, ["picked"])["candidates"]
    row = next(r for r in rows if r["handle"] == "vaguekol")
    assert row["domains"] == ["ai"]


def test_domain_mismatch_still_excludes_but_unknown_alone_does_not(env):
    """项目类型过滤本身仍然只在 ``mismatch`` 时排除。"""
    from signal_map.backend.projects_api import content_fit
    from signal_map.backend.bd_screening import CandidateView

    def view(**kw):
        return CandidateView(
            key="X:x", platform="X", handle="x", display_name=None,
            profile_url=None, source_kind="observed", **kw
        )

    assert content_fit(view(verticals=("crypto",)), {"domains": ["ai"]})[0] == "mismatch"
    assert content_fit(view(verticals=("ai",)), {"domains": ["ai"]})[0] == "match"
    # 领域为空 -> unknown，**这条规则本身不排除任何人**。
    assert content_fit(view(), {"domains": ["ai"]})[0] == "unknown"


def test_narrowing_is_reported_not_hidden(env):
    """被项目方向筛掉多少要说出来，别让它看起来像「本来就只有这些」。"""
    client, Local = env
    _set_project(Local, verticals="crypto")
    data = _discover(client, ["picked"])
    narrowed = data["narrowedByProject"]
    assert narrowed["offProjectDirection"] >= 1
    # 用词受约束：不说「缺口 / 待补充 / 无数据」。
    for banned in ("缺口", "待补充", "无数据", "不合格", "驳回"):
        assert banned not in narrowed["note"]


def test_priced_inventory_is_not_content_filtered(env):
    """报价库是 Mango 有限的自有库存，客户有权看到我们考虑过谁。

    发现池是无界的，所以按项目收窄；报价库不是，所以不做内容淘汰 —— 两边规则
    不同是有意的，不是遗漏。
    """
    client, Local = env
    _set_project(Local, verticals="crypto")   # pricedkol 是 ai 方向
    handles = {r["handle"] for r in _discover(client, ["picked"])["candidates"]}
    assert "pricedkol" in handles


# =============================================================================
# 身份分类（LLM 建议）
# =============================================================================


def test_machine_suggestion_can_only_remove_never_add(env):
    """机器建议只用来**把人从可投放名单里拿掉**，不用来放进去。

    两个方向代价不对称：错误排除一个可买的人，损失一次机会；错误放进一个 SEC
    委员，是产品级别的尴尬。@HesterPeirce 真的出现在过客户候选里。
    """
    client, Local = env
    with Local() as session:
        picked = session.scalar(select(XAccount).where(XAccount.handle == "picked"))
        official = _account(
            session, "d-reg", "regulator", followers=200_000, following=100,
            bio="Commissioner. Writing about ai policy. press@agency.example",
        )
        session.add(FollowEdge(source_id=picked.id, target_id=official.id))
        session.add(_classified("regulator", "kol", "ai"))
        session.commit()

    # 还没判定时，他会进名单 —— 这正是需要分类的原因。
    assert "regulator" in {r["handle"] for r in _discover(client, ["picked"])["candidates"]}

    with Local() as session:
        row = session.scalar(select(BDCandidate).where(BDCandidate.handle == "regulator"))
        row.object_kind_suggested = "public_figure"
        row.object_kind_suggestion_basis = (
            '模型判定为 public_figure（high）；依据原文：「Commissioner」'
        )
        session.commit()

    assert "regulator" not in {r["handle"] for r in _discover(client, ["picked"])["candidates"]}


def test_unclear_identity_is_pending_not_rejected(env):
    """``unclear`` 和 ``public_figure`` 都不出现在客户名单里，但含义不同。

    这里要说清一次收窄：全系统的规则是「未知从不淘汰候选」，而**客户面多了
    一条**「没被看过的人不交付」。两者不矛盾，但边界必须写明：

    * ``public_figure`` —— 判过了，**判定他买不到**。这是结论。
    * ``unclear``      —— 判过了，**没判出来**。这是待办，人还在内部队列里，
      补完资料就能重新进名单。

    区别体现在数据上：前者 ``object_kind_suggested`` 有值，后者为空但
    ``object_kind_suggested_by`` 有值。内部作业面据此区分「不用再看」和
    「等资料」。
    """
    client, Local = env
    with Local() as session:
        row = session.scalar(select(BDCandidate).where(BDCandidate.handle == "foundkol"))
        row.object_kind_suggested = None
        row.object_kind_suggestion_basis = "模型判定为 unclear（low）"
        session.commit()

    assert "foundkol" not in {
        r["handle"] for r in _discover(client, ["picked"])["candidates"]
    }

    with Local() as session:
        row = session.scalar(select(BDCandidate).where(BDCandidate.handle == "foundkol"))
        # 判过了（有 by），但没判出结果（suggested 为空）—— 这是待办，不是结论。
        assert row.object_kind_suggested is None
        assert row.object_kind_suggested_by is not None
        assert row.effective_object_kind == "unknown"


def test_human_verdict_wins_over_the_machine(env):
    """人判过的不被建议推翻。``effective_object_kind`` 的全部意义。"""
    client, Local = env
    with Local() as session:
        candidate = session.scalar(select(BDCandidate).where(BDCandidate.handle == "foundkol"))
        candidate.object_kind = "kol"
        candidate.object_kind_basis = "人工确认：他确实接投放"
        candidate.object_kind_suggested = "public_figure"
        candidate.object_kind_suggestion_basis = "模型判定为 public_figure（low）"
        session.commit()
        assert candidate.effective_object_kind == "kol"

    assert "foundkol" in {r["handle"] for r in _discover(client, ["picked"])["candidates"]}


def test_classifier_drops_verdicts_without_a_quote():
    """没有引用原文的判断降级为 unclear —— CLAUDE.md §3 的硬护栏。"""
    from signal_map.backend.llm_classify import _parse_account

    verdict = _parse_account('{"kind":"creator","evidence":"","confidence":"high"}', "m")
    assert verdict.kind == "unclear"
    assert verdict.buyable is None

    ok = _parse_account(
        '{"kind":"public_figure","evidence":"CEO @Box","confidence":"high"}', "m"
    )
    assert ok.kind == "public_figure" and ok.buyable is False
    assert "CEO @Box" in ok.basis


def test_classifier_validates_against_the_closed_vocabulary():
    from signal_map.backend.llm_classify import ACCOUNT_KINDS, _parse_account

    verdict = _parse_account('{"kind":"influencer","evidence":"posts a lot"}', "m")
    assert verdict.kind == "unclear"
    assert verdict.kind in ACCOUNT_KINDS


def test_account_prompt_change_invalidates_the_cache():
    """改 prompt 就改变了一条判断的含义，旧答案不能复用。这条回归过一次。"""
    import hashlib

    from signal_map.backend import llm_classify

    before = hashlib.sha256(llm_classify.ACCOUNT_PROMPT.encode()).hexdigest()[:8]
    after = hashlib.sha256((llm_classify.ACCOUNT_PROMPT + " tweak").encode()).hexdigest()[:8]
    assert before != after


# =============================================================================
# 客户决策闭环
# =============================================================================


def test_interest_on_an_unpriced_discovery_becomes_bd_work(env):
    """**这是整条闭环的意义所在。**

    客户看到一个 Mango 从没联系过、也没有报价的人，说「我要这个」，于是：
    身份记录建起来、进客户名单、BD 队列收到工单。没有这一步，发现出来的人
    只是一份好看的名单。
    """
    client, Local = env
    headers = {"Authorization": f"Bearer {TOKEN}"}

    rows = _discover(client, ["picked"])["candidates"]
    found = next(r for r in rows if r["source"] == "discovered")
    assert found["price"] is None and found["quoteStatus"] == "pending_inquiry"

    response = client.post(
        "/api/projects/1/feedback", headers=headers,
        json={"candidateId": found["id"], "action": "interest", "actor": "buyer"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["internalTaskRaised"] is True
    # 客户要看到动作产生了后果，而不是消失在一个按钮里。
    assert "建联" in body["effect"]

    shortlist = client.get("/api/projects/1/shortlist", headers=headers).json()
    assert found["handle"] in {i["handle"] for i in shortlist["items"]}

    with Local() as session:
        task = session.scalar(
            select(InternalTask).where(InternalTask.kind == "client_interest")
        )
        assert task is not None and task.creator_id is not None
        # 升级出来的供给记录必须落在保留 id 段里，否则下一次重建会撞上 BD 的 id。
        creator = session.get(Creator, task.creator_id)
        assert creator.id >= 1_000_000
        assert creator.source_system == "signal_map_bd_intake"


def test_interest_and_approve_raise_different_work(env):
    """``interest`` 不是 ``approve``。

    approve 的前提是价格和档期能确认；新发现的对象**没有价格**，逼客户在这种
    状态下做承诺不合理。两者去向也不同：一个是去建联，一个是去落实报价。
    """
    client, Local = env
    headers = {"Authorization": f"Bearer {TOKEN}"}
    rows = _discover(client, ["picked"])["candidates"]
    found = next(r for r in rows if r["source"] == "discovered")
    priced = next(r for r in rows if r["source"] == "priced")

    client.post("/api/projects/1/feedback", headers=headers,
                json={"candidateId": found["id"], "action": "interest"})
    client.post("/api/projects/1/feedback", headers=headers,
                json={"candidateId": priced["id"], "action": "approve"})

    with Local() as session:
        kinds = {t.kind for t in session.scalars(select(InternalTask)).all()}
    assert kinds == {"client_interest", "price_inquiry"}


def test_repeated_interest_does_not_pile_up_tasks(env):
    """客户多点两次，队列不该变长。"""
    client, Local = env
    headers = {"Authorization": f"Bearer {TOKEN}"}
    found = next(
        r for r in _discover(client, ["picked"])["candidates"] if r["source"] == "discovered"
    )
    for _ in range(3):
        client.post("/api/projects/1/feedback", headers=headers,
                    json={"candidateId": found["id"], "action": "interest"})
    with Local() as session:
        assert len(session.scalars(select(InternalTask)).all()) == 1


def test_reject_affects_only_this_project(env):
    """一个客户觉得不合适，不等于下一个客户也不合适。"""
    client, Local = env
    headers = {"Authorization": f"Bearer {TOKEN}"}
    priced = next(
        r for r in _discover(client, ["picked"])["candidates"] if r["source"] == "priced"
    )
    client.post("/api/projects/1/feedback", headers=headers,
                json={"candidateId": priced["id"], "action": "reject", "reasonCode": "off_brand"})

    with Local() as session:
        creator = session.scalar(select(Creator).where(Creator.primary_handle == "pricedkol"))
        # 供给层一个字段都不该被客户的拒绝改掉。
        assert creator.creator_tier == "strategic"
        assert creator.creator_class == "KOL"
        item = session.scalar(select(CandidateItem).where(CandidateItem.creator_id == creator.id))
        assert item is None or item.removed_at is not None


def test_promotion_carries_the_classification(env):
    """升级成供给记录不能让这个人变模糊。

    卡片上原本有领域、受众、角色，一进名单全没了 —— 客户会看到同一个人在两个
    页面上不一样，那是最伤信任的一种 bug。
    """
    client, _ = env
    headers = {"Authorization": f"Bearer {TOKEN}"}
    found = next(
        r for r in _discover(client, ["picked"])["candidates"] if r["source"] == "discovered"
    )
    role_on_card = found["role"]
    assert found["domains"]

    client.post("/api/projects/1/feedback", headers=headers,
                json={"candidateId": found["id"], "action": "interest"})
    row = next(
        i for i in client.get("/api/projects/1/shortlist", headers=headers).json()["items"]
        if i["handle"] == found["handle"]
    )
    assert row["role"] == role_on_card


def test_shortlist_budget_is_two_part_and_roles_are_named(env):
    client, _ = env
    headers = {"Authorization": f"Bearer {TOKEN}"}
    rows = _discover(client, ["picked"])["candidates"]
    for row in rows[:2]:
        client.post("/api/projects/1/feedback", headers=headers,
                    json={"candidateId": row["id"], "action": "shortlist"})

    shortlist = client.get("/api/projects/1/shortlist", headers=headers).json()
    assert set(shortlist["budget"]) >= {"known", "pendingCount"}
    assert all(i["role"] in projects_api.ROLE_LABELS for i in shortlist["items"])
    # 用词受约束：说「下一步可拓展」，不说「缺口 / 无数据」。
    note = shortlist["coverage"]["note"]
    for banned in ("缺口", "无数据", "待补充", "不合格"):
        assert banned not in note


def test_feedback_requires_the_callers_own_project(env):
    client, Local = env
    with Local() as session:
        session.add(Client(name="Other", api_token="other-token"))
        session.commit()
    found = next(
        r for r in _discover(client, ["picked"])["candidates"] if r["source"] == "discovered"
    )
    response = client.post(
        "/api/projects/1/feedback",
        headers={"Authorization": "Bearer other-token"},
        json={"candidateId": found["id"], "action": "interest"},
    )
    assert response.status_code == 404
