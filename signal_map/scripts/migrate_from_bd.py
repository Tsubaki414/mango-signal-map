"""One-way migration: Mango BD (``kol.db``) -> Signal Map (``signal_map.db``).

The BD system is the previous generation and is being retired. Its
hand-collected supply data is the asset worth carrying forward: 357 creators,
301 of them priced, 699 rate-card rows over 254 distinct quote messages, plus
contacts and sponsorship evidence.

Design constraints:

* **Read-only on the source.** This script opens ``kol.db`` read-only and
  never writes to it, so the original stays an intact archive and a bad run
  costs nothing but a re-run.
* **Idempotent.** Re-running drops and rebuilds the Signal Map tables by
  default. Quote parsing will keep improving, and re-parsing from preserved
  raw text must always be possible.
* **Quotes are re-parsed, not copied.** The BD ``deliverable`` column was
  ~32% unusable (labels carrying a second deliverable, or a bare number) and
  ``is_package`` was False on all 699 rows including ones literally labelled
  "Package". Copying that forward would carry the defects into the client
  product. ``raw_quote_text`` is intact for every row, so it is re-parsed
  through ``signal_map.backend.quotes``.
* **Provenance is known; parse quality is what gates display.** These prices
  were collected first-hand by Mango from the creators themselves, so the
  missing per-row quote date is not a reason to withhold them. A quote is
  cleared for a client surface when its *parse* is trustworthy. What the
  client sees is a price **band**, never the figure itself -- that number is
  what the creator asked Mango for, i.e. Mango's cost.

Usage::

    python -m signal_map.scripts.migrate_from_bd            # rebuild
    python -m signal_map.scripts.migrate_from_bd --dry-run  # report only
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.models import (  # noqa: E402
    Base,
    ContactMethod,
    Creator,
    ProcurementRoute,
    Quote,
    QuoteMessage,
    SocialAccount,
    SponsorshipEvidence,
    tier_for_class,
)
from signal_map.backend.normalize import (  # noqa: E402
    derive_audience_types,
    derive_market_region,
    is_global_scope,
    normalize_country,
    normalize_language,
    normalize_verticals,
)
from signal_map.backend.quotes import (  # noqa: E402
    FX_ASOF,
    parse_quote_message,
    price_band,
    to_usd,
)

BD_DB_PATH = REPO_ROOT / "kol_database" / "data" / "kol.db"
SOURCE_SYSTEM = "mango_bd"

#: Text indicating the price came via a manager/agency rather than the creator.
_VIA_INTERMEDIARY = re.compile(r"经纪|代理|代回复|\bagency\b|\bmanager\b", re.IGNORECASE)


#: Agencies named in BD's ``建联备注`` notes. A creator with no personal contact
#: is not unreachable -- Mango reaches them through whoever manages them, and
#: that route is the answer to "who do we buy through", not a missing field.
_KNOWN_AGENCIES = ("Neura Agency", "Castle and Castle", "C & C")

#: Booking platforms are a procurement route too: the creator publishes a
#: rate page and Mango books through it.
_BOOKING_PLATFORMS = (("passionfroot", "Passionfroot"), ("claryomedia", "Claryo Media"))


def _procurement_route(internal_notes: str | None) -> tuple[str, str] | None:
    """Return ``(route_type, counterparty)`` parsed from BD's outreach note.

    Deliberately conservative: the note column also holds URLs, follow-up
    text and reply transcripts, and inventing an agency from a free-text note
    would put a wrong counterparty on a real purchase.
    """
    if not internal_notes:
        return None
    for agency in _KNOWN_AGENCIES:
        if agency.lower() in internal_notes.lower():
            return "agency", agency
    lowered = internal_notes.lower()
    for needle, label in _BOOKING_PLATFORMS:
        if needle in lowered:
            return "booking_platform", label
    return None


def _quote_source(raw_text: str | None) -> tuple[str, bool]:
    """Return ``(source, is_inferred)`` for one quote message.

    Every quote in this dataset was collected first-hand by Mango, asking
    creators for their rates -- confirmed by the project owner, who did the
    collection. So the source is ``kol_direct`` as a **recorded** fact, not an
    inference, and the absence of a per-row quote date is not a reason to
    withhold the price.

    A message that names a manager or agency is upgraded to
    ``manager_agency`` -- that part *is* read from the text, so it is flagged
    inferred.
    """
    if raw_text and _VIA_INTERMEDIARY.search(raw_text):
        return "manager_agency", True
    return "kol_direct", False


def _as_date(value) -> dt.date | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text[: len(fmt) + 6], fmt).date()
        except ValueError:
            continue
    return None


def _as_datetime(value) -> dt.datetime | None:
    date = _as_date(value)
    return dt.datetime(date.year, date.month, date.day) if date else None


def open_source(path: Path) -> sqlite3.Connection:
    """Open the BD database read-only so a migration bug cannot damage it."""
    if not path.exists():
        raise SystemExit(f"BD database not found: {path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


#: Tables a rebuild may destroy: everything this script can re-derive from
#: ``kol.db``, and nothing else.
#:
#: Anything absent from this set survives a rebuild because it **cannot be
#: re-derived**. The observation layer alone cost roughly 30,000 X API calls to
#: collect (252,305 follow edges, 4,888 attention signals, 249 reviewed-or-
#: pending Root candidates), and ``drop_all()`` with no argument was quietly
#: taking all of it -- ``observation_models`` registers on the same ``Base``,
#: so "drop the Signal Map tables" meant "drop those too".
#:
#: Client-side operational data is protected for a different reason: a re-run
#: to improve quote parsing must not delete a client's own briefs, candidate
#: list or feedback history. Those are records of what a person did.
_MIGRATED_TABLES = frozenset({
    "creators", "social_accounts", "contact_methods",
    "quote_messages", "quotes", "sponsorship_evidence", "procurement_routes",
})


def _rebuildable_tables() -> list:
    """The table objects a rebuild is allowed to drop, in dependency order."""
    return [
        table
        for table in reversed(Base.metadata.sorted_tables)
        if table.name in _MIGRATED_TABLES
    ]


#: Rows in the rebuildable tables that did **not** come from ``kol.db``.
#: They were created by Mango's own BD work -- a creator promoted out of the
#: observation layer, a quote obtained by actually asking someone -- and
#: ``kol.db`` cannot re-derive a single one of them.
#:
#: This is the same class of mistake as ``Base.metadata.drop_all()`` taking the
#: follow graph with it: "rebuild the migrated tables" reads as safe right up
#: until those tables also hold data that was never migrated. So the rebuild
#: round-trips them out and back, keeping their primary keys, and
#: ``verify_referential_integrity`` still has to pass afterwards.
_LOCAL_SOURCE_SYSTEM = "signal_map_bd_intake"

#: Table -> the column naming where a row came from. Only tables that actually
#: carry local rows need to be listed; the others have no such column.
_PRESERVE_BY_SOURCE: tuple[tuple[str, str], ...] = (
    ("creators", "source_system"),
    ("quote_messages", "source_system"),
    ("sponsorship_evidence", "source_system"),
)


def _dump_local_rows() -> dict[str, list[dict]]:
    """Read out everything a rebuild would otherwise destroy.

    ``social_accounts``, ``contact_methods``, ``quotes`` and
    ``procurement_routes`` have no source column, so they are selected by
    **their creator** instead: anything hanging off a locally-created creator
    is itself local.
    """
    dumped: dict[str, list[dict]] = {}
    with sm_db.engine.begin() as connection:
        existing = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        local_creator_ids: list[int] = []
        if "creators" in existing:
            local_creator_ids = [
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT id FROM creators WHERE source_system = ?", (_LOCAL_SOURCE_SYSTEM,)
                ).fetchall()
            ]

        def rows(sql: str, params: tuple = ()) -> list[dict]:
            result = connection.exec_driver_sql(sql, params)
            columns = list(result.keys())
            return [dict(zip(columns, row)) for row in result.fetchall()]

        for table, column in _PRESERVE_BY_SOURCE:
            if table in existing:
                dumped[table] = rows(
                    f"SELECT * FROM {table} WHERE {column} = ?", (_LOCAL_SOURCE_SYSTEM,)
                )

        if local_creator_ids:
            placeholders = ",".join("?" * len(local_creator_ids))
            for table in ("social_accounts", "contact_methods", "procurement_routes"):
                if table in existing:
                    dumped.setdefault(table, []).extend(
                        rows(
                            f"SELECT * FROM {table} WHERE creator_id IN ({placeholders})",
                            tuple(local_creator_ids),
                        )
                    )
        # Quotes follow their message, which is where the source is recorded --
        # a locally-obtained quote can hang off a creator that came from BD.
        if "quotes" in existing and "quote_messages" in existing:
            dumped["quotes"] = rows(
                "SELECT q.* FROM quotes q JOIN quote_messages m ON m.id = q.message_id "
                "WHERE m.source_system = ?",
                (_LOCAL_SOURCE_SYSTEM,),
            )
    return {table: data for table, data in dumped.items() if data}


def _restore_local_rows(dumped: dict[str, list[dict]]) -> int:
    """Put them back, primary keys and all.

    Insertion order follows ``_MIGRATED_TABLES``' dependency order so foreign
    keys resolve as each table lands.
    """
    order = [
        "creators", "social_accounts", "contact_methods",
        "quote_messages", "quotes", "sponsorship_evidence", "procurement_routes",
    ]
    restored = 0
    with sm_db.engine.begin() as connection:
        for table in order:
            for row in dumped.get(table, []):
                columns = ",".join(row)
                marks = ",".join("?" * len(row))
                connection.exec_driver_sql(
                    f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({marks})",
                    tuple(row.values()),
                )
                restored += 1
    return restored


def _drop_migrated_tables() -> None:
    """Drop only the re-derivable tables, with foreign keys off for the drop.

    The surviving tables point *into* the dropped ones -- ``candidate_items``
    references ``quotes``, ``attention_signals`` references ``creators`` -- so
    SQLite refuses the drop while enforcement is on.

    Turning it off is safe here **only because the migration carries BD's
    primary keys across**: ``creators.id`` and ``quotes.id`` come back with the
    same values, so every surviving reference resolves again the moment the
    rebuild finishes. It is not safe as a general habit, which is why
    ``verify_referential_integrity`` runs afterwards and the run fails loudly
    if anything is left dangling.
    """
    with sm_db.engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        for table in _rebuildable_tables():
            connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table.name}")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def verify_referential_integrity() -> list[str]:
    """Report references left dangling by the rebuild. Empty means clean.

    Checked rather than assumed: the whole reason ids are now carried over is
    that "the ids happened to line up" is exactly how the earlier follow-graph
    join bug hid a clean, entirely false zero.
    """
    problems: list[str] = []
    with sm_db.engine.begin() as connection:
        for row in connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
            problems.append(f"{row[0]} row {row[1]} -> missing {row[2]}")
        # PRAGMA foreign_key_check does not see AttentionSignal.source_creator_id
        # when the value is simply absent from a rebuilt creators table under
        # some SQLite builds, so assert the join that actually matters.
        orphaned = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM attention_signals s "
            "WHERE s.source_creator_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM creators c WHERE c.id = s.source_creator_id)"
        ).scalar()
        if orphaned:
            problems.append(f"{orphaned} attention_signals point at a missing creator")
        orphaned_accounts = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM x_accounts a WHERE a.creator_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM creators c WHERE c.id = a.creator_id)"
        ).scalar()
        if orphaned_accounts:
            problems.append(f"{orphaned_accounts} x_accounts point at a missing creator")
    return problems


def migrate(dry_run: bool = False, rebuild: bool = True) -> dict:
    src = open_source(BD_DB_PATH)
    stats: Counter = Counter()
    parse_flags: Counter = Counter()

    preserved: dict[str, list[dict]] = {}
    if not dry_run:
        sm_db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if rebuild:
            # Read out Mango's own rows *before* the drop, not after -- there
            # is no "after" for a dropped table.
            preserved = _dump_local_rows()
            _drop_migrated_tables()
        Base.metadata.create_all(sm_db.engine)

    session = sm_db.get_session() if not dry_run else None
    creator_id_map: dict[int, Creator] = {}

    # The market-region ladder falls back to the account bio, so bios have to
    # be on hand while creators are being built -- social accounts are only
    # written further down.
    bios_by_creator: dict[int, str] = {}
    for row in src.execute(
        "SELECT creator_id, bio FROM social_accounts WHERE bio IS NOT NULL AND bio != ''"
    ):
        bios_by_creator.setdefault(row["creator_id"], row["bio"])

    summaries_by_creator: dict[int, str] = {}
    for row in src.execute(
        "SELECT creator_id, content_summary FROM social_accounts "
        "WHERE content_summary IS NOT NULL AND content_summary != ''"
    ):
        summaries_by_creator.setdefault(row["creator_id"], row["content_summary"])

    try:
        # --- creators -------------------------------------------------------
        for row in src.execute("SELECT * FROM creators"):
            languages = normalize_language(row["language"])
            country = normalize_country(row["region"])
            verticals = normalize_verticals(row["categories"])
            market_region, market_basis = derive_market_region(
                languages=languages,
                base_country=country,
                region_raw=row["region"],
                provenance=row["source_files"],
                bio=bios_by_creator.get(row["id"]),
            )
            audience_types, audience_basis, audience_evidence = derive_audience_types(
                verticals=verticals,
                bio=bios_by_creator.get(row["id"]),
                content_summary=summaries_by_creator.get(row["id"]),
            )

            stats["creators"] += 1
            if languages:
                stats["creators_language_resolved"] += 1
            if country:
                stats["creators_country_resolved"] += 1
            elif is_global_scope(row["region"]):
                stats["creators_region_global_scope"] += 1
            if verticals:
                stats["creators_verticals_resolved"] += 1
            if market_region:
                stats[f"market_via_{market_basis}"] += 1
            else:
                stats["market_unresolved"] += 1
            if audience_types:
                stats[f"audience_via_{audience_basis}"] += 1
            else:
                stats["audience_unresolved"] += 1
            stats[f"tier_{tier_for_class(row['creator_class'])}"] += 1

            creator = Creator(
                # Carry BD's primary key across rather than letting autoincrement
                # assign one. Today the two agree for all 357 creators, but only
                # because BD's ids happen to be gapless and insertion order
                # matches -- a coincidence, not a guarantee. The follow graph and
                # every AttentionSignal reference ``creators.id`` and survive a
                # rebuild, so one deleted BD row would silently repoint 4,888
                # attention signals at the wrong creators. Same id-space failure
                # that already produced a clean, entirely false zero once.
                id=row["id"],
                display_name=row["display_name"],
                primary_handle=row["primary_handle"],
                languages=languages,
                base_country=country,
                market_region=market_region,
                market_region_basis=market_basis,
                # audience_markets stays NULL even though market_region is
                # populated: one is a coarse inference with a stated basis, the
                # other is measured audience geography. Collapsing them would
                # let an inference be read as a measurement.
                audience_markets=None,
                audience_types=audience_types,
                audience_types_basis=audience_basis,
                audience_types_evidence=audience_evidence,
                verticals=verticals,
                language_raw=row["language"],
                region_raw=row["region"],
                categories_raw=row["categories"],
                creator_class=row["creator_class"] or "Unknown",
                creator_class_confidence=row["classification_confidence"],
                creator_tier=tier_for_class(row["creator_class"]),
                promotion_level=row["promotion_level"],
                internal_notes=row["internal_notes"],
                source_system=SOURCE_SYSTEM,
                source_ref=f"creators.id={row['id']}",
                first_seen_at=_as_datetime(row["created_at"]),
            )
            if session:
                session.add(creator)
            creator_id_map[row["id"]] = creator

        if session:
            session.flush()  # assign creator PKs for the FKs below

        # --- procurement routes (path 2) -----------------------------------
        # Not "out of scope" after all: every priced creator was contacted
        # directly, but some are *managed*, and for those the agency is who
        # Mango actually buys through. Stored as a route rather than a
        # contact -- the agency is a counterparty, not the creator's inbox.
        for row in src.execute("SELECT id, internal_notes FROM creators"):
            route = _procurement_route(row["internal_notes"])
            creator = creator_id_map.get(row["id"])
            if route is None or creator is None:
                continue
            route_type, counterparty = route
            stats[f"route_{route_type}"] += 1
            if session:
                session.add(
                    ProcurementRoute(
                        creator_id=creator.id,
                        route_type=route_type,
                        counterparty_name=counterparty,
                        provides_quotes=True,
                        accepts_commercial_work=True,
                        # Recorded from Mango's own outreach note, but nobody
                        # has re-confirmed the agency still represents them.
                        verified_at=None,
                        notes="from Mango BD 建联备注",
                    )
                )
        if session:
            session.flush()

        # --- social accounts ------------------------------------------------
        for row in src.execute("SELECT * FROM social_accounts"):
            creator = creator_id_map.get(row["creator_id"])
            if creator is None:
                stats["social_accounts_orphaned"] += 1
                continue
            stats["social_accounts"] += 1
            if session:
                session.add(
                    SocialAccount(
                        creator_id=creator.id,
                        platform=row["platform"],
                        handle=row["handle"],
                        profile_url=row["profile_url"],
                        platform_uid=row["x_rest_id"] or row["youtube_channel_id"],
                        is_primary=(row["handle"] or "") == (creator.primary_handle or ""),
                        avatar_url=row["avatar_url"],
                        bio=row["bio"],
                        verified=row["verified"],
                        followers=row["followers"],
                        avg_views=row["avg_views"],
                        median_views=row["median_views"],
                        engagement_rate=row["engagement_rate"],
                        posting_frequency=row["posting_frequency"],
                        promotional_content_ratio=row["promotional_content_ratio"],
                        content_summary=row["content_summary"],
                        metrics_observed_at=_as_datetime(row["last_enriched_at"]),
                    )
                )

        # --- contacts (internal only) ---------------------------------------
        for row in src.execute("SELECT * FROM contact_methods"):
            creator = creator_id_map.get(row["creator_id"])
            if creator is None:
                stats["contacts_orphaned"] += 1
                continue
            stats["contacts"] += 1
            if session:
                session.add(
                    ContactMethod(
                        creator_id=creator.id,
                        method_type=row["method_type"],
                        value=row["value"],
                        # BD never recorded contact verification, so this stays
                        # NULL rather than defaulting to "verified".
                        verified_at=None,
                    )
                )

        # --- quotes: group rate_cards by distinct raw text, then re-parse ----
        by_message: dict[tuple[int, str], list[sqlite3.Row]] = defaultdict(list)
        for row in src.execute(
            "SELECT * FROM rate_cards WHERE raw_quote_text IS NOT NULL AND raw_quote_text != ''"
        ):
            by_message[(row["creator_id"], row["raw_quote_text"])].append(row)
        stats["bd_rate_card_rows"] = sum(len(v) for v in by_message.values())
        stats["quote_messages"] = len(by_message)


        for (bd_creator_id, raw_text), rows in by_message.items():
            creator = creator_id_map.get(bd_creator_id)
            if creator is None:
                stats["quotes_orphaned"] += 1
                continue

            source, inferred = _quote_source(raw_text)
            # Majority currency across the BD rows for this message: the parser
            # only needs it when the text itself carries no currency marker.
            currencies = Counter(r["quote_currency"] for r in rows if r["quote_currency"])
            default_currency = currencies.most_common(1)[0][0] if currencies else "USD"

            message = QuoteMessage(
                creator_id=creator.id,
                raw_text=raw_text,
                source=source,
                source_is_inferred=inferred,
                # quoted_at is genuinely unknown -- BD stored no quote date and
                # back-filling it from the import date would invent a fact.
                quoted_at=None,
                observed_at=_as_date(
                    src.execute(
                        "SELECT created_at FROM creators WHERE id = ?", (bd_creator_id,)
                    ).fetchone()["created_at"]
                ),
                valid_until=None,
                source_system=SOURCE_SYSTEM,
                source_ref=f"rate_cards.id in ({','.join(str(r['id']) for r in rows)})",
            )
            if session:
                session.add(message)
                session.flush()

            parsed = parse_quote_message(raw_text, default_currency=default_currency)
            if not parsed:
                stats["messages_yielding_no_rows"] += 1
                continue

            # Did this message price anything at all? An unpriced fragment
            # sitting beside priced ones is the message's *terms* -- "含1轮修改,
            # 交付7-10工作日", "付费投放/whitelist/独家等另议" -- not a deliverable
            # anyone failed to parse. Five such rows were in the review queue,
            # where no amount of re-reading could resolve them.
            message_priced_something = any(i.amount is not None for i in parsed)

            for item in parsed:
                stats["quotes"] += 1
                for flag in item.flags:
                    parse_flags[flag] += 1

                amount_usd, fx_rate = to_usd(item.amount, item.currency)
                if amount_usd is not None:
                    stats["quotes_priced"] += 1
                if item.status_hint == "no_quote":
                    stats["quotes_declined"] += 1

                # These prices were collected first-hand by Mango from the
                # creators themselves, so provenance is a recorded fact and
                # the missing per-row quote date is not a reason to hold them
                # back. Parse quality is a separate question: a medium-
                # confidence parse still goes to review even though its source
                # is known.
                needs_review = item.parse_confidence not in {"high", "medium"}
                if item.status_hint == "no_quote":
                    # "They declined to price it" is a finished answer, not a
                    # pending one. Leaving it flagged for review kept 20 rows
                    # in Mango's queue that no amount of reviewing can resolve,
                    # burying the ones a person can actually fix.
                    status, needs_review = "no_quote", False
                elif amount_usd is None and message_priced_something:
                    # Kept, not deleted: dropping it would lose the terms text,
                    # and a fragment the parser could not read is occasionally a
                    # real deliverable. It just is not review work.
                    status, needs_review = "no_quote", False
                    stats["quotes_trailing_terms"] += 1
                elif needs_review or amount_usd is None:
                    status = "needs_review"
                else:
                    status = "confirmed_valid"
                review_reason = _review_reason(item, amount_usd) if needs_review else None

                # Client-visible means "may appear on a client surface", not
                # "show this number". The exact figure stays internal; the
                # client sees a band (see below).
                client_visible = status == "confirmed_valid"
                if client_visible:
                    stats["client_visible"] += 1

                if session:
                    session.add(
                        Quote(
                            creator_id=creator.id,
                            message_id=message.id,
                            deliverable_raw=item.deliverable_raw,
                            content_format=item.content_format,
                            platform=item.platform,
                            quantity=item.quantity,
                            is_package=item.is_package,
                            package_contents=item.package_contents,
                            amount=item.amount,
                            amount_min=item.amount_min,
                            amount_max=item.amount_max,
                            currency=item.currency,
                            amount_usd=amount_usd,
                            fx_rate_used=fx_rate,
                            fx_asof=FX_ASOF if fx_rate else None,
                            status=status,
                            needs_review=needs_review,
                            review_reason=review_reason,
                            parse_confidence=item.parse_confidence,
                            parse_notes=item.parse_notes,
                            raw_segment=item.raw_segment,
                            # The quoted figure is what the creator asked Mango
                            # for, i.e. Mango's cost.
                            internal_cost_usd=amount_usd,
                            # Passed through as the client price by explicit
                            # decision: no markup for now, the client is quoted
                            # the creator's own rate. The two columns stay
                            # separate so a markup can be applied later without
                            # re-deriving cost -- and so the *rule* that they
                            # are different things survives the current policy.
                            client_price_usd=amount_usd if client_visible else None,
                            client_price_band=price_band(amount_usd),
                            client_visible=client_visible,
                            client_visibility_note=(
                                "报价由 Mango 直接向创作者收集；客户端显示价格区间，"
                                "具体成交价由 Mango 报出"
                                if client_visible
                                else "解析置信度不足，需人工复核后方可对客展示"
                            ),
                        )
                    )

        # --- sponsorship evidence -------------------------------------------
        for row in src.execute("SELECT * FROM sponsorship_evidence"):
            creator = creator_id_map.get(row["creator_id"])
            if creator is None:
                # 12 BD rows carry ``creator_id IS NULL``: they record that a
                # *company* ran a sponsorship, with no creator identified.
                # That is opportunity-side evidence, not creator 历史合作, and
                # it cannot answer "has this creator been paid before?" -- so
                # it is deliberately out of scope for the MVP rather than
                # forced onto an arbitrary creator. Counted, not silently lost.
                stats["sponsorship_skipped_no_creator"] += 1
                continue
            stats["sponsorship_evidence"] += 1
            if session:
                session.add(
                    SponsorshipEvidence(
                        creator_id=creator.id,
                        sponsor_name=_col(row, "sponsor_name") or _col(row, "company_name"),
                        evidence_type=_col(row, "evidence_type"),
                        review_status=_col(row, "review_status") or "unreviewed",
                        platform=_col(row, "platform"),
                        post_url=_col(row, "post_url") or _col(row, "url"),
                        published_at=_as_date(_col(row, "published_at")),
                        notes=_col(row, "notes"),
                        # Only confirmed paid rows may ever be shown; nothing
                        # is cleared automatically by an import.
                        client_visible=False,
                        source_system=SOURCE_SYSTEM,
                        source_ref=f"sponsorship_evidence.id={row['id']}",
                    )
                )

        if session:
            session.commit()
    finally:
        if session:
            session.close()
        src.close()

    if preserved:
        # Restored after the BD rows land, so foreign keys into ``creators``
        # and ``quote_messages`` resolve. Counted separately in the report so a
        # run that silently preserved nothing is visible.
        stats["local_rows_preserved"] = _restore_local_rows(preserved)
        for table, data in sorted(preserved.items()):
            stats[f"preserved_{table}"] = len(data)

    if not dry_run:
        session = sm_db.get_session()
        try:
            stats["creators_with_priced_quote"] = _count_priced_creators(session)
        finally:
            session.close()

    return {"stats": dict(stats), "parse_flags": dict(parse_flags)}


def _col(row: sqlite3.Row, name: str):
    """Read an optional column -- the BD schema drifted across migrations."""
    return row[name] if name in row.keys() else None


def _review_reason(item, amount_usd: float | None) -> str:
    reasons = []
    if amount_usd is None:
        reasons.append("no usable price parsed")
    if item.parse_confidence != "high":
        reasons.append(f"parse confidence {item.parse_confidence}")
    if "assumed_currency" in item.flags:
        reasons.append("currency assumed, not stated")
    if item.is_package:
        reasons.append("package price cannot be summed with single-item prices")
    reasons.append("quote date and source not recorded by Mango BD")
    return "; ".join(reasons)


def _count_priced_creators(session) -> int:
    from sqlalchemy import func, select

    return session.scalar(
        select(func.count(func.distinct(Quote.creator_id))).where(Quote.amount_usd.is_not(None))
    ) or 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--no-rebuild", action="store_true", help="keep existing rows")
    args = parser.parse_args()

    result = migrate(dry_run=args.dry_run, rebuild=not args.no_rebuild)

    print(f"source : {BD_DB_PATH}  (read-only)")
    print(f"target : {sm_db.DB_PATH}{'  [DRY RUN, nothing written]' if args.dry_run else ''}")
    print("\n--- migrated ---")
    for key, value in sorted(result["stats"].items()):
        print(f"  {key:34} {value}")
    print("\n--- quote parse flags ---")
    for key, value in sorted(result["parse_flags"].items(), key=lambda kv: -kv[1]):
        print(f"  {key:34} {value}")

    if not args.dry_run:
        problems = verify_referential_integrity()
        print("\n--- referential integrity ---")
        if problems:
            for problem in problems:
                print(f"  ✗ {problem}")
            raise SystemExit(
                "rebuild left dangling references — the follow graph and the "
                "rebuilt creators no longer agree; restore and investigate"
            )
        print("  ✓ 关注图与重建后的创作者表一致")


if __name__ == "__main__":
    main()
