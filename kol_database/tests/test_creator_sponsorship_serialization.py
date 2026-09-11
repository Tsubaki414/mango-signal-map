"""Creator-page sponsorship wording must preserve evidence review status."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import Company, Creator, SponsorshipEvidence
from backend.serializers import creator_detail


def _creator_with(sponsorships: list[SponsorshipEvidence]) -> Creator:
    creator = Creator(display_name="Creator", creator_class="KOL")
    creator.social_accounts = []
    creator.contacts = []
    creator.rate_cards = []
    creator.campaigns = []
    creator.sponsorships = sponsorships
    return creator


class TestCreatorSponsorshipReviewStatus(unittest.TestCase):
    def test_unreviewed_paid_observation_stays_explicitly_unreviewed(self):
        company = Company(company_id="company:a", name="Company A")
        evidence = SponsorshipEvidence(
            company=company,
            company_id=company.company_id,
            creator_name_raw="Creator",
            platform="YouTube",
            content_url="https://example.com/video",
            disclosure_type="paid_sponsorship",
            review_status="unreviewed",
        )
        detail = creator_detail(_creator_with([evidence]))
        self.assertEqual(detail["sponsorship_history"][0]["review_status"], "unreviewed")
        self.assertEqual(detail["repeat_confirmed_paid_sponsor_companies"], [])

    def test_repeat_sponsor_claim_requires_two_confirmed_paid_rows(self):
        company = Company(company_id="company:a", name="Company A")
        rows = [
            SponsorshipEvidence(
                company=company,
                company_id=company.company_id,
                creator_name_raw="Creator",
                platform="YouTube",
                content_url=f"https://example.com/{index}",
                disclosure_type=disclosure,
                review_status=status,
            )
            for index, (disclosure, status) in enumerate(
                [
                    ("paid_sponsorship", "confirmed"),
                    ("paid_sponsorship", "confirmed"),
                    ("paid_sponsorship", "rejected"),
                    ("affiliate", "confirmed"),
                ]
            )
        ]
        detail = creator_detail(_creator_with(rows))
        self.assertEqual(detail["repeat_confirmed_paid_sponsor_companies"], ["Company A"])
        self.assertEqual(detail["repeat_sponsor_companies"], ["Company A"])

    def test_two_unreviewed_paid_rows_are_shown_as_repeat_observation_not_confirmed_repeat(self):
        company = Company(company_id="company:a", name="Company A")
        rows = [
            SponsorshipEvidence(
                company=company,
                company_id=company.company_id,
                creator_name_raw="Creator",
                platform="YouTube",
                content_url=f"https://example.com/unreviewed-{index}",
                disclosure_type="paid_sponsorship",
                review_status="unreviewed",
            )
            for index in range(2)
        ]
        detail = creator_detail(_creator_with(rows))
        self.assertEqual(detail["repeat_confirmed_paid_sponsor_companies"], [])
        self.assertEqual(
            detail["repeat_paid_observations"],
            [{
                "company": "Company A",
                "observation_count": 2,
                "confirmed_count": 0,
                "unreviewed_count": 2,
                "status": "review_required",
            }],
        )


if __name__ == "__main__":
    unittest.main()
