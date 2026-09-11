"""Exact URL-keyed, NULL-only sponsorship publication-date backfill."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import SponsorshipEvidence
from backend.bd_serializers import sponsorship_json
from scripts.backfill_sponsorship_publish_dates import (
    PUBLISH_DATES_BY_URL,
    backfill_publish_dates,
)


def evidence(url: str, published_at: str | None) -> SponsorshipEvidence:
    return SponsorshipEvidence(
        company_id="company:test",
        creator_name_raw="Creator",
        platform="YouTube",
        content_url=url,
        disclosure_type="paid_sponsorship",
        published_at=published_at,
    )


class TestSponsorshipPublishDateBackfill(unittest.TestCase):
    def test_all_seven_exact_urls_fill_and_second_run_is_idempotent(self):
        rows = [evidence(url, None) for url in PUBLISH_DATES_BY_URL]
        self.assertEqual(backfill_publish_dates(rows), 7)
        self.assertEqual(
            {row.content_url: row.published_at for row in rows},
            PUBLISH_DATES_BY_URL,
        )
        self.assertEqual(backfill_publish_dates(rows), 0)

    def test_existing_date_is_never_overwritten(self):
        url = next(iter(PUBLISH_DATES_BY_URL))
        row = evidence(url, "2099-01-01")
        self.assertEqual(backfill_publish_dates([row]), 0)
        self.assertEqual(row.published_at, "2099-01-01")

    def test_notion_evergreen_directory_remains_without_fake_publish_date(self):
        row = evidence("https://www.notion.com/partners", None)
        row.created_at = dt.datetime(2026, 8, 27, 3, 38, 43)
        self.assertEqual(backfill_publish_dates([row]), 0)
        self.assertIsNone(row.published_at)
        serialized = sponsorship_json(row)
        self.assertIsNone(serialized["published_at"])
        self.assertEqual(serialized["collected_at"], "2026-08-27T03:38:43")


if __name__ == "__main__":
    unittest.main()
