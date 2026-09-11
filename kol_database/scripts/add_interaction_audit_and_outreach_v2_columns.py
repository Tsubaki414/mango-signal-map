"""One-time migration: create interaction_checks / interaction_evidence
(auditable per-tweet interaction trail, replacing the flat summary fields
on intro_bridges) and add the v2 columns to outreach_logs (intro_path_id,
linked_action_item_id, owner, contacted_who, contact_channel,
evidence_url, voided, voided_reason). Idempotent -- safe to run on every
boot, same pattern as add_intro_bridge_schema.py.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import DB_PATH, init_db  # noqa: E402


def main():
    init_db()  # creates interaction_checks / interaction_evidence (new tables)
    print("interaction_checks / interaction_evidence: ensured via create_all()")

    conn = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(outreach_logs)").fetchall()]
    new_cols = {
        "intro_path_id": "INTEGER",
        "linked_action_item_id": "INTEGER",
        "owner": "VARCHAR(120)",
        "contacted_who": "VARCHAR(255)",
        "contact_channel": "VARCHAR(40)",
        "evidence_url": "VARCHAR(500)",
        "voided": "BOOLEAN DEFAULT 0",
        "voided_reason": "TEXT",
    }
    for name, sql_type in new_cols.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE outreach_logs ADD COLUMN {name} {sql_type}")
            print(f"outreach_logs: added {name}")
        else:
            print(f"outreach_logs: {name} already present, skipped")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
