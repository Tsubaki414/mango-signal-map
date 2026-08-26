import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.fx import to_usd


class TestFx(unittest.TestCase):
    def test_usd_passthrough(self):
        amount, rate = to_usd(100, "USD")
        self.assertEqual(amount, 100)
        self.assertEqual(rate, 1.0)

    def test_gbp_conversion(self):
        amount, rate = to_usd(100, "GBP")
        self.assertAlmostEqual(amount, 127.0)
        self.assertEqual(rate, 1.27)

    def test_none_amount(self):
        amount, rate = to_usd(None, "USD")
        self.assertIsNone(amount)
        self.assertIsNone(rate)

    def test_unknown_currency_returns_none(self):
        amount, rate = to_usd(100, "XYZ")
        self.assertIsNone(amount)
        self.assertIsNone(rate)


if __name__ == "__main__":
    unittest.main()
