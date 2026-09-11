from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bd_serializers import _path_json, company_summary
from backend.models import ActionItem, Company, IntroPath, OutreachLog
from scripts.add_intro_path_direction_columns import ensure_intro_path_direction_columns
from scripts.backfill_intro_path_directions import backfill_intro_path_directions
from scripts.migrate_bd_data import _backfill_source_path_directions, _direction_provenance


class TestIntroPathDirectionSchemaMigration(unittest.TestCase):
    def test_idempotently_adds_columns_without_touching_existing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE intro_paths (id INTEGER PRIMARY KEY, path_labels TEXT)")
            conn.execute("INSERT INTO intro_paths (path_labels) VALUES ('@a -> @b')")
            conn.commit()
            conn.close()

            self.assertEqual(
                ensure_intro_path_direction_columns(db_path),
                ["edge_directions", "direction_data_unavailable", "direction_data_unavailable_reason"],
            )
            self.assertEqual(ensure_intro_path_direction_columns(db_path), [])

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT path_labels, edge_directions, direction_data_unavailable, "
                "direction_data_unavailable_reason FROM intro_paths"
            ).fetchone()
            conn.close()
            self.assertEqual(row, ("@a -> @b", None, 0, None))

    def test_existing_db_boot_path_adds_columns_and_backfills_null_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination_path = Path(tmp) / "persistent.db"
            source_path = Path(tmp) / "source.db"
            destination = sqlite3.connect(destination_path)
            destination.execute("CREATE TABLE intro_paths (id INTEGER PRIMARY KEY, source_id TEXT, path_labels TEXT)")
            destination.executemany(
                "INSERT INTO intro_paths (source_id, path_labels) VALUES (?, ?)",
                [("source:x", "@a -> @b"), (None, "@human -> @target")],
            )
            destination.commit()
            destination.close()

            source = sqlite3.connect(source_path)
            source.execute(
                "CREATE TABLE x_paths (path_id TEXT, edge_directions_json TEXT, raw_json TEXT)"
            )
            source.execute(
                "CREATE TABLE operator_person_paths "
                "(operator_path_id TEXT, primary_edge_directions_json TEXT, raw_json TEXT)"
            )
            source.execute("INSERT INTO x_paths VALUES ('source:x', '[\"follows\"]', '{}')")
            source.commit()
            source.close()

            first = backfill_intro_path_directions(destination_path, source_path)
            second = backfill_intro_path_directions(destination_path, source_path)
            self.assertEqual(first["updated"], 1)
            self.assertEqual(first["remaining_source_rows_without_payload"], 0)
            self.assertEqual(second["updated"], 0)

            destination = sqlite3.connect(destination_path)
            rows = destination.execute(
                "SELECT source_id, edge_directions FROM intro_paths ORDER BY id"
            ).fetchall()
            destination.close()
            self.assertEqual(rows, [("source:x", '["follows"]'), (None, None)])


class TestDirectionSourceBackfill(unittest.TestCase):
    def test_preserves_source_json_exactly(self):
        source = '["@MangoLabs_ -> @jenniekusu", "@jenniekusu -> @MangoLabs_"]'
        stored, unavailable, reason = _direction_provenance(source, "{}")
        self.assertEqual(stored, source)
        self.assertFalse(unavailable)
        self.assertIsNone(reason)

    def test_empty_source_payload_carries_precise_identity_reason(self):
        raw = json.dumps({"graph_reachability_status": "not_evaluable_identity_unconfirmed"})
        stored, unavailable, reason = _direction_provenance("[]", raw)
        self.assertEqual(stored, "[]")
        self.assertTrue(unavailable)
        self.assertIn("目标 X 身份", reason)

    def test_backfill_only_fills_unmigrated_source_owned_path(self):
        path = IntroPath(source_id="source:path", target_type="company_account", root="mango")
        _backfill_source_path_directions(path, '["follows"]', "{}")
        self.assertEqual(path.edge_directions, '["follows"]')

        # A later migration run must not overwrite a curated non-NULL value.
        _backfill_source_path_directions(path, '["followed_by"]', "{}")
        self.assertEqual(path.edge_directions, '["follows"]')

        human_path = IntroPath(source_id=None, target_type="company_account", root="mango")
        _backfill_source_path_directions(human_path, '["follows"]', "{}")
        self.assertIsNone(human_path.edge_directions)


