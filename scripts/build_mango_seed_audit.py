#!/usr/bin/env python3
"""Build an offline, direction-preserving Mango seed-to-target X relationship audit.

Only positive follow observations are materialized as edges. Missing IDs in a
partial snapshot remain unknown, and employment/seed membership is never used
as a graph edge.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from mangobd.x_network import edge_kind, find_user_objects, is_routable_path


ROOT = Path(__file__).resolve().parents[1]
SEED_CACHE = ROOT / "data" / "cache" / "rapidx" / "v4" / "mango-seeds"
OLD_TARGET_CACHE = ROOT / "data" / "cache" / "rapidx"
NEW_TARGET_IDENTITIES = ROOT / "outputs" / "pilot_v4" / "x_target_identities_v4.json"
OLD_TARGET_HANDLES = ROOT / "data" / "pilot_v3" / "target_x_handles.json"
OUT = ROOT / "outputs" / "pilot_v4"

ROOT_ENTITY_ID = "mango"

SEED_CSV_FIELDS = [
    "entity_id",
    "name",
    "handle",
    "rest_id",
    "identity_status",
    "identity_evaluable",
    "following_observed_count",
    "following_complete",
    "following_coverage_label",
    "following_cache_file",
    "followers_observed_count",
    "followers_complete",
    "followers_coverage_label",
    "followers_cache_file",
    "observed_mutual_count",
    "mutual_audit_complete",
    "observed_mutual_count_is_lower_bound",
    "mutual_coverage_status",
    "non_observation_interpretation",
    "last_verified_at",
    "evidence_type",
    "confidence",
    "fact_status",
    "evidence_scope",
]

PATH_CSV_FIELDS = [
    "record_type",
    "company_key",
    "company",
    "aliases",
    "cohorts",
    "requested_handles",
    "technical_x_identity_status",
    "technical_identity_confirmed",
    "target_rest_id",
    "target_handle",
    "identity_last_verified_at",
    "target_graph_reachable",
    "graph_reachability_status",
    "x_relationship_level",
    "x_degree",
    "seed_entity_id",
    "seed_handle",
    "seed_rest_id",
    "seed_follows_target",
    "target_follows_seed",
    "mutual_follow",
    "root_seed_edge_kind",
    "seed_target_edge_kind",
    "primary_path_labels",
    "primary_path_rest_ids",
    "primary_edge_directions",
    "evidence_files",
    "relationship_coverage_complete",
    "non_observation_is_not_negative_evidence",
    "human_intro_status",
    "last_verified_at",
    "evidence_type",
    "confidence",
    "fact_status",
    "evidence_scope",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: json.dumps(row.get(field), ensure_ascii=False, sort_keys=True)
                    if isinstance(row.get(field), (list, dict))
                    else row.get(field, "")
                    for field in fields
                }
            )


def normalize_handle(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def normalize_company_key(value: Any) -> str:
    """Create a conservative punctuation-insensitive company dedupe key."""
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    output = []
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _file_mtime(path: Path) -> str | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def _latest_timestamp(values: Iterable[Any]) -> str | None:
    timestamps = sorted(str(value) for value in values if value)
    return timestamps[-1] if timestamps else None


def _evidence_last_verified(files: Iterable[Any]) -> str | None:
    timestamps = []
    for value in files:
        if not value:
            continue
        path = Path(str(value))
        if not path.is_absolute():
            path = ROOT / path
        timestamp = _file_mtime(path)
        if timestamp:
            timestamps.append(timestamp)
    return _latest_timestamp(timestamps)


def empty_snapshot(handle: str, kind: str, path: Path) -> dict[str, Any]:
    return {
        "handle": handle,
        "kind": kind,
        "ids": [],
        "count": 0,
        "pages": 0,
        "complete": False,
        "coverage_label": "missing_cache",
        "cache_file": _relative(path),
        "cache_last_modified_at": None,
        "cache_status": "missing",
    }


def load_direction_snapshot(cache_dir: Path, handle: str, kind: str) -> dict[str, Any]:
    if kind not in {"following", "followers"}:
        raise ValueError(f"Unsupported direction: {kind}")
    path = cache_dir / f"{kind}-{handle}.json"
    if not path.exists():
        return empty_snapshot(handle, kind, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = sorted({str(value) for value in payload.get("ids", []) if str(value)})
    complete = bool(payload.get("complete"))
    return {
        "handle": handle,
        "kind": kind,
        "ids": ids,
        "count": len(ids),
        "pages": int(payload.get("pages") or 0),
        "complete": complete,
        "coverage_label": str(
            payload.get("coverage_label") or ("complete" if complete else "partial_or_unknown")
        ),
        "cache_file": _relative(path),
        "cache_last_modified_at": _file_mtime(path),
        "cache_status": "loaded",
    }


def audit_seed(
    profile: dict[str, Any],
    following: dict[str, Any],
    followers: dict[str, Any],
) -> dict[str, Any]:
    """Summarize one seed without converting partial-cache absence into a negative."""
    identity_evaluable = bool(
        profile.get("rest_id") and profile.get("identity_status") == "exact_handle_match"
    )
    following_ids = {str(value) for value in following.get("ids", [])}
    follower_ids = {str(value) for value in followers.get("ids", [])}
    mutual_ids = sorted(following_ids & follower_ids) if identity_evaluable else []
    mutual_complete = bool(
        identity_evaluable and following.get("complete") and followers.get("complete")
    )
    if not identity_evaluable:
        mutual_status = "unavailable_seed_identity_not_exact"
        interpretation = "Relationship directions are not evaluated until the seed has an exact technical X ID."
    elif mutual_complete:
        mutual_status = "complete_both_direction_snapshots"
        interpretation = (
            "Mutual-follow membership is complete for the two cached ID snapshots; this does not establish "
            "real-world relationship strength or introduction willingness."
        )
    else:
        mutual_status = "observed_lower_bound_partial_or_missing_direction"
        interpretation = (
            "Observed mutuals are a lower bound. An ID absent from a partial or missing snapshot is unknown, "
            "not evidence that the follow edge does not exist."
        )
    return {
        "entity_id": str(profile.get("entity_id") or ""),
        "name": str(profile.get("name") or ""),
        "handle": str(profile.get("handle") or ""),
        "rest_id": str(profile.get("rest_id") or ""),
        "resolved_handle": str(profile.get("resolved_handle") or ""),
        "identity_status": str(profile.get("identity_status") or "unknown"),
        "identity_evaluable": identity_evaluable,
        "following_observed_count": len(following_ids),
        "following_complete": bool(following.get("complete")),
        "following_pages": int(following.get("pages") or 0),
        "following_coverage_label": str(following.get("coverage_label") or "unknown"),
        "following_cache_file": str(following.get("cache_file") or ""),
        "following_cache_last_modified_at": following.get("cache_last_modified_at"),
        "followers_observed_count": len(follower_ids),
        "followers_complete": bool(followers.get("complete")),
        "followers_pages": int(followers.get("pages") or 0),
        "followers_coverage_label": str(followers.get("coverage_label") or "unknown"),
        "followers_cache_file": str(followers.get("cache_file") or ""),
        "followers_cache_last_modified_at": followers.get("cache_last_modified_at"),
        "observed_mutual_count": len(mutual_ids),
        "observed_mutual_rest_ids": mutual_ids,
        "mutual_audit_complete": mutual_complete,
        "observed_mutual_count_is_lower_bound": bool(identity_evaluable and not mutual_complete),
        "mutual_coverage_status": mutual_status,
        "non_observation_interpretation": interpretation,
        "last_verified_at": _latest_timestamp(
            [following.get("cache_last_modified_at"), followers.get("cache_last_modified_at")]
        ),
        "evidence_type": "first_party_social",
        "confidence": "high" if identity_evaluable else "low",
        "fact_status": "confirmed_positive_observations" if identity_evaluable else "unverified",
        "evidence_scope": "Rapid X cached follower/following IDs only; no employment edge inferred.",
    }


def _profile_identity(user: dict[str, Any]) -> tuple[str, str]:
    legacy = user.get("legacy") or {}
    core = user.get("core") or {}
    rest_id = user.get("rest_id") or user.get("id_str") or user.get("id") or ""
    handle = (
        legacy.get("screen_name")
        or core.get("screen_name")
        or user.get("screen_name")
        or user.get("username")
        or ""
    )
    return str(rest_id), str(handle)


def normalize_new_target(row: dict[str, Any]) -> dict[str, Any]:
    requested = str(row.get("requested_handle") or "")
    resolved = str(row.get("resolved_handle") or "")
    rest_id = str(row.get("resolved_rest_id") or "")
    status = str(row.get("technical_x_identity_status") or "unresolved")
    official = str(row.get("official_handle") or "")
    if status == "confirmed_exact_requested_handle":
        identity_basis_matches = bool(requested and normalize_handle(requested) == normalize_handle(resolved))
    elif status in {
        "confirmed_official_link_exact_cached_rapid_candidate",
        "confirmed_corrected_official_handle_exact_rapid_profile",
    }:
        identity_basis_matches = bool(official and normalize_handle(official) == normalize_handle(resolved))
    else:
        identity_basis_matches = False
    exact = bool(row.get("technical_identity_confirmed") and rest_id and resolved and identity_basis_matches)
    if row.get("technical_identity_confirmed") and not exact:
        status = "unresolved_inconsistent_exact_identity_fields"
    return {
        "company": str(row.get("company") or ""),
        "aliases": _unique_strings(row.get("aliases") or []),
        "cohorts": [str(row.get("cohort") or "new_v4")],
        "requested_handles": _unique_strings([requested, official]),
        "technical_identity_confirmed": exact,
        "technical_x_identity_status": status,
        "target_rest_id": rest_id if exact else "",
        "target_handle": resolved if exact else "",
        "identity_evidence_files": _unique_strings([
            row.get("cache_file"),
            *(
                item for item in row.get("identity_resolution_evidence") or []
                if isinstance(item, str) and not item.startswith(("http://", "https://"))
            ),
        ]),
        "identity_observations": [
            {
                "cohort": str(row.get("cohort") or "new_v4"),
                "requested_handle": requested,
                "status": status,
                "exact": exact,
                "cache_file": str(row.get("cache_file") or ""),
                "official_handle": official,
                "identity_resolution_source": row.get("identity_resolution_source"),
            }
        ],
    }


def resolve_old_target(
    row: dict[str, Any],
    cache_payload: Any | None,
    cache_file: str,
) -> dict[str, Any]:
    """Resolve a v3 target only when one cached user exactly matches the requested handle."""
    requested = str(row.get("handle") or "")
    matches: dict[str, str] = {}
    for user in find_user_objects(cache_payload) if cache_payload is not None else []:
        rest_id, handle = _profile_identity(user)
        if rest_id and normalize_handle(handle) == normalize_handle(requested):
            matches[rest_id] = handle
    if len(matches) == 1:
        rest_id, handle = next(iter(matches.items()))
        exact = True
        status = "confirmed_exact_requested_handle"
    elif len(matches) > 1:
        rest_id, handle = "", ""
        exact = False
        status = "unresolved_multiple_exact_handle_ids"
    elif cache_payload is None:
        rest_id, handle = "", ""
        exact = False
        status = "unresolved_target_cache_missing"
    else:
        rest_id, handle = "", ""
        exact = False
        status = "unresolved_no_exact_handle_user_in_cache"
    return {
        "company": str(row.get("company") or ""),
        "aliases": [],
        "cohorts": ["existing_v3"],
        "requested_handles": _unique_strings([requested]),
        "technical_identity_confirmed": exact,
        "technical_x_identity_status": status,
        "target_rest_id": rest_id,
        "target_handle": handle,
        "identity_evidence_files": _unique_strings([cache_file]),
        "identity_observations": [
            {
                "cohort": "existing_v3",
                "requested_handle": requested,
                "prior_verification_status": str(row.get("verification_status") or ""),
                "status": status,
                "exact": exact,
                "cache_file": cache_file,
            }
        ],
    }


def _target_match_keys(target: dict[str, Any]) -> set[str]:
    keys = {f"company:{normalize_company_key(target.get('company'))}"}
    keys.update(
        f"company:{normalize_company_key(alias)}"
        for alias in target.get("aliases", [])
        if normalize_company_key(alias)
    )
    keys.update(
        f"handle:{normalize_handle(handle)}"
        for handle in target.get("requested_handles", [])
        if normalize_handle(handle)
    )
    if target.get("target_rest_id"):
        keys.add(f"id:{target['target_rest_id']}")
    return {key for key in keys if not key.endswith(":")}


def _merge_targets(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_exact = bool(left.get("technical_identity_confirmed"))
    right_exact = bool(right.get("technical_identity_confirmed"))
    left_id = str(left.get("target_rest_id") or "")
    right_id = str(right.get("target_rest_id") or "")
    conflict = left_exact and right_exact and left_id != right_id
    if conflict:
        exact = False
        rest_id = ""
        handle = ""
        status = "unresolved_conflicting_exact_ids_across_cohorts"
    elif right_exact:
        exact = True
        rest_id = right_id
        handle = str(right.get("target_handle") or "")
        status = "confirmed_exact_requested_handle"
    elif left_exact:
        exact = True
        rest_id = left_id
        handle = str(left.get("target_handle") or "")
        status = "confirmed_exact_requested_handle"
    else:
        exact = False
        rest_id = ""
        handle = ""
        statuses = _unique_strings(
            [left.get("technical_x_identity_status"), right.get("technical_x_identity_status")]
        )
        status = statuses[0] if len(statuses) == 1 else "unresolved_multiple_identity_observations"
    company = str(left.get("company") or right.get("company") or "")
    aliases = _unique_strings(
        list(left.get("aliases", []))
        + list(right.get("aliases", []))
        + ([right.get("company")] if right.get("company") != company else [])
    )
    return {
        "company": company,
        "aliases": aliases,
        "cohorts": _unique_strings(list(left.get("cohorts", [])) + list(right.get("cohorts", []))),
        "requested_handles": _unique_strings(
            list(left.get("requested_handles", [])) + list(right.get("requested_handles", []))
        ),
        "technical_identity_confirmed": exact,
        "technical_x_identity_status": status,
        "target_rest_id": rest_id,
        "target_handle": handle,
        "identity_evidence_files": _unique_strings(
            list(left.get("identity_evidence_files", []))
            + list(right.get("identity_evidence_files", []))
        ),
        "identity_observations": list(left.get("identity_observations", []))
        + list(right.get("identity_observations", [])),
    }


def dedupe_targets(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe company cohorts by exact ID, requested handle, company, or declared alias."""
    merged: list[dict[str, Any]] = []
    for record in records:
        matches = [index for index, item in enumerate(merged) if _target_match_keys(item) & _target_match_keys(record)]
        if not matches:
            merged.append(record)
            continue
        base_index = matches[0]
        combined = _merge_targets(merged[base_index], record)
        for index in reversed(matches[1:]):
            combined = _merge_targets(combined, merged.pop(index))
        merged[base_index] = combined
    for target in merged:
        target["company_key"] = normalize_company_key(target["company"])
        target["identity_last_verified_at"] = _evidence_last_verified(
            target.get("identity_evidence_files", [])
        )
        target["identity_evidence_type"] = "first_party_social"
        target["identity_confidence"] = (
            "high" if target.get("technical_identity_confirmed") else "low"
        )
        target["identity_fact_status"] = (
            "confirmed" if target.get("technical_identity_confirmed") else "unverified"
        )
    return sorted(merged, key=lambda row: (row["company"].casefold(), row["company_key"]))


