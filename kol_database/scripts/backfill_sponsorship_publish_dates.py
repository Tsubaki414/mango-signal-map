"""Backfill seven missing YouTube upload dates by exact content URL.

Dates were retrieved from public YouTube metadata on 2026-08-29.  The update
is URL-keyed, NULL-only and idempotent: an existing production/human date is
never replaced.  The Notion evergreen partner directory intentionally is not
listed because it has no honest publication date; its ``created_at`` remains
the observation/collection date.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session  # noqa: E402
from backend.models import SponsorshipEvidence  # noqa: E402


PUBLISH_DATES_BY_URL = {
    "https://www.youtube.com/watch?v=cg7k-7QThqU": "2025-11-11",
    "https://www.youtube.com/watch?v=vyOUX-uB_PQ": "2025-07-26",
    "https://www.youtube.com/watch?v=4WqAmAZ3atI": "2025-02-26",
    "https://www.youtube.com/watch?v=-VYvADXIJbU": "2026-07-07",
    "https://www.youtube.com/watch?v=NeOgIXFuSdM": "2026-05-12",
    "https://www.youtube.com/watch?v=-_ZlUTXaFqA": "2026-04-16",
    "https://www.youtube.com/watch?v=gdpMHIzTYvc": "2026-02-18",
}


def backfill_publish_dates(rows: list[SponsorshipEvidence]) -> int:
    changed = 0
    for row in rows:
        published_at = PUBLISH_DATES_BY_URL.get((row.content_url or "").strip())
        if published_at is None or (row.published_at or "").strip():
            continue
        row.published_at = published_at
        changed += 1
    return changed


def main() -> None:
    session = get_session()
    try:
        rows = (
            session.query(SponsorshipEvidence)
            .filter(SponsorshipEvidence.content_url.in_(PUBLISH_DATES_BY_URL))
            .all()
        )
        changed = backfill_publish_dates(rows)
        session.commit()
        print(f"[backfill_sponsorship_publish_dates] filled {changed} NULL YouTube upload dates")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
