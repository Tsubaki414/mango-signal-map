"""Add the Root-review columns to an existing ``x_accounts`` table.

``create_all`` creates missing *tables*, never missing *columns*, so a schema
change to the observation layer needs an explicit ALTER. And the observation
tables are the one part of Signal Map that a migration re-run must **not**
rebuild: ``migrate_from_bd`` drops and recreates the product tables freely
because it can re-derive them from ``kol.db``, but the follow graph cost real
API quota to collect and exists nowhere else.

Idempotent — re-running is a no-op.

    python -m signal_map.scripts.add_root_review_columns
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402

COLUMNS = {
    "root_review_status": "VARCHAR(20) NOT NULL DEFAULT 'pending'",
    "root_suggested_type": "VARCHAR(30)",
    "root_suggestion_basis": "TEXT",
    "root_suggested_by": "VARCHAR(120)",
}


def main() -> None:
    with sm_db.engine.begin() as connection:
        existing = {
            row[1] for row in connection.execute(text("PRAGMA table_info(x_accounts)"))
        }
        added = []
        for column, spec in COLUMNS.items():
            if column in existing:
                continue
            connection.execute(text(f"ALTER TABLE x_accounts ADD COLUMN {column} {spec}"))
            added.append(column)

        if "root_review_status" in added:
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_x_accounts_root_review_status "
                     "ON x_accounts (root_review_status)")
            )
            # Existing rows land on 'pending' via the column default, which is
            # the honest starting state: nobody has reviewed any of them.

    print(f"added: {', '.join(added) if added else 'nothing (already present)'}")


if __name__ == "__main__":
    main()
