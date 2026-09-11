"""Idempotently add explicit commercial assumptions to campaign shortlists."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import DB_PATH  # noqa: E402


def ensure_column(db_path: str | Path = DB_PATH) -> bool:
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(shortlists)")}
        if "pricing_assumptions_json" in columns:
            return False
        connection.execute("ALTER TABLE shortlists ADD COLUMN pricing_assumptions_json TEXT")
        connection.commit()
    return True


def main() -> int:
    changed = ensure_column()
    print("Added shortlists.pricing_assumptions_json." if changed else "shortlists.pricing_assumptions_json already present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
