import json
import importlib.util
import unittest
from pathlib import Path

from mangobd.discovery import classify_sponsor_disclosure, cluster_campaigns, score_candidate

_BUILD_SPEC = importlib.util.spec_from_file_location(
    "root_build_v4_candidates", Path(__file__).resolve().parents[1] / "scripts" / "build_v4_candidates.py"
)
assert _BUILD_SPEC and _BUILD_SPEC.loader
_BUILD_MODULE = importlib.util.module_from_spec(_BUILD_SPEC)
_BUILD_SPEC.loader.exec_module(_BUILD_MODULE)
infer_spend_level = _BUILD_MODULE.infer_spend_level


class DiscoveryTests(unittest.TestCase):
    def test_affiliate_is_not_promoted_to_paid_sponsorship(self):
        self.assertEqual(classify_sponsor_disclosure("This description contains an affiliate link"), "affiliate_or_referral")
        self.assertEqual(classify_sponsor_disclosure("Sponsored by Acme #ad"), "confirmed_paid_sponsorship")

    def test_repeat_paid_mentions_form_campaign_signal(self):
        records = [
            {"project_id":"p","creator_id":"a","content_date":"2026-01-01","disclosure":"Sponsored by P"},
            {"project_id":"p","creator_id":"a","content_date":"2026-01-20","disclosure":"#ad"},
        ]
        cluster = cluster_campaigns(records)[0]
        self.assertEqual(cluster["confirmed_paid_count"], 2)
        self.assertEqual(cluster["campaign_signal"], "multi_creator_or_repeat_paid")

    def test_famous_cold_target_can_lose_to_midmarket_warm_target(self):
        config = json.loads(Path("config/discovery_v4.json").read_text(encoding="utf-8"))
        common = dict(timing_score=80, fit_score=85, buyer_clarity_score=70, evidence_quality_score=85)
        famous = score_candidate({
            "company":"Famous","reachability_level":"unresolved","spend_mechanism_level":"active_creator_or_partner_budget",
            "penalties":["oversaturated_famous_cold_target"], **common,
        }, config)
        mid = score_candidate({
            "company":"Mid","reachability_level":"x_secondary_operator","spend_mechanism_level":"formal_paid_program",
            "penalties":[], **common,
        }, config)
        self.assertGreater(mid["overall_priority"], famous["overall_priority"])

    def test_negative_cash_language_never_becomes_active_cash_spend(self):
        fixtures = {
            "Talus Network": {
                "live_metric": "Launched an accelerator with technical and go-to-market support.",
                "budget_interpretation": "No public cash pool or per-team spend was identified.",
                "funding_signal": "$9M funding.",
            },
            "Allora Network": {
                "live_metric": "The agent accelerator supports storytelling and positioning.",
                "budget_interpretation": "No public cash campaign pool was found.",
                "funding_signal": "$35M funding.",
            },
            "MyShell": {
                "live_metric": "Creator monetization mechanics and thousands of agents.",
                "risk": "Verify the cash campaign budget before outreach.",
                "funding_signal": "$16.6M funding.",
            },
            "Sapien": {
                "live_metric": "Contributor network with completed tasks.",
                "budget_interpretation": "USDC payouts exist, but available campaign cash needs verification.",
                "funding_signal": "$10.5M funding.",
            },
        }
        inferred = {name: infer_spend_level(record) for name, record in fixtures.items()}
        self.assertEqual(inferred["Talus Network"], "formal_paid_program")
        self.assertEqual(inferred["Allora Network"], "formal_paid_program")
        self.assertEqual(inferred["MyShell"], "funding_or_token_value_only")
        self.assertEqual(inferred["Sapien"], "funding_or_token_value_only")
        self.assertNotIn("active_creator_or_partner_budget", inferred.values())


if __name__ == "__main__":
    unittest.main()
