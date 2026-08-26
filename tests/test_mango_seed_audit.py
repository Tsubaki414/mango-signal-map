import unittest

from scripts.build_mango_seed_audit import (
    audit_seed,
    build_target_relationships,
    dedupe_targets,
    normalize_new_target,
    resolve_old_target,
)


def snapshot(kind, ids, complete=True, cache_file=None):
    return {
        "kind": kind,
        "ids": list(ids),
        "complete": complete,
        "pages": 1,
        "coverage_label": "complete" if complete else "page_capped_partial",
        "cache_file": cache_file or f"{kind}.json",
    }


def profile(entity_id, handle, rest_id, exact=True):
    return {
        "entity_id": entity_id,
        "name": handle,
        "handle": handle,
        "resolved_handle": handle,
        "rest_id": rest_id,
        "identity_status": "exact_handle_match" if exact else "handle_mismatch_requires_review",
    }


def runtime(profile_row, following_ids=(), follower_ids=(), following_complete=True, followers_complete=True):
    following = snapshot("following", following_ids, following_complete, f"following-{profile_row['handle']}.json")
    followers = snapshot("followers", follower_ids, followers_complete, f"followers-{profile_row['handle']}.json")
    return {
        "audit": audit_seed(profile_row, following, followers),
        "following": following,
        "followers": followers,
    }


def exact_target(company="Target", rest_id="T", handle="target"):
    return {
        "company_key": company.casefold(),
        "company": company,
        "aliases": [],
        "cohorts": ["test"],
        "requested_handles": [handle],
        "technical_identity_confirmed": True,
        "technical_x_identity_status": "confirmed_exact_requested_handle",
        "target_rest_id": rest_id,
        "target_handle": handle,
        "identity_evidence_files": ["target.json"],
        "identity_observations": [],
    }


