"""Focused contracts for the Top-5 sales packet release."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bd_api import _price_campaign_tier, _public_internal_person_copy, _select_campaign_tier
from backend.campaign_matching import match_creator_to_campaign, quote_scope_fit


ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "data" / "pilot_v7" / "top5_sales_packets_v7.json").read_text(encoding="utf-8"))
APP_JS = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")


def candidate(creator_id: int, role: str, quote: float) -> dict:
    return {"id": creator_id, "campaign_role": role, "quote": {"representative": quote}}


class TestSalesPacketDataContract(unittest.TestCase):
    def test_five_packets_have_complete_buyer_maps_and_no_fake_official_claims(self):
        packets = DATA["packets"]
        buyers = DATA["buyer_map_entries"]
        self.assertEqual(len(packets), 5)
        self.assertEqual(len({row["company_id"] for row in packets}), 5)
        self.assertEqual(len(buyers), 25)
        for packet in packets:
            company_buyers = [row for row in buyers if row["company_id"] == packet["company_id"]]
            self.assertEqual(len(company_buyers), 5)
            self.assertTrue(any(row["buyer_type"] == "economic_buyer" for row in company_buyers))
            self.assertGreaterEqual(len(packet["qualification_questions"]), 5)
            self.assertGreaterEqual(len(packet["switch_rules"]), 3)
        for buyer in buyers:
            if buyer["verification_status"].startswith("official"):
                self.assertTrue(buyer["source_url"])

    def test_customer_email_uses_a_team_signature_not_a_personal_name(self):
        for packet in DATA["packets"]:
            self.assertNotIn("Solomon", packet["email_body_en"])
            self.assertTrue(packet["email_body_en"].endswith("Mango Labs BD Team"))

    def test_public_relationship_copy_uses_a_neutral_internal_root(self):
        raw = "Solomon (@Solomon_Nahhh) -> Jennie (@jennie) -> Runway"
        rendered = _public_internal_person_copy(raw)
        self.assertEqual(rendered, "Mango 内部联系人 -> Jennie (@jennie) -> Runway")
        self.assertNotIn("Solomon", rendered)


class TestQuoteGroundedPricing(unittest.TestCase):
    def setUp(self):
        self.rows = [
            candidate(1, "Strategic KOL", 1000),
            candidate(2, "Technical educator", 700),
            candidate(3, "KOC", 200),
            candidate(4, "Strategic KOL", 400),
            candidate(5, "Media/community channel", 300),
        ]

    def test_tiers_use_different_real_rows_and_never_invent_a_service_fee(self):
        lean = _select_campaign_tier(self.rows, 3, lean=True)
        recommended = _select_campaign_tier(self.rows, 4)
        premium = _select_campaign_tier(self.rows, 5)
        self.assertEqual(len(lean), 3)
        self.assertEqual(len(recommended), 4)
        self.assertEqual(len(premium), 5)
        priced = _price_campaign_tier(
            "recommended", "Recommended", recommended,
            service_fee_pct=None, contingency_pct=None, localization_fee_usd=None,
        )
        self.assertIsNone(priced["mango_service_fee_usd"])
        self.assertFalse(priced["commercial_assumptions_complete"])
        self.assertEqual(priced["total_client_budget_usd"], priced["creator_media_cost_usd"])

    def test_user_supplied_commercial_assumptions_are_formula_based(self):
        priced = _price_campaign_tier(
            "lean", "Lean", self.rows[:2],
            service_fee_pct=20, contingency_pct=10, localization_fee_usd=500,
        )
        self.assertEqual(priced["creator_media_cost_usd"], 1700)
        self.assertEqual(priced["mango_service_fee_usd"], 340)
        self.assertEqual(priced["contingency_usd"], 170)
        self.assertEqual(priced["total_client_budget_usd"], 2710)

    def test_runway_requires_filmmaking_evidence_and_does_not_price_an_x_post_as_production(self):
        company = SimpleNamespace(company_id="company:runway")
        generic = SimpleNamespace(
            creator_class="KOL", categories="AI,Tech", region=None, language="English",
            social_accounts=[SimpleNamespace(platform="X", bio="AI tools and tech news", content_summary=None, recent_content=[])],
            rate_cards=[],
        )
        filmmaker = SimpleNamespace(
            creator_class="KOL", categories="AI,Film", region=None, language="English",
            social_accounts=[SimpleNamespace(platform="Instagram", bio="AI filmmaker and cinematic storyteller", content_summary=None, recent_content=[])],
            rate_cards=[],
        )
        self.assertTrue(match_creator_to_campaign(generic, company)["generic_only"])
        fit = match_creator_to_campaign(filmmaker, company)
        self.assertEqual(fit["best_capability"]["key"], "ai_filmmaker")
        x_post = SimpleNamespace(deliverable="X single post")
        self.assertFalse(quote_scope_fit(x_post, fit["best_capability"])["price_included"])
        generic_reel = SimpleNamespace(deliverable="IG Reel (30-60s)")
        self.assertFalse(quote_scope_fit(generic_reel, fit["best_capability"])["price_included"])
        film_production = SimpleNamespace(deliverable="AI short film production")
        self.assertTrue(quote_scope_fit(film_production, fit["best_capability"])["price_included"])

    def test_program_total_excludes_specialists_that_need_a_separate_quote(self):
        rows = [
            {"id": 1, "quote": {"representative": 1000}, "price_included_in_tier": True},
            {"id": 2, "quote": {"representative": 50}, "price_included_in_tier": False},
        ]
        priced = _price_campaign_tier(
            "recommended", "Recommended", rows,
            service_fee_pct=None, contingency_pct=None, localization_fee_usd=None,
            unpriced_scope=["creative direction"],
        )
        self.assertEqual(priced["creator_media_cost_usd"], 1000)
        self.assertEqual(priced["priced_creator_count"], 1)
        self.assertFalse(priced["program_budget_complete"])

    def test_frontend_exposes_buyer_truth_pricing_boundary_and_copy_actions(self):
        for phrase in (
            "买方图谱、预算状态与发送文案",
            "未计价范围和“需重新询价”人选不在小计中",
            "待逐一询价",
            "复制 Email",
            "复制 X DM",
            "保存推荐组合为内部候选名单",
        ):
            self.assertIn(phrase, APP_JS)


if __name__ == "__main__":
    unittest.main()