class TestDirectionSerialization(unittest.TestCase):
    def test_symbolic_directions_become_explicit_per_hop_facts(self):
        path = IntroPath(
            target_type="operator_person",
            root="solomon",
            path_labels="Solomon (@Solomon_Nahhh) -> Evie (@0xEvieYang) -> Target (@target)",
            edge_directions='["mutual_follow", "followed_by"]',
        )
        result = _path_json(path)
        self.assertTrue(result["direction_data_available"])
        self.assertEqual(result["direction_steps"][0]["relationship"], "mutual_follow")
        self.assertTrue(result["direction_steps"][0]["from_follows_to"])
        self.assertTrue(result["direction_steps"][0]["to_follows_from"])
        self.assertEqual(result["direction_steps"][1]["relationship"], "followed_by")
        self.assertFalse(result["direction_steps"][1]["from_follows_to"])
        self.assertTrue(result["direction_steps"][1]["to_follows_from"])

    def test_explicit_atomic_edges_group_into_mutual_and_one_way_hops(self):
        path = IntroPath(
            target_type="operator_person",
            root="mango",
            path_labels="@MangoLabs_ -> @jenniekusu -> @target",
            edge_directions=json.dumps(
                [
                    "@MangoLabs_ -> @jenniekusu",
                    "@jenniekusu -> @MangoLabs_",
                    "@jenniekusu -> @target",
                ]
            ),
        )
        result = _path_json(path)
        self.assertEqual(
            [step["relationship"] for step in result["direction_steps"]],
            ["mutual_follow", "follows"],
        )

    def test_missing_directions_stay_unknown_instead_of_inferred(self):
        path = IntroPath(
            target_type="company_account",
            root="mango",
            path_labels="@MangoLabs_ -> @target",
            edge_directions="[]",
            direction_data_unavailable=True,
            direction_data_unavailable_reason="源记录未提供方向。",
        )
        result = _path_json(path)
        self.assertTrue(result["direction_data_unavailable"])
        self.assertEqual(result["direction_steps"][0]["relationship"], "unknown")
        self.assertIsNone(result["direction_steps"][0]["from_follows_to"])
        self.assertEqual(result["direction_data_unavailable_reason"], "源记录未提供方向。")

    def test_nonmatching_payload_cannot_be_labeled_available(self):
        path = IntroPath(
            target_type="company_account",
            root="mango",
            path_labels="@a -> @b",
            edge_directions='["@other -> @target"]',
        )
        result = _path_json(path)
        self.assertFalse(result["direction_data_available"])
        self.assertTrue(result["direction_data_unavailable"])
        self.assertEqual(result["direction_steps"][0]["relationship"], "unknown")

    def test_frontend_names_follow_direction_without_ambiguous_arrows(self):
        source = (Path(__file__).resolve().parents[1] / "frontend" / "app.js").read_text(encoding="utf-8")
        block = source.split("function directionStepsHtml", 1)[1].split("function pathBlockHtml", 1)[0]
        self.assertIn("双向互关", block)
        self.assertIn("关注方向未知", block)
        self.assertIn("关注 ${right}", block)


class TestLiveExecutionOwnerAndDueDate(unittest.TestCase):
    @staticmethod
    def _company() -> Company:
        company = Company(company_id="company:test", name="Test", spend_evidence_level="L1", priority_tier="C")
        company.intro_paths = []
        company.operators = []
        company.sponsorships = []
        company.action_items = [
            ActionItem(
                execution_wave=1,
                primary_next_action="Historical action",
                owner="Historical Owner",
                due_date="2026-09-01",
            )
        ]
        company.outreach_logs = []
        return company

    def test_latest_nonvoid_outreach_owner_and_followup_override_action_item(self):
        company = self._company()
        company.outreach_logs = [
            OutreachLog(
                id=9,
                stage="contact_attempted",
                owner="Live Owner",
                next_follow_up_date="2026-08-30",
                occurred_at=dt.datetime(2026, 8, 28, 12, 0),
                voided=False,
            )
        ]
        row = company_summary(company)
        self.assertEqual(row["owner"], "Live Owner")
        self.assertEqual(row["due_date"], "2026-08-30")

    def test_latest_voided_log_is_ignored_and_blank_live_fields_do_not_borrow_from_action(self):
        company = self._company()
        company.outreach_logs = [
            OutreachLog(
                id=8,
                stage="contact_attempted",
                owner="Older Owner",
                next_follow_up_date="2026-08-29",
                occurred_at=dt.datetime(2026, 8, 27, 12, 0),
                voided=False,
            ),
            OutreachLog(
                id=9,
                stage="contact_attempted",
                owner=None,
                next_follow_up_date=None,
                occurred_at=dt.datetime(2026, 8, 28, 12, 0),
                voided=False,
            ),
            OutreachLog(
                id=10,
                stage="contact_attempted",
                owner="Voided Owner",
                next_follow_up_date="2099-01-01",
                occurred_at=dt.datetime(2026, 8, 29, 12, 0),
                voided=True,
            ),
        ]
        row = company_summary(company)
        self.assertIsNone(row["owner"])
        self.assertIsNone(row["due_date"])
        self.assertEqual(row["current_work_kind"], "outreach_follow_up")
        self.assertEqual(row["current_workflow_status"], "follow_up_scheduled")
        # The ActionItem remains available as raw history but cannot lend its
        # owner/date to a different current outreach continuation.
        self.assertEqual(row["action_status"], "open")

    def test_home_actions_reuse_reconciled_summary_owner_and_due_date(self):
        source = (Path(__file__).resolve().parents[1] / "backend" / "bd_api.py").read_text(encoding="utf-8")
        block = source.split("def home_summary", 1)[1].split("campaign_ready =", 1)[0]
        # All stored/dynamic action variants now use one unified append path,
        # so one reference covers every card instead of duplicated branches.
        self.assertEqual(block.count('"owner": row["owner"]'), 1)
        self.assertEqual(block.count('"due_date": row["due_date"]'), 1)


if __name__ == "__main__":
    unittest.main()
