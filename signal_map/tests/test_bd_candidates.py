"""待建联筛选的测试。

比 CRUD 更重要的是这几条，因为它们出错时**看起来是对的**：

* 领域不符时，再好的联系方式也不能把人抬进询价队列（规格明令）。
* 没有联系方式不能删人，只能降到「优先补充商务路径」。
* 关系证据的方向 —— 是目标人物关注了候选，不是反过来。
* 外部名单的评分与分层不参与任何判断。
* 「有邮箱」「接过广告」「确认愿意」三件事分开，且自我推广不算任何一件。
* 同一个账号只有一条身份记录，名单重复导入不会造出第二条。
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from signal_map.backend import app as app_module
from signal_map.backend import bd_discovery, bd_screening
from signal_map.backend.bd_models import BDCandidate
from signal_map.backend.bd_screening import CandidateView, DomainPreferences
from signal_map.backend.models import (
    AttentionSignal,
    Base,
    ContactMethod,
    Creator,
    Quote,
    QuoteMessage,
    SocialAccount,
    SponsorshipEvidence,
)
from signal_map.backend.observation_models import RosterCentrality, XAccount

INTERNAL = "internal-test-token"


# =============================================================================
# 夹具
# =============================================================================


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
    with Local() as seed:
        _seed(seed)
    yield TestClient(app_module.app), Local
    app_module.app.dependency_overrides.clear()


@pytest.fixture
def session(env):
    _, Local = env
    with Local() as s:
        yield s


def _seed(session: Session) -> None:
    """一个够小、但每条规则都能被打到的库。"""
    # 目标人物：已人工确认的 Root。
    root = XAccount(
        rest_id="root-1", handle="realroot", display_name="Real Root",
        followers=900_000, following=300, roles="observed,root",
        root_review_status="accepted", root_type="investor",
    )
    # 已被否决的账号：它的关注信号必须彻底不出现。
    rejected = XAccount(
        rest_id="root-2", handle="rejectedroot", followers=20_000, following=800,
        roles="observed", root_review_status="rejected",
    )
    session.add_all([root, rejected])

    # 已有报价的对象：被目标人物关注。
    priced = Creator(
        display_name="Priced Voice", primary_handle="pricedvoice",
        creator_class="KOL", creator_tier="strategic",
        verticals="ai", market_region="europe_america", languages="en",
        audience_types="developers",
    )
    # 无报价、内容对口、有联系方式和历史合作 -> 应当是「优先询价」。
    ready = Creator(
        display_name="Ready For Quote", primary_handle="readyquote",
        creator_class="KOL", creator_tier="strategic",
        verticals="ai", market_region="europe_america", languages="en",
        audience_types="developers",
    )
    # 内容不相关，但联系方式和历史合作都齐 -> 必须仍然是「仅作目标或观察」。
    offtopic = Creator(
        display_name="Off Topic", primary_handle="offtopic",
        creator_class="KOL", creator_tier="strategic",
        verticals="gaming", market_region="europe_america", languages="en",
    )
    # 内容对口但完全没有联系路径 -> 「优先补充商务路径」，不能被删掉。
    noroute = Creator(
        display_name="No Route", primary_handle="noroute",
        creator_class="KOL", creator_tier="strategic",
        verticals="ai", market_region="europe_america", languages="en",
    )
    session.add_all([priced, ready, offtopic, noroute])
    session.flush()

    for creator, handle in (
        (priced, "pricedvoice"), (ready, "readyquote"),
        (offtopic, "offtopic"), (noroute, "noroute"),
    ):
        session.add(SocialAccount(
            creator_id=creator.id, platform="X", handle=handle,
            followers=100_000, is_primary=True,
        ))

    message = QuoteMessage(creator_id=priced.id, raw_text="X Thread: $1,300")
    session.add(message)
    session.flush()
    session.add(Quote(
        creator_id=priced.id, message_id=message.id, platform="X",
        content_format="x_thread", amount=1300, currency="USD", amount_usd=1300,
        internal_cost_usd=1300, status="confirmed_valid", needs_review=False,
    ))

    for creator in (ready, offtopic):
        session.add(ContactMethod(creator_id=creator.id, method_type="email", value="a@b.c"))
        session.add(SponsorshipEvidence(
            creator_id=creator.id, sponsor_name="SomeBrand",
            evidence_type="paid_sponsorship", review_status="unreviewed",
        ))

    # 关系边：真 Root 关注 priced；被否决的账号也关注 priced。
    for node, creator_id in (("root-1", priced.id), ("root-2", priced.id)):
        session.add(AttentionSignal(
            source_node=node, target_node=f"acct-{creator_id}",
            source_creator_id=creator_id, platform="X", signal_type="follow",
            direction="source_to_target", collected_at=dt.datetime(2026, 9, 1),
            confidence="research_lead",
        ))
    session.commit()


PREFS = {
    "verticals": ["ai"], "market_regions": ["europe_america"],
    "languages": ["en"], "platforms": ["X"],
}


def _screen_all(session: Session, prefs: DomainPreferences) -> dict[str, object]:
    targets = bd_screening.resolve_target_nodes(session, prefs.target_handles)
    views = bd_discovery.build_views(
        session, target_accounts=targets, include_observed=True, min_roster_followers=1
    )
    collected = bd_screening.collected_target_nodes(session, set(targets))
    relations = bd_screening.signals_by_creator(session, set(targets) or None)
    stored = bd_discovery.stored_signals_by_key(session, views)
    return {
        view.handle: bd_screening.screen(
            view, prefs,
            stored_signals=stored.get(view.key, []),
            relation_signals=relations.get(view.creator.id, []) if view.creator else [],
            target_accounts=targets, collected_nodes=collected,
        )
        for view in views
    }


# =============================================================================
# 四档优先级 —— 规格里的硬规则
# =============================================================================


def test_contactable_and_paid_evidence_goes_to_priority_inquiry(session):
    results = _screen_all(session, DomainPreferences.from_dict(PREFS))
    assert results["readyquote"].priority == "priority_inquiry"
    assert results["readyquote"].next_step_kind == "request_quote"


def test_contact_details_cannot_offset_irrelevant_content(session):
    """联系方式不能抵消内容不相关 —— 规格原文。

    ``offtopic`` 的联系方式和历史合作证据都比 ``readyquote`` 齐，唯一的差别
    是内容方向。它必须落在「仅作目标或观察」。
    """
    results = _screen_all(session, DomainPreferences.from_dict(PREFS))
    off = results["offtopic"]
    assert off.priority == "observe_only"
    assert off.domain_fit.verdict == "mismatch"
    # 商务证据仍然要如实展示，只是不参与这个判断。
    assert off.commercial.contactable is True
    assert off.commercial.has_paid_evidence is True


def test_missing_contact_never_removes_a_candidate(session):
    """缺少联系方式不删人，只降到「优先补充商务路径」。"""
    results = _screen_all(session, DomainPreferences.from_dict(PREFS))
    assert "noroute" in results
    assert results["noroute"].priority == "need_bd_path"
    assert results["noroute"].next_step_kind == "find_contact"


def test_target_person_never_enters_the_inquiry_queue(session):
    """投资人/研究者这类目标人物，关系再强也要另行确认商业意愿。"""
    session.add(BDCandidate(
        platform="X", handle="realroot", display_name="Real Root",
        object_kind="target_person", source_name="test",
    ))
    session.commit()
    results = _screen_all(session, DomainPreferences.from_dict(PREFS))
    row = results["realroot"]
    assert row.priority == "observe_only"
    assert "商业合作意愿" in row.priority_reason


def test_no_numeric_fit_score_is_produced(session):
    """没有项目简报时不生成统一的项目匹配排名 —— 所以卡片里不能有匹配分。"""
    results = _screen_all(session, DomainPreferences.from_dict(PREFS))
    payload = bd_screening.screening_dict(results["readyquote"])
    assert "score" not in payload
    assert "rank_score" not in payload
    assert "match_score" not in payload
    assert set(payload["domain_fit"]) == {"verdict", "label_zh", "summary", "axes"}


# =============================================================================
# 商务成熟度 —— 三件事分开
# =============================================================================


def test_three_commercial_facts_stay_separate(session):
    results = _screen_all(session, DomainPreferences.from_dict(PREFS))
    stances = results["readyquote"].commercial.stances
    assert stances["contactable"]["holds"] is True
    assert stances["has_commercial_history"]["holds"] is True
    # 没有人回复过，所以第三件必须是 False —— 它只能来自真实回复。
    assert stances["confirmed_willing"]["holds"] is False


def test_self_promotion_is_not_willingness_to_take_third_party_work():
    """卖自己的课不等于愿意帮别人打广告。"""
    signals = bd_screening.signals_from_bio(
        "AI educator. Join my course on prompt engineering!", source_url=None
    )
    kinds = {s.signal_type for s in signals}
    assert kinds == {"self_promotion_only"}
    assert all(s.stance is None for s in signals)


def test_growth_service_bio_withdraws_the_acceptance_reading():
    """「DM for collabs」是换量说法，不能同时算成承接品牌投放。

    这是实测里真实发生过的误判：只按措辞收，「优先询价」前八名全是简介写着
    DM for Collabs 的同圈互推账号。
    """
    signals = bd_screening.signals_from_bio(
        "AI tips daily | DM for collabs | Brand partnerships welcome", source_url=None
    )
    kinds = {s.signal_type for s in signals}
    assert "accepts_brand_work_statement" not in kinds
    assert "self_promotion_only" in kinds


def test_email_is_read_from_the_bio_never_constructed():
    signals = bd_screening.signals_from_bio(
        "Builder. Business: hi@example.com", source_url="https://x.com/x"
    )
    email = next(s for s in signals if s.signal_type == "business_email")
    assert email.value == "hi@example.com"
    assert email.is_inferred is False
    # 引用原文必须真的出现在简介里，否则日后无法复核。
    assert "hi@example.com" in email.evidence_quote
    # 简介里没有邮箱就不能凭空造一个。
    assert not [
        s for s in bd_screening.signals_from_bio("Just an AI builder", source_url=None)
        if s.signal_type == "business_email"
    ]


def test_unverified_contact_is_reported_as_unverified(session):
    results = _screen_all(session, DomainPreferences.from_dict(PREFS))
    rows = [
        s for s in results["readyquote"].commercial.signals
        if s.get("source") == "contact_methods"
    ]
    assert rows and all(r["verified_at"] is None for r in rows)
    assert all("未核验" in (r["note"] or "") for r in rows)


# =============================================================================
# 关系证据 —— 方向与零结果
# =============================================================================


def test_relation_evidence_is_target_follows_candidate(session):
    prefs = DomainPreferences.from_dict({**PREFS, "target_handles": ["realroot"]})
    results = _screen_all(session, prefs)
    # priced 有报价，所以不在 BD 池里；直接对它建一个视图来检查方向。
    creator = session.scalar(select(Creator).where(Creator.primary_handle == "pricedvoice"))
    targets = bd_screening.resolve_target_nodes(session, prefs.target_handles)
    relations = bd_screening.signals_by_creator(session, set(targets))
    evidence = bd_screening.relation_evidence(
        CandidateView(
            key="X:pricedvoice", platform="X", handle="pricedvoice",
            display_name=None, profile_url=None, source_kind="creator", creator=creator,
        ),
        prefs,
        signals=relations.get(creator.id, []),
        target_accounts=targets,
        collected_nodes=bd_screening.collected_target_nodes(session, set(targets)),
    )
    assert evidence.distinct_targets == 1
    row = evidence.targets[0]
    assert row["target_handle"] == "realroot"
    assert row["direction_text"] == "@realroot 关注了本候选"
    assert row["direction"] == "source_to_target"
    assert "不证明看过、认可或会转发" in evidence.as_dict()["claim_limit"]
    assert results  # 池子本身仍然算得出来


def test_rejected_target_signals_never_reappear(session):
    """人工否掉的账号，其关注信号必须彻底消失，不只是排后面。"""
    creator = session.scalar(select(Creator).where(Creator.primary_handle == "pricedvoice"))
    relations = bd_screening.signals_by_creator(session, None)
    nodes = {s.source_node for s in relations.get(creator.id, [])}
    assert "root-1" in nodes
    assert "root-2" not in nodes


def test_zero_relations_says_not_observed_not_no_relationship(session):
    prefs = DomainPreferences.from_dict({**PREFS, "target_handles": ["realroot"]})
    results = _screen_all(session, prefs)
    note = results["readyquote"].relation.as_dict()["coverage_note"]
    assert "未采集" in note or "未观察到" in note
    assert "无关系" not in note.replace("不代表无关系", "").replace("而非无关系", "")


def test_relation_evidence_ignores_procurement_and_contact_data(session):
    """Mango 能联系到某人，不能当作这个 KOL 影响得了目标大 V 的证据。"""
    prefs = DomainPreferences.from_dict({**PREFS, "target_handles": ["realroot"]})
    results = _screen_all(session, prefs)
    # ready 有邮箱、有历史合作，但没有任何目标关注边。
    assert results["readyquote"].commercial.contactable is True
    assert results["readyquote"].relation.targets == []


# =============================================================================
# 外部名单：待核验输入
# =============================================================================


def test_source_list_score_and_tier_never_drive_the_verdict(session):
    """附件的评分和分层是输入，不是结论。

    给一个内容明显不符的对象挂上最高的外部评分，它仍然必须落在观察档。
    """
    creator = session.scalar(select(Creator).where(Creator.primary_handle == "offtopic"))
    session.add(BDCandidate(
        platform="X", handle="offtopic", creator_id=creator.id,
        source_name="boss_sample", source_score_raw="99.9",
        source_tier_raw="A_paid_reachable", source_notes_raw="ilands_fit=high",
    ))
    session.commit()

    results = _screen_all(session, DomainPreferences.from_dict(PREFS))
    row = results["offtopic"]
    assert row.priority == "observe_only"

    payload = bd_screening.screening_dict(row)
    claims = payload["source_claims"]
    assert claims["status"] == "unverified"
    assert claims["score_raw"] == "99.9"
    assert "未参与本页任何判断" in claims["warning"]
    # 名单的说法不能渗进任何一条判断依据。
    assert "99.9" not in row.priority_reason
    assert not any("99.9" in (a.detail or "") for a in row.domain_fit.axes)


def test_ingest_reuses_the_existing_identity_record(session):
    """同一账号复用身份记录，重复导入不产生第二条 Creator。"""
    from signal_map.scripts.ingest_bd_candidates import IncomingCandidate, ingest

    before = session.scalar(select(Creator).where(Creator.primary_handle == "readyquote"))
    row = IncomingCandidate(
        platform="X", handle="ReadyQuote", display_name="Ready For Quote",
        source_ref="row1", tier_raw="S",
    )
    first = ingest(session, "list_a", [row], dry_run=False)
    second = ingest(session, "list_b", [row], dry_run=False)

    assert first["new"] == 1 and second["new"] == 0 and second["updated"] == 1
    assert first["linked_to_creator"] == 1
    creators = session.scalars(
        select(Creator).where(Creator.primary_handle == "readyquote")
    ).all()
    assert len(creators) == 1 and creators[0].id == before.id

    candidates = session.scalars(
        select(BDCandidate).where(BDCandidate.handle == "readyquote")
    ).all()
    assert len(candidates) == 1
    assert candidates[0].creator_id == before.id


# =============================================================================
# 报价衔接
# =============================================================================


def test_quote_intake_appends_and_never_exposes_cost_as_client_price(env):
    client, Local = env
    headers = {"Authorization": f"Bearer {INTERNAL}"}
    response = client.post(
        "/api/internal/bd/candidates/X/readyquote/quotes",
        headers=headers,
        json={
            "actor": "fiona", "raw_text": "Thread $800", "amount": 800,
            "currency": "USD", "platform": "X", "content_format": "x_thread",
            "quote_source": "kol_direct",
        },
    )
    assert response.status_code == 201
    state = response.json()["quote_state"]
    assert state["has_priced_quote"] is True
    assert state["in_client_pool"] is True

    with Local() as check:
        quote = check.scalar(select(Quote).order_by(Quote.id.desc()))
        assert quote.internal_cost_usd == 800
        # 成本绝不能被抄成对客价，也不能自动对客可见。
        assert quote.client_price_usd is None
        assert quote.client_visible is False
        assert quote.client_price_band is not None
        # 原文必存，解析永远可以重来。
        assert check.get(QuoteMessage, quote.message_id).raw_text == "Thread $800"


def test_second_quote_does_not_overwrite_the_first(env):
    client, Local = env
    headers = {"Authorization": f"Bearer {INTERNAL}"}
    for amount in (800, 950):
        client.post(
            "/api/internal/bd/candidates/X/readyquote/quotes", headers=headers,
            json={"actor": "fiona", "raw_text": f"Thread ${amount}", "amount": amount},
        )
    with Local() as check:
        creator = check.scalar(select(Creator).where(Creator.primary_handle == "readyquote"))
        amounts = sorted(q.amount_usd for q in check.scalars(
            select(Quote).where(Quote.creator_id == creator.id)
        ))
    assert amounts == [800, 950]


def test_priced_creator_is_not_in_the_bd_queue(session):
    """已有报价的对象走客户筛选池，不再出现在待建联队列里。"""
    results = _screen_all(session, DomainPreferences.from_dict(PREFS))
    assert "pricedvoice" not in results


# =============================================================================
# 证据与权限
# =============================================================================


def test_commercial_signal_requires_a_source(env):
    client, _ = env
    headers = {"Authorization": f"Bearer {INTERNAL}"}
    response = client.post(
        "/api/internal/bd/candidates/X/readyquote/commercial-signals",
        headers=headers,
        json={"actor": "fiona", "signal_type": "third_party_sponsorship"},
    )
    assert response.status_code == 422


def test_confirmed_willingness_must_be_human_verified(env):
    client, _ = env
    headers = {"Authorization": f"Bearer {INTERNAL}"}
    body = {
        "actor": "fiona", "signal_type": "confirmed_willing_reply",
        "evidence_quote": "邮件回复：可以合作", "verified": False,
    }
    assert client.post(
        "/api/internal/bd/candidates/X/readyquote/commercial-signals",
        headers=headers, json=body,
    ).status_code == 422

    body["verified"] = True
    ok = client.post(
        "/api/internal/bd/candidates/X/readyquote/commercial-signals",
        headers=headers, json=body,
    )
    assert ok.status_code == 201
    stances = ok.json()["commercial_maturity"]["stances"]
    assert stances["confirmed_willing"]["holds"] is True


def test_bd_routes_are_behind_the_internal_guard(env):
    """新增的每一条 BD 路由都必须要 token —— 这里有成本和联系方式。"""
    client, _ = env
    paths = [
        path for path in app_module.app.openapi()["paths"]
        if path.startswith("/api/internal/bd")
    ]
    assert paths, "BD router is not mounted"
    for path in paths:
        for method in ("get", "post", "patch"):
            if method not in app_module.app.openapi()["paths"][path]:
                continue
            url = path.replace("{platform}", "X").replace("{handle}", "readyquote")
            response = client.request(method, url, json={})
            assert response.status_code == 401, f"{method} {path} is unguarded"


def test_client_api_never_serves_bd_candidate_data(env):
    """客户接口不引用 BD 层的任何东西 —— 联系方式和供应商身份永远内部可见。"""
    for path in app_module.app.openapi()["paths"]:
        assert not path.startswith("/api/client/bd")
    from signal_map.backend import client_safe

    source = client_safe.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert "bd_screening" not in text
    assert "bd_models" not in text
    assert "CommercialSignal" not in text


# =============================================================================
# 观察层发现
# =============================================================================


def test_discovery_basis_is_not_relation_evidence(session):
    """「我们的创作者关注了他」不能冒充「目标人物关注了他」。"""
    # 简介里刻意不放商务入口：这个账号只能由 roster centrality 这一路进来，
    # 才测得到「那条依据不是关系证据」。有商务入口的会先走 buyable 那一路。
    account = XAccount(
        rest_id="obs-1", handle="discovered", bio="AI builder posting about agents",
        followers=40_000, following=500, roles="observed",
    )
    session.add(account)
    session.flush()
    session.add(RosterCentrality(
        account_id=account.id, roster_followers=9, in_vertical_followers=4,
        computed_date=dt.date(2026, 9, 1), roster_size=180,
    ))
    session.commit()

    prefs = DomainPreferences.from_dict({**PREFS, "target_handles": ["realroot"]})
    results = _screen_all(session, prefs)
    row = results["discovered"]

    basis = row.view.discovery_basis
    assert basis["source"] == "roster_centrality"
    assert "不是「目标人物关注了他」" in basis["caveat"]
    # 发现依据不得进入关系证据。
    assert row.relation.targets == []


def test_observed_account_derivations_carry_their_basis(session):
    account = XAccount(
        rest_id="obs-2", handle="derived", bio="Daily AI news for developers and founders",
        followers=30_000, following=400, roles="observed",
    )
    session.add(account)
    session.flush()
    session.add(RosterCentrality(
        account_id=account.id, roster_followers=5, computed_date=dt.date(2026, 9, 1),
        roster_size=180,
    ))
    session.commit()

    views = {
        v.handle: v for v in bd_discovery.build_views(
            session, include_observed=True, min_roster_followers=1
        )
    }
    view = views["derived"]
    assert "ai" in view.verticals
    assert view.verticals_basis == "bio"
    # 观察层没有内容语言字段，就不能假装有。
    assert view.languages == ()
    assert view.market_region_basis in ("bio_script", "unknown")


def test_accounts_without_a_bio_stay_out_of_the_queue(session):
    """没有简介就没有任何内容依据 —— 与其塞进队列让人逐个点开，不如留给补采。"""
    account = XAccount(rest_id="obs-3", handle="nobio", followers=90_000, roles="observed")
    session.add(account)
    session.flush()
    session.add(RosterCentrality(
        account_id=account.id, roster_followers=20, computed_date=dt.date(2026, 9, 1),
        roster_size=180,
    ))
    session.commit()

    handles = {
        v.handle for v in bd_discovery.build_views(
            session, include_observed=True, min_roster_followers=1
        )
    }
    assert "nobio" not in handles


# =============================================================================
# 目标人物驱动的发现 —— 方法主线
# =============================================================================


def _seed_target_graph(session: Session) -> None:
    """两位名人目标 + 三个他们关注的账号。

    ``buyable`` 被两位都关注，``single`` 只被一位关注，``notfollowed`` 谁都
    没关注 —— 它必须完全不出现，因为没有任何目标人物指向它。
    """
    from signal_map.backend.observation_models import FollowEdge, FollowSnapshot

    famous_a = XAccount(
        rest_id="fam-a", handle="karpathy", display_name="Andrej Karpathy",
        followers=4_000_000, following=1_100, roles="target",
    )
    famous_b = XAccount(
        rest_id="fam-b", handle="sama", display_name="Sam Altman",
        followers=6_000_000, following=1_000, roles="target",
    )
    buyable = XAccount(
        rest_id="buy-1", handle="buyablekol", display_name="Buyable KOL",
        bio="AI engineering deep dives for developers. Brand partnerships: hi@buy.com",
        followers=80_000, following=400, roles="observed",
    )
    single = XAccount(
        rest_id="buy-2", handle="singlekol", bio="AI news for founders",
        followers=50_000, following=300, roles="observed",
    )
    notfollowed = XAccount(
        rest_id="buy-3", handle="notfollowed", bio="AI builder for developers",
        followers=70_000, following=200, roles="observed",
    )
    session.add_all([famous_a, famous_b, buyable, single, notfollowed])
    session.flush()

    for observer in (famous_a, famous_b):
        snapshot = FollowSnapshot(
            observer_id=observer.id, kind="following",
            collected_date=dt.date(2026, 9, 9), is_complete=True, edge_count=2,
        )
        session.add(snapshot)
        session.flush()
        targets = [buyable] if observer is famous_b else [buyable, single]
        for target in targets:
            session.add(FollowEdge(
                source_id=observer.id, target_id=target.id,
                first_seen_at=dt.datetime(2026, 9, 9),
                last_seen_at=dt.datetime(2026, 9, 9),
                last_seen_snapshot_id=snapshot.id,
            ))
    session.commit()


def test_pool_is_defined_by_buyability_not_by_who_follows_them(session):
    """池子由「能不能买」定义 —— 这是审计之后改过来的核心。

    ``buyablekol`` 简介里有商务邮箱，进池子。``singlekol`` 同样被目标关注，
    但简介里读不出任何商务入口，**不进**：他被谁关注不能让他变得可买。
    """
    _seed_target_graph(session)
    targets = bd_screening.resolve_target_nodes(session, ("karpathy", "sama"))
    views = {
        v.handle: v for v in bd_discovery.build_views(
            session, target_accounts=targets,
            include_creators=False, include_ingested=False,
        )
    }
    assert "buyablekol" in views
    assert "singlekol" not in views
    assert "notfollowed" not in views
    assert views["buyablekol"].discovery_basis["source"] == "buyable_signal"


def test_famous_account_with_no_commercial_signal_stays_out(session):
    """被 40 位名人关注、但买不到的人，不能因为那些关注进池子。

    这是审计里最贵的一课：按「多少名人关注他」排序，前 20 名是 Stripe CEO、
    Shopify CEO、Sundar Pichai、Hinton、OpenAI —— 一个能买的都没有。名人互相
    关注是这张图的结构性质。
    """
    from signal_map.backend.observation_models import FollowEdge

    famous = XAccount(
        rest_id="fam-c", handle="megaceo", display_name="Mega CEO",
        bio="CEO of a very large company. Thoughts on AI.",
        followers=5_000_000, following=300, roles="observed",
    )
    session.add(famous)
    session.flush()
    _seed_target_graph(session)
    for handle in ("karpathy", "sama"):
        target = session.scalar(select(XAccount).where(XAccount.handle == handle))
        session.add(FollowEdge(source_id=target.id, target_id=famous.id))
    session.commit()

    targets = bd_screening.resolve_target_nodes(session, ("karpathy", "sama"))
    handles = {
        v.handle for v in bd_discovery.build_views(
            session, target_accounts=targets,
            include_creators=False, include_ingested=False,
        )
    }
    assert "megaceo" not in handles


def test_buyable_candidate_with_no_target_follow_is_kept(session):
    """能买、内容对口，但一位目标都没关注 —— 仍然是有效建联对象，不能删。"""
    _seed_target_graph(session)
    account = XAccount(
        rest_id="buy-9", handle="unfollowedkol",
        bio="AI tutorials for developers. Brand partnerships: hi@kol.com",
        followers=90_000, following=300, roles="observed",
    )
    session.add(account)
    session.commit()

    targets = bd_screening.resolve_target_nodes(session, ("karpathy", "sama"))
    views = {
        v.handle: v for v in bd_discovery.build_views(
            session, target_accounts=targets,
            include_creators=False, include_ingested=False,
        )
    }
    assert "unfollowedkol" in views
    assert views["unfollowedkol"].target_follow_edges == []


def test_min_targets_only_gates_the_badge_not_the_pool(session):
    """``min_targets`` 只决定标记显不显示，不决定谁进池子。"""
    _seed_target_graph(session)
    targets = bd_screening.resolve_target_nodes(session, ("karpathy", "sama"))
    views = {
        v.handle: v for v in bd_discovery.build_views(
            session, target_accounts=targets, min_targets=3,
            include_creators=False, include_ingested=False,
        )
    }
    # 门槛提到 3，@buyablekol 只被 2 位关注 —— 标记不显示，但人还在。
    assert "buyablekol" in views
    assert views["buyablekol"].target_follow_edges == []


def test_target_people_are_never_in_the_buyable_pool(session):
    """名人是受众，不是投放对象：他们互相关注也不能把对方带进池子。"""
    _seed_target_graph(session)
    from signal_map.backend.observation_models import FollowEdge

    a = session.scalar(select(XAccount).where(XAccount.handle == "karpathy"))
    b = session.scalar(select(XAccount).where(XAccount.handle == "sama"))
    session.add(FollowEdge(source_id=a.id, target_id=b.id))
    session.commit()

    targets = bd_screening.resolve_target_nodes(session, ("karpathy", "sama"))
    handles = {
        v.handle for v in bd_discovery.build_views(
            session, target_accounts=targets, include_observed=False,
            include_creators=False, include_ingested=False,
        )
    }
    assert "sama" not in handles and "karpathy" not in handles


def test_target_follow_edge_becomes_relation_evidence(session):
    """这条边同时是发现依据和关系证据，方向必须写明。"""
    _seed_target_graph(session)
    prefs = DomainPreferences.from_dict({**PREFS, "target_handles": ["karpathy", "sama"]})
    results = _screen_all(session, prefs)
    row = results["buyablekol"]

    assert row.relation.distinct_targets == 2
    directions = {t["direction_text"] for t in row.relation.targets}
    assert directions == {"@karpathy 关注了本候选", "@sama 关注了本候选"}
    assert any("共同关注" in s for s in row.relation.priority_signals)
    # X 不暴露关注开始时间，所以这里绝不能编一个。
    assert all(t["occurred_at"] is None for t in row.relation.targets)
    assert all(t["collected_at"] for t in row.relation.targets)


def test_roster_centrality_is_not_presented_as_target_attention(session):
    """两种 discovery_basis 不能混。"""
    account = XAccount(
        rest_id="obs-9", handle="rosterfound", bio="AI tools for developers",
        followers=40_000, following=500, roles="observed",
    )
    session.add(account)
    session.flush()
    session.add(RosterCentrality(
        account_id=account.id, roster_followers=12, computed_date=dt.date(2026, 9, 1),
        roster_size=180,
    ))
    session.commit()

    views = {
        v.handle: v for v in bd_discovery.build_views(
            session, include_observed=True, min_roster_followers=1
        )
    }
    basis = views["rosterfound"].discovery_basis
    assert basis["source"] == "roster_centrality"
    assert not basis.get("is_relation_evidence")
    assert views["rosterfound"].target_follow_edges == []


def test_priced_creator_followed_by_a_target_stays_out_of_the_bd_queue(session):
    """已有报价的人由客户推荐那一块负责，不该在两处同时出现。"""
    _seed_target_graph(session)
    priced = session.scalar(select(Creator).where(Creator.primary_handle == "pricedvoice"))
    account = session.scalar(select(XAccount).where(XAccount.handle == "buyablekol"))
    account.creator_id = priced.id
    session.commit()

    targets = bd_screening.resolve_target_nodes(session, ("karpathy", "sama"))
    handles = {
        v.handle for v in bd_discovery.build_views(session, target_accounts=targets)
    }
    assert "buyablekol" not in handles


# =============================================================================
# 实时性
# =============================================================================


def test_changing_preferences_changes_both_lists(env):
    client, _ = env
    headers = {"Authorization": f"Bearer {INTERNAL}"}

    def run(verticals):
        return client.post(
            "/api/internal/bd/screen", headers=headers,
            json={"verticals": verticals, "market_regions": ["europe_america"],
                  "platforms": ["X"], "limit": 20, "priced_limit": 10},
        ).json()

    ai = run(["ai"])
    gaming = run(["gaming"])

    ai_ready = {i["handle"] for i in ai["bd_candidates"]["items"] if i["priority"] == "priority_inquiry"}
    gaming_ready = {i["handle"] for i in gaming["bd_candidates"]["items"] if i["priority"] == "priority_inquiry"}
    assert ai_ready != gaming_ready
    assert "readyquote" in ai_ready
    assert "offtopic" in gaming_ready


def test_supply_gap_is_reported_rather_than_filled(env):
    """库存里没有这种人时要明说，而不是拿近似的顶上。"""
    client, _ = env
    response = client.post(
        "/api/internal/bd/screen",
        headers={"Authorization": f"Bearer {INTERNAL}"},
        json={"market_regions": ["chinese"], "priced_limit": 5},
    )
    gaps = response.json()["priced_recommendations"]["supply_gaps"]
    assert any(g["axis"] == "market_language" for g in gaps)


def test_temporary_screening_does_not_persist_a_brief(env):
    """随手试筛不该在客户的项目历史里留下一份简报。"""
    client, Local = env
    from signal_map.backend.models import Brief, RecommendationRun

    client.post(
        "/api/internal/bd/screen", headers={"Authorization": f"Bearer {INTERNAL}"},
        json={"verticals": ["ai"], "priced_limit": 3},
    )
    with Local() as check:
        assert check.scalars(select(Brief)).all() == []
        assert check.scalars(select(RecommendationRun)).all() == []


# =============================================================================
# 重建保护
# =============================================================================


def test_bd_tables_are_not_dropped_by_a_migration_rebuild():
    """``migrate_from_bd`` 只能删可以从 kol.db 重新算出来的表。

    ``bd_candidates`` 和 ``commercial_signals`` 记的是 Mango 自己做过的判断和
    找到的证据，kol.db 一条都还原不了。这条断言和观察层那条是同一个教训。
    """
    from signal_map.scripts.migrate_from_bd import _MIGRATED_TABLES

    for table in ("bd_candidates", "commercial_signals", "attention_signals",
                  "follow_edges", "x_accounts", "roster_centrality"):
        assert table not in _MIGRATED_TABLES


def test_locally_obtained_rows_are_preserved_across_a_rebuild():
    """本地取得的报价不能被重建抹掉 —— kol.db 里没有它。"""
    from signal_map.scripts import migrate_from_bd

    assert migrate_from_bd._LOCAL_SOURCE_SYSTEM == "signal_map_bd_intake"
    # 三张按 source_system 保留的表都要在，尤其是 quote_messages：
    # 报价原文丢了就再也解析不回来。
    tables = {table for table, _ in migrate_from_bd._PRESERVE_BY_SOURCE}
    assert {"creators", "quote_messages"} <= tables
