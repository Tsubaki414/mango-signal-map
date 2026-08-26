import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_sponsor_intelligence_expanded_v4 import (
    build_products,
    write_outputs,
)


ROOT = Path(__file__).resolve().parents[1]


def observation(
    observation_id: str,
    creator: str,
    handle: str,
    brand: str,
    disclosure_type: str,
    published_at: str,
    *,
    confidence: float = 0.99,
) -> dict:
    return {
        "id": observation_id,
        "creator": creator,
        "creator_handle_or_channel": handle,
        "brand": brand,
        "platform": "youtube",
        "content_url": f"https://www.youtube.com/watch?v={observation_id:0<11}"[:43],
        "content_title": f"Evidence for {observation_id}",
        "published_at": published_at,
        "disclosure_type": disclosure_type,
        "evidence_text": f"Fixture disclosure: {disclosure_type}",
        "evidence_location": "YouTube description — fixture",
        "source_type": "first_party_creator_description",
        "extraction_method": "unit-test fixture",
        "extracted_at": "2026-08-24T17:48:59Z",
        "confidence": confidence,
        "business_contact_or_route": f"https://www.youtube.com/{handle}/about",
        "notes": "Synthetic test observation.",
    }


class SponsorIntelligenceExpandedTests(unittest.TestCase):
    def test_affiliate_mention_and_unverified_never_increment_paid(self):
        records = [
            observation("affiliate1", "Creator A", "@creator_a", "Brand X", "affiliate", "2026-01-01"),
            observation("mention001", "Creator A", "@creator_a", "Brand X", "mention", "2026-01-02"),
            observation("unknown001", "Creator A", "@creator_a", "Brand X", "unverified", "2026-01-03", confidence=0.4),
        ]
        products = build_products(records)
        edge = products["edges"][0]

        self.assertEqual(edge["paid_observation_count"], 0)
        self.assertEqual(edge["affiliate_observation_count"], 1)
        self.assertEqual(edge["mention_observation_count"], 1)
        self.assertEqual(edge["unverified_observation_count"], 1)
        self.assertFalse(edge["paid_signal"])
        self.assertFalse(edge["repeat_paid"])
        self.assertEqual(edge["commercial_status"], "affiliate_and_nonpaid_only")
        self.assertEqual(products["brand_signals"][0]["unique_paid_creators"], 0)
        self.assertEqual(products["creator_bridges"], [])
        self.assertTrue(
            products["manifest"]["invariants"][
                "affiliate_and_mentions_never_increment_paid"
            ]
        )

    def test_repeat_paid_edge_counts_only_paid_observations(self):
        records = [
            observation("paid0000001", "Creator A", "@creator_a", "Brand X", "paid_sponsorship", "2026-01-01"),
            observation("paid0000002", "Creator A", "@creator_a", "Brand X", "paid_sponsorship", "2026-01-20"),
            observation("aff00000001", "Creator A", "@creator_a", "Brand X", "affiliate", "2026-01-25"),
        ]
        products = build_products(records)
        edge = products["edges"][0]
        signal = products["brand_signals"][0]

        self.assertEqual(edge["observation_count"], 3)
        self.assertEqual(edge["paid_observation_count"], 2)
        self.assertEqual(edge["affiliate_observation_count"], 1)
        self.assertTrue(edge["repeat_paid"])
        self.assertEqual(edge["paid_dates"], ["2026-01-01", "2026-01-20"])
        self.assertEqual(signal["repeat_paid_creator_count"], 1)
        self.assertEqual(signal["activation_signal"], "repeat_paid_single_creator")

    def test_cross_brand_bridge_uses_paid_evidence_only(self):
        records = [
            observation("a_paid00001", "Creator A", "@creator_a", "Brand A", "paid_sponsorship", "2026-01-01"),
            observation("b_paid00001", "Creator A", "@creator_a", "Brand B", "paid_sponsorship", "2026-01-02"),
            observation("c_aff000001", "Creator A", "@creator_a", "Brand C", "affiliate", "2026-01-03"),
            observation("d_mention01", "Creator A", "@creator_a", "Brand D", "mention", "2026-01-04"),
            observation("a_aff000001", "Creator B", "@creator_b", "Brand A", "affiliate", "2026-01-05"),
            observation("b_aff000001", "Creator B", "@creator_b", "Brand B", "affiliate", "2026-01-06"),
        ]
        products = build_products(records)

        self.assertEqual(len(products["creator_bridges"]), 1)
        bridge = products["creator_bridges"][0]
        self.assertEqual(bridge["creator_handle_or_channel"], "@creator_a")
        self.assertEqual(bridge["paid_brand_count"], 2)
        self.assertEqual(bridge["paid_brands"], ["Brand A", "Brand B"])
        self.assertEqual(bridge["observation_ids"], ["a_paid00001", "b_paid00001"])
        self.assertNotIn("Brand C", bridge["paid_brands"])
        self.assertNotIn("Brand D", bridge["paid_brands"])

    def test_real_dataset_has_expected_evidence_preserving_signals(self):
        records = json.loads(
            (ROOT / "data" / "pilot_v4" / "sponsor_observations_expanded.json").read_text(
                encoding="utf-8"
            )
        )
        products = build_products(records)
        summary = products["summary"]

        self.assertEqual(summary["counts"]["atomic_observations"], 52)
        self.assertEqual(summary["counts"]["creator_brand_edges"], 50)
        self.assertEqual(summary["counts"]["creator_bridge_signals"], 5)
        self.assertEqual(
            summary["disclosure_distribution"],
            {
                "paid_sponsorship": 36,
                "affiliate": 7,
                "mention": 9,
                "unverified": 0,
            },
        )

        pika = next(edge for edge in products["edges"] if edge["brand"] == "Pika")
        self.assertEqual(pika["paid_observation_count"], 0)
        self.assertEqual(pika["mention_observation_count"], 1)

        nocode = next(
            edge
            for edge in products["edges"]
            if edge["brand"] == "Synthesia"
            and edge["creator_handle_or_channel"] == "@nocodemba"
        )
        self.assertTrue(nocode["repeat_paid"])
        self.assertEqual(nocode["paid_observation_count"], 2)

        bridge_handles = {
            bridge["creator_handle_or_channel"]
            for bridge in products["creator_bridges"]
        }
        self.assertIn("@dansmarttutorials", bridge_handles)
        self.assertNotIn("@cybernews", bridge_handles)

    def test_write_outputs_creates_all_json_and_csv_artifacts(self):
        products = build_products(
            [
                observation("paid0000001", "Creator A", "@creator_a", "Brand A", "paid_sponsorship", "2026-01-01"),
                observation("paid0000002", "Creator A", "@creator_a", "Brand B", "paid_sponsorship", "2026-01-02"),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_outputs(products, Path(tmp))
            self.assertEqual(len(paths), 12)
            self.assertTrue(all(path.exists() for path in paths))
            for path in paths:
                if path.suffix == ".json":
                    json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
