"""Resolve 市场待确认 / 人群待确认 creators by reading what they actually publish.

The migration derives ``market_region`` and ``audience_types`` from data that
was already on file. A residue of priced creators had nothing on file at all --
no language, no country, no bio, no content summary -- so both fields came back
unknown and the creators risked dropping out of every filtered result.

This script goes and looks, per platform:

* **X** -- via Rapid X (``kol_database.backend.rapidx_client``), which per
  AGENTS.md is the only sanctioned source for X data. Reads the profile bio.
* **YouTube** -- there is no ``YOUTUBE_API_KEY`` configured, and these creators
  have no X presence at all, so their content only exists on their channel
  page. Reads the public channel description and keyword tags, which are the
  creator's own words about their channel.

What it does *not* do: invent anything. A creator whose page cannot be read
stays 待确认 and is reported, not guessed at.

Cost control (AGENTS.md): responses are cached to disk, requests are rate
limited, and ``--limit`` caps a run. Re-running is cheap because the cache is
consulted first.

Usage::

    python -m signal_map.scripts.enrich_unknown_profiles --dry-run
    python -m signal_map.scripts.enrich_unknown_profiles --limit 10
    python -m signal_map.scripts.enrich_unknown_profiles
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as html_mod
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import certifi

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import or_, select  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.models import Creator, Quote, SocialAccount  # noqa: E402
from signal_map.backend.normalize import (  # noqa: E402
    derive_audience_types,
    derive_market_region,
)

CACHE_DIR = REPO_ROOT / "signal_map" / "data" / "cache" / "profiles"
CACHE_TTL_SECONDS = 30 * 24 * 3600  # profile copy changes slowly

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
_YT_DESCRIPTION = re.compile(r'<meta name="description" content="([^"]{0,600})"')
_YT_KEYWORDS = re.compile(r'<meta name="keywords" content="([^"]{0,600})"')

_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class FetchError(RuntimeError):
    pass


def _cache_path(kind: str, key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.@-]", "_", key)[:80]
    return CACHE_DIR / f"{kind}-{safe}.json"


def _read_cache(path: Path) -> dict | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# --- YouTube -----------------------------------------------------------------


def _channel_url(account: SocialAccount) -> str | None:
    """Prefer the stored profile URL; fall back to an @handle."""
    url = (account.profile_url or "").strip()
    if "youtube.com" in url:
        return url.split("?")[0].rstrip("/")
    handle = (account.handle or "").strip()
    if handle:
        return f"https://www.youtube.com/@{handle.lstrip('@')}"
    return None


def fetch_youtube_channel(account: SocialAccount, *, force: bool = False) -> dict:
    """Return ``{"description", "keywords", "url", "fetched_at"}``.

    Uses the public channel page because no YouTube API key is configured and
    these creators exist nowhere else -- they have no X accounts at all.
    """
    url = _channel_url(account)
    if not url:
        raise FetchError("no YouTube URL or handle on file")

    cache = _cache_path("youtube", url.rsplit("/", 1)[-1])
    if not force:
        cached = _read_cache(cache)
        if cached:
            return cached

    request = urllib.request.Request(
        url, headers={"User-Agent": _BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=_SSL_CONTEXT) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"{type(exc).__name__}: {str(exc)[:120]}") from exc

    description = _YT_DESCRIPTION.search(body)
    keywords = _YT_KEYWORDS.search(body)
    payload = {
        "url": url,
        "description": html_mod.unescape(description.group(1)) if description else None,
        "keywords": html_mod.unescape(keywords.group(1)) if keywords else None,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    _write_cache(cache, payload)
    return payload


# --- X -----------------------------------------------------------------------


def fetch_x_profile(handle: str, client, *, force: bool = False, with_posts: bool = True) -> dict:
    """Return ``{"bio", "name", "posts", "fetched_at"}`` from Rapid X.

    Reads recent posts, not just the bio. A bio is a slogan -- "Turning AI into
    money machines", "Anime edits & visual storytelling" -- and often says
    nothing about who the account addresses. What someone actually posts does.
    Rapid X caches responses to disk, so a re-run costs no quota.
    """
    payload = client.get_user(handle, force=force)
    user = payload.get("result", {}).get("data", {}).get("user", {}).get("result", {})
    legacy = user.get("legacy", {}) or {}
    core = user.get("core", {}) or {}

    posts: list[str] = []
    if with_posts and user.get("rest_id"):
        try:
            from kol_database.backend.enrichment import extract_tweets

            tweets_payload = client.get_user_tweets(str(user["rest_id"]), count=30, force=force)
            posts = [record.text for record in extract_tweets(tweets_payload) if record.text]
        except Exception:  # noqa: BLE001 -- a profile with no readable timeline is still usable
            posts = []

    return {
        "bio": legacy.get("description"),
        "name": core.get("name") or legacy.get("name"),
        "posts": posts,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


# --- main --------------------------------------------------------------------


def targets(session):
    """Priced creators still missing a market bucket or an audience type.

    Restricted to priced creators on purpose: they are the MVP candidate pool,
    and spending API calls on inventory that cannot be sold yet is waste.
    """
    priced = select(Quote.creator_id).where(Quote.amount_usd.is_not(None)).distinct().subquery()
    return list(
        session.scalars(
            select(Creator)
            .join(priced, Creator.id == priced.c.creator_id)
            .where(or_(Creator.market_region.is_(None), Creator.audience_types.is_(None)))
            .order_by(Creator.id)
        )
    )


def _pick_account(creator: Creator) -> SocialAccount | None:
    """X first (a sanctioned API), then YouTube (page read), then nothing."""
    by_platform = {account.platform: account for account in creator.accounts}
    return by_platform.get("X") or by_platform.get("YouTube")


def enrich(limit: int | None = None, dry_run: bool = False, force: bool = False) -> dict:
    stats: Counter = Counter()
    unresolved: list[tuple[str, str]] = []
    rapidx = None

    with sm_db.get_session() as session:
        creators = targets(session)
        stats["targets"] = len(creators)
        if limit:
            creators = creators[:limit]

        for creator in creators:
            account = _pick_account(creator)
            if account is None:
                stats["skipped_no_usable_account"] += 1
                unresolved.append((creator.display_name, "no X or YouTube account on file"))
                continue

            if dry_run:
                stats[f"would_fetch_{account.platform.lower()}"] += 1
                continue

            bio_text = None
            content_text = None
            try:
                if account.platform == "X":
                    if rapidx is None:
                        from kol_database.backend.rapidx_client import RapidXClient

                        rapidx = RapidXClient()
                        if not rapidx.is_configured:
                            raise FetchError("RAPID_X_API_KEY not configured")
                    profile = fetch_x_profile(account.handle or "", rapidx, force=force)
                    bio_text = profile.get("bio")
                    posts = profile.get("posts") or []
                    if posts:
                        # Recent post text, capped: enough to characterise the
                        # account without storing a timeline archive.
                        content_text = " • ".join(posts)[:4000]
                        stats["fetched_x_with_posts"] += 1
                    stats["fetched_x"] += 1
                else:
                    channel = fetch_youtube_channel(account, force=force)
                    bio_text = channel.get("description")
                    # Channel keyword tags are the creator's own topic labels
                    # and are far richer for audience inference than the
                    # one-line description on its own.
                    content_text = channel.get("keywords")
                    stats["fetched_youtube"] += 1
                    time.sleep(0.8)  # be a polite client on a public page
            except Exception as exc:  # noqa: BLE001 -- one bad profile must not abort the run
                stats["fetch_failed"] += 1
                unresolved.append((creator.display_name, str(exc)[:110]))
                continue

            if not bio_text and not content_text:
                stats["fetched_but_empty"] += 1
                unresolved.append((creator.display_name, "profile carried no description"))
                continue

            # Persist the profile copy, which is a stable description of the
            # account. Raw post text is deliberately *not* written to
            # ``content_summary`` -- that column means "one-line summary of
            # what this account posts", and a 4,000-character timeline dump
            # would quietly change what every reader of it thinks it holds.
            # Posts stay transient evidence; Rapid X's disk cache keeps the
            # derivation reproducible without another API call.
            if bio_text and not account.bio:
                account.bio = bio_text
            if account.platform != "X" and content_text and not account.content_summary:
                account.content_summary = content_text
            account.metrics_observed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

            before_market = creator.market_region
            before_audience = creator.audience_types

            if creator.market_region is None:
                region, basis = derive_market_region(
                    languages=creator.languages,
                    base_country=creator.base_country,
                    region_raw=creator.region_raw,
                    provenance=creator.source_ref,
                    bio=" ".join(part for part in (bio_text, content_text) if part),
                )
                creator.market_region = region
                creator.market_region_basis = basis

            if creator.audience_types is None:
                audiences, basis, evidence = derive_audience_types(
                    verticals=creator.verticals,
                    bio=bio_text,
                    content_summary=content_text,
                )
                creator.audience_types = audiences
                creator.audience_types_basis = basis
                creator.audience_types_evidence = evidence

            if before_market is None and creator.market_region:
                stats["market_resolved"] += 1
            if before_audience is None and creator.audience_types:
                stats["audience_resolved"] += 1
            if creator.market_region is None and creator.audience_types is None:
                unresolved.append((creator.display_name, "content read, but no usable signal"))

        if not dry_run:
            session.commit()

    return {"stats": dict(stats), "unresolved": unresolved}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="cap how many creators to fetch this run")
    parser.add_argument("--dry-run", action="store_true", help="report targets without fetching")
    parser.add_argument("--force", action="store_true", help="bypass the response cache")
    args = parser.parse_args()

    result = enrich(limit=args.limit, dry_run=args.dry_run, force=args.force)

    print("--- enrichment ---")
    for key, value in sorted(result["stats"].items()):
        print(f"  {key:30} {value}")
    if result["unresolved"]:
        print(f"\n--- still 待确认 ({len(result['unresolved'])}) ---")
        for name, reason in result["unresolved"][:40]:
            print(f"  {name[:38]:40} {reason}")


if __name__ == "__main__":
    main()
