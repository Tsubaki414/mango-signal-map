#!/usr/bin/env python3
"""Collect the Rapid X inputs needed for Solomon-rooted 1st/2nd/3rd-degree paths."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from mangobd.rapid_x import RapidXClient
from mangobd.x_network import load_best_id_cache


ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / "data" / "cache" / "rapidx"
CACHE = CACHE_ROOT / "v3"
TARGETS = ROOT / "data" / "pilot_v3" / "target_x_handles.json"
OPERATORS = ROOT / "data" / "pilot_v3" / "operator_x_search_queue.json"
ENTITIES = ROOT / "data" / "pilot" / "entities.json"
RUN_LOG = ROOT / "outputs" / "pilot_v3" / "solomon_x_collection_log.json"


def next_cursor(payload: dict[str, Any]) -> str | None:
    raw = payload.get("next_cursor_str", payload.get("next_cursor"))
    return None if raw in (None, "", "0", 0, "-1", -1) else str(raw)


def collect_ids(
    client: RapidXClient,
    handle: str,
    kind: str,
    fetch: Callable[..., dict[str, Any]],
    max_pages: int,
    force: bool,
    page_size: int = 5000,
) -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{kind}-{handle}.json"
    ids: set[str] = set()
    cursor: str | None = None
    pages = 0
    if path.exists() and not force:
        saved = json.loads(path.read_text(encoding="utf-8"))
        ids.update(str(value) for value in saved.get("ids", []))
        cursor = saved.get("resume_cursor")
        pages = int(saved.get("pages", 0))
        if saved.get("complete") or pages >= max_pages:
            return saved
    complete = False
    while pages < max_pages:
        payload = fetch(handle, count=page_size, cursor=cursor, force=force, cache_ttl_seconds=86_400 * 30)
        pages += 1
        ids.update(str(value) for value in payload.get("ids", []))
        cursor = next_cursor(payload)
        if not cursor:
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


def connector_handles() -> list[str]:
    entities = json.loads(ENTITIES.read_text(encoding="utf-8"))
    return sorted({
        entity.get("handles", {}).get("x")
        for entity in entities
        if entity.get("handles", {}).get("x") and entity["id"] != "mango"
    })


def resolve_profiles(client: RapidXClient, force: bool) -> list[dict]:
    results = []
    for target in json.loads(TARGETS.read_text(encoding="utf-8")):
        handle = target["handle"]
        try:
            payload = client.get_user(handle, force=force, cache_ttl_seconds=86_400 * 30)
            explicit = CACHE_ROOT / f"target-{handle}.json"
            explicit.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append({"company": target["company"], "handle": handle, "status": "saved"})
        except Exception as exc:
            results.append({"company": target["company"], "handle": handle, "status": "failed", "error": str(exc)})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=["profiles", "operators", "solomon-followers", "connectors", "target-followers", "target-followings", "all"],
        default="all",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help="dotenv file read in-process; values are never logged")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    client = RapidXClient(cache_dir=CACHE / "requests", env_file=args.env_file)
    log: list[dict] = []

    if args.scope in {"profiles", "all"}:
        log.extend(resolve_profiles(client, args.force))
    if args.scope in {"operators", "all"}:
        for item in json.loads(OPERATORS.read_text(encoding="utf-8")):
            try:
                payload = client.search(item["query"], "Top", 20, force=args.force, cache_ttl_seconds=86_400 * 30)
                path = CACHE / f"operator-search-{item['priority']:02d}.json"
                path.write_text(json.dumps({"search":item,"response":payload}, ensure_ascii=False, indent=2), encoding="utf-8")
                log.append({"company":item["company"],"person":item["person"],"kind":"operator_search","status":"saved","path":str(path)})
            except Exception as exc:
                log.append({"company":item["company"],"person":item["person"],"kind":"operator_search","status":"failed","error":str(exc)})
    if args.scope in {"solomon-followers", "all"}:
        try:
            log.append(collect_ids(client, "Solomon_Nahhh", "followers", client.get_follower_ids, 3, args.force))
        except Exception as exc:
            log.append({"handle":"Solomon_Nahhh","kind":"followers","status":"failed","error":str(exc)})
    if args.scope in {"connectors", "all"}:
        for handle in connector_handles():
            try:
                cached_ids, cached_complete, cached_pages = load_best_id_cache(CACHE_ROOT, handle, "following")
                if cached_complete and not args.force:
                    log.append({
                        "handle": handle,
                        "kind": "following",
                        "count": len(cached_ids),
                        "pages": cached_pages,
                        "complete": True,
                        "coverage_label": "complete_existing_rapid_x_cache",
                    })
                    continue
                log.append(collect_ids(client, handle, "following", client.get_following_ids, 5, args.force))
            except Exception as exc:
                log.append({"handle":handle,"kind":"following","status":"failed","error":str(exc)})
    if args.scope in {"target-followers", "target-followings", "all"}:
        kinds = ["followers", "following"] if args.scope == "all" else ["followers" if args.scope == "target-followers" else "following"]
        for kind in kinds:
            fetch = client.get_follower_ids if kind == "followers" else client.get_following_ids
            max_pages = 1 if kind == "followers" else 3
            for target in json.loads(TARGETS.read_text(encoding="utf-8")):
                try:
                    result = collect_ids(client, target["handle"], kind, fetch, max_pages, args.force)
                    if kind == "followers" and not result["complete"]:
                        result["coverage_label"] = "one_page_sample"
                    log.append(result)
                except Exception as exc:
                    log.append({"company":target["company"],"handle":target["handle"],"kind":kind,"status":"failed","error":str(exc)})

    RUN_LOG.write_text(json.dumps({"scope":args.scope,"records":log}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"scope":args.scope,"records":len(log),"failed":sum(item.get("status")=="failed" for item in log),"log":str(RUN_LOG)},indent=2))


if __name__ == "__main__":
    main()
