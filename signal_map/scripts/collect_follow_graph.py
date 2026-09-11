"""Stage 1 — snapshot what Mango's own roster pays attention to on X.

Pulls the **following** list of each priced creator and stores it as a dated
snapshot. Accounts followed by many roster members are Root candidates, ranked
by centrality in the vertical our inventory actually lives in -- rather than a
hand-guessed list of famous people our roster has no relation to.

Why a dated snapshot rather than a plain edge dump
--------------------------------------------------
X does not expose when a follow began, so a change is only visible by having
looked before and looking again. For the Root graph that matters twice: Root
candidates drift as the roster moves, and a Root **unfollowing** a creator is
a real degradation of that attention path. Re-running adds evidence instead of
overwriting it.

Direction, stated because it is the easy mistake
------------------------------------------------
These edges are ``KOL → follows → account``. That is **not** an attention path
-- it says our creator can see that account, not that the account sees our
creator. It is a candidate generator. Nothing here may be written to
``AttentionSignal``; verifying the reverse edge is stage 2.

Cost: ~400 calls for 192 creators at 500 ids/page. Cache-first, resumable, and
``--limit`` caps a run.

Usage::

    python -m signal_map.scripts.collect_follow_graph --dry-run
    python -m signal_map.scripts.collect_follow_graph --limit 10
    python -m signal_map.scripts.collect_follow_graph          # full roster
    python -m signal_map.scripts.collect_follow_graph --centrality-only
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from sqlalchemy import func, or_, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.models import Creator, Quote, SocialAccount  # noqa: E402
from signal_map.backend.observation_models import (  # noqa: E402
    FollowEdge,
    FollowSnapshot,
    RosterCentrality,
    XAccount,
)

#: Page cap per observer. A creator following more than this is recorded as an
#: incomplete snapshot rather than silently truncated -- absence of an edge in
#: a truncated pull means nothing, and the flag is what stops a later query
#: from concluding otherwise.
MAX_PAGES = 8
PAGE_SIZE = 500


def _load_dotenv() -> None:
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


def roster(session: Session) -> list[tuple[Creator, SocialAccount]]:
    """Priced creators with an X handle -- the sellable inventory."""
    priced = select(Quote.creator_id).where(Quote.amount_usd.is_not(None)).distinct().subquery()
    rows = session.execute(
        select(Creator, SocialAccount)
        .join(SocialAccount, SocialAccount.creator_id == Creator.id)
        .join(priced, Creator.id == priced.c.creator_id)
        .where(SocialAccount.platform == "X", SocialAccount.handle.is_not(None))
        .order_by(Creator.id)
    ).all()
    seen: set[int] = set()
    unique: list[tuple[Creator, SocialAccount]] = []
    for creator, account in rows:
        if creator.id not in seen:
            seen.add(creator.id)
            unique.append((creator, account))
    return unique


def _upsert_account(
    session: Session,
    rest_id: str,
    *,
    handle: str | None = None,
    role: str | None = None,
    creator_id: int | None = None,
    **profile,
) -> XAccount:
    """Find-or-create by ``rest_id``, recording a handle change if seen.

    A rename is not noise -- it is one of the discovery signals this layer
    exists to catch -- so the old handle is appended rather than overwritten.
    """
    account = session.scalar(select(XAccount).where(XAccount.rest_id == str(rest_id)))
    if account is None:
        account = XAccount(rest_id=str(rest_id), handle=handle, creator_id=creator_id)
        session.add(account)
        session.flush()
    if handle and account.handle and handle.lower() != account.handle.lower():
        history = [h for h in (account.handle_history or "").split(",") if h]
        history.append(f"{account.handle}@{dt.date.today().isoformat()}")
        account.handle_history = ",".join(history)[:2000]
        account.handle = handle
    elif handle and not account.handle:
        account.handle = handle
    if creator_id and not account.creator_id:
        account.creator_id = creator_id
    for key, value in profile.items():
        if value is not None:
            setattr(account, key, value)
    if role:
        roles = {r.strip() for r in (account.roles or "").split(",") if r.strip()}
        roles.add(role)
        account.roles = ",".join(sorted(roles))
    return account


def _extract_ids(payload: dict) -> tuple[list[str], str | None]:
    """Pull id list + next cursor out of a Rapid X ids response."""
    ids: list[str] = []
    for key in ("ids", "users", "data", "result"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            for item in value:
                if isinstance(item, (str, int)):
                    ids.append(str(item))
                elif isinstance(item, dict):
                    found = item.get("id_str") or item.get("rest_id") or item.get("id")
                    if found:
                        ids.append(str(found))
            break
    cursor = payload.get("next_cursor_str") or payload.get("next_cursor")
    if cursor in {"0", "-1", 0, -1, ""}:
        cursor = None
    return ids, (str(cursor) if cursor else None)


def collect_one(
    session: Session, client, observer: XAccount, handle: str, force: bool,
    max_pages: int = MAX_PAGES,
) -> FollowSnapshot:
    """Snapshot one account's following list and fold it into the edge table.

    ``max_pages`` is a parameter rather than the module constant because the
    two callers face different lists. A creator follows a few hundred accounts;
    a target the client picked -- an a16z partner, a lab founder -- can follow
    tens of thousands, and truncating one of those manufactures false absence
    in exactly the layer that must not have any.
    """
    today = dt.date.today()
    snapshot = FollowSnapshot(
        observer_id=observer.id, kind="following", collected_date=today
    )
    session.add(snapshot)
    session.flush()

    ids: list[str] = []
    cursor: str | None = None
    pages = 0
    try:
        while pages < max_pages:
            payload = client.get_following_ids(
                handle, count=PAGE_SIZE, cursor=cursor, force=force,
                cache_ttl_seconds=86_400 * 7,
            )
            page_ids, cursor = _extract_ids(payload)
            pages += 1
            ids.extend(page_ids)
            if not cursor or not page_ids:
                break
        snapshot.is_complete = cursor is None
        if cursor is not None:
            snapshot.coverage_limitation = (
                f"stopped at {max_pages} pages ({len(ids)} ids); list is longer, "
                "so a missing edge here is not evidence of absence"
            )
    except Exception as exc:  # noqa: BLE001 -- one bad account must not end the run
        snapshot.error = str(exc)[:400]
        snapshot.is_complete = False

    unique_ids = list(dict.fromkeys(ids))
    snapshot.edge_count = len(unique_ids)
    snapshot.pages_fetched = pages

    stamp = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for target_rest_id in unique_ids:
        target = _upsert_account(session, target_rest_id, role="observed")
        edge = session.scalar(
            select(FollowEdge).where(
                FollowEdge.source_id == observer.id, FollowEdge.target_id == target.id
            )
        )
        if edge is None:
            session.add(
                FollowEdge(
                    source_id=observer.id,
                    target_id=target.id,
                    first_seen_at=stamp,
                    last_seen_at=stamp,
                    first_seen_snapshot_id=snapshot.id,
                    last_seen_snapshot_id=snapshot.id,
                )
            )
        else:
            edge.last_seen_at = stamp
            edge.last_seen_snapshot_id = snapshot.id
            edge.times_seen += 1
            edge.disappeared_at = None  # a re-followed edge is live again

    # Only a complete snapshot can prove an edge is gone. After a truncated
    # pull, a missing edge is unobserved, not unfollowed.
    if snapshot.is_complete and not snapshot.error:
        session.execute(
            FollowEdge.__table__.update()
            .where(
                FollowEdge.source_id == observer.id,
                # ``!=`` alone would miss rows with a NULL last_seen_snapshot_id:
                # SQL evaluates NULL != x as NULL, not TRUE, so an edge that
                # predates snapshot tracking would never be marked gone.
                or_(
                    FollowEdge.last_seen_snapshot_id.is_(None),
                    FollowEdge.last_seen_snapshot_id != snapshot.id,
                ),
                FollowEdge.disappeared_at.is_(None),
            )
            .values(disappeared_at=stamp)
        )

    session.commit()
    return snapshot


def compute_centrality(session: Session) -> int:
    """Rank accounts by how much of Mango's roster follows them."""
    today = dt.date.today()
    roster_ids = [
        row[0]
        for row in session.execute(
            select(XAccount.id).where(XAccount.roles.like("%roster%"))
        ).all()
    ]
    if not roster_ids:
        return 0

    collected = session.scalar(
        select(func.count(func.distinct(FollowSnapshot.observer_id))).where(
            FollowSnapshot.kind == "following"
        )
    ) or 0

    counts = session.execute(
        select(FollowEdge.target_id, func.count())
        .where(FollowEdge.source_id.in_(roster_ids), FollowEdge.disappeared_at.is_(None))
        .group_by(FollowEdge.target_id)
    ).all()

    # Purity filter, after XHunt's "≥40% in-niche" idea: an account followed by
    # many roster members only reads as an insider if those members share its
    # vertical. Without this a generic mega-account outranks a real specialist.
    vertical_by_account = {
        row[0]: row[1]
        for row in session.execute(
            select(XAccount.id, Creator.verticals)
            .join(Creator, XAccount.creator_id == Creator.id)
            .where(XAccount.creator_id.is_not(None))
        ).all()
    }
    roster_verticals: Counter = Counter()
    for verticals in vertical_by_account.values():
        for vertical in (verticals or "").split(","):
            if vertical.strip():
                roster_verticals[vertical.strip()] += 1
    dominant = {v for v, _ in roster_verticals.most_common(3)}

    written = 0
    for target_id, total in counts:
        if total < 2:
            continue  # a single follower is noise, not centrality
        in_vertical = 0
        for source_id, in session.execute(
            select(FollowEdge.source_id).where(
                FollowEdge.target_id == target_id,
                FollowEdge.source_id.in_(roster_ids),
                FollowEdge.disappeared_at.is_(None),
            )
        ).all():
            verticals = {
                v.strip() for v in (vertical_by_account.get(source_id) or "").split(",") if v.strip()
            }
            if verticals & dominant:
                in_vertical += 1

        existing = session.scalar(
            select(RosterCentrality).where(
                RosterCentrality.account_id == target_id,
                RosterCentrality.computed_date == today,
            )
        )
        row = existing or RosterCentrality(account_id=target_id, computed_date=today)
        row.roster_followers = total
        row.in_vertical_followers = in_vertical
        row.roster_size = collected
        row.roster_share = round(total / collected, 4) if collected else None

        account = session.get(XAccount, target_id)
        row.global_followers = account.followers
        # Unknown global count leaves this NULL rather than defaulting to a
        # number -- a missing denominator is not a low concentration.
        row.audience_concentration = (
            round(total / account.followers, 8) if account.followers else None
        )
        session.add(row)
        _upsert_account(session, account.rest_id, role="root_candidate")
        written += 1

    session.commit()
    return written


