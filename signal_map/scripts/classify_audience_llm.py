"""Re-classify creator audiences with an LLM, replacing the keyword ladder.

The regex derivation matched **what a creator does** and stored it as **who
watches them** -- an anime-edits channel came out as "creators" when its
audience is anime fans. That is a semantic error, so this pass re-runs every
priced creator that has content, not only the ones the regex left unresolved.

Precedence: an existing ``content``/``vertical`` basis is *replaced* by an
``llm`` verdict, because the LLM reads the same evidence better. A verdict is
only written when the model returns at least one type -- an empty verdict
leaves the previous value alone rather than deleting a usable, if coarse,
answer.

Every write records the model id and the quoted evidence, so a later model
change is visible and each assigned type stays traceable to the text that
justified it.

Cost control (AGENTS.md): verdicts are cached on disk keyed by content+model,
``--limit`` caps a run, and creators with no content are skipped without a
call.

Usage::

    python -m signal_map.scripts.classify_audience_llm --dry-run
    python -m signal_map.scripts.classify_audience_llm --limit 10
    python -m signal_map.scripts.classify_audience_llm
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.llm_classify import (  # noqa: E402
    LLMError,
    build_profile_text,
    classify_audience,
    classify_verticals,
    resolve_provider,
)
from signal_map.backend.models import Creator, Quote, SocialAccount  # noqa: E402


def _load_dotenv() -> None:
    """Load the repo-root .env into this process only. Never logged."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip()


def targets(session) -> list[Creator]:
    """Every priced creator. The regex verdicts need revisiting too, so this
    is deliberately not restricted to the unresolved ones."""
    priced = select(Quote.creator_id).where(Quote.amount_usd.is_not(None)).distinct().subquery()
    return list(
        session.scalars(
            select(Creator).join(priced, Creator.id == priced.c.creator_id).order_by(Creator.id)
        )
    )


def _best_account(creator: Creator) -> SocialAccount | None:
    """The account carrying the most content to reason over."""
    scored = [
        (len(account.bio or "") + len(account.content_summary or ""), account)
        for account in creator.accounts
    ]
    if not scored:
        return None
    size, account = max(scored, key=lambda pair: pair[0])
    return account if size else None


def run(limit: int | None = None, dry_run: bool = False, force: bool = False) -> dict:
    _load_dotenv()
    stats: Counter = Counter()
    changes: list[tuple[str, str, str]] = []

    provider = resolve_provider()
    stats_model = provider.model

    with sm_db.get_session() as session:
        creators = targets(session)
        stats["priced_creators"] = len(creators)
        if limit:
            creators = creators[:limit]

        for creator in creators:
            account = _best_account(creator)
            if account is None:
                stats["skipped_no_content"] += 1
                continue

            profile_text = build_profile_text(
                display_name=creator.display_name,
                bio=account.bio,
                content_summary=account.content_summary,
                verticals=creator.verticals,
                platform=account.platform,
            )
            if dry_run:
                stats["would_classify"] += 1
                continue

            try:
                verdict = classify_audience(profile_text, force=force)
            except LLMError as exc:
                stats["llm_failed"] += 1
                if stats["llm_failed"] <= 3:
                    changes.append((creator.display_name, "ERROR", str(exc)[:120]))
                continue

            stats["cached" if verdict.from_cache else "called"] += 1

            if not verdict.types:
                # An empty verdict is a legitimate "cannot tell". It must not
                # wipe a coarse-but-usable existing answer.
                stats["llm_returned_empty"] += 1
                if creator.audience_types is None:
                    stats["still_unknown"] += 1
                continue

            before = creator.audience_types
            if before != verdict.types_csv:
                stats["changed"] += 1
                changes.append((creator.display_name, before or "(none)", verdict.types_csv))
            else:
                stats["unchanged"] += 1

            creator.audience_types = verdict.types_csv
            creator.audience_types_basis = "llm"
            creator.audience_types_evidence = verdict.evidence_text

            # Verticals drive the 内容与行业 axis, and a creator with none is
            # unmatched on it. Only filled when missing -- a category already
            # on file came from the source sheet and is not the model's to
            # overwrite.
            if creator.verticals is None:
                try:
                    vertical_verdict = classify_verticals(profile_text)
                except LLMError:
                    vertical_verdict = None
                if vertical_verdict and vertical_verdict.csv:
                    creator.verticals = vertical_verdict.csv
                    stats["verticals_filled"] += 1
            creator.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

        if not dry_run:
            session.commit()

    return {"stats": dict(stats), "model": stats_model, "changes": changes}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="cap how many creators to classify")
    parser.add_argument("--dry-run", action="store_true", help="report targets without calling")
    parser.add_argument("--force", action="store_true", help="bypass the verdict cache")
    parser.add_argument("--show", type=int, default=25, help="how many changes to print")
    args = parser.parse_args()

    result = run(limit=args.limit, dry_run=args.dry_run, force=args.force)
    print(f"--- llm audience classification (model: {result['model']}) ---")
    for key, value in sorted(result["stats"].items()):
        print(f"  {key:28} {value}")
    if result["changes"]:
        print(f"\n--- changes ({len(result['changes'])}) ---")
        for name, before, after in result["changes"][: args.show]:
            print(f"  {name[:28]:30} {before[:34]:36} -> {after}")


if __name__ == "__main__":
    main()
