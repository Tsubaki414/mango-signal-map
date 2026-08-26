import json
import tempfile
import unittest
from pathlib import Path

from mangobd.x_network import edge_kind, enumerate_paths, enumerate_routable_paths, find_user_objects, is_routable_path, load_best_id_cache, load_legacy_id_pages, operational_x_score, path_strength, relationship_adjacency


class XNetworkTests(unittest.TestCase):
    def test_enumerates_secondary_and_third_degree_paths(self):
        graph = {
            "solomon": {"jennie", "doris"},
            "jennie": {"solomon", "target", "doris"},
            "doris": {"solomon", "jennie"},
        }
        paths = enumerate_paths(graph, "solomon", "target", max_hops=3)
        self.assertIn(["solomon", "jennie", "target"], paths)
        self.assertIn(["solomon", "doris", "jennie", "target"], paths)
        self.assertEqual(edge_kind(graph, "solomon", "jennie"), "mutual_follow")
        self.assertEqual(operational_x_score(graph, paths[0]), 52)
        self.assertEqual(path_strength(graph, paths[0]), 16.2)

    def test_degree_graph_includes_inbound_follow_without_losing_direction(self):
        directed = {"target": {"connector"}, "connector": {"solomon"}}
        paths = enumerate_paths(relationship_adjacency(directed), "solomon", "target", max_hops=2)
        self.assertEqual(paths, [["solomon", "connector", "target"]])
        self.assertEqual(edge_kind(directed, "solomon", "connector"), "followed_by")
        self.assertEqual(edge_kind(directed, "connector", "target"), "followed_by")
        self.assertTrue(is_routable_path(directed, paths[0]))

    def test_shared_followed_account_is_not_an_intro_route(self):
        directed = {"solomon": {"popular"}, "jennie": {"popular", "target"}}
        path = ["solomon", "popular", "jennie", "target"]
        self.assertFalse(is_routable_path(directed, path))
        self.assertEqual(enumerate_routable_paths(directed, "solomon", "target", max_hops=3), [])

    def test_id_page_loader_tracks_terminal_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "following-ids-demo.json").write_text(
                json.dumps({"ids": ["1", "2"], "next_cursor_str": "abc"}), encoding="utf-8"
            )
            (root / "following-ids-demo-p2.json").write_text(
                json.dumps({"ids": ["2", "3"], "next_cursor_str": "0"}), encoding="utf-8"
            )
            ids, complete, pages = load_legacy_id_pages(root, "demo")
            self.assertEqual(ids, {"1", "2", "3"})
            self.assertTrue(complete)
            self.assertEqual(pages, 2)

    def test_best_cache_prefers_complete_combined_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "following-ids-demo.json").write_text(
                json.dumps({"ids": ["1"], "next_cursor_str": "abc"}), encoding="utf-8"
            )
            (root / "v3").mkdir()
            (root / "v3" / "following-demo.json").write_text(
                json.dumps({"ids": ["1", "2"], "complete": True, "pages": 2}), encoding="utf-8"
            )
            ids, complete, pages = load_best_id_cache(root, "demo")
            self.assertEqual(ids, {"1", "2"})
            self.assertTrue(complete)
            self.assertEqual(pages, 2)

    def test_finds_and_deduplicates_nested_user_objects(self):
        user = {"rest_id":"1","legacy":{"screen_name":"demo"}}
        payload = {"a":[user,{"nested":user}]}
        self.assertEqual(find_user_objects(payload), [user])

    def test_finds_flat_get_users_v2_objects(self):
        payload = {
            "result": [
                {
                    "id": 10,
                    "id_str": "10",
                    "screen_name": "alpha",
                    "name": "Alpha",
                    "status": {"quoted_user": {"id_str": "99", "screen_name": "nested"}},
                },
                {"id": 11, "id_str": "11", "screen_name": "beta", "description": "AI founder"},
            ]
        }
        users = find_user_objects(payload)
        self.assertEqual({item["id_str"] for item in users}, {"10", "11"})

    def test_search_tweet_wrapper_does_not_hide_nested_author_user(self):
        author = {
            "__typename": "User",
            "rest_id": "42",
            "core": {"screen_name": "official_ai", "name": "Official AI"},
            "legacy": {"description": "Official account"},
        }
        payload = {
            "tweet_results": {
                "result": {
                    "__typename": "Tweet",
                    "rest_id": "2090000000000000000",
                    "legacy": {"full_text": "Official AI update"},
                    "core": {"user_results": {"result": author}},
                }
            }
        }
        self.assertEqual(find_user_objects(payload), [author])


if __name__ == "__main__":
    unittest.main()
