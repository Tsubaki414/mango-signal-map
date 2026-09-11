#!/usr/bin/env python3
"""Idempotently apply the safe subset of Apify candidates to the Cockpit DB.

Every inserted operator remains identity-unconfirmed. Every inserted commercial
observation remains unreviewed. Brand-attribution holds are never imported.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB = ROOT / "kol_database" / "deploy_seed.db"
DEFAULT_OPERATORS = ROOT / "data" / "pilot_v5" / "apify_operator_candidates_v1.json"
DEFAULT_SPONSORSHIPS = ROOT / "data" / "pilot_v5" / "apify_sponsorship_candidates_v1.json"
DEFAULT_CREATORS = ROOT / "data" / "pilot_v5" / "apify_creator_expansion_candidates_v1.json"


def now_db() -> str:
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0).isoformat(sep=" ")


def youtube_handle(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"youtube\.com/(?:@|c/|user/)([^/?#]+)", url, re.I)
    return unquote(match.group(1)).lstrip("@") if match else None


def instagram_handle(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if "instagram.com" not in parsed.netloc.casefold():
        return None
    return unquote(parsed.path.strip("/").split("/")[0]) or None


def find_creator(connection: sqlite3.Connection, name: str, channel_url: str | None) -> int | None:
    handle = youtube_handle(channel_url)
    if handle:
        row = connection.execute(
            "SELECT creator_id FROM social_accounts WHERE platform = 'YouTube' AND lower(handle) = lower(?)",
            (handle,),
        ).fetchone()
        if row:
            return int(row[0])
    row = connection.execute(
        "SELECT id FROM creators WHERE lower(trim(display_name)) = lower(trim(?))",
        (name,),
    ).fetchone()
    return int(row[0]) if row else None


def operator_priority(row: dict) -> tuple[int, str]:
    """Prefer the smallest set of roles closest to Mango's actual buyer."""

    title = str(row.get("current_role") or "").casefold()
    score = 0
    for value, terms in (
        (104, ("creator partnership", "influencer", "affiliate", "partner marketing")),
        (92, ("partnership", "business development", "ecosystem")),
        (88, ("community", "developer relations", "devrel")),
        (84, ("growth marketing", "performance marketing")),
        (78, ("product marketing", "field marketing")),
        (68, ("marketing", "growth")),
    ):
        if any(term in title for term in terms):
            score = max(score, value)
    if any(term in title for term in ("apac", "asia", "japan", "korea", "china", "india", "regional")):
        score += 8
    if any(term in title for term in ("public sector", "policy", "gsi partnership")):
        score -= 30
    if any(term in title for term in ("positions", "member of marketing staff")):
        score -= 20
    return score, str(row.get("name") or "").casefold()


def select_operator_rows(rows: list[dict], max_per_company: int) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("company_id") or ""), []).append(row)
    selected = []
    for company_id in sorted(grouped):
        selected.extend(
            sorted(grouped[company_id], key=lambda row: (-operator_priority(row)[0], operator_priority(row)[1]))[
                :max_per_company
            ]
        )
    return selected