class MangoSeedAuditTests(unittest.TestCase):
    def test_secondary_path_preserves_actual_direction(self):
        root = runtime(profile("mango", "MangoLabs_", "R"), following_ids=["S"])
        seed = runtime(profile("person_seed", "seed", "S"), following_ids=["R", "T"])
        target = build_target_relationships([exact_target()], [root, seed])[0]

        self.assertTrue(target["graph_reachable"])
        self.assertEqual(target["x_degree"], 2)
        path = target["mango_secondary_paths"][0]
        self.assertEqual(path["path_rest_ids"], ["R", "S", "T"])
        self.assertEqual(path["edge_kinds"], ["mutual_follow", "follows"])
        self.assertIn("@MangoLabs_ -> @seed", path["edge_directions"])
        self.assertIn("@seed -> @MangoLabs_", path["edge_directions"])
        self.assertIn("@seed -> @target", path["edge_directions"])
        self.assertEqual(path["human_intro_status"], "human_intro_unvalidated")

    def test_shared_interest_v_is_not_a_secondary_route(self):
        # R -> S and T -> S means R and T merely share the account S as an interest.
        root = runtime(profile("mango", "MangoLabs_", "R"), following_ids=["S"])
        seed = runtime(profile("person_seed", "seed", "S"), follower_ids=["T"])
        target = build_target_relationships([exact_target()], [root, seed])[0]

        self.assertFalse(target["graph_reachable"])
        self.assertEqual(target["mango_secondary_paths"], [])
        self.assertEqual(target["rejected_shared_interest_v_count"], 1)
        self.assertEqual(target["direct_seed_edges"][0]["observed_edge_rest_id_directions"], ["T->S"])

    def test_partial_snapshot_mutual_is_lower_bound_not_complete_zero(self):
        audited = audit_seed(
            profile("person_seed", "seed", "S"),
            snapshot("following", [], True),
            snapshot("followers", [], False),
        )
        self.assertEqual(audited["observed_mutual_count"], 0)
        self.assertFalse(audited["mutual_audit_complete"])
        self.assertTrue(audited["observed_mutual_count_is_lower_bound"])
        self.assertIn("unknown", audited["non_observation_interpretation"])

    def test_no_path_in_partial_cache_is_not_negative_relationship_evidence(self):
        root = runtime(profile("mango", "MangoLabs_", "R"), followers_complete=False)
        target = build_target_relationships([exact_target()], [root])[0]
        self.assertFalse(target["graph_reachable"])
        self.assertFalse(target["relationship_coverage_complete"])
        self.assertTrue(target["non_observation_is_not_negative_evidence"])
        self.assertEqual(
            target["graph_reachability_status"],
            "no_observed_routable_path_in_available_cache",
        )

    def test_unresolved_target_is_not_assigned_false_graph_reachability(self):
        unresolved = exact_target()
        unresolved.update(
            technical_identity_confirmed=False,
            technical_x_identity_status="unresolved_no_exact_handle_user_in_cache",
            target_rest_id="",
            target_handle="",
        )
        root = runtime(profile("mango", "MangoLabs_", "R"))
        target = build_target_relationships([unresolved], [root])[0]
        self.assertIsNone(target["graph_reachable"])
        self.assertEqual(target["human_intro_status"], "not_evaluated")
        self.assertEqual(target["direct_seed_edges"], [])

    def test_old_target_requires_exact_cached_handle(self):
        row = {"company": "Target", "handle": "target", "verification_status": "cached_resolved"}
        mismatch = {
            "result": {
                "data": {
                    "user": {
                        "result": {
                            "rest_id": "T",
                            "core": {"screen_name": "other", "name": "Other"},
                            "legacy": {},
                        }
                    }
                }
            }
        }
        result = resolve_old_target(row, mismatch, "cache.json")
        self.assertFalse(result["technical_identity_confirmed"])
        self.assertEqual(result["target_rest_id"], "")

    def test_company_is_deduped_across_old_and_new_cohorts(self):
        new = normalize_new_target(
            {
                "company": "Acme AI",
                "aliases": ["Acme"],
                "cohort": "new_v4",
                "requested_handle": "@acme",
                "technical_x_identity_status": "confirmed_exact_requested_handle",
                "technical_identity_confirmed": True,
                "resolved_rest_id": "T",
                "resolved_handle": "Acme",
                "cache_file": "new.json",
            }
        )
        old = resolve_old_target(
            {"company": "Acme AI", "handle": "acme"},
            {"rest_id": "T", "screen_name": "acme"},
            "old.json",
        )
        merged = dedupe_targets([new, old])
        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0]["cohorts"]), {"new_v4", "existing_v3"})
        self.assertTrue(merged[0]["technical_identity_confirmed"])

    def test_corrected_official_handle_is_kept_after_strict_rapid_confirmation(self):
        target = normalize_new_target({
            "company": "PixVerse",
            "cohort": "new_v4",
            "requested_handle": "@PixVerse_",
            "official_handle": "@PixVerse",
            "resolved_handle": "PixVerse",
            "resolved_rest_id": "1716359572896858112",
            "technical_x_identity_status": "confirmed_corrected_official_handle_exact_rapid_profile",
            "technical_identity_confirmed": True,
            "cache_file": "old-request.json",
            "identity_resolution_evidence": ["correction.json"],
        })
        self.assertTrue(target["technical_identity_confirmed"])
        self.assertEqual(target["target_handle"], "PixVerse")
        self.assertIn("correction.json", target["identity_evidence_files"])

    def test_corrected_handle_mismatch_is_rejected(self):
        target = normalize_new_target({
            "company": "Demo",
            "requested_handle": "@old",
            "official_handle": "@official",
            "resolved_handle": "different",
            "resolved_rest_id": "T",
            "technical_x_identity_status": "confirmed_corrected_official_handle_exact_rapid_profile",
            "technical_identity_confirmed": True,
        })
        self.assertFalse(target["technical_identity_confirmed"])
        self.assertEqual(target["technical_x_identity_status"], "unresolved_inconsistent_exact_identity_fields")


if __name__ == "__main__":
    unittest.main()
