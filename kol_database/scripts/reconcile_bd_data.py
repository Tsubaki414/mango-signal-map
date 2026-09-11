"""Post-migration data-integrity reconciliation for the BD-intelligence
tables. Not a dashboard -- a scriptable check with a machine-readable
report, meant to run after every `migrate_bd_data.py` (and periodically in
production) to catch exactly the failure classes that have already bitten
this migration once (the gtm_motion character-by-character join bug) or
could plausibly bite it again (duplicate entities, orphan rows, quotes
silently appearing on unenriched stub creators).

Exit code is 0 if every check passes, 1 if any check has findings -- safe
to wire into a pre-deploy step later.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session  # noqa: E402
from backend.models import (  # noqa: E402
    ActionItem,
    Company,
    CompanySource,
    Creator,
    CreatorExclusion,
    GtmCase,
    IntroPath,
    Operator,
    SocialAccount,
    SolomonReview,
    SponsorshipEvidence,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BD_SQLITE_PATH = REPO_ROOT / "outputs" / "pilot_v4" / "mango_bd_v4.sqlite"
REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "reconciliation_report.json"

# A char-by-char-joined string looks like "M; o; v; e; ..." -- most tokens
# between "; " separators are length <=2. This is the exact shape of the
# gtm_motion bug found and fixed in this repo's history; this regex ports
# that incident into a permanent regression check.
_CHAR_JOIN_PATTERN = re.compile(r"^(?:.{1,2}; ){5,}.{1,2}\.?$")


def _looks_char_joined(value: str | None) -> bool:
    if not value or len(value) < 20:
        return False
    return bool(_CHAR_JOIN_PATTERN.match(value))


def _inventory_delta(source_ids: set[str], destination_ids: set[str]) -> tuple[set[str], set[str]]:
    """Return (missing source rows, unexpected source-backed rows).

    The live cockpit intentionally contains separately sourced, human- or
    research-sprint-added rows on top of the canonical v4 snapshot. Comparing
    total row counts treats every legitimate extension as migration drift.
    Stable source keys let reconciliation ask the real question instead:
    did every canonical row survive, and did a row claim a canonical source
    key that the source no longer contains?
    """
    return source_ids - destination_ids, destination_ids - source_ids


def run() -> dict:
    session = get_session()
    findings: list[dict] = []

    def flag(check: str, severity: str, detail: str, **extra):
        findings.append({"check": check, "severity": severity, "detail": detail, **extra})

    # --- 1. Canonical source membership + local-extension provenance ---
    # The live DB is a superset of the 77-company v4 snapshot: a later,
    # evidence-backed research batch added 12 companies and 19 candidate
    # operators. Total-count equality therefore creates false alarms. Check
    # canonical stable-key membership instead, then independently require
    # provenance on every local extension.
    src_counts: dict[str, int] = {}
    src_ids: dict[str, set[str]] = {}
    if BD_SQLITE_PATH.exists():
        conn = sqlite3.connect(str(BD_SQLITE_PATH))
        conn.row_factory = sqlite3.Row
        src_ids["companies"] = {row["company_id"] for row in conn.execute("SELECT company_id FROM companies")}
        src_ids["operator_identities"] = {
            row["company_id"]
            for row in conn.execute("SELECT company_id FROM operator_identities WHERE operator_name IS NOT NULL")
        }
        src_ids["action_queue"] = {row["action_id"] for row in conn.execute("SELECT action_id FROM action_queue")}
        src_counts["gtm_cases"] = conn.execute("SELECT COUNT(*) c FROM gtm_cases").fetchone()["c"]
        conn.close()
    else:
        flag("source_db_missing", "warning", f"{BD_SQLITE_PATH} not found -- skipping source/destination count comparison.")

    dest_source_ids = {
        "companies": {row.company_id for row in session.query(Company).all()},
        "operator_identities": {
            source_id
            for (source_id,) in session.query(Operator.source_id).filter(Operator.source_id.isnot(None)).all()
        },
        "action_queue": {
            source_id
            for (source_id,) in session.query(ActionItem.source_id).filter(ActionItem.source_id.isnot(None)).all()
        },
    }
    for table, source_keys in src_ids.items():
        missing, unexpected = _inventory_delta(source_keys, dest_source_ids[table])
        if missing:
            flag(
                "canonical_source_rows_missing",
                "error",
                f"destination is missing {len(missing)} canonical row(s) from source table '{table}'.",
                table=table,
                missing_source_ids=sorted(missing),
            )
        # Company has no source_id column, so extra company_ids are the
        # separately governed local extension set checked just below. For
        # source-keyed tables, an unexpected non-NULL source_id really is a
        # migration/provenance error.
        if unexpected and table != "companies":
            flag(
                "unexpected_canonical_source_id",
                "error",
                f"destination has {len(unexpected)} source-backed row(s) absent from canonical table '{table}'.",
                table=table,
                unexpected_source_ids=sorted(unexpected),
            )

    if "gtm_cases" in src_counts:
        dest_gtm_count = session.query(GtmCase).count()
        if dest_gtm_count != src_counts["gtm_cases"]:
            flag(
                "authoritative_row_count_mismatch",
                "error",
                f"source table 'gtm_cases' has {src_counts['gtm_cases']} rows but destination has {dest_gtm_count}.",
                table="gtm_cases",
                source_count=src_counts["gtm_cases"],
                dest_count=dest_gtm_count,
            )

    canonical_company_ids = src_ids.get("companies", set())
    local_companies = session.query(Company).filter(~Company.company_id.in_(canonical_company_ids)).all() if canonical_company_ids else []
    for company in local_companies:
        source_count = session.query(CompanySource).filter(CompanySource.company_id == company.company_id).count()
        if company.stage != "agent_discovered" or source_count == 0:
            flag(
                "local_company_missing_provenance",
                "error",
                f"Local company {company.company_id!r} is outside canonical v4 but lacks the required agent_discovered stage and/or CompanySource row.",
                company_id=company.company_id,
                stage=company.stage,
                source_count=source_count,
            )

    local_operators = session.query(Operator).filter(Operator.source_id.is_(None)).all()
    for operator in local_operators:
        if not operator.evidence_urls and not operator.last_verified_at:
            flag(
                "local_operator_missing_provenance",
                "error",
                f"Local operator {operator.id} ({operator.name or 'unnamed'}) has neither evidence_urls nor a human verification timestamp.",
                operator_id=operator.id,
                company_id=operator.company_id,
            )

    # --- 2. Duplicate companies ---
    companies = session.query(Company).all()
    names_seen: dict[str, list[str]] = {}
    for c in companies:
        names_seen.setdefault(c.name.strip().lower(), []).append(c.company_id)
    for name, ids in names_seen.items():
        if len(ids) > 1:
            flag("duplicate_company_name", "error", f'"{name}" appears as {len(ids)} separate Company rows.', company_ids=ids)

    # --- 3. Duplicate creators / handles ---
    accounts = session.query(SocialAccount).all()
    handle_seen: dict[tuple[str, str], list[int]] = {}
    for a in accounts:
        if not a.handle:
            continue
        key = (a.platform, a.handle.strip().lower())
        handle_seen.setdefault(key, []).append(a.creator_id)
    for (platform, handle), creator_ids in handle_seen.items():
        unique_creators = set(creator_ids)
        if len(unique_creators) > 1:
            flag(
                "duplicate_handle_across_creators",
                "error",
                f"{platform}:{handle} is attached to {len(unique_creators)} different Creator rows -- entity resolution should have merged these.",
                platform=platform,
                handle=handle,
                creator_ids=sorted(unique_creators),
            )

    # --- 4. Orphan rows (company_id / creator_id pointing nowhere) ---
    company_ids = {c.company_id for c in companies}
    creator_ids = {c.id for c in session.query(Creator).all()}

    for row in session.query(SponsorshipEvidence).all():
        if row.company_id not in company_ids:
            flag("orphan_sponsorship_evidence", "error", f"SponsorshipEvidence {row.id} references missing company_id {row.company_id!r}.", id=row.id)
        if row.creator_id is not None and row.creator_id not in creator_ids:
            flag("orphan_sponsorship_evidence_creator", "error", f"SponsorshipEvidence {row.id} references missing creator_id {row.creator_id!r}.", id=row.id)
    for row in session.query(IntroPath).all():
        if row.company_id not in company_ids:
            flag("orphan_intro_path", "error", f"IntroPath {row.id} references missing company_id {row.company_id!r}.", id=row.id)
    for row in session.query(ActionItem).all():
        if row.company_id not in company_ids:
            flag("orphan_action_item", "error", f"ActionItem {row.id} references missing company_id {row.company_id!r}.", id=row.id)
    for row in session.query(Operator).all():
        if row.company_id not in company_ids:
            flag("orphan_operator", "error", f"Operator {row.id} references missing company_id {row.company_id!r}.", id=row.id)
    for row in session.query(SolomonReview).all():
        if row.company_id not in company_ids:
            flag("orphan_solomon_review", "error", f"SolomonReview {row.id} references missing company_id {row.company_id!r}.", id=row.id)
    for row in session.query(CreatorExclusion).all():
        if row.creator_id not in creator_ids:
            flag("orphan_creator_exclusion", "error", f"CreatorExclusion {row.id} references missing creator_id {row.creator_id!r}.", id=row.id)
        if row.company_id is not None and row.company_id not in company_ids:
            flag("orphan_creator_exclusion_company", "error", f"CreatorExclusion {row.id} references missing company_id {row.company_id!r}.", id=row.id)

    # --- 4b. Duplicate source_id -- the exact shape of the delete-and-reinsert
    # bug this migration used to have (upsert-by-source_id now prevents it,
    # this is the regression guard). ---
    for model, label in ((Operator, "operator"), (IntroPath, "intro_path"), (ActionItem, "action_item"), (SponsorshipEvidence, "sponsorship_evidence")):
        seen: dict[str, int] = {}
        for row in session.query(model).filter(model.source_id.isnot(None)).all():
            if row.source_id in seen:
                flag(
                    "duplicate_source_id",
                    "error",
                    f"{label} source_id {row.source_id!r} appears on rows {seen[row.source_id]} and {row.id} -- migration upsert-by-key is not deduplicating correctly.",
                    model=label,
                    source_id=row.source_id,
                )
            seen[row.source_id] = row.id

    # --- 5. Character-by-character join corruption (the gtm_motion regression) ---
    for row in session.query(GtmCase).all():
        for field in ("gtm_motion", "what_mango_should_copy", "what_not_to_copy"):
            value = getattr(row, field)
            if _looks_char_joined(value):
                flag("char_join_corruption", "error", f"GtmCase {row.id}.{field} looks character-by-character joined.", id=row.id, field=field, sample=value[:80])
    for row in session.query(ActionItem).all():
        for field in ("primary_next_action", "fallback", "success_condition"):
            value = getattr(row, field)
            if _looks_char_joined(value):
                flag("char_join_corruption", "error", f"ActionItem {row.id}.{field} looks character-by-character joined.", id=row.id, field=field, sample=(value or "")[:80])
    for row in session.query(IntroPath).all():
        if _looks_char_joined(row.path_labels):
            flag("char_join_corruption", "error", f"IntroPath {row.id}.path_labels looks character-by-character joined.", id=row.id, sample=row.path_labels[:80])

    # --- 6. Unenriched / BD-created creators must not have a priced rate card ---
    bd_created = session.query(Creator).filter(Creator.source_files.is_(None)).all()
    for c in bd_created:
        priced = [rc for rc in c.rate_cards if rc.quote_amount_usd is not None]
        if priced:
            flag(
                "fabricated_quote_on_unenriched_creator",
                "error",
                f"Creator {c.id} ({c.display_name}) was auto-created from BD sponsorship evidence (no source_files) but has {len(priced)} priced rate card(s) -- no quote should exist for a creator Mango has never actually quoted.",
                creator_id=c.id,
            )

    # --- 7. Summary counts (informational, not findings) ---
    summary = {
        "companies": len(companies),
        "creators_total": len(creator_ids),
        "creators_bd_created": len(bd_created),
        "creators_bd_created_still_unknown": sum(1 for c in bd_created if c.creator_class == "Unknown"),
        "creators_bd_created_classified": sum(1 for c in bd_created if c.creator_class != "Unknown"),
        "operators": session.query(Operator).count(),
        "operators_canonical_source_backed": len(dest_source_ids["operator_identities"]),
        "operators_local_research_or_human": len(local_operators),
        "operators_confirmed": session.query(Operator).filter(Operator.identity_confirmed.is_(True)).count(),
        "intro_paths": session.query(IntroPath).count(),
        "action_items": session.query(ActionItem).count(),
        "sponsorship_evidence": session.query(SponsorshipEvidence).count(),
        "sponsorship_evidence_paid": session.query(SponsorshipEvidence).filter(SponsorshipEvidence.disclosure_type == "paid_sponsorship").count(),
        "gtm_cases": session.query(GtmCase).count(),
        "companies_canonical_source_backed": len(canonical_company_ids & dest_source_ids["companies"]),
        "companies_local_research": len(local_companies),
        "companies_missing_category_or_geography": session.query(Company).filter((Company.category.is_(None)) | (Company.geography.is_(None))).count(),
    }

    session.close()

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": not any(f["severity"] == "error" for f in findings),
        "summary": summary,
        "findings": findings,
    }
    return report


def main() -> int:
    report = run()
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Reconciliation {'PASSED' if report['ok'] else 'FAILED'} -- {len(report['findings'])} finding(s).")
    print("Summary:")
    for k, v in report["summary"].items():
        print(f"  {k}: {v}")
    if report["findings"]:
        print("\nFindings:")
        for f in report["findings"]:
            print(f"  [{f['severity'].upper()}] {f['check']}: {f['detail']}")
    print(f"\nFull report written to {REPORT_PATH}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
