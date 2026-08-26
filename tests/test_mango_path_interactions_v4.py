import unittest

from scripts.collect_mango_path_interactions_v4 import adjacent_pairs, deduplicate_observations


class MangoPathInteractionTests(unittest.TestCase):
    def test_adjacent_pairs_deduplicate_shared_root_seed_edge(self):
        targets = [
            {"company": "A", "graph_reachable": True, "mango_secondary_paths": [{"path_labels": ["@Mango", "@Seed", "@A"]}]},
            {"company": "B", "graph_reachable": True, "mango_secondary_paths": [{"path_labels": ["@Mango", "@Seed", "@B"]}]},
        ]
        pairs = adjacent_pairs(targets)
        self.assertEqual(len(pairs), 3)
        root_pair = next(row for row in pairs if row["path_segment"] == "mango_root_to_seed")
        self.assertEqual(root_pair["companies"], ["A", "B"])

    def test_unreachable_target_does_not_create_query_pairs(self):
        targets = [{"company": "A", "graph_reachable": False, "mango_secondary_paths": [{"path_labels": ["@Mango", "@Seed", "@A"]}]}]
        self.assertEqual(adjacent_pairs(targets), [])

    def test_deduplication_merges_company_scope(self):
        rows = [
            {"tweet_id": "1", "source_handle": "a", "destination_handle": "b", "created_at": "x", "companies": ["A"], "raw_cache_files": ["a"]},
            {"tweet_id": "1", "source_handle": "a", "destination_handle": "b", "created_at": "x", "companies": ["B"], "raw_cache_files": ["b"]},
        ]
        result = deduplicate_observations(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["companies"], ["A", "B"])
        self.assertEqual(result[0]["raw_cache_files"], ["a", "b"])

    def test_same_tweet_can_retain_multiple_counterparty_edges(self):
        rows = [
            {"tweet_id": "1", "source_handle": "a", "destination_handle": "b", "created_at": "x", "companies": ["B"], "raw_cache_files": ["b"]},
            {"tweet_id": "1", "source_handle": "a", "destination_handle": "c", "created_at": "x", "companies": ["C"], "raw_cache_files": ["c"]},
        ]
        result = deduplicate_observations(rows)
        self.assertEqual(len(result), 2)
        self.assertEqual({row["destination_handle"] for row in result}, {"b", "c"})


if __name__ == "__main__":
    unittest.main()
