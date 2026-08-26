import unittest

from scripts import build_priority_action_queue_v4 as queue


class PriorityActionQueueTests(unittest.TestCase):
    def test_affiliate_never_counts_as_paid(self):
        index = queue.sponsor_index([
            {"brand": "Demo", "creator": "A", "disclosure_type": "affiliate", "content_url": "a"},
            {"brand": "Demo", "creator": "B", "disclosure_type": "mention", "content_url": "b"},
        ])
        signal = index["demo"]
        self.assertEqual(signal["explicit_paid_count"], 0)
        self.assertEqual(signal["affiliate_count"], 1)
        self.assertEqual(signal["signal"], "affiliate_only")

    def test_multi_creator_paid_is_explicitly_counted(self):
        index = queue.sponsor_index([
            {"brand": "Demo", "creator": "A", "disclosure_type": "paid_sponsorship", "content_url": "a"},
            {"brand": "Demo", "creator": "B", "disclosure_type": "paid_sponsorship", "content_url": "b"},
        ])
        self.assertEqual(index["demo"]["unique_paid_creator_count"], 2)
        self.assertEqual(index["demo"]["signal"], "multi_creator_explicit_paid_activity")

    def test_reciprocal_public_interaction_does_not_validate_intro(self):
        path = {
            "primary_path_labels": ["Solomon (@root)", "Connector (@middle)", "Target (@target)"],
        }
        index = {
            ("root", "middle"): [{"interaction_type": "reply", "tweet_url": "one"}],
            ("middle", "root"): [{"interaction_type": "reply", "tweet_url": "two"}],
        }
        signal = queue.connector_interaction_signal(path, index)
        self.assertEqual(signal["state"], "reciprocal_public_interaction_observed")
        self.assertEqual(signal["human_intro_status"], "human_intro_unvalidated")

    def test_graph_adjustment_keeps_company_follow_at_secondary_level(self):
        config = {
            "weights": {"reachability": 1, "spend_mechanism": 0, "timing": 0, "fit": 0, "buyer_clarity": 0, "evidence_quality": 0},
            "reachability_levels": {"x_secondary_company_follow": 52, "x_third_routable": 38, "public_business_route": 42, "unresolved": 10},
            "spend_mechanism_levels": {"unknown": 10},
            "penalties": {"identity_unresolved": 6},
            "tiers": {"act_now": 76, "validate": 66, "watch": 0},
        }
        candidate = {
            "company": "Demo", "rank": 1, "overall_priority": 1, "buyer_or_route": "route",
            "penalties": ["identity_unresolved"], "spend_mechanism_level": "unknown",
            "timing_score": 0, "fit_score": 0, "buyer_clarity_score": 0, "evidence_quality_score": 0,
        }
        path = {
            "technical_x_identity_status": "confirmed_exact_requested_handle", "graph_reachable": True,
            "graph_reachability_status": "reachable_in_observed_graph", "x_relationship_level": "L2",
            "x_degree": "secondary", "primary_path_labels": ["Root (@root)", "C (@c)", "Demo (@demo)"],
            "primary_edge_directions": ["mutual_follow", "follows"], "human_intro_status": "human_intro_unvalidated",
        }
        result = queue.graph_adjust_candidate(candidate, path, {}, config)
        self.assertEqual(result["reachability_level"], "x_secondary_company_follow")
        self.assertEqual(result["reachability_score"], 52)
        self.assertEqual(result["human_intro_status"], "human_intro_unvalidated")
        self.assertNotIn("identity_unresolved", result["applied_penalties"])

    def test_corrected_official_identity_and_direct_company_follow_are_scored_without_identity_penalty(self):
        config = {
            "weights": {"reachability": 1, "spend_mechanism": 0, "timing": 0, "fit": 0, "buyer_clarity": 0, "evidence_quality": 0},
            "reachability_levels": {"x_direct_company_follow": 62, "public_business_route": 42, "unresolved": 10},
            "spend_mechanism_levels": {"unknown": 10},
            "penalties": {"identity_unresolved": 6},
            "tiers": {"act_now": 76, "validate": 66, "watch": 0},
        }
        candidate = {
            "company": "Demo", "rank": 1, "overall_priority": 1, "buyer_or_route": "route",
            "penalties": ["identity_unresolved"], "spend_mechanism_level": "unknown",
            "timing_score": 0, "fit_score": 0, "buyer_clarity_score": 0, "evidence_quality_score": 0,
        }
        path = {
            "technical_x_identity_status": "confirmed_corrected_official_handle_exact_rapid_profile",
            "graph_reachable": True, "x_degree": "direct", "primary_path_labels": ["Root (@root)", "Demo (@demo)"],
            "primary_edge_directions": ["follows"], "human_intro_status": "human_intro_unvalidated",
        }
        result = queue.graph_adjust_candidate(candidate, path, {}, config)
        self.assertEqual(result["reachability_level"], "x_direct_company_follow")
        self.assertEqual(result["reachability_score"], 62)
        self.assertNotIn("identity_unresolved", result["applied_penalties"])

    def test_direct_company_path_produces_direct_contextual_action_without_claiming_relationship(self):
        action = queue.next_action(
            {
                "graph_reachable": True,
                "x_degree": "direct",
                "x_primary_path": ["Solomon (@root)", "Kite (@kite)"],
                "buyer_or_route": "official form",
            },
            {},
        )
        self.assertIn("direct X follow", action)
        self.assertIn("不要把公司账号邻接当成人际关系", action)
        self.assertIn("official form", action)

    def test_mango_path_is_carried_without_validating_human_intro(self):
        target = {
            "company": "Demo",
            "graph_reachable": True,
            "target_handle": "demo",
            "mango_secondary_paths": [{
                "connector_handle": "seed",
                "path_labels": ["@MangoLabs_", "@seed", "@demo"],
                "edge_directions": ["@MangoLabs_ -> @seed", "@seed -> @demo"],
            }],
        }
        row = queue.mango_only_legacy_action_row(target, {"rank": 2, "overall_priority_solomon_x": 70})
        self.assertTrue(row["mango_graph_reachable"])
        self.assertEqual(row["human_intro_status"], "human_intro_unvalidated")
        self.assertFalse(row["budget_authority_confirmed"])

    def test_mango_interaction_prefers_connector_with_target_side_evidence(self):
        target = {
            "target_handle": "demo",
            "mango_secondary_paths": [
                {"connector_handle": "cold"},
                {"connector_handle": "warm"},
            ],
        }
        index = {
            ("warm", "demo"): [{"interaction_type": "mention", "tweet_url": "https://x.com/warm/status/1"}],
            ("warm", "mangolabs_"): [{"interaction_type": "reply", "tweet_url": "https://x.com/warm/status/2"}],
        }
        signal = queue.mango_interaction_signal(target, index)
        self.assertEqual(signal["state"], "seed_target_public_interaction_observed")
        self.assertEqual(signal["preferred_connector_handle"], "@warm")
        self.assertEqual(signal["preferred_path_index"], 1)
        self.assertEqual(signal["human_intro_status"], "human_intro_unvalidated")

    def test_zero_mango_interactions_are_bounded_sample_absence(self):
        target = {"target_handle": "demo", "mango_secondary_paths": [{"connector_handle": "seed"}]}
        signal = queue.mango_interaction_signal(target, {})
        self.assertEqual(signal["state"], "follow_path_only_no_interaction_observed_in_bounded_sample")
        self.assertEqual(signal["seed_target_interaction_count"], 0)
        self.assertEqual(signal["human_intro_status"], "human_intro_unvalidated")

    def test_action_evidence_contract_keeps_intro_and_budget_unverified(self):
        fields = queue.evidence_contract_fields(
            row={
                "graph_reachable": True,
                "mango_graph_reachable": False,
                "connector_interaction_signal": {"state": "one_way_public_interaction_observed"},
                "last_verified_at": "2026-08-25",
            }
        )
        self.assertIn("rapid_x_follow_graph", fields["evidence_types"])
        self.assertIn("rapid_x_public_interaction", fields["evidence_types"])
        self.assertIn("unverified_human_intro_willingness", fields["fact_status_rollup"])
        self.assertIn("unverified_budget_authority", fields["fact_status_rollup"])

    def test_selection_includes_reachable_candidate_beyond_top_n(self):
        rows = [
            {"company": "Top", "pre_graph_rank": 1, "graph_reachable": False},
            {"company": "Warm", "pre_graph_rank": 40, "graph_reachable": True},
            {"company": "Cold", "pre_graph_rank": 41, "graph_reachable": False},
        ]
        selected = queue.select_new_actions(rows, set(), top_n=1)
        self.assertEqual([row["company"] for row in selected], ["Top", "Warm"])

    def test_legacy_context_surfaces_public_evidence_but_excludes_linkedin(self):
        context = queue.legacy_evidence_context(
            {
                "budget_signals": ["Creator program", "$10M funding"],
                "evidence_ids": ["official", "linkedin"],
                "last_verified_date": "2026-08-24",
            },
            {
                "official": {"source_url": "https://example.com/program", "source_type": "official", "claim": "Program exists", "verified_date": "2026-08-24"},
                "linkedin": {"source_url": "https://linkedin.com/in/demo", "source_type": "linkedin", "claim": "Person", "verified_date": "2026-08-24"},
            },
        )
        self.assertIn("Creator program", context["budget_summary"])
        self.assertIn("Financing/capacity alone", context["budget_summary"])
        self.assertEqual(context["evidence_urls"], ["https://example.com/program"])

    def test_preserved_v3_paths_are_backfilled_from_positive_rapid_x_ids(self):
        primary = {
            row["company"]: row
            for row in queue.load_json(queue.V3_PATHS, [])
            if row.get("primary") is True
        }
        expected_targets = {
            "Replit": "@jenniekusu -> @Replit",
            "Cursor": "@jenniekusu -> @cursor_ai",
            "Runway": "@jenniekusu -> @runwayml",
            "Perplexity": "@jenniekusu -> @perplexity_ai",
            "Pika": "@jenniekusu -> @pika_labs",
        }
        for company, target_direction in expected_targets.items():
            with self.subTest(company=company):
                result = queue.legacy_direction_backfill(primary[company])
                self.assertFalse(result["direction_data_unavailable"])
                self.assertEqual(result["primary_edge_kinds"], ["mutual_follow", "follows"])
                self.assertIn("@Solomon_Nahhh -> @jenniekusu", result["primary_edge_directions"])
                self.assertIn("@jenniekusu -> @Solomon_Nahhh", result["primary_edge_directions"])
                self.assertIn(target_direction, result["primary_edge_directions"])
                self.assertTrue(result["direction_evidence_files"])

    def test_missing_legacy_cache_is_not_silently_rendered_as_no_direction(self):
        result = queue.legacy_direction_backfill({
            "path_rest_ids": ["1", "2"],
            "path_labels": ["Root (@not_cached_root)", "Target (@not_cached_target)"],
            "edge_types": ["follows"],
        })
        self.assertTrue(result["direction_data_unavailable"])
        self.assertEqual(result["primary_edge_directions"], [])
        self.assertIn("Missing positive", result["direction_data_reason"])


if __name__ == "__main__":
    unittest.main()
