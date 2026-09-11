"""PATCH semantics for shortlist pricing overrides."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app import ShortlistItemEdit, _apply_shortlist_item_edit
from backend.models import ShortlistItem


class TestShortlistItemPatch(unittest.TestCase):
    def test_explicit_null_clears_manual_quote_override(self):
        item = ShortlistItem(
            shortlist_id=1,
            creator_id=2,
            rate_card_id=3,
            quote_usd_override=1250,
            notes="keep",
        )
        _apply_shortlist_item_edit(item, ShortlistItemEdit(quote_usd_override=None))
        self.assertIsNone(item.quote_usd_override)
        self.assertEqual(item.rate_card_id, 3)
        self.assertEqual(item.notes, "keep")

    def test_omitted_quote_override_is_preserved(self):
        item = ShortlistItem(
            shortlist_id=1,
            creator_id=2,
            rate_card_id=3,
            quote_usd_override=1250,
            notes="old",
        )
        _apply_shortlist_item_edit(item, ShortlistItemEdit(notes="new"))
        self.assertEqual(item.quote_usd_override, 1250)
        self.assertEqual(item.notes, "new")

    def test_explicit_null_also_clears_optional_rate_card_and_notes(self):
        item = ShortlistItem(
            shortlist_id=1,
            creator_id=2,
            rate_card_id=3,
            quote_usd_override=None,
            notes="old",
        )
        _apply_shortlist_item_edit(item, ShortlistItemEdit(rate_card_id=None, notes=None))
        self.assertIsNone(item.rate_card_id)
        self.assertIsNone(item.notes)

    def test_negative_or_non_finite_override_is_rejected(self):
        for value in (-0.01, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ShortlistItemEdit(quote_usd_override=value)


if __name__ == "__main__":
    unittest.main()
