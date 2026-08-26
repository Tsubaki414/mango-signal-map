import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.enrichment import TweetRecord, compute_stats, extract_promoted_projects


def _tweet(views, likes=10, replies=1, reposts=1, is_repost=False, is_pinned=False, text="hello", days_ago=0):
    return TweetRecord(
        post_id="1",
        text=text,
        created_at_raw=None,
        created_at=dt.datetime(2026, 8, 25) - dt.timedelta(days=days_ago),
        views=views,
        likes=likes,
        replies=replies,
        reposts=reposts,
        is_repost=is_repost,
        is_pinned=is_pinned,
    )


class TestComputeStats(unittest.TestCase):
    def test_pinned_excluded_from_averages(self):
        records = [_tweet(1_000_000, is_pinned=True)] + [_tweet(1000, days_ago=i) for i in range(5)]
        stats = compute_stats(records)
        # If the pinned viral post leaked in, avg_views would be enormous.
        self.assertLess(stats.avg_views, 5000)

    def test_reposts_excluded_from_original_averages_but_counted_in_ratio(self):
        records = [_tweet(1000, days_ago=i) for i in range(3)] + [_tweet(50, is_repost=True, days_ago=i) for i in range(3)]
        stats = compute_stats(records)
        self.assertEqual(stats.original_sample_size, 3)
        self.assertAlmostEqual(stats.original_repost_ratio, 0.5)

    def test_trimmed_mean_drops_single_outlier(self):
        views = [100, 200, 300, 400, 100_000]
        records = [_tweet(v, days_ago=i) for i, v in enumerate(views)]
        stats = compute_stats(records)
        # trimmed mean drops the single highest (100000) and lowest (100) -> mean(200,300,400)=300
        self.assertEqual(stats.avg_views, 300.0)
        # median stays honest about the raw distribution
        self.assertEqual(stats.median_views, 300.0)

    def test_no_posts_returns_none_not_zero(self):
        stats = compute_stats([])
        self.assertIsNone(stats.avg_views)
        self.assertIsNone(stats.engagement_rate)

    def test_low_sample_flagged(self):
        records = [_tweet(1000), _tweet(2000)]
        stats = compute_stats(records)
        self.assertTrue(stats.low_sample)


class TestPromotedProjects(unittest.TestCase):
    def test_extracts_cashtags_from_originals_only(self):
        records = [
            _tweet(1000, text="Excited about $NOTION and $AI"),
            _tweet(1000, text="Check out $NOTION again"),
            _tweet(1000, text="RT mentioning $SPAM", is_repost=True),
        ]
        counts = extract_promoted_projects(records)
        self.assertEqual(counts.get("$NOTION"), 2)
        self.assertNotIn("$SPAM", counts)


if __name__ == "__main__":
    unittest.main()
