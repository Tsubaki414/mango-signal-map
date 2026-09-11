"""One-time migration: add created_at to action_items and sponsorship_evidence
(needed for the "new since your last visit" home-page indicator), and create
the solomon_review_history table (decision audit trail).

Existing rows are backfilled to the time this script runs -- there's no real
historical creation date for them, and backfilling to "now" means nothing
already on file falsely shows as new; only genuinely new rows from this
point on carry a real date.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import DB_PATH, init_db  # noqa: E402


def main():
    conn = sqlite3.connect(DB_PATH)
    now = conn.execute("SELECT datetime('now')").fetchone()[0]

    for table in ("action_items", "sponsorship_evidence"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if "created_at" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN created_at DATETIME")
            conn.execute(f"UPDATE {table} SET created_at = ? WHERE created_at IS NULL", (now,))
            print(f"{table}: added + backfilled created_at")
        else:
            print(f"{table}: created_at already present, skipped")

    conn.commit()
    conn.close()

    init_db()  # creates solomon_review_history (new table, no ALTER needed)
    print("solomon_review_history: ensured via create_all()")


if __name__ == "__main__":
    main()
