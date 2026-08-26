import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.compute import quote_summary, rate_card_cpm
from backend.models import Creator, RateCard, SocialAccount


def _rc(amount_usd, confident=True):
    return RateCard(deliverable="Post", quote_amount_usd=amount_usd, quote_currency="USD", is_confident=confident)


class TestCpm(unittest.TestCase):
    def test_cpm_basic(self):
        rc = _rc(500)
        self.assertEqual(rate_card_cpm(rc, 20000), 25.0)

    def test_cpm_none_when_no_views(self):
        rc = _rc(500)
        self.assertIsNone(rate_card_cpm(rc, None))
        self.assertIsNone(rate_card_cpm(rc, 0))

    def test_cpm_none_when_no_quote(self):
        rc = _rc(None)
        self.assertIsNone(rate_card_cpm(rc, 20000))


class TestQuoteSummary(unittest.TestCase):
    def test_ignores_unconfident_rows(self):
        creator = Creator(display_name="Test")
        creator.rate_cards = [_rc(100), _rc(300), _rc(999999, confident=False)]
        creator.social_accounts = [SocialAccount(platform="X", handle="test", avg_views=10000)]
        summary = quote_summary(creator)
        self.assertEqual(summary["quote_min_usd"], 100)
        self.assertEqual(summary["quote_max_usd"], 300)
        self.assertEqual(summary["confident_count"], 2)
        self.assertEqual(summary["deliverable_count"], 3)

    def test_no_confident_rows_returns_none_range(self):
        creator = Creator(display_name="Test")
        creator.rate_cards = [_rc(None, confident=False)]
        creator.social_accounts = []
        summary = quote_summary(creator)
        self.assertIsNone(summary["quote_min_usd"])
        self.assertIsNone(summary["cpm_min"])

    def test_cpm_range_uses_primary_account_views(self):
        creator = Creator(display_name="Test")
        creator.rate_cards = [_rc(100), _rc(500)]
        creator.social_accounts = [SocialAccount(platform="X", handle="test", avg_views=10000)]
        summary = quote_summary(creator)
        self.assertEqual(summary["cpm_min"], 10.0)
        self.assertEqual(summary["cpm_max"], 50.0)


if __name__ == "__main__":
    unittest.main()
