import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.reconcile_bd_data import _inventory_delta, _looks_char_joined


class TestCharJoinDetection(unittest.TestCase):
    def test_flags_the_actual_gtm_motion_regression(self):
        corrupted = "M; o; v; e;  ; c; r; e; a; t; o; r; s;  ; u; p;  ; a; n;  ; e; c; o; s; y; s; t; e; m; ."
        self.assertTrue(_looks_char_joined(corrupted))

    def test_does_not_flag_normal_prose(self):
        normal = "Move creators up an ecosystem ladder from community challenges and meetups to Creative Partners."
        self.assertFalse(_looks_char_joined(normal))

    def test_does_not_flag_short_strings(self):
        self.assertFalse(_looks_char_joined("Wave 1"))

    def test_does_not_flag_none(self):
        self.assertFalse(_looks_char_joined(None))

    def test_does_not_flag_semicolon_separated_real_phrases(self):
        # A legitimate "; "-joined list of short real words should not trip
        # the detector -- the pattern specifically targets single/double
        # character tokens, not short-but-real ones.
        normal = "Do not sell audience reach; do not treat credits as cash; do not assume willingness."
        self.assertFalse(_looks_char_joined(normal))


class TestSourceInventoryReconciliation(unittest.TestCase):
    def test_local_extensions_do_not_look_like_missing_canonical_rows(self):
        missing, unexpected = _inventory_delta(
            {"company:a", "company:b"},
            {"company:a", "company:b", "company:local"},
        )
        self.assertEqual(missing, set())
        self.assertEqual(unexpected, {"company:local"})

    def test_deleted_canonical_row_is_still_detected(self):
        missing, unexpected = _inventory_delta(
            {"operator:a", "operator:b"},
            {"operator:a"},
        )
        self.assertEqual(missing, {"operator:b"})
        self.assertEqual(unexpected, set())

if __name__ == "__main__":
    unittest.main()
