#!/usr/bin/env python3
"""Apply the sourced v8 creator review without overwriting human work."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

DEFAULT_SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "pilot_v8" / "creator_classification_review_v8.json"


def apply(db_path: Path, snapshot_path: Path) -> dict[str, int]:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    updated = 0
    skipped = 0
    with connection:
        for item in payload["rows"]:
            creator = connection.execute(
                "SELECT * FROM creators WHERE id=?", (item["creator_id"],)
            ).fetchone()
            if (
                creator is None
                or creator["display_name"] != item["display_name"]
                or creator["creator_class"] != "Unknown"
                or creator["creator_class_locked"]
            ):
                skipped += 1
                continue
            connection.execute(
                """UPDATE creators
                   SET creator_class=?, creator_class_reason=?, creator_class_source=?,
                       classification_confidence=?, categories=?, updated_at=?
                   WHERE id=?""",
                (
                    item["creator_class"],
                    item["reason"],
                    "public_review_v8",
                    item["confidence"],
                    ",".join(item["categories"]),
                    dt.datetime.now(dt.UTC).replace(tzinfo=None).isoformat(),
                    item["creator_id"],
                ),
            )
            account = connection.execute(
                """SELECT id FROM social_accounts
                   WHERE creator_id=? AND platform='YouTube'
                   ORDER BY id LIMIT 1""",
                (item["creator_id"],),
            ).fetchone()
            if account:
                connection.execute(
                    """UPDATE social_accounts
                       SET content_summary=CASE
                             WHEN content_summary IS NULL OR trim(content_summary)='' THEN ?
                             ELSE content_summary END,
                           followers=CASE WHEN ? IS NOT NULL THEN ? ELSE followers END,
                           followers_source=CASE WHEN ? IS NOT NULL THEN 'public_page' ELSE followers_source END
                       WHERE id=?""",
                    (
                        item["content_summary"],
                        item.get("public_followers"),
                        item.get("public_followers"),
                        item.get("public_followers"),
                        account["id"],
                    ),
                )
            updated += 1
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key violations after creator review: {violations[:3]}")
    connection.close()
    return {"updated": updated, "skipped": skipped}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    args = parser.parse_args()
    print(apply(args.db, args.snapshot))


if __name__ == "__main__":
    main()
