#!/usr/bin/env python3
"""Cost-capped expansion of Solomon's broad X connector frontier through Rapid X only."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from mangobd.rapid_x import RapidXClient
from mangobd.x_network import find_user_objects, load_best_id_cache


ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / "data" / "cache" / "rapidx"
CACHE = CACHE_ROOT / "v4"
OUT = ROOT / "outputs" / "pilot_v4"
FRONTIER = ROOT / "data" / "pilot_v4" / "x_target_frontier.json"
ROOT_HANDLE = "Solomon_Nahhh"
FIRST_DEGREE_PROFILE_CACHE = CACHE / "solomon-first-degree-profiles.json"
FIRST_DEGREE_COVERAGE = OUT / "solomon_first_degree_coverage.json"


KEYWORD_GROUPS = {
    "ai": (["artificial intelligence", " ai ", "llm", "agent", "machine learning", "inference"], 4),
    "buyer": (["growth", "marketing", "partnership", "business development", " bd ", "gtm"], 5),
    "authority": (["founder", "co-founder", "ceo", "investor", "venture", " vc "], 3),
    "distribution": (["creator", "youtube", "podcast", "newsletter", "community", "devrel", "developer relations"], 3),
    "crypto": (["crypto", "web3", "onchain", "blockchain"], 1),
}


def normalize_user(user: dict) -> dict:
    legacy = user.get("legacy") or {}
    core = user.get("core") or {}
    rest_id = user.get("rest_id") or user.get("id_str") or user.get("id")
    if rest_id is None:
        raise ValueError("Rapid X user object has no rest_id, id_str, or id")
    return {
        "rest_id": str(rest_id),
        "handle": legacy.get("screen_name") or core.get("screen_name") or user.get("screen_name") or user.get("username") or "",
        "name": legacy.get("name") or core.get("name") or user.get("name") or "",
        "description": legacy.get("description") or user.get("description") or "",
        "followers_count": legacy.get("followers_count", user.get("followers_count")),
        "following_count": legacy.get("friends_count", user.get("friends_count", user.get("following_count"))),
        "verified": bool(user.get("is_blue_verified") or legacy.get("verified") or user.get("verified")),
        "profile_resolution_status": "resolved",
    }


def merge_first_degree_membership(
    outbound_ids: set[str],
    inbound_ids: set[str],
    *,
    outbound_complete: bool,
    inbound_complete: bool,
) -> list[dict]:
    """Merge observed following/follower IDs without treating partial absence as an edge negation."""
    outbound = {str(value) for value in outbound_ids}
    inbound = {str(value) for value in inbound_ids}
    rows: list[dict] = []
    for rest_id in sorted(outbound | inbound):
        observed_outbound = rest_id in outbound
        observed_inbound = rest_id in inbound
        solomon_follows = True if observed_outbound else (False if outbound_complete else None)
        follows_solomon = True if observed_inbound else (False if inbound_complete else None)
        if observed_outbound and observed_inbound:
            mutual_follow: bool | None = True
            certainty = "confirmed_mutual"
        elif solomon_follows is not None and follows_solomon is not None:
            mutual_follow = False
            certainty = "confirmed_one_way"
        else:
            mutual_follow = None
            certainty = "partial_cache_cannot_assert_non_mutual"
        rows.append({
            "rest_id": rest_id,
            "solomon_follows": solomon_follows,
            "follows_solomon": follows_solomon,
            "mutual_follow": mutual_follow,
            "relationship_degree": "direct",
            "relationship_certainty": certainty,
        })
    return rows


def build_first_degree_coverage(
    outbound_ids: set[str],
    inbound_ids: set[str],
    *,
    outbound_complete: bool,
    inbound_complete: bool,
    outbound_pages: int,
    inbound_pages: int,
) -> dict:
    """Describe exactly what the two directional caches do and do not prove."""
    outbound = {str(value) for value in outbound_ids}
    inbound = {str(value) for value in inbound_ids}
    both_complete = outbound_complete and inbound_complete
    notes = []
    if not outbound_complete:
        notes.append("Following coverage is partial; inbound-only IDs cannot be asserted as accounts Solomon does not follow.")
    if not inbound_complete:
        notes.append("Follower coverage is partial; outbound-only IDs cannot be asserted as accounts that do not follow Solomon.")
    if both_complete:
        notes.append("Both directional caches are complete; observed one-way relationships may be asserted non-mutual.")
    return {
        "root_handle": ROOT_HANDLE,
        "relationship_degree": "direct",
        "outbound": {
            "kind": "following",
            "observed_count": len(outbound),
            "pages": outbound_pages,
            "complete": outbound_complete,
            "coverage_label": "complete" if outbound_complete else "partial",
            "absence_assertable": outbound_complete,
        },
        "inbound": {
            "kind": "followers",
            "observed_count": len(inbound),
            "pages": inbound_pages,
            "complete": inbound_complete,
            "coverage_label": "complete" if inbound_complete else "partial",
            "absence_assertable": inbound_complete,
        },
        "union": {"observed_count": len(outbound | inbound)},
        "intersection": {"observed_count": len(outbound & inbound)},
        "mutual_certainty": {
            "confirmed_intersection_is_mutual": True,
            "all_mutuals_enumerated": both_complete,
            "can_assert_non_mutual_for_outbound_only": inbound_complete,
            "can_assert_non_mutual_for_inbound_only": outbound_complete,
            "notes": notes,
        },
    }


def _select_cache_snapshot(candidates: list[tuple[set[str], bool, int]]) -> tuple[set[str], bool, int]:
    return max(candidates, key=lambda item: (item[1], len(item[0]), item[2]))


def load_direction_cache(handle: str, kind: str) -> tuple[set[str], bool, int]:
    """Load legacy/v2/v3 plus this script's v4 combined cache for one edge direction."""
    candidates = [load_best_id_cache(CACHE_ROOT, handle, kind)]
    combined = CACHE / f"{kind}-{handle}.json"
    if combined.exists():
        payload = json.loads(combined.read_text(encoding="utf-8"))
        candidates.append((
            {str(value) for value in payload.get("ids", [])},
            bool(payload.get("complete")),
            int(payload.get("pages", 0)),
        ))
    return _select_cache_snapshot(candidates)


