#!/usr/bin/env python3
"""Collect and classify Rapid X interaction evidence along observed v4 paths.

The collector intentionally searches only adjacent accounts on retained primary
paths.  A zero-result query means "not observed in this bounded search sample";
it is never promoted to proof that two accounts have never interacted.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from mangobd.rapid_x import RapidXClient


ROOT = Path(__file__).resolve().parents[1]
PATHS = ROOT / "outputs" / "pilot_v4" / "solomon_paths_expanded_v4.json"
RAW_DIR = ROOT / "data" / "cache" / "rapidx" / "v4" / "interactions"
OUT_JSON = ROOT / "outputs" / "pilot_v4" / "x_path_interactions_v4.json"
OUT_CSV = ROOT / "outputs" / "pilot_v4" / "x_path_interactions_v4.csv"
SUMMARY = ROOT / "outputs" / "pilot_v4" / "x_path_interactions_summary_v4.json"

HANDLE_RE = re.compile(r"\(@([A-Za-z0-9_]{1,15})\)\s*$")


def handle_from_label(label: str) -> str | None:
    match = HANDLE_RE.search(label or "")
    return match.group(1) if match else None


def primary_adjacent_pairs(paths: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return unique adjacent pairs from technically reachable primary paths."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in paths:
        if item.get("graph_reachable") is not True:
            continue
        labels = item.get("primary_path_labels") or []
        handles = [handle_from_label(label) for label in labels]
        if not handles or any(handle is None for handle in handles):
            continue
        for index, (left, right) in enumerate(zip(handles, handles[1:])):
            assert left is not None and right is not None
            key = (left.lower(), right.lower())
            row = grouped.setdefault(
                key,
                {
                    "left_handle": left,
                    "right_handle": right,
                    "path_segment": "root_to_connector" if index == 0 else "connector_to_target",
                    "companies": [],
                },
            )
            if item.get("company") not in row["companies"]:
                row["companies"].append(item.get("company"))
    return sorted(grouped.values(), key=lambda row: (row["path_segment"], row["left_handle"].lower(), row["right_handle"].lower()))


def _screen_name_from_user(user: Any) -> str | None:
    if not isinstance(user, dict):
        return None
    core = user.get("core")
    if isinstance(core, dict) and core.get("screen_name"):
        return str(core["screen_name"])
    legacy = user.get("legacy")
    if isinstance(legacy, dict) and legacy.get("screen_name"):
        return str(legacy["screen_name"])
    if user.get("screen_name"):
        return str(user["screen_name"])
    nested = user.get("result")
    return _screen_name_from_user(nested) if isinstance(nested, dict) else None


def _tweet_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if isinstance(value.get("tweet"), dict):
        value = value["tweet"]
    legacy = value.get("legacy")
    if isinstance(legacy, dict) and legacy.get("id_str") and "full_text" in legacy:
        return value
    return None


def iter_top_level_tweets(payload: Any) -> Iterable[dict[str, Any]]:
    """Yield timeline tweet results without treating nested quoted posts as hits."""
    seen: set[str] = set()

    def walk(value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            tweet_results = value.get("tweet_results")
            if isinstance(tweet_results, dict):
                tweet = _tweet_result(tweet_results.get("result"))
                if tweet:
                    tweet_id = str(tweet.get("rest_id") or tweet["legacy"].get("id_str"))
                    if tweet_id not in seen:
                        seen.add(tweet_id)
                        yield tweet
                    return
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)

    yield from walk(payload)


def tweet_author_handle(tweet: dict[str, Any]) -> str | None:
    core = tweet.get("core")
    if not isinstance(core, dict):
        return None
    user_results = core.get("user_results")
    if not isinstance(user_results, dict):
        return None
    return _screen_name_from_user(user_results.get("result"))


def quoted_author_handle(tweet: dict[str, Any]) -> str | None:
    quoted = tweet.get("quoted_status_result")
    if not isinstance(quoted, dict):
        return None
    result = _tweet_result(quoted.get("result"))
    return tweet_author_handle(result) if result else None


def classify_interaction(tweet: dict[str, Any], source: str, destination: str) -> str | None:
    """Classify only exact source→destination interactions from a search hit."""
    if (tweet_author_handle(tweet) or "").lower() != source.lower():
        return None
    legacy = tweet.get("legacy") or {}
    if str(legacy.get("in_reply_to_screen_name") or "").lower() == destination.lower():
        return "reply"
    if legacy.get("is_quote_status") and (quoted_author_handle(tweet) or "").lower() == destination.lower():
        return "quote"
    mentions = (legacy.get("entities") or {}).get("user_mentions") or []
    if any(str(item.get("screen_name") or "").lower() == destination.lower() for item in mentions if isinstance(item, dict)):
        return "mention"
    return None


def interaction_rows(payload: dict[str, Any], source: str, destination: str, query: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tweet in iter_top_level_tweets(payload):
        kind = classify_interaction(tweet, source, destination)
        if not kind:
            continue
        legacy = tweet.get("legacy") or {}
        tweet_id = str(tweet.get("rest_id") or legacy.get("id_str") or "")
        rows.append(
            {
                "source_handle": source,
                "destination_handle": destination,
                "interaction_type": kind,
                "tweet_id": tweet_id,
                "interaction_id": f"{source.lower()}:{destination.lower()}:{tweet_id}",
                "tweet_url": f"https://x.com/{source}/status/{tweet_id}" if tweet_id else "",
                "created_at": legacy.get("created_at") or "",
                "text": legacy.get("full_text") or "",
                "query": query,
                "fact_status": "observed_public_x_interaction",
                "confidence": "high",
                "human_relationship_inference": "not_inferred",
            }
        )
    rows.sort(key=lambda row: (row["created_at"], row["tweet_id"]), reverse=True)
    return rows


def safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-").lower()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()

    paths = json.loads(PATHS.read_text(encoding="utf-8"))
    pairs = primary_adjacent_pairs(paths)
    client = RapidXClient(cache_dir=RAW_DIR / "requests", env_file=args.env_file)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    observations: list[dict[str, Any]] = []
    query_audit: list[dict[str, Any]] = []
    failed = 0

    for pair in pairs:
        for source, destination in (
            (pair["left_handle"], pair["right_handle"]),
            (pair["right_handle"], pair["left_handle"]),
        ):
            query = f"from:{source} @{destination}"
            raw_path = RAW_DIR / f"{safe_slug(source)}--to--{safe_slug(destination)}.json"
            try:
                payload = client.search(
                    query,
                    "Latest",
                    args.count,
                    force=args.force,
                    cache_ttl_seconds=86_400 * 30,
                )
                write_json(raw_path, {"query": query, "response": payload})
                hits = interaction_rows(payload, source, destination, query)
                for hit in hits:
                    hit.update(
                        {
                            "path_segment": pair["path_segment"],
                            "companies": pair["companies"],
                            "raw_cache_file": str(raw_path.relative_to(ROOT)),
                        }
                    )
                observations.extend(hits)
                query_audit.append(
                    {
                        "source_handle": source,
                        "destination_handle": destination,
                        "query": query,
                        "path_segment": pair["path_segment"],
                        "companies": pair["companies"],
                        "matching_observations": len(hits),
                        "sample_status": "observed" if hits else "not_observed_in_bounded_query_sample",
                        "negative_inference_allowed": False,
                        "raw_cache_file": str(raw_path.relative_to(ROOT)),
                    }
                )
            except Exception as exc:
                failed += 1
                query_audit.append(
                    {
                        "source_handle": source,
                        "destination_handle": destination,
                        "query": query,
                        "path_segment": pair["path_segment"],
                        "companies": pair["companies"],
                        "matching_observations": None,
                        "sample_status": "query_failed",
                        "negative_inference_allowed": False,
                        "error": str(exc),
                        "raw_cache_file": str(raw_path.relative_to(ROOT)),
                    }
                )

    deduped = {
        (row["source_handle"].lower(), row["destination_handle"].lower(), row["tweet_id"]): row
        for row in observations if row["tweet_id"]
    }
    observations = sorted(deduped.values(), key=lambda row: (row["created_at"], row["tweet_id"]), reverse=True)
    observed_pairs = {
        (row["source_handle"].lower(), row["destination_handle"].lower())
        for row in observations
    }
    audited_at = datetime.now(timezone.utc).isoformat()
    artifact = {
        "schema_version": "v4",
        "audited_at": audited_at,
        "scope": "Adjacent pairs on retained Solomon-rooted primary paths for the expanded v4 target cohort.",
        "interpretation_rule": "A positive exact-author/exact-counterparty hit is an observed public X interaction. Zero hits mean only not observed in this bounded Rapid X search sample. Human relationship strength and intro willingness are not inferred.",
        "query_count": len(query_audit),
        "query_failures": failed,
        "observed_interaction_count": len(observations),
        "observed_directed_pair_count": len(observed_pairs),
        "query_audit": query_audit,
        "observations": observations,
    }
    write_json(OUT_JSON, artifact)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "interaction_id", "source_handle", "destination_handle", "interaction_type", "tweet_id", "tweet_url",
        "created_at", "text", "path_segment", "companies", "fact_status", "confidence",
        "human_relationship_inference", "query", "raw_cache_file",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in observations:
            writer.writerow({**row, "companies": " | ".join(row["companies"])})

    write_json(
        SUMMARY,
        {
            "schema_version": "v4",
            "audited_at": audited_at,
            "primary_reachable_targets": sum(item.get("graph_reachable") is True for item in paths),
            "unique_adjacent_pairs": len(pairs),
            "directed_queries": len(query_audit),
            "query_failures": failed,
            "observed_interactions": len(observations),
            "observed_directed_pairs": len(observed_pairs),
            "not_observed_queries": sum(row["sample_status"] == "not_observed_in_bounded_query_sample" for row in query_audit),
            "negative_inference_allowed": False,
            "outputs": [str(OUT_JSON.relative_to(ROOT)), str(OUT_CSV.relative_to(ROOT))],
        },
    )
    print(json.dumps({"pairs": len(pairs), "queries": len(query_audit), "failed": failed, "observations": len(observations)}, indent=2))


if __name__ == "__main__":
    main()