def create_pending_creator(
    connection: sqlite3.Connection,
    *,
    name: str,
    channel_url: str | None,
    subscribers: int | None,
) -> int:
    handle = youtube_handle(channel_url)
    timestamp = now_db()
    cursor = connection.execute(
        """
        INSERT INTO creators (
            display_name, primary_handle, language, categories,
            creator_class, creator_class_reason, creator_class_source,
            creator_class_locked, classification_confidence,
            promotion_level_source, promotion_level_locked,
            internal_notes, source_files, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            handle,
            None,
            "AI productivity, presentation software, YouTube",
            "Unknown",
            "Apify surfaced a brand-attributed Gamma commercial observation; entity and campaign fit need human review.",
            "auto",
            0,
            "Medium",
            "unset",
            0,
            "Imported as a pending creator candidate; no outreach willingness, geography, or rate is inferred.",
            "apify_sponsorship_candidates_v1.json",
            timestamp,
            timestamp,
        ),
    )
    creator_id = int(cursor.lastrowid)
    connection.execute(
        """
        INSERT INTO social_accounts (
            creator_id, platform, platform_raw, handle, profile_url,
            followers, followers_source, last_enriched_at
        ) VALUES (?, 'YouTube', 'YouTube', ?, ?, ?, 'apify', ?)
        """,
        (creator_id, handle, channel_url, subscribers, timestamp),
    )
    return creator_id


def apply_rows(
    connection: sqlite3.Connection,
    *,
    operators: list[dict],
    sponsorships: list[dict],
    creators: list[dict],
) -> dict[str, int]:
    stats = {
        "operators_created": 0,
        "operators_existing": 0,
        "creators_created_pending_review": 0,
        "creators_matched": 0,
        "instagram_accounts_added": 0,
        "sponsorships_created_unreviewed": 0,
        "sponsorships_existing": 0,
        "sponsorships_held_for_attribution": 0,
    }
    for row in operators:
        existing = connection.execute(
            "SELECT id FROM operators WHERE company_id = ? AND lower(trim(name)) = lower(trim(?))",
            (row["company_id"], row["name"]),
        ).fetchone()
        if existing:
            stats["operators_existing"] += 1
            continue
        connection.execute(
            """
            INSERT INTO operators (
                company_id, source_id, name, role, identity_confirmed,
                identity_status, budget_authority_confirmed, evidence_urls
            ) VALUES (?, ?, ?, ?, 0, ?, 0, ?)
            """,
            (
                row["company_id"],
                row["observation_id"],
                row["name"],
                row.get("current_role"),
                "public_linkedin_current_role_candidate_needs_first_party_or_x_confirmation",
                row.get("public_profile_url"),
            ),
        )
        stats["operators_created"] += 1

    creator_export = {row.get("creator"): row for row in creators}
    creator_ids: dict[str, int] = {}
    for row in sponsorships:
        if row.get("import_action") == "hold_for_brand_attribution_review":
            stats["sponsorships_held_for_attribution"] += 1
            continue
        # ``existing_sponsorship`` is only an export-time observation against
        # the research DB.  Production may be an older persistent volume, so
        # idempotency must be decided from the target DB at apply time.
        if connection.execute(
            "SELECT 1 FROM sponsorship_evidence WHERE content_url = ? OR source_id = ?",
            (row["content_url"], row["observation_id"]),
        ).fetchone():
            stats["sponsorships_existing"] += 1
            continue
        name = row.get("creator") or "Unknown creator"
        creator_id = find_creator(connection, name, row.get("channel_url"))
        if creator_id:
            stats["creators_matched"] += 1
        else:
            metrics = row.get("metrics") or {}
            creator_id = create_pending_creator(
                connection,
                name=name,
                channel_url=row.get("channel_url"),
                subscribers=metrics.get("subscribers"),
            )
            stats["creators_created_pending_review"] += 1
        creator_ids[name] = creator_id
        snippets = row.get("disclosure_snippets") or []
        evidence_text = " | ".join(snippets[:5])
        if row.get("brand_link_candidates"):
            evidence_text = f"{evidence_text} | Brand-link candidates: {', '.join(row['brand_link_candidates'])}".strip(" |")
        disclosure = row.get("disclosure_type_candidate") or "unknown_commercial_relationship"
        confidence = 0.85 if disclosure == "paid_sponsorship" else 0.72 if disclosure == "affiliate" else 0.55
        connection.execute(
            """
            INSERT INTO sponsorship_evidence (
                company_id, creator_id, creator_name_raw, creator_handle_raw,
                platform, content_url, content_title, published_at,
                disclosure_type, evidence_text, confidence, review_status,
                source_id, created_at
            ) VALUES (?, ?, ?, ?, 'YouTube', ?, ?, ?, ?, ?, ?, 'unreviewed', ?, ?)
            """,
            (
                row["company_id"],
                creator_id,
                name[:255],
                youtube_handle(row.get("channel_url")),
                row["content_url"],
                row.get("content_title"),
                row.get("published_at"),
                disclosure,
                evidence_text or "Apify commercial candidate; inspect the source URL before confirming.",
                confidence,
                row["observation_id"],
                now_db(),
            ),
        )
        stats["sponsorships_created_unreviewed"] += 1

    for name, creator_id in creator_ids.items():
        creator = creator_export.get(name) or {}
        for profile_url in creator.get("instagram_profiles") or []:
            handle = instagram_handle(profile_url)
            if not handle:
                continue
            exists = connection.execute(
                "SELECT 1 FROM social_accounts WHERE platform = 'Instagram' AND lower(handle) = lower(?)",
                (handle,),
            ).fetchone()
            if exists:
                continue
            connection.execute(
                """
                INSERT INTO social_accounts (
                    creator_id, platform, platform_raw, handle, profile_url, followers_source
                ) VALUES (?, 'Instagram', 'Instagram', ?, ?, 'apify_explicit_youtube_link')
                """,
                (creator_id, handle, profile_url),
            )
            stats["instagram_accounts_added"] += 1
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--operators", type=Path, default=DEFAULT_OPERATORS)
    parser.add_argument("--sponsorships", type=Path, default=DEFAULT_SPONSORSHIPS)
    parser.add_argument("--creators", type=Path, default=DEFAULT_CREATORS)
    parser.add_argument("--max-operators-per-company", type=int, default=3)
    parser.add_argument("--apply", action="store_true", help="commit changes; default is rollback dry-run")
    args = parser.parse_args()
    if args.max_operators_per_company < 1:
        raise SystemExit("--max-operators-per-company must be positive")
    operator_rows = select_operator_rows(
        json.loads(args.operators.read_text(encoding="utf-8"))["operators"],
        args.max_operators_per_company,
    )
    sponsorship_rows = json.loads(args.sponsorships.read_text(encoding="utf-8"))["sponsorships"]
    creator_rows = json.loads(args.creators.read_text(encoding="utf-8"))["creators"]
    connection = sqlite3.connect(args.db)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        stats = apply_rows(
            connection,
            operators=operator_rows,
            sponsorships=sponsorship_rows,
            creators=creator_rows,
        )
        if args.apply:
            connection.commit()
        else:
            connection.rollback()
        print(json.dumps({"mode": "applied" if args.apply else "dry_run", "db": str(args.db), **stats}, indent=2))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
