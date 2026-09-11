"""One-time migration: create the outreach_logs table (real-world funnel
events -- contact attempted, connector replied, meeting booked, etc.) and
add interaction-evidence columns to intro_bridges (has the bridge and
target ever actually replied to/mentioned each other on X, not just
mutually followed). Idempotent -- safe to run on every boot, same pattern
as add_intro_bridge_schema.py.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import DB_PATH, init_db  # noqa: E402


def main():
    conn = sqlite3.connect(DB_PATH)

    cols = [r[1] for r in conn.execute("PRAGMA table_info(intro_bridges)").fetchall()]
    new_cols = {
        "interaction_checked_at": "DATETIME",
        "interaction_count_recent": "INTEGER",
        "most_recent_interaction_at": "DATETIME",
        "interaction_sample_text": "TEXT",
    }
    for name, sql_type in new_cols.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE intro_bridges ADD COLUMN {name} {sql_type}")
            print(f"intro_bridges: added {name}")
        else:
            print(f"intro_bridges: {name} already present, skipped")

    conn.commit()
    conn.close()

    init_db()  # creates outreach_logs (new table, no ALTER needed)
    print("outreach_logs: ensured via create_all()")


if __name__ == "__main__":
    main()
