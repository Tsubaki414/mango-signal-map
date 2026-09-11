"""Snapshot data/kol.db into data/backups/ before any risky operation
(migration re-run, bulk edit, manual DB surgery). Keeps the most recent
KEEP_LAST backups and prunes older ones so this doesn't grow unbounded.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.environ.get("DB_PATH", REPO_ROOT / "data" / "kol.db"))
KEEP_LAST = 20


def backup_database(
    db_path: Path | None = None,
    backup_dir: Path | None = None,
    *,
    label: str = "manual",
    keep_last: int = KEEP_LAST,
) -> Path:
    """Create one recoverable database snapshot and return its path.

    Kept as a callable function so every bulk-mutation script can use the
    exact same backup implementation instead of open-coding a copy step.
    ``db_path``/``backup_dir`` are injectable for mounted volumes and tests.
    """

    db_path = Path(db_path or DEFAULT_DB_PATH)
    backup_dir = Path(
        backup_dir
        or os.environ.get("BACKUP_DIR")
        or db_path.parent / "backups"
    )
    if not db_path.exists():
        raise FileNotFoundError(f"No database at {db_path} -- nothing to back up.")
    if keep_last < 1:
        raise ValueError("keep_last must be at least 1")

    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in label).strip("_") or "manual"
    # Microseconds prevent two safe, rapidly-repeated mutations from
    # silently overwriting the same-second backup.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    dest = backup_dir / f"kol_{timestamp}_{safe_label}.db"
    # SQLite's online backup API produces a transactionally-consistent
    # snapshot even when the source uses WAL; a plain filesystem copy can
    # miss committed pages still living in the sidecar file.
    with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as source:
        with sqlite3.connect(dest) as target:
            source.backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise sqlite3.DatabaseError("backup integrity_check failed")
    print(f"Backed up {db_path} -> {dest}")

    backups = sorted(backup_dir.glob("kol_*.db"))
    if len(backups) > keep_last:
        for old in backups[: len(backups) - keep_last]:
            old.unlink()
            print(f"Pruned old backup: {old.name}")

    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Positional label stays compatible with the documented historical call.
    parser.add_argument("label", nargs="?", default="manual")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--keep-last", type=int, default=KEEP_LAST)
    args = parser.parse_args(argv)
    try:
        backup_database(
            db_path=args.db,
            backup_dir=args.backup_dir,
            label=args.label,
            keep_last=args.keep_last,
        )
    except (FileNotFoundError, ValueError, sqlite3.DatabaseError) as exc:
        print(exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
