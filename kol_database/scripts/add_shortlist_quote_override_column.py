"""Idempotently add the campaign-only quote override to old DB volumes."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import DB_PATH  # noqa: E402


def ensure_shortlist_quote_override_column(db_path: str | Path = DB_PATH) -> bool:
    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(shortlist_items)")
        }
        if "quote_usd_override" in columns:
            return False
        connection.execute(
            "ALTER TABLE shortlist_items ADD COLUMN quote_usd_override FLOAT"
        )
        connection.commit()
    return True


def main() -> int:
    changed = ensure_shortlist_quote_override_column()
    print(
        "Added shortlist_items.quote_usd_override."
        if changed
        else "shortlist_items.quote_usd_override already present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
