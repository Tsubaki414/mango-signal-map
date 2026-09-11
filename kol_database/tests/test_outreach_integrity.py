"""Regression tests for outreach-event provenance and E4-E6 attribution."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bd_api import (
    OutreachLogCreate,
    OutreachLogEdit,
    _apply_outreach_log_edit,
    _validate_outreach_context,
    router,
)
from backend.models import ActionItem, Base, Company, IntroBridge, IntroPath, Operator, OutreachLog


class TestOutreachContextIntegrity(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

        self.company_a = Company(company_id="company:a", name="Company A")
        self.company_b = Company(company_id="company:b", name="Company B")
        self.session.add_all([self.company_a, self.company_b])
        self.session.flush()

        self.operator_a = Operator(company_id=self.company_a.company_id, name="Alice")
        self.operator_a_alt = Operator(company_id=self.company_a.company_id, name="Avery")
        self.operator_b = Operator(company_id=self.company_b.company_id, name="Bob")
        self.session.add_all([self.operator_a, self.operator_a_alt, self.operator_b])
        self.session.flush()

        self.bridge_a = IntroBridge(
            company_id=self.company_a.company_id,
            operator_id=self.operator_a.id,
            bridge_handle="alice_connector",
            mango_side_handle="Solomon_Nahhh",
        )
        self.path_a = IntroPath(company_id=self.company_a.company_id, target_type="operator_person", root="solomon")
        self.path_b = IntroPath(company_id=self.company_b.company_id, target_type="operator_person", root="solomon")
        self.action_a = ActionItem(company_id=self.company_a.company_id, primary_next_action="Ask Alice")
        self.action_b = ActionItem(company_id=self.company_b.company_id, primary_next_action="Ask Bob")
        self.session.add_all([self.bridge_a, self.path_a, self.path_b, self.action_a, self.action_b])
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def assert_invalid(self, body: OutreachLogCreate, message_fragment: str):
        with self.assertRaises(HTTPException) as raised:
            _validate_outreach_context(self.session, self.company_a.company_id, body)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn(message_fragment, str(raised.exception.detail))

    def body(self, stage: str, **kwargs) -> OutreachLogCreate:
        return OutreachLogCreate(stage=stage, owner="Fiona", contact_channel="dm", **kwargs)

    def test_rejects_foreign_keys_from_another_company(self):
        cases = [
            (self.body("contact_attempted", operator_id=self.operator_b.id), "operator_id"),
            (self.body("contact_attempted", operator_id=self.operator_a.id, intro_path_id=self.path_b.id), "intro_path_id"),
            (self.body("contact_attempted", operator_id=self.operator_a.id, linked_action_item_id=self.action_b.id), "linked_action_item_id"),
        ]
        for body, field in cases:
            with self.subTest(field=field):
                self.assert_invalid(body, field)

    def test_bridge_infers_its_target_operator(self):
        context = _validate_outreach_context(
            self.session,
            self.company_a.company_id,
            self.body("connector_replied", bridge_id=self.bridge_a.id),
        )
        self.assertEqual(context["bridge_id"], self.bridge_a.id)
        self.assertEqual(context["operator_id"], self.operator_a.id)

    def test_rejects_bridge_operator_mismatch(self):
        self.assert_invalid(
            self.body(
                "connector_replied",
                bridge_id=self.bridge_a.id,
                operator_id=self.operator_a_alt.id,
            ),
            "不一致",
        )

    def test_connector_and_intro_stages_require_a_bridge(self):
        for stage in ("connector_replied", "relationship_rejected", "intro_accepted", "intro_made"):
            with self.subTest(stage=stage):
                self.assert_invalid(
                    self.body(stage, operator_id=self.operator_a.id),
                    "bridge_id",
                )

    def test_bridge_target_reply_requires_prior_intro_for_same_bridge(self):
        reply = self.body("target_replied", bridge_id=self.bridge_a.id)
        self.assert_invalid(reply, "intro_made")

        self.session.add(
            OutreachLog(
                company_id=self.company_a.company_id,
                operator_id=self.operator_a.id,
                bridge_id=self.bridge_a.id,
                stage="intro_made",
                voided=False,
            )
        )
        self.session.commit()
        context = _validate_outreach_context(self.session, self.company_a.company_id, reply)
        self.assertEqual(context["bridge_id"], self.bridge_a.id)

    def test_direct_target_reply_is_valid_but_has_no_bridge(self):
        context = _validate_outreach_context(
            self.session,
            self.company_a.company_id,
            self.body("target_replied", operator_id=self.operator_a.id),
        )
        self.assertEqual(context["operator_id"], self.operator_a.id)
        self.assertIsNone(context["bridge_id"])

    def test_rejects_unowned_or_channel_less_execution_records(self):
        self.assert_invalid(
            OutreachLogCreate(stage="contact_attempted", operator_id=self.operator_a.id, contact_channel="dm"),
            "Mango 执行人",
        )
        self.assert_invalid(
            OutreachLogCreate(stage="contact_attempted", operator_id=self.operator_a.id, owner="Fiona"),
            "联系渠道",
        )

    def test_company_level_event_requires_named_contact_target(self):
        self.assert_invalid(self.body("contact_attempted"), "实际联系")
        context = _validate_outreach_context(
            self.session,
            self.company_a.company_id,
            self.body("contact_attempted", contacted_who="@company_a"),
        )
        self.assertIsNone(context["operator_id"])
        self.assertIsNone(context["bridge_id"])


class TestOutreachLogBoundedEdit(unittest.TestCase):
    def test_operational_fields_can_change_without_rewriting_provenance(self):
        log = OutreachLog(
            id=9,
            company_id="company:a",
            operator_id=3,
            bridge_id=4,
            intro_path_id=5,
            linked_action_item_id=6,
            owner="Fiona",
            contacted_who="@target",
            contact_channel="dm",
            evidence_url="https://example.com/evidence",
            stage="contact_attempted",
            notes="old",
            next_follow_up_date="2026-08-30",
            voided=False,
        )
        immutable = {
            "company_id": log.company_id,
            "operator_id": log.operator_id,
            "bridge_id": log.bridge_id,
            "intro_path_id": log.intro_path_id,
            "linked_action_item_id": log.linked_action_item_id,
            "contacted_who": log.contacted_who,
            "contact_channel": log.contact_channel,
            "evidence_url": log.evidence_url,
            "stage": log.stage,
        }
        _apply_outreach_log_edit(
            log,
            OutreachLogEdit(
                owner=" Solomon ",
                notes="corrected operational note",
                next_follow_up_date="2026-09-01",
            ),
        )
        self.assertEqual(log.owner, "Solomon")
        self.assertEqual(log.notes, "corrected operational note")
        self.assertEqual(log.next_follow_up_date, "2026-09-01")
        self.assertEqual({field: getattr(log, field) for field in immutable}, immutable)

    def test_explicit_null_clears_optional_fields_but_omission_preserves(self):
        log = OutreachLog(
            company_id="company:a",
            stage="contact_attempted",
            owner="Fiona",
            notes="old",
            next_follow_up_date="2026-08-30",
            voided=False,
        )
        _apply_outreach_log_edit(log, OutreachLogEdit(notes=None, next_follow_up_date=None))
        self.assertIsNone(log.notes)
        self.assertIsNone(log.next_follow_up_date)
        self.assertEqual(log.owner, "Fiona")

    def test_voided_rows_invalid_dates_and_empty_owner_are_rejected(self):
        live = OutreachLog(company_id="company:a", stage="contact_attempted", owner="Fiona", voided=False)
        for body, fragment in (
            (OutreachLogEdit(next_follow_up_date="tomorrow"), "YYYY-MM-DD"),
            (OutreachLogEdit(owner="  "), "owner"),
            (OutreachLogEdit(), "至少提供"),
        ):
            with self.subTest(fragment=fragment), self.assertRaises(HTTPException) as raised:
                _apply_outreach_log_edit(live, body)
            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn(fragment, str(raised.exception.detail))

        voided = OutreachLog(company_id="company:a", stage="contact_attempted", owner="Fiona", voided=True)
        with self.assertRaises(HTTPException) as raised:
            _apply_outreach_log_edit(voided, OutreachLogEdit(notes="should fail"))
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("已作废", str(raised.exception.detail))

    def test_stage_and_evidence_attribution_are_not_patch_fields(self):
        for payload in ({"stage": "meeting_booked"}, {"evidence_url": "https://example.com/new"}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                OutreachLogEdit(**payload)

    def test_api_has_patch_and_void_but_no_hard_delete(self):
        methods_by_path = {route.path: route.methods for route in router.routes}
        self.assertIn("PATCH", methods_by_path["/api/outreach-logs/{log_id}"])
        self.assertIn("PATCH", methods_by_path["/api/outreach-logs/{log_id}/void"])
        self.assertNotIn("DELETE", methods_by_path["/api/outreach-logs/{log_id}"])


if __name__ == "__main__":
    unittest.main()
