"""Recommendation-engine tests.

Built on an in-memory database so the guarantees are checked against
constructed cases rather than whatever the live data happens to contain.
"""

from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from signal_map.backend.budget import BudgetLine, summarize
from signal_map.backend.models import (
    Base,
    Brief,
    Client,
    ContactMethod,
    Creator,
    Quote,
    QuoteMessage,
    SocialAccount,
)
from signal_map.backend.recommend import (
    AXES,
    RULE_VERSION,
    VERDICTS,
    assess,
    budget_for,
    candidate_pool,
    run_recommendation,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess


def make_creator(
    session,
    name="Test Creator",
    *,
    tier="strategic",
    verticals="ai,developer_tools",
    market="europe_america",
    languages="en",
    audiences="developers",
    price=1000.0,
    content_format="x_thread",
    platform="X",
    is_package=False,
    needs_review=True,
    status="needs_review",
    promotion_level=None,
    with_contact=True,
    currency="USD",
) -> Creator:
    creator = Creator(
        display_name=name,
        creator_class="KOL",
        creator_tier=tier,
        verticals=verticals,
        market_region=market,
        market_region_basis="language" if market else "unknown",
        languages=languages,
        audience_types=audiences,
        audience_types_basis="llm" if audiences else "unknown",
        audience_types_evidence='developers:"ships code"' if audiences else None,
        promotion_level=promotion_level,
    )
    session.add(creator)
    session.flush()
    session.add(SocialAccount(creator_id=creator.id, platform=platform, handle=name, followers=50_000))
    if with_contact:
        session.add(ContactMethod(creator_id=creator.id, method_type="email", value="a@b.c"))
    if price is not None:
        message = QuoteMessage(creator_id=creator.id, raw_text=f"{content_format}: ${price}")
        session.add(message)
        session.flush()
        session.add(
            Quote(
                creator_id=creator.id,
                message_id=message.id,
                deliverable_raw=content_format,
                content_format=content_format,
                platform=platform,
                amount=price,
                amount_usd=price,
                currency=currency,
                is_package=is_package,
                status=status,
                needs_review=needs_review,
                parse_confidence="high",
            )
        )
    session.flush()
    session.refresh(creator)
    return creator


def make_brief(session, **kwargs) -> Brief:
    client = Client(name="ACME")
    session.add(client)
    session.flush()
    defaults = dict(
        client_id=client.id,
        name="Launch",
        verticals="ai",
        target_markets="europe_america",
        content_languages="en",
        target_audiences="developers",
        objectives="credibility",
        platforms="X",
        content_formats="x_thread",
        total_budget_usd=50_000,
        per_creator_budget_max_usd=5_000,
    )
    defaults.update(kwargs)
    brief = Brief(**defaults)
    session.add(brief)
    session.flush()
    return brief


# --- the core contract -------------------------------------------------------


def test_every_axis_is_always_returned(session):
    """Never one unexplainable score: every axis travels with every result.

    Asserted against ``AXES`` rather than a literal count, so adding an axis
    (Root signals were added as #13) does not require editing this test -- the
    contract is completeness, not a number.
    """
    creator = make_creator(session)
    result = assess(creator, make_brief(session))
    assert [a.key for a in result.axes] == [key for key, _ in AXES]
    assert len(result.axes) == len(AXES)


def test_every_axis_verdict_is_from_the_closed_set(session):
    result = assess(make_creator(session), make_brief(session))
    for axis in result.axes:
        assert axis.verdict in VERDICTS
        assert axis.detail  # never a bare verdict with no explanation


def test_unstated_preference_is_not_asked_not_a_penalty(session):
    """An axis the client never mentioned must not be scored as a failure."""
    creator = make_creator(session)
    brief = make_brief(session, content_formats=None, objectives=None)
    result = assess(creator, brief)
    assert result.axis("content_format").verdict == "not_asked"
    assert result.axis("objective").verdict == "not_asked"


def test_missing_data_is_unknown_and_never_removes_the_creator(session):
    """Absent data lowers confidence; it does not disprove fit."""
    creator = make_creator(
        session, market=None, languages=None, audiences=None, verticals=None
    )
    result = assess(creator, make_brief(session))
    assert result.axis("market_language").verdict == "unknown"
    assert result.axis("audience").verdict == "unknown"
    assert result.axis("content_vertical").verdict == "unknown"
    assert "目标人群" in result.missing
    assert result.rank_score > 0  # still rankable, still returned


def test_partially_known_axis_is_partial_not_unknown(session):
    """Market unknown but language matching is a real half-answer, and must
    not be flattened to "no data"."""
    creator = make_creator(session, market=None, languages="en")
    result = assess(creator, make_brief(session))
    assert result.axis("market_language").verdict == "partial"
    assert "市场待确认" in result.axis("market_language").detail


def test_mismatch_is_recorded_not_hidden(session):
    creator = make_creator(session, verticals="crypto", audiences="traders")
    result = assess(creator, make_brief(session))
    assert result.axis("content_vertical").verdict == "mismatch"
    assert any("内容与行业" in item for item in result.unmet)


def test_mismatched_creator_is_still_returned(session):
    """Only three things remove a creator, and a preference miss is not one --
    the client is entitled to see what Mango considered and why it ranked low."""
    make_creator(session, name="Off target", verticals="crypto", audiences="traders")
    brief = make_brief(session)
    _, picks, _ = run_recommendation(session, brief, limit=10, persist=False)
    assert [p.creator_name for p in picks] == ["Off target"]


# --- hard gates --------------------------------------------------------------


def test_non_creator_is_excluded_from_the_pool(session):
    """A news outlet or bot has no audience relationship to sell."""
    make_creator(session, name="Wire Service", tier="non_creator")
    make_creator(session, name="Real KOL", tier="strategic")
    assert [c.display_name for c in candidate_pool(session)] == ["Real KOL"]


def test_creator_without_a_priced_quote_is_excluded(session):
    make_creator(session, name="No price", price=None)
    make_creator(session, name="Priced", price=500.0)
    assert [c.display_name for c in candidate_pool(session)] == ["Priced"]


def test_client_excluded_creator_is_reported_not_silently_dropped(session):
    creator = make_creator(session, name="Rejected")
    make_creator(session, name="Kept")
    brief = make_brief(session, must_exclude_creator_ids=str(creator.id))
    _, picks, exclusions = run_recommendation(session, brief, limit=10, persist=False)
    assert [p.creator_name for p in picks] == ["Kept"]
    assert [e.creator_name for e in exclusions] == ["Rejected"]


def test_must_include_creator_is_pinned_with_axes_intact(session):
    """Pinning must not hide a miss -- the client still sees every axis."""
    weak = make_creator(session, name="Client pick", verticals="crypto", audiences="traders")
    make_creator(session, name="Strong fit")
    brief = make_brief(session, must_include_creator_ids=str(weak.id))
    _, picks, _ = run_recommendation(session, brief, limit=10, persist=False)
    assert picks[0].creator_name == "Client pick"
    assert picks[0].axis("content_vertical").verdict == "mismatch"


# --- budget ------------------------------------------------------------------


def test_over_budget_is_a_mismatch_with_the_number_shown(session):
    creator = make_creator(session, price=9_000.0)
    result = assess(creator, make_brief(session, per_creator_budget_max_usd=5_000))
    axis = result.axis("budget")
    assert axis.verdict == "mismatch"
    assert "9,000" in axis.detail


def test_budget_axis_names_the_deliverable_and_flags_off_spec(session):
    """A bare "$400 在预算区间内" reads as the price for what the client asked
    for. When the only quote is a different format, say so."""
    creator = make_creator(session, content_format="x_single_post", price=400.0)
    result = assess(creator, make_brief(session, content_formats="x_thread"))
    axis = result.axis("budget")
    assert "非所需形式" in axis.detail
    assert axis.verdict == "partial"


def test_package_blocks_an_exact_total():
    lines = [
        BudgetLine(1, "A", 1, "thread", unit_amount_usd=500, is_confirmed=True),
        BudgetLine(2, "B", 2, "bundle", unit_amount_usd=2000, is_package=True, is_confirmed=True),
    ]
    summary = summarize(lines, budget_usd=10_000)
    assert summary.needs_mango_confirmation
    assert not summary.is_exact
    assert "需要 Mango 确认" in summary.client_total_label()


def test_unpriced_line_is_not_counted_as_zero():
    """Folding an unknown in at zero would read as "free" rather than
    "unknown", and understate the total Mango has to honour."""
    lines = [
        BudgetLine(1, "A", 1, "thread", unit_amount_usd=500, is_confirmed=True),
        BudgetLine(2, "B", None, None, unit_amount_usd=None),
    ]
    summary = summarize(lines)
    assert summary.total_usd == 500
    assert summary.unpriced_lines == 1
    assert summary.needs_mango_confirmation


def test_mixed_currencies_are_flagged_as_a_reference_rate():
    lines = [
        BudgetLine(1, "A", 1, "x", unit_amount_usd=500, currency="USD", is_confirmed=True),
        BudgetLine(2, "B", 2, "y", unit_amount_usd=635, currency="GBP", is_confirmed=True),
    ]
    summary = summarize(lines)
    assert summary.needs_mango_confirmation
    assert any("币种" in note for note in summary.notes)


def test_clean_total_is_exact_and_printed_plainly():
    lines = [BudgetLine(1, "A", 1, "thread", unit_amount_usd=500, is_confirmed=True)]
    summary = summarize(lines, budget_usd=1_000)
    assert summary.is_exact
    assert summary.client_total_label() == "$500"
    assert summary.remaining_usd == 500


def test_quantity_multiplies_the_line():
    line = BudgetLine(1, "A", 1, "thread", quantity=3, unit_amount_usd=500)
    assert line.line_total_usd == 1500


def test_budget_for_uses_internal_price_not_the_client_price(session):
    """Internal planning uses amount_usd; the client surface must not."""
    make_creator(session, price=800.0)
    brief = make_brief(session)
    _, picks, _ = run_recommendation(session, brief, limit=5, persist=False)
    summary = budget_for(picks, brief)
    assert summary.total_usd == 800
    # Nothing was cleared for client display, so the client price stays absent.
    assert all(line.client_visible_price is None for line in summary.lines)


# --- quote certainty, risk, provenance --------------------------------------


def test_imported_quote_reads_as_needing_review(session):
    creator = make_creator(session, needs_review=True, status="needs_review")
    result = assess(creator, make_brief(session))
    assert result.axis("quote_certainty").verdict == "partial"
    assert "复核" in result.axis("quote_certainty").detail


def test_confirmed_quote_reads_as_confirmed(session):
    creator = make_creator(session, needs_review=False, status="confirmed_valid")
    result = assess(creator, make_brief(session))
    assert result.axis("quote_certainty").verdict == "match"


def test_high_promotion_is_a_risk_and_low_tolerance_makes_it_a_mismatch(session):
    creator = make_creator(session, promotion_level="High")
    strict = assess(creator, make_brief(session, risk_tolerance="low"))
    tolerant = assess(creator, make_brief(session, risk_tolerance="high"))
    assert strict.axis("risk").verdict == "mismatch"
    assert tolerant.axis("risk").verdict == "partial"


def test_missing_contact_raises_a_risk_and_a_next_step(session):
    creator = make_creator(session, with_contact=False)
    result = assess(creator, make_brief(session))
    assert any("联系方式" in risk for risk in result.risks)
    assert any("联系方式" in step for step in result.next_steps)


def test_reasons_are_generated_from_stored_fields(session):
    """No model writes recommendation prose -- reasons must be traceable to
    fields the database actually holds."""
    creator = make_creator(session)
    result = assess(creator, make_brief(session))
    assert result.reasons
    assert any("developer_tools" in reason or "ai" in reason for reason in result.reasons)


# --- run persistence ---------------------------------------------------------


def test_run_persists_axes_and_rule_version(session):
    make_creator(session)
    brief = make_brief(session)
    run, picks, _ = run_recommendation(session, brief, limit=5, persist=True)

    assert run.rule_version == RULE_VERSION
    assert run.candidate_pool_size == 1
    assert run.returned_count == 1

    stored = run.items[0]
    axes = json.loads(stored.axis_scores_json)
    assert len(axes) == len(AXES)
    assert json.loads(stored.reasons_json)
    assert stored.rank_score is not None


def test_run_snapshots_the_brief_so_later_edits_do_not_rewrite_history(session):
    make_creator(session)
    brief = make_brief(session, verticals="ai")
    run, _, _ = run_recommendation(session, brief, limit=5, persist=True)

    brief.verticals = "crypto"
    session.flush()

    snapshot = json.loads(run.brief_snapshot_json)
    assert snapshot["verticals"] == "ai"


def test_unsupported_filters_are_reported_not_silently_ignored(session):
    """A filter that quietly does nothing is worse than one that says so."""
    make_creator(session)
    brief = make_brief(session, creator_size_preference="mid")
    run, _, _ = run_recommendation(session, brief, limit=5, persist=False)
    assert run.unsupported_filters
    assert "体量" in run.unsupported_filters


def test_better_fit_outranks_worse_fit(session):
    make_creator(session, name="Weak", verticals="crypto", audiences="traders", price=500.0)
    make_creator(session, name="Strong", verticals="ai,developer_tools", audiences="developers", price=500.0)
    _, picks, _ = run_recommendation(session, make_brief(session), limit=5, persist=False)
    assert picks[0].creator_name == "Strong"


# --- attention path (path 3) -------------------------------------------------


def test_no_root_signal_is_unknown_not_mismatch(session):
    """We checked some Roots, not all of them. Absence in a partial sample
    disproves nothing, so it can never read as a conflict."""
    result = assess(make_creator(session), make_brief(session))
    axis = result.axis("attention_path")
    assert axis.verdict == "unknown"
    assert "关系数据待补充" in axis.detail


def test_no_root_signal_never_removes_a_creator(session):
    """Root signals are an added axis, not a filter."""
    make_creator(session, name="No roots")
    _, picks, _ = run_recommendation(session, make_brief(session), limit=5, persist=False)
    assert [p.creator_name for p in picks] == ["No roots"]


def test_a_follow_signal_caps_at_partial_never_match(session):
    """A bare follow is 研究线索. Only interaction evidence may raise it, so
    the axis must not present a follow as a confirmed relationship."""
    from types import SimpleNamespace

    signals = [
        SimpleNamespace(
            source_handle="AndrewYNg", signal_type="follow",
            confidence="research_lead", coverage_limitation=None,
        )
    ]
    result = assess(make_creator(session), make_brief(session), signals)
    axis = result.axis("attention_path")
    assert axis.verdict == "partial"
    assert "@AndrewYNg" in axis.detail
    assert "关注信号" in axis.detail  # labelled as a follow, not a relationship


def test_truncated_root_collection_is_disclosed_on_the_axis(session):
    from types import SimpleNamespace

    signals = [
        SimpleNamespace(
            source_handle="minchoi", signal_type="follow",
            confidence="research_lead", coverage_limitation="truncated at 8 pages",
        )
    ]
    result = assess(make_creator(session), make_brief(session), signals)
    assert "部分名单未采全" in result.axis("attention_path").detail


def test_interaction_evidence_reads_differently_from_a_follow(session):
    """A follow and a reply are different facts; the axis must not blur them."""
    from types import SimpleNamespace

    def sig(kind, confidence="research_lead"):
        return SimpleNamespace(
            source_handle="minchoi", signal_type=kind, confidence=confidence,
            occurred_at=None, evidence_url="", coverage_limitation=None,
        )

    follow_only = assess(make_creator(session), make_brief(session), [sig("follow")])
    with_reply = assess(
        make_creator(session, name="B"), make_brief(session), [sig("follow"), sig("reply")]
    )
    assert "关注信号" in follow_only.axis("attention_path").detail
    assert "历史熟悉度信号" in with_reply.axis("attention_path").detail


def test_repeated_recent_interaction_earns_high_confidence_wording(session):
    from types import SimpleNamespace

    signals = [
        SimpleNamespace(
            source_handle="minchoi", signal_type="reply",
            confidence="high_confidence_inference", occurred_at=None,
            evidence_url="", coverage_limitation=None,
        )
    ]
    detail = assess(make_creator(session), make_brief(session), signals).axis(
        "attention_path"
    ).detail
    assert "高概率推断" in detail


def test_no_signal_grade_ever_claims_a_verified_relationship(session):
    """Nothing collected from an API may read as 已验证 -- that needs a human."""
    from types import SimpleNamespace

    for index, confidence in enumerate(("research_lead", "high_confidence_inference")):
        signals = [
            SimpleNamespace(
                source_handle="x", signal_type="reply", confidence=confidence,
                occurred_at=None, evidence_url="", coverage_limitation=None,
            )
        ]
        creator = make_creator(session, name=f"Grade {index}")
        axis = assess(creator, make_brief(session), signals).axis("attention_path")
        assert axis.verdict == "partial"  # never "match"
        # The word may appear as a *disclaimer* ("非已验证关系"). What is banned
        # is asserting it -- so check for the claim, not the substring.
        claim = re.search(r"(?<!非)已验证", axis.detail)
        assert claim is None, axis.detail
        assert "确定" not in axis.detail and "一定" not in axis.detail
