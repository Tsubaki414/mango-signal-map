"""Root review — the gate between "an algorithm surfaced it" and "it is a Root".

The thing under test is a separation, not a feature: a machine suggestion and a
human decision live in different columns, and only the human's writes to
``root_type``. Everything downstream — the 注意力路径 axis wording, what a
client is told — keys off the human's column.

The arithmetic flags get their own tests because one of them was added after
the first version shipped a hole: @MagnaDing follows 84 of Mango's 188
creators, which is the most roster-saturated account in the entire queue, and
the original share-of-following flag cleared it as "selective" because its
follow list is large. That case is now a regression test.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from signal_map.backend import app as app_module
from signal_map.backend import root_review
from signal_map.backend.models import AttentionSignal, Base, Brief, Client, Creator
from signal_map.backend.observation_models import (
    ROOT_REVIEW_STATUSES,
    ROOT_TYPES,
    XAccount,
)
from signal_map.backend.recommend import assess, attention_signals_by_creator

INTERNAL = "internal-test-token"


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, expire_on_commit=False)
    with Local() as db:
        _seed(db)
        yield db


@pytest.fixture
def api(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        db = Local()
        try:
            yield db
        finally:
            db.close()

    app_module.app.dependency_overrides[app_module.db] = override
    monkeypatch.setenv("SIGNAL_MAP_INTERNAL_TOKEN", INTERNAL)
    with Local() as seed:
        _seed(seed)
    yield TestClient(app_module.app), Local
    app_module.app.dependency_overrides.clear()


def _seed(session: Session) -> None:
    """Two creators, one real Root, one peer account that swept the roster."""
    session.add(Client(name="ACME", api_token="tok-acme"))
    creators = []
    for i in range(4):
        creator = Creator(
            display_name=f"Creator {i}", primary_handle=f"c{i}",
            creator_class="KOL", creator_tier="strategic",
            verticals="ai", market_region="europe_america", languages="en",
        )
        session.add(creator)
        creators.append(creator)
    session.flush()

    # A genuine Root: follows thousands, two of them ours.
    session.add(XAccount(
        rest_id="root-1", handle="realroot", display_name="Real Root",
        bio="Managing Partner at a venture fund. Backing founders.",
        followers=900_000, following=3_000, listed_count=8_000, roles="root_candidate",
    ))
    # A peer: a small follow list, nearly half of Mango's roster inside it.
    session.add(XAccount(
        rest_id="peer-1", handle="peeracct", display_name="Peer",
        bio="AI content creator | Ghostwriter | DM for collab",
        followers=20_000, following=700, listed_count=100, roles="root_candidate",
    ))
    # The @MagnaDing shape: big follow list, but it swept the roster.
    session.add(XAccount(
        rest_id="sweeper-1", handle="sweeper", display_name="Sweeper",
        bio="Founder", followers=15_000, following=4_000, listed_count=170,
        roles="root_candidate",
    ))
    # Mango's own roster, the denominator for roster_sweep.
    for i, creator in enumerate(creators):
        session.add(XAccount(
            rest_id=f"roster-{i}", handle=f"c{i}", roles="roster", creator_id=creator.id,
        ))
    session.flush()

    def signal(source: str, creator: Creator, kind: str = "follow") -> AttentionSignal:
        return AttentionSignal(
            source_node=source, target_node=f"roster-{creators.index(creator)}",
            source_creator_id=creator.id, platform="X", signal_type=kind,
            direction="source_to_target", observation_count=1,
            raw_evidence="observed", confidence="research_lead", human_confirmed=False,
        )

    session.add(signal("root-1", creators[0]))
    session.add(signal("peer-1", creators[0]))
    for creator in creators[:3]:  # 3 of 4 = 75% of the roster
        session.add(signal("sweeper-1", creator))
    session.commit()


# --- the separation ----------------------------------------------------------


def test_a_suggestion_never_becomes_a_decision(session):
    """A stored suggestion leaves ``root_type`` and the review status alone."""
    account = session.scalar(select(XAccount).where(XAccount.handle == "realroot"))
    account.root_suggested_type = "investor"
    account.root_suggestion_basis = 'model judged 「投资人」, evidence: "Managing Partner"'
    account.root_suggested_by = "llm:grok"
    session.commit()

    assert account.root_type is None
    assert account.root_review_status == "pending"
    assert session.scalar(
        select(XAccount.rest_id).where(XAccount.root_review_status == "accepted")
    ) is None


def test_accepting_requires_a_real_type(session):
    """An accepted Root with no type cannot answer which 圈层 it reaches."""
    account = session.scalar(select(XAccount).where(XAccount.handle == "realroot"))
    with pytest.raises(ValueError):
        root_review.decide(session, account, actor="fiona", status="accepted")
    with pytest.raises(ValueError):
        root_review.decide(
            session, account, actor="fiona", status="accepted", root_type="unknown"
        )
    with pytest.raises(ValueError):
        root_review.decide(session, account, actor="fiona", status="not-a-status")
    # Nothing was written by any of the rejected attempts.
    assert account.root_type is None
    assert account.root_review_status == "pending"


def test_rejecting_clears_any_type_and_the_root_role(session):
    account = session.scalar(select(XAccount).where(XAccount.handle == "realroot"))
    root_review.decide(
        session, account, actor="fiona", status="accepted", root_type="investor"
    )
    session.commit()
    assert account.has_role("root")

    root_review.decide(session, account, actor="fiona", status="rejected", note="看错了")
    session.commit()
    # A stale type left behind would read as an accepted Root to any later query.
    assert account.root_type is None
    assert not account.has_role("root")
    assert not account.has_role("root_candidate")
    assert account.root_review_status == "rejected"


# --- what the decision changes downstream ------------------------------------


def _axis(session, creator_name: str = "Creator 0"):
    creator = session.scalar(select(Creator).where(Creator.display_name == creator_name))
    signals = attention_signals_by_creator(session).get(creator.id, [])
    brief = Brief(client_id=1, name="b", verticals="ai")
    result = assess(creator, brief, signals)
    return next(a for a in result.axes if a.key == "attention_path")


def test_unreviewed_candidates_are_never_called_roots(session):
    axis = _axis(session)
    assert axis.verdict == "partial"
    assert "候选账号" in axis.detail
    assert "已确认 Root" not in axis.detail


def test_accepting_a_root_changes_what_the_axis_says(session):
    account = session.scalar(select(XAccount).where(XAccount.handle == "realroot"))
    root_review.decide(
        session, account, actor="fiona", status="accepted", root_type="investor"
    )
    session.commit()

    axis = _axis(session)
    assert "已确认 Root" in axis.detail
    assert "投资人" in axis.detail
    assert "@realroot" in axis.detail
    # The unreviewed signals (peer + sweeper) are still counted somewhere, but
    # they no longer stand in the sentence beside a confirmed Root.
    assert "@peeracct" not in axis.detail
    assert "@sweeper" not in axis.detail
    assert "另有 2 条来自未审核候选" in axis.detail


def test_unreviewed_candidates_are_never_named_to_a_client(session):
    """Internally the handles stay; to a client only a count goes out.

    Naming an unreviewed candidate published @AlfaizAliX -- which follows 70 of
    Mango's creators, 9.5% of its whole follow list -- as an attention path,
    picked which three to show alphabetically, and disclosed which accounts
    Mango tracks. All three were live in the generated samples.
    """
    axis = _axis(session)
    assert "@realroot" in axis.detail  # Mango must be able to check the claim
    assert "@peeracct" in axis.detail

    assert "@" not in axis.detail_for_client
    assert "个待确认账号" in axis.detail_for_client
    assert "仅作研究线索" in axis.detail_for_client


def test_an_accepted_root_is_named_to_the_client(session):
    """The naming is the value -- withholding it everywhere would be as wrong."""
    account = session.scalar(select(XAccount).where(XAccount.handle == "realroot"))
    root_review.decide(
        session, account, actor="fiona", status="accepted", root_type="investor"
    )
    session.commit()

    axis = _axis(session)
    assert "@realroot" in axis.detail_for_client
    assert "已确认 Root（投资人）" in axis.detail_for_client
    # …but the unreviewed ones beside it still are not named.
    assert "@peeracct" not in axis.detail_for_client
    assert "@sweeper" not in axis.detail_for_client


def test_a_rejected_root_stops_counting_entirely(session):
    for handle in ("realroot", "peeracct", "sweeper"):
        account = session.scalar(select(XAccount).where(XAccount.handle == handle))
        root_review.decide(session, account, actor="fiona", status="rejected")
    session.commit()

    axis = _axis(session)
    # Withdrawn evidence must not come back as 关系数据待补充's opposite.
    assert axis.verdict == "unknown"
    assert "关系数据待补充" in axis.detail


def test_rejected_signals_are_dropped_at_the_source(session):
    """Not filtered per-reader: one filter, so a new caller cannot forget it."""
    account = session.scalar(select(XAccount).where(XAccount.handle == "peeracct"))
    root_review.decide(session, account, actor="fiona", status="rejected")
    session.commit()

    grouped = attention_signals_by_creator(session)
    handles = {s.source_handle for signals in grouped.values() for s in signals}
    assert "peeracct" not in handles
    assert "realroot" in handles


# --- the flags ---------------------------------------------------------------


def test_roster_sweep_fires_when_share_of_following_would_not(session):
    """The @MagnaDing regression: 84 of 188 creators, but a 4,000-strong follow
    list, so share-of-following reads a harmless 2%. Sweeping most of one
    agency's roster is the signal; the ratio that hides it is not the only one."""
    account = session.scalar(select(XAccount).where(XAccount.handle == "sweeper"))
    flags = root_review.review_flags(account, creators_followed=3, interaction_count=0,
                                     roster_size=4)
    keys = {f.key: f for f in flags}

    assert 3 / (account.following or 1) < root_review.PEER_NETWORK_SHARE
    assert "roster_sweep" in keys
    assert keys["roster_sweep"].reading == "against_root"
    # And it must not simultaneously claim the opposite.
    assert "selective_follow" not in keys


