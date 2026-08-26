import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_business_knowledge_graph_v4.py"
SPEC = importlib.util.spec_from_file_location("build_business_knowledge_graph_v4", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class BusinessKnowledgeGraphV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.output_dir = Path(cls.tmp.name)
        cls.summary = MODULE.build(cls.output_dir)
        cls.payload = json.loads((cls.output_dir / "business_knowledge_graph_v4.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_required_input_coverage(self):
        counts = self.summary["input_coverage"]
        self.assertEqual(counts["companies"], 77)
        target_identities = json.loads(
            (ROOT / "outputs" / "pilot_v4" / "mango_seed_target_paths.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            counts["confirmed_target_x_identities"],
            sum(row.get("technical_identity_confirmed") is True for row in target_identities.get("targets") or []),
        )
        self.assertEqual(counts["sponsor_observations"], 52)
        self.assertEqual(counts["creator_brand_edges"], 50)
        self.assertEqual(counts["sponsor_brands"], 14)
        self.assertEqual(counts["sponsor_creators"], 41)
        operator_routes = json.loads(
            (ROOT / "data" / "pilot_v4" / "top_operator_routes.json").read_text(encoding="utf-8")
        )
        self.assertEqual(counts["operator_route_records"], len(operator_routes))
        strict_named = sum(
            (row.get("operator_identity") or {}).get("confirmed") is True
            for row in operator_routes
        )
        self.assertEqual(counts["operator_person_identity_edges"], strict_named)
        self.assertEqual(counts["gtm_cases"], 8)
        self.assertEqual(
            counts["solomon_v4_paths"]
            + counts["solomon_v3_paths"]
            + counts["mango_paths"]
            + counts["solomon_operator_person_paths"]
            + counts["mango_operator_person_paths"],
            len(self.payload["paths"]),
        )

    def test_every_edge_has_evidence_contract_and_valid_endpoints(self):
        node_ids = {node["node_id"] for node in self.payload["nodes"]}
        for edge in self.payload["edges"]:
            self.assertIn(edge["source"], node_ids)
            self.assertIn(edge["target"], node_ids)
            self.assertIn(edge["fact_status"], MODULE.FACT_STATUSES)
            self.assertIn(edge["confidence"], MODULE.CONFIDENCES)
            self.assertTrue(edge["last_verified_at"])
            self.assertTrue(edge["evidence"])
            self.assertTrue(edge["provenance"])
        for path in self.payload["paths"]:
            self.assertTrue(set(path["path_node_ids"]).issubset(node_ids))

    def test_relationship_concepts_are_separate(self):
        edge_types = self.summary["edges_by_type"]
        self.assertGreater(edge_types["FOLLOWS"], 0)
        self.assertGreater(sum(edge_types.get(kind, 0) for kind in ("MENTIONED", "REPLIED_TO", "QUOTED", "PUBLIC_INTERACTION")), 0)
        self.assertEqual(self.summary["introduction_willingness_edge_count"], 0)
        self.assertEqual(self.summary["human_intro_validated_path_count"], 0)
        self.assertTrue(all(path["human_intro_status"] == "human_intro_unvalidated" for path in self.payload["paths"]))

    def test_cross_scope_duplicate_interactions_are_merged_by_semantic_id(self):
        self.assertLessEqual(
            self.summary["input_coverage"]["x_interactions"],
            self.summary["input_coverage"]["x_interaction_observations"],
        )
        for edge in (edge for edge in self.payload["edges"] if edge["edge_category"] == "public_social_interaction"):
            self.assertTrue(edge["properties"]["collection_scopes"])

    def test_sponsor_classification_is_not_promoted(self):
        sponsor_edges = [edge for edge in self.payload["edges"] if edge["edge_category"] == "creator_brand_commercial"]
        self.assertEqual(len(sponsor_edges), 50)
        self.assertEqual(sum(edge["edge_type"] == "PAID_CREATOR_SPONSORSHIP" for edge in sponsor_edges), 34)
        self.assertEqual(sum(edge["edge_type"] == "AFFILIATE_REFERRAL" for edge in sponsor_edges), 7)
        self.assertEqual(sum(edge["edge_type"] == "MENTIONED_BRAND" for edge in sponsor_edges), 9)

    def test_no_unsupported_entities_are_invented(self):
        for kind in ("investor", "accelerator", "agency"):
            self.assertEqual(self.summary["unsupported_entity_instances"][kind], 0)

    def test_graphml_is_directed_and_matches_json_counts(self):
        graphml = self.output_dir / "business_knowledge_graph_v4.graphml"
        root = ET.parse(graphml).getroot()
        namespace = {"g": "http://graphml.graphdrawing.org/xmlns"}
        graph = root.find("g:graph", namespace)
        self.assertIsNotNone(graph)
        self.assertEqual(graph.attrib["edgedefault"], "directed")
        self.assertEqual(len(root.findall(".//g:node", namespace)), self.summary["node_count"])
        self.assertEqual(len(root.findall(".//g:edge", namespace)), self.summary["edge_count"])


if __name__ == "__main__":
    unittest.main()
