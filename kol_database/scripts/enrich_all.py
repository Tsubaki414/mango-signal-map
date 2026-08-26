"""Bulk-enrich every creator with an enrichable social account (X via Rapid
X; YouTube/Instagram/TikTok via ScrapeCreators, or YouTube via the official
Data API v3 adapter if that key is set instead) and classify them with GPT.
Cache-first, so re-running only refreshes what's missing/stale.

ScrapeCreators is credit-metered (not a flat-rate plan like Rapid X): each
Instagram enrich costs 1 credit, YouTube and TikTok cost 2 (profile call +
videos call). Use --limit and --platform to spend deliberately rather than
accidentally draining the balance in one run -- this script does not check
remaining balance before starting.

Usage:
    PYTHONPATH=kol_database python3 kol_database/scripts/enrich_all.py \
        [--platform x|youtube|instagram|tiktok|all] [--limit N] [--force]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import joinedload  # noqa: E402

from backend.app import _enrich_creator_accounts  # noqa: E402
from backend.db import get_session  # noqa: E402
from backend.models import Creator  # noqa: E402
from backend.rapidx_client import RapidXClient  # noqa: E402
from backend.scrapecreators_client import ScrapeCreatorsClient  # noqa: E402
from backend.youtube_client import YouTubeClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=["x", "youtube", "instagram", "tiktok", "all"], default="all")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-classify", action="store_true")
    args = parser.parse_args()

    rapid_client = RapidXClient()
    youtube_client = YouTubeClient()
    sc_client = ScrapeCreatorsClient()
    print(f"Rapid X configured: {rapid_client.is_configured}")
    print(f"YouTube (official) configured: {youtube_client.is_configured}")
    print(f"ScrapeCreators configured: {sc_client.is_configured}")

    session = get_session()
    creators = (
        session.query(Creator)
        .options(joinedload(Creator.social_accounts))
        .all()
    )

    target_platforms = {
        "x": {"X", "X(TWITTER)", "TWITTER"},
        "youtube": {"YOUTUBE"},
        "instagram": {"INSTAGRAM"},
        "tiktok": {"TIKTOK"},
        "all": {"X", "X(TWITTER)", "TWITTER", "YOUTUBE", "INSTAGRAM", "TIKTOK"},
    }[args.platform]

    targets = [
        c for c in creators
        if any(a.platform.upper() in target_platforms and a.handle for a in c.social_accounts)
    ]
    if args.limit:
        targets = targets[: args.limit]

    print(f"Total creators: {len(creators)}; enrichable targets: {len(targets)}")

    ok, errors, skipped = 0, 0, 0
    error_samples: list[str] = []
    started = time.time()

    for i, creator in enumerate(targets, 1):
        try:
            result = _enrich_creator_accounts(
                session, creator, rapid_client, youtube_client, sc_client, do_classify=not args.no_classify
            )
            for entry in result["accounts"]:
                if entry.get("status") == "ok":
                    ok += 1
                elif entry.get("status") == "skipped":
                    skipped += 1
                else:
                    errors += 1
                    msg = f"{creator.display_name} ({entry.get('handle')}): {entry.get('error')}"
                    error_samples.append(msg)
        except Exception as exc:  # noqa: BLE001 -- bulk job must not die on one bad row
            errors += 1
            error_samples.append(f"{creator.display_name}: unexpected {exc!r}")

        if i % 10 == 0 or i == len(targets):
            elapsed = time.time() - started
            print(f"[{i}/{len(targets)}] ok={ok} errors={errors} skipped={skipped} elapsed={elapsed:.0f}s", flush=True)

    session.close()
    print("\n=== Bulk enrichment summary ===")
    print(f"ok={ok} errors={errors} skipped={skipped}")
    if error_samples:
        print("Sample errors (up to 15):")
        for msg in error_samples[:15]:
            print(f"  - {msg}")


if __name__ == "__main__":
    main()
