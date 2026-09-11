from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bd_compute import (
    channel_access,
    deduplicate_bridge_stages,
    interaction_audit_summary,
    intro_readiness,
    operator_relevance,
    relationship_evidence,
    sponsor_side_intelligence_prospects,
    sponsorship_evidence_semantics,
)
from backend.models import (
    Company,
    ContactMethod,
    Creator,
    InteractionCheck,
    InteractionEvidence,
    IntroBridge,
    Operator,
    OutreachLog,
    RateCard,
    SponsorshipEvidence,
)


def company() -> Company:
    item = Company(company_id="company:test", name="Test", spend_evidence_level="L1")
    item.intro_paths = []
    item.operators = []
    item.action_items = []
    item.outreach_logs = []
    item.sponsorships = []
    return item


class TestFourIndependentDimensions(unittest.TestCase):
    def test_open_dm_is_cold_channel_not_relationship_or_intro(self):
        item = company()
        item.operators = [Operator(id=1, name="Person", role="Founder", x_handle="person", can_dm=True)]
        self.assertEqual(channel_access(item)["state"], "x_dm_appears_open")
        self.assertTrue(channel_access(item)["cold"])
        self.assertEqual(relationship_evidence(item)["state"], "none_found")
        self.assertEqual(intro_readiness(item)["label"], "Unverified")

    def test_mutual_follow_is_deduplicated_and_only_needs_internal_check(self):
        item = company()
        operator = Operator(id=1, name="Target", role="Head of Growth")
        operator.bridges = [
            IntroBridge(id=10, bridge_handle="SamePerson", mango_side_handle="MangoLabs_"),
            IntroBridge(id=11, bridge_handle="sameperson", mango_side_handle="Solomon_Nahhh"),
        ]
        item.operators = [operator]
        rows = deduplicate_bridge_stages(operator.bridges, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mango_side_handles"], ["MangoLabs_", "Solomon_Nahhh"])
        self.assertEqual(rows[0]["interaction_verification_status"], "unchecked")
        self.assertEqual(relationship_evidence(item)["state"], "mutual_follow_candidate")
        self.assertEqual(intro_readiness(item)["label"], "Mango needs to ask internally")

    def test_public_interaction_does_not_make_intro_ready(self):
        item = company()
        bridge = IntroBridge(id=10, bridge_handle="person", mango_side_handle="Solomon_Nahhh")
        bridge.interaction_checks = [
            InteractionCheck(
                from_handle="person",
                to_handle="target",
                query_string="from:person to:target",
                checked_at=dt.datetime(2026, 8, 1),
                coverage_note="single latest search",
                evidence=[
                    InteractionEvidence(
                        tweet_id="1",
                        tweet_url="https://x.com/person/status/1",
                        posted_at=dt.datetime(2026, 7, 1),
                        author_handle="person",
                        recipient_handle="target",
                        interaction_type="reply",
                    )
                ],
            )
        ]
        operator = Operator(id=1, name="Target", role="Head of Growth")
        operator.bridges = [bridge]
        item.operators = [operator]
        self.assertEqual(relationship_evidence(item)["state"], "public_interaction_found")
        self.assertEqual(intro_readiness(item)["label"], "Mango needs to ask internally")

    def test_only_human_logs_advance_intro_readiness(self):
        item = company()
        bridge = IntroBridge(id=10, bridge_handle="person", mango_side_handle="Solomon_Nahhh")
        operator = Operator(id=1, name="Target", role="Head of Growth")
        operator.bridges = [bridge]
        item.operators = [operator]
        item.outreach_logs = [
            OutreachLog(bridge_id=10, stage="relationship_confirmed", occurred_at=dt.datetime(2026, 8, 1)),
            OutreachLog(bridge_id=10, stage="intro_accepted", occurred_at=dt.datetime(2026, 8, 2)),
            OutreachLog(bridge_id=10, stage="intro_made", occurred_at=dt.datetime(2026, 8, 3)),
            OutreachLog(bridge_id=10, stage="target_replied", occurred_at=dt.datetime(2026, 8, 4)),
            OutreachLog(bridge_id=10, stage="meeting_booked", occurred_at=dt.datetime(2026, 8, 5)),
        ]
        self.assertEqual(intro_readiness(item)["label"], "Meeting booked")

    def test_founder_title_alone_is_not_budget_owner(self):
        self.assertEqual(operator_relevance(Operator(name="F", role="Founder"))["label"], "Influencer")
        self.assertEqual(
            operator_relevance(Operator(name="G", role="Head of Creator Partnerships"))["label"],
            "Likely budget owner",
        )
        self.assertEqual(
            operator_relevance(
                Operator(
                    name="G",
                    role="Head of Creator Partnerships",
                    budget_authority_confirmed=True,
                    budget_authority_verified_at=dt.datetime(2026, 8, 1),
                )
            )["label"],
            "Confirmed decision-maker",
        )


class TestInteractionAuditDenominator(unittest.TestCase):
    def test_counts_unique_company_person_pairs_not_raw_root_rows(self):
        item = company()
        operator = Operator(id=1, name="Target")
        checked = IntroBridge(id=10, bridge_handle="same", mango_side_handle="MangoLabs_")
        checked.interaction_checks = [
            InteractionCheck(
                from_handle="same",
                to_handle="target",
                query_string="q",
                checked_at=dt.datetime(2026, 8, 1),
                coverage_note="scope",
                evidence=[],
            )
        ]
        operator.bridges = [checked, IntroBridge(id=11, bridge_handle="SAME", mango_side_handle="Solomon_Nahhh")]
        item.operators = [operator]
        audit = interaction_audit_summary([item])
        self.assertEqual(audit["raw_bridge_rows"], 2)
        self.assertEqual(audit["unique_company_person_pairs"], 1)
        self.assertEqual(audit["checked"], 1)
        self.assertEqual(audit["directional_query_count"], 1)
        self.assertEqual(audit["no_public_interaction_found"], 1)
        self.assertEqual(audit["unchecked"], 0)


class TestSponsorshipSemantics(unittest.TestCase):
    def test_legacy_mention_is_organic_and_never_paid(self):
        evidence = SponsorshipEvidence(
            disclosure_type="mention",
            review_status="unreviewed",
            confidence=0.8,
            published_at="2026-01-01",
            created_at=dt.datetime(2026, 2, 1),
        )
        row = sponsorship_evidence_semantics(evidence)
        self.assertEqual(row["evidence_type"], "organic_mention")
        self.assertEqual(row["commercial_status"], "non_paid_or_unknown")
        self.assertIsNone(row["verified_at"])

    def test_paid_observation_stays_unreviewed_until_human_review(self):
        evidence = SponsorshipEvidence(disclosure_type="paid_sponsorship", review_status="unreviewed")
        self.assertEqual(
            sponsorship_evidence_semantics(evidence)["commercial_status"],
            "unreviewed_paid_observation",
        )

    def test_sponsor_intelligence_separates_creator_and_media(self):
        companies = [Company(company_id=f"company:{index}", name=f"C{index}") for index in (1, 2)]
        creators = []
        for creator_id, creator_class in ((1, "KOL"), (2, "Media / Community Account")):
            creator = Creator(id=creator_id, display_name=f"Person {creator_id}", creator_class=creator_class)
            creator.contacts = [ContactMethod(method_type="email", value="hello@example.com")]
            creator.rate_cards = [RateCard(deliverable="Video", quote_amount_usd=1000, is_confident=True)]
            creator.sponsorships = [
                SponsorshipEvidence(
                    company=company_item,
                    company_id=company_item.company_id,
                    disclosure_type="paid_sponsorship",
                    review_status="unreviewed",
                    content_url=f"https://example.com/{creator_id}/{company_item.company_id}",
                    creator_name_raw=creator.display_name,
                    platform="YouTube",
                )
                for company_item in companies
            ]
            creators.append(creator)
        people, media = sponsor_side_intelligence_prospects(creators)
        self.assertEqual([row["creator_name"] for row in people], ["Person 1"])
        self.assertEqual([row["creator_name"] for row in media], ["Person 2"])
        self.assertEqual(people[0]["intro_readiness"], "unverified")
        self.assertIn("不请求引荐", people[0]["exact_first_ask"])
        mango = people[0]["mango_relationship"]
        self.assertTrue(mango["numeric_quote_available"])
        self.assertNotIn("confirmed_quote_available", mango)
        self.assertNotIn("quote_available", mango)


if __name__ == "__main__":
    unittest.main()
