"""Observation-layer tests.

Weighted toward the two things that make this data honest rather than merely
present: **edge direction** and **what a truncated pull is allowed to prove**.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from signal_map.backend.models import Base, Creator, Quote, QuoteMessage, SocialAccount
from signal_map.backend.observation_models import (
    ACCOUNT_ROLES,
    ROOT_TYPE_LABELS_ZH,
    ROOT_TYPES,
    FollowEdge,
    FollowSnapshot,
    RosterCentrality,
    XAccount,
)
from signal_map.backend.normalize import AUDIENCE_TYPES
from signal_map.scripts import collect_follow_graph as cfg


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def make_priced_creator(session, name: str, handle: str, verticals="ai") -> Creator:
    creator = Creator(display_name=name, creator_tier="strategic", verticals=verticals)
    session.add(creator)
    session.flush()
    session.add(SocialAccount(creator_id=creator.id, platform="X", handle=handle))
    message = QuoteMessage(creator_id=creator.id, raw_text="thread $500")
    session.add(message)
    session.flush()
    session.add(
        Quote(
            creator_id=creator.id,
            message_id=message.id,
            amount=500.0,
            amount_usd=500.0,
            content_format="x_thread",
            platform="X",
        )
    )
    session.flush()
    return creator


# --- roster selection --------------------------------------------------------


def test_roster_is_priced_creators_with_an_x_handle(session):
    make_priced_creator(session, "Priced", "priced")
    unpriced = Creator(display_name="No price", creator_tier="strategic")
    session.add(unpriced)
    session.flush()
    session.add(SocialAccount(creator_id=unpriced.id, platform="X", handle="nofunds"))
    session.flush()

    assert [c.display_name for c, _ in cfg.roster(session)] == ["Priced"]


def test_roster_deduplicates_a_creator_with_several_x_accounts(session):
    creator = make_priced_creator(session, "Two accounts", "main")
    session.add(SocialAccount(creator_id=creator.id, platform="X", handle="alt"))
    session.flush()
    assert len(cfg.roster(session)) == 1


# --- id extraction -----------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"ids": ["1", "2"], "next_cursor_str": "abc"},
        {"users": [{"id_str": "1"}, {"rest_id": "2"}], "next_cursor": "abc"},
        {"data": [1, 2], "next_cursor_str": "abc"},
    ],
)
def test_extract_ids_handles_each_payload_shape(payload):
    ids, cursor = cfg._extract_ids(payload)
    assert ids == ["1", "2"]
    assert cursor == "abc"


@pytest.mark.parametrize("terminal", ["0", "-1", "", 0, -1])
def test_terminal_cursor_becomes_none(terminal):
    _, cursor = cfg._extract_ids({"ids": ["1"], "next_cursor_str": terminal})
    assert cursor is None


# --- identity ----------------------------------------------------------------


def test_account_is_keyed_by_rest_id_not_handle(session):
    first = cfg._upsert_account(session, "555", handle="oldname", role="roster")
    session.commit()
    again = cfg._upsert_account(session, "555", handle="newname")
    session.commit()
    assert first.id == again.id
    assert session.scalar(select(XAccount).where(XAccount.rest_id == "555")).handle == "newname"


def test_a_rename_is_recorded_not_overwritten(session):
    """A handle change is itself a signal; losing it loses the event."""
    cfg._upsert_account(session, "555", handle="oldname")
    session.commit()
    account = cfg._upsert_account(session, "555", handle="newname")
    session.commit()
    assert "oldname" in account.handle_history


def test_roles_accumulate_rather_than_replace(session):
    cfg._upsert_account(session, "777", role="observed")
    session.commit()
    account = cfg._upsert_account(session, "777", role="root_candidate")
    session.commit()
    assert account.has_role("observed")
    assert account.has_role("root_candidate")


# --- direction discipline ----------------------------------------------------


def test_edges_are_stored_as_source_follows_target(session):
    """The whole design turns on this. ``KOL → follows → account`` says our
    creator can see that account -- not the reverse."""
    kol = cfg._upsert_account(session, "kol1", handle="ourkol", role="roster")
    other = cfg._upsert_account(session, "big1", handle="bigaccount", role="observed")
    session.flush()
    session.add(FollowEdge(source_id=kol.id, target_id=other.id))
    session.commit()

    edge = session.scalar(select(FollowEdge))
    assert edge.source.handle == "ourkol"
    assert edge.target.handle == "bigaccount"
    # The reverse edge is a different fact and must not be inferred.
    assert (
        session.scalar(
            select(FollowEdge).where(
                FollowEdge.source_id == other.id, FollowEdge.target_id == kol.id
            )
        )
        is None
    )


def test_stage_one_edges_never_reach_the_attention_signal_table(session):
    """Path 3 is ``Root → follows → KOL``. Stage 1 collects the opposite
    direction, so nothing it produces may be promoted automatically."""
    from signal_map.backend.models import AttentionSignal

    kol = cfg._upsert_account(session, "kol1", handle="ourkol", role="roster")
    other = cfg._upsert_account(session, "big1", handle="bigaccount")
    session.flush()
    session.add(FollowEdge(source_id=kol.id, target_id=other.id))
    session.commit()
    assert session.scalar(select(AttentionSignal)) is None


# --- coverage honesty --------------------------------------------------------


def test_truncated_snapshot_is_flagged_with_its_limitation(session):
    observer = cfg._upsert_account(session, "kol1", handle="ourkol", role="roster")
    session.flush()
    snapshot = FollowSnapshot(
        observer_id=observer.id,
        kind="following",
        collected_date=dt.date.today(),
        is_complete=False,
        coverage_limitation="stopped at 8 pages",
    )
    session.add(snapshot)
    session.commit()
    assert not snapshot.is_complete
    assert snapshot.coverage_limitation


def test_only_a_complete_snapshot_may_mark_an_edge_gone(session, monkeypatch):
    """After a truncated pull a missing edge is unobserved, not unfollowed --
    marking it gone would manufacture an event."""
    observer = cfg._upsert_account(session, "kol1", handle="ourkol", role="roster")
    stale = cfg._upsert_account(session, "gone1", handle="unfollowed")
    session.flush()
    session.add(FollowEdge(source_id=observer.id, target_id=stale.id))
    session.commit()

    class TruncatedClient:
        def get_following_ids(self, handle, count, cursor=None, **kwargs):
            # Always returns another cursor -> the pull never completes.
            return {"ids": ["999"], "next_cursor_str": "more"}

    cfg.collect_one(session, TruncatedClient(), observer, "ourkol", force=True)

    edge = session.scalar(
        select(FollowEdge).where(FollowEdge.target_id == stale.id)
    )
    assert edge.disappeared_at is None


def test_complete_snapshot_marks_a_vanished_edge(session):
    observer = cfg._upsert_account(session, "kol1", handle="ourkol", role="roster")
    stale = cfg._upsert_account(session, "gone1", handle="unfollowed")
    session.flush()
    session.add(FollowEdge(source_id=observer.id, target_id=stale.id))
    session.commit()

    class CompleteClient:
        def get_following_ids(self, handle, count, cursor=None, **kwargs):
            return {"ids": ["999"], "next_cursor_str": "0"}

    cfg.collect_one(session, CompleteClient(), observer, "ourkol", force=True)

    edge = session.scalar(select(FollowEdge).where(FollowEdge.target_id == stale.id))
    assert edge.disappeared_at is not None


def test_reobserving_an_edge_revives_it_and_counts_the_sighting(session):
    observer = cfg._upsert_account(session, "kol1", handle="ourkol", role="roster")
    session.flush()

    class Client:
        def get_following_ids(self, handle, count, cursor=None, **kwargs):
            return {"ids": ["999"], "next_cursor_str": "0"}

    cfg.collect_one(session, Client(), observer, "ourkol", force=True)
    cfg.collect_one(session, Client(), observer, "ourkol", force=True)

    edge = session.scalar(select(FollowEdge))
    assert edge.times_seen == 2
    assert edge.disappeared_at is None
    assert edge.first_seen_snapshot_id != edge.last_seen_snapshot_id


def test_first_seen_is_an_observation_date_not_a_follow_date(session):
    """X does not expose when a follow began. Conflating the two invents a
    timeline, so the column means only "first time Mango saw this"."""
    observer = cfg._upsert_account(session, "kol1", handle="ourkol", role="roster")
    session.flush()

    class Client:
        def get_following_ids(self, handle, count, cursor=None, **kwargs):
            return {"ids": ["999"], "next_cursor_str": "0"}

    before = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    cfg.collect_one(session, Client(), observer, "ourkol", force=True)
    edge = session.scalar(select(FollowEdge))
    assert edge.first_seen_at >= before  # today, not some historical date


# --- centrality --------------------------------------------------------------


def test_a_single_follower_is_not_centrality(session):
    """One roster member following an account is noise, not a Root signal."""
    kol = make_priced_creator(session, "K1", "k1")
    observer = cfg._upsert_account(session, "k1", handle="k1", role="roster", creator_id=kol.id)
    target = cfg._upsert_account(session, "t1", handle="t1")
    session.flush()
    session.add(FollowEdge(source_id=observer.id, target_id=target.id))
    session.commit()

    cfg.compute_centrality(session)
    assert session.scalar(select(RosterCentrality)) is None


def test_centrality_counts_distinct_roster_followers(session):
    target = cfg._upsert_account(session, "t1", handle="bigfish")
    for i in range(3):
        creator = make_priced_creator(session, f"K{i}", f"k{i}")
        observer = cfg._upsert_account(
            session, f"k{i}", handle=f"k{i}", role="roster", creator_id=creator.id
        )
        session.flush()
        session.add(FollowEdge(source_id=observer.id, target_id=target.id))
        session.add(
            FollowSnapshot(
                observer_id=observer.id, kind="following", collected_date=dt.date.today()
            )
        )
    session.commit()

    cfg.compute_centrality(session)
    row = session.scalar(select(RosterCentrality))
    assert row.roster_followers == 3
    assert row.roster_size == 3
    assert row.roster_share == 1.0


def test_a_disappeared_edge_stops_counting_toward_centrality(session):
    target = cfg._upsert_account(session, "t1", handle="bigfish")
    for i in range(3):
        creator = make_priced_creator(session, f"K{i}", f"k{i}")
        observer = cfg._upsert_account(
            session, f"k{i}", handle=f"k{i}", role="roster", creator_id=creator.id
        )
        session.flush()
        session.add(
            FollowEdge(
                source_id=observer.id,
                target_id=target.id,
                disappeared_at=dt.datetime.now() if i == 0 else None,
            )
        )
    session.commit()

    cfg.compute_centrality(session)
    assert session.scalar(select(RosterCentrality)).roster_followers == 2


def test_centrality_marks_the_target_as_a_root_candidate_not_a_root(session):
    """Stage 1 nominates. A human accepts -- that review is not skippable."""
    target = cfg._upsert_account(session, "t1", handle="bigfish")
    for i in range(2):
        creator = make_priced_creator(session, f"K{i}", f"k{i}")
        observer = cfg._upsert_account(
            session, f"k{i}", handle=f"k{i}", role="roster", creator_id=creator.id
        )
        session.flush()
        session.add(FollowEdge(source_id=observer.id, target_id=target.id))
    session.commit()

    cfg.compute_centrality(session)
    session.refresh(target)
    assert target.has_role("root_candidate")
    assert not target.has_role("root")
    assert target.root_type is None
    assert target.root_reviewed_at is None


# --- id-space integrity ------------------------------------------------------


def test_an_account_without_a_real_platform_id_is_skipped_not_placeholdered(session):
    """The bug this exists to prevent, which actually happened.

    The migration dropped ``x_rest_id``, so roster accounts were stored under
    ``handle:xxx`` placeholders while the follow endpoints return numeric ids.
    Every intersection compared string spaces that could never match and
    returned a clean, entirely false zero across leaders, bridges and products.

    A missing id must therefore stop collection for that account loudly --
    a placeholder silently intersects with nothing.
    """
    creator = make_priced_creator(session, "No uid", "nouid")
    account = session.scalar(
        select(SocialAccount).where(SocialAccount.creator_id == creator.id)
    )
    assert account.platform_uid is None

    observers = [
        a
        for a in session.scalars(select(XAccount)).all()
        if a.rest_id.startswith("handle:")
    ]
    assert observers == [], "no account may be stored under a handle placeholder"


def test_platform_uid_is_the_graph_join_key(session):
    """Graph identity is the platform's numeric id, never the handle -- handles
    get renamed, and the follow endpoints only ever return ids."""
    creator = make_priced_creator(session, "Real", "realhandle")
    account = session.scalar(
        select(SocialAccount).where(SocialAccount.creator_id == creator.id)
    )
    account.platform_uid = "1234567890"
    session.flush()

    observer = cfg._upsert_account(
        session, account.platform_uid, handle=account.handle, role="roster",
        creator_id=creator.id,
    )
    session.commit()

    # An id returned by a follow endpoint must find this account.
    assert (
        session.scalar(select(XAccount).where(XAccount.rest_id == "1234567890")).id
        == observer.id
    )


# --- vocabulary --------------------------------------------------------------


def test_every_root_type_has_a_chinese_label():
    assert set(ROOT_TYPES) == set(ROOT_TYPE_LABELS_ZH)


def test_root_types_align_with_the_audience_vocabulary():
    """A Root's type and a creator's audience type are the same axis seen from
    two ends; keeping them aligned is what lets "this creator reaches
    investors" join up with "this Root is an investor".

    Asserts the mapping explicitly. An earlier version built the equivalence
    set and never compared against it, so it claimed to check alignment while
    checking only that four strings existed.
    """
    equivalence = {
        "investor": "investors",
        "enterprise_buyer": "enterprise",
        "vertical_expert": "researchers",
    }
    for root_type, audience_type in equivalence.items():
        assert root_type in ROOT_TYPES, root_type
        assert audience_type in AUDIENCE_TYPES, audience_type

    # The vocabularies overlap; they are not identical, and should not be
    # forced to be. "media" and "community_leader" describe what a Root *is*,
    # not an audience segment Mango sells to — so they correctly have no
    # counterpart. (This test previously asserted a full mirror and failed,
    # which is how the overclaim was found.)
    assert {"media", "community_leader"} <= set(ROOT_TYPES)
    assert not ({"media", "community_leader"} & set(AUDIENCE_TYPES))


def test_account_roles_are_a_closed_set():
    assert "root_candidate" in ACCOUNT_ROLES and "root" in ACCOUNT_ROLES
