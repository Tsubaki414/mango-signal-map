"""Quote-parser tests.

Every multi-item case here is a verbatim string from the Mango BD data, not a
synthetic example. These are the exact shapes the previous parser mangled, so
regressions show up as a real row a client would have seen.
"""

from __future__ import annotations

import pytest

from signal_map.backend.quotes import (
    FX_TO_USD,
    classify_deliverable,
    parse_quote_message,
    to_usd,
)


def labels_and_amounts(text: str, currency: str = "USD") -> list[tuple[str, float | None]]:
    return [(q.deliverable_raw, q.amount) for q in parse_quote_message(text, currency)]


# --- the defects that motivated the fork ------------------------------------


def test_slash_separated_items_split_into_separate_rows():
    """Real row that the BD parser turned into one $30 quote whose label was
    'Quote =   / Thread $100 / Single $80'."""
    result = labels_and_amounts("Quote = $30 / Thread $100 / Single $80")
    assert [amount for _, amount in result] == [30.0, 100.0, 80.0]
    assert [label for label, _ in result] == ["Quote =", "Thread", "Single"]


def test_glued_lowercase_digit_boundary_splits():
    """'single post80 usd for quote' -- no separator at all between items."""
    result = labels_and_amounts("150 for video/thread post; 130 for single post80 usd for quote")
    assert [amount for _, amount in result] == [150.0, 130.0, 80.0]


def test_glued_currency_and_capital_splits():
    result = labels_and_amounts("Single Sponsored Post: $150 USDDedicated Thread: $200 USD")
    assert [amount for _, amount in result] == [150.0, 200.0]
    assert result[1][0] == "Dedicated Thread"


def test_no_label_ever_retains_a_price():
    """The BD parser built labels from before+after concatenated, so a failed
    split leaked the next item's price into the label. Scanning must not."""
    messy = [
        "Quote reposts: $80• Dedicated posts: $150• Threads: $250",
        "X Post – $500Thread – $800QRT/comment amplification – from $200",
        "Thread post : $150 , only post - $100 , Qoute post $50 .",
        "Single X post: $500 Quote repost: $200",
        "> Single Tweet - $100 > 1x Thread - $150> Quote Tweet - $50",
    ]
    for text in messy:
        for quote in parse_quote_message(text):
            if quote.amount is not None:
                assert "$" not in quote.deliverable_raw, (text, quote.deliverable_raw)


def test_package_is_detected():
    """``is_package`` was False on all 699 BD rows, including ones labelled
    'Package'. A package summed with single-item prices produces a total
    Mango cannot honour, so this flag has to be right."""
    quotes = parse_quote_message(
        "Collaboration packages (all four platforms): 1 post — $799; 2 posts — $999"
    )
    assert all(q.is_package for q in quotes)


def test_bundle_totals_are_not_split_into_components():
    text = (
        "Bundle A(1 IG Reel+1 LinkedIn视频) $40,000；"
        "Bundle B(IG Reel跨平台+Newsletter) $50,000"
    )
    amounts = [q.amount for q in parse_quote_message(text)]
    assert amounts == [40000.0, 50000.0]


# --- amount shapes -----------------------------------------------------------


def test_range_becomes_min_max_and_midpoint():
    (quote,) = [q for q in parse_quote_message("Sponsored tweets: $200–400") if q.amount]
    assert (quote.amount_min, quote.amount_max, quote.amount) == (200.0, 400.0, 300.0)


def test_range_is_one_row_not_two():
    assert len([q for q in parse_quote_message("Threads: $250-700") if q.amount]) == 1


def test_k_multiplier():
    (quote,) = [q for q in parse_quote_message("Dedicated thread: £6k") if q.amount]
    assert quote.amount == 6000.0
    assert quote.currency == "GBP"


def test_currency_word_prefix():
    """'USD 1600' -- a shape the BD parser missed entirely."""
    quotes = [q for q in parse_quote_message("USD 1600 => Dedicated long-form Tweet") if q.amount]
    assert quotes[0].amount == 1600.0
    assert quotes[0].currency == "USD"


def test_currency_words_and_symbols():
    assert [q.currency for q in parse_quote_message("899 pounds") if q.amount] == ["GBP"]
    assert [q.currency for q in parse_quote_message("1000美元/条") if q.amount] == ["USD"]
    assert [q.currency for q in parse_quote_message("Essential €5,500") if q.amount] == ["EUR"]