def add_snapshot_edges(
    adjacency: dict[str, set[str]],
    ledger: dict[tuple[str, str], set[str]],
    observer_rest_id: str,
    following: dict[str, Any],
    followers: dict[str, Any],
) -> None:
    """Orient both snapshot kinds as actual `source follows target` edges."""
    for target_id in following.get("ids", []):
        target_id = str(target_id)
        if target_id and target_id != observer_rest_id:
            adjacency.setdefault(observer_rest_id, set()).add(target_id)
            ledger.setdefault((observer_rest_id, target_id), set()).add(str(following.get("cache_file") or ""))
    for source_id in followers.get("ids", []):
        source_id = str(source_id)
        if source_id and source_id != observer_rest_id:
            adjacency.setdefault(source_id, set()).add(observer_rest_id)
            ledger.setdefault((source_id, observer_rest_id), set()).add(str(followers.get("cache_file") or ""))


def observed_directions(
    adjacency: dict[str, set[str]], source: str, target: str
) -> list[tuple[str, str]]:
    directions = []
    if target in adjacency.get(source, set()):
        directions.append((source, target))
    if source in adjacency.get(target, set()):
        directions.append((target, source))
    return directions


def _direction_labels(
    directions: list[tuple[str, str]], labels: dict[str, str]
) -> list[str]:
    return [f"{labels.get(source, source)} -> {labels.get(target, target)}" for source, target in directions]


