"""Stage 2 — the real path-3 edges: does a Root follow one of our creators?

Stage 1 collected ``KOL → follows → account``, which is a candidate generator
and nothing more. This script collects the **opposite direction**, which is the
only one that answers the actual question:

    Root → follows → our creator   ⇒  our creator's content can enter that
                                      Root's timeline

Everything written here lands in ``AttentionSignal`` with its direction,
observation date, evidence and coverage limits attached, because a signal
missing any of those is not usable evidence.

What a follow does and does not prove
-------------------------------------
It proves a follow relationship existed **at observation time**. It does not
prove the Root reads that creator, endorses them, saw any particular post, or
that a campaign will reach them. X does not expose when the follow began, so
recency cannot be claimed either. Per
[product-language.md](../../kol_database/docs/product-language.md) a bare
follow is **研究线索**; upgrading it needs interaction evidence (stage 3).

Zero results mean **当前未观察到**, never 两者无关系 — especially when the
Root's following list was truncated at the page cap.

Usage::

    python -m signal_map.scripts.collect_attention_signals --dry-run
    python -m signal_map.scripts.collect_attention_signals --roots @a,@b
    python -m signal_map.scripts.collect_attention_signals --from-candidates 40
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.models import AttentionSignal  # noqa: E402
from signal_map.backend.observation_models import RosterCentrality, XAccount  # noqa: E402
from signal_map.scripts.collect_follow_graph import (  # noqa: E402
    PAGE_SIZE,
    _extract_ids,
    _load_dotenv,
    _upsert_account,
)

SOURCE_SYSTEM = "signal_map_stage2"

#: Higher than stage 1's cap. A root's following list is the entire evidence
#: base here, and a capped read manufactures false absence -- the one error
#: this layer must not make. 20 pages covers 10,000 accounts, which is past
#: the tail of any plausible root.
MAX_PAGES = 20


def roster_by_rest_id(session: Session) -> dict[str, tuple[int, str]]:
    """``{x_rest_id: (creator_id, handle)}`` for the sellable roster."""
    rows = session.execute(
        select(XAccount.rest_id, XAccount.creator_id, XAccount.handle).where(
            XAccount.roles.like("%roster%"), XAccount.creator_id.is_not(None)
        )
    ).all()
    return {rest_id: (creator_id, handle) for rest_id, creator_id, handle in rows}


def candidate_roots(session: Session, limit: int) -> list[XAccount]:
    """Top Root candidates from stage 1, mid-band only.

    The band matters: a mega-account is followed by everyone so its follow
    carries no information, and a sub-1K account is usually a mutual-growth
    node rather than a Root. Both extremes were checked empirically.
    """
    # Skip roots already checked today: their following list is cached, so a
    # re-fetch adds no evidence and only costs time.
    checked_today = select(AttentionSignal.source_node).where(
        AttentionSignal.collected_at >= dt.datetime.combine(dt.date.today(), dt.time.min)
    )
    rows = session.execute(
        select(XAccount)
        .join(RosterCentrality, RosterCentrality.account_id == XAccount.id)
        .where(
            XAccount.handle.is_not(None),
            XAccount.followers.between(10_000, 2_000_000),
            RosterCentrality.roster_followers >= 20,
            ~XAccount.roles.like("%roster%"),
            XAccount.rest_id.not_in(checked_today),
        )
        .order_by(RosterCentrality.audience_concentration.desc())
        .limit(limit)
    ).scalars().all()
    return list(rows)


def collect_root(
    session: Session, client, root: XAccount, roster: dict, force: bool
) -> tuple[int, bool]:
    """Pull one Root's following list and record every hit on our roster.

    Returns ``(hits, complete)``. An incomplete pull still records its hits --
    they are real -- but its coverage limitation travels with each row so a
    later reader cannot treat a miss as absence.
    """
    ids: list[str] = []
    cursor: str | None = None
    pages = 0
    error: str | None = None
    try:
        while pages < MAX_PAGES:
            payload = client.get_following_ids(
                root.handle, count=PAGE_SIZE, cursor=cursor, force=force,
                cache_ttl_seconds=86_400 * 7,
            )
            page_ids, cursor = _extract_ids(payload)
            pages += 1
            ids.extend(page_ids)
            if not cursor or not page_ids:
                break
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:300]

    collected = len(dict.fromkeys(ids))
    # Trusting the cursor alone is not enough: the API can return a terminal
    # cursor before the list is exhausted, which records a short pull as
    # "complete" and strips its coverage warning. Caught on @icreatelife --
    # 3,832 following, 3,000 read, no limitation recorded. So compare what we
    # got against what the profile says exists.
    expected = root.following
    short = expected is not None and collected < expected * 0.98

    complete = cursor is None and error is None and not short
    limitation = None
    if not complete:
        if error is not None:
            limitation = f"collection error: {error}"
        elif short:
            limitation = (
                f"read {collected:,} of ~{expected:,} accounts this root follows "
                f"({pages} pages, capped)"
            )
        else:
            limitation = f"root following list truncated at {pages} pages ({collected} ids)"
        limitation += " — a creator absent from this sample is 当前未观察到, not unfollowed"

    observed = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    hits = 0
    for rest_id in dict.fromkeys(ids):
        match = roster.get(rest_id)
        if match is None:
            continue
        creator_id, handle = match
        existing = session.scalar(
            select(AttentionSignal).where(
                AttentionSignal.source_node == root.rest_id,
                AttentionSignal.target_node == rest_id,
                AttentionSignal.signal_type == "follow",
            )
        )
        if existing is not None:
            # Only a *new day's* look is a new observation. Re-running the
            # script against a cached response is one observation read twice,
            # and counting it again would inflate the evidence for free --
            # which is exactly how a follow starts looking like a pattern.
            if existing.collected_at is None or existing.collected_at.date() < observed.date():
                existing.observation_count += 1
                existing.collected_at = observed
            existing.coverage_limitation = limitation
            continue

        session.add(
            AttentionSignal(
                # Root is the source: the edge is Root → follows → creator.
                source_node=root.rest_id,
                target_node=rest_id,
                source_creator_id=creator_id,
                platform="X",
                signal_type="follow",
                direction="source_to_target",
                # X does not expose when the follow began. Left NULL rather
                # than back-filled from today, which would invent a date.
                occurred_at=None,
                observation_count=1,
                raw_evidence=f"@{root.handle} follows @{handle}",
                evidence_url=f"https://x.com/{root.handle}/following",
                collected_at=observed,
                coverage_limitation=limitation,
                # A bare follow is 研究线索. Only interaction evidence
                # (stage 3) or a human check may raise this.
                confidence="research_lead",
                human_confirmed=False,
                source_system=SOURCE_SYSTEM,
            )
        )
        hits += 1

    session.commit()
    return hits, complete


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", help="comma-separated handles to check")
    parser.add_argument(
        "--from-candidates", type=int, default=0,
        help="take the top N stage-1 Root candidates",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    _load_dotenv()
    stats: Counter = Counter()

    with sm_db.get_session() as session:
        roster = roster_by_rest_id(session)
        stats["roster"] = len(roster)

        roots: list[XAccount] = []
        if args.roots:
            from mangobd.rapid_x import RapidXClient, RapidXError

            try:
                client = RapidXClient(
                    cache_dir=REPO_ROOT / "signal_map" / "data" / "cache" / "rapidx",
                    env_file=REPO_ROOT / ".env",
                )
            except RapidXError as exc:
                raise SystemExit(str(exc))
            for handle in [h.strip().lstrip("@") for h in args.roots.split(",") if h.strip()]:
                account = session.scalar(select(XAccount).where(XAccount.handle == handle))
                if account is None:
                    # Named Roots are allowed even when stage 1 never saw them;
                    # the client is entitled to ask about any account.
                    account = _upsert_account(session, f"pending:{handle}", handle=handle)
                    session.commit()
                roots.append(account)
        if args.from_candidates:
            roots.extend(candidate_roots(session, args.from_candidates))

        if not roots:
            raise SystemExit("nothing to do: pass --roots or --from-candidates")

        stats["roots"] = len(roots)
        if args.dry_run:
            print(f"would check {len(roots)} roots against {len(roster)} creators")
            for root in roots[:20]:
                print(f"  @{root.handle}")
            return

        from mangobd.rapid_x import RapidXClient, RapidXError

        try:
            client = RapidXClient(
                cache_dir=REPO_ROOT / "signal_map" / "data" / "cache" / "rapidx",
                env_file=REPO_ROOT / ".env",
            )
        except RapidXError as exc:
            raise SystemExit(str(exc))

        found: list[tuple[str, int]] = []
        for root in roots:
            hits, complete = collect_root(session, client, root, roster, args.force)
            stats["complete" if complete else "truncated"] += 1
            stats["signals"] += hits
            if hits:
                stats["roots_with_a_path"] += 1
                found.append((root.handle, hits))
            # A Root that follows one of our creators is worth marking, but it
            # is still only a *candidate* until a human accepts it.
            if hits:
                _upsert_account(session, root.rest_id, role="root_candidate")
        session.commit()

    print("--- attention signals (path 3) ---")
    for key, value in sorted(stats.items()):
        print(f"  {key:22} {value:,}")
    if found:
        print("\n--- roots that follow our creators ---")
        for handle, hits in sorted(found, key=lambda x: -x[1]):
            print(f"  @{handle:<22} {hits}")


if __name__ == "__main__":
    main()
