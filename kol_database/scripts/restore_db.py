"""Restore data/kol.db from a backup in data/backups/. Destructive by
nature (overwrites the live DB), so it requires --yes and always takes a
safety snapshot of whatever is currently live before overwriting it --
even a bad restore is itself undoable.

Usage:
    python3 scripts/restore_db.py --list
    python3 scripts/restore_db.py --yes                      # restores the most recent backup
    python3 scripts/restore_db.py --yes --file kol_20260826_120000_manual.db
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from backup_db import backup_database

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.environ.get("DB_PATH", REPO_ROOT / "data" / "kol.db"))


def _validate_sqlite(db_path: Path) -> None:
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise sqlite3.DatabaseError("restore source failed integrity_check")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="List available backups and exit.")
    parser.add_argument("--file", help="Backup filename (in data/backups/) to restore. Defaults to the most recent.")
    parser.add_argument("--yes", action="store_true", help="Required to actually perform the restore.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="Live SQLite DB path; defaults to DB_PATH.")
    parser.add_argument("--backup-dir", type=Path, help="Backup directory; defaults to BACKUP_DIR or <DB parent>/backups.")
    args = parser.parse_args()

    db_path = args.db
    backup_dir = args.backup_dir or Path(os.environ.get("BACKUP_DIR", db_path.parent / "backups"))

    backups = sorted(backup_dir.glob("kol_*.db"))
    if args.list or not backups:
        if not backups:
            print(f"No backups found in {backup_dir}.")
            return 1
        print("Available backups (most recent last):")
        for b in backups:
            print(f"  {b.name}")
        return 0

    target = backup_dir / Path(args.file).name if args.file else backups[-1]
    if not target.exists():
        print(f"Backup not found: {target}")
        return 1

    if not args.yes:
        print(f"Would restore {target.name} -> {db_path}. Re-run with --yes to actually do it.")
        return 1

    try:
        _validate_sqlite(target)
        if db_path.exists():
            backup_database(
                db_path=db_path,
                backup_dir=backup_dir,
                label="pre_restore_safety",
            )

        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Stage and validate beside the destination, then replace atomically.
        with tempfile.NamedTemporaryFile(
            prefix=f".{db_path.name}.restore-",
            dir=db_path.parent,
            delete=False,
        ) as temporary:
            staged = Path(temporary.name)
        try:
            shutil.copy2(target, staged)
            _validate_sqlite(staged)
            os.replace(staged, db_path)
        finally:
            if staged.exists():
                staged.unlink()
    except (FileNotFoundError, OSError, sqlite3.DatabaseError, ValueError) as exc:
        print(f"Restore failed: {exc}")
        return 1

    print(f"Restored {target.name} -> {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
