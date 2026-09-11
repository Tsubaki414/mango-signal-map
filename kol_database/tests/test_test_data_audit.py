from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.audit_and_clean_test_data import PILOT_COMPANY_IDS, run_audit


SCHEMA = """
CREATE TABLE solomon_reviews (
    id INTEGER PRIMARY KEY,
    company_id TEXT UNIQUE NOT NULL,
    decision TEXT,
    intro_path_confirmed_real BOOLEAN,
    creator_suggestions_sellable BOOLEAN,
    missing_info TEXT,
    solomon_notes TEXT,
    next_action TEXT,
    reviewed_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE TABLE solomon_review_history (
    id INTEGER PRIMARY KEY,
    company_id TEXT NOT NULL,
    decision TEXT,
    intro_path_confirmed_real BOOLEAN,
    creator_suggestions_sellable BOOLEAN,
    missing_info TEXT,
    solomon_notes TEXT,
    next_action TEXT,
    recorded_at DATETIME NOT NULL
);
CREATE TABLE shortlists (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    client_or_campaign TEXT,
    budget_usd FLOAT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    company_id TEXT,
    objective TEXT,
    target_audience TEXT,
    region_pref TEXT,
    language_pref TEXT,
    platforms_pref TEXT,
    timing TEXT
);
CREATE TABLE shortlist_items (
    id INTEGER PRIMARY KEY,
    shortlist_id INTEGER NOT NULL,
    creator_id INTEGER NOT NULL
);
CREATE TABLE action_items (
    id INTEGER PRIMARY KEY,
    company_id TEXT NOT NULL,
    primary_next_action TEXT,
    fallback TEXT,
    success_condition TEXT,
    outcome_notes TEXT,
    source_id TEXT,
    created_at DATETIME,
    status TEXT,
    owner TEXT
);
"""


