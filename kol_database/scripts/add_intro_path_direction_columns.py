"""Add lossless follow-direction provenance to ``intro_paths``.

Idempotent and safe on every boot.  This migration deliberately does not
invent/backfill direction values: ``migrate_bd_data.py`` copies those from
the authoritative v4 graph after these columns exist.  Existing rows remain
distinguishable because ``edge_directions`` is NULL until that backfill runs.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import DB_PATH  # noqa: E402


COLUMNS = {
    "edge_directions": "TEXT",
    "direction_data_unavailable": "BOOLEAN NOT NULL DEFAULT 0",
    "direction_data_unavailable_reason": "TEXT",
}


def ensure_intro_path_direction_columns(db_path: str | Path = DB_PATH) -> list[str]:
    """Ensure the three direction-provenance columns and return additions."""

    conn = sqlite3.connect(str(db_path))
    try:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intro_paths'"
        ).fetchone()
        if table_exists is None:
            raise RuntimeError("intro_paths table does not exist; initialize the database first")

        existing = {row[1] for row in conn.execute("PRAGMA table_info(intro_paths)")}
        added: list[str] = []
        for name, sql_type in COLUMNS.items():
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE intro_paths ADD COLUMN {name} {sql_type}")
            added.append(name)
        conn.commit()
        return added
    finally:
        conn.close()


def main() -> None:
    added = ensure_intro_path_direction_columns()
    if added:
        print(f"intro_paths: added {', '.join(added)}")
    else:
        print("intro_paths: direction columns already present, skipped")


if __name__ == "__main__":
    main()
