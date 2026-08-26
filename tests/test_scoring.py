import json
import unittest
from pathlib import Path

from mangobd.models import Evidence, Project
from mangobd.scoring import score_projects


class ScoringTests(unittest.TestCase):
    def test_reachability_is_largest_single_weight(self):
        config = json.loads(Path("config/scoring.json").read_text())
        self.assertGreaterEqual(config["weights"]["reachability"], max(
            value for key, value in config["weights"].items() if key != "reachability"
        ))

    def test_verified_path_beats_cold_when_other_signals_match(self):
        config = json.loads(Path("config/scoring.json").read_text())
        evidence = Evidence("e1", "x", "https://example.com", "x", None, "2026-08-24", "confirmed", "official")
        common = dict(
            category="AI", geography="US", stage="Growth", business_model="Subscription",
            budget_signals=["ARR", "creator program"], marketing_channels=["YouTube"],
            historical_campaigns=["sponsor"], decision_maker_ids=[], public_contact_methods=[],
            budget_confidence="confirmed", fit_score=80, timing_score=80, evidence_ids=["e1"],
            recommended_next_action="contact", last_verified_date="2026-08-24"
        )
        warm = Project("warm", "Warm", mango_relationship_path=["mango", "connector", "operator"], reachability_level="verified_two_hop", **common)
        cold = Project("cold", "Cold", mango_relationship_path=[], reachability_level="cold_only", **common)
        ranked = score_projects([cold, warm], {"e1": evidence}, config)
        self.assertEqual(ranked[0].id, "warm")

    def test_reachability_degree_order_is_explicit(self):
        levels = json.loads(Path("config/scoring.json").read_text())["reachability_levels"]
        self.assertGreater(levels["verified_two_hop"], levels["verified_third_degree"])
        self.assertGreater(levels["verified_third_degree"], levels["public_business_contact"])
        self.assertGreater(levels["public_business_contact"], levels["linkedin_third_plus_only"])


if __name__ == "__main__":
    unittest.main()