def _edge_files(
    ledger: dict[tuple[str, str], set[str]], directions: Iterable[tuple[str, str]]
) -> list[str]:
    return sorted(
        {
            path
            for direction in directions
            for path in ledger.get(direction, set())
            if path
        }
    )


def build_target_relationships(
    targets: list[dict[str, Any]],
    seed_runtime: list[dict[str, Any]],
    root_entity_id: str = ROOT_ENTITY_ID,
) -> list[dict[str, Any]]:
    """Evaluate exact targets against observed seed edges and routable 1/2-hop Mango paths."""
    adjacency: dict[str, set[str]] = {}
    ledger: dict[tuple[str, str], set[str]] = {}
    eligible_seeds = [row for row in seed_runtime if row["audit"]["identity_evaluable"]]
    for runtime in eligible_seeds:
        add_snapshot_edges(
            adjacency,
            ledger,
            runtime["audit"]["rest_id"],
            runtime["following"],
            runtime["followers"],
        )
    root_runtime = next(
        (row for row in eligible_seeds if row["audit"]["entity_id"] == root_entity_id),
        None,
    )
    root_id = root_runtime["audit"]["rest_id"] if root_runtime else ""
    root_handle = root_runtime["audit"]["handle"] if root_runtime else ""
    weak_coverage_complete = bool(
        root_runtime
        and eligible_seeds
        and len(eligible_seeds) == len(seed_runtime)
        and all(
            row["audit"]["following_complete"] and row["audit"]["followers_complete"]
            for row in eligible_seeds
        )
    )
    forward_coverage_complete = bool(
        root_runtime
        and eligible_seeds
        and len(eligible_seeds) == len(seed_runtime)
        and all(row["audit"]["following_complete"] for row in eligible_seeds)
    )
    relationship_last_verified_at = _latest_timestamp(
        timestamp
        for row in eligible_seeds
        for timestamp in (
            row["following"].get("cache_last_modified_at"),
            row["followers"].get("cache_last_modified_at"),
        )
    )
    labels = {
        row["audit"]["rest_id"]: f"@{row['audit']['handle']}"
        for row in eligible_seeds
    }
    evaluated = []
    for target in targets:
        base = {
            **target,
            "relationship_coverage_complete": weak_coverage_complete,
            "forward_following_coverage_complete": forward_coverage_complete,
            "relationship_coverage_note": (
                "All eligible seed following and follower snapshots are complete."
                if weak_coverage_complete
                else "At least one seed follower/following snapshot or seed identity is partial, missing, or unresolved; absent weak edges remain unknown."
            ),
        }
        if not target.get("technical_identity_confirmed") or not target.get("target_rest_id"):
            evaluated.append(
                {
                    **base,
                    "graph_reachable": None,
                    "graph_reachability_status": "not_evaluable_technical_x_identity_unresolved",
                    "x_relationship_level": None,
                    "x_degree": None,
                    "direct_seed_edges": [],
                    "mango_direct_paths": [],
                    "mango_secondary_paths": [],
                    "rejected_shared_interest_v_count": 0,
                    "non_observation_is_not_negative_evidence": True,
                    "human_intro_status": "not_evaluated",
                    "last_verified_at": target.get("identity_last_verified_at"),
                    "evidence_type": "first_party_social",
                    "confidence": "low",
                    "fact_status": "unverified",
                    "evidence_scope": "Target omitted from graph matching until an exact technical X ID is confirmed.",
                }
            )
            continue

        target_id = str(target["target_rest_id"])
        target_label = f"@{target.get('target_handle') or target['company']}"
        target_labels = {**labels, target_id: target_label}
        direct_edges = []
        for runtime in eligible_seeds:
            audit = runtime["audit"]
            seed_id = audit["rest_id"]
            directions = observed_directions(adjacency, seed_id, target_id)
            if not directions:
                continue
            kind = edge_kind(adjacency, seed_id, target_id)
            direct_edges.append(
                {
                    "seed_entity_id": audit["entity_id"],
                    "seed_name": audit["name"],
                    "seed_handle": audit["handle"],
                    "seed_rest_id": seed_id,
                    "target_rest_id": target_id,
                    "seed_follows_target": (seed_id, target_id) in directions,
                    "target_follows_seed": (target_id, seed_id) in directions,
                    "mutual_follow": kind == "mutual_follow",
                    "seed_target_edge_kind": kind,
                    "observed_edge_directions": _direction_labels(directions, target_labels),
                    "observed_edge_rest_id_directions": [f"{source}->{dest}" for source, dest in directions],
                    "evidence_files": _edge_files(ledger, directions),
                    "last_verified_at": _evidence_last_verified(_edge_files(ledger, directions)),
                    "evidence_type": "first_party_social",
                    "confidence": "high",
                    "fact_status": "confirmed",
                    "graph_reachable_from_seed": True,
                    "human_intro_status": "human_intro_unvalidated",
                    "evidence_scope": "Positive cached X follow observations only; seed membership is not an edge.",
                }
            )

        direct_paths = []
        secondary_paths = []
        rejected_v = 0
        for direct in direct_edges:
            seed_id = direct["seed_rest_id"]
            seed_handle = direct["seed_handle"]
            if seed_id == root_id:
                directions = observed_directions(adjacency, root_id, target_id)
                direct_paths.append(
                    {
                        "x_relationship_level": "direct",
                        "x_degree": 1,
                        "path_labels": [f"@{root_handle}", target_label],
                        "path_rest_ids": [root_id, target_id],
                        "edge_kinds": [edge_kind(adjacency, root_id, target_id)],
                        "edge_directions": _direction_labels(directions, target_labels),
                        "edge_rest_id_directions": [f"{source}->{dest}" for source, dest in directions],
                        "evidence_files": _edge_files(ledger, directions),
                        "last_verified_at": _evidence_last_verified(_edge_files(ledger, directions)),
                        "evidence_type": "first_party_social",
                        "confidence": "high",
                        "fact_status": "confirmed_in_cached_graph",
                        "graph_reachable": True,
                        "human_intro_status": "human_intro_unvalidated",
                    }
                )
                continue
            if not root_id or edge_kind(adjacency, root_id, seed_id) == "no_observed_edge":
                continue
            candidate_path = [root_id, seed_id, target_id]
            if not is_routable_path(adjacency, candidate_path):
                rejected_v += 1
                continue
            first_directions = observed_directions(adjacency, root_id, seed_id)
            second_directions = observed_directions(adjacency, seed_id, target_id)
            secondary_paths.append(
                {
                    "x_relationship_level": "secondary",
                    "x_degree": 2,
                    "connector_entity_id": direct["seed_entity_id"],
                    "connector_name": direct["seed_name"],
                    "connector_handle": seed_handle,
                    "connector_rest_id": seed_id,
                    "path_labels": [f"@{root_handle}", f"@{seed_handle}", target_label],
                    "path_rest_ids": candidate_path,
                    "edge_kinds": [
                        edge_kind(adjacency, root_id, seed_id),
                        edge_kind(adjacency, seed_id, target_id),
                    ],
                    "root_seed_observed_directions": _direction_labels(first_directions, target_labels),
                    "seed_target_observed_directions": _direction_labels(second_directions, target_labels),
                    "edge_directions": _direction_labels(first_directions + second_directions, target_labels),
                    "edge_rest_id_directions": [
                        f"{source}->{dest}" for source, dest in first_directions + second_directions
                    ],
                    "evidence_files": _edge_files(ledger, first_directions + second_directions),
                    "last_verified_at": _evidence_last_verified(
                        _edge_files(ledger, first_directions + second_directions)
                    ),
                    "evidence_type": "first_party_social",
                    "confidence": "high",
                    "fact_status": "confirmed_in_cached_graph",
                    "graph_reachable": True,
                    "human_intro_status": "human_intro_unvalidated",
                    "evidence_scope": "Both hops are positive cached X follow observations; employment/seed membership is not used as a hop.",
                }
            )
        direct_paths.sort(key=lambda row: (row["path_rest_ids"], row["edge_kinds"]))
        secondary_paths.sort(key=lambda row: (row["connector_handle"].casefold(), row["connector_rest_id"]))
        graph_reachable = bool(direct_paths or secondary_paths)
        if direct_paths:
            level, degree = "direct", 1
        elif secondary_paths:
            level, degree = "secondary", 2
        else:
            level, degree = None, None
        evaluated.append(
            {
                **base,
                "graph_reachable": graph_reachable,
                "graph_reachability_status": (
                    "observed_routable_x_follow_path"
                    if graph_reachable
                    else "no_observed_routable_path_in_available_cache"
                ),
                "x_relationship_level": level,
                "x_degree": degree,
                "direct_seed_edges": direct_edges,
                "mango_direct_paths": direct_paths,
                "mango_secondary_paths": secondary_paths,
                "rejected_shared_interest_v_count": rejected_v,
                "non_observation_is_not_negative_evidence": not graph_reachable,
                "human_intro_status": (
                    "human_intro_unvalidated" if graph_reachable else "not_applicable_no_observed_graph_path"
                ),
                "last_verified_at": relationship_last_verified_at,
                "evidence_type": "first_party_social",
                "confidence": "high" if graph_reachable else "low",
                "fact_status": "confirmed_in_cached_graph" if graph_reachable else "unverified_outside_available_cache",
                "evidence_scope": (
                    "Graph reachability is based only on positive cached X follow edges. It does not establish interaction, relationship strength, or willingness to introduce."
                ),
            }
        )
    return sorted(evaluated, key=lambda row: (row["company"].casefold(), row["company_key"]))


