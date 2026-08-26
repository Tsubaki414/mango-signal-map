import unittest

from scripts.collect_mango_seed_network import normalize_profile, seed_accounts


class MangoSeedNetworkTests(unittest.TestCase):
    def test_seed_filter_excludes_companies_and_creators(self):
        entities = [
            {"id": "mango", "name": "Mango", "handles": {"x": "MangoLabs_"}},
            {"id": "person_amy", "name": "Amy", "handles": {"x": "amy"}},
            {"id": "seed_demo", "name": "Demo", "handles": {"x": "@demo"}},
            {"id": "co_target", "name": "Target", "handles": {"x": "target"}},
            {"id": "creator_one", "name": "Creator", "handles": {"x": "creator"}},
        ]
        self.assertEqual(
            [item["handle"] for item in seed_accounts(entities)],
            ["MangoLabs_", "amy", "demo"],
        )

    def test_flat_profile_requires_exact_handle(self):
        seed = {"entity_id": "seed_demo", "name": "Demo", "handle": "demo"}
        matched = normalize_profile({"id_str": "1", "screen_name": "Demo"}, seed)
        mismatched = normalize_profile({"id_str": "2", "screen_name": "other"}, seed)
        self.assertEqual(matched["identity_status"], "exact_handle_match")
        self.assertEqual(mismatched["identity_status"], "handle_mismatch_requires_review")


if __name__ == "__main__":
    unittest.main()
