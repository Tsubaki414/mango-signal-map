"""Add independent human-verification timestamps to operators.

Idempotent and safe on every boot. Existing ``last_verified_at`` values are
not backfilled because a historical generic edit does not prove which fact a
person actually checked.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import DB_PATH  # noqa: E402


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(operators)")}
        for name in (
            "identity_human_verified_at",
            "role_human_verified_at",
            "budget_authority_verified_at",
        ):
            if name in columns:
                print(f"operators: {name} already present, skipped")
                continue
            conn.execute(f"ALTER TABLE operators ADD COLUMN {name} DATETIME")
            print(f"operators: added {name}")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
