import unittest

from scripts import build_v4_x_graph as graph


def target_cache(result_type, requested_handle, users):
    return {
        "request": {
            "priority_rank": 1,
            "company": "Example AI",
            "aliases": [],
            "cohort": "new_longtail_ai_v4",
            "handle": requested_handle,
            "handle_status": "verify",
        },
        "result_type": result_type,
        "response": {"result": users},
    }


class V4XGraphTests(unittest.TestCase):
    def test_profile_lookup_requires_exact_requested_handle(self):
        matching = {
            "rest_id": "42",
            "legacy": {"screen_name": "Example_AI", "name": "Example AI"},
        }
        resolved = graph.resolve_target_identity(
            target_cache("profile_lookup", "@example_ai", matching), "cache.json"
        )
        self.assertTrue(resolved["technical_identity_confirmed"])
        self.assertEqual(resolved["resolved_rest_id"], "42")
        self.assertEqual(resolved["technical_x_identity_status"], "confirmed_exact_requested_handle")

        mismatch = {
            "rest_id": "99",
            "legacy": {"screen_name": "ExampleSupport", "name": "Support"},
        }
        unresolved = graph.resolve_target_identity(
            target_cache("profile_lookup", "@example_ai", mismatch), "cache.json"
        )
        self.assertFalse(unresolved["technical_identity_confirmed"])
        self.assertEqual(unresolved["technical_x_identity_status"], "unresolved_profile_lookup_handle_mismatch")

    def test_search_results_always_require_manual_match(self):
        exact_named_candidate = {
            "rest_id": "42",
            "legacy": {"screen_name": "example_ai", "name": "Example AI"},
        }
        result = graph.resolve_target_identity(
            target_cache("search_requires_manual_match", "@example_ai", exact_named_candidate),
            "search-cache.json",
        )
        self.assertFalse(result["technical_identity_confirmed"])
        self.assertTrue(result["manual_match_required"])
        self.assertEqual(result["resolved_rest_id"], "")
        self.assertEqual(result["search_candidate_count"], 1)
        self.assertEqual(result["technical_x_identity_status"], "search_candidates_manual_match_required")

    def test_official_link_confirms_only_exact_cached_rapid_handle_and_rest_id(self):
        candidate = {
            "__typename": "User",
            "rest_id": "42",
            "core": {"screen_name": "Official_AI", "name": "Official AI"},
        }
        cache = target_cache("search_requires_manual_match", "", candidate)
        base = graph.resolve_target_identity(cache, "search-cache.json")
        adjudication = {
            "company": "Example AI",
            "status": "confirmed_official_link_matches_rapid_candidate",
            "official_domain": "official.ai",
            "official_social_url": "https://x.com/Official_AI",
            "proposed_handle": "@Official_AI",
            "rest_id": "42",
            "rapid_candidate_match": {
                "matched": True, "handle": "@official_ai", "rest_id": "42",
            },
            "evidence": [],
        }
        resolved = graph.apply_identity_adjudication(base, cache, adjudication)
        self.assertTrue(resolved["technical_identity_confirmed"])
        self.assertEqual(resolved["resolved_rest_id"], "42")
        self.assertEqual(
            resolved["technical_x_identity_status"],
            "confirmed_official_link_exact_cached_rapid_candidate",
        )

        tampered = {**adjudication, "rest_id": "99"}
        rejected = graph.apply_identity_adjudication(base, cache, tampered)
        self.assertFalse(rejected["technical_identity_confirmed"])
        self.assertEqual(rejected["resolved_rest_id"], "")

    def test_corrected_handle_requires_exact_rapid_profile_resolution(self):
        cache = target_cache("search_requires_manual_match", "", {})
        base = graph.resolve_target_identity(cache, "search-cache.json")
        adjudication = {
            "company": "Example AI",
            "status": "corrected_handle_requires_rapid_lookup",
            "official_domain": "official.ai",
            "official_social_url": "https://x.com/Official_AI",
            "proposed_handle": "@Official_AI",
            "evidence": [],
        }
        correction = {
            "company": "Example AI",
            "status": "confirmed_exact_official_handle_from_rapid_profile",
            "confirmed": True,
            "requested_handle": "@Official_AI",
            "returned_handle": "official_ai",
            "rest_id": "42",
            "profile": {"rest_id": "42", "handle": "Official_AI", "name": "Official AI"},
            "cache_file": "correction.json",
        }
        resolved = graph.apply_identity_adjudication(base, cache, adjudication, correction)
        self.assertTrue(resolved["technical_identity_confirmed"])
        self.assertEqual(resolved["resolved_handle"], "Official_AI")

        mismatched = {**correction, "returned_handle": "OfficialSupport"}
        rejected = graph.apply_identity_adjudication(base, cache, adjudication, mismatched)
        self.assertFalse(rejected["technical_identity_confirmed"])
        self.assertEqual(
            rejected["technical_x_identity_status"],
            "corrected_official_handle_resolution_artifact_exact_validation_failed",
        )

    def test_unresolved_official_link_adjudication_cannot_confirm(self):
        candidate = {
            "rest_id": "42",
            "legacy": {"screen_name": "example_ai", "name": "Example AI"},
        }
        cache = target_cache("profile_lookup", "@example_ai", candidate)
        base = graph.resolve_target_identity(cache, "profile-cache.json")
        self.assertTrue(base["technical_identity_confirmed"])
        unresolved = graph.apply_identity_adjudication(base, cache, {
            "company": "Example AI",
            "status": "unresolved_no_official_link",
            "official_domain": "example.ai",
            "official_social_url": "",
            "proposed_handle": "",
            "evidence": [],
        })
        self.assertFalse(unresolved["technical_identity_confirmed"])
        self.assertEqual(unresolved["technical_x_identity_status"], "unresolved_no_official_link")

    def test_directional_snapshot_preserves_following_and_follower_edges(self):
        adjacency = {}
        ledger = {}
        graph.add_direction_snapshot(
            adjacency,
            ledger,
            "observer",
            {
                "kind": "following",
                "handle": "observer_handle",
                "ids": ["outbound"],
                "pages": 1,
                "complete": True,
                "coverage_label": "complete",
                "cache_file": "following.json",
            },
            edge_role_prefix="connector",
        )
        graph.add_direction_snapshot(
            adjacency,
            ledger,
            "observer",
            {
                "kind": "followers",
                "handle": "observer_handle",
                "ids": ["inbound"],
                "pages": 1,
                "complete": False,
                "coverage_label": "page_capped_partial",
                "cache_file": "followers.json",
            },
            edge_role_prefix="connector",
        )
        self.assertIn("outbound", adjacency["observer"])
        self.assertNotIn("observer", adjacency.get("outbound", set()))
        self.assertIn("observer", adjacency["inbound"])
        self.assertEqual(ledger[("inbound", "observer")]["coverage_labels"], {"page_capped_partial"})

    def test_shared_interest_v_shape_is_rejected(self):
        directed = {
            "solomon": {"popular"},
            "connector": {"popular", "target"},
        }
        routable, rejected = graph.enumerate_routable_candidate_paths(
            directed, "solomon", "target", max_hops=3
        )
        self.assertEqual(routable, [])
        self.assertIn(["solomon", "popular", "connector", "target"], rejected)

    def test_hop_labels_distinguish_direct_secondary_and_third(self):
        self.assertEqual(graph.hop_label(1), ("direct", "L1"))
        self.assertEqual(graph.hop_label(2), ("secondary", "L2"))
        self.assertEqual(graph.hop_label(3), ("third", "L3"))
        with self.assertRaises(ValueError):
            graph.hop_label(4)


if __name__ == "__main__":
    unittest.main()
