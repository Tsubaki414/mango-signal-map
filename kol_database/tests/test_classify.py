import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.classify import normalize_classification_result


class TestNormalizeClassificationResult(unittest.TestCase):
    def test_literal_null_string_becomes_none(self):
        # Regression: GPT sometimes emits the string "null" instead of a
        # real JSON null; this must not end up stored as the text "null".
        result = normalize_classification_result(
            {"creator_class": "KOL", "region": "null", "language": "Spanish"}
        )
        self.assertIsNone(result["region"])
        self.assertEqual(result["language"], "Spanish")

    def test_n_a_and_none_variants_cleaned(self):
        for junk in ("N/A", "None", "n/a", "  ", "Unknown"):
            result = normalize_classification_result({"creator_class": "KOL", "region": junk})
            self.assertIsNone(result["region"], f"{junk!r} should normalize to None")

    def test_invalid_creator_class_falls_back_to_unknown(self):
        result = normalize_classification_result({"creator_class": "Superstar"})
        self.assertEqual(result["creator_class"], "Unknown")

    def test_unknown_class_gets_low_confidence_default(self):
        result = normalize_classification_result({"creator_class": "Unknown"})
        self.assertEqual(result["classification_confidence"], "Low")

    def test_valid_fields_pass_through_untouched(self):
        result = normalize_classification_result(
            {"creator_class": "Top KOL", "promotion_level": "Medium", "classification_confidence": "High", "region": "US"}
        )
        self.assertEqual(result["creator_class"], "Top KOL")
        self.assertEqual(result["promotion_level"], "Medium")
        self.assertEqual(result["classification_confidence"], "High")
        self.assertEqual(result["region"], "US")


if __name__ == "__main__":
    unittest.main()