def test_glued_list_index_is_not_read_as_a_price():
    """'...350$2. Per single post' -- the '$' closing 350$ must not be read as
    opening '$2'."""
    amounts = [q.amount for q in parse_quote_message("thread350$2. Per single post $75")]
    assert 2.0 not in amounts


def test_turnaround_and_percentages_are_not_prices():
    for text in ("Delivery 7-10 days", "Exclusivity +40%"):
        assert all(q.amount is None for q in parse_quote_message(text))


# --- refusals and non-prices -------------------------------------------------


def test_declined_to_quote_is_preserved_not_dropped():
    """"They wouldn't give a price" is commercially meaningful -- it becomes a
    询价 task, so it must survive as a row."""
    quotes = parse_quote_message("未报价 — 要求先提供项目细节才回复具体价格")
    assert len(quotes) == 1
    assert quotes[0].status_hint == "no_quote"
    assert quotes[0].amount is None


def test_media_kit_on_request_is_a_refusal():
    (quote,) = parse_quote_message("Media kit and rates available on request")
    assert quote.status_hint == "no_quote"


def test_complimentary_is_not_a_refusal_and_not_a_price():
    (quote,) = parse_quote_message("Single LinkedIn post: Complimentary")
    assert quote.amount is None
    assert quote.status_hint != "no_quote"
    assert "complimentary" in quote.flags


def test_unparseable_text_never_invents_a_number():
    for quote in parse_quote_message("Rates depend on the brief and content complexity"):
        assert quote.amount is None
        assert quote.parse_confidence == "unparsed"


def test_empty_input_returns_nothing():
    assert parse_quote_message("") == []
    assert parse_quote_message("   ") == []


# --- classification ----------------------------------------------------------


@pytest.mark.parametrize(
    "label,expected_format,expected_platform",
    [
        ("X thread", "x_thread", "X"),
        ("Dedicated Thread", "x_thread", "X"),
        ("Quote repost", "x_quote_repost", "X"),
        ("QRT", "x_quote_repost", "X"),
        ("Quote", "x_quote_repost", "X"),
        ("Single post", "x_single_post", "X"),
        ("Sponsored tweets", "x_single_post", "X"),
        ("YT专属视频", "youtube_dedicated", "YouTube"),
        ("YT植入", "youtube_integration", "YouTube"),
        ("YouTube Short", "youtube_short", "YouTube"),
        ("IG Reel", "instagram_reel", "Instagram"),
        ("Newsletter", "newsletter", "Newsletter"),
    ],
)
def test_classification(label, expected_format, expected_platform):
    assert classify_deliverable(label) == (expected_format, expected_platform)


def test_cjk_latin_boundary_classifies():
    """``\\b`` does not fire between CJK and Latin in Python, so these exact
    strings silently fell through to 'unknown' before."""
    assert classify_deliverable("LinkedIn帖")[0] == "linkedin_post"
    assert classify_deliverable("1条TikTok")[0] == "tiktok_video"


def test_negated_offering_is_not_classified_as_offered():
    """Real row: "(不做专属视频);Newsletter(115K+订阅)顶部". Matching through
    the negation classified it as the very format the creator refused, and the
    recommendation then told a client "已有 youtube_dedicated 的报价"."""
    assert classify_deliverable("(不做专属视频);Newsletter(115K+订阅)顶部") == (
        "newsletter",
        "Newsletter",
    )


def test_negation_only_label_classifies_to_nothing():
    assert classify_deliverable("不做专属视频") == ("unknown", "Unknown")


def test_bare_shorts_is_youtube_but_singular_short_video_is_not():
    """"Shorts" alone is YouTube-specific; "Post + Short Video" is an X post."""
    assert classify_deliverable("Shorts") == ("youtube_short", "YouTube")
    assert classify_deliverable("Post + Short Video")[0] == "x_single_post"


def test_unclassifiable_label_stays_unknown_not_guessed():
    assert classify_deliverable("+VAT(") == ("unknown", "Unknown")
    assert classify_deliverable("") == ("unknown", "Unknown")


