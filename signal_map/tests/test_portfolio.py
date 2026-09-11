"""Portfolio-scenario tests.

The interesting properties are not "does it pick something" but: does it pick
*differently* per thesis, does it explain what each pick added, and does it
tell the truth about leftover budget.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from signal_map.backend.models import Base
from signal_map.backend.portfolio import (
    BUDGET_TARGET,
    SCENARIOS,
    build_all,
    build_scenario,
)
from signal_map.backend.recommend import assess
from signal_map.tests.test_recommend import make_brief, make_creator


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def build(session, creators, brief, signals=None, budget=10_000):
    assessments = [assess(c, brief, (signals or {}).get(c.id)) for c in creators]
    by_id = {c.id: c for c in creators}
    return assessments, by_id, signals or {}


# --- scenario definitions ----------------------------------------------------


def test_every_scenario_declares_its_tradeoff():
    """A scenario shown without what it gives up is a recommendation
    pretending to be a choice."""
    for key, config in SCENARIOS.items():
        assert config["thesis"], key
        assert config["tradeoff"], key
        assert config["max_picks"] > 0, key
        assert 0.0 <= config["cost_sensitivity"] <= 1.0, key


def test_credibility_ignores_price_and_conversion_does_not():
    """Cost sensitivity is what stops every scenario picking the same cheap
    creators -- which also made 专业可信度方案 contradict its own 单价高 thesis."""
    assert SCENARIOS["credibility"]["cost_sensitivity"] == 0.0
    assert SCENARIOS["conversion"]["cost_sensitivity"] > 0.5


# --- selection ---------------------------------------------------------------


def test_picks_carry_what_they_added(session):
    """Each pick must justify its own place in the portfolio."""
    creators = [
        make_creator(session, name="Dev", audiences="developers", price=500.0),
        make_creator(session, name="Designer", audiences="designers", price=500.0),
    ]
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    scenario = build_scenario("coverage", assessments, by_id, signals, 10_000)

    assert scenario.picks
    first = scenario.picks[0]
    assert first.adds_audiences  # the first pick adds everything it covers
    assert first.role


def test_a_second_creator_covering_the_same_audience_adds_nothing_new(session):
    creators = [
        make_creator(session, name="Dev A", audiences="developers", price=500.0),
        make_creator(session, name="Dev B", audiences="developers", price=500.0),
    ]
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    scenario = build_scenario("coverage", assessments, by_id, signals, 10_000)

    assert len(scenario.picks) == 2
    assert scenario.picks[1].adds_audiences == []


def test_overlap_is_reported_not_optimised_away(session):
    """Some repetition is deliberate. The client is entitled to see how much
    of it they are buying."""
    for i in range(3):
        make_creator(session, name=f"Dev {i}", audiences="developers", price=100.0)
    creators = list(session.query(type(make_creator(session, name="X"))).all())[:3]
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    scenario = build_scenario("coverage", assessments, by_id, signals, 10_000)
    assert scenario.audience_overlap > 0


def test_head_scenario_seeds_the_largest_account_first(session):
    """Greedy marginal coverage never picks a head account on its own: it is
    expensive and covers audiences a cheaper creator also covers."""
    small = make_creator(session, name="Small", audiences="developers", price=100.0)
    big = make_creator(session, name="Big", audiences="developers", price=5_000.0)
    session.query(type(big)).filter_by(id=big.id).one()
    for account in big.accounts:
        account.followers = 5_000_000
    for account in small.accounts:
        account.followers = 1_000
    session.flush()

    brief = make_brief(session)
    assessments, by_id, signals = build(session, [small, big], brief)
    scenario = build_scenario("head_and_vertical", assessments, by_id, signals, 50_000)

    assert scenario.picks[0].creator_name == "Big"
    assert scenario.picks[0].role == "引爆者"


def test_budget_ceiling_stops_selection(session):
    for i in range(6):
        make_creator(session, name=f"C{i}", audiences="developers", price=400.0)
    creators = session.query(type(make_creator(session, name="Z"))).all()
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    scenario = build_scenario("coverage", assessments, by_id, signals, 1_000)

    spent = scenario.budget.total_usd or 0
    assert spent <= 1_000 * BUDGET_TARGET + 400  # last pick may cross the target


def test_a_creator_over_budget_is_never_picked(session):
    make_creator(session, name="Too expensive", price=9_000.0)
    creators = session.query(type(make_creator(session, name="Y"))).all()
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    scenario = build_scenario("credibility", assessments, by_id, signals, 1_000)
    assert all(p.creator_name != "Too expensive" for p in scenario.picks)


# --- honesty -----------------------------------------------------------------


def test_leftover_budget_is_explained_not_hidden(session):
    """Padding the portfolio to hit the budget number would be worse than
    saying the roster is cheaper than the budget."""
    make_creator(session, name="Cheap", price=50.0)
    creators = session.query(type(make_creator(session, name="W"))).all()
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    scenario = build_scenario("credibility", assessments, by_id, signals, 100_000)

    assert any("未用满预算" in note for note in scenario.client_notes)
    assert scenario.notes  # the internal version still carries the figures


def test_client_notes_never_let_the_spend_be_recovered(session):
    """The leak that shipped once: "仅用掉预算的 7%（剩余 $37,125）" beside a
    visible budget of $40,000 hands the client Mango's exact cost. A
    percentage leaks it exactly as well as the remainder does."""
    import re

    make_creator(session, name="Cheap", price=50.0)
    creators = session.query(type(make_creator(session, name="W2"))).all()
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    scenario = build_scenario("credibility", assessments, by_id, signals, 100_000)

    for note in scenario.client_notes:
        assert "$" not in note, note
        assert not re.search(r"\d+\s*%", note), note
    # And the internal note is allowed to say it plainly.
    assert any("$" in note for note in scenario.notes)


def test_an_unbuildable_scenario_says_so_rather_than_vanishing(session):
    """A missing option is information; omitting it reads as never considered."""
    make_creator(session, name="Distribution only", tier="distribution")
    creators = session.query(type(make_creator(session, name="V"))).all()
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    # credibility accepts strategic tier only
    scenario = build_scenario("credibility", assessments, by_id, signals, 10_000)
    if not scenario.picks:
        assert scenario.notes
        assert scenario.budget is not None


def test_build_all_returns_every_scenario_even_when_empty(session):
    make_creator(session, name="Only one")
    creators = session.query(type(make_creator(session, name="U"))).all()
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    scenarios = build_all(assessments, by_id, signals, 10_000)
    assert {s.key for s in scenarios} == set(SCENARIOS)


def test_package_in_a_portfolio_forces_mango_confirmation(session):
    make_creator(session, name="Bundle", price=800.0, is_package=True)
    creators = session.query(type(make_creator(session, name="T"))).all()
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    scenario = build_scenario("coverage", assessments, by_id, signals, 10_000)
    if scenario.picks:
        assert scenario.budget.needs_mango_confirmation


def test_roles_come_from_stored_fields_only(session):
    """A role with no supporting field would be an unfounded claim."""
    dev = make_creator(session, name="Dev", audiences="developers")
    consumer = make_creator(session, name="Consumer", audiences="consumers")
    brief = make_brief(session)
    assessments, by_id, signals = build(session, [dev, consumer], brief)
    scenario = build_scenario("coverage", assessments, by_id, signals, 10_000)
    roles = {p.creator_name: p.role for p in scenario.picks}
    assert roles.get("Dev") == "专业验证者"
    assert roles.get("Consumer") == "转化推动者"


def test_creator_with_no_root_signal_is_not_penalised(session):
    """Coverage is only claimed for data that exists; absence is not a fault."""
    make_creator(session, name="No roots")
    creators = session.query(type(make_creator(session, name="S"))).all()
    brief = make_brief(session)
    assessments, by_id, signals = build(session, creators, brief)
    scenario = build_scenario("coverage", assessments, by_id, signals, 10_000)
    assert scenario.roots_reached == 0
    assert scenario.picks  # still selected
