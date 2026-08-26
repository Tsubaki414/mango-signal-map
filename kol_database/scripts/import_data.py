"""CLI entry point: import KOL_data/*.csv|xlsx into kol_database/data/kol.db.

Usage:
    PYTHONPATH=kol_database python3 kol_database/scripts/import_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.importer import run_import  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KOL_DATA_DIR = REPO_ROOT / "KOL_data"


def main() -> None:
    init_db()
    session = get_session()
    try:
        stats = run_import(KOL_DATA_DIR, session)
    finally:
        session.close()
    print("Import complete:")
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
