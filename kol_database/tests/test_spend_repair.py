"""Regression tests for guarded spend-classification boot repairs."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import Company
from scripts.fix_fundraise_only_spend_evidence import (
    LEGACY_OVERCORRECTION_RESTORES,
    repair_spend_classifications,
)


def company(company_id: str, level: str, mechanism: str, evidence: str = "full evidence") -> Company:
    return Company(
        company_id=company_id,
        name=company_id,
        spend_evidence_level=level,
        spend_mechanism_level=mechanism,
        budget_evidence=evidence,
    )


class TestSpendRepair(unittest.TestCase):
    def test_four_negation_false_positives_are_repaired_without_losing_evidence(self):
        rows = [
            company("company:talusnetwork", "L3", "active_creator_or_partner_budget", "Talus source text"),
            company("company:myshell", "L3", "active_creator_or_partner_budget", "MyShell source text"),
            company("company:alloranetwork", "L3", "active_creator_or_partner_budget", "Allora source text"),
            company("company:sapien", "L3", "active_creator_or_partner_budget", "Sapien source text"),
        ]
        changed, restored = repair_spend_classifications(rows)
        self.assertEqual((changed, restored), (4, 0))
        by_id = {row.company_id: row for row in rows}
        self.assertEqual(by_id["company:talusnetwork"].spend_evidence_level, "L2")
        self.assertEqual(by_id["company:alloranetwork"].spend_evidence_level, "L2")
        self.assertEqual(by_id["company:myshell"].spend_evidence_level, "L1")
        self.assertEqual(by_id["company:sapien"].spend_evidence_level, "L1")
        self.assertEqual([row.budget_evidence for row in rows], [
            "Talus source text",
            "MyShell source text",
            "Allora source text",
            "Sapien source text",
        ])

    def test_old_production_overcorrections_restore_only_exact_signatures(self):
        rows = [
            company(company_id, old_level, old_mechanism)
            for company_id, old_level, old_mechanism, _new_level, _new_mechanism in LEGACY_OVERCORRECTION_RESTORES
        ]
        changed, restored = repair_spend_classifications(rows)
        self.assertEqual((changed, restored), (0, 11))
        self.assertTrue(all(row.spend_evidence_level == "L2" for row in rows))

    def test_guard_preserves_later_human_or_pipeline_correction(self):
        mismatched = company("company:olas", "L1", "funding_or_token_value_only", "human correction")
        current = company("company:gaib", "L2", "formal_paid_program", "already current")
        changed, restored = repair_spend_classifications([mismatched, current])
        self.assertEqual((changed, restored), (0, 0))
        self.assertEqual(mismatched.spend_evidence_level, "L1")
        self.assertEqual(mismatched.spend_mechanism_level, "funding_or_token_value_only")
        self.assertEqual(current.spend_evidence_level, "L2")


if __name__ == "__main__":
    unittest.main()
