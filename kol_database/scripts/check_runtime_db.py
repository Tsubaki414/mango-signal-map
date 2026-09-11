"""Validate a Mango cockpit SQLite database without mutating it.

The release image uses this twice: an exact contract for the baked seed and
a minimum-compatible contract for a persistent runtime volume.  The latter
is deliberately fail-closed.  An older production volume must be migrated
and validated explicitly; it must never be silently replaced by the seed.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.environ.get("DB_PATH", APP_ROOT / "data" / "kol.db"))

LEGACY_PREFLIGHT_COUNTS = {
    "companies": 89,
    "operators": 29,
    "intro_paths": 640,
    "action_items": 42,
    "sponsorship_evidence": 64,
}

RELEASE_COUNTS = {
    "companies": 99,
    "operators": 94,
    "intro_paths": 640,
    "action_items": 42,
    "sponsorship_evidence": 93,
    "company_longlist_assessments": 97,
    "company_research_dossiers": 15,
    "company_contact_routes": 45,
    "company_commercial_evidence": 97,
    "company_decisions": 15,
    "company_asia_profiles": 15,
    "company_asia_evidence": 8,
    "company_sales_packets": 5,
    "company_buyer_map_entries": 25,
}

# The audited seed includes additional historical Apify candidate rows from
# exploratory runs.  An older production volume receives the deliberately
# narrower deploy import (up to three current-role candidates per company),
# so the safe runtime floor is lower while every other release surface must
# still reach the exact audited baseline.
RUNTIME_MIN_COUNTS = {
    **RELEASE_COUNTS,
    "operators": 73,
}

REQUIRED_TABLES = {
    "companies",
    "operators",
    "intro_paths",
    "action_items",
    "sponsorship_evidence",
    "creators",
    "contact_methods",
    "shortlists",
    "solomon_reviews",
    "outreach_logs",
}

COMMERCIAL_REQUIRED_TABLES = {
    "company_longlist_assessments",
    "company_research_dossiers",
    "company_contact_routes",
    "company_commercial_evidence",
    "company_decisions",
    "company_asia_profiles",
    "company_asia_evidence",
    "company_sales_packets",
    "company_buyer_map_entries",
}

REQUIRED_RELEASE_COLUMNS = {
    "intro_paths": {
        "edge_directions",
        "direction_data_unavailable",
        "direction_data_unavailable_reason",
    },
    "shortlist_items": {"quote_usd_override"},
    "shortlists": {"pricing_assumptions_json"},
}


class ContractError(RuntimeError):
    """Raised when a database is unsafe or incompatible with this release."""


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)


def validate_database(
    db_path: Path,
    *,
    exact_seed: bool,
    preflight: bool = False,
) -> dict[str, int]:
    db_path = Path(db_path)
    if not db_path.is_file() or db_path.stat().st_size == 0:
        raise ContractError(f"Database is missing or empty: {db_path}")

    with _open_read_only(db_path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ContractError("SQLite integrity_check failed")

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required_tables = REQUIRED_TABLES if preflight else REQUIRED_TABLES | COMMERCIAL_REQUIRED_TABLES
        missing_tables = sorted(required_tables - tables)
        if missing_tables:
            raise ContractError(f"Missing required tables: {', '.join(missing_tables)}")

        if not preflight:
            for table, required_columns in REQUIRED_RELEASE_COLUMNS.items():
                columns = {
                    row[1]
                    for row in connection.execute(f'PRAGMA table_info("{table}")')
                }
                missing_columns = sorted(required_columns - columns)
                if missing_columns:
                    raise ContractError(
                        f"Missing {table} columns: {', '.join(missing_columns)}"
                    )

        expected_counts = (
            LEGACY_PREFLIGHT_COUNTS
            if preflight
            else RELEASE_COUNTS
            if exact_seed
            else RUNTIME_MIN_COUNTS
        )
        counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in expected_counts
        }
        for table, expected in expected_counts.items():
            actual = counts[table]
            valid = actual == expected if exact_seed else actual >= expected
            relation = "exactly" if exact_seed else "at least"
            if not valid:
                raise ContractError(
                    f"{table} has {actual} rows; release requires {relation} {expected}"
                )

        if not preflight:
            direction_payloads = connection.execute(
                "SELECT COUNT(*) FROM intro_paths WHERE edge_directions IS NOT NULL"
            ).fetchone()[0]
            if direction_payloads != counts["intro_paths"]:
                raise ContractError(
                    "Every intro path must carry an edge_directions payload, including "
                    "an explicit unavailable marker"
                )

            missing_reasons = connection.execute(
                """
                SELECT COUNT(*)
                FROM intro_paths
                WHERE direction_data_unavailable = 1
                  AND (
                    direction_data_unavailable_reason IS NULL
                    OR TRIM(direction_data_unavailable_reason) = ''
                  )
                """
            ).fetchone()[0]
            if missing_reasons:
                raise ContractError(
                    f"{missing_reasons} unavailable direction rows have no reason"
                )

        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_violations:
            raise ContractError(
                f"Foreign-key check found {len(foreign_key_violations)} violation(s)"
            )

    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--seed-contract",
        action="store_true",
        help="Require the exact audited release-seed row counts.",
    )
    mode.add_argument(
        "--runtime-contract",
        action="store_true",
        help="Require a compatible persistent DB with at least the audited baseline.",
    )
    mode.add_argument(
        "--preflight-contract",
        action="store_true",
        help=(
            "Before migrations: require integrity, base tables/counts, and "
            "foreign keys, but allow release columns that migrations add."
        ),
    )
    args = parser.parse_args(argv)

    try:
        counts = validate_database(
            args.db,
            exact_seed=args.seed_contract,
            preflight=args.preflight_contract,
        )
    except (ContractError, sqlite3.DatabaseError, OSError) as exc:
        print(f"Database contract FAILED: {exc}", file=sys.stderr)
        return 1

    summary = ", ".join(f"{table}={count}" for table, count in counts.items())
    mode_name = (
        "seed"
        if args.seed_contract
        else "preflight"
        if args.preflight_contract
        else "runtime"
    )
    print(f"Database contract PASSED ({mode_name}): {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
