"""API-level integrity regressions for campaigns, shortlists and actions."""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

app_module = importlib.import_module("backend.app")
bd_api_module = importlib.import_module("backend.bd_api")
from backend.models import ActionItem, Base, Company, Creator, RateCard, Shortlist, ShortlistItem


class TestCampaignApiIntegrity(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        session = self.Session()
        try:
            company = Company(company_id="company:acme", name="Acme")
            creator_a = Creator(display_name="Creator A", creator_class="KOL")
            creator_b = Creator(display_name="Creator B", creator_class="KOL")
            creator_media = Creator(display_name="Media Channel", creator_class="Media / Community Account")
            creator_unknown = Creator(display_name="Unknown Account", creator_class="Unknown")
            card_a = RateCard(creator=creator_a, deliverable="Video A", quote_amount_usd=1000)
            card_b = RateCard(creator=creator_b, deliverable="Video B", quote_amount_usd=2000)
            card_media = RateCard(creator=creator_media, deliverable="Newsletter", quote_amount_usd=300)
            card_unknown = RateCard(creator=creator_unknown, deliverable="Unknown placement", quote_amount_usd=75)
            shortlist = Shortlist(name="Existing")
            empty_shortlist = Shortlist(name="Empty")
            session.add_all([
                company,
                creator_a,
                creator_b,
                creator_media,
                creator_unknown,
                card_a,
                card_b,
                card_media,
                card_unknown,
                shortlist,
                empty_shortlist,
            ])
            session.flush()
            item = ShortlistItem(
                shortlist_id=shortlist.id,
                creator_id=creator_a.id,
                rate_card_id=card_a.id,
                quote_usd_override=1200,
            )
            action = ActionItem(
                company_id=company.company_id,
                owner="Fiona",
                primary_next_action="Verify relationship",
                fallback="Use official form",
                status="open",
            )
            session.add_all([item, action])
            session.commit()
            self.creator_a_id = creator_a.id
            self.creator_b_id = creator_b.id
            self.card_a_id = card_a.id
            self.card_b_id = card_b.id
            self.creator_media_id = creator_media.id
            self.creator_unknown_id = creator_unknown.id
            self.card_media_id = card_media.id
            self.card_unknown_id = card_unknown.id
            self.shortlist_id = shortlist.id
            self.empty_shortlist_id = empty_shortlist.id
            self.item_id = item.id
            self.action_id = action.id
        finally:
            session.close()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def request(self, method: str, path: str, payload: dict):
        with (
            patch.dict(os.environ, {"INTERNAL_ACCESS_TOKEN": "test-token"}),
            patch.object(app_module, "get_session", side_effect=self.Session),
            patch.object(bd_api_module, "get_session", side_effect=self.Session),
        ):
            return self.client.request(
                method,
                path,
                json=payload,
                headers={"Authorization": "Bearer test-token"},
            )

    def test_shortlist_requires_trimmed_nonblank_name_and_nonnegative_budget(self):
        blank = self.request("POST", "/api/shortlists", {"name": "   "})
        negative = self.request("POST", "/api/shortlists", {"name": "Valid", "budget_usd": -1})
        self.assertEqual(blank.status_code, 422)
        self.assertEqual(negative.status_code, 422)

        valid = self.request(
            "POST",
            "/api/shortlists",
            {"name": "  Pilot list  ", "objective": "   ", "budget_usd": 0},
        )
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertEqual(valid.json()["name"], "Pilot list")
        self.assertIsNone(valid.json()["objective"])

    def test_company_campaign_requires_objective_and_never_stores_blank_name(self):
        for payload in (
            {},
            {"objective": "   "},
            {"objective": "Launch", "budget_usd": -0.01},
        ):
            with self.subTest(payload=payload):
                response = self.request(
                    "POST",
                    "/api/companies/company:acme/campaigns",
                    payload,
                )
                self.assertEqual(response.status_code, 422)

        valid = self.request(
            "POST",
            "/api/companies/company:acme/campaigns",
            {"name": "   ", "objective": "  Launch in Asia  ", "budget_usd": 0},
        )
        self.assertEqual(valid.status_code, 200, valid.text)
        self.assertEqual(valid.json()["name"], "Acme campaign")
        session = self.Session()
        try:
            campaign = session.get(Shortlist, valid.json()["id"])
            self.assertEqual(campaign.objective, "Launch in Asia")
            self.assertEqual(campaign.budget_usd, 0)
        finally:
            session.close()

    def test_add_rejects_rate_card_owned_by_another_creator(self):
        response = self.request(
            "POST",
            f"/api/shortlists/{self.empty_shortlist_id}/items",
            {"creator_id": self.creator_a_id, "rate_card_id": self.card_b_id},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("rate_card_id", response.json()["detail"])
        session = self.Session()
        try:
            self.assertEqual(
                session.query(ShortlistItem)
                .filter(ShortlistItem.shortlist_id == self.empty_shortlist_id)
                .count(),
                0,
            )
        finally:
            session.close()

    def test_edit_rejects_cross_creator_card_and_preserves_existing_quote_state(self):
        response = self.request(
            "PATCH",
            f"/api/shortlists/{self.shortlist_id}/items/{self.item_id}",
            {"rate_card_id": self.card_b_id, "quote_usd_override": None},
        )
        self.assertEqual(response.status_code, 422)
        session = self.Session()
        try:
            item = session.get(ShortlistItem, self.item_id)
            self.assertEqual(item.rate_card_id, self.card_a_id)
            self.assertEqual(item.quote_usd_override, 1200)
        finally:
            session.close()

    def test_matching_rate_card_is_accepted_and_explicit_null_override_still_clears(self):
        response = self.request(
            "PATCH",
            f"/api/shortlists/{self.shortlist_id}/items/{self.item_id}",
            {"rate_card_id": self.card_a_id, "quote_usd_override": None},
        )
        self.assertEqual(response.status_code, 200, response.text)
        item = next(row for row in response.json()["items"] if row["item_id"] == self.item_id)
        self.assertEqual(item["rate_card_id"], self.card_a_id)
        self.assertFalse(item["quote_is_override"])
        self.assertEqual(item["quote_usd"], 1000)

    def test_budget_keeps_media_and_needs_review_out_of_strategic_kol_spend(self):
        for creator_id, card_id in (
            (self.creator_media_id, self.card_media_id),
            (self.creator_unknown_id, self.card_unknown_id),
        ):
            response = self.request(
                "POST",
                f"/api/shortlists/{self.shortlist_id}/items",
                {"creator_id": creator_id, "rate_card_id": card_id},
            )
            self.assertEqual(response.status_code, 200, response.text)

        detail = self.request("GET", f"/api/shortlists/{self.shortlist_id}", {})
        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()
        self.assertEqual(payload["strategic_spend_usd"], 1200)
        self.assertEqual(payload["distribution_spend_usd"], 0)
        self.assertEqual(payload["media_spend_usd"], 300)
        self.assertEqual(payload["needs_review_spend_usd"], 75)
        self.assertEqual(payload["total_spend_usd"], 1575)
        self.assertEqual(payload["kol_spend_usd"], 1200)
        self.assertEqual(payload["koc_spend_usd"], 0)

    def test_action_create_requires_owner_fallback_and_valid_execution_fields(self):
        for payload in (
            {"primary_next_action": "Do it", "owner": "Fiona", "fallback": "Plan B"},
            {"primary_next_action": "Do it", "owner": "Fiona", "fallback": "Plan B", "due_date": "   "},
            {"primary_next_action": "Do it", "due_date": "2026-09-02"},
            {"primary_next_action": "Do it", "owner": " ", "fallback": "Plan B", "due_date": "2026-09-02"},
            {
                "primary_next_action": "Do it",
                "owner": "Fiona",
                "fallback": "Plan B",
                "due_date": "2026-09-02",
                "status": "later",
            },
            {
                "primary_next_action": "Do it",
                "owner": "Fiona",
                "fallback": "Plan B",
                "due_date": "tomorrow",
            },
        ):
            with self.subTest(payload=payload):
                response = self.request(
                    "POST",
                    "/api/companies/company:acme/action-items",
                    payload,
                )
                self.assertEqual(response.status_code, 422)

        valid = self.request(
            "POST",
            "/api/companies/company:acme/action-items",
            {
                "primary_next_action": "  Do it  ",
                "owner": "  Fiona  ",
                "fallback": "  Plan B  ",
                "due_date": "2026-09-02",
            },
        )
        self.assertEqual(valid.status_code, 200, valid.text)
        created = max(valid.json()["action_items"], key=lambda row: row["id"])
        self.assertEqual(created["owner"], "Fiona")
        self.assertEqual(created["primary_next_action"], "Do it")
        self.assertEqual(created["fallback"], "Plan B")
        self.assertEqual(created["due_date"], "2026-09-02")

    def test_action_edit_cannot_clear_invariants_and_accepts_iso_due_date(self):
        for payload in ({"owner": None}, {"fallback": "  "}, {"status": "later"}, {"due_date": "2026/09/01"}):
            with self.subTest(payload=payload):
                response = self.request(
                    "PATCH",
                    f"/api/action-items/{self.action_id}",
                    payload,
                )
                self.assertEqual(response.status_code, 422)

        valid = self.request(
            "PATCH",
            f"/api/action-items/{self.action_id}",
            {"owner": "  Solomon  ", "due_date": "2026-09-01", "status": "in_progress"},
        )
        self.assertEqual(valid.status_code, 200, valid.text)
        session = self.Session()
        try:
            action = session.get(ActionItem, self.action_id)
            self.assertEqual(action.owner, "Solomon")
            self.assertEqual(action.due_date, "2026-09-01")
            self.assertEqual(action.status, "in_progress")
            self.assertEqual(action.fallback, "Use official form")
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
