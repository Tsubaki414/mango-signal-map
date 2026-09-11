from __future__ import annotations

import collections
import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.models import ActionItem, Company, Operator, OutreachLog
from backend.bd_api import _business_today, _home_action_sort_key, _is_home_operational_summary, home_summary
from backend.bd_serializers import company_summary, current_action_item, has_active_outreach_continuation
from backend.release_schedule import (
    RELEASE_DATE,
    RELEASE_DEFAULT_DUE_DATES,
    TODAY_INTERNAL_CHECKS,
    WAVE_1_REMAINDER,
    WAVE_2,
    due_date_source,
    planned_work_type,
)
from scripts.apply_release_schedule import apply_release_dates


class TestReleaseSchedule(unittest.TestCase):
    def test_home_uses_shanghai_business_day_across_railway_utc_boundary(self):
        instant = dt.datetime(2026, 8, 28, 19, 30, tzinfo=dt.timezone.utc)
        self.assertEqual(_business_today(instant), dt.date(2026, 8, 29))

        with patch.dict("os.environ", {"MANGO_BUSINESS_TIMEZONE": "UTC"}):
            self.assertEqual(_business_today(instant), dt.date(2026, 8, 28))

    def test_schedule_covers_all_42_actions_with_sane_daily_load(self):
        self.assertEqual(len(RELEASE_DEFAULT_DUE_DATES), 42)
        counts = collections.Counter(RELEASE_DEFAULT_DUE_DATES.values())
        self.assertEqual(counts[RELEASE_DATE.isoformat()], 3)
        self.assertLessEqual(max(counts.values()), 3)
        self.assertEqual(sum(counts.values()), 42)
        self.assertTrue(all(RELEASE_DEFAULT_DUE_DATES[cid] == RELEASE_DATE.isoformat() for cid in TODAY_INTERNAL_CHECKS))

    def test_release_day_items_are_internal_checks_not_contact_claims(self):
        self.assertEqual(
            set(TODAY_INTERNAL_CHECKS),
            {"company:replit", "company:cursor", "company:perplexity"},
        )
        self.assertTrue(all(planned_work_type(company_id) == "internal_relationship_check" for company_id in TODAY_INTERNAL_CHECKS))
        self.assertTrue(all(planned_work_type(company_id) == "planned_work_item" for company_id in WAVE_1_REMAINDER + WAVE_2))

    def test_null_only_apply_is_idempotent_and_preserves_human_date(self):
        rows = [ActionItem(company_id=company_id, primary_next_action="work") for company_id in RELEASE_DEFAULT_DUE_DATES]
        human = next(row for row in rows if row.company_id == "company:runway")
        human.due_date = "2026-12-31"
        changed, missing = apply_release_dates(rows)
        self.assertEqual(changed, 41)
        self.assertEqual(missing, [])
        self.assertEqual(human.due_date, "2026-12-31")
        changed_again, missing_again = apply_release_dates(rows)
        self.assertEqual(changed_again, 0)
        self.assertEqual(missing_again, [])
        self.assertEqual(human.due_date, "2026-12-31")

    def test_due_date_source_distinguishes_default_from_human_schedule(self):
        company_id = TODAY_INTERNAL_CHECKS[0]
        self.assertEqual(
            due_date_source(company_id, RELEASE_DEFAULT_DUE_DATES[company_id]),
            "release_planning_default",
        )
        self.assertEqual(due_date_source(company_id, "2026-12-31"), "human_or_external_schedule")

    def test_serializer_marks_planning_date_and_legacy_freshness_honestly(self):
        company = Company(
            company_id="company:replit",
            name="Replit",
            category="AI coding",
            spend_evidence_level="legacy_unmapped",
            raw_json=json.dumps(
                {
                    "cohort_group": "existing_v3",
                    "evidence_contract_status": "legacy_ranking_sources_not_carried_requires_revalidation",
                    "last_verified_at": "2026-08-24",
                }
            ),
        )
        company.operators = []
        company.intro_paths = []
        company.sponsorships = []
        company.outreach_logs = []
        company.action_items = [
            ActionItem(
                company_id=company.company_id,
                primary_next_action="Internal relationship check",
                fallback="Official channel",
                due_date=RELEASE_DEFAULT_DUE_DATES[company.company_id],
            )
        ]
        row = company_summary(company)
        self.assertEqual(row["due_date_source"], "release_planning_default")
        self.assertEqual(row["planned_work_type"], "internal_relationship_check")
        self.assertTrue(row["evidence_contract"]["historical_reference"])
        self.assertTrue(row["evidence_contract"]["revalidation_needed"])
        self.assertEqual(
            row["evidence_contract"]["freshness_status"],
            "historical_reference_revalidation_needed",
        )

    def test_record_timestamp_never_claims_human_verification(self):
        company = Company(
            company_id="company:current",
            name="Current record",
            spend_evidence_level="L1",
            last_verified_at=dt.datetime(2026, 8, 26),
            raw_json=json.dumps({"last_verified_at": "2026-08-26"}),
        )
        company.operators = []
        company.intro_paths = []
        company.sponsorships = []
        company.outreach_logs = []
        company.action_items = []
        contract = company_summary(company)["evidence_contract"]
        self.assertEqual(contract["record_last_verified_at"], "2026-08-26T00:00:00")
        self.assertEqual(contract["freshness_status"], "record_timestamp_present")
        self.assertEqual(contract["human_review_status"], "not_recorded")
        self.assertNotIn("human_last_verified_at", contract)
        self.assertNotEqual(contract["freshness_status"], "human_verified")

    def test_computed_guidance_without_action_item_is_explicitly_unscheduled(self):
        company = Company(
            company_id="company:unscheduled",
            name="Unscheduled",
            spend_evidence_level="L1",
        )
        company.operators = []
        company.intro_paths = []
        company.sponsorships = []
        company.outreach_logs = []
        company.action_items = []
        row = company_summary(company)
        self.assertFalse(row["has_scheduled_action"])
        self.assertEqual(row["action_record_status"], "unscheduled_research_suggestion")
        self.assertIsNone(row["action_id"])
        self.assertIsNone(row["action_status"])
        self.assertIsNone(row["action_fallback"])
        self.assertIsNone(row["owner"])
        self.assertIsNone(row["due_date"])
        self.assertEqual(row["current_work_kind"], "unscheduled_research_suggestion")
        self.assertIsNone(row["current_workflow_status"])
        self.assertEqual(row["next_action_source"], "unscheduled_research_suggestion")

    def test_current_action_prefers_active_then_earliest_due_and_newest_terminal(self):
        company = Company(company_id="company:multi", name="Multiple actions", spend_evidence_level="L1")
        company.action_items = [
            ActionItem(
                id=1,
                status="done",
                primary_next_action="Completed historical task",
                fallback="Old fallback",
                due_date="2026-08-29",
                created_at=dt.datetime(2026, 8, 20),
            ),
            ActionItem(
                id=2,
                status="open",
                primary_next_action="Later open task",
                fallback="Later fallback",
                due_date="2026-09-05",
                created_at=dt.datetime(2026, 8, 28),
            ),
            ActionItem(
                id=3,
                status="in_progress",
                primary_next_action="Current active task",
                fallback="Current fallback",
                due_date="2026-09-02",
                created_at=dt.datetime(2026, 8, 27),
            ),
        ]
        selected = current_action_item(company)
        self.assertEqual(selected.id, 3)

        company.action_items = [
            ActionItem(
                id=10,
                status="done",
                primary_next_action="Older terminal",
                created_at=dt.datetime(2026, 8, 20),
            ),
            ActionItem(
                id=11,
                status="blocked",
                primary_next_action="Newest terminal",
                created_at=dt.datetime(2026, 8, 28),
            ),
        ]
        self.assertEqual(current_action_item(company).id, 11)

    def test_summary_action_fields_are_from_one_selected_persisted_item(self):
        company = Company(
            company_id="company:replit",
            name="Replit",
            spend_evidence_level="L2",
        )
        company.operators = []
        company.intro_paths = []
        company.sponsorships = []
        company.outreach_logs = []
        company.action_items = [
            ActionItem(
                id=1,
                status="done",
                execution_wave=1,
                primary_next_action="Do not repeat completed task",
                fallback="Completed fallback",
                due_date=RELEASE_DATE.isoformat(),
                created_at=dt.datetime(2026, 8, 20),
            ),
            ActionItem(
                id=2,
                status="open",
                execution_wave=2,
                primary_next_action="Ask Jennie to verify the relationship internally",
                fallback="If Jennie has no route, use the official channel",
                due_date=RELEASE_DATE.isoformat(),
                created_at=dt.datetime(2026, 8, 28),
            ),
        ]
        row = company_summary(company)
        self.assertEqual(row["action_id"], 2)
        self.assertEqual(row["action_status"], "open")
        self.assertEqual(row["next_action_source"], "scheduled_internal_verification")
        self.assertEqual(row["next_action"], "Ask Jennie to verify the relationship internally")
        self.assertEqual(row["action_fallback"], "If Jennie has no route, use the official channel")
        self.assertEqual(row["current_fallback"], "If Jennie has no route, use the official channel")
        self.assertEqual(row["current_work_kind"], "release_internal_check")
        self.assertEqual(row["current_workflow_status"], "open")

    def test_runway_like_active_action_does_not_borrow_live_computed_copy(self):
        company = Company(company_id="company:runway", name="Runway", spend_evidence_level="L2")
        operator = Operator(
            name="Growth operator",
            role="Head of Growth",
            x_handle="runway_growth",
            can_dm=True,
        )
        operator.bridges = []
        company.operators = [operator]
        company.intro_paths = []
        company.sponsorships = []
        company.outreach_logs = []
        company.action_items = [
            ActionItem(
                id=20,
                status="open",
                primary_next_action="Ask the campaign owner to validate the brief",
                fallback="Use Runway's official partner form",
                owner="Solomon",
                due_date="2026-08-30",
            )
        ]

        row = company_summary(company)

        self.assertEqual(row["best_route_kind"], "direct_only")
        self.assertEqual(row["current_work_kind"], "action_item")
        self.assertEqual(row["current_workflow_status"], "open")
        self.assertEqual(row["next_action_source"], "stored_action_item")
        self.assertEqual(row["next_action"], "Ask the campaign owner to validate the brief")
        self.assertEqual(row["owner"], "Solomon")
        self.assertEqual(row["due_date"], "2026-08-30")
        self.assertEqual(row["current_fallback"], "Use Runway's official partner form")
        self.assertNotIn("可直接私信", row["next_action"])

    def test_terminal_action_with_active_outreach_uses_only_outreach_current_fields(self):
        company = Company(company_id="company:terminal-live", name="Terminal with live outreach", spend_evidence_level="L1")
        company.operators = []
        company.intro_paths = []
        company.sponsorships = []
        company.action_items = [
            ActionItem(
                id=30,
                status="done",
                primary_next_action="Historical completed action",
                fallback="Historical fallback",
                owner="Historical owner",
                due_date="2026-08-20",
            )
        ]
        company.outreach_logs = [
            OutreachLog(
                id=30,
                stage="connector_replied",
                owner="Live owner",
                next_follow_up_date="2026-08-30",
                occurred_at=dt.datetime(2026, 8, 29, 9, 0),
                created_at=dt.datetime(2026, 8, 29, 9, 1),
                voided=False,
            )
        ]

        row = company_summary(company)

        self.assertEqual(row["action_id"], 30)
        self.assertEqual(row["action_status"], "done")
        self.assertEqual(row["action_fallback"], "Historical fallback")
        self.assertTrue(row["has_scheduled_action"])
        self.assertEqual(row["action_record_status"], "outreach_follow_up")
        self.assertEqual(row["current_work_kind"], "outreach_follow_up")
        self.assertEqual(row["current_workflow_status"], "follow_up_scheduled")
        self.assertEqual(row["next_action_source"], "live_outreach_continuation")
        self.assertIn("已回复", row["next_action"])
        self.assertEqual(row["owner"], "Live owner")
        self.assertEqual(row["due_date"], "2026-08-30")
        self.assertIsNone(row["current_fallback"])

    def test_newer_terminal_outreach_retires_older_active_action_as_current_work(self):
        for stage, expected_phrase in (
            ("rejected", "目标方已拒绝"),
            ("relationship_rejected", "关系不成立"),
        ):
            with self.subTest(stage=stage):
                action_created = dt.datetime(2026, 8, 28, 9, 0)
                outcome_at = dt.datetime(2026, 8, 29, 10, 30)
                company = Company(
                    company_id=f"company:newer-{stage}",
                    name=f"Newer {stage}",
                    spend_evidence_level="L1",
                )
                company.operators = []
                company.intro_paths = []
                company.sponsorships = []
                company.action_items = [
                    ActionItem(
                        id=50,
                        status="open",
                        primary_next_action="Repeat the now-invalid old ask",
                        fallback="Stale action fallback",
                        owner="Old owner",
                        due_date="2026-08-30",
                        created_at=action_created,
                    )
                ]
                company.outreach_logs = [
                    OutreachLog(
                        id=60,
                        stage=stage,
                        owner="Outcome recorder",
                        contacted_who="Recorded counterpart",
                        occurred_at=outcome_at,
                        created_at=outcome_at + dt.timedelta(minutes=1),
                        voided=False,
                    )
                ]

                row = company_summary(company)

                # Raw ActionItem history remains auditable, but it no longer
                # owns any field in the reconciled current-work projection.
                self.assertEqual(row["action_id"], 50)
                self.assertEqual(row["action_status"], "open")
                self.assertEqual(row["action_fallback"], "Stale action fallback")
                self.assertFalse(row["has_scheduled_action"])
                self.assertEqual(row["action_record_status"], "terminal_outreach_outcome")
                self.assertEqual(row["current_work_kind"], "outreach_terminal_outcome")
                self.assertEqual(row["current_workflow_status"], stage)
                self.assertEqual(row["next_action_source"], "newer_terminal_outreach")
                self.assertIn(expected_phrase, row["next_action"])
                self.assertIn(expected_phrase, row["current_fallback"])
                self.assertNotEqual(row["next_action"], "Repeat the now-invalid old ask")
                self.assertNotEqual(row["current_fallback"], "Stale action fallback")
                self.assertEqual(row["owner"], "Outcome recorder")
                self.assertIsNone(row["due_date"])
                self.assertEqual(row["planned_work_type"], "terminal_outreach_outcome")
                self.assertEqual(row["current_work_created_at"], outcome_at.isoformat())
                self.assertEqual(row["current_source_type"], "outreach_log")
                self.assertEqual(row["current_source_id"], 60)
                self.assertEqual(row["current_source_occurred_at"], outcome_at.isoformat())
                self.assertTrue(row["next_action_is_fresh"])

    def test_older_terminal_outreach_does_not_override_newer_active_action(self):
        outcome_at = dt.datetime(2026, 8, 27, 10, 30)
        action_created = dt.datetime(2026, 8, 29, 9, 0)
        company = Company(
            company_id="company:new-action-after-rejection",
            name="New action after rejection",
            spend_evidence_level="L1",
        )
        company.operators = []
        company.intro_paths = []
        company.sponsorships = []
        company.action_items = [
            ActionItem(
                id=70,
                status="in_progress",
                primary_next_action="Use the newly approved alternate route",
                fallback="Pause if the alternate route fails",
                owner="New owner",
                due_date="2026-09-01",
                created_at=action_created,
            )
        ]
        company.outreach_logs = [
            OutreachLog(
                id=71,
                stage="rejected",
                owner="Earlier recorder",
                contacted_who="Earlier counterpart",
                occurred_at=outcome_at,
                created_at=outcome_at + dt.timedelta(minutes=1),
                voided=False,
            )
        ]

        row = company_summary(company)

        self.assertTrue(row["has_scheduled_action"])
        self.assertEqual(row["action_record_status"], "scheduled_action_item")
        self.assertEqual(row["current_work_kind"], "action_item")
        self.assertEqual(row["current_workflow_status"], "in_progress")
        self.assertEqual(row["next_action_source"], "stored_action_item")
        self.assertEqual(row["next_action"], "Use the newly approved alternate route")
        self.assertEqual(row["current_fallback"], "Pause if the alternate route fails")
        self.assertEqual(row["owner"], "New owner")
        self.assertEqual(row["due_date"], "2026-09-01")
        self.assertEqual(row["current_source_type"], "action_item")
        self.assertEqual(row["current_source_id"], 70)
        self.assertIsNone(row["current_source_occurred_at"])

    def test_voided_newer_terminal_outreach_does_not_override_active_action(self):
        company = Company(
            company_id="company:voided-terminal",
            name="Voided terminal",
            spend_evidence_level="L1",
        )
        company.operators = []
        company.intro_paths = []
        company.sponsorships = []
        company.action_items = [
            ActionItem(
                id=80,
                status="open",
                primary_next_action="Keep valid action",
                fallback="Keep valid fallback",
                owner="Action owner",
                due_date="2026-09-01",
                created_at=dt.datetime(2026, 8, 28),
            )
        ]
        company.outreach_logs = [
            OutreachLog(
                id=81,
                stage="rejected",
                owner="Mistaken recorder",
                contacted_who="Wrong counterpart",
                occurred_at=dt.datetime(2026, 8, 29),
                created_at=dt.datetime(2026, 8, 29),
                voided=True,
            )
        ]

        row = company_summary(company)

        self.assertEqual(row["current_work_kind"], "action_item")
        self.assertEqual(row["next_action"], "Keep valid action")
        self.assertEqual(row["owner"], "Action owner")

    def test_terminal_action_without_outreach_is_only_unscheduled_suggestion(self):
        company = Company(company_id="company:terminal-only", name="Terminal only", spend_evidence_level="L1")
        operator = Operator(name="Target", x_handle="target", can_dm=True)
        operator.bridges = []
        company.operators = [operator]
        company.intro_paths = []
        company.sponsorships = []
        company.outreach_logs = []
        company.action_items = [
            ActionItem(
                id=40,
                status="blocked",
                primary_next_action="Historical blocked action",
                fallback="Historical blocked fallback",
                owner="Historical owner",
                due_date="2026-08-20",
            )
        ]

        row = company_summary(company)

        self.assertEqual(row["action_id"], 40)
        self.assertEqual(row["action_status"], "blocked")
        self.assertEqual(row["action_fallback"], "Historical blocked fallback")
        self.assertFalse(row["has_scheduled_action"])
        self.assertEqual(row["action_record_status"], "unscheduled_research_suggestion")
        self.assertEqual(row["current_work_kind"], "unscheduled_research_suggestion")
        self.assertIsNone(row["current_workflow_status"])
        self.assertEqual(row["next_action_source"], "unscheduled_research_suggestion")
        self.assertIsNone(row["owner"])
        self.assertIsNone(row["due_date"])
        self.assertNotEqual(row["current_fallback"], "Historical blocked fallback")

    def test_terminal_action_home_filter_requires_real_nonvoid_outreach_continuation(self):
        self.assertFalse(
            _is_home_operational_summary(
                {
                    "action_status": "done",
                    "execution_priority": "现在联系",
                    "has_active_outreach_continuation": False,
                }
            )
        )
        self.assertFalse(
            _is_home_operational_summary(
                {
                    "action_status": "blocked",
                    "execution_priority": "现在联系",
                    "has_active_outreach_continuation": False,
                }
            )
        )
        self.assertTrue(
            _is_home_operational_summary(
                {
                    "action_status": "done",
                    "execution_priority": "暂缓",
                    "has_active_outreach_continuation": True,
                }
            )
        )

    def test_only_latest_nonvoid_active_outreach_reopens_terminal_action(self):
        company = Company(company_id="company:followup", name="Follow-up", spend_evidence_level="L1")
        company.outreach_logs = [
            OutreachLog(
                id=1,
                stage="connector_replied",
                occurred_at=dt.datetime(2026, 8, 27),
                voided=True,
            )
        ]
        self.assertFalse(has_active_outreach_continuation(company))

        company.outreach_logs.append(
            OutreachLog(
                id=2,
                stage="connector_replied",
                occurred_at=dt.datetime(2026, 8, 28),
                voided=False,
            )
        )
        self.assertTrue(has_active_outreach_continuation(company))

        company.outreach_logs.append(
            OutreachLog(
                id=3,
                stage="rejected",
                occurred_at=dt.datetime(2026, 8, 29),
                voided=False,
            )
        )
        self.assertFalse(has_active_outreach_continuation(company))

    def test_home_excludes_terminal_actions_and_keeps_real_outreach_continuation(self):
        def company_with_action(company_id: str, status: str, *, live_log: OutreachLog | None = None) -> Company:
            company = Company(company_id=company_id, name=company_id, spend_evidence_level="L1")
            company.operators = []
            company.intro_paths = []
            company.sponsorships = []
            company.action_items = [
                ActionItem(
                    id={
                        "company:replit": 1,
                        "company:done": 2,
                        "company:blocked-live": 3,
                        "company:blocked-void": 4,
                    }[company_id],
                    status=status,
                    primary_next_action=f"primary:{company_id}",
                    fallback=f"fallback:{company_id}",
                    owner="Solomon",
                    due_date=RELEASE_DATE.isoformat(),
                )
            ]
            company.outreach_logs = [live_log] if live_log else []
            return company

        companies = [
            company_with_action("company:replit", "open"),
            company_with_action("company:done", "done"),
            company_with_action(
                "company:blocked-live",
                "blocked",
                live_log=OutreachLog(
                    id=3,
                    stage="connector_replied",
                    occurred_at=dt.datetime(2026, 8, 28),
                    voided=False,
                ),
            ),
            company_with_action(
                "company:blocked-void",
                "blocked",
                live_log=OutreachLog(
                    id=4,
                    stage="connector_replied",
                    occurred_at=dt.datetime(2026, 8, 28),
                    voided=True,
                ),
            ),
        ]

        class EmptyQuery:
            def options(self, *_args):
                return self

            def filter(self, *_args):
                return self

            def order_by(self, *_args):
                return self

            def limit(self, *_args):
                return self

            def all(self):
                return []

        class FakeSession:
            def query(self, *_args):
                return EmptyQuery()

            def close(self):
                pass

        with (
            patch("backend.bd_api.get_session", return_value=FakeSession()),
            patch("backend.bd_api._load_all_companies", return_value=companies),
        ):
            payload = home_summary()

        priority_ids = {row["company_id"] for row in payload["top_priority_companies"]}
        action_ids = {row["company_id"] for row in payload["top_actions"]}
        self.assertEqual(priority_ids, {"company:replit", "company:blocked-live"})
        self.assertEqual(action_ids, priority_ids)

        release_action = next(row for row in payload["top_actions"] if row["company_id"] == "company:replit")
        self.assertEqual(release_action["action_id"], 1)
        self.assertEqual(release_action["action_status"], "open")
        self.assertEqual(release_action["execution_status"], "open")
        self.assertIn("execution_priority", release_action)
        self.assertEqual(release_action["primary_next_action"], "primary:company:replit")
        self.assertEqual(release_action["fallback"], "fallback:company:replit")

        outreach_action = next(
            row for row in payload["top_actions"] if row["company_id"] == "company:blocked-live"
        )
        self.assertEqual(outreach_action["action_status"], "blocked")
        self.assertEqual(outreach_action["current_work_kind"], "outreach_follow_up")
        self.assertEqual(outreach_action["current_workflow_status"], "follow_up_scheduled")
        self.assertEqual(outreach_action["execution_status"], "follow_up_scheduled")
        self.assertIsNone(outreach_action["fallback"])

    def test_all_release_day_internal_actions_beat_live_cold_dm_copy(self):
        for company_id in TODAY_INTERNAL_CHECKS:
            with self.subTest(company_id=company_id):
                company = Company(
                    company_id=company_id,
                    name=company_id.removeprefix("company:").title(),
                    spend_evidence_level="L2",
                )
                operator = Operator(
                    name="Current operator",
                    role="Head of Growth",
                    x_handle="current_operator",
                    can_dm=True,
                )
                operator.bridges = []
                company.operators = [operator]
                company.intro_paths = []
                company.sponsorships = []
                company.outreach_logs = []
                stored_action = "向 Jennie 做内部关系核实，先不直接联系目标。"
                company.action_items = [
                    ActionItem(
                        company_id=company_id,
                        execution_wave=1,
                        primary_next_action=stored_action,
                        fallback="若内部无路径，再使用已观测的冷启动渠道。",
                        due_date=RELEASE_DATE.isoformat(),
                    )
                ]
                row = company_summary(company)
                self.assertEqual(row["next_action"], stored_action)
                self.assertEqual(row["next_action_source"], "scheduled_internal_verification")
                self.assertEqual(row["channel_access"]["state"], "x_dm_appears_open")
                self.assertNotIn("可直接私信", row["next_action"])

    def test_today_rows_sort_before_a_higher_priority_future_row(self):
        today_rows = [
            {
                "company_id": company_id,
                "name": company_id,
                "due_date": RELEASE_DATE.isoformat(),
                "execution_priority": "等待内部核实",
                "priority": "Medium",
            }
            for company_id in TODAY_INTERNAL_CHECKS
        ]
        future = {
            "company_id": "company:pixverse",
            "name": "PixVerse",
            "due_date": "2026-08-30",
            "execution_priority": "现在联系",
            "priority": "High",
        }
        ordered = sorted(today_rows + [future], key=lambda row: _home_action_sort_key(row, today=RELEASE_DATE))
        self.assertEqual({row["company_id"] for row in ordered[:3]}, set(TODAY_INTERNAL_CHECKS))
        self.assertEqual(ordered[3]["company_id"], "company:pixverse")


if __name__ == "__main__":
    unittest.main()
