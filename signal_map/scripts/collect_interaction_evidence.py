"""Stage 3 — upgrade follow signals with real interaction evidence.

A follow is the weakest thing this system records. It proves an account
followed another at observation time and nothing else. What actually
distinguishes a real attention path is whether the Root has *engaged*:
replied, quoted, or mentioned.

Reads the Root's recent posts and looks for our creators in them, then writes
each hit as its own ``AttentionSignal`` alongside the follow -- never replacing
it. A reply and a follow are two separate observations; collapsing them would
lose the ability to say which one was seen.

Evidence ladder, from [product-language.md](../../kol_database/docs/product-language.md):

===========================================  ==========================  =========================
observed                                     confidence                  may be called
===========================================  ==========================  =========================
follow only                                  research_lead               存在关注 Signal
1 interaction                                research_lead               历史熟悉度信号
2+ interactions, both directions, ~18 months high_confidence_inference   近期多次双向互动
a human confirmed it                         verified_fact               —
===========================================  ==========================  =========================

Nothing here reaches ``verified_fact``: that requires a person, not an API.

Scope: only Roots that already have a follow edge to one of our creators. A
Root with no follow is not worth reading 100 posts of, and reading everything
would cost far more than it returns.

Usage::

    python -m signal_map.scripts.collect_interaction_evidence --dry-run
    python -m signal_map.scripts.collect_interaction_evidence --limit 20
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.models import AttentionSignal  # noqa: E402
from signal_map.backend.observation_models import XAccount  # noqa: E402
from signal_map.scripts.collect_follow_graph import _load_dotenv  # noqa: E402

SOURCE_SYSTEM = "signal_map_stage3"
POSTS_PER_ROOT = 100

#: Recency window for "近期". Older interactions still count as familiarity
#: signals, they just do not qualify a pair as a current relationship.
RECENT_MONTHS = 18


def _post_url(handle: str, post_id: str) -> str:
    """TweetRecord carries a post id, not a URL; build the citable link."""
    return f"https://x.com/{handle}/status/{post_id}" if post_id else ""


def _mention_pattern(handle: str) -> re.Pattern:
    return re.compile(rf"(?<![\w@]){re.escape('@' + handle)}\b", re.IGNORECASE)


def follow_pairs(
    session: Session, limit: int, only_handles: set[str] | None = None
) -> list[tuple[XAccount, dict]]:
    """Roots with at least one follow edge, and the creators they follow.

    Returns ``[(root_account, {creator_rest_id: (creator_id, handle)}), ...]``.

    ``only_handles`` scopes the run to specific accounts. Without it the ranking
    is by follow count descending, which puts the mutual-follow pods first --
    they follow 60–86 of our creators each, so they win that ordering outright
    while being the accounts ``root_review`` exists to reject. Reading 100 posts
    of each of those is the most expensive way to learn nothing. When a client
    has named their targets, those are the only accounts worth reading.
    """
    query = (
        select(AttentionSignal.source_node, func.count())
        .where(AttentionSignal.signal_type == "follow")
        .group_by(AttentionSignal.source_node)
        .order_by(func.count().desc())
    )
    if only_handles:
        wanted = {h.lstrip("@").lower() for h in only_handles}
        rest_ids = [
            account.rest_id
            for account in session.scalars(select(XAccount).where(XAccount.handle.is_not(None)))
            if (account.handle or "").lower() in wanted
        ]
        if not rest_ids:
            return []
        query = query.where(AttentionSignal.source_node.in_(rest_ids))
    roots = session.execute(query.limit(limit)).all()

    out: list[tuple[XAccount, dict]] = []
    for source_node, _ in roots:
        root = session.scalar(select(XAccount).where(XAccount.rest_id == source_node))
        if root is None or not root.handle:
            continue
        targets: dict[str, tuple[int, str]] = {}
        for signal in session.scalars(
            select(AttentionSignal).where(
                AttentionSignal.source_node == source_node,
                AttentionSignal.signal_type == "follow",
            )
        ):
            handle = session.scalar(
                select(XAccount.handle).where(XAccount.rest_id == signal.target_node)
            )
            if handle:
                targets[signal.target_node] = (signal.source_creator_id, handle)
        if targets:
            out.append((root, targets))
    return out


def _classify(record, root_handle: str) -> str:
    """reply / quote / mention, from the tweet's own shape."""
    text = (record.text or "").strip()
    if record.is_repost:
        return "repost"
    if text.startswith("@"):
        return "reply"
    if "https://twitter.com/" in text or "https://x.com/" in text:
        return "quote"
    return "mention"


