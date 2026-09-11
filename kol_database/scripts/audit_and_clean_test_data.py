"""Evidence-first audit and narrowly-scoped cleanup of dev/UAT data.

Default mode is a database read-only dry run. It writes two audit artifacts
(`reports/test_data_audit.json` and `.md`) but never changes SQLite. Passing
``--apply`` performs only cleanup actions whose rows still match one of the
known Runway UAT fingerprints below; a recoverable database backup is created
before the transaction starts.

The four test-data classifications are kept separate from
``pilot_configuration``. A blank ``SolomonReview`` row means a company was
selected for the eight-company pilot; it is neither a human review nor real
research evidence and must not be deleted merely because it is blank.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backup_db import backup_database  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.environ.get("DB_PATH", REPO_ROOT / "data" / "kol.db"))
DEFAULT_REPORT_DIR = REPO_ROOT / "reports"

CLASSIFICATIONS = (
    "confirmed_test_data",
    "likely_test_data",
    "real_research_data",
    "uncertain",
)

PILOT_COMPANY_IDS = (
    "company:runway",
    "company:cursor",
    "company:genspark",
    "company:elevenlabs",
    "company:synthesia",
    "company:gumloop",
    "company:manus",
    "company:clay",
)

# These timestamps/text fragments come from independently-observed copies of
# the same Runway UAT walkthrough. A future real Solomon review is never
# reset merely because it also chooses `proceed`; it must match this known
# fingerprint (or be the already-partially-cleaned residue on the same seed
# row with no reviewed_at/text fields).
RUNWAY_PILOT_CREATED_PREFIX = "2026-08-26 05:15:14."
RUNWAY_UAT_REVIEWED_PREFIXES = (
    "2026-08-26 05:15:34.",
    "2026-08-27 03:42:52.",
)
RUNWAY_UAT_TEXT_MARKERS = (
    "真实两跳路径",
    "real 2-hop path via jennie liu",
    "ask jennie liu whether she's willing to make a warm intro",
    "问 jennie liu 这周是否愿意向 cristobal 做一次暖引荐",
    "推荐创作者名单里也有一位有真实历史合作关系的候选人",
    "suggested creator list has a genuine prior-relationship lead",
)

# Exact known shortlist fingerprints. IDs are intentionally absent: local
# and deployed SQLite files used different auto-increment IDs.
RUNWAY_EMPTY_LOCAL_CREATED_PREFIXES = (
    "2026-08-27 03:26:44.",
    "2026-08-27 03:27:51.",
)
RUNWAY_5000_LIVE_CREATED_PREFIX = "2026-08-27 03:25:06."
RUNWAY_5000_OBJECTIVE = "推广 Runway 新功能"

TEST_NAME_RE = re.compile(r"(?:^|[^a-z0-9])(test|uat|demo|dummy|sample|fake)(?:$|[^a-z0-9])", re.I)
TEST_NAME_ZH = ("测试", "示例", "演示数据", "假数据")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _starts(value: Any, prefixes: Iterable[str]) -> bool:
    text = str(value or "")
    return any(text.startswith(prefix) for prefix in prefixes)


def _contains_test_marker(*values: Any) -> bool:
    text = " ".join(str(value or "") for value in values)
    return bool(TEST_NAME_RE.search(text)) or any(marker in text for marker in TEST_NAME_ZH)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _rows(conn: sqlite3.Connection, table: str, *, order_by: str = "id") -> list[sqlite3.Row]:
    if not _table_exists(conn, table):
        return []
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    ordering = order_by if order_by in columns else "rowid"
    return conn.execute(f'SELECT * FROM "{table}" ORDER BY "{ordering}"').fetchall()


def _count(conn: sqlite3.Connection, table: str, where: str = "1=1", params: tuple = ()) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(f'SELECT count(*) FROM "{table}" WHERE {where}', params).fetchone()[0])


def _finding(
    table: str,
    row_id: Any,
    classification: str,
    reason: str,
    *,
    entity_key: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if classification not in CLASSIFICATIONS:
        raise ValueError(f"Unknown classification: {classification}")
    return {
        "table": table,
        "row_id": row_id,
        "entity_key": entity_key,
        "classification": classification,
        "reason": reason,
        "evidence": evidence or {},
    }


def _known_runway_review_residue(row: dict[str, Any]) -> tuple[bool, str, list[str]]:
    if row.get("company_id") != "company:runway":
        return False, "not Runway", []

    created_is_seed = str(row.get("created_at") or "").startswith(RUNWAY_PILOT_CREATED_PREFIX)
    reviewed_is_known = _starts(row.get("reviewed_at"), RUNWAY_UAT_REVIEWED_PREFIXES)
    text_blob = " ".join(
        str(row.get(key) or "")
        for key in ("missing_info", "solomon_notes", "next_action")
    ).lower()
    matched_markers = [marker for marker in RUNWAY_UAT_TEXT_MARKERS if marker.lower() in text_blob]

    mutable_fields = (
        "decision",
        "intro_path_confirmed_real",
        "creator_suggestions_sellable",
        "missing_info",
        "solomon_notes",
        "next_action",
        "reviewed_at",
    )
    fields_with_values = [field for field in mutable_fields if row.get(field) is not None]

    if reviewed_is_known or matched_markers:
        reason_bits = []
        if reviewed_is_known:
            reason_bits.append(f"known reviewed_at={row.get('reviewed_at')}")
        if matched_markers:
            reason_bits.append(f"known text marker(s): {matched_markers}")
        return True, "; ".join(reason_bits), fields_with_values

    # Residue left by the old cleanup: it changed the two tri-state booleans
    # to False instead of restoring NULL. Restrict this to the exact bulk-
    # seeded Runway row and only when no decision/text/review timestamp exists;
    # a later genuine review on the same pilot row will have reviewed_at set.
    no_human_review_fields = all(
        _blank(row.get(field))
        for field in ("decision", "missing_info", "solomon_notes", "next_action", "reviewed_at")
    )
    tri_state_residue = any(
        row.get(field) is not None
        for field in ("intro_path_confirmed_real", "creator_suggestions_sellable")
    )
    if created_is_seed and no_human_review_fields and tri_state_residue:
        residue_fields = [
            field
            for field in ("intro_path_confirmed_real", "creator_suggestions_sellable")
            if row.get(field) is not None
        ]
        return (
            True,
            "known bulk-seeded Runway pilot row has no human review but retains non-NULL tri-state UAT cleanup residue",
            residue_fields,
        )

    return False, "does not match a known Runway UAT review fingerprint", []


def _runway_shortlist_fingerprint(row: dict[str, Any]) -> tuple[str | None, str]:
    if int(row.get("item_count") or 0) != 0:
        return None, "has creator items"
    if str(row.get("name") or "").strip() != "Runway campaign":
        return None, "name differs"
    company_match = row.get("company_id") == "company:runway"
    client_match = str(row.get("client_or_campaign") or "").strip().lower() == "runway"
    if not (company_match or client_match):
        return None, "not linked to Runway"

    created_at = str(row.get("created_at") or "")
    if _starts(created_at, RUNWAY_EMPTY_LOCAL_CREATED_PREFIXES):
        if (
            row.get("budget_usd") is None
            and _blank(row.get("objective"))
            and _blank(row.get("target_audience"))
        ):
            return "runway_empty_local_20260827", "matches one of the two known empty local Runway UAT rows"
        return None, "known local timestamp but metadata no longer matches the UAT fingerprint"

    if created_at.startswith(RUNWAY_5000_LIVE_CREATED_PREFIX):
        budget = row.get("budget_usd")
        if (
            budget is not None
            and float(budget) == 5000.0
            and str(row.get("objective") or "").strip() == RUNWAY_5000_OBJECTIVE
            and _blank(row.get("target_audience"))
        ):
            return "runway_5000_live_20260827", "matches the user-identified $5,000 / zero-creator Runway UAT row"
        return None, "known live timestamp but metadata no longer matches the UAT fingerprint"

    return None, "does not match a known Runway UAT timestamp"


def _scan_database(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    classifications: dict[str, list[dict[str, Any]]] = {key: [] for key in CLASSIFICATIONS}
    cleanup_plan: list[dict[str, Any]] = []
    pilot_configuration: list[dict[str, Any]] = []

    table_names = (
        "solomon_reviews",
        "solomon_review_history",
        "outreach_logs",
        "campaign_history",
        "shortlists",
        "shortlist_items",
        "action_items",
        "companies",
        "operators",
        "creators",
        "connector_briefs",
    )
    table_scans = {
        table: {
            "exists": _table_exists(conn, table),
            "row_count": _count(conn, table),
        }
        for table in table_names
    }

    # ReviewDecision is a field on SolomonReview, not a standalone table.
    for raw in _rows(conn, "solomon_reviews"):
        row = dict(raw)
        is_pilot = row.get("company_id") in PILOT_COMPANY_IDS
        if is_pilot:
            human_fields = any(
                row.get(field) is not None
                for field in ("decision", "missing_info", "solomon_notes", "next_action", "reviewed_at")
            )
            pilot_configuration.append(
                {
                    "table": "solomon_reviews",
                    "row_id": row.get("id"),
                    "company_id": row.get("company_id"),
                    "record_role": "pilot_configuration",
                    "human_review_status": "contains_review_fields" if human_fields else "not_human_reviewed",
                    "decision": row.get("decision"),
                    "reviewed_at": row.get("reviewed_at"),
                    "created_at": row.get("created_at"),
                    "reason": "row selects a real company into the eight-company UAT pilot; it is not itself research evidence or a completed human review",
                }
            )

        matched, reason, residue_fields = _known_runway_review_residue(row)
        if matched:
            classifications["confirmed_test_data"].append(
                _finding(
                    "solomon_reviews",
                    row.get("id"),
                    "confirmed_test_data",
                    reason,
                    entity_key=row.get("company_id"),
                    evidence={field: row.get(field) for field in residue_fields},
                )
            )
            cleanup_plan.append(
                {
                    "table": "solomon_reviews",
                    "row_id": row.get("id"),
                    "entity_key": row.get("company_id"),
                    "action": "reset_known_uat_review_fields_to_null_keep_pilot_row",
                    "fingerprint_id": "runway_review_uat_20260826_27",
                    "fields": [
                        "decision",
                        "intro_path_confirmed_real",
                        "creator_suggestions_sellable",
                        "missing_info",
                        "solomon_notes",
                        "next_action",
                        "reviewed_at",
                    ],
                    "reason": reason,
                }
            )
        elif not is_pilot:
            classification = "likely_test_data" if _contains_test_marker(*row.values()) else "real_research_data"
            classifications[classification].append(
                _finding(
                    "solomon_reviews",
                    row.get("id"),
                    classification,
                    "non-pilot review contains a literal test marker" if classification == "likely_test_data" else "non-pilot human review row has no test marker",
                    entity_key=row.get("company_id"),
                )
            )

    # Append-only review history: exact Runway UAT snapshots are fabricated
    # history and can be deleted only when the same strict fingerprint hits.
    for raw in _rows(conn, "solomon_review_history"):
        row = dict(raw)
        proxy = dict(row)
        proxy["created_at"] = RUNWAY_PILOT_CREATED_PREFIX
        proxy["reviewed_at"] = row.get("recorded_at")
        matched, reason, _ = _known_runway_review_residue(proxy)
        if matched:
            classifications["confirmed_test_data"].append(
                _finding(
                    "solomon_review_history",
                    row.get("id"),
                    "confirmed_test_data",
                    f"known Runway UAT decision must not survive as human decision history: {reason}",
                    entity_key=row.get("company_id"),
                )
            )
            cleanup_plan.append(
                {
                    "table": "solomon_review_history",
                    "row_id": row.get("id"),
                    "entity_key": row.get("company_id"),
                    "action": "delete_exact_uat_history_row",
                    "fingerprint_id": "runway_review_history_uat_20260826_27",
                    "reason": reason,
                }
            )
        else:
            classification = "likely_test_data" if _contains_test_marker(*row.values()) else "real_research_data"
            classifications[classification].append(
                _finding(
                    "solomon_review_history",
                    row.get("id"),
                    classification,
                    "literal test marker in review history" if classification == "likely_test_data" else "append-only human review history with no test marker",
                    entity_key=row.get("company_id"),
                )
            )

    # A Shortlist is the Campaign entity in this schema. Enumerate every
    # row with its item count; only exact known Runway fingerprints get a
    # cleanup action. Ordinary empty drafts remain untouched/uncertain.
    shortlist_rows: list[dict[str, Any]] = []
    if _table_exists(conn, "shortlists"):
        shortlist_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT s.*, count(si.id) AS item_count
                FROM shortlists s
                LEFT JOIN shortlist_items si ON si.shortlist_id = s.id
                GROUP BY s.id
                ORDER BY s.id
                """
            ).fetchall()
        ]
    for row in shortlist_rows:
        fingerprint_id, fingerprint_reason = _runway_shortlist_fingerprint(row)
        evidence = {
            key: row.get(key)
            for key in (
                "name",
                "client_or_campaign",
                "company_id",
                "budget_usd",
                "objective",
                "target_audience",
                "region_pref",
                "language_pref",
                "platforms_pref",
                "timing",
                "created_at",
                "item_count",
            )
        }
        if fingerprint_id:
            classifications["confirmed_test_data"].append(
                _finding(
                    "shortlists",
                    row.get("id"),
                    "confirmed_test_data",
                    fingerprint_reason,
                    entity_key=row.get("company_id") or row.get("client_or_campaign"),
                    evidence=evidence,
                )
            )
            cleanup_plan.append(
                {
                    "table": "shortlists",
                    "row_id": row.get("id"),
                    "entity_key": row.get("company_id") or row.get("client_or_campaign"),
                    "action": "delete_exact_uat_shortlist",
                    "fingerprint_id": fingerprint_id,
                    "reason": fingerprint_reason,
                }
            )
        elif int(row.get("item_count") or 0) == 0:
            classifications["uncertain"].append(
                _finding(
                    "shortlists",
                    row.get("id"),
                    "uncertain",
                    f"empty campaign draft, preserved because {fingerprint_reason}",
                    entity_key=row.get("company_id") or row.get("client_or_campaign"),
                    evidence=evidence,
                )
            )
        else:
            classifications["real_research_data"].append(
                _finding(
                    "shortlists",
                    row.get("id"),
                    "real_research_data",
                    "campaign/shortlist contains selected creator items and does not match a UAT fingerprint",
                    entity_key=row.get("company_id") or row.get("client_or_campaign"),
                    evidence=evidence,
                )
            )

    # Human-entered execution/event tables: dynamically enumerate each row.
    for table, text_fields in (
        (
            "outreach_logs",
            ("stage", "notes", "contacted_who", "contact_channel", "evidence_url", "voided_reason"),
        ),
        (
            "campaign_history",
            ("campaign_name", "campaign_date", "deliverable", "performance_notes"),
        ),
    ):
        for raw in _rows(conn, table):
            row = dict(raw)
            values = [row.get(field) for field in text_fields]
            has_marker = _contains_test_marker(*values)
            # A deliberately voided outreach test remains as its audit trail;
            # it is classified but never hard-deleted by this script.
            confirmed_voided_test = table == "outreach_logs" and bool(row.get("voided")) and has_marker
            classification = (
                "confirmed_test_data"
                if confirmed_voided_test
                else "likely_test_data"
                if has_marker
                else "real_research_data"
            )
            reason = (
                "voided outreach row explicitly identifies itself as test data; retained as an audit tombstone"
                if confirmed_voided_test
                else "literal test marker requires human review; no automatic cleanup"
                if has_marker
                else "operational row has no UAT/test marker"
            )
            classifications[classification].append(
                _finding(
                    table,
                    row.get("id"),
                    classification,
                    reason,
                    entity_key=row.get("company_id") or row.get("creator_id"),
                    evidence={field: row.get(field) for field in text_fields if row.get(field) is not None},
                )
            )

    # The 42 migrated ActionItems share one backfill timestamp. A stable
    # source_id is affirmative research provenance, not a fabricated date.
    for raw in _rows(conn, "action_items"):
        row = dict(raw)
        text_values = [
            row.get(field)
            for field in ("primary_next_action", "fallback", "success_condition", "outcome_notes")
        ]
        if row.get("source_id"):
            classification = "real_research_data"
            reason = "stable migrated source_id identifies original BD research; created_at may be migration backfill"
        elif _contains_test_marker(*text_values):
            classification = "likely_test_data"
            reason = "literal test marker in human-created action prose; preserved pending human review"
        else:
            classification = "uncertain"
            reason = "human-created action has no source_id and no explicit test marker; provenance requires human review"
        classifications[classification].append(
            _finding(
                "action_items",
                row.get("id"),
                classification,
                reason,
                entity_key=row.get("company_id"),
                evidence={
                    "source_id": row.get("source_id"),
                    "created_at": row.get("created_at"),
                    "status": row.get("status"),
                    "owner": row.get("owner"),
                },
            )
        )

    # Literal-name sweep of the large entity tables. We record exact hits
    # plus scanned counts rather than dumping hundreds of ordinary rows.
    entity_specs = {
        "companies": ("company_id", ("name", "company_id")),
        "operators": ("id", ("name", "x_handle", "source_id")),
        "creators": ("id", ("display_name", "primary_handle")),
    }
    for table, (id_field, fields) in entity_specs.items():
        for raw in _rows(conn, table, order_by=id_field):
            row = dict(raw)
            if _contains_test_marker(*(row.get(field) for field in fields)):
                classifications["likely_test_data"].append(
                    _finding(
                        table,
                        row.get(id_field),
                        "likely_test_data",
                        "literal test/demo/UAT marker in entity identity; preserved pending human review",
                        evidence={field: row.get(field) for field in fields},
                    )
                )

    # Connector briefs are hand-curated research artifacts. List every row
    # so stale claims remain auditable, but never mutate them here.
    for raw in _rows(conn, "connector_briefs"):
        row = dict(raw)
        classifications["real_research_data"].append(
            _finding(
                "connector_briefs",
                row.get("id"),
                "real_research_data",
                "hand-curated research brief; stale/unsupported wording must be hedged in display, not deleted as test data",
                entity_key=row.get("connector_x_handle") or row.get("connector_name"),
                evidence={"connector_name": row.get("connector_name")},
            )
        )

    # Cross-table facts needed to distinguish a graph hypothesis from actual
    # execution. There is no standalone ReviewDecision or Campaign table:
    # they map to solomon_reviews.decision and shortlists respectively.
    runway_review = None
    if _table_exists(conn, "solomon_reviews"):
        runway_review = _dict(
            conn.execute(
                "SELECT * FROM solomon_reviews WHERE company_id='company:runway'"
            ).fetchone()
        )
    runway_action_rows = []
    if _table_exists(conn, "action_items"):
        runway_action_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM action_items WHERE company_id='company:runway' ORDER BY id"
            ).fetchall()
        ]
    runway_cross_table = {
        "semantic_mapping": {
            "ReviewDecision": "solomon_reviews.decision",
            "Campaign": "shortlists (campaign metadata lives on Shortlist)",
            "CreatorCampaignHistory": "campaign_history",
        },
        "company_id": "company:runway",
        "review": runway_review,
        "counts": {
            "review_history": _count(conn, "solomon_review_history", "company_id=?", ("company:runway",)),
            "outreach_logs": _count(conn, "outreach_logs", "company_id=?", ("company:runway",)),
            "shortlists_campaigns": _count(conn, "shortlists", "company_id=?", ("company:runway",)),
            "action_items": _count(conn, "action_items", "company_id=?", ("company:runway",)),
            "operators": _count(conn, "operators", "company_id=?", ("company:runway",)),
        },
        "action_items": runway_action_rows,
    }

    return {
        "table_scans": table_scans,
        "classifications": classifications,
        "classification_counts": {key: len(value) for key, value in classifications.items()},
        "pilot_configuration": pilot_configuration,
        "pilot_configuration_count": len(pilot_configuration),
        "cleanup_plan": cleanup_plan,
        "runway_cross_table": runway_cross_table,
        "global_execution_counts": {
            "outreach_logs": _count(conn, "outreach_logs"),
            "campaign_history": _count(conn, "campaign_history"),
            "shortlists_campaigns": _count(conn, "shortlists"),
            "shortlist_items": _count(conn, "shortlist_items"),
            "solomon_review_history": _count(conn, "solomon_review_history"),
            "action_items": _count(conn, "action_items"),
        },
        "limitations": [
            "This is a current-database audit. Previously hard-deleted rows have no tombstone in this SQLite file and require a dated backup for historical proof.",
            "A deployed/mounted database is a separate audit target; pass its exact path with --db instead of inferring live state from this local file.",
        ],
    }