def load_first_degree_state() -> tuple[list[dict], dict]:
    outbound_ids, outbound_complete, outbound_pages = load_direction_cache(ROOT_HANDLE, "following")
    inbound_ids, inbound_complete, inbound_pages = load_direction_cache(ROOT_HANDLE, "followers")
    if not outbound_ids and not inbound_ids:
        raise RuntimeError("Solomon following/follower ID caches are both missing or empty")
    membership = merge_first_degree_membership(
        outbound_ids,
        inbound_ids,
        outbound_complete=outbound_complete,
        inbound_complete=inbound_complete,
    )
    coverage = build_first_degree_coverage(
        outbound_ids,
        inbound_ids,
        outbound_complete=outbound_complete,
        inbound_complete=inbound_complete,
        outbound_pages=outbound_pages,
        inbound_pages=inbound_pages,
    )
    return membership, coverage


def merge_profiles_with_membership(profiles: list[dict], membership: list[dict]) -> list[dict]:
    """Join cached profiles to the direct frontier while retaining unresolved IDs."""
    profile_by_id = {str(item["rest_id"]): item for item in profiles if item.get("rest_id")}
    rows = []
    for relation in membership:
        rest_id = relation["rest_id"]
        profile = profile_by_id.get(rest_id, {
            "rest_id": rest_id,
            "handle": "",
            "name": "",
            "description": "",
            "followers_count": None,
            "following_count": None,
            "verified": False,
            "profile_resolution_status": "unresolved",
        })
        rows.append({**profile, **relation})
    return rows


def sort_scored_profiles(rows: list[dict]) -> list[dict]:
    """Rank by semantic fit plus bounded relationship evidence, never audience size."""
    return sorted(
        rows,
        key=lambda item: (
            -item["connector_priority_score"],
            -item["connector_discovery_score"],
            str(item.get("handle") or "").casefold(),
            str(item.get("rest_id") or ""),
        ),
    )


def score_profile(profile: dict) -> dict:
    text = f" {profile.get('name','')} {profile.get('description','')} ".lower()
    matched = []
    score = 0
    for group, (keywords, weight) in KEYWORD_GROUPS.items():
        if any(keyword in text for keyword in keywords):
            matched.append(group)
            score += weight
    if "ai" in matched and "buyer" in matched:
        score += 5
    if "buyer" in matched and "authority" in matched:
        score += 3
    relationship_signal_score = (
        6 if profile.get("mutual_follow") is True
        else 4 if profile.get("follows_solomon") is True
        else 2 if profile.get("solomon_follows") is True
        else 0
    )
    return {
        **profile,
        "matched_groups": matched,
        "connector_discovery_score": score,
        "relationship_signal_score": relationship_signal_score,
        "connector_priority_score": score + relationship_signal_score,
        "human_relationship_status": "unknown_requires_solomon_review",
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = [
        "rank", "connector_priority_score", "connector_discovery_score", "relationship_signal_score",
        "handle", "name", "rest_id", "matched_groups",
        "description", "followers_count", "following_count", "verified",
        "solomon_follows", "follows_solomon", "mutual_follow", "relationship_degree",
        "relationship_certainty", "profile_resolution_status", "human_relationship_status",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: json.dumps(row.get(field), ensure_ascii=False) if isinstance(row.get(field), list) else row.get(field, "") for field in fields})


