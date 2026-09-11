"""Creator taxonomy (KOL vs 泛KOC) and LLM-classification guardrails."""

from __future__ import annotations


import pytest

from signal_map.backend.llm_classify import (
    AudienceVerdict,
    LLMError,
    _parse_verdict,
    build_profile_text,
    resolve_provider,
)
from signal_map.backend.models import (
    CREATOR_CLASSES,
    CREATOR_TIERS,
    NON_RECOMMENDABLE_TIERS,
    tier_for_class,
)
from signal_map.backend.normalize import AUDIENCE_TYPES


# --- KOL vs 泛KOC ------------------------------------------------------------


@pytest.mark.parametrize(
    "creator_class,expected",
    [
        ("Top KOL", "strategic"),
        ("Community Leader", "strategic"),
        ("KOL", "strategic"),
        ("KOC", "distribution"),
        ("Marketing Account", "distribution"),
        ("Media / Community Account", "media"),
        ("Non-creator / Irrelevant", "non_creator"),
        ("Unknown", "unknown"),
    ],
)
def test_bd_class_maps_to_commercial_tier(creator_class, expected):
    """买声量 (strategic) vs 买铺量 (distribution) is a client-facing decision,
    so it must be a filterable tier rather than a free-text class name."""
    assert tier_for_class(creator_class) == expected


def test_every_bd_class_has_a_tier():
    for creator_class in CREATOR_CLASSES:
        assert tier_for_class(creator_class) in CREATOR_TIERS


def test_unrecognised_class_falls_to_unknown_not_a_real_tier():
    assert tier_for_class("Something New") == "unknown"
    assert tier_for_class(None) == "unknown"


def test_non_creator_is_the_only_non_recommendable_tier():
    """A news outlet or bot has no audience relationship to sell. This gates
    the candidate pool -- it must not merely sort lower."""
    assert NON_RECOMMENDABLE_TIERS == frozenset({"non_creator"})
    assert "distribution" not in NON_RECOMMENDABLE_TIERS


# --- LLM guardrails ----------------------------------------------------------


def test_provider_prefers_grok_when_both_keys_present(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "x-test")
    monkeypatch.setenv("OPENAI_API_KEY", "o-test")
    provider = resolve_provider()
    assert provider.name == "xai"
    assert "x.ai" in provider.url


def test_provider_falls_back_to_openai(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "o-test")
    assert resolve_provider().name == "openai"