def test_x_thread_beats_meta_threads_when_x_is_explicit():
    assert classify_deliverable("X threads")[0] == "x_thread"
    assert classify_deliverable("Threads")[1] == "Threads"


# --- currency conversion -----------------------------------------------------


def test_to_usd_converts_and_reports_rate():
    amount, rate = to_usd(100.0, "GBP")
    assert rate == FX_TO_USD["GBP"]
    assert amount == pytest.approx(127.0)


def test_unknown_currency_yields_none_not_a_usd_assumption():
    """Assuming USD for an unrecognised currency silently corrupts every
    budget total downstream."""
    assert to_usd(100.0, "XYZ") == (None, None)


def test_to_usd_none_in_none_out():
    assert to_usd(None, "USD") == (None, None)


# --- client-facing price bands -----------------------------------------------


def test_band_places_a_cost_without_revealing_it():
    from signal_map.backend.quotes import price_band

    assert price_band(300) == "$500 以下"
    assert price_band(1_300) == "$1,000–2,500"
    assert price_band(75_000) == "$50,000 以上"


def test_band_is_wide_enough_not_to_back_solve_the_cost():
    """Two quite different costs must land in the same band, or the band
    leaks the figure it exists to hide."""
    from signal_map.backend.quotes import price_band

    assert price_band(1_100) == price_band(2_400)


def test_unknown_amount_has_no_band():
    from signal_map.backend.quotes import price_band

    assert price_band(None) is None


# --- bare amounts: the shape-inferred fallback --------------------------------


def test_a_message_that_is_only_a_number_is_a_price():
    """39 creators had no price at all because their quote read "300".

    They sat outside the recommendable pool entirely -- roughly 15% of priced
    inventory, invisible rather than merely uncertain.
    """
    rows = parse_quote_message("300")
    assert len(rows) == 1
    assert rows[0].amount == 300.0
    assert rows[0].currency == "USD"
    assert rows[0].parse_confidence == "low"
    assert "amount_inferred_from_message_shape" in rows[0].flags


def test_bare_amounts_keep_their_labels():
    rows = parse_quote_message("Single 250; Thread 400; Quote 200")
    assert [(r.deliverable_raw, r.amount) for r in rows] == [
        ("Single", 250.0), ("Thread", 400.0), ("Quote", 200.0)
    ]
    assert {r.content_format for r in rows} == {"x_single_post", "x_thread", "x_quote_repost"}


def test_a_bare_range_parses_as_a_range():
    rows = parse_quote_message("Rates depend on post type 200-500")
    assert len(rows) == 1
    assert (rows[0].amount_min, rows[0].amount_max) == (200.0, 500.0)
    # And the decline patterns must not win over a real number: "depends on"
    # matches, but the message does state a price.
    assert rows[0].status_hint != "no_quote"


def test_durations_and_percentages_are_never_prices():
    """The guard the fallback must not break. Both live in real messages."""
    for text in (
        "含1轮修改,交付7-10工作日",
        "付费投放+30-50%;独家+40%",
        "专属视频(6-8min);植入(60-90s)",
        "2 revisions included",
    ):
        assert all(r.amount is None for r in parse_quote_message(text)), text


def test_the_bare_scan_never_runs_when_the_message_names_a_currency():
    """Message-level guard, not a numeric floor -- the real minimum quote in
    this dataset is $20, so any floor high enough to exclude "2 revisions"
    would discard genuine cheap quotes."""
    rows = parse_quote_message("专属视频 $1,300;植入 $500;含1轮修改,交付7-10工作日")
    assert sorted(r.amount for r in rows if r.amount) == [500.0, 1300.0]
    assert all("amount_inferred_from_message_shape" not in r.flags for r in rows)


def test_an_unlabelled_amount_stores_no_placeholder():
    """``deliverable_raw`` holds what the creator wrote. "(unlabelled)" is not
    something anyone wrote, and it reached a client card as
    "$500 以下（(unlabelled)）"."""
    row = parse_quote_message("300")[0]
    assert row.deliverable_raw is None


def test_declining_to_quote_is_a_finished_answer_not_a_pending_one():
    for text in ("Not provided", "Flexible; negotiable", "Tailored to campaign scope",
                 "Varies by scope and deliverables", "Media kit available on request"):
        rows = parse_quote_message(text)
        assert any(r.status_hint == "no_quote" for r in rows), text
