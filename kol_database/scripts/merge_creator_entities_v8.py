#!/usr/bin/env python3
"""Merge exact cross-platform duplicate creator entities.

This deliberately does not use fuzzy matching. A group qualifies only when
every row has the exact same normalized display name and public business
email. Old creator ids are preserved in ``creator_entity_aliases`` so saved
links resolve to the canonical entity.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
from pathlib import Path


CLASS_PRIORITY = {
    "Top KOL": 7,
    "KOL": 6,
    "Community Leader": 5,
    "KOC": 4,
    "Marketing Account": 3,
    "Media / Community Account": 2,
    "Unknown": 0,
}


def _csv_union(values: list[str | None]) -> str | None:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in (value or "").split(","):
            clean = item.strip()
            if clean and clean.casefold() not in seen:
                result.append(clean)
                seen.add(clean.casefold())
    return ",".join(result) or None


def merge(db_path: Path) -> dict[str, int]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    merged_groups = 0
    retired_ids = 0
    with connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS creator_entity_aliases (
                alias_creator_id INTEGER PRIMARY KEY,
                canonical_creator_id INTEGER NOT NULL,
                alias_display_name VARCHAR(255) NOT NULL,
                merge_basis TEXT NOT NULL,
                merged_at DATETIME NOT NULL,
                FOREIGN KEY(canonical_creator_id) REFERENCES creators(id)
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS ix_creator_entity_aliases_canonical_creator_id "
            "ON creator_entity_aliases(canonical_creator_id)"
        )
        groups = connection.execute(
            """SELECT lower(trim(c.display_name)) AS normalized_name,
                      lower(trim(cm.value)) AS normalized_email,
                      group_concat(DISTINCT c.id) AS creator_ids,
                      count(DISTINCT c.id) AS creator_count
               FROM creators c
               JOIN contact_methods cm
                 ON cm.creator_id=c.id AND lower(cm.method_type)='email'
               GROUP BY normalized_name, normalized_email
               HAVING creator_count > 1"""
        ).fetchall()
        for group in groups:
            ids = sorted(int(item) for item in group["creator_ids"].split(","))
            placeholders = ",".join("?" for _ in ids)
            creators = connection.execute(
                f"SELECT * FROM creators WHERE id IN ({placeholders})", ids
            ).fetchall()
            priced_counts = {
                row["creator_id"]: row["n"]
                for row in connection.execute(
                    f"SELECT creator_id, count(*) n FROM rate_cards "
                    f"WHERE creator_id IN ({placeholders}) AND quote_amount_usd IS NOT NULL "
                    "GROUP BY creator_id",
                    ids,
                )
            }
            canonical = max(
                creators,
                key=lambda row: (
                    priced_counts.get(row["id"], 0),
                    CLASS_PRIORITY.get(row["creator_class"], 0),
                    -row["id"],
                ),
            )
            canonical_id = canonical["id"]
            aliases = [row for row in creators if row["id"] != canonical_id]
            best_class = max(
                (row["creator_class"] for row in creators),
                key=lambda value: CLASS_PRIORITY.get(value, 0),
            )
            best_confidence = "High" if any(row["classification_confidence"] == "High" for row in creators) else "Medium"
            connection.execute(
                """UPDATE creators
                   SET creator_class=?, creator_class_reason=?, creator_class_source=?,
                       classification_confidence=?, region=?, language=?, categories=?,
                       source_files=?, updated_at=?
                   WHERE id=?""",
                (
                    best_class,
                    "跨平台实体合并：公开名称与 business email 完全一致；平台账号保留为同一 creator 的多个账户。",
                    "entity_merge_v8",
                    best_confidence,
                    _csv_union([row["region"] for row in creators]),
                    _csv_union([row["language"] for row in creators]),
                    _csv_union([row["categories"] for row in creators]),
                    _csv_union([row["source_files"] for row in creators]),
                    dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(),
                    canonical_id,
                ),
            )
            for alias in aliases:
                alias_id = alias["id"]
                connection.execute(
                    """INSERT OR IGNORE INTO creator_entity_aliases
                       (alias_creator_id, canonical_creator_id, alias_display_name, merge_basis, merged_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        alias_id,
                        canonical_id,
                        alias["display_name"],
                        "exact_normalized_display_name_and_public_business_email",
                        dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(),
                    ),
                )
                duplicate_contacts = connection.execute(
                    "SELECT id, method_type, value FROM contact_methods WHERE creator_id=?",
                    (alias_id,),
                ).fetchall()
                for contact in duplicate_contacts:
                    existing = connection.execute(
                        """SELECT 1 FROM contact_methods
                           WHERE creator_id=? AND lower(method_type)=lower(?) AND lower(value)=lower(?)""",
                        (canonical_id, contact["method_type"], contact["value"]),
                    ).fetchone()
                    if existing:
                        connection.execute("DELETE FROM contact_methods WHERE id=?", (contact["id"],))
                    else:
                        connection.execute(
                            "UPDATE contact_methods SET creator_id=? WHERE id=?",
                            (canonical_id, contact["id"]),
                        )
                for table in (
                    "social_accounts",
                    "rate_cards",
                    "campaign_history",
                    "sponsorship_evidence",
                    "shortlist_items",
                    "creator_exclusions",
                ):
                    connection.execute(
                        f"UPDATE {table} SET creator_id=? WHERE creator_id=?",
                        (canonical_id, alias_id),
                    )
                connection.execute("DELETE FROM creators WHERE id=?", (alias_id,))
                retired_ids += 1
            merged_groups += 1
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key violations after creator merge: {violations[:3]}")
    connection.close()
    return {"merged_groups": merged_groups, "retired_creator_ids": retired_ids}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    print(merge(args.db))


if __name__ == "__main__":
    main()
