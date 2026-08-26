#!/usr/bin/env python3
"""Collect cost-capped, directional Rapid X coverage for Mango and employee seed accounts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mangobd.rapid_x import RapidXClient
from mangobd.x_network import TERMINAL_CURSORS, find_user_object, load_best_id_cache


ROOT = Path(__file__).resolve().parents[1]
ENTITIES = ROOT / "data" / "pilot" / "entities.json"
CACHE_ROOT = ROOT / "data" / "cache" / "rapidx"
CACHE = CACHE_ROOT / "v4" / "mango-seeds"
OUT = ROOT / "outputs" / "pilot_v4"


def seed_accounts(entities: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Return Mango plus people/seed nodes, excluding companies and creators."""
    rows = []
    for entity in entities:
        entity_id = str(entity.get("id", ""))
        handle = str((entity.get("handles") or {}).get("x") or "").lstrip("@")
        if not handle:
            continue
        if entity_id == "mango" or entity_id.startswith(("person_", "seed_")):
            rows.append({"entity_id": entity_id, "name": str(entity.get("name", "")), "handle": handle})
    return sorted(rows, key=lambda item: (item["entity_id"] != "mango", item["entity_id"]))


def normalize_profile(user: dict[str, Any], seed: dict[str, str]) -> dict[str, Any]:
    legacy = user.get("legacy") or {}
    core = user.get("core") or {}
    rest_id = user.get("rest_id") or user.get("id_str") or user.get("id")
    return {
        **seed,
        "rest_id": str(rest_id) if rest_id is not None else None,
        "resolved_handle": legacy.get("screen_name") or core.get("screen_name") or user.get("screen_name"),
        "display_name": legacy.get("name") or core.get("name") or user.get("name"),
        "description": legacy.get("description") or user.get("description") or "",
        "followers_count": legacy.get("followers_count", user.get("followers_count")),
        "following_count": legacy.get("friends_count", user.get("friends_count")),
        "identity_status": "exact_handle_match"
        if str(legacy.get("screen_name") or core.get("screen_name") or user.get("screen_name") or "").casefold()
        == seed["handle"].casefold()
        else "handle_mismatch_requires_review",
    }


def collect_profile(client: RapidXClient, seed: dict[str, str], force: bool) -> dict[str, Any]:
    payload = client.get_user(seed["handle"], force=force, cache_ttl_seconds=86_400 * 30)
    user = find_user_object(payload)
    if not user:
        return {**seed, "rest_id": None, "identity_status": "response_missing_user"}
    return normalize_profile(user, seed)


def _cursor(payload: dict[str, Any]) -> str | None:
    raw = payload.get("next_cursor_str", payload.get("next_cursor"))
    return None if raw in TERMINAL_CURSORS else str(raw)


def collect_direction(
    client: RapidXClient,
    handle: str,
    kind: str,
    max_pages: int,
    force: bool,
) -> dict[str, Any]:
    if kind not in {"following", "followers"}:
        raise ValueError(f"Unsupported direction: {kind}")
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{kind}-{handle}.json"
    ids: set[str] = set()
    pages = 0
    cursor = None
    if path.exists() and not force:
        saved = json.loads(path.read_text(encoding="utf-8"))
        ids.update(str(value) for value in saved.get("ids", []))
        pages = int(saved.get("pages", 0))
        cursor = saved.get("resume_cursor")
        if saved.get("complete") or pages >= max_pages:
            return saved
    elif not force:
        legacy_ids, legacy_complete, legacy_pages = load_best_id_cache(CACHE_ROOT, handle, kind)
        if legacy_ids:
            ids.update(legacy_ids)
            pages = legacy_pages
            if legacy_complete:
                result = {
                    "handle": handle,
                    "kind": kind,
                    "ids": sorted(ids),
                    "count": len(ids),
                    "pages": pages,
                    "complete": True,
                    "resume_cursor": None,
                    "coverage_label": "complete_existing_rapid_x_cache",
                }
                path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                return result

    fetch = client.get_following_ids if kind == "following" else client.get_follower_ids
    complete = False
    while pages < max_pages:
        payload = fetch(handle, count=5000, cursor=cursor, force=force, cache_ttl_seconds=86_400 * 30)
        ids.update(str(value) for value in payload.get("ids", []))
        pages += 1
        cursor = _cursor(payload)
        if cursor is None:
            complete = True
            break
    result = {
        "handle": handle,
        "kind": kind,
        "ids": sorted(ids),
        "count": len(ids),
        "pages": pages,
        "complete": complete,
        "resume_cursor": cursor,
        "coverage_label": "complete" if complete else "page_capped_partial",
    }
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["profiles", "relations", "all"], default="all")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--following-pages", type=int, default=3)
    parser.add_argument("--follower-pages", type=int, default=1)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-seeds", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    entities = json.loads(ENTITIES.read_text(encoding="utf-8"))
    seeds = seed_accounts(entities)[args.offset:]
    if args.max_seeds:
        seeds = seeds[: args.max_seeds]
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    client = RapidXClient(cache_dir=CACHE / "requests", env_file=args.env_file)
    profiles: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    if args.stage in {"profiles", "all"}:
        for seed in seeds:
            try:
                profiles.append(collect_profile(client, seed, args.force))
            except Exception as exc:
                failures.append({"handle": seed["handle"], "stage": "profile", "error": str(exc)})
        (CACHE / "profiles.json").write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.stage in {"relations", "all"}:
        for seed in seeds:
            for kind, page_cap in (("following", args.following_pages), ("followers", args.follower_pages)):
                try:
                    relations.append(collect_direction(client, seed["handle"], kind, page_cap, args.force))
                except Exception as exc:
                    failures.append({"handle": seed["handle"], "stage": kind, "error": str(exc)})

    summary = {
        "stage": args.stage,
        "seed_count": len(seeds),
        "profile_records": len(profiles),
        "directional_records": len(relations),
        "failures": failures,
        "following_complete": sum(item["kind"] == "following" and item["complete"] for item in relations),
        "followers_complete": sum(item["kind"] == "followers" and item["complete"] for item in relations),
    }
    (OUT / "mango_seed_collection_log.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