def test_a_selective_follower_is_not_flagged(session):
    account = session.scalar(select(XAccount).where(XAccount.handle == "realroot"))
    flags = root_review.review_flags(account, creators_followed=1, interaction_count=0,
                                     roster_size=188)
    keys = {f.key for f in flags}
    assert "selective_follow" in keys
    assert "roster_sweep" not in keys
    assert "peer_network" not in keys


def test_growth_service_bio_reads_against_root(session):
    account = session.scalar(select(XAccount).where(XAccount.handle == "peeracct"))
    flags = root_review.review_flags(account, creators_followed=1, interaction_count=0,
                                     roster_size=188)
    growth = next(f for f in flags if f.key == "growth_service")
    assert growth.reading == "against_root"
    assert "ghostwriter" in growth.detail


def test_flags_are_never_summed_into_a_score(session):
    """Three-valued readings, no arithmetic. A single number would have ranked a
    mutual-follow pod above a Turing laureate -- both score alike on every
    metric available here."""
    account = session.scalar(select(XAccount).where(XAccount.handle == "peeracct"))
    flags = root_review.review_flags(account, 1, 0, 188)
    assert {f.reading for f in flags} <= {"supports_root", "against_root", "context"}
    assert not hasattr(root_review.ReviewFlag, "score")
    assert not hasattr(root_review.RootCandidate, "score")


