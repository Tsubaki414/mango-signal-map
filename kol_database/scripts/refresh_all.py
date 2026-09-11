"""One repeatable command to refresh the BD-intelligence side of the
Cockpit after the source data changes (a new mango_bd_v4.sqlite research
pass, or just periodically). Runs, in order:

  1. backup_db.py       -- snapshot before touching anything
  2. migrate_bd_data.py  -- re-sync Company/Operator/IntroPath/ActionItem/
                             SponsorshipEvidence/GtmCase/ConnectorBrief from
                             the source SQLite (idempotent, see that script)
      3. fix_fundraise_only_spend_evidence.py -- repair conservative spend
                             classifications after the source import
      4. classify_bd_creators.py -- classify any newly-created Unknown creators
                             (skips already-classified/locked ones; no-op if
                             OPENAI_API_KEY isn't set)
  4. reconcile_bd_data.py -- verify the result, fail loudly if not clean

Opportunity/Creator-fit/Relationship scores are never stored -- they're
computed live from current DB state on every request (see bd_compute.py),
so there is nothing to "recompute" after this refresh; the very next page
load already reflects the new data. Stops at the first step that fails.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable


def run(label: str, script: str, *args: str, fatal: bool = True) -> bool:
    print(f"\n=== {label} ===")
    result = subprocess.run([PYTHON, str(SCRIPTS_DIR / script), *args])
    if result.returncode != 0:
        if fatal:
            print(f"[refresh_all] {label} failed (exit {result.returncode}) -- stopping.")
            return False
        print(f"[refresh_all] {label} did not complete cleanly (exit {result.returncode}) -- continuing, see output above.")
    return True


def main() -> int:
    steps = [
        ("Backup current DB", "backup_db.py", ["pre_refresh"], True),
        ("Migrate BD data", "migrate_bd_data.py", [], True),
        (
            "Repair negation-induced spend classifications",
            "fix_fundraise_only_spend_evidence.py",
            [],
            True,
        ),
        (
            "Backfill exact NULL sponsorship publish dates",
            "backfill_sponsorship_publish_dates.py",
            [],
            True,
        ),
        ("Apply NULL-only release planning dates", "apply_release_schedule.py", [], True),
        # Non-fatal: a missing OPENAI_API_KEY or a handful of per-creator
        # classify errors shouldn't block reconciliation from still running.
        ("Classify new BD creators", "classify_bd_creators.py", [], False),
        ("Reconcile", "reconcile_bd_data.py", [], True),
    ]
    for label, script, args, fatal in steps:
        if not run(label, script, *args, fatal=fatal):
            return 1
    print("\n[refresh_all] Done -- scores recompute automatically on next request, nothing further to run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
