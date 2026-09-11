"""One-time migration: add Operator.can_dm/can_dm_checked_at columns and
create the intro_bridges table (mutual-follow bridge-person candidates).
Idempotent -- safe to run on every boot, same pattern as
add_created_at_columns.py.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import DB_PATH, init_db  # noqa: E402


def main():
    conn = sqlite3.connect(DB_PATH)

    cols = [r[1] for r in conn.execute("PRAGMA table_info(operators)").fetchall()]
    if "can_dm" not in cols:
        conn.execute("ALTER TABLE operators ADD COLUMN can_dm BOOLEAN")
        print("operators: added can_dm")
    else:
        print("operators: can_dm already present, skipped")
    if "can_dm_checked_at" not in cols:
        conn.execute("ALTER TABLE operators ADD COLUMN can_dm_checked_at DATETIME")
        print("operators: added can_dm_checked_at")
    else:
        print("operators: can_dm_checked_at already present, skipped")

    conn.commit()
    conn.close()

    init_db()  # creates intro_bridges (new table, no ALTER needed)
    print("intro_bridges: ensured via create_all()")


if __name__ == "__main__":
    main()
