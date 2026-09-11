"""Migration tests.

These run against the real BD database when it is present, because the whole
point of the migration is fidelity to that specific data. They are skipped
rather than failed when it is absent, so the suite still runs on a machine
that only has the Signal Map side.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import func, or_, select

from signal_map.backend import db as sm_db
from signal_map.backend.models import ContactMethod, Creator, Quote, QuoteMessage
from signal_map.backend.normalize import (
    derive_audience_types,
    derive_market_region,
    follower_tier,
    is_global_scope,
    normalize_country,
    normalize_language,
    normalize_verticals,
)
from signal_map.scripts.migrate_from_bd import BD_DB_PATH

pytestmark = pytest.mark.skipif(
    not BD_DB_PATH.exists() or not sm_db.DB_PATH.exists(),
    reason="requires the BD source database and a completed migration",
)


@pytest.fixture(scope="module")
def session():
    with sm_db.get_session() as sess:
        yield sess


@pytest.fixture(scope="module")
def bd():
    con = sqlite3.connect(f"file:{BD_DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    yield con
    con.close()


# --- the isolation guarantees -----------------------------------------------


def test_cost_and_client_price_stay_separate_columns(session):
    """Current policy is pass-through: no markup, the client is quoted the
    creator's own rate. The columns stay distinct anyway, so a markup can be
    applied later without re-deriving cost -- and so the *rule* that they are
    different things outlives the current policy."""
    quote = session.scalar(
        select(Quote).where(Quote.client_price_usd.is_not(None)).limit(1)
    )
    assert quote is not None
    assert quote.internal_cost_usd is not None
    # Equal today by decision, not because one column is an alias of the other.
    assert quote.client_price_usd == quote.internal_cost_usd


def test_a_quote_not_cleared_for_display_carries_no_client_price(session):
    """Pass-through applies only to quotes that passed the parse gate."""
    leaked = session.scalar(
        select(func.count())
        .select_from(Quote)
        .where(~Quote.client_visible, Quote.client_price_usd.is_not(None))
    )
    assert leaked == 0


def test_cost_is_recorded_because_provenance_is_known(session):
    """These prices were collected first-hand by Mango from the creators, so
    the figure is a known cost rather than an undifferentiated number."""
    priced = session.scalar(
        select(func.count()).select_from(Quote).where(Quote.amount_usd.is_not(None))
    )
    with_cost = session.scalar(
        select(func.count()).select_from(Quote).where(Quote.internal_cost_usd.is_not(None))
    )
    assert with_cost == priced > 0


def test_client_display_price_is_gated_on_visibility_not_on_cost(session):
    """The accessor reads ``client_price_usd`` and nothing else. It must never
    fall back to ``internal_cost_usd`` -- today they happen to be equal, but a
    fallback would silently publish cost the moment a markup is introduced."""
    for quote in session.scalars(select(Quote).limit(50)):
        if quote.client_visible:
            assert quote.client_display_price == quote.client_price_usd
        else:
            assert quote.client_display_price is None


def test_client_visible_quotes_carry_both_a_figure_and_a_band(session):
    """The band survives pass-through pricing: it is what a surface falls back
    to if a price is ever withdrawn, and what a scenario shows in aggregate."""
    visible = list(
        session.scalars(select(Quote).where(Quote.client_visible).limit(50))
    )
    assert visible
    for quote in visible:
        assert quote.client_display_band
        assert quote.client_display_price is not None


def test_only_trustworthy_parses_reach_a_client_surface(session):
    """Provenance is settled; parse quality is what still gates display."""
    leaked = session.scalar(
        select(func.count())
        .select_from(Quote)
        .where(Quote.client_visible, Quote.parse_confidence.notin_(["high", "medium"]))
    )
    assert leaked == 0


def test_unpriced_and_declined_quotes_are_not_client_visible(session):
    assert (
        session.scalar(
            select(func.count())
            .select_from(Quote)
            .where(Quote.client_visible, Quote.amount_usd.is_(None))
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count())
            .select_from(Quote)
            .where(Quote.client_visible, Quote.status == "no_quote")
        )
        == 0
    )


def test_quote_source_records_first_party_collection(session):
    """Mango collected these directly, so the source is a recorded fact rather
    than an inference."""
    direct = session.scalar(
        select(func.count())
        .select_from(QuoteMessage)
        .where(QuoteMessage.source == "kol_direct", ~QuoteMessage.source_is_inferred)
    )
    assert direct > 0


# --- fidelity to the source --------------------------------------------------


def test_all_creators_migrated(session, bd):
    """每条 BD creator 都migrate 过来了。

    只数 **BD 来源**的行，不数总数。本地新建的 creator 现在是系统设计的一部分
    （客户表达兴趣会把一个发现对象升级成可复用的供给记录），断言总数会让这条
    合法路径把测试打挂 —— 而那正是这个测试该保护的东西之一。
    """
    migrated = session.scalar(
        select(func.count()).select_from(Creator).where(
            Creator.source_system == "mango_bd"
        )
    )
    assert migrated == bd.execute("SELECT COUNT(*) FROM creators").fetchone()[0]


def test_all_contacts_migrated(session, bd):
    assert session.scalar(select(func.count()).select_from(ContactMethod)) == bd.execute(
        "SELECT COUNT(*) FROM contact_methods"
    ).fetchone()[0]


def test_no_priced_creator_was_lost(session, bd):
    """The re-parse must not lose supply. BD's 301 "creators with a rate card"
    included 41 whose rows held no parseable price at all, so the meaningful
    baseline is creators with at least one priced row."""
    bd_priced = bd.execute(
        "SELECT COUNT(DISTINCT creator_id) FROM rate_cards WHERE quote_amount_usd IS NOT NULL"
    ).fetchone()[0]
    migrated = session.scalar(
        select(func.count(func.distinct(Quote.creator_id))).where(Quote.amount_usd.is_not(None))
    )
    assert migrated >= bd_priced


def test_reparse_recovers_more_prices_than_bd_had(session, bd):
    bd_priced_rows = bd.execute(
        "SELECT COUNT(*) FROM rate_cards WHERE quote_amount_usd IS NOT NULL"
    ).fetchone()[0]
    migrated = session.scalar(
        select(func.count()).select_from(Quote).where(Quote.amount_usd.is_not(None))
    )
    assert migrated > bd_priced_rows


def test_every_quote_keeps_a_link_to_verbatim_source_text(session):
    """Re-parsing must always stay possible, so no quote may float free of the
    message it came from."""
    orphans = session.scalar(
        select(func.count())
        .select_from(Quote)
        .outerjoin(QuoteMessage, Quote.message_id == QuoteMessage.id)
        .where(QuoteMessage.id.is_(None))
    )
    assert orphans == 0


def test_raw_text_is_preserved_verbatim(session, bd):
    """Byte-identical, not normalised -- the normalised copy is only ever a
    working value inside the parser."""
    bd_texts = {
        row[0] for row in bd.execute("SELECT DISTINCT raw_quote_text FROM rate_cards WHERE raw_quote_text != ''")
    }
    migrated = {text for (text,) in session.execute(select(QuoteMessage.raw_text)).all()}
    assert migrated <= bd_texts
    assert len(migrated) > 0


def test_declined_quotes_survive_as_rows(session):
    """"They refused to price it" generates a 询价 task, so it must not be
    silently dropped during import."""
    assert session.scalar(
        select(func.count()).select_from(Quote).where(Quote.status == "no_quote")
    ) > 0


def test_priced_quotes_carry_currency_and_fx_provenance(session):
    for quote in session.scalars(select(Quote).where(Quote.amount_usd.is_not(None)).limit(100)):
        assert quote.currency
        assert quote.fx_rate_used is not None
        assert quote.fx_asof


def test_quote_date_is_not_invented(session):
    """BD stored no quote date. Back-filling it from the import date would
    manufacture a fact the client would then rely on."""
    assert (
        session.scalar(
            select(func.count()).select_from(QuoteMessage).where(QuoteMessage.quoted_at.is_not(None))
        )
        == 0
    )


def test_measured_audience_geography_is_never_inferred(session):
    """``market_region`` is a coarse inference; ``audience_markets`` is measured
    audience geography. Collapsing them would let a language-derived guess be
    read as something Mango actually observed, so this column stays empty until
    real research fills it."""
    assert (
        session.scalar(
            select(func.count()).select_from(Creator).where(Creator.audience_markets.is_not(None))
        )
        == 0
    )


def test_market_region_covers_most_priced_inventory(session):
    """The whole point of the coarse bucket: a country-based market filter
    matched 42 of 262 priced creators and would have hidden the rest."""
    priced = select(Quote.creator_id).where(Quote.amount_usd.is_not(None)).distinct().subquery()
    total = session.scalar(select(func.count()).select_from(priced))
    covered = session.scalar(
        select(func.count())
        .select_from(Creator)
        .join(priced, Creator.id == priced.c.creator_id)
        .where(Creator.market_region.is_not(None))
    )
    assert covered / total > 0.85


def test_every_market_region_states_its_basis(session):
    """A client card must be able to say 依据：内容语言 rather than implying
    Mango measured an audience."""
    bad = session.scalar(
        select(func.count())
        .select_from(Creator)
        .where(Creator.market_region.is_not(None), Creator.market_region_basis == "unknown")
    )
    assert bad == 0


def test_audience_types_cover_most_priced_inventory(session):
    """目标人群 matched zero creators before this field existed, so the filter
    was unusable. Threshold is set to hold after migration alone, without the
    enrichment pass."""
    priced = select(Quote.creator_id).where(Quote.amount_usd.is_not(None)).distinct().subquery()
    total = session.scalar(select(func.count()).select_from(priced))
    covered = session.scalar(
        select(func.count())
        .select_from(Creator)
        .join(priced, Creator.id == priced.c.creator_id)
        .where(Creator.audience_types.is_not(None))
    )
    assert covered / total > 0.80


def test_every_audience_type_states_its_basis_and_evidence(session):
    """Some of the input (recent posts) is transient, so an assignment without
    stored evidence would be permanently unauditable."""
    bad = session.scalar(
        select(func.count())
        .select_from(Creator)
        .where(
            Creator.audience_types.is_not(None),
            or_(
                Creator.audience_types_basis == "unknown",
                Creator.audience_types_evidence.is_(None),
            ),
        )
    )
    assert bad == 0


def test_creators_without_a_market_signal_are_still_present(session):
    """They must be filterable-around, not deleted -- a creator with no market
    signal is still sellable inventory."""
    assert (
        session.scalar(
            select(func.count()).select_from(Creator).where(Creator.market_region.is_(None))
        )
        > 0
    )


def test_raw_values_preserved_for_renormalisation(session, bd):
    """A future normaliser fix must be re-derivable without touching kol.db."""
    bd_with_language = bd.execute(
        "SELECT COUNT(*) FROM creators WHERE language IS NOT NULL"
    ).fetchone()[0]
    kept = session.scalar(
        select(func.count()).select_from(Creator).where(Creator.language_raw.is_not(None))
    )
    assert kept == bd_with_language


# --- normalisers -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("English", "en"), ("en", "en"), ("Spanish", "es"), ("es", "es"),
     ("Portuguese", "pt"), ("Amharic", "am"), ("English, Spanish", "en,es")],
)
def test_language_normalisation(raw, expected):
    assert normalize_language(raw) == expected


def test_unknown_language_returns_none_not_a_guess():
    assert normalize_language("Klingon") is None
    assert normalize_language(None) is None


@pytest.mark.parametrize(
    "raw,expected",
    [("United States", "US"), ("USA", "US"), ("US", "US"), ("NYC", "US"),
     ("United Kingdom", "GB"), ("UK", "GB"), ("Brazil", "BR"),
     ("Czechia", "CZ"), ("Japan, United States", "JP")],
)
def test_country_normalisation(raw, expected):
    assert normalize_country(raw) == expected


def test_global_is_a_scope_not_a_country():
    assert normalize_country("Global") is None
    assert is_global_scope("Global") is True


def test_vertical_normalisation():
    assert normalize_verticals("AI,Tech") == "ai,consumer_tech"
    assert normalize_verticals("Crypto") == "crypto"
    assert normalize_verticals("UX,UI,Design,AI") == "design,ai"


def test_unmappable_categories_return_none():
    assert normalize_verticals("Underwater Basket Weaving") is None
    assert normalize_verticals(None) is None


def test_follower_tier_unknown_stays_unknown():
    assert follower_tier(None) is None
    assert follower_tier(5_000) == "nano"
    assert follower_tier(2_000_000) == "mega"


# --- market region -----------------------------------------------------------


def test_english_means_europe_america():
    assert derive_market_region("en") == ("europe_america", "language")


def test_language_beats_country_because_it_proxies_the_audience():
    """An English-language creator reaches a Western audience whether they sit
    in Singapore or Texas. Where they live answers a different question."""
    assert derive_market_region("en", base_country="SG") == ("europe_america", "language")


def test_country_used_only_when_language_is_unknown():
    assert derive_market_region(None, base_country="BR") == ("latam", "country")


def test_spanish_defaults_to_latam_but_a_european_base_overrides():
    assert derive_market_region("es") == ("latam", "language")
    assert derive_market_region("es", base_country="ES") == ("europe_america", "language")


def test_global_scope_is_not_forced_into_a_region():
    assert derive_market_region(None, region_raw="Global") == ("global", "country")


def test_provenance_sheet_naming_the_language_is_a_signal():
    region, basis = derive_market_region(None, provenance="建联名单总表 - Fiona-英文AI KOL copy.csv")
    assert (region, basis) == ("europe_america", "provenance")


def test_latin_script_bio_is_weak_but_real_evidence():
    region, basis = derive_market_region(
        None, bio="AI for Creators - Video Editing Tips, helping you make viral content"
    )
    assert (region, basis) == ("europe_america", "bio_script")


def test_cjk_bio_is_not_read_as_western():
    assert derive_market_region(None, bio="专注 AI 工具测评，分享效率工作流") == (None, "unknown")


def test_no_signal_at_all_stays_unknown():
    assert derive_market_region(None) == (None, "unknown")
    assert derive_market_region(None, bio="hi") == (None, "unknown")


# --- audience types ----------------------------------------------------------


def test_audience_read_from_the_creators_own_words_beats_category():
    types, basis, evidence = derive_audience_types(
        verticals="consumer_tech", bio="Python tutorials for backend engineers"
    )
    assert basis == "content"
    assert "developers" in types
    assert "developers:" in evidence


def test_audience_falls_back_to_vertical_when_no_text():
    types, basis, evidence = derive_audience_types(verticals="developer_tools")
    assert (types, basis) == ("developers", "vertical")
    assert evidence == "developers:vertical=developer_tools"


def test_ai_vertical_alone_says_nothing_about_audience():
    """An AI account may address developers or consumers. Treating "AI" as an
    audience signal was the original recommender's mistake."""
    assert derive_audience_types(verticals="ai") == (None, "unknown", None)


