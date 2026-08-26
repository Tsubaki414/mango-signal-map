import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts import build_v4_database as database


class V4DatabaseTests(unittest.TestCase):
    def test_company_id_is_stable_and_rejects_empty(self):
        self.assertEqual(database.company_id("v0 / Vercel"), "company:v0vercel")
        with self.assertRaises(ValueError):
            database.company_id("---")

    def test_only_explicit_brand_aliases_resolve(self):
        ids = {"viggleai": "company:viggleai"}
        self.assertEqual(database.resolve_company_key("Viggle", ids), "viggleai")
        with self.assertRaises(ValueError):
            database.resolve_company_key("Viggle Labs", ids)

    def test_schema_rejects_invalid_sponsor_disclosure(self):
        connection = sqlite3.connect(":memory:")
        database.create_schema(connection)
        connection.execute(
            "INSERT INTO companies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("company:demo", "Demo", "cohort", "group", "", "", 1, "method", "group", 1, "watch", "L1", "unknown", "", "", "", "", "{}"),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO sponsor_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("bad", "company:demo", "Creator", "@c", "youtube", "https://example.com/bad", "", "2026-01-01", "probably_paid", "", "", 0.5, "", "{}"),
            )
        connection.close()

    def test_graphml_loader_preserves_edge_direction(self):
        content = """<?xml version='1.0' encoding='utf-8'?>
<graphml xmlns='http://graphml.graphdrawing.org/xmlns'>
  <graph id='g' edgedefault='directed'>
    <node id='x_1'><data key='n_label'>One</data><data key='n_handle'>one</data></node>
    <node id='x_2'><data key='n_label'>Two</data><data key='n_handle'>two</data></node>
    <edge id='e1' source='x_1' target='x_2'><data key='e_relation'>follows</data><data key='e_coverage'>complete</data></edge>
  </graph>
</graphml>"""
        connection = sqlite3.connect(":memory:")
        database.create_schema(connection)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.graphml"
            path.write_text(content, encoding="utf-8")
            counts = database.load_graphml(connection, path, batch_size=5000)
        self.assertEqual(counts, (2, 1))
        edge = connection.execute("SELECT source_node_id,target_node_id,relation,cache_coverage FROM x_edges").fetchone()
        self.assertEqual(edge, ("x_1", "x_2", "follows", "complete"))
        connection.close()

    def test_interactions_keep_same_tweet_for_different_counterparties_and_scopes(self):
        connection = sqlite3.connect(":memory:")
        database.create_schema(connection)
        connection.execute(
            "INSERT INTO companies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("company:one", "One", "cohort", "group", "", "", 1, "method", "group", 1, "watch", "L1", "unknown", "", "", "", "", "{}"),
        )
        artifacts = {
            "x_path_interactions_v4.json": {
                "observations": [{
                    "tweet_id": "123", "interaction_id": "alice:bob:123",
                    "source_handle": "Alice", "destination_handle": "Bob",
                    "interaction_type": "mention", "tweet_url": "https://x.com/Alice/status/123",
                    "companies": ["One"], "raw_cache_file": "one.json",
                }]
            },
            "mango_path_interactions_v4.json": {
                "observations": [{
                    "tweet_id": "123", "interaction_id": "alice:carol:123",
                    "source_handle": "Alice", "destination_handle": "Carol",
                    "interaction_type": "mention", "tweet_url": "https://x.com/Alice/status/123",
                    "companies": ["One"], "raw_cache_files": ["two.json"],
                }]
            },
        }

        def fake_load(path, default=None):
            return artifacts[path.name]

        with patch.object(database, "load_json", side_effect=fake_load):
            database.load_interactions(connection, {"one": "company:one"})

        rows = connection.execute(
            "SELECT interaction_id,tweet_id,collection_scopes_json,raw_cache_files_json "
            "FROM x_interactions ORDER BY interaction_id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual({row[1] for row in rows}, {"123"})
        self.assertEqual(
            {tuple(json.loads(row[2])) for row in rows},
            {("solomon_primary_paths",), ("mango_seed_paths",)},
        )
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM x_interaction_companies").fetchone()[0], 2)
        connection.close()

    def test_same_semantic_interaction_merges_collection_scopes(self):
        connection = sqlite3.connect(":memory:")
        database.create_schema(connection)
        connection.execute(
            "INSERT INTO companies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("company:one", "One", "cohort", "group", "", "", 1, "method", "group", 1, "watch", "L1", "unknown", "", "", "", "", "{}"),
        )
        row = {
            "tweet_id": "123", "interaction_id": "alice:bob:123",
            "source_handle": "Alice", "destination_handle": "Bob", "interaction_type": "mention",
            "tweet_url": "https://x.com/Alice/status/123", "companies": ["One"],
        }
        artifacts = {
            "x_path_interactions_v4.json": {"observations": [{**row, "raw_cache_file": "one.json"}]},
            "mango_path_interactions_v4.json": {"observations": [{**row, "raw_cache_files": ["two.json"]}]},
        }
        with patch.object(database, "load_json", side_effect=lambda path, default=None: artifacts[path.name]):
            database.load_interactions(connection, {"one": "company:one"})
        stored = connection.execute(
            "SELECT collection_scopes_json,raw_cache_files_json,raw_json FROM x_interactions"
        ).fetchone()
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM x_interactions").fetchone()[0], 1)
        self.assertEqual(set(json.loads(stored[0])), {"solomon_primary_paths", "mango_seed_paths"})
        self.assertEqual(set(json.loads(stored[1])), {"one.json", "two.json"})
        self.assertEqual(len(json.loads(stored[2])["observations"]), 2)
        connection.close()


if __name__ == "__main__":
    unittest.main()
