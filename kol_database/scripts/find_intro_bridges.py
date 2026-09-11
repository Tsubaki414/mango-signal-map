"""Find genuine mutual-follow introduction bridges for a target operator.

For a given X handle (a confirmed operator at a target company), this:
  1. Pulls their *complete* following list (paginated past any 5000-id cap).
  2. Checks their profile's can_dm (public DM) permission.
  3. Intersects their following list against Mango-side connectors' networks
     (Solomon_Nahhh, MangoLabs_ -- both already have complete cached
     following+followers) to find shared accounts.
  4. Resolves candidate identities and drops obvious mega-accounts (a
     shared follow of @elonmusk means nothing -- everyone follows him).
  5. For surviving candidates, verifies FULL bidirectionality on both
     sides (candidate <-> Mango-side connector, and candidate <-> target)
     before ever calling something a "bridge" -- a one-directional overlap
     is not a credible introduction path, only a candidate worth flagging
     separately (see "cultivate" tier).

Writes verified bridges to the intro_bridges table and updates
Operator.can_dm. Safe to re-run for the same operator (clears that
operator's old bridge rows first, so results reflect the latest check).

Usage: python3 scripts/find_intro_bridges.py <company_id> <operator_id> <x_handle>
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mangobd.rapid_x import RapidXClient, RapidXError  # noqa: E402
import json  # noqa: E402
import datetime as dt  # noqa: E402

from backend.db import get_session  # noqa: E402
from backend.models import IntroBridge, Operator  # noqa: E402

CACHE_DIR = REPO_ROOT / "data" / "cache" / "rapidx"
MANGO_SIDE_HANDLES = ["Solomon_Nahhh", "MangoLabs_"]
MANGO_SEED_CACHE = REPO_ROOT / "data" / "cache" / "rapidx" / "v4" / "mango-seeds"

# A shared follow of a mega-account (news orgs, global celebrities) is
# noise, not a relationship signal -- everyone in tech follows @elonmusk.
# A near-empty account is too likely to be a bot/alt to trust either.
PLAUSIBLE_FOLLOWER_MIN = 300
PLAUSIBLE_FOLLOWER_MAX = 150_000


def _crawl_following(client: RapidXClient, username: str, max_pages: int = 60) -> set[str]:
    ids: set[str] = set()
    cursor = None
    for _ in range(max_pages):
        resp = client.get_following_ids(username, count=500, cursor=cursor)
        page_ids = resp.get("ids") or []
        if not page_ids:
            break
        ids.update(str(i) for i in page_ids)
        next_cursor = resp.get("next_cursor_str") or resp.get("next_cursor")
        if not next_cursor or str(next_cursor) in ("0", str(cursor)):
            break
        cursor = str(next_cursor)
    return ids


def _mango_side_network(client: RapidXClient) -> dict[str, set[str]]:
    """Returns {handle: following_ids} and separately tracks mutuals per
    handle -- callers need both "in their network at all" (following OR
    followers) and "genuinely mutual" (following AND followers)."""
    out = {}
    for handle in MANGO_SIDE_HANDLES:
        follow_file = MANGO_SEED_CACHE / f"following-{handle}.json"
        follower_file = MANGO_SEED_CACHE / f"followers-{handle}.json"
        if follow_file.exists() and follower_file.exists():
            following = set(str(i) for i in json.loads(follow_file.read_text())["ids"])
            followers = set(str(i) for i in json.loads(follower_file.read_text())["ids"])
        else:
            following = _crawl_following(client, handle)
            followers = set()  # follower crawls are expensive; skip if no cache
        out[handle] = {"following": following, "followers": followers, "mutual": following & followers}
    return out


def find_bridges_for_operator(company_id: str, operator_id: int, x_handle: str, dry_run: bool = False) -> dict:
    client = RapidXClient(cache_dir=str(CACHE_DIR), env_file=str(REPO_ROOT / ".env"))

    profile = client.get_user(x_handle)
    result_node = profile["result"]["data"]["user"]["result"]
    target_rest_id = result_node["rest_id"]
    can_dm = result_node.get("dm_permissions", {}).get("can_dm")

    target_following = _crawl_following(client, x_handle)
    mango_networks = _mango_side_network(client)

    verified_bridges = []
    for mango_handle, net in mango_networks.items():
        overlap = target_following & net["mutual"]  # start from the strongest signal: already-mutual with Mango side
        for cand_id in overlap:
            time.sleep(0.1)
            try:
                cand_users = client.get_users_by_ids([cand_id])
            except RapidXError:
                continue
            users = cand_users.get("result") or []
            if not users:
                continue
            u = users[0]
            followers_count = int(u.get("followers_count", 0) or 0)
            if not (PLAUSIBLE_FOLLOWER_MIN <= followers_count <= PLAUSIBLE_FOLLOWER_MAX):
                continue
            cand_handle = u.get("screen_name")
            if not cand_handle:
                continue
            # Verify the candidate follows the TARGET back (full mutuality
            # on the target side too -- we already know target follows them).
            cand_following = _crawl_following(client, cand_handle)
            mutual_with_target = target_rest_id in cand_following
            if not mutual_with_target:
                continue
            verified_bridges.append(
                {
                    "bridge_handle": cand_handle,
                    "bridge_name": u.get("name"),
                    "bridge_bio": (u.get("description") or "")[:500],
                    "bridge_followers_count": followers_count,
                    "mango_side_handle": mango_handle,
                    "mutual_with_mango_side": True,
                    "mutual_with_target": True,
                    "confidence_note": f"Verified: {mango_handle} <-> @{cand_handle} mutual (both directions), @{cand_handle} <-> @{x_handle} mutual (both directions).",
                }
            )

    if dry_run:
        return {"x_handle": x_handle, "can_dm": can_dm, "bridges": verified_bridges}

    session = get_session()
    try:
        op = session.get(Operator, operator_id)
        op.can_dm = can_dm
        op.can_dm_checked_at = dt.datetime.utcnow()
        session.query(IntroBridge).filter(IntroBridge.operator_id == operator_id).delete()
        for b in verified_bridges:
            session.add(IntroBridge(company_id=company_id, operator_id=operator_id, **b))
        session.commit()
    finally:
        session.close()

    return {"x_handle": x_handle, "can_dm": can_dm, "bridges": verified_bridges}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python3 scripts/find_intro_bridges.py <company_id> <operator_id> <x_handle>")
        sys.exit(1)
    result = find_bridges_for_operator(sys.argv[1], int(sys.argv[2]), sys.argv[3])
    print(json.dumps(result, indent=2, ensure_ascii=False))
