import unittest

from scripts import build_company_master_v4 as master


class CompanyMasterV4Tests(unittest.TestCase):
    def test_linkedin_routes_are_excluded(self):
        routes = master.non_linkedin_routes([
            "https://linkedin.com/in/person",
            "creator@example.com",
            {"type": "form", "value": "https://example.com/apply"},
        ])
        self.assertEqual(routes, ["creator@example.com", {"type": "form", "value": "https://example.com/apply"}])

    def test_budget_confidence_does_not_treat_funding_as_cash_budget(self):
        row = {"spend_evidence_level": "L1", "official_source_count": 2}
        self.assertEqual(master.budget_confidence(row, None), "low_capacity_or_unverified_lead")

    def test_inherited_budget_confidence_is_explicitly_legacy_unrevalidated(self):
        self.assertEqual(
            master.budget_confidence({}, {"budget_confidence": "confirmed"}),
            "legacy_unrevalidated_confirmed",
        )
        self.assertEqual(
            master.budget_confidence({}, {"budget_confidence": "high_probability"}),
            "legacy_unrevalidated_high_probability",
        )

    def test_best_action_uses_wave_then_within_group_rank(self):
        actions = [
            {"execution_wave": 2, "within_group_rank": 1},
            {"execution_wave": 1, "within_group_rank": 7},
            {"execution_wave": 1, "within_group_rank": 2},
        ]
        self.assertIs(master.best_action(actions), actions[2])

    def test_company_master_is_unique_and_keeps_intro_unvalidated(self):
        rows, summary = master.build_master()
        self.assertEqual(len(rows), 77)
        self.assertEqual(len({master.normalize_name(row["company"]) for row in rows}), 77)
        self.assertEqual(summary["company_count"], 77)
        self.assertTrue(all(row["human_intro_status"] == "human_intro_unvalidated" for row in rows))
        self.assertTrue(all(row["global_rank"] is None for row in rows))
        self.assertGreaterEqual(summary["solomon_graph_reachable_count"], 22)
        gamma = next(row for row in rows if row["company"] == "Gamma")
        self.assertFalse(any("linkedin" in str(url).lower() for url in gamma["source_urls"]))
        viggle = next(row for row in rows if row["company"] == "Viggle AI")
        self.assertEqual(viggle["sponsor_observation_count"], 1)
        self.assertEqual(summary["sponsor_observation_company_count"], 14)
        legacy = [row for row in rows if row["cohort"] == "existing_v3_ranked"]
        self.assertEqual(len(legacy), 26)
        self.assertTrue(all(row["budget_confidence"].startswith("legacy_unrevalidated_") for row in legacy))
        self.assertTrue(all(row["revalidation_needed"] is True for row in legacy))
        self.assertTrue(all(row["spend_last_verified_at"] for row in legacy))
        replit = next(row for row in legacy if row["company"] == "Replit")
        self.assertEqual(replit["spend_last_verified_at"], "2026-08-24")
        self.assertNotEqual(replit["spend_last_verified_at"], replit["last_verified_at"])
        self.assertEqual(summary["legacy_bare_high_confidence_conflict_count"], 0)
        preserved = [
            row for row in rows
            if row["company"] in {"Replit", "Cursor", "Runway", "Perplexity", "Pika"}
        ]
        self.assertEqual(len(preserved), 5)
        self.assertTrue(all(row["solomon_primary_edge_directions"] for row in preserved))
        self.assertTrue(all(row["solomon_direction_data_unavailable"] is False for row in preserved))


if __name__ == "__main__":
    unittest.main()