def test_no_signal_yields_unknown_not_a_default_audience():
    assert derive_audience_types(None) == (None, "unknown", None)
    assert derive_audience_types(None, bio="") == (None, "unknown", None)


def test_audience_is_capped_so_it_stays_a_judgment():
    types, _, evidence = derive_audience_types(
        None,
        bio="founder investor developer researcher marketer trader designer creator",
    )
    assert len(types.split(",")) <= 3
    assert len(evidence.split(";")) == len(types.split(","))


def test_evidence_quotes_the_phrase_that_triggered_each_type():
    types, _, evidence = derive_audience_types(None, bio="Passive income from AI automation")
    assert types == "founders"
    assert "passive income" in evidence.lower()


# --- what a rebuild is allowed to destroy -------------------------------------


def test_a_rebuild_never_drops_the_observation_layer():
    """``drop_all()`` with no argument was taking the follow graph with it.

    ``observation_models`` registers on the same ``Base``, so "drop the Signal
    Map tables" silently meant x_accounts, follow_edges, follow_snapshots,
    roster_centrality and attention_signals too -- roughly 30,000 X API calls
    of collection (252,305 follow edges, 4,888 attention signals) that the
    migration cannot re-derive from ``kol.db``. The docstring's advice to
    "re-run the enricher afterwards" did not cover any of it.
    """
    from signal_map.scripts.migrate_from_bd import _MIGRATED_TABLES

    unrecoverable = {
        "x_accounts", "follow_edges", "follow_snapshots",
        "roster_centrality", "attention_signals",
    }
    assert unrecoverable.isdisjoint(_MIGRATED_TABLES)


