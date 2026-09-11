"""Classify the creators that migrate_bd_data.py auto-created from BD
sponsorship evidence (source_files IS NULL) and are still sitting in
Unknown. Grounded strictly in the real evidence already in the DB (channel
name/handle + the sponsorship rows that caused the creator to be created)
-- see backend/bd_creator_classify.py's system prompt for exactly what
signal is (and isn't) used. Never invents follower/bio data that doesn't
exist for these unenriched stub creators.

Idempotent and safe to re-run: creators with creator_class_locked=True
(a human has already classified them) are skipped, and results are cached
by (model, prompt, evidence) so a re-run without --force just replays the
same verdicts instantly.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.bd_creator_classify import classify_bd_creator  # noqa: E402
from backend.classify import ClassifyError, is_configured  # noqa: E402
from backend.db import get_session  # noqa: E402
from backend.models import Creator, SponsorshipEvidence  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="Bypass the GPT response cache and re-ask.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change without writing to the DB.")
    args = parser.parse_args()

    if not is_configured():
        print("OPENAI_API_KEY is not set -- cannot run GPT-assisted classification. Nothing changed.")
        return 1

    session = get_session()
    candidates = (
        session.query(Creator)
        .filter(Creator.source_files.is_(None), Creator.creator_class == "Unknown", Creator.creator_class_locked.is_(False))
        .all()
    )
    print(f"{len(candidates)} BD-created, still-Unknown, unlocked creators to classify.")

    counts: dict[str, int] = {}
    errors = 0
    for creator in candidates:
        account = creator.social_accounts[0] if creator.social_accounts else None
        evidence_rows = session.query(SponsorshipEvidence).filter(SponsorshipEvidence.creator_id == creator.id).all()
        payload = {
            "platform": account.platform if account else None,
            "handle": account.handle if account else None,
            "display_name": creator.display_name,
            "evidence": [
                {
                    "company_name": row.company.name,
                    "disclosure_type": row.disclosure_type,
                    "content_title": row.content_title,
                }
                for row in evidence_rows
            ],
        }
        try:
            result = classify_bd_creator(payload, force=args.force)
        except ClassifyError as exc:
            print(f"  [ERROR] {creator.display_name} (id={creator.id}): {exc}")
            errors += 1
            continue

        new_class = result["creator_class"]
        counts[new_class] = counts.get(new_class, 0) + 1
        print(f"  {creator.display_name!r} (id={creator.id}) -> {new_class} [{result.get('classification_confidence')}] -- {result.get('creator_class_reason')}")

        if not args.dry_run:
            creator.creator_class = new_class
            creator.creator_class_reason = result.get("creator_class_reason")
            creator.classification_confidence = result.get("classification_confidence")
            creator.creator_class_source = "auto"
            creator.updated_at = dt.datetime.utcnow()

    if not args.dry_run:
        session.commit()
    session.close()

    print("\nResult counts:", counts)
    if errors:
        print(f"{errors} creator(s) failed to classify (see errors above) -- left as Unknown, safe to re-run.")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
