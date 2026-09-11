"""Regression tests for the operator evidence/write contract."""

from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bd_api import OperatorEdit, _apply_operator_edit
from backend.bd_compute import direct_channel_status
from backend.models import Company, Operator


class TestOperatorVerificationContract(unittest.TestCase):
    def test_source_match_flag_is_not_api_writable(self):
        with self.assertRaises(ValidationError):
            OperatorEdit(identity_confirmed=True)

    def test_generic_identity_edit_invalidates_source_match_without_human_verification(self):
        operator = Operator(
            name="Alice",
            role="Growth",
            x_handle="alice",
            identity_confirmed=True,
            identity_status="confirmed_official_role_and_rapid_x_exact_profile",
            evidence_urls="https://company.example/team",
        )
        _apply_operator_edit(operator, OperatorEdit(x_handle="alice_new"))
        self.assertFalse(operator.identity_confirmed)
        self.assertEqual(operator.identity_status, "human_edited_needs_source_rematch")
        self.assertIsNone(operator.identity_human_verified_at)
        self.assertIsNone(operator.role_human_verified_at)
        self.assertIsNone(operator.last_verified_at)

    def test_handle_change_clears_old_x_profile_and_dm_facts(self):
        operator = Operator(
            id=7,
            name="Alice",
            role="Growth",
            x_handle="alice_old",
            x_rest_id="123456",
            can_dm=True,
            can_dm_checked_at=dt.datetime(2026, 8, 1),
            identity_confirmed=True,
        )
        company = Company(company_id="company-1", name="Example")
        company.operators = [operator]

        _apply_operator_edit(operator, OperatorEdit(x_handle="alice_new"))

        self.assertEqual(operator.x_handle, "alice_new")
        self.assertIsNone(operator.x_rest_id)
        self.assertIsNone(operator.can_dm)
        self.assertIsNone(operator.can_dm_checked_at)
        self.assertIsNone(direct_channel_status(company))

    def test_human_identity_verification_requires_handle_and_evidence(self):
        operator = Operator(
            name="Alice",
            role="Growth",
            x_handle="alice",
            identity_confirmed=True,
            identity_status="confirmed_official_role_and_rapid_x_exact_profile",
        )
        with self.assertRaises(HTTPException) as raised:
            _apply_operator_edit(operator, OperatorEdit(identity_human_verified=True))
        self.assertEqual(raised.exception.status_code, 422)

    def test_human_identity_verification_requires_existing_exact_profile_match(self):
        operator = Operator(
            name="Alice",
            role="Growth",
            x_handle="alice_old",
            x_rest_id="123456",
            can_dm=True,
            identity_confirmed=True,
            identity_status="confirmed_official_role_and_rapid_x_exact_profile",
            evidence_urls="https://company.example/team",
        )

        with self.assertRaises(HTTPException) as raised:
            _apply_operator_edit(
                operator,
                OperatorEdit(x_handle="alice_new", identity_human_verified=True),
            )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(operator.x_handle, "alice_old")
        self.assertEqual(operator.x_rest_id, "123456")
        self.assertTrue(operator.can_dm)
        self.assertIsNone(operator.identity_human_verified_at)

    def test_human_identity_verification_accepts_unchanged_double_source_match(self):
        checked_at = dt.datetime(2026, 8, 28, 7, 45)
        operator = Operator(
            name="Alice",
            role="Growth",
            x_handle="alice",
            identity_confirmed=True,
            identity_status="confirmed_official_role_and_rapid_x_exact_profile",
            evidence_urls="https://company.example/team",
        )

        _apply_operator_edit(
            operator,
            OperatorEdit(identity_human_verified=True),
            verified_at=checked_at,
        )

        self.assertEqual(operator.identity_human_verified_at, checked_at)

    def test_replacing_source_evidence_invalidates_pipeline_match(self):
        operator = Operator(
            name="Alice",
            role="Growth",
            x_handle="alice",
            identity_confirmed=True,
            identity_status="confirmed_official_role_and_rapid_x_exact_profile",
            evidence_urls="https://company.example/team",
        )
        _apply_operator_edit(operator, OperatorEdit(evidence_urls="https://other.example/profile"))
        self.assertFalse(operator.identity_confirmed)
        self.assertEqual(operator.identity_status, "human_edited_needs_source_rematch")

    def test_identity_and_role_verification_are_independent(self):
        checked_at = dt.datetime(2026, 8, 28, 6, 30)
        operator = Operator(
            name="Alice",
            role="Growth",
            x_handle="alice",
            identity_confirmed=True,
            identity_status="confirmed_official_role_and_rapid_x_exact_profile",
            evidence_urls="https://company.example/team",
        )
        _apply_operator_edit(
            operator,
            OperatorEdit(
                identity_human_verified=True,
                role_human_verified=False,
            ),
            verified_at=checked_at,
        )
        self.assertEqual(operator.identity_human_verified_at, checked_at)
        self.assertIsNone(operator.role_human_verified_at)
        self.assertEqual(operator.last_verified_at, checked_at)

    def test_role_change_without_reconfirmation_clears_role_verification(self):
        old_check = dt.datetime(2026, 8, 1)
        operator = Operator(
            name="Alice",
            role="Growth",
            x_handle="alice",
            identity_confirmed=True,
            evidence_urls="https://company.example/team",
            role_human_verified_at=old_check,
            last_verified_at=old_check,
        )
        _apply_operator_edit(operator, OperatorEdit(role="Partnerships"))
        self.assertIsNone(operator.role_human_verified_at)
        self.assertIsNone(operator.last_verified_at)
        self.assertFalse(operator.identity_confirmed)

    def test_budget_confirmation_requires_evidence(self):
        operator = Operator(name="Alice", role="Growth", x_handle="alice")
        with self.assertRaises(HTTPException) as raised:
            _apply_operator_edit(operator, OperatorEdit(budget_authority_confirmed=True))
        self.assertEqual(raised.exception.status_code, 422)

    def test_role_or_evidence_change_invalidates_old_budget_confirmation(self):
        old_check = dt.datetime(2026, 8, 1)
        for edit in (
            OperatorEdit(role="Engineering"),
            OperatorEdit(evidence_urls=""),
        ):
            with self.subTest(edit=edit.model_dump(exclude_none=True)):
                operator = Operator(
                    name="Alice",
                    role="Growth",
                    x_handle="alice",
                    evidence_urls="https://company.example/team",
                    budget_authority_confirmed=True,
                    budget_authority_verified_at=old_check,
                    last_verified_at=old_check,
                )
                _apply_operator_edit(operator, edit)
                self.assertFalse(operator.budget_authority_confirmed)
                self.assertIsNone(operator.budget_authority_verified_at)
                self.assertIsNone(operator.last_verified_at)


if __name__ == "__main__":
    unittest.main()