def flatten_target_rows(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def common(target: dict[str, Any]) -> dict[str, Any]:
        return {
            "company_key": target["company_key"],
            "company": target["company"],
            "aliases": target.get("aliases", []),
            "cohorts": target.get("cohorts", []),
            "requested_handles": target.get("requested_handles", []),
            "technical_x_identity_status": target["technical_x_identity_status"],
            "technical_identity_confirmed": target["technical_identity_confirmed"],
            "target_rest_id": target.get("target_rest_id", ""),
            "target_handle": target.get("target_handle", ""),
            "identity_last_verified_at": target.get("identity_last_verified_at"),
            "target_graph_reachable": target.get("graph_reachable"),
            "graph_reachability_status": target["graph_reachability_status"],
            "x_relationship_level": target.get("x_relationship_level"),
            "x_degree": target.get("x_degree"),
            "relationship_coverage_complete": target["relationship_coverage_complete"],
            "non_observation_is_not_negative_evidence": target[
                "non_observation_is_not_negative_evidence"
            ],
            "human_intro_status": target["human_intro_status"],
            "last_verified_at": target.get("last_verified_at"),
            "evidence_type": target.get("evidence_type"),
            "confidence": target.get("confidence"),
            "fact_status": target.get("fact_status"),
            "evidence_scope": target["evidence_scope"],
        }

    for target in targets:
        base = common(target)
        if not target["technical_identity_confirmed"]:
            rows.append({**base, "record_type": "unresolved_target"})
            continue
        for edge in target["direct_seed_edges"]:
            if edge["seed_entity_id"] == ROOT_ENTITY_ID:
                continue
            rows.append(
                {
                    **base,
                    "record_type": "seed_target_edge_observation",
                    "seed_entity_id": edge["seed_entity_id"],
                    "seed_handle": edge["seed_handle"],
                    "seed_rest_id": edge["seed_rest_id"],
                    "seed_follows_target": edge["seed_follows_target"],
                    "target_follows_seed": edge["target_follows_seed"],
                    "mutual_follow": edge["mutual_follow"],
                    "seed_target_edge_kind": edge["seed_target_edge_kind"],
                    "primary_path_labels": [f"@{edge['seed_handle']}", f"@{target['target_handle']}"],
                    "primary_path_rest_ids": [edge["seed_rest_id"], target["target_rest_id"]],
                    "primary_edge_directions": edge["observed_edge_directions"],
                    "evidence_files": edge["evidence_files"],
                    "last_verified_at": edge["last_verified_at"],
                    "evidence_type": edge["evidence_type"],
                    "confidence": edge["confidence"],
                    "fact_status": edge["fact_status"],
                }
            )
        for path in target["mango_direct_paths"]:
            root_edge = next(
                edge for edge in target["direct_seed_edges"] if edge["seed_entity_id"] == ROOT_ENTITY_ID
            )
            rows.append(
                {
                    **base,
                    "record_type": "mango_direct_path",
                    "seed_entity_id": ROOT_ENTITY_ID,
                    "seed_handle": root_edge["seed_handle"],
                    "seed_rest_id": root_edge["seed_rest_id"],
                    "seed_follows_target": root_edge["seed_follows_target"],
                    "target_follows_seed": root_edge["target_follows_seed"],
                    "mutual_follow": root_edge["mutual_follow"],
                    "seed_target_edge_kind": root_edge["seed_target_edge_kind"],
                    "primary_path_labels": path["path_labels"],
                    "primary_path_rest_ids": path["path_rest_ids"],
                    "primary_edge_directions": path["edge_directions"],
                    "evidence_files": path["evidence_files"],
                    "last_verified_at": path["last_verified_at"],
                    "evidence_type": path["evidence_type"],
                    "confidence": path["confidence"],
                    "fact_status": path["fact_status"],
                }
            )
        for path in target["mango_secondary_paths"]:
            edge = next(
                item
                for item in target["direct_seed_edges"]
                if item["seed_rest_id"] == path["connector_rest_id"]
            )
            rows.append(
                {
                    **base,
                    "record_type": "mango_secondary_path",
                    "seed_entity_id": path["connector_entity_id"],
                    "seed_handle": path["connector_handle"],
                    "seed_rest_id": path["connector_rest_id"],
                    "seed_follows_target": edge["seed_follows_target"],
                    "target_follows_seed": edge["target_follows_seed"],
                    "mutual_follow": edge["mutual_follow"],
                    "root_seed_edge_kind": path["edge_kinds"][0],
                    "seed_target_edge_kind": path["edge_kinds"][1],
                    "primary_path_labels": path["path_labels"],
                    "primary_path_rest_ids": path["path_rest_ids"],
                    "primary_edge_directions": path["edge_directions"],
                    "evidence_files": path["evidence_files"],
                    "last_verified_at": path["last_verified_at"],
                    "evidence_type": path["evidence_type"],
                    "confidence": path["confidence"],
                    "fact_status": path["fact_status"],
                }
            )
        if not target["direct_seed_edges"]:
            rows.append({**base, "record_type": "no_observed_x_path"})
    return rows


def load_old_targets(rows: list[dict[str, Any]], cache_dir: Path) -> list[dict[str, Any]]:
    cache_index = {
        path.stem.removeprefix("target-").casefold(): path
        for path in cache_dir.glob("target-*.json")
    }
    targets = []
    for row in rows:
        requested = normalize_handle(row.get("handle"))
        path = cache_index.get(requested)
        payload = json.loads(path.read_text(encoding="utf-8")) if path else None
        targets.append(resolve_old_target(row, payload, _relative(path) if path else ""))
    return targets


def main() -> None:
    generated_at = utc_now()
    profiles = json.loads((SEED_CACHE / "profiles.json").read_text(encoding="utf-8"))
    runtime = []
    for profile in profiles:
        following = load_direction_snapshot(SEED_CACHE, str(profile.get("handle") or ""), "following")
        followers = load_direction_snapshot(SEED_CACHE, str(profile.get("handle") or ""), "followers")
        runtime.append(
            {
                "audit": audit_seed(profile, following, followers),
                "following": following,
                "followers": followers,
            }
        )

    new_rows = json.loads(NEW_TARGET_IDENTITIES.read_text(encoding="utf-8"))
    old_rows = json.loads(OLD_TARGET_HANDLES.read_text(encoding="utf-8"))
    target_inventory = dedupe_targets(
        [normalize_new_target(row) for row in new_rows]
        + load_old_targets(old_rows, OLD_TARGET_CACHE)
    )
    target_audit = build_target_relationships(target_inventory, runtime)

    seed_rows = [row["audit"] for row in runtime]
    root = next((row for row in seed_rows if row["entity_id"] == ROOT_ENTITY_ID), None)
    network_document = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "source_scope": "Offline Rapid X seed profile/following/follower caches only.",
        "root": {
            "entity_id": root["entity_id"],
            "handle": root["handle"],
            "rest_id": root["rest_id"],
        }
        if root
        else None,
        "summary": {
            "seed_count": len(seed_rows),
            "identity_evaluable_count": sum(row["identity_evaluable"] for row in seed_rows),
            "following_complete_count": sum(row["following_complete"] for row in seed_rows),
            "followers_complete_count": sum(row["followers_complete"] for row in seed_rows),
            "mutual_audit_complete_count": sum(row["mutual_audit_complete"] for row in seed_rows),
            "mutual_audit_lower_bound_count": sum(
                row["observed_mutual_count_is_lower_bound"] for row in seed_rows
            ),
            "observed_mutual_memberships_total": sum(row["observed_mutual_count"] for row in seed_rows),
            "employment_edges_inferred": 0,
        },
        "seeds": seed_rows,
        "limitations": [
            "A partial follower/following snapshot supports positive observed edges only; absence remains unknown.",
            "Observed X follows do not prove interaction history, relationship strength, or introduction willingness.",
            "Mango employment or seed-list membership is not represented as an X edge.",
        ],
    }
    target_document = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "source_scope": "Exact technical target identities matched against offline Rapid X seed relationship caches.",
        "root": network_document["root"],
        "summary": {
            "company_target_count_after_dedupe": len(target_audit),
            "exact_technical_identity_count": sum(
                row["technical_identity_confirmed"] for row in target_audit
            ),
            "unresolved_identity_count": sum(
                not row["technical_identity_confirmed"] for row in target_audit
            ),
            "direct_seed_target_edge_count": sum(
                len(row["direct_seed_edges"]) for row in target_audit
            ),
            "companies_with_any_seed_target_edge": sum(
                bool(row["direct_seed_edges"]) for row in target_audit
            ),
            "mango_direct_path_count": sum(len(row["mango_direct_paths"]) for row in target_audit),
            "mango_secondary_path_count": sum(
                len(row["mango_secondary_paths"]) for row in target_audit
            ),
            "companies_graph_reachable_from_mango": sum(
                row["graph_reachable"] is True for row in target_audit
            ),
            "rejected_shared_interest_v_count": sum(
                row["rejected_shared_interest_v_count"] for row in target_audit
            ),
            "human_introductions_validated": 0,
        },
        "targets": target_audit,
        "limitations": [
            "Unresolved target identities are not assigned false graph negatives.",
            "No observed path means only no path in available cache; it is not proof that no relationship exists.",
            "Graph reachability is separate from a human connector's willingness or ability to introduce.",
            "Shared-interest V paths are rejected as non-routable.",
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    write_json(OUT / "mango_seed_network_audit.json", network_document)
    write_csv(OUT / "mango_seed_network_audit.csv", seed_rows, SEED_CSV_FIELDS)
    write_json(OUT / "mango_seed_target_paths.json", target_document)
    flat_rows = flatten_target_rows(target_audit)
    write_csv(OUT / "mango_seed_target_paths.csv", flat_rows, PATH_CSV_FIELDS)
    print(
        json.dumps(
            {
                **network_document["summary"],
                **target_document["summary"],
                "target_path_csv_rows": len(flat_rows),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
