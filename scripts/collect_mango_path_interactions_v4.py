#!/usr/bin/env python3
"""Collect bounded Rapid X interaction evidence for Mango seed target paths."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from mangobd.rapid_x import RapidXClient
try:  # package import in tests
    from scripts.collect_x_interactions_v4 import interaction_rows, safe_slug, write_json
except ModuleNotFoundError:  # direct script execution
    from collect_x_interactions_v4 import interaction_rows, safe_slug, write_json


ROOT = Path(__file__).resolve().parents[1]
PATHS = ROOT / "outputs" / "pilot_v4" / "mango_seed_target_paths.json"
RAW_DIR = ROOT / "data" / "cache" / "rapidx" / "v4" / "mango-interactions"
OUT_JSON = ROOT / "outputs" / "pilot_v4" / "mango_path_interactions_v4.json"
OUT_CSV = ROOT / "outputs" / "pilot_v4" / "mango_path_interactions_v4.csv"
SUMMARY = ROOT / "outputs" / "pilot_v4" / "mango_path_interactions_summary_v4.json"


def adjacent_pairs(targets: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for target in targets:
        if target.get("graph_reachable") is not True:
            continue
        for path in target.get("mango_secondary_paths") or []:
            labels = [str(value).lstrip("@") for value in path.get("path_labels") or []]
            if len(labels) != 3 or any(not label for label in labels):
                continue
            for index, (left, right) in enumerate(zip(labels, labels[1:])):
                key = (left.lower(), right.lower())
                row = grouped.setdefault(
                    key,
                    {
                        "left_handle": left,
                        "right_handle": right,
                        "path_segment": "mango_root_to_seed" if index == 0 else "seed_to_target",
                        "companies": [],
                    },
                )
                if target["company"] not in row["companies"]:
                    row["companies"].append(target["company"])
    return sorted(grouped.values(), key=lambda row: (row["path_segment"], row["left_handle"].lower(), row["right_handle"].lower()))


def deduplicate_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        tweet_id = str(row.get("tweet_id") or "")
        if not tweet_id:
            continue
        key = (
            str(row.get("source_handle") or "").lower(),
            str(row.get("destination_handle") or "").lower(),
            tweet_id,
        )
        current = merged.get(key)
        if current is None:
            merged[key] = row
            continue
        current["companies"] = sorted(set(current.get("companies") or []) | set(row.get("companies") or []))
        current["raw_cache_files"] = sorted(set(current.get("raw_cache_files") or []) | set(row.get("raw_cache_files") or []))
    return sorted(merged.values(), key=lambda row: (row.get("created_at") or "", row["tweet_id"]), reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--max-pairs", type=int, default=0, help="0 audits every unique adjacent pair")
    args = parser.parse_args()

    artifact = json.loads(PATHS.read_text(encoding="utf-8"))
    all_pairs = adjacent_pairs(artifact.get("targets") or [])
    pairs = all_pairs[: args.max_pairs] if args.max_pairs > 0 else all_pairs
    client = RapidXClient(cache_dir=RAW_DIR / "requests", env_file=args.env_file)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    observations: list[dict[str, Any]] = []
    query_audit: list[dict[str, Any]] = []

    for pair in pairs:
        for source, destination in (
            (pair["left_handle"], pair["right_handle"]),
            (pair["right_handle"], pair["left_handle"]),
        ):
            query = f"from:{source} @{destination}"
            raw_path = RAW_DIR / f"{safe_slug(source)}--to--{safe_slug(destination)}.json"
            relative = str(raw_path.relative_to(ROOT))
            try:
                payload = client.search(query, "Latest", args.count, force=args.force, cache_ttl_seconds=86_400 * 30)
                write_json(raw_path, {"query": query, "response": payload})
                hits = interaction_rows(payload, source, destination, query)
                for hit in hits:
                    hit.update({
                        "path_segment": pair["path_segment"],
                        "companies": pair["companies"],
                        "raw_cache_files": [relative],
                    })
                observations.extend(hits)
                query_audit.append({
                    "source_handle": source,
                    "destination_handle": destination,
                    "query": query,
                    "path_segment": pair["path_segment"],
                    "companies": pair["companies"],
                    "matching_observations": len(hits),
                    "sample_status": "observed" if hits else "not_observed_in_bounded_query_sample",
                    "negative_inference_allowed": False,
                    "raw_cache_file": relative,
                })
            except Exception as exc:
                query_audit.append({
                    "source_handle": source,
                    "destination_handle": destination,
                    "query": query,
                    "path_segment": pair["path_segment"],
                    "companies": pair["companies"],
                    "matching_observations": None,
                    "sample_status": "query_failed",
                    "negative_inference_allowed": False,
                    "error": str(exc),
                    "raw_cache_file": relative,
                })

    observations = deduplicate_observations(observations)
    observed_pairs = {
        (row["source_handle"].lower(), row["destination_handle"].lower())
        for row in observations
    }
    audited_at = datetime.now(timezone.utc).isoformat()
    output = {
        "schema_version": "v4",
        "audited_at": audited_at,
        "scope": "Adjacent pairs on positive Mango-rooted secondary target paths.",
        "interpretation_rule": "Positive exact-author/exact-counterparty results prove public X interactions only. Zero hits do not prove no interaction; human relationship and intro willingness remain unvalidated.",
        "pair_count_total": len(all_pairs),
        "pair_count_audited": len(pairs),
        "query_count": len(query_audit),
        "query_failures": sum(row["sample_status"] == "query_failed" for row in query_audit),
        "observed_interaction_count": len(observations),
        "observed_directed_pair_count": len(observed_pairs),
        "query_audit": query_audit,
        "observations": observations,
    }
    write_json(OUT_JSON, output)

    fields = [
        "interaction_id", "source_handle", "destination_handle", "interaction_type", "tweet_id", "tweet_url",
        "created_at", "text", "path_segment", "companies", "fact_status", "confidence",
        "human_relationship_inference", "query", "raw_cache_files",
    ]
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in observations:
            writer.writerow({
                **row,
                "companies": " | ".join(row.get("companies") or []),
                "raw_cache_files": " | ".join(row.get("raw_cache_files") or []),
            })
    summary = {
        "schema_version": "v4",
        "audited_at": audited_at,
        "reachable_companies": (artifact.get("summary") or {}).get("companies_graph_reachable_from_mango"),
        "unique_adjacent_pairs_total": len(all_pairs),
        "unique_adjacent_pairs_audited": len(pairs),
        "directed_queries": len(query_audit),
        "query_failures": output["query_failures"],
        "observed_interactions": len(observations),
        "observed_directed_pairs": len(observed_pairs),
        "not_observed_queries": sum(row["sample_status"] == "not_observed_in_bounded_query_sample" for row in query_audit),
        "negative_inference_allowed": False,
        "outputs": [str(OUT_JSON.relative_to(ROOT)), str(OUT_CSV.relative_to(ROOT))],
    }
    write_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
