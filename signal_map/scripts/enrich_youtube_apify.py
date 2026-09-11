"""Fill in YouTube channel content via Apify, then re-derive market and audience.

A block of priced creators are YouTube-only with no description on file, so
neither derivation had anything to read for them. Apify returns the full About
text, recent video titles, subscriber count and ``channelLocation`` -- more
than the ~160-character ``<meta name="description">`` the public page exposes,
and the location is a stated country rather than a guess.

Only touches accounts that are actually missing content, and only for priced
creators: spending on inventory that cannot be sold yet is waste.

Run this *after* ``migrate_from_bd``, which drops and rebuilds the tables.

Usage::

    python -m signal_map.scripts.enrich_youtube_apify --dry-run
    python -m signal_map.scripts.enrich_youtube_apify --limit 20
    python -m signal_map.scripts.enrich_youtube_apify
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import or_, select  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.apify_client import (  # noqa: E402
    ApifyError,
    fetch_youtube_channels,
    normalize_channel_url,
)
from signal_map.backend.models import Creator, Quote, SocialAccount  # noqa: E402
from signal_map.backend.normalize import (  # noqa: E402
    derive_audience_types,
    derive_market_region,
    normalize_country,
)


def _load_dotenv() -> None:
    """Load the repo-root .env the same way the BD backend does.

    Values are only ever put into this process's environment -- never logged
    and never written to a cache file.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os_key = key.strip()
        if os_key and os_key not in __import__("os").environ:
            __import__("os").environ[os_key] = value.strip()


def targets(session) -> list[tuple[Creator, SocialAccount]]:
    """Priced creators whose YouTube account has no description on file."""
    priced = select(Quote.creator_id).where(Quote.amount_usd.is_not(None)).distinct().subquery()
    rows = session.execute(
        select(Creator, SocialAccount)
        .join(SocialAccount, SocialAccount.creator_id == Creator.id)
        .join(priced, Creator.id == priced.c.creator_id)
        .where(
            SocialAccount.platform == "YouTube",
            or_(SocialAccount.bio.is_(None), SocialAccount.bio == ""),
        )
        .order_by(Creator.id)
    ).all()
    return [(creator, account) for creator, account in rows]


def run(limit: int | None = None, dry_run: bool = False, force: bool = False) -> dict:
    _load_dotenv()
    stats: Counter = Counter()
    unresolved: list[tuple[str, str]] = []

    with sm_db.get_session() as session:
        pairs = targets(session)
        stats["targets"] = len(pairs)
        if limit:
            pairs = pairs[:limit]

        resolvable: list[tuple[Creator, SocialAccount, str]] = []
        for creator, account in pairs:
            url = normalize_channel_url(account.profile_url, account.handle)
            if not url:
                stats["no_channel_url"] += 1
                unresolved.append((creator.display_name, "no resolvable YouTube URL"))
                continue
            resolvable.append((creator, account, url))

        if dry_run:
            stats["would_fetch"] = len(resolvable)
            return {"stats": dict(stats), "unresolved": unresolved}

        try:
            channels = fetch_youtube_channels([url for _, _, url in resolvable], force=force)
        except ApifyError as exc:
            return {"stats": dict(stats), "unresolved": [("(all)", str(exc)[:200])]}

        for creator, account, url in resolvable:
            channel = channels.get(url)
            if channel is None:
                stats["not_returned_by_actor"] += 1
                unresolved.append((creator.display_name, "actor returned no row for this channel"))
                continue
            stats["cached" if channel.from_cache else "fetched"] += 1

            if channel.description and not account.bio:
                account.bio = channel.description
                stats["description_filled"] += 1
            if channel.content_text and not account.content_summary:
                account.content_summary = channel.content_text
                stats["video_titles_filled"] += 1
            if channel.subscriber_count and not account.followers:
                account.followers = channel.subscriber_count
                stats["followers_filled"] += 1
            account.metrics_observed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

            # channelLocation is a stated country, which outranks the
            # bio-script guess the fallback ladder would otherwise land on.
            if channel.location and not creator.base_country:
                country = normalize_country(channel.location)
                if country:
                    creator.base_country = country
                    creator.region_raw = creator.region_raw or channel.location
                    stats["country_filled"] += 1

            # Re-derive only what is still missing. An existing value came from
            # stronger evidence (a declared language, a human edit) and must
            # not be overwritten by a channel blurb.
            if creator.market_region is None:
                region, basis = derive_market_region(
                    languages=creator.languages,
                    base_country=creator.base_country,
                    region_raw=creator.region_raw,
                    provenance=creator.source_ref,
                    bio=" ".join(
                        part for part in (channel.description, channel.content_text) if part
                    ),
                )
                if region:
                    creator.market_region = region
                    creator.market_region_basis = basis
                    stats["market_resolved"] += 1

            if creator.audience_types is None:
                # Recent video titles are what the channel actually publishes;
                # the description is marketing copy. Both are passed, titles as
                # the content signal.
                audiences, basis, evidence = derive_audience_types(
                    verticals=creator.verticals,
                    bio=channel.description,
                    content_summary=channel.content_text,
                )
                if audiences:
                    creator.audience_types = audiences
                    creator.audience_types_basis = basis
                    creator.audience_types_evidence = evidence
                    stats["audience_resolved"] += 1

        session.commit()

    return {"stats": dict(stats), "unresolved": unresolved}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="cap how many channels to fetch")
    parser.add_argument("--dry-run", action="store_true", help="report targets without fetching")
    parser.add_argument("--force", action="store_true", help="bypass the response cache")
    args = parser.parse_args()

    result = run(limit=args.limit, dry_run=args.dry_run, force=args.force)
    print("--- apify youtube enrichment ---")
    for key, value in sorted(result["stats"].items()):
        print(f"  {key:28} {value}")
    if result["unresolved"]:
        print(f"\n--- unresolved ({len(result['unresolved'])}) ---")
        for name, reason in result["unresolved"][:30]:
            print(f"  {name[:34]:36} {reason}")


if __name__ == "__main__":
    main()