def _extract_profiles(payload: dict) -> list[dict]:
    """Flatten a get-users-v2 response into ``{rest_id, handle, name, ...}``.

    The endpoint nests differently depending on the account, so this walks for
    anything carrying a ``rest_id`` rather than assuming one shape.
    """
    found: list[dict] = []

    def as_int(value) -> int | None:
        # get-users-v2 returns counts as strings ("241601548"), so a plain
        # dict.get lands a str in an Integer column and silently reads as null.
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def walk(node):
        if isinstance(node, dict):
            rest_id = node.get("rest_id") or node.get("id_str") or node.get("id")
            legacy = node.get("legacy") or {}
            core = node.get("core") or {}
            # Fields may sit flat on the node or nested under legacy/core
            # depending on the endpoint; read whichever is present.
            flat = {**node, **legacy, **core}
            if rest_id and flat.get("screen_name"):
                found.append(
                    {
                        "rest_id": str(rest_id),
                        "handle": flat.get("screen_name"),
                        "display_name": flat.get("name"),
                        "bio": flat.get("description"),
                        "followers": as_int(flat.get("followers_count")),
                        "following": as_int(flat.get("friends_count")),
                        "listed": as_int(flat.get("listed_count")),
                        "verified": bool(
                            node.get("is_blue_verified") or flat.get("verified")
                        ),
                    }
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def resolve_candidates(session: Session, client, limit: int, force: bool) -> int:
    """Put handles and follower counts on the top Root candidates.

    Stage 1 returns numeric ids only. A ranked list of integers cannot be
    reviewed by a human, and human review is the step this whole design refuses
    to skip -- so resolution is part of the pipeline, not an optional extra.
    """
    rows = session.execute(
        select(XAccount.rest_id)
        .join(RosterCentrality, RosterCentrality.account_id == XAccount.id)
        .where(
            or_(XAccount.handle.is_(None), XAccount.followers.is_(None)),
            XAccount.rest_id.not_like("handle:%"),
        )
        .order_by(RosterCentrality.roster_followers.desc())
        .limit(limit)
    ).all()
    rest_ids = [row[0] for row in rows]
    resolved = 0

    for start in range(0, len(rest_ids), 100):
        batch = rest_ids[start : start + 100]
        try:
            payload = client.get_users_by_ids(batch, force=force, cache_ttl_seconds=86_400 * 30)
        except Exception:  # noqa: BLE001 -- a bad batch must not end the run
            continue
        for profile in _extract_profiles(payload):
            if profile["rest_id"] not in batch:
                continue
            _upsert_account(
                session,
                profile["rest_id"],
                handle=profile.get("handle"),
                display_name=profile.get("display_name"),
                bio=profile.get("bio"),
                followers=profile.get("followers"),
                following=profile.get("following"),
                verified=profile.get("verified"),
                listed_count=profile.get("listed"),
                profile_refreshed_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
            )
            resolved += 1
        session.commit()
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, help="cap how many creators to collect")
    parser.add_argument("--dry-run", action="store_true", help="report targets only")
    parser.add_argument("--force", action="store_true", help="bypass the response cache")
    parser.add_argument(
        "--centrality-only", action="store_true", help="recompute ranking without collecting"
    )
    parser.add_argument(
        "--resolve", type=int, metavar="N",
        help="resolve handles/profiles for the top N Root candidates and exit",
    )
    parser.add_argument("--stale-days", type=int, default=7,
                        help="re-collect an observer only if its last snapshot is older")
    args = parser.parse_args()

    _load_dotenv()
    stats: Counter = Counter()

    with sm_db.get_session() as session:
        if args.centrality_only:
            written = compute_centrality(session)
            print(f"centrality rows written: {written}")
            return

        if args.resolve:
            from mangobd.rapid_x import RapidXClient, RapidXError

            try:
                client = RapidXClient(
                    cache_dir=REPO_ROOT / "signal_map" / "data" / "cache" / "rapidx",
                    env_file=REPO_ROOT / ".env",
                )
            except RapidXError as exc:
                raise SystemExit(str(exc))
            resolved = resolve_candidates(session, client, args.resolve, args.force)
            print(f"profiles resolved: {resolved}")
            return

        pairs = roster(session)
        stats["roster"] = len(pairs)

        if args.dry_run:
            print(f"roster with an X handle: {len(pairs)}")
            print(f"est. API calls: ~{len(pairs) * 2} (cache-first, {PAGE_SIZE} ids/page)")
            return

        # ``src/mangobd``'s client, not ``kol_database``'s: only this one has
        # the cursor-paginated follower/following id endpoints. Same Rapid X
        # host and key, separate cache directory.
        from mangobd.rapid_x import RapidXClient, RapidXError

        try:
            client = RapidXClient(
                cache_dir=REPO_ROOT / "signal_map" / "data" / "cache" / "rapidx",
                env_file=REPO_ROOT / ".env",
            )
        except RapidXError as exc:
            raise SystemExit(str(exc))

        cutoff = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(
            days=args.stale_days
        )
        todo = pairs[: args.limit] if args.limit else pairs

        for creator, account in todo:
            # Without a real numeric id this account can never match a follow
            # list, so it is skipped loudly rather than stored under a
            # "handle:" placeholder that silently intersects with nothing.
            if not account.platform_uid:
                stats["skipped_no_platform_uid"] += 1
                continue
            observer = _upsert_account(
                session,
                account.platform_uid,
                handle=account.handle,
                role="roster",
                creator_id=creator.id,
                followers=account.followers,
            )
            session.commit()

            recent = session.scalar(
                select(FollowSnapshot)
                .where(
                    FollowSnapshot.observer_id == observer.id,
                    FollowSnapshot.kind == "following",
                    FollowSnapshot.collected_at >= cutoff,
                )
                .limit(1)
            )
            if recent is not None and not args.force:
                stats["skipped_fresh"] += 1
                continue

            snapshot = collect_one(session, client, observer, account.handle, args.force)
            if snapshot.error:
                stats["failed"] += 1
            elif snapshot.is_complete:
                stats["complete"] += 1
            else:
                stats["truncated"] += 1
            stats["edges"] += snapshot.edge_count

        written = compute_centrality(session)
        stats["centrality_rows"] = written

    print("--- follow-graph snapshot ---")
    for key, value in sorted(stats.items()):
        print(f"  {key:22} {value:,}")


if __name__ == "__main__":
    main()