def test_a_rebuild_never_drops_a_clients_own_records():
    """A re-run to improve quote parsing must not delete what a person did."""
    from signal_map.scripts.migrate_from_bd import _MIGRATED_TABLES

    client_records = {
        "clients", "briefs", "candidate_items", "feedback_events",
        "recommendation_runs", "recommendations", "internal_tasks",
    }
    assert client_records.isdisjoint(_MIGRATED_TABLES)


def test_every_migrated_table_actually_exists():
    """A typo here would silently protect a table instead of rebuilding it."""
    from signal_map.backend.models import Base
    from signal_map.scripts.migrate_from_bd import _MIGRATED_TABLES

    assert _MIGRATED_TABLES <= set(Base.metadata.tables)


def test_creator_ids_are_carried_over_not_reassigned(session, bd):
    """The follow graph and every AttentionSignal reference ``creators.id`` and
    survive a rebuild. Before this, the two agreed only because BD's ids happen
    to be gapless and insertion order matched -- one deleted BD row would have
    silently repointed 4,888 attention signals at the wrong creators."""
    bd_ids = {row[0] for row in bd.execute("SELECT id FROM creators")}
    sm_ids = {
        c.id
        for c in session.scalars(
            select(Creator).where(Creator.source_system == "mango_bd")
        ).all()
    }
    assert sm_ids == bd_ids
    # 本地新建的 id 必须落在保留段里，否则下一次重建会和 BD 的 id 撞上。
    local = session.scalars(
        select(Creator).where(Creator.source_system != "mango_bd")
    ).all()
    assert all(c.id >= 1_000_000 for c in local), [c.id for c in local]