def _seed_pilot(conn: sqlite3.Connection) -> None:
    for index, company_id in enumerate(PILOT_COMPANY_IDS, start=1):
        conn.execute(
            """
            INSERT INTO solomon_reviews
              (id, company_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                index,
                company_id,
                f"2026-08-26 05:15:14.{357000 + index:06d}",
                f"2026-08-26 05:15:14.{357000 + index:06d}",
            ),
        )


class AuditFixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "kol.db"
        self.reports = self.root / "reports"
        self.backups = self.root / "backups"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(SCHEMA)
            _seed_pilot(conn)

    def close(self):
        self.temp.cleanup()


class TestAuditDryRun(unittest.TestCase):
    def setUp(self):
        self.fx = AuditFixture()

    def tearDown(self):
        self.fx.close()

    def test_default_dry_run_reports_exact_rows_without_mutating(self):
        with sqlite3.connect(self.fx.db) as conn:
            conn.execute(
                """
                UPDATE solomon_reviews
                SET decision='proceed', intro_path_confirmed_real=1,
                    creator_suggestions_sellable=1,
                    solomon_notes='已有一条真实两跳路径',
                    reviewed_at='2026-08-27 03:42:52.250526'
                WHERE company_id='company:runway'
                """
            )
            conn.execute(
                """
                INSERT INTO shortlists
                  (id,name,client_or_campaign,company_id,created_at,updated_at)
                VALUES
                  (11,'Runway campaign','Runway','company:runway',
                   '2026-08-27 03:26:44.753175','2026-08-27 03:26:44.753181')
                """
            )
            # A legitimate empty draft: report it, never infer it is test.
            conn.execute(
                """
                INSERT INTO shortlists
                  (id,name,client_or_campaign,company_id,budget_usd,region_pref,
                   created_at,updated_at)
                VALUES
                  (12,'Q4 launch','Cursor','company:cursor',8000,'APAC',
                   '2026-08-28 01:00:00','2026-08-28 01:00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO action_items
                  (id,company_id,primary_next_action,source_id,created_at,status,owner)
                VALUES
                  (3,'company:runway','Ask Jennie to validate the route',
                   'action:runway:003','2026-08-27 03:38:43','open','Solomon')
                """
            )

        report = run_audit(
            self.fx.db,
            self.fx.reports,
            apply=False,
            backup_dir=self.fx.backups,
        )

        self.assertEqual(report["mode"], "dry-run")
        self.assertFalse(report["cleanup"]["database_mutated"])
        self.assertEqual(len(report["cleanup"]["planned"]), 2)
        self.assertFalse(self.fx.backups.exists())
        self.assertEqual(report["current_state"]["pilot_configuration_count"], 8)

        confirmed = report["discovered_state"]["classifications"]["confirmed_test_data"]
        self.assertEqual({(row["table"], row["row_id"]) for row in confirmed}, {("solomon_reviews", 1), ("shortlists", 11)})
        uncertain = report["discovered_state"]["classifications"]["uncertain"]
        self.assertEqual([(row["table"], row["row_id"]) for row in uncertain], [("shortlists", 12)])

        with sqlite3.connect(self.fx.db) as conn:
            review = conn.execute(
                "SELECT decision,intro_path_confirmed_real FROM solomon_reviews WHERE id=1"
            ).fetchone()
            self.assertEqual(review, ("proceed", 1))
            self.assertEqual(conn.execute("SELECT count(*) FROM shortlists").fetchone()[0], 2)

        json_report = json.loads((self.fx.reports / "test_data_audit.json").read_text())
        self.assertEqual(json_report["schema_version"], "test_data_audit.v2")
        self.assertIn("pilot_configuration", (self.fx.reports / "test_data_audit.md").read_text())


class TestAuditApply(unittest.TestCase):
    def setUp(self):
        self.fx = AuditFixture()

    def tearDown(self):
        self.fx.close()

    def test_apply_backs_up_then_cleans_only_exact_fingerprints(self):
        with sqlite3.connect(self.fx.db) as conn:
            conn.execute(
                """
                UPDATE solomon_reviews
                SET decision='proceed', intro_path_confirmed_real=1,
                    creator_suggestions_sellable=1,
                    next_action='问 Jennie Liu 这周是否愿意向 Cristobal 做一次暖引荐',
                    reviewed_at='2026-08-27 03:42:52.250526'
                WHERE id=1
                """
            )
            conn.execute(
                """
                INSERT INTO shortlists
                  (id,name,client_or_campaign,company_id,created_at,updated_at)
                VALUES
                  (21,'Runway campaign','Runway','company:runway',
                   '2026-08-27 03:27:51.129854','2026-08-27 03:27:51.129866'),
                  (22,'Legitimate empty draft','Runway','company:runway',
                   '2026-09-01 00:00:00','2026-09-01 00:00:00')
                """
            )

        report = run_audit(
            self.fx.db,
            self.fx.reports,
            apply=True,
            backup_dir=self.fx.backups,
        )

        self.assertTrue(report["cleanup"]["database_mutated"])
        self.assertEqual(len(report["cleanup"]["applied"]), 2)
        backups = list(self.fx.backups.glob("kol_*_pre_test_data_cleanup.db"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as backup:
            self.assertEqual(backup.execute("SELECT decision FROM solomon_reviews WHERE id=1").fetchone()[0], "proceed")
            self.assertEqual(backup.execute("SELECT count(*) FROM shortlists").fetchone()[0], 2)

        with sqlite3.connect(self.fx.db) as conn:
            review = conn.execute(
                """
                SELECT decision,intro_path_confirmed_real,
                       creator_suggestions_sellable,next_action,reviewed_at
                FROM solomon_reviews WHERE id=1
                """
            ).fetchone()
            self.assertEqual(review, (None, None, None, None, None))
            self.assertEqual(conn.execute("SELECT count(*) FROM solomon_reviews").fetchone()[0], 8)
            self.assertEqual(conn.execute("SELECT id FROM shortlists").fetchall(), [(22,)])

        self.assertEqual(report["current_state"]["pilot_configuration_count"], 8)
        self.assertEqual(report["current_state"]["classification_counts"]["confirmed_test_data"], 0)
        self.assertEqual(report["current_state"]["classification_counts"]["uncertain"], 1)

    def test_partial_boolean_residue_is_reset_but_pilot_row_is_kept(self):
        with sqlite3.connect(self.fx.db) as conn:
            conn.execute(
                """
                UPDATE solomon_reviews
                SET intro_path_confirmed_real=0,
                    creator_suggestions_sellable=0
                WHERE id=1
                """
            )

        report = run_audit(
            self.fx.db,
            self.fx.reports,
            apply=True,
            backup_dir=self.fx.backups,
        )
        self.assertEqual(len(report["cleanup"]["applied"]), 1)
        with sqlite3.connect(self.fx.db) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT intro_path_confirmed_real,creator_suggestions_sellable FROM solomon_reviews WHERE id=1"
                ).fetchone(),
                (None, None),
            )
            self.assertEqual(conn.execute("SELECT count(*) FROM solomon_reviews").fetchone()[0], 8)

    def test_future_real_runway_review_does_not_match_old_uat_fingerprint(self):
        with sqlite3.connect(self.fx.db) as conn:
            conn.execute(
                """
                UPDATE solomon_reviews
                SET decision='proceed', intro_path_confirmed_real=0,
                    creator_suggestions_sellable=1,
                    solomon_notes='Solomon reviewed the new evidence in September.',
                    reviewed_at='2026-09-15 10:00:00'
                WHERE id=1
                """
            )

        report = run_audit(
            self.fx.db,
            self.fx.reports,
            apply=True,
            backup_dir=self.fx.backups,
        )
        self.assertEqual(report["cleanup"]["planned"], [])
        self.assertFalse(report["cleanup"]["database_mutated"])
        self.assertFalse(self.fx.backups.exists())
        with sqlite3.connect(self.fx.db) as conn:
            self.assertEqual(conn.execute("SELECT decision FROM solomon_reviews WHERE id=1").fetchone()[0], "proceed")

    def test_live_5000_fingerprint_is_exact_and_near_match_is_preserved(self):
        with sqlite3.connect(self.fx.db) as conn:
            conn.execute(
                """
                INSERT INTO shortlists
                  (id,name,client_or_campaign,company_id,budget_usd,objective,
                   created_at,updated_at)
                VALUES
                  (31,'Runway campaign','Runway','company:runway',5000,'推广 Runway 新功能',
                   '2026-08-27 03:25:06.000001','2026-08-27 03:25:06.000001'),
                  (32,'Runway campaign','Runway','company:runway',5000,'Real customer brief',
                   '2026-08-27 03:25:06.000002','2026-08-27 03:25:06.000002')
                """
            )

        report = run_audit(
            self.fx.db,
            self.fx.reports,
            apply=True,
            backup_dir=self.fx.backups,
        )
        self.assertEqual([row["row_id"] for row in report["cleanup"]["applied"]], [31])
        with sqlite3.connect(self.fx.db) as conn:
            self.assertEqual(conn.execute("SELECT id FROM shortlists").fetchall(), [(32,)])


if __name__ == "__main__":
    unittest.main()