def collect_root_interactions(
    session: Session, client, root: XAccount, targets: dict, force: bool
) -> int:
    """Read a Root's recent posts; record every mention of our creators."""
    from kol_database.backend.enrichment import extract_tweets

    try:
        profile = client.get_user(root.handle, force=force, cache_ttl_seconds=86_400 * 7)
        user = profile.get("result", {}).get("data", {}).get("user", {}).get("result", {})
        rest_id = user.get("rest_id") or root.rest_id
        payload = client.get_user_tweets(
            str(rest_id), count=POSTS_PER_ROOT, force=force, cache_ttl_seconds=86_400 * 3
        )
        records = extract_tweets(payload)
    except Exception:  # noqa: BLE001 -- an unreadable timeline is not a failure
        return 0

    observed = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    patterns = {rid: _mention_pattern(handle) for rid, (_, handle) in targets.items()}
    written = 0

    for record in records:
        text = record.text or ""
        for rest_id, pattern in patterns.items():
            if not pattern.search(text):
                continue
            creator_id, handle = targets[rest_id]
            kind = _classify(record, root.handle)
            occurred = record.created_at

            existing = session.scalar(
                select(AttentionSignal).where(
                    AttentionSignal.source_node == root.rest_id,
                    AttentionSignal.target_node == rest_id,
                    AttentionSignal.signal_type == kind,
                    AttentionSignal.evidence_url == _post_url(root.handle, record.post_id),
                )
            )
            if existing is not None:
                continue

            session.add(
                AttentionSignal(
                    source_node=root.rest_id,
                    target_node=rest_id,
                    source_creator_id=creator_id,
                    platform="X",
                    signal_type=kind,
                    direction="source_to_target",
                    # Unlike a follow, an interaction *does* carry a real date.
                    occurred_at=occurred.date() if occurred else None,
                    observation_count=1,
                    raw_evidence=text[:400],
                    evidence_url=_post_url(root.handle, record.post_id),
                    collected_at=observed,
                    coverage_limitation=(
                        f"read the {POSTS_PER_ROOT} most recent timeline posts only. "
                        "The timeline endpoint excludes replies, so reply-based "
                        "interaction is structurally unobserved here — its absence "
                        "is 当前未观察到, not evidence of none. Older posts likewise."
                    ),
                    # One interaction is 历史熟悉度信号 -- informative, and
                    # explicitly not proof of a relationship. Only the pattern
                    # (below) earns a higher grade.
                    confidence="research_lead",
                    human_confirmed=False,
                    source_system=SOURCE_SYSTEM,
                )
            )
            written += 1
    session.commit()
    return written


def upgrade_confidence(session: Session) -> int:
    """Raise a pair to 高概率推断 when the *pattern* justifies it.

    Requires 2+ interactions (a follow alone never counts) within the recency
    window. Direction is not checked here because only one direction has been
    collected -- so this deliberately stops short of 近期多次**双向**互动,
    which needs the creator's own timeline too.
    """
    cutoff = dt.date.today() - dt.timedelta(days=RECENT_MONTHS * 30)
    pairs = session.execute(
        select(AttentionSignal.source_node, AttentionSignal.target_node, func.count())
        .where(
            AttentionSignal.signal_type != "follow",
            AttentionSignal.occurred_at.is_not(None),
            AttentionSignal.occurred_at >= cutoff,
        )
        .group_by(AttentionSignal.source_node, AttentionSignal.target_node)
        .having(func.count() >= 2)
    ).all()

    upgraded = 0
    for source, target, _ in pairs:
        for signal in session.scalars(
            select(AttentionSignal).where(
                AttentionSignal.source_node == source,
                AttentionSignal.target_node == target,
                AttentionSignal.signal_type != "follow",
            )
        ):
            if signal.confidence != "high_confidence_inference":
                signal.confidence = "high_confidence_inference"
                upgraded += 1
    session.commit()
    return upgraded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50, help="how many roots to read")
    parser.add_argument("--handles", help="comma-separated handles to scope the run to")
    parser.add_argument(
        "--targets-only", action="store_true",
        help="scope to accounts marked object_kind=target_person",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    _load_dotenv()
    stats: Counter = Counter()

    with sm_db.get_session() as session:
        only: set[str] | None = None
        if args.handles:
            only = {h.strip() for h in args.handles.split(",") if h.strip()}
        elif args.targets_only:
            from signal_map.backend.bd_models import BDCandidate

            only = set(session.scalars(
                select(BDCandidate.handle).where(
                    BDCandidate.object_kind == "target_person",
                    BDCandidate.platform == "X",
                )
            ).all())
        pairs = follow_pairs(session, args.limit, only)
        stats["roots_with_follow_edges"] = len(pairs)
        stats["creator_pairs"] = sum(len(t) for _, t in pairs)

        if args.dry_run:
            print(f"would read {POSTS_PER_ROOT} posts each for {len(pairs)} roots")
            for root, targets in pairs[:15]:
                print(f"  @{root.handle:<22} {len(targets)} creators")
            return

        from mangobd.rapid_x import RapidXClient, RapidXError

        try:
            client = RapidXClient(
                cache_dir=REPO_ROOT / "signal_map" / "data" / "cache" / "rapidx",
                env_file=REPO_ROOT / ".env",
            )
        except RapidXError as exc:
            raise SystemExit(str(exc))

        for root, targets in pairs:
            found = collect_root_interactions(session, client, root, targets, args.force)
            stats["interactions"] += found
            if found:
                stats["roots_with_interaction"] += 1

        stats["upgraded_to_high_confidence"] = upgrade_confidence(session)

    print("--- interaction evidence (stage 3) ---")
    for key, value in sorted(stats.items()):
        print(f"  {key:28} {value:,}")


if __name__ == "__main__":
    main()