def resolve_first_degree(
    client: RapidXClient,
    force: bool,
    batch_size: int,
    max_batches: int | None = None,
) -> list[dict]:
    membership, coverage = load_first_degree_state()
    OUT.mkdir(parents=True, exist_ok=True)
    FIRST_DEGREE_COVERAGE.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    cached_profiles = json.loads(FIRST_DEGREE_PROFILE_CACHE.read_text(encoding="utf-8")) if FIRST_DEGREE_PROFILE_CACHE.exists() and not force else []
    existing = {
        str(item["rest_id"]): item
        for item in cached_profiles
        if item.get("rest_id") and item.get("profile_resolution_status", "resolved") == "resolved"
    }
    frontier_ids = {item["rest_id"] for item in membership}
    pending = [value for value in sorted(frontier_ids) if value not in existing]
    for batch_index, start in enumerate(range(0, len(pending), batch_size)):
        if max_batches is not None and batch_index >= max_batches:
            break
        payload = client.get_users_by_ids(pending[start:start + batch_size], force=force, cache_ttl_seconds=86_400 * 30)
        for user in find_user_objects(payload):
            profile = normalize_user(user)
            existing[profile["rest_id"]] = profile
        merged = merge_profiles_with_membership(list(existing.values()), membership)
        FIRST_DEGREE_PROFILE_CACHE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    merged = merge_profiles_with_membership(list(existing.values()), membership)
    FIRST_DEGREE_PROFILE_CACHE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def build_scores() -> list[dict]:
    if not FIRST_DEGREE_PROFILE_CACHE.exists():
        raise RuntimeError("Run --stage resolve-first-degree first")
    membership, coverage = load_first_degree_state()
    cached_profiles = json.loads(FIRST_DEGREE_PROFILE_CACHE.read_text(encoding="utf-8"))
    profiles = merge_profiles_with_membership(cached_profiles, membership)
    FIRST_DEGREE_PROFILE_CACHE.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = sort_scored_profiles([score_profile(item) for item in profiles])
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    OUT.mkdir(parents=True, exist_ok=True)
    FIRST_DEGREE_COVERAGE.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "solomon_first_degree_frontier.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(OUT / "solomon_first_degree_frontier.csv", rows)
    return rows


def collect_relationship_ids(
    client: RapidXClient,
    handle: str,
    kind: str,
    force: bool,
    max_pages: int,
) -> dict:
    """Collect one explicit edge direction with a hard page cap and resumable cursor."""
    if kind not in {"following", "followers"}:
        raise ValueError(f"Unsupported relationship kind: {kind}")
    path = CACHE / f"{kind}-{handle}.json"
    if path.exists() and not force:
        cached = json.loads(path.read_text(encoding="utf-8"))
        return {
            "kind": kind,
            "handle": handle,
            "ids": sorted({str(value) for value in cached.get("ids", [])}),
            "count": len({str(value) for value in cached.get("ids", [])}),
            "pages": int(cached.get("pages", 0)),
            "complete": bool(cached.get("complete")),
            "resume_cursor": cached.get("resume_cursor"),
            "coverage_label": cached.get("coverage_label") or ("complete" if cached.get("complete") else "page_capped_partial"),
            "status": cached.get("status", "cached"),
        }
    ids: set[str] = set()
    cursor = None
    pages = 0
    complete = False
    while pages < max_pages:
        fetch = client.get_following_ids if kind == "following" else client.get_follower_ids
        payload = fetch(handle, count=5000, cursor=cursor, force=force, cache_ttl_seconds=86_400 * 30)
        ids.update(str(value) for value in payload.get("ids", []))
        pages += 1
        raw = payload.get("next_cursor_str", payload.get("next_cursor"))
        cursor = None if raw in (None, "", "0", 0, "-1", -1) else str(raw)
        if not cursor:
            complete = True
            break
    result = {
        "kind": kind,
        "handle": handle,
        "ids": sorted(ids),
        "count": len(ids),
        "pages": pages,
        "complete": complete,
        "resume_cursor": cursor,
        "coverage_label": "complete" if complete else "page_capped_partial",
        "status": "collected",
    }
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def collect_following(client: RapidXClient, handle: str, force: bool, max_pages: int = 3) -> dict:
    """Backward-compatible wrapper for outbound connector edges."""
    return collect_relationship_ids(client, handle, "following", force, max_pages)


