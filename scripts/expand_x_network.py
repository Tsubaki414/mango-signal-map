#!/usr/bin/env python3
"""Resume a cost-capped Rapid X relationship expansion once a valid key is set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from mangobd.rapid_x import RapidXClient


ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "data" / "pilot_v2" / "x_scan_queue.json"
CACHE = ROOT / "data" / "cache" / "rapidx" / "v2"
OUTPUT = ROOT / "outputs" / "pilot_v2" / "x_network_results.json"


def next_cursor(payload: dict[str, Any]) -> str | None:
    raw = payload.get("next_cursor_str", payload.get("next_cursor"))
    if raw in (None, "", "0", 0, "-1", -1):
        return None
    return str(raw)


def collect_ids(
    handle: str,
    kind: str,
    fetch: Callable[..., dict[str, Any]],
    page_size: int,
    max_pages: int,
    force: bool,
) -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    combined_path = CACHE / f"{kind}-{handle}.json"
    cursor: str | None = None
    ids: set[str] = set()
    pages = 0
    if combined_path.exists() and not force:
        saved = json.loads(combined_path.read_text(encoding="utf-8"))
        if saved.get("complete") or int(saved.get("pages", 0)) >= max_pages:
            return saved
        cursor = saved.get("resume_cursor")
        ids.update(str(value) for value in saved.get("ids", []))
        pages = int(saved.get("pages", 0))
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
    }
    combined_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scope",
        choices=["mango-followers", "remaining-seeds", "target-followings", "expanded-targets", "target-follower-samples", "all"],
        default="all",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))["collection_order"]
    client = RapidXClient(cache_dir=CACHE / "requests")
    collected: list[dict[str, Any]] = []

    def wanted(priority: int) -> bool:
        return args.scope == "all" or {
            "mango-followers": 1,
            "remaining-seeds": 2,
            "target-followings": 3,
            "expanded-targets": 4,
            "target-follower-samples": 5,
        }.get(args.scope) == priority

    for item in queue:
        priority = int(item["priority"])
        if priority > 5 or not wanted(priority):
            continue
        handles = [item["username"]] if "username" in item else item.get("usernames", [])
        page_size = int(item.get("page_size", 5000))
        max_pages = int(item.get("max_pages", item.get("max_pages_per_account", 3)))
        for handle in handles:
            try:
                if priority in {1, 5}:
                    result = collect_ids(handle, "followers", client.get_follower_ids, page_size, max_pages, args.force)
                    result["coverage_label"] = "complete_if_no_resume_cursor" if priority == 1 else "one_page_sample_unless_complete"
                else:
                    if priority == 4:
                        client.get_user(handle, force=args.force, cache_ttl_seconds=86_400 * 30)
                    result = collect_ids(handle, "following", client.get_following_ids, page_size, max_pages, args.force)
                    result["coverage_label"] = "complete" if result["complete"] else "page_capped_partial"
                collected.append(result)
            except Exception as exc:  # keep the queue resumable when one account fails
                collected.append({
                    "handle": handle,
                    "kind": "followers" if priority in {1, 5} else "following",
                    "complete": False,
                    "coverage_label": "request_failed",
                    "error": str(exc),
                })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"scope": args.scope, "collections": collected}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "collections": len(collected), "ids": sum(int(x.get("count", 0)) for x in collected)}, indent=2))


if __name__ == "__main__":
    main()
