"""Suggest a Root type for every candidate — so a human reviews, not types.

249 accounts follow at least one of Mango's creators. Every one needs a person
to say what it is, and the rule-based suggester manages that for 1 of 200: a
bio says "General Partner" or it does not, and almost none of them do. Telling
an 行业大佬 from a 垂直专家 from a content creator is a semantic judgment, which
is the same wall the audience classifier hit.

What this writes and what it does not
-------------------------------------
It writes ``root_suggested_type`` / ``root_suggestion_basis`` /
``root_suggested_by``. It **never** writes ``root_type``,
``root_review_status`` or any of the ``root_reviewed_*`` fields. Those are set
by a person through ``PATCH /api/internal/roots/{handle}`` and by nothing else.

That separation is the whole point. An accepted Root becomes a client-facing
claim -- "被 已确认 Root（投资人）@x 关注" -- and a claim no person stood behind
is exactly what this product forbids. The model's job is to make the reviewer's
minute cheaper, not to replace it.

The most valuable answer here is ``not_a_root``. Inspecting the queue shows the
majority are content creators in the same niche as Mango's own roster, and the
arithmetic flags (``roster_sweep`` / ``peer_network``) only catch the ones that
followed the roster in bulk. A big AI-tips account that followed three of our
creators looks clean to arithmetic and is still not a Root.

Usage::

    python -m signal_map.scripts.classify_roots_llm --dry-run
    python -m signal_map.scripts.classify_roots_llm --limit 20
    python -m signal_map.scripts.classify_roots_llm --force
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import func, select  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.llm_classify import (  # noqa: E402
    NOT_A_ROOT,
    LLMError,
    build_profile_text,
    classify_root,
    resolve_provider,
)
from signal_map.backend.models import AttentionSignal  # noqa: E402
from signal_map.backend.observation_models import (  # noqa: E402
    ROOT_TYPE_LABELS_ZH,
    XAccount,
)
from signal_map.scripts.collect_follow_graph import _load_dotenv  # noqa: E402


def candidates(session, limit: int, redo: bool) -> list[XAccount]:
    """Accounts with at least one observed edge to the roster.

    Only these matter: an account nobody's creator connects to is not a Root
    candidate, it is a row in the follow graph.
    """
    observed = select(AttentionSignal.source_node).distinct().subquery()
    edges = (
        select(
            AttentionSignal.source_node.label("node"),
            func.count(func.distinct(AttentionSignal.source_creator_id)).label("n"),
        )
        .group_by(AttentionSignal.source_node)
        .subquery()
    )
    query = (
        select(XAccount)
        .join(observed, observed.c.source_node == XAccount.rest_id)
        .join(edges, edges.c.node == XAccount.rest_id)
        # Never re-suggest for an account a person already ruled on: the
        # suggestion column would then disagree with a standing human verdict.
        .where(XAccount.root_review_status == "pending")
        .order_by(edges.c.n.desc())
    )
    if not redo:
        query = query.where(XAccount.root_suggested_type.is_(None))
    if limit:
        query = query.limit(limit)
    return list(session.scalars(query).all())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="cap the number classified")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="ignore the response cache")
    parser.add_argument(
        "--redo", action="store_true", help="re-suggest accounts that already have one"
    )
    args = parser.parse_args()

    _load_dotenv()
    try:
        provider = resolve_provider()
    except LLMError as exc:
        raise SystemExit(str(exc))

    stats: Counter = Counter()
    with sm_db.get_session() as session:
        accounts = candidates(session, args.limit, args.redo)
        print(f"{len(accounts)} candidates to classify via {provider.name}/{provider.model}")
        if args.dry_run:
            for account in accounts[:20]:
                print(f"  @{account.handle}")
            return

        for index, account in enumerate(accounts, 1):
            profile = build_profile_text(
                display_name=account.display_name,
                bio=account.bio,
                platform="X",
            )
            if not profile.strip():
                # No bio to read. Left untouched and counted, rather than
                # recorded as not_a_root -- that would be a verdict drawn from
                # nothing, and the reviewer needs to know it is a gap.
                stats["no_content"] += 1
                continue
            try:
                verdict = classify_root(profile, force=args.force)
            except LLMError as exc:
                stats["failed"] += 1
                print(f"  ! @{account.handle}: {str(exc)[:120]}")
                continue

            account.root_suggested_type = (
                verdict.root_type if verdict.is_root else None
            )
            account.root_suggestion_basis = verdict.basis
            account.root_suggested_by = f"llm:{verdict.model}"
            stats[verdict.root_type or NOT_A_ROOT] += 1
            stats["cached" if verdict.from_cache else "called"] += 1

            if index % 25 == 0:
                session.commit()
                print(f"  … {index}/{len(accounts)}")
        session.commit()

        print("\n--- root type suggestions ---")
        for key, count in stats.most_common():
            if key in {"cached", "called", "failed", "no_content"}:
                continue
            label = ROOT_TYPE_LABELS_ZH.get(key, "非 Root（疑为同类创作者）" if key == NOT_A_ROOT else key)
            print(f"  {label:<24} {count}")
        print(
            f"\n  api calls {stats['called']} · from cache {stats['cached']}"
            f" · failed {stats['failed']} · no bio {stats['no_content']}"
        )
        print("\n  这些只是建议。root_type 仍需人工在 PATCH /api/internal/roots/{handle} 确认。")


if __name__ == "__main__":
    main()
