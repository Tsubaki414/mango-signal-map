import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bd_compute import (
    best_intro_path,
    creator_campaign_fit,
    opportunity_priority,
    reachability_summary,
    relationship_strength,
)
from backend.models import Company, Creator, IntroPath, Operator, RateCard, SponsorshipEvidence


def _path(target_type="operator_person", degree_label="direct", reachable=True, primary=True, hop_count=1):
    return IntroPath(
        target_type=target_type,
        root="mango",
        is_primary=primary,
        degree_label=degree_label,
        hop_count=hop_count,
        graph_reachable=reachable,
        human_intro_status="human_intro_unvalidated",
    )


def _company(**overrides):
    defaults = dict(company_id="company:test", name="Test Co", spend_evidence_level="L1", priority_tier="C")
    defaults.update(overrides)
    c = Company(**defaults)
    c.intro_paths = []
    c.operators = []
    c.action_items = []
    c.sponsorships = []
    return c


class TestBestIntroPath(unittest.TestCase):
    def test_prefers_reachable_over_unreachable(self):
        unreachable = _path(reachable=False)
        reachable = _path(reachable=True)
        self.assertIs(best_intro_path([unreachable, reachable]), reachable)

    def test_prefers_direct_over_secondary(self):
        secondary = _path(degree_label="secondary")
        direct = _path(degree_label="direct")
        self.assertIs(best_intro_path([secondary, direct]), direct)

    def test_filters_by_target_type(self):
        person = _path(target_type="operator_person")
        company_acct = _path(target_type="company_account")
        self.assertIs(best_intro_path([person, company_acct], target_type="company_account"), company_acct)

    def test_empty_list_returns_none(self):
        self.assertIsNone(best_intro_path([]))


class TestReachability(unittest.TestCase):
    def test_no_paths_is_level_zero(self):
        c = _company()
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 0)

    def test_confirmed_operator_with_reachable_person_path_is_level_three(self):
        c = _company()
        c.intro_paths = [_path(target_type="operator_person", reachable=True)]
        c.operators = [Operator(name="Jane", identity_confirmed=True)]
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 3)
        self.assertIsNotNone(summary["confirmed_operator"])

    def test_reachable_company_path_without_confirmed_operator_is_level_two(self):
        c = _company()
        c.intro_paths = [_path(target_type="company_account", reachable=True)]
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 2)

    def test_unreachable_path_is_level_one(self):
        c = _company()
        c.intro_paths = [_path(target_type="company_account", reachable=False)]
        summary = reachability_summary(c)
        self.assertEqual(summary["level"], 1)


class TestOpportunityPriority(unittest.TestCase):
    def test_high_spend_no_reach_is_watchlist_not_high(self):
        """The spec's explicit non-negotiable: a high-budget-but-unreachable
        company must never rank as top priority -- it lands in Watchlist."""
        c = _company(spend_evidence_level="L3", priority_tier="A")
        result = opportunity_priority(c)
        self.assertEqual(result["label"], "Watchlist")

    def test_low_spend_no_reach_is_low_not_watchlist(self):
        c = _company(spend_evidence_level="L1", priority_tier="D")
        result = opportunity_priority(c)
        self.assertEqual(result["label"], "Low")

    def test_reachable_operator_with_spend_is_high(self):
        c = _company(spend_evidence_level="L2")
        c.intro_paths = [_path(target_type="operator_person", reachable=True)]
        c.operators = [Operator(name="Jane", identity_confirmed=True)]
        result = opportunity_priority(c)
        self.assertEqual(result["label"], "High")

    def test_reasons_always_present(self):
        c = _company()
        result = opportunity_priority(c)
        self.assertTrue(len(result["reasons"]) > 0)


class TestCreatorCampaignFit(unittest.TestCase):
    def test_flags_missing_quote(self):
        creator = Creator(display_name="Test", categories=None)
        creator.rate_cards = []
        creator.sponsorships = []
        fit = creator_campaign_fit(creator)
        self.assertFalse(fit["has_quote"])
        self.assertIn("no Mango quote yet -- would need outreach", fit["reasons"])

    def test_flags_confirmed_quote(self):
        creator = Creator(display_name="Test", categories=None)
        creator.rate_cards = [RateCard(deliverable="Post", quote_amount_usd=500, is_confident=True)]
        creator.sponsorships = []
        fit = creator_campaign_fit(creator)
        self.assertTrue(fit["has_quote"])

    def test_flags_prior_paid_sponsorship_of_same_company(self):
        company = _company()
        creator = Creator(display_name="Test", categories=None)
        creator.rate_cards = []
        creator.sponsorships = [
            SponsorshipEvidence(company_id=company.company_id, platform="YouTube", disclosure_type="paid_sponsorship", confidence=0.9)
        ]
        fit = creator_campaign_fit(creator, company)
        self.assertTrue(any("previously paid-sponsored" in r for r in fit["reasons"]))

    def test_does_not_promote_mention_to_paid(self):
        company = _company()
        creator = Creator(display_name="Test", categories=None)
        creator.rate_cards = []
        creator.sponsorships = [
            SponsorshipEvidence(company_id=company.company_id, platform="YouTube", disclosure_type="mention", confidence=0.5)
        ]
        fit = creator_campaign_fit(creator, company)
        self.assertFalse(any("paid-sponsored" in r for r in fit["reasons"]))
        self.assertTrue(any("mentioned/affiliated" in r for r in fit["reasons"]))


class TestRelationshipStrength(unittest.TestCase):
    def test_none_path_is_cold_contact(self):
        result = relationship_strength(None)
        self.assertEqual(result["label"], "Cold contact only")
        self.assertEqual(result["level"], 0)

    def test_unreachable_path_is_weak(self):
        result = relationship_strength(_path(reachable=False))
        self.assertEqual(result["label"], "Weak / unconfirmed")

    def test_direct_reachable_path_is_strongest(self):
        result = relationship_strength(_path(degree_label="direct", reachable=True))
        self.assertEqual(result["label"], "Direct relationship")


if __name__ == "__main__":
    unittest.main()
