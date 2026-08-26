import json
import unittest
from pathlib import Path

from mangobd.discovery import classify_sponsor_disclosure, cluster_campaigns, score_candidate


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


if __name__ == "__main__":
    unittest.main()
