import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.parsing import followers_annotation, parse_followers, parse_quote_text


class TestParseFollowers(unittest.TestCase):
    def test_plain_comma_number(self):
        self.assertEqual(parse_followers("864,168"), 864168)

    def test_k_suffix(self):
        self.assertEqual(parse_followers("1.2K"), 1200)

    def test_m_suffix(self):
        self.assertEqual(parse_followers("2.5M"), 2_500_000)

    def test_none_and_empty(self):
        self.assertIsNone(parse_followers(None))
        self.assertIsNone(parse_followers(""))

    def test_does_not_concatenate_trailing_annotation(self):
        # Regression: a cell like "54,591（10天均浏览1,290...)" must NOT become
        # 545911012902 by gluing every digit run together.
        raw = "54,591（10天均浏览1,290，浏览/粉丝比2.4%，⚠️近期浏览偏低）"
        self.assertEqual(parse_followers(raw), 54591)

    def test_annotation_extracted_separately(self):
        raw = "54,591（10天均浏览1,290，浏览/粉丝比2.4%）"
        note = followers_annotation(raw)
        self.assertIn("1,290", note)
        self.assertNotIn("54,591", note)


class TestParseQuoteText(unittest.TestCase):
    def test_bulleted_list(self):
        text = "• Single post: $300• Dedicated thread: $400• Quote post: $150• Repost: $80"
        results = parse_quote_text(text)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.is_confident for r in results))
        amounts = sorted(r.amount for r in results)
        self.assertEqual(amounts, [80.0, 150.0, 300.0, 400.0])

    def test_semicolon_list_mixed_currency(self):
        text = "Dedicated thread: £1,499Single post: £999Quote repost: £597"
        results = parse_quote_text(text)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.currency == "GBP" for r in results))

    def test_range_price_uses_midpoint(self):
        results = parse_quote_text("$500 to $800")
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual((r.amount_min, r.amount_max, r.amount), (500.0, 800.0, 650.0))

    def test_no_price_stays_unconfident_not_guessed(self):
        results = parse_quote_text("Flexible to requirements and budget")
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].is_confident)
        self.assertIsNone(results[0].amount)

    def test_bare_number_with_per_cue(self):
        results = parse_quote_text("150 per tweet; 300 per Post")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.is_confident for r in results))
        self.assertEqual(sorted(r.amount for r in results), [150.0, 300.0])

    def test_percentage_not_misread_as_price(self):
        # "30-50%" must not become a $30-50 price just because it has digits.
        results = parse_quote_text("付费投放+30-50%")
        self.assertFalse(results[0].is_confident)

    def test_empty_text(self):
        self.assertEqual(parse_quote_text(""), [])
        self.assertEqual(parse_quote_text(None), [])

    def test_k_suffix_means_thousands(self):
        # Regression (real data): "£6k" is six thousand pounds, not literally
        # £6 -- a 1000x-too-low quote would badly mislead budget math.
        results = parse_quote_text("3条 £6k/$8000")
        confident = [r for r in results if r.is_confident]
        self.assertTrue(any(r.amount == 6000.0 and r.currency == "GBP" for r in confident))

    def test_k_suffix_with_decimal(self):
        results = parse_quote_text("每30天+£2.5k")
        confident = [r for r in results if r.is_confident]
        self.assertTrue(any(r.amount == 2500.0 for r in confident))

    def test_glued_numbered_list_does_not_swallow_list_index_as_price(self):
        # Regression (found in real data, creator "iam_elias1"): with no
        # space before the next list item, "350$2. Per single post" must
        # not be misread as a $2 price -- the real $350 should win.
        text = "1. Per thread 350$2. Per single post 250$3. Per quote 150$"
        results = parse_quote_text(text)
        self.assertEqual(results[0].amount, 350.0)
        self.assertNotEqual(results[0].amount, 2.0)


if __name__ == "__main__":
    unittest.main()
