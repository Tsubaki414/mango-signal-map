#!/usr/bin/env python3
"""Collect cost-bounded Apify observations into Mango's review queue.

No Actor output is promoted into confirmed commercial evidence. Raw items are
cached under the gitignored data/cache tree; normalized, sourced observations
land in an idempotent queue whose human review fields survive refreshes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mangobd.apify import ApifyClient  # noqa: E402
from mangobd.apify_ingestion import (  # noqa: E402
    contact_actor_input,
    instagram_actor_input,
    linkedin_actor_input,
    merge_review_queue,
    normalize_apify_items,
    safe_run_manifest,
    utc_now,
    website_actor_input,
    youtube_actor_input,
)


DEFAULT_CONFIG = ROOT / "config" / "apify_sources.json"
DEFAULT_CACHE = ROOT / "data" / "cache" / "apify"
DEFAULT_QUEUE = ROOT / "data" / "pilot_v5" / "apify_research_review_queue.json"
DEFAULT_RUN_INDEX = ROOT / "data" / "pilot_v5" / "apify_collection_runs.json"


def read_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_run_index(path: Path, row: dict[str, Any]) -> None:
    payload = {"schema_version": 1, "runs": []}
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    by_id = {item["actor_run_id"]: item for item in payload.get("runs", [])}
    by_id[row["actor_run_id"]] = row
    write_json(
        path,
        {
            "schema_version": 1,
            "generated_at": utc_now(),
            "runs": sorted(by_id.values(), key=lambda item: item.get("started_at") or ""),
        },
    )


def collection_summary(
    *,
    company_id: str,
    source_kind: str,
    run: dict[str, Any],
    observations: list[dict[str, Any]],
    queue_path: Path,
    raw_path: Path,
) -> dict[str, Any]:
    contacts = {
        key: sorted(
            {
                value
                for observation in observations
                for value in observation.get("candidate_contacts", {}).get(key, [])
            }
        )
        for key in (
            "emails",
            "linkedin_urls",
            "youtube_urls",
            "instagram_urls",
            "tiktok_urls",
            "x_urls",
            "contact_urls",
        )
    }
    return {
        "company_id": company_id,
        "source_kind": source_kind,
        "actor_run_id": run.get("id"),
        "status": run.get("status"),
        "dataset_id": run.get("defaultDatasetId"),
        "usage_total_usd": run.get("usageTotalUsd"),
        "observation_count": len(observations),
        "review_status": "unreviewed",
        "candidate_contact_counts": {key: len(values) for key, values in contacts.items()},
        "review_queue": str(queue_path.relative_to(ROOT)),
        "raw_cache": str(raw_path.relative_to(ROOT)),
    }


def persist_completed_run(
    *,
    client: ApifyClient,
    source_kind: str,
    source_config: dict[str, Any],
    company_id: str,
    company_name: str,
    actor_input: dict[str, Any],
    run: dict[str, Any],
    max_items: int,
    cache_dir: Path,
    queue_path: Path,
    run_index_path: Path,
) -> dict[str, Any]:
    dataset_id = run.get("defaultDatasetId")
    if not dataset_id:
        raise SystemExit(f"Actor run {run.get('id')} did not return a dataset")
    items = client.get_dataset_items(dataset_id, limit=max_items)
    run_dir = cache_dir / source_kind / run["id"]
    raw_path = run_dir / "items.json"
    manifest_path = run_dir / "manifest.json"
    write_json(raw_path, items)
    manifest = safe_run_manifest(run, actor_input=actor_input)
    manifest.update(
        {
            "actor_store_id": source_config["actor_id"],
            "company_id": company_id,
            "company_name": company_name,
            "source_kind": source_kind,
            "raw_item_count": len(items),
            "raw_cache_path": str(raw_path.relative_to(ROOT)),
        }
    )
    write_json(manifest_path, manifest)
    observations = normalize_apify_items(
        items,
        company_id=company_id,
        company_name=company_name,
        source_kind=source_kind,
        actor_id=source_config["actor_id"],
        actor_build=run.get("buildNumber") or source_config.get("build"),
        run_id=run["id"],
        dataset_id=dataset_id,
        raw_cache_path=str(raw_path.relative_to(ROOT)),
    )
    merge_review_queue(queue_path, observations)
    merge_run_index(run_index_path, manifest)
    return collection_summary(
        company_id=company_id,
        source_kind=source_kind,
        run=run,
        observations=observations,
        queue_path=queue_path,
        raw_path=raw_path,
    )
def run_health(args: argparse.Namespace) -> int:
    user = ApifyClient(env_file=args.env_file).get_current_user()
    safe = {
        "ok": bool(user.get("id")),
        "username": user.get("username"),
        "organization_token": bool(user.get("organizationOwnerUserId")),
        "plan": (user.get("plan") or {}).get("id") if isinstance(user.get("plan"), dict) else None,
    }
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


def run_recent_runs(args: argparse.Namespace) -> int:
    rows = ApifyClient(env_file=args.env_file).list_runs(limit=args.limit)
    safe = [
        {
            "id": row.get("id"),
            "act_id": row.get("actId"),
            "status": row.get("status"),
            "started_at": row.get("startedAt"),
            "finished_at": row.get("finishedAt"),
            "dataset_id": row.get("defaultDatasetId"),
            "usage_total_usd": row.get("usageTotalUsd"),
        }
        for row in rows
    ]
    print(json.dumps(safe, ensure_ascii=False, indent=2))
    return 0


def run_refresh_runs(args: argparse.Namespace) -> int:
    """Refresh final cost/status metadata for already persisted runs."""

    if not args.run_index.exists():
        raise SystemExit(f"Run index not found: {args.run_index}")
    payload = json.loads(args.run_index.read_text(encoding="utf-8"))
    client = ApifyClient(env_file=args.env_file)
    refreshed = []
    for old in payload.get("runs", []):
        run = client.get_run(old["actor_run_id"])
        row = safe_run_manifest(run, actor_input=old.get("actor_input") or {})
        for key in (
            "actor_store_id", "company_id", "company_name", "source_kind",
            "raw_item_count", "raw_cache_path",
        ):
            if key in old:
                row[key] = old[key]
        refreshed.append(row)
        raw_cache_path = row.get("raw_cache_path")
        if raw_cache_path:
            manifest_path = (ROOT / raw_cache_path).parent / "manifest.json"
            if manifest_path.exists():
                write_json(manifest_path, row)
    refreshed.sort(key=lambda row: row.get("started_at") or "")
    write_json(
        args.run_index,
        {"schema_version": 1, "generated_at": utc_now(), "runs": refreshed},
    )
    print(json.dumps({
        "refreshed_runs": len(refreshed),
        "total_usage_usd": round(sum(float(row.get("usage_total_usd") or 0) for row in refreshed), 6),
        "all_succeeded": all(row.get("status") == "SUCCEEDED" for row in refreshed),
    }, ensure_ascii=False, indent=2))
    return 0


def run_collect(args: argparse.Namespace) -> int:
    config = read_config(args.config)
    source = config["sources"].get(args.source)
    if not source:
        raise SystemExit(f"Unknown source: {args.source}")
    actor_id = source.get("actor_id")
    if not actor_id:
        raise SystemExit(f"Source {args.source} has no approved Actor yet; inspect and select one first")
    max_items = args.max_items or int(source["max_items"])
    max_charge = args.max_charge_usd or float(source["max_total_charge_usd"])
    if args.source == "official_web":
        actor_input = website_actor_input(args.url, max_pages=max_items, max_depth=args.max_depth)
    elif args.source == "linkedin":
        actor_input = linkedin_actor_input(args.url, max_items=max_items, job_titles=args.job_title or None)
    elif args.source == "youtube_sponsorship":
        actor_input = youtube_actor_input(args.query or [], max_results=max_items)
    elif args.source == "public_contact":
        actor_input = contact_actor_input(args.url, max_requests=max_items, max_depth=args.max_depth)
    else:
        actor_input = instagram_actor_input(args.url, max_results=max_items)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "actor_id": actor_id,
                    "build": source.get("build"),
                    "max_total_charge_usd": max_charge,
                    "input": actor_input,
                    "will_auto_confirm": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    client = ApifyClient(env_file=args.env_file)
    started = client.start_actor(
        actor_id,
        actor_input,
        build=source.get("build"),
        timeout_seconds=args.actor_timeout,
        max_total_charge_usd=max_charge,
    )
    run = client.wait_for_run(started["id"], wait_timeout_seconds=args.wait_timeout)
    summary = persist_completed_run(
        client=client,
        source_kind=args.source,
        source_config=source,
        company_id=args.company_id,
        company_name=args.company_name,
        actor_input=actor_input,
        run=run,
        max_items=max_items,
        cache_dir=args.cache_dir,
        queue_path=args.queue,
        run_index_path=args.run_index,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_ingest_existing(args: argparse.Namespace) -> int:
    config = read_config(args.config)
    source = config["sources"].get(args.source)
    if not source or not source.get("actor_id"):
        raise SystemExit(f"Unknown or unconfigured source: {args.source}")
    client = ApifyClient(env_file=args.env_file)
    actor = client.get_actor(source["actor_id"])
    run = client.get_run(args.run_id)
    if run.get("actId") != actor.get("id"):
        raise SystemExit("Run actor does not match the configured source Actor")
    if run.get("status") != "SUCCEEDED":
        raise SystemExit(f"Run is not ingestible: {run.get('status')}")
    store_id = run.get("defaultKeyValueStoreId")
    actor_input: dict[str, Any] = {"recovered_run": True}
    if store_id:
        recovered = client.get_key_value_record(store_id, "INPUT")
        if isinstance(recovered, dict):
            actor_input = recovered
    summary = persist_completed_run(
        client=client,
        source_kind=args.source,
        source_config=source,
        company_id=args.company_id,
        company_name=args.company_name,
        actor_input=actor_input,
        run=run,
        max_items=args.max_items or int(source["max_items"]),
        cache_dir=args.cache_dir,
        queue_path=args.queue,
        run_index_path=args.run_index,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def run_rebuild_cache(args: argparse.Namespace) -> int:
    manifests = sorted(args.cache_dir.glob("*/*/manifest.json"))
    totals = {"runs": 0, "raw_items": 0, "observations": 0, "eligible_for_evidence_review": 0}
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        raw_path_value = manifest.get("raw_cache_path")
        if not raw_path_value:
            continue
        raw_path = ROOT / raw_path_value
        if not raw_path.exists():
            continue
        items = json.loads(raw_path.read_text(encoding="utf-8"))
        actor_id = manifest.get("actor_store_id") or "apify/website-content-crawler"
        observations = normalize_apify_items(
            items,
            company_id=manifest["company_id"],
            company_name=manifest["company_name"],
            source_kind=manifest["source_kind"],
            actor_id=actor_id,
            actor_build=manifest.get("actor_build_number"),
            run_id=manifest["actor_run_id"],
            dataset_id=manifest["dataset_id"],
            raw_cache_path=raw_path_value,
            collected_at=manifest.get("finished_at") or manifest.get("started_at"),
        )
        merge_review_queue(args.queue, observations)
        totals["runs"] += 1
        totals["raw_items"] += len(items)
        totals["observations"] += len(observations)
        totals["eligible_for_evidence_review"] += sum(
            bool(row.get("eligible_for_evidence_review")) for row in observations
        )
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--env-file", type=Path, default=ROOT / ".env")
    root.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="validate the token without starting an Actor")
    recent = commands.add_parser("recent-runs", help="show safe metadata for recent Actor runs")
    recent.add_argument("--limit", type=int, default=10)
    refresh = commands.add_parser("refresh-runs", help="refresh final status and cost for persisted Actor runs")
    refresh.add_argument("--run-index", type=Path, default=DEFAULT_RUN_INDEX)
    rebuild = commands.add_parser("rebuild-cache", help="re-normalize cached runs without starting an Actor")
    rebuild.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    rebuild.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    collect = commands.add_parser("collect", help="run one bounded Actor collection")
    collect.add_argument("--source", choices=["official_web", "linkedin", "youtube_sponsorship", "public_contact", "campaign_creator"], required=True)
    collect.add_argument("--company-id", required=True)
    collect.add_argument("--company-name", required=True)
    collect.add_argument("--url", action="append", default=[], help="repeat for each start/profile/company URL")
    collect.add_argument("--query", action="append", help="repeat for YouTube search queries")
    collect.add_argument("--job-title", action="append", help="repeat to override LinkedIn operator-role filters")
    collect.add_argument("--max-items", type=int)
    collect.add_argument("--max-depth", type=int, default=0)
    collect.add_argument("--max-charge-usd", type=float)
    collect.add_argument("--actor-timeout", type=int, default=600)
    collect.add_argument("--wait-timeout", type=int, default=900)
    collect.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    collect.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    collect.add_argument("--run-index", type=Path, default=DEFAULT_RUN_INDEX)
    collect.add_argument("--dry-run", action="store_true")
    ingest = commands.add_parser("ingest-run", help="recover and ingest an already-started successful run")
    ingest.add_argument("--run-id", required=True)
    ingest.add_argument("--source", choices=["official_web", "linkedin", "youtube_sponsorship", "public_contact", "campaign_creator"], required=True)
    ingest.add_argument("--company-id", required=True)
    ingest.add_argument("--company-name", required=True)
    ingest.add_argument("--max-items", type=int)
    ingest.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    ingest.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    ingest.add_argument("--run-index", type=Path, default=DEFAULT_RUN_INDEX)
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "health":
        return run_health(args)
    if args.command == "recent-runs":
        return run_recent_runs(args)
    if args.command == "refresh-runs":
        return run_refresh_runs(args)
    if args.command == "rebuild-cache":
        return run_rebuild_cache(args)
    if args.command == "ingest-run":
        return run_ingest_existing(args)
    return run_collect(args)


if __name__ == "__main__":
    raise SystemExit(main())
