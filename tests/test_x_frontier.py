import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import expand_solomon_frontier as frontier


class SolomonFrontierTests(unittest.TestCase):
    def test_normalize_user_accepts_flat_rapid_x_schema(self):
        row = frontier.normalize_user({
            "id_str": "42",
            "screen_name": "flat_user",
            "name": "Flat User",
            "description": "AI growth founder",
            "followers_count": 900,
            "friends_count": 80,
            "verified": True,
        })
        self.assertEqual(row["rest_id"], "42")
        self.assertEqual(row["handle"], "flat_user")
        self.assertEqual(row["description"], "AI growth founder")
        self.assertEqual(row["following_count"], 80)
        self.assertTrue(row["verified"])

    def test_partial_follower_cache_does_not_assert_non_mutual(self):
        rows = frontier.merge_first_degree_membership(
            {"1", "2"},
            {"2", "3"},
            outbound_complete=True,
            inbound_complete=False,
        )
        by_id = {row["rest_id"]: row for row in rows}
        self.assertTrue(by_id["1"]["solomon_follows"])
        self.assertIsNone(by_id["1"]["follows_solomon"])
        self.assertIsNone(by_id["1"]["mutual_follow"])
        self.assertEqual(by_id["1"]["relationship_degree"], "direct")
        self.assertTrue(by_id["2"]["mutual_follow"])
        self.assertFalse(by_id["3"]["solomon_follows"])
        self.assertFalse(by_id["3"]["mutual_follow"])

        coverage = frontier.build_first_degree_coverage(
            {"1", "2"},
            {"2", "3"},
            outbound_complete=True,
            inbound_complete=False,
            outbound_pages=2,
            inbound_pages=1,
        )
        self.assertEqual(coverage["union"]["observed_count"], 3)
        self.assertEqual(coverage["intersection"]["observed_count"], 1)
        self.assertFalse(coverage["mutual_certainty"]["all_mutuals_enumerated"])
        self.assertFalse(coverage["mutual_certainty"]["can_assert_non_mutual_for_outbound_only"])
        self.assertTrue(coverage["mutual_certainty"]["can_assert_non_mutual_for_inbound_only"])
        self.assertIn("Follower coverage is partial", coverage["mutual_certainty"]["notes"][0])

    def test_complete_directional_caches_allow_one_way_assertion(self):
        rows = frontier.merge_first_degree_membership(
            {"1", "2"},
            {"2", "3"},
            outbound_complete=True,
            inbound_complete=True,
        )
        by_id = {row["rest_id"]: row for row in rows}
        self.assertFalse(by_id["1"]["follows_solomon"])
        self.assertFalse(by_id["1"]["mutual_follow"])
        self.assertEqual(by_id["1"]["relationship_certainty"], "confirmed_one_way")

    def test_score_and_tie_break_ignore_follower_count(self):
        high_audience = frontier.score_profile({
            "rest_id": "2",
            "handle": "zeta",
            "name": "AI Growth",
            "description": "AI marketing founder",
            "followers_count": 9_000_000,
        })
        low_audience = frontier.score_profile({
            "rest_id": "1",
            "handle": "alpha",
            "name": "AI Growth",
            "description": "AI marketing founder",
            "followers_count": 10,
        })
        self.assertEqual(high_audience["connector_discovery_score"], low_audience["connector_discovery_score"])
        ranked = frontier.sort_scored_profiles([high_audience, low_audience])
        self.assertEqual([row["handle"] for row in ranked], ["alpha", "zeta"])

    def test_relationship_signal_improves_priority_without_changing_semantic_score(self):
        mutual = frontier.score_profile({
            "rest_id": "1",
            "handle": "mutual",
            "name": "AI Growth",
            "description": "AI marketing founder",
            "mutual_follow": True,
        })
        one_way = frontier.score_profile({
            "rest_id": "2",
            "handle": "oneway",
            "name": "AI Growth",
            "description": "AI marketing founder",
            "solomon_follows": True,
            "follows_solomon": False,
            "mutual_follow": False,
        })
        self.assertEqual(mutual["connector_discovery_score"], one_way["connector_discovery_score"])
        self.assertGreater(mutual["connector_priority_score"], one_way["connector_priority_score"])
        self.assertNotIn("followers_count", mutual)

    def test_follower_collection_is_page_capped_and_direction_labeled(self):
        class FakeClient:
            def __init__(self):
                self.calls = 0

            def get_follower_ids(self, handle, **kwargs):
                self.calls += 1
                return {"ids": ["7", "8"], "next_cursor_str": "more"}

        client = FakeClient()
        with tempfile.TemporaryDirectory() as directory, patch.object(frontier, "CACHE", Path(directory)):
            result = frontier.collect_followers(client, "connector", force=False, max_pages=1)
        self.assertEqual(client.calls, 1)
        self.assertEqual(result["kind"], "followers")
        self.assertEqual(result["pages"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["coverage_label"], "page_capped_partial")


if __name__ == "__main__":
    unittest.main()
