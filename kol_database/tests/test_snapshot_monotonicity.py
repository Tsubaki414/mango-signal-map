"""Recurring baked snapshots must never roll back live/human evidence."""

from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import Base, Company, InteractionCheck, InteractionEvidence, IntroBridge, Operator
from scripts.apply_bridge_interaction_snapshot import apply_bridge_interaction_row
from scripts.apply_gap_operators import apply_gap_operator_rows
from scripts.apply_interaction_evidence_snapshot import apply_interaction_snapshot
from scripts.apply_intro_bridges_snapshot import (
    SNAPSHOT_PATH as INTRO_BRIDGES_SNAPSHOT_PATH,
    apply_operator_dm_snapshot,
    insert_missing_bridges,
)
from scripts.apply_operator_x_handles_snapshot import (
    COMPANY_NAME_TO_ID,
    SNAPSHOT_PATH as OPERATOR_HANDLES_SNAPSHOT_PATH,
    apply_operator_x_handle_rows,
)


class TestMonotonicSnapshots(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_older_operator_dm_snapshot_cannot_overwrite_newer_destination(self):
        operator = Operator(
            company_id="company:a",
            name="Target",
            can_dm=False,
            can_dm_checked_at=dt.datetime(2026, 9, 1),
        )
        changed = apply_operator_dm_snapshot(
            operator,
            {"can_dm": True, "can_dm_checked_at": "2026-08-27T10:00:00"},
        )
        self.assertFalse(changed)
        self.assertFalse(operator.can_dm)
        self.assertEqual(operator.can_dm_checked_at, dt.datetime(2026, 9, 1))

    def test_older_flat_interaction_snapshot_cannot_overwrite_newer_destination(self):
        bridge = IntroBridge(
            company_id="company:a",
            operator_id=1,
            bridge_handle="person",
            mango_side_handle="Solomon_Nahhh",
            interaction_checked_at=dt.datetime(2026, 9, 1),
            interaction_count_recent=7,
            interaction_sample_text="newer production evidence",
        )
        changed = apply_bridge_interaction_row(
            bridge,
            {
                "interaction_checked_at": "2026-08-28T01:00:00",
                "interaction_count_recent": 1,
                "most_recent_interaction_at": "2024-01-01T00:00:00",
                "interaction_sample_text": "older baked evidence",
            },
        )
        self.assertFalse(changed)
        self.assertEqual(bridge.interaction_count_recent, 7)
        self.assertEqual(bridge.interaction_sample_text, "newer production evidence")

    def test_detailed_snapshot_inserts_only_missing_direction_and_never_deletes_newer_check(self):
        session = self.Session()
        try:
            company = Company(company_id="company:a", name="A")
            operator = Operator(company_id=company.company_id, name="Target", x_handle="target")
            bridge = IntroBridge(
                company_id=company.company_id,
                operator=operator,
                bridge_handle="connector",
                mango_side_handle="Solomon_Nahhh",
            )
            newer = InteractionCheck(
                bridge=bridge,
                from_handle="connector",
                to_handle="target",
                query_string="from:connector to:target",
                checked_at=dt.datetime(2026, 9, 1),
                coverage_note="newer human/production scope",
            )
            newer.evidence.append(
                InteractionEvidence(
                    tweet_id="newer",
                    tweet_url="https://x.com/connector/status/newer",
                    author_handle="connector",
                    recipient_handle="target",
                    interaction_type="reply",
                    is_auditable=True,
                )
            )
            session.add_all([company, operator, bridge, newer])
            session.commit()

            snapshot = {
                "checks": [
                    {
                        "company_id": "company:a",
                        "target_handle": "target",
                        "bridge_handle": "connector",
                        "from_handle": "connector",
                        "to_handle": "target",
                        "query_string": "from:connector to:target",
                        "coverage_note": "older baked scope",
                        "evidence": [],
                    },
                    {
                        "company_id": "company:a",
                        "target_handle": "target",
                        "bridge_handle": "connector",
                        "from_handle": "target",
                        "to_handle": "connector",
                        "query_string": "from:target to:connector",
                        "coverage_note": "older baked reverse-direction scope",
                        "evidence": [],
                    },
                ]
            }
            checked = {("company:a", "target", "connector"): dt.datetime(2026, 8, 28, 1)}
            self.assertEqual(apply_interaction_snapshot(session, snapshot, checked), (1, 1, 0))
            session.commit()
            session.expire_all()

            checks = session.query(InteractionCheck).order_by(InteractionCheck.from_handle).all()
            self.assertEqual(len(checks), 2)
            preserved = next(row for row in checks if row.from_handle == "connector")
            inserted = next(row for row in checks if row.from_handle == "target")
            self.assertEqual(preserved.checked_at, dt.datetime(2026, 9, 1))
            self.assertEqual(preserved.coverage_note, "newer human/production scope")
            self.assertEqual([e.tweet_id for e in preserved.evidence], ["newer"])
            self.assertEqual(inserted.checked_at, dt.datetime(2026, 8, 28, 1))

            self.assertEqual(apply_interaction_snapshot(session, snapshot, checked), (0, 0, 0))
            session.commit()
            self.assertEqual(session.query(InteractionCheck).count(), 2)
        finally:
            session.close()

    def test_dual_mango_roots_are_distinct_atomic_bridge_rows(self):
        session = self.Session()
        try:
            company = Company(company_id="company:gaib", name="GAIB")
            operator = Operator(company_id=company.company_id, name="Target", x_handle="target")
            session.add_all([company, operator])
            session.flush()
            session.add(
                IntroBridge(
                    company_id=company.company_id,
                    operator_id=operator.id,
                    bridge_handle="SamePerson",
                    mango_side_handle="Solomon_Nahhh",
                )
            )
            session.commit()
            rows = [
                {
                    "company_id": company.company_id,
                    "operator_id": 99,
                    "bridge_handle": "sameperson",
                    "bridge_name": "Same Person",
                    "bridge_bio": None,
                    "bridge_followers_count": 10,
                    "mango_side_handle": "Solomon_Nahhh",
                    "mutual_with_mango_side": True,
                    "mutual_with_target": True,
                    "confidence_note": "root one",
                },
                {
                    "company_id": company.company_id,
                    "operator_id": 99,
                    "bridge_handle": "sameperson",
                    "bridge_name": "Same Person",
                    "bridge_bio": None,
                    "bridge_followers_count": 10,
                    "mango_side_handle": "MangoLabs_",
                    "mutual_with_mango_side": True,
                    "mutual_with_target": True,
                    "confidence_note": "root two",
                },
            ]
            self.assertEqual(insert_missing_bridges(session, rows, {99: operator}), 1)
            session.commit()
            roots = {
                row.mango_side_handle
                for row in session.query(IntroBridge).filter(IntroBridge.operator_id == operator.id)
            }
            self.assertEqual(roots, {"Solomon_Nahhh", "MangoLabs_"})
            self.assertEqual(insert_missing_bridges(session, rows, {99: operator}), 0)
        finally:
            session.close()

    def test_gap_candidates_never_modify_company_with_newer_human_operator(self):
        session = self.Session()
        try:
            company = Company(company_id="company:gaib", name="GAIB")
            human = Operator(company_id=company.company_id, name="Human-selected operator", identity_status="human")
            session.add_all([company, human])
            session.commit()
            rows = [
                {
                    "company_name": "GAIB",
                    "name": "Baked candidate",
                    "role": "Founder",
                    "x_handle": "baked",
                    "source_url": "https://example.com",
                }
            ]
            self.assertEqual(apply_gap_operator_rows(session, rows), 0)
            session.commit()
            self.assertEqual([row.name for row in session.query(Operator).all()], ["Human-selected operator"])
        finally:
            session.close()

    def test_operator_handle_snapshot_fills_blank_once_without_touching_evidence_flags(self):
        session = self.Session()
        try:
            company = Company(company_id="company:perplexity", name="Perplexity")
            operator = Operator(
                company_id=company.company_id,
                name="  RAMAN   Malik ",
                role="VP Product and Growth",
                x_handle=None,
                identity_confirmed=False,
                identity_status="candidate_needs_x_verification",
                identity_human_verified_at=dt.datetime(2026, 8, 1),
                role_human_verified_at=dt.datetime(2026, 8, 2),
                budget_authority_confirmed=True,
                budget_authority_verified_at=dt.datetime(2026, 8, 3),
                evidence_urls="https://company.example/team",
            )
            session.add_all([company, operator])
            session.commit()
            rows = [
                {
                    "company": "Perplexity",
                    "name": "Raman Malik",
                    "x_handle": "@ramanrmalik",
                }
            ]

            first = apply_operator_x_handle_rows(session, rows)
            session.commit()
            self.assertEqual(first["updated"], 1)
            self.assertEqual(operator.x_handle, "ramanrmalik")
            self.assertFalse(operator.identity_confirmed)
            self.assertEqual(operator.identity_status, "candidate_needs_x_verification")
            self.assertEqual(operator.identity_human_verified_at, dt.datetime(2026, 8, 1))
            self.assertEqual(operator.role_human_verified_at, dt.datetime(2026, 8, 2))
            self.assertTrue(operator.budget_authority_confirmed)
            self.assertEqual(operator.budget_authority_verified_at, dt.datetime(2026, 8, 3))
            self.assertEqual(operator.evidence_urls, "https://company.example/team")

            second = apply_operator_x_handle_rows(session, rows)
            session.commit()
            self.assertEqual(second["updated"], 0)
            self.assertEqual(second["already_current"], 1)
            self.assertEqual(second["conflicts"], [])
        finally:
            session.close()

    def test_operator_handle_snapshot_preserves_nonblank_conflict(self):
        session = self.Session()
        try:
            company = Company(company_id="company:krea", name="Krea")
            operator = Operator(
                company_id=company.company_id,
                name="Victor Perez",
                x_handle="human_curated_handle",
            )
            session.add_all([company, operator])
            session.commit()

            result = apply_operator_x_handle_rows(
                session,
                [{"company": "Krea", "name": "Victor Perez", "x_handle": "viccpoes"}],
            )
            session.commit()

            self.assertEqual(result["updated"], 0)
            self.assertEqual(len(result["conflicts"]), 1)
            self.assertEqual(operator.x_handle, "human_curated_handle")
        finally:
            session.close()

    def test_operator_handle_snapshot_reports_missing_exact_name(self):
        session = self.Session()
        try:
            company = Company(company_id="company:gumloop", name="Gumloop")
            session.add_all(
                [
                    company,
                    Operator(
                        company_id=company.company_id,
                        name="Different Person",
                        x_handle=None,
                    ),
                ]
            )
            session.commit()

            result = apply_operator_x_handle_rows(
                session,
                [
                    {
                        "company": "Gumloop",
                        "name": "Max Brodeur-Urbas",
                        "x_handle": "MaxBrodeurUrbas",
                    }
                ],
            )

            self.assertEqual(result["updated"], 0)
            self.assertEqual(result["missing"][0]["reason"], "operator_not_found")
        finally:
            session.close()

    def test_handle_repair_allows_later_dm_snapshot_exact_match(self):
        session = self.Session()
        try:
            company = Company(company_id="company:perplexity", name="Perplexity")
            operator = Operator(
                company_id=company.company_id,
                name="Raman Malik",
                x_handle=None,
                can_dm=None,
                can_dm_checked_at=None,
            )
            session.add_all([company, operator])
            session.commit()

            result = apply_operator_x_handle_rows(
                session,
                [
                    {
                        "company": "Perplexity",
                        "name": "Raman Malik",
                        "x_handle": "ramanrmalik",
                    }
                ],
            )
            self.assertEqual(result["updated"], 1)
            resolved = (
                session.query(Operator)
                .filter(
                    Operator.company_id == "company:perplexity",
                    Operator.x_handle == "ramanrmalik",
                )
                .one_or_none()
            )
            self.assertIs(resolved, operator)
            self.assertTrue(
                apply_operator_dm_snapshot(
                    resolved,
                    {
                        "can_dm": True,
                        "can_dm_checked_at": "2026-08-27T10:38:48.865571",
                    },
                )
            )
            self.assertTrue(operator.can_dm)
            self.assertEqual(
                operator.can_dm_checked_at,
                dt.datetime(2026, 8, 27, 10, 38, 48, 865571),
            )
        finally:
            session.close()

    def test_release_snapshots_fill_four_handles_but_only_three_have_dm_evidence(self):
        session = self.Session()
        try:
            handle_rows = json.loads(OPERATOR_HANDLES_SNAPSHOT_PATH.read_text(encoding="utf-8"))
            for row in handle_rows:
                company_id = COMPANY_NAME_TO_ID[row["company"]]
                session.add(Company(company_id=company_id, name=row["company"]))
                session.add(
                    Operator(
                        company_id=company_id,
                        name=row["name"],
                        x_handle=None,
                        can_dm=None,
                        can_dm_checked_at=None,
                    )
                )
            session.commit()

            first = apply_operator_x_handle_rows(session, handle_rows)
            second = apply_operator_x_handle_rows(session, handle_rows)
            self.assertEqual(first["updated"], 4)
            self.assertEqual(second["updated"], 0)
            self.assertEqual(second["already_current"], 4)

            intro_snapshot = json.loads(
                INTRO_BRIDGES_SNAPSHOT_PATH.read_text(encoding="utf-8")
            )
            dm_rows = [
                row
                for row in intro_snapshot["operators"]
                if row["company_id"] in set(COMPANY_NAME_TO_ID.values())
            ]
            self.assertEqual(
                {row["company_id"] for row in dm_rows},
                {"company:perplexity", "company:gumloop", "company:krea"},
            )
            advanced = 0
            for row in dm_rows:
                operator = (
                    session.query(Operator)
                    .filter(
                        Operator.company_id == row["company_id"],
                        Operator.x_handle == row["x_handle"],
                    )
                    .one()
                )
                advanced += int(apply_operator_dm_snapshot(operator, row))
            self.assertEqual(advanced, 3)

            vercel = (
                session.query(Operator)
                .filter(Operator.company_id == "company:v0vercel")
                .one()
            )
            self.assertEqual(vercel.x_handle, "keithmessick")
            self.assertIsNone(vercel.can_dm)
            self.assertIsNone(vercel.can_dm_checked_at)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