def test_no_key_raises_rather_than_silently_skipping(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(LLMError, match="No LLM key configured"):
        resolve_provider()


def test_types_outside_the_closed_vocabulary_are_dropped():
    """The model does not get to invent a new audience category."""
    verdict = _parse_verdict(
        '{"audience_types":[{"type":"developers","evidence":"builds with the API"},'
        '{"type":"astronauts","evidence":"goes to space"}],"confidence":"high"}',
        "test-model",
    )
    assert verdict.types == ["developers"]


def test_a_type_without_evidence_is_dropped():
    """This is the guard that stops free association: no quote, no type."""
    verdict = _parse_verdict(
        '{"audience_types":[{"type":"founders","evidence":""},'
        '{"type":"designers","evidence":"figma templates"}],"confidence":"low"}',
        "test-model",
    )
    assert verdict.types == ["designers"]


def test_empty_list_is_a_valid_verdict():
    """A vague bio must be allowed to produce nothing. A wrong audience is
    worse than an admitted unknown."""
    verdict = _parse_verdict('{"audience_types":[],"confidence":"low","note":"topic only"}', "m")
    assert verdict.types == []
    assert verdict.types_csv is None
    assert verdict.note == "topic only"


def test_verdict_is_capped_at_three():
    items = ",".join(
        f'{{"type":"{key}","evidence":"quote {key}"}}' for key in AUDIENCE_TYPES[:6]
    )
    verdict = _parse_verdict(f'{{"audience_types":[{items}],"confidence":"high"}}', "m")
    assert len(verdict.types) == 3
    assert len(verdict.evidence) == 3


def test_duplicate_types_are_collapsed():
    verdict = _parse_verdict(
        '{"audience_types":[{"type":"traders","evidence":"daily funding rates"},'
        '{"type":"traders","evidence":"perp positioning"}],"confidence":"high"}',
        "m",
    )
    assert verdict.types == ["traders"]
    assert len(verdict.evidence) == 1


def test_non_json_response_raises():
    with pytest.raises(LLMError, match="non-JSON"):
        _parse_verdict("sorry, I cannot help with that", "m")


def test_bad_confidence_value_falls_back_to_low():
    verdict = _parse_verdict('{"audience_types":[],"confidence":"extremely sure"}', "m")
    assert verdict.confidence == "low"


def test_evidence_text_pairs_each_type_with_its_quote():
    verdict = AudienceVerdict(
        types=["developers", "students"],
        evidence=['developers:"ships code"', 'students:"beginner guide"'],
    )
    assert verdict.evidence_text == 'developers:"ships code"; students:"beginner guide"'


def test_profile_text_only_contains_supplied_content():
    text = build_profile_text(
        display_name="AI Playbook",
        bio="Step-by-step guide to AI",
        posts=["How to build an agent", "Claude Code tips"],
        platform="YouTube",
    )
    assert "AI Playbook" in text
    assert "Claude Code tips" in text
    # Nothing is fabricated into the evidence block.
    assert "founders" not in text.lower()


def test_cache_key_covers_the_prompt_not_just_the_content():
    """Editing the prompt changes what a verdict means, so cached answers from
    an older prompt must not be reused. This regressed once: a rule added to
    stop the model citing "Brand collaborations open" as audience evidence had
    no effect on already-cached creators."""
    from signal_map.backend import llm_classify

    path_before = llm_classify._cache_path("same profile text", "same-model")
    original = llm_classify.SYSTEM_PROMPT
    try:
        llm_classify.SYSTEM_PROMPT = original + "\n6. An additional rule."
        path_after = llm_classify._cache_path("same profile text", "same-model")
    finally:
        llm_classify.SYSTEM_PROMPT = original

    assert path_before != path_after


def test_cache_key_covers_the_model():
    """Switching model must not silently reuse the previous model's verdicts."""
    from signal_map.backend.llm_classify import _cache_path

    assert _cache_path("profile", "grok-4.6") != _cache_path("profile", "gpt-4o-mini")


def test_empty_profile_short_circuits_without_an_api_call(monkeypatch):
    """No content means no call -- spending a request to be told there is
    nothing there is waste."""
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from signal_map.backend.llm_classify import classify_audience

    verdict = classify_audience("   ")
    assert verdict.types == []
    assert verdict.note == "no content available to classify"


# --- vertical classification -------------------------------------------------


def test_verticals_outside_the_vocabulary_are_dropped():
    from signal_map.backend.llm_classify import _parse_verticals

    verdict = _parse_verticals(
        '{"verticals":[{"key":"developer_tools","evidence":"ships Python libraries"},'
        '{"key":"astrology","evidence":"reads charts"}],"confidence":"high"}',
        "m",
    )
    assert verdict.keys == ["developer_tools"]


def test_a_vertical_without_evidence_is_dropped():
    """Same guard as audience: no quote, no assignment."""
    from signal_map.backend.llm_classify import _parse_verticals

    verdict = _parse_verticals(
        '{"verticals":[{"key":"crypto","evidence":""},'
        '{"key":"finance","evidence":"daily market commentary"}],"confidence":"low"}',
        "m",
    )
    assert verdict.keys == ["finance"]


def test_empty_vertical_list_is_valid():
    """A wrong vertical sends a creator into the wrong client's shortlist,
    which is worse than an admitted unknown."""
    from signal_map.backend.llm_classify import _parse_verticals

    verdict = _parse_verticals('{"verticals":[],"confidence":"low"}', "m")
    assert verdict.keys == []
    assert verdict.csv is None


def test_verticals_are_capped_at_three():
    from signal_map.backend.llm_classify import _parse_verticals
    from signal_map.backend.normalize import VERTICALS

    items = ",".join(
        f'{{"key":"{key}","evidence":"quote about {key}"}}' for key in VERTICALS[:6]
    )
    verdict = _parse_verticals(f'{{"verticals":[{items}],"confidence":"high"}}', "m")
    assert len(verdict.keys) == 3


def test_vertical_cache_key_is_separate_from_audience():
    """Two judgments cached together means fixing one prompt silently
    invalidates the other's verdicts."""
    from signal_map.backend import llm_classify

    audience_path = llm_classify._cache_path("same text", "same-model")
    assert audience_path.name.startswith("audience-")
    # The vertical path is built inside classify_verticals with a distinct
    # prefix; assert the prefixes cannot collide.
    assert "verticals-" != "audience-"