def collect_followers(client: RapidXClient, handle: str, force: bool, max_pages: int = 1) -> dict:
    """Cost-capped inbound sample; partial absence must never be read as a negative edge."""
    return collect_relationship_ids(client, handle, "followers", force, max_pages)


def failed_collection(handle: str, kind: str, error: Exception) -> dict:
    return {
        "kind": kind,
        "handle": handle,
        "ids": [],
        "count": 0,
        "pages": 0,
        "complete": False,
        "resume_cursor": None,
        "coverage_label": "failed_no_coverage",
        "status": "failed",
        "error": str(error),
    }


def expand_connectors(
    client: RapidXClient,
    force: bool,
    limit: int,
    following_pages: int = 3,
    follower_pages: int = 1,
) -> list[dict]:
    scores = build_scores()
    selected = [row for row in scores if row["connector_discovery_score"] > 0 and row["handle"]][:limit]
    results = []
    for row in selected:
        expansions = []
        for kind, page_cap in (("following", following_pages), ("followers", follower_pages)):
            try:
                expansions.append(collect_relationship_ids(client, row["handle"], kind, force, page_cap))
            except Exception as exc:
                expansions.append(failed_collection(row["handle"], kind, exc))
        by_kind = {item["kind"]: item for item in expansions}
        results.append({
            "rank": row["rank"],
            "score": row["connector_discovery_score"],
            "priority_score": row["connector_priority_score"],
            "handle": row["handle"],
            "following": by_kind["following"],
            "followers": by_kind["followers"],
            "relationship_expansions": expansions,
        })
    (OUT / "solomon_connector_expansion_log.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def resolve_targets(client: RapidXClient, force: bool) -> list[dict]:
    if not FRONTIER.exists():
        raise RuntimeError("Run scripts/build_v4_candidates.py first")
    results = []
    for item in json.loads(FRONTIER.read_text(encoding="utf-8")):
        handle = str(item.get("handle") or "").lstrip("@")
        try:
            if handle:
                payload = client.get_user(handle, force=force, cache_ttl_seconds=86_400 * 30)
                kind = "profile_lookup"
            else:
                payload = client.search(item["company"], "Top", 20, force=force, cache_ttl_seconds=86_400 * 30)
                kind = "search_requires_manual_match"
            path = CACHE / f"target-{item['priority_rank']:03d}-{re.sub(r'[^a-z0-9]+','-',item['company'].lower()).strip('-')}.json"
            path.write_text(json.dumps({"request":item,"result_type":kind,"response":payload}, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append({"company":item["company"],"handle":handle,"status":"saved","result_type":kind,"path":str(path)})
        except Exception as exc:
            results.append({"company":item["company"],"handle":handle,"status":"failed","error":str(exc)})
    (OUT / "x_target_resolution_log.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["resolve-first-degree","score-first-degree","expand-connectors","resolve-targets","all"], default="all")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument(
        "--max-profile-batches",
        type=int,
        default=0,
        help="Hard cap for this invocation; 0 processes every currently pending profile batch",
    )
    parser.add_argument("--top-connectors", type=int, default=50)
    parser.add_argument("--connector-following-pages", type=int, default=3)
    parser.add_argument("--connector-follower-pages", type=int, default=1)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    client = None if args.stage == "score-first-degree" else RapidXClient(cache_dir=CACHE / "requests", env_file=args.env_file)
    summary = {}
    if args.stage in {"resolve-first-degree","all"}:
        profiles = resolve_first_degree(
            client,
            args.force,
            args.batch_size,
            max_batches=args.max_profile_batches or None,
        )
        summary["first_degree_profiles"] = {
            "frontier": len(profiles),
            "resolved": sum(item.get("profile_resolution_status") == "resolved" for item in profiles),
            "unresolved": sum(item.get("profile_resolution_status") != "resolved" for item in profiles),
            "batch_size": args.batch_size,
            "batch_cap": args.max_profile_batches or None,
        }
    if args.stage in {"score-first-degree","all"}:
        summary["profiles_scored"] = len(build_scores())
    if args.stage in {"expand-connectors","all"}:
        summary["connectors_expanded"] = len(expand_connectors(
            client,
            args.force,
            args.top_connectors,
            following_pages=args.connector_following_pages,
            follower_pages=args.connector_follower_pages,
        ))
    if args.stage in {"resolve-targets","all"}:
        summary["targets_processed"] = len(resolve_targets(client, args.force))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