def _fetch_shortlist_with_count(conn: sqlite3.Connection, row_id: int) -> dict[str, Any] | None:
    if not _table_exists(conn, "shortlists"):
        return None
    row = conn.execute(
        """
        SELECT s.*, count(si.id) AS item_count
        FROM shortlists s
        LEFT JOIN shortlist_items si ON si.shortlist_id = s.id
        WHERE s.id=?
        GROUP BY s.id
        """,
        (row_id,),
    ).fetchone()
    return _dict(row)


def _apply_cleanup(conn: sqlite3.Connection, plan: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    try:
        for action in plan:
            table = action["table"]
            row_id = action["row_id"]
            if table == "solomon_reviews":
                row = _dict(conn.execute("SELECT * FROM solomon_reviews WHERE id=?", (row_id,)).fetchone())
                matched, reason, _ = _known_runway_review_residue(row or {})
                if not matched:
                    skipped.append({**action, "skip_reason": f"fingerprint no longer matches: {reason}"})
                    continue
                conn.execute(
                    """
                    UPDATE solomon_reviews
                    SET decision=NULL,
                        intro_path_confirmed_real=NULL,
                        creator_suggestions_sellable=NULL,
                        missing_info=NULL,
                        solomon_notes=NULL,
                        next_action=NULL,
                        reviewed_at=NULL,
                        updated_at=datetime('now')
                    WHERE id=? AND company_id='company:runway'
                    """,
                    (row_id,),
                )
                applied.append(action)
            elif table == "solomon_review_history":
                row = _dict(conn.execute("SELECT * FROM solomon_review_history WHERE id=?", (row_id,)).fetchone())
                if row is None or row.get("company_id") != "company:runway":
                    skipped.append({**action, "skip_reason": "row missing or no longer belongs to Runway"})
                    continue
                proxy = dict(row)
                proxy["created_at"] = RUNWAY_PILOT_CREATED_PREFIX
                proxy["reviewed_at"] = row.get("recorded_at")
                matched, reason, _ = _known_runway_review_residue(proxy)
                if not matched:
                    skipped.append({**action, "skip_reason": f"fingerprint no longer matches: {reason}"})
                    continue
                conn.execute("DELETE FROM solomon_review_history WHERE id=?", (row_id,))
                applied.append(action)
            elif table == "shortlists":
                row = _fetch_shortlist_with_count(conn, int(row_id))
                fingerprint_id, reason = _runway_shortlist_fingerprint(row or {})
                if fingerprint_id != action.get("fingerprint_id"):
                    skipped.append({**action, "skip_reason": f"fingerprint no longer matches: {reason}"})
                    continue
                conn.execute("DELETE FROM shortlists WHERE id=?", (row_id,))
                applied.append(action)
            else:  # defensive: the planner must never produce broad actions
                skipped.append({**action, "skip_reason": "unsupported cleanup action/table"})
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return applied, skipped


def _render_findings(findings: list[dict[str, Any]]) -> list[str]:
    if not findings:
        return ["(none)"]
    lines = ["| Table | Row | Entity | Decision reason |", "|---|---:|---|---|"]
    for item in findings:
        reason = str(item["reason"]).replace("|", "\\|").replace("\n", " ")
        entity = str(item.get("entity_key") or "").replace("|", "\\|")
        lines.append(f"| `{item['table']}` | `{item['row_id']}` | {entity} | {reason} |")
    return lines


def _render_markdown(report: dict[str, Any]) -> str:
    current = report["current_state"]
    discovered = report["discovered_state"]
    cleanup = report["cleanup"]
    mode = report["mode"]
    lines = [
        "# Test / UAT Data Audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        f"- Database: `{report['database_path']}`",
        f"- Mode: **{mode}**",
        f"- Database mutated by this run: **{str(cleanup['database_mutated']).lower()}**",
        f"- Backup: `{cleanup['backup_path'] or 'not created (no write)'}`",
        "",
        "The audit distinguishes four test-data decisions from a separate",
        "`pilot_configuration` record role. A blank pilot row is not a completed",
        "human review and is not counted as real research evidence.",
        "",
        "## Table scan",
        "",
        "| Table | Exists | Rows |",
        "|---|---:|---:|",
    ]
    for table, stats in current["table_scans"].items():
        lines.append(f"| `{table}` | {str(stats['exists']).lower()} | {stats['row_count']} |")

    for classification in CLASSIFICATIONS:
        lines.extend(["", f"## {classification}", ""])
        lines.extend(_render_findings(discovered["classifications"][classification]))

    lines.extend(["", "## pilot_configuration (kept; not a human review)", ""])
    if current["pilot_configuration"]:
        lines.extend(
            [
                "| Row | Company | Human review status | Decision | Reviewed at |",
                "|---:|---|---|---|---|",
            ]
        )
        for item in current["pilot_configuration"]:
            lines.append(
                f"| `{item['row_id']}` | `{item['company_id']}` | {item['human_review_status']} | "
                f"{item['decision'] or ''} | {item['reviewed_at'] or ''} |"
            )
    else:
        lines.append("(none)")

    lines.extend(["", "## Cleanup", ""])
    lines.append(f"- Planned exact-fingerprint actions: **{len(cleanup['planned'])}**")
    lines.append(f"- Applied: **{len(cleanup['applied'])}**")
    lines.append(f"- Skipped after revalidation: **{len(cleanup['skipped'])}**")
    for item in cleanup["applied"]:
        lines.append(
            f"- Applied `{item['action']}` to `{item['table']}` row `{item['row_id']}` "
            f"(`{item['fingerprint_id']}`)."
        )
    if mode == "dry-run" and cleanup["planned"]:
        lines.append("- Dry run only: rerun with `--apply` to execute these exact actions after an automatic backup.")

    counts = current["global_execution_counts"]
    lines.extend(
        [
            "",
            "## Current execution/campaign state",
            "",
            f"- OutreachLog rows: **{counts['outreach_logs']}**",
            f"- CampaignHistory rows: **{counts['campaign_history']}**",
            f"- Campaign/Shortlist rows: **{counts['shortlists_campaigns']}**",
            f"- Shortlist items: **{counts['shortlist_items']}**",
            f"- Solomon review history rows: **{counts['solomon_review_history']}**",
            f"- ActionItem rows: **{counts['action_items']}**",
            "",
            "`ReviewDecision` is `solomon_reviews.decision`; there is no standalone",
            "ReviewDecision table. A Campaign is stored in `shortlists`;",
            "`campaign_history` is creator-side historical campaign evidence.",
            "",
            "## Safety contract",
            "",
            "- Default invocation never mutates SQLite.",
            "- `--apply` creates a recoverable backup before opening the write transaction.",
            "- Every planned row is fingerprint-revalidated inside the transaction.",
            "- Ordinary empty campaign drafts are reported as `uncertain`, never auto-deleted.",
            "- The eight pilot membership rows are retained.",
            "",
            "## Limitations",
            "",
        ]
    )
    for limitation in current["limitations"]:
        lines.append(f"- {limitation}")
    lines.extend(
        [
            "",
        ]
    )
    return "\n".join(lines)


def _write_reports(report: dict[str, Any], report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "test_data_audit.json"
    md_path = report_dir / "test_data_audit.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, md_path


def run_audit(
    db_path: Path,
    report_dir: Path,
    *,
    apply: bool = False,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    db_path = Path(db_path).resolve()
    report_dir = Path(report_dir).resolve()
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as read_conn:
        discovered = _scan_database(read_conn)

    backup_path: Path | None = None
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if apply and discovered["cleanup_plan"]:
        backup_path = backup_database(
            db_path=db_path,
            backup_dir=Path(backup_dir) if backup_dir else db_path.parent / "backups",
            label="pre_test_data_cleanup",
        )
        with closing(sqlite3.connect(db_path)) as write_conn:
            with write_conn:
                applied, skipped = _apply_cleanup(write_conn, discovered["cleanup_plan"])

    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as read_conn:
        current = _scan_database(read_conn)

    report = {
        "schema_version": "test_data_audit.v2",
        "generated_at": _now_iso(),
        "database_path": str(db_path),
        "mode": "apply" if apply else "dry-run",
        "discovered_state": discovered,
        "current_state": current,
        "cleanup": {
            "planned": discovered["cleanup_plan"],
            "applied": applied,
            "skipped": skipped,
            "database_mutated": bool(applied),
            "backup_path": str(backup_path) if backup_path else None,
        },
    }
    json_path, md_path = _write_reports(report, report_dir)
    report["report_paths"] = {"json": str(json_path), "markdown": str(md_path)}
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply only exact known UAT cleanup actions (default: dry-run).")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help=f"SQLite database (default: {DEFAULT_DB_PATH}).")
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR, help=f"Audit output directory (default: {DEFAULT_REPORT_DIR}).")
    parser.add_argument("--backup-dir", type=Path, default=None, help="Backup directory for --apply (default: DB sibling backups/).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = run_audit(
        args.db,
        args.report_dir,
        apply=args.apply,
        backup_dir=args.backup_dir,
    )
    cleanup = report["cleanup"]
    print(f"mode={report['mode']}")
    print(f"planned={len(cleanup['planned'])} applied={len(cleanup['applied'])} skipped={len(cleanup['skipped'])}")
    if cleanup["backup_path"]:
        print(f"backup={cleanup['backup_path']}")
    print(f"json={report['report_paths']['json']}")
    print(f"markdown={report['report_paths']['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