def test_rule_suggester_declines_rather_than_guessing(session):
    """It only claims types a bio states structurally. Guessing 行业大佬 from a
    keyword is exactly the failure the audience classifier already hit."""
    account = session.scalar(select(XAccount).where(XAccount.handle == "realroot"))
    root_type, basis = root_review.suggest_type(account, [])
    assert root_type == "investor"
    assert "Managing Partner" in basis

    vague = XAccount(rest_id="x", handle="vague", bio="Building things. Coffee.")
    assert root_review.suggest_type(vague, []) == (None, None)
    # A rule may never propose these two: telling them apart needs to know a
    # person's standing in a field, which no keyword can see.
    for _, markers in root_review._TYPE_MARKERS:
        assert markers
    assert {t for t, _ in root_review._TYPE_MARKERS}.isdisjoint(
        {"industry_leader", "vertical_expert"}
    )


# --- API ---------------------------------------------------------------------


def test_review_queue_returns_flags_not_a_ranking_number(api):
    client, _ = api
    response = client.get(
        "/api/internal/roots/review-queue", headers={"Authorization": f"Bearer {INTERNAL}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    candidate = body["candidates"][0]
    assert candidate["flags"]
    assert "score" not in candidate
    assert candidate["review_status"] == "pending"
    assert candidate["root_type"] is None
    # The reviewer needs the closed vocabulary to answer with.
    assert {t["key"] for t in body["root_types"]} == set(ROOT_TYPES)


def test_queue_shows_whose_attention_path_is_at_stake(api):
    client, _ = api
    body = client.get(
        "/api/internal/roots/review-queue", headers={"Authorization": f"Bearer {INTERNAL}"}
    ).json()
    sweeper = next(c for c in body["candidates"] if c["handle"] == "sweeper")
    assert sweeper["creators_followed"] == 3
    assert len(sweeper["creator_names"]) == 3
    assert sweeper["profile_url"] == "https://x.com/sweeper"


def test_deciding_through_the_api_requires_an_actor_and_a_valid_type(api):
    client, _ = api
    headers = {"Authorization": f"Bearer {INTERNAL}"}

    assert client.patch(
        "/api/internal/roots/realroot", headers=headers,
        json={"status": "accepted", "root_type": "investor"},
    ).status_code == 422  # no actor

    assert client.patch(
        "/api/internal/roots/realroot", headers=headers,
        json={"actor": "fiona", "status": "accepted"},
    ).status_code == 422  # no type

    assert client.patch(
        "/api/internal/roots/realroot", headers=headers,
        json={"actor": "fiona", "status": "maybe", "root_type": "investor"},
    ).status_code == 422  # not a status

    ok = client.patch(
        "/api/internal/roots/realroot", headers=headers,
        json={"actor": "fiona", "status": "accepted", "root_type": "investor",
              "verticals": "ai", "note": "确认过 fund 主页"},
    )
    assert ok.status_code == 200
    assert ok.json()["root_type"] == "investor"
    assert ok.json()["reviewed_by"] == "fiona"
    assert ok.json()["reviewed_at"] is not None


def test_summary_says_plainly_that_pending_candidates_are_not_roots(api):
    client, _ = api
    body = client.get(
        "/api/internal/roots/summary", headers={"Authorization": f"Bearer {INTERNAL}"}
    ).json()
    assert body["candidates_with_edges"] == 3
    assert body["pending"] == 3
    assert body["accepted"] == 0
    assert "研究线索" in body["note"]


def test_summary_route_is_not_shadowed_by_the_handle_route(api):
    """``/roots/summary`` must not resolve as a handle named "summary"."""
    client, _ = api
    body = client.get(
        "/api/internal/roots/summary", headers={"Authorization": f"Bearer {INTERNAL}"}
    ).json()
    assert "candidates_with_edges" in body
    assert "rest_id" not in body


def test_review_queue_is_behind_the_internal_guard(api):
    client, _ = api
    for path in ("/api/internal/roots/summary", "/api/internal/roots/review-queue"):
        assert client.get(path).status_code == 401


def test_every_review_status_is_reachable(session):
    """The vocabulary and the writer agree — a status nothing can set is a lie."""
    account = session.scalar(select(XAccount).where(XAccount.handle == "realroot"))
    assert account.root_review_status == "pending"
    for status in ("accepted", "rejected"):
        root_review.decide(
            session, account, actor="fiona", status=status,
            root_type="investor" if status == "accepted" else None,
        )
        assert account.root_review_status == status
    assert set(ROOT_REVIEW_STATUSES) == {"pending", "accepted", "rejected"}
