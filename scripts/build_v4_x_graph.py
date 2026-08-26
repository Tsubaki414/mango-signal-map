#!/usr/bin/env python3
"""Build the offline v4 Solomon X identity and directed path graph from cached Rapid X data."""

from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from mangobd.x_network import (
    edge_kind,
    find_user_objects,
    is_routable_path,
    load_best_id_cache,
    operational_x_score,
    path_strength,
    relationship_adjacency,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "pilot_v4"
OUT = ROOT / "outputs" / "pilot_v4"
CACHE_ROOT = ROOT / "data" / "cache" / "rapidx"
CACHE_V4 = CACHE_ROOT / "v4"
FRONTIER = DATA / "x_target_frontier.json"
FIRST_DEGREE = OUT / "solomon_first_degree_frontier.json"
CONNECTOR_LOG = OUT / "solomon_connector_expansion_log.json"
ADJUDICATIONS = DATA / "x_identity_adjudications.json"
CORRECTION_RESOLUTIONS = OUT / "x_identity_correction_resolutions.json"

SOLOMON_ID = "1770646033481310208"
SOLOMON_HANDLE = "Solomon_Nahhh"
MAX_ALTERNATIVE_PATHS = 20

IDENTITY_CSV_FIELDS = [
    "priority_rank", "company", "aliases", "cohort", "frontier_handle_status",
    "requested_handle", "result_type", "cache_file", "cache_status",
    "technical_x_identity_status", "technical_identity_confirmed", "manual_match_required",
    "resolved_rest_id", "resolved_handle", "profile_name", "search_candidate_count",
    "search_candidate_handles", "identity_rule", "evidence_scope",
    "identity_adjudication_status", "official_domain", "official_social_url",
    "official_handle", "identity_resolution_source", "identity_resolution_evidence",
]

PATH_CSV_FIELDS = [
    "priority_rank", "company", "technical_x_identity_status", "target_handle",
    "target_rest_id", "graph_reachable", "graph_reachability_status",
    "x_relationship_level", "x_degree", "hop_count", "primary_path_labels",
    "primary_path_rest_ids", "primary_edge_directions", "primary_path_strength",
    "primary_operational_x_score", "candidate_path_count", "routable_path_count",
    "rejected_shared_interest_v_count", "alternative_path_count_total",
    "alternative_paths_retained_count", "alternative_paths_truncated_count",
    "human_intro_status", "evidence_scope",
]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                field: json.dumps(row.get(field), ensure_ascii=False)
                if isinstance(row.get(field), (list, dict)) else row.get(field, "")
                for field in fields
            })


def normalize_handle(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def normalize_user(user: dict[str, Any]) -> dict[str, Any]:
    legacy = user.get("legacy") or {}
    core = user.get("core") or {}
    rest_id = user.get("rest_id") or user.get("id_str") or user.get("id")
    screen_name = (
        legacy.get("screen_name") or core.get("screen_name") or user.get("screen_name")
        or user.get("username") or ""
    )
    return {
        "rest_id": str(rest_id) if rest_id is not None else "",
        "handle": str(screen_name),
        "name": str(legacy.get("name") or core.get("name") or user.get("name") or ""),
        "description": str(legacy.get("description") or user.get("description") or ""),
        "followers_count": legacy.get("followers_count", user.get("followers_count")),
        "following_count": legacy.get("friends_count", user.get("friends_count", user.get("following_count"))),
        "verified": bool(user.get("is_blue_verified") or legacy.get("verified") or user.get("verified")),
    }


def unique_normalized_users(payload: Any) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for user in find_user_objects(payload):
        normalized = normalize_user(user)
        if normalized["rest_id"]:
            by_id[normalized["rest_id"]] = normalized
    return sorted(by_id.values(), key=lambda row: (normalize_handle(row["handle"]), row["rest_id"]))


def resolve_target_identity(cache_record: dict[str, Any], cache_file: str = "") -> dict[str, Any]:
    """Confirm profile lookups only on an exact case-insensitive requested-handle match."""
    request = cache_record.get("request") or {}
    result_type = str(cache_record.get("result_type") or "unknown")
    requested_handle = str(request.get("handle") or "")
    requested_key = normalize_handle(requested_handle)
    candidates = unique_normalized_users(cache_record.get("response"))
    exact = [row for row in candidates if requested_key and normalize_handle(row["handle"]) == requested_key]
    common = {
        "priority_rank": request.get("priority_rank"),
        "company": request.get("company", ""),
        "aliases": request.get("aliases", []),
        "cohort": request.get("cohort", ""),
        "frontier_handle_status": request.get("handle_status", ""),
        "requested_handle": requested_handle,
        "result_type": result_type,
        "cache_file": cache_file,
        "cache_status": "loaded",
        "identity_rule": "profile_lookup requires exact requested-handle match; search results always require manual match",
        "evidence_scope": "Cached Rapid X response only; technical identity does not establish graph reachability or introduction willingness.",
        "search_candidates": candidates,
        "search_candidate_count": len(candidates),
        "search_candidate_handles": [f"@{row['handle']}" for row in candidates if row["handle"]],
    }
    if result_type == "profile_lookup" and len(exact) == 1:
        profile = exact[0]
        return {
            **common,
            "technical_x_identity_status": "confirmed_exact_requested_handle",
            "technical_identity_confirmed": True,
            "manual_match_required": False,
            "resolved_rest_id": profile["rest_id"],
            "resolved_handle": profile["handle"],
            "profile_name": profile["name"],
            "profile": profile,
        }
    if result_type == "profile_lookup" and len(exact) > 1:
        status = "ambiguous_multiple_exact_handle_profiles"
    elif result_type == "profile_lookup" and candidates:
        status = "unresolved_profile_lookup_handle_mismatch"
    elif result_type == "profile_lookup":
        status = "unresolved_profile_lookup_no_user_object"
    elif result_type.startswith("search"):
        status = "search_candidates_manual_match_required"
    else:
        status = "unresolved_unknown_result_type"
    return {
        **common,
        "technical_x_identity_status": status,
        "technical_identity_confirmed": False,
        "manual_match_required": True,
        "resolved_rest_id": "",
        "resolved_handle": "",
        "profile_name": "",
        "profile": None,
    }


def _base_adjudication_metadata(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        **identity,
        "identity_adjudication_status": "not_required_initial_exact_profile_lookup",
        "official_domain": "",
        "official_social_url": "",
        "official_handle": "",
        "identity_resolution_source": "rapid_x_initial_exact_profile_lookup",
        "identity_resolution_evidence": [identity.get("cache_file", "")],
    }


def _adjudication_metadata(
    identity: dict[str, Any], adjudication: dict[str, Any], *, source: str,
    evidence: list[Any],
) -> dict[str, Any]:
    return {
        **identity,
        "identity_adjudication_status": adjudication.get("status", ""),
        "official_domain": adjudication.get("official_domain", ""),
        "official_social_url": adjudication.get("official_social_url", ""),
        "official_handle": adjudication.get("proposed_handle", ""),
        "identity_resolution_source": source,
        "identity_resolution_evidence": evidence,
    }


def _unconfirmed_after_adjudication(
    identity: dict[str, Any], adjudication: dict[str, Any], status: str,
    *, source: str, evidence: list[Any],
) -> dict[str, Any]:
    unresolved = {
        **identity,
        "technical_x_identity_status": status,
        "technical_identity_confirmed": False,
        "manual_match_required": True,
        "resolved_rest_id": "",
        "resolved_handle": "",
        "profile_name": "",
        "profile": None,
    }
    return _adjudication_metadata(unresolved, adjudication, source=source, evidence=evidence)


def _confirmed_after_adjudication(
    identity: dict[str, Any], adjudication: dict[str, Any], profile: dict[str, Any],
    status: str, *, source: str, evidence: list[Any],
) -> dict[str, Any]:
    confirmed = {
        **identity,
        "technical_x_identity_status": status,
        "technical_identity_confirmed": True,
        "manual_match_required": False,
        "resolved_rest_id": str(profile["rest_id"]),
        "resolved_handle": str(profile["handle"]),
        "profile_name": str(profile.get("name") or identity.get("company") or ""),
        "profile": profile,
        "identity_rule": (
            "Official-domain handle evidence plus an exact case-insensitive Rapid X "
            "screen_name match is required; rest_id must match the cited Rapid record."
        ),
        "evidence_scope": (
            "Official-link adjudication and cached Rapid X identity response only; technical "
            "identity does not establish graph reachability or introduction willingness."
        ),
    }
    return _adjudication_metadata(confirmed, adjudication, source=source, evidence=evidence)


def validated_correction_profile(
    correction: dict[str, Any] | None, official_handle: str,
) -> dict[str, Any] | None:
    """Trust a correction only when every recorded handle/rest_id agrees exactly."""
    if not correction or correction.get("status") != "confirmed_exact_official_handle_from_rapid_profile":
        return None
    if correction.get("confirmed") is not True:
        return None
    expected = normalize_handle(official_handle)
    profile = correction.get("profile")
    if not expected or not isinstance(profile, dict):
        return None
    profile_handle = str(profile.get("handle") or "")
    profile_rest_id = str(profile.get("rest_id") or "")
    recorded_rest_id = str(correction.get("rest_id") or "")
    handles = [
        correction.get("requested_handle"), correction.get("returned_handle"), profile_handle,
    ]
    if any(normalize_handle(value) != expected for value in handles):
        return None
    if not profile_rest_id or recorded_rest_id != profile_rest_id:
        return None
    return profile


def apply_identity_adjudication(
    identity: dict[str, Any],
    cache_record: dict[str, Any],
    adjudication: dict[str, Any] | None,
    correction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply official-link adjudication without relaxing exact Rapid X identity rules."""
    if adjudication is None:
        return _base_adjudication_metadata(identity)

    adjudication_status = str(adjudication.get("status") or "")
    official_handle = str(adjudication.get("proposed_handle") or "")
    official_evidence = list(adjudication.get("evidence") or [])
    initial_cache = identity.get("cache_file", "")

    if adjudication_status == "unresolved_no_official_link":
        return _unconfirmed_after_adjudication(
            identity, adjudication, "unresolved_no_official_link",
            source="official_link_adjudication_unresolved",
            evidence=official_evidence + ([initial_cache] if initial_cache else []),
        )

    if adjudication_status == "confirmed_official_link_matches_rapid_candidate":
        match = adjudication.get("rapid_candidate_match") or {}
        adjudicated_rest_id = str(adjudication.get("rest_id") or "")
        metadata_exact = (
            match.get("matched") is True
            and normalize_handle(match.get("handle")) == normalize_handle(official_handle)
            and str(match.get("rest_id") or "") == adjudicated_rest_id
        )
        exact = [
            profile for profile in unique_normalized_users(cache_record.get("response"))
            if normalize_handle(profile["handle"]) == normalize_handle(official_handle)
            and profile["rest_id"] == adjudicated_rest_id
        ]
        if metadata_exact and len(exact) == 1:
            return _confirmed_after_adjudication(
                identity, adjudication, exact[0],
                "confirmed_official_link_exact_cached_rapid_candidate",
                source="official_link_plus_existing_rapid_x_candidate",
                evidence=official_evidence + ([initial_cache] if initial_cache else []),
            )
        return _unconfirmed_after_adjudication(
            identity, adjudication, "official_adjudication_cached_rapid_exact_match_failed",
            source="official_link_plus_existing_rapid_x_candidate_validation_failed",
            evidence=official_evidence + ([initial_cache] if initial_cache else []),
        )

    if adjudication_status == "corrected_handle_requires_rapid_lookup":
        profile = validated_correction_profile(correction, official_handle)
        correction_cache = str((correction or {}).get("cache_file") or "")
        evidence = official_evidence + ([correction_cache] if correction_cache else [])
        if profile is not None:
            return _confirmed_after_adjudication(
                identity, adjudication, profile,
                "confirmed_corrected_official_handle_exact_rapid_profile",
                source="official_link_plus_rapid_x_correction_profile_lookup",
                evidence=evidence,
            )
        correction_status = str((correction or {}).get("status") or "lookup_pending")
        if correction_status == "confirmed_exact_official_handle_from_rapid_profile":
            correction_status = "resolution_artifact_exact_validation_failed"
        return _unconfirmed_after_adjudication(
            identity, adjudication, f"corrected_official_handle_{correction_status}",
            source="official_link_plus_rapid_x_correction_profile_lookup_unconfirmed",
            evidence=evidence,
        )

    return _unconfirmed_after_adjudication(
        identity, adjudication, "unsupported_identity_adjudication_status",
        source="identity_adjudication_rejected", evidence=official_evidence,
    )


def unresolved_identity_from_frontier(frontier: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "priority_rank": frontier.get("priority_rank"),
        "company": frontier.get("company", ""),
        "aliases": frontier.get("aliases", []),
        "cohort": frontier.get("cohort", ""),
        "frontier_handle_status": frontier.get("handle_status", ""),
        "requested_handle": frontier.get("handle", ""),
        "result_type": "missing_cache",
        "cache_file": "",
        "cache_status": "missing",
        "technical_x_identity_status": status,
        "technical_identity_confirmed": False,
        "manual_match_required": True,
        "resolved_rest_id": "",
        "resolved_handle": "",
        "profile_name": "",
        "profile": None,
        "search_candidates": [],
        "search_candidate_count": 0,
        "search_candidate_handles": [],
        "identity_rule": "profile_lookup requires exact requested-handle match; search results always require manual match",
        "evidence_scope": "No usable cached Rapid X identity response.",
    }


def index_unique_by_company(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        company = str(row.get("company") or "")
        if not company:
            raise ValueError(f"{label} row is missing company")
        if company in indexed:
            raise ValueError(f"Duplicate {label} company: {company}")
        indexed[company] = row
    return indexed


def load_identities(
    frontier_rows: list[dict[str, Any]],
    adjudication_rows: list[dict[str, Any]] | None = None,
    correction_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    adjudications = index_unique_by_company(adjudication_rows or [], "identity adjudication")
    corrections = index_unique_by_company(correction_rows or [], "identity correction")
    frontier_companies = {str(row.get("company") or "") for row in frontier_rows}
    unknown_adjudications = sorted(set(adjudications) - frontier_companies)
    unknown_corrections = sorted(set(corrections) - frontier_companies)
    if unknown_adjudications:
        raise ValueError(f"Identity adjudications outside frontier: {unknown_adjudications}")
    if unknown_corrections:
        raise ValueError(f"Identity corrections outside frontier: {unknown_corrections}")

    cache_by_rank: dict[int, tuple[Path, dict[str, Any]]] = {}
    for path in sorted(CACHE_V4.glob("target-*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        rank = (record.get("request") or {}).get("priority_rank")
        if isinstance(rank, int):
            cache_by_rank[rank] = (path, record)
    identities = []
    for frontier in sorted(frontier_rows, key=lambda row: row["priority_rank"]):
        rank = int(frontier["priority_rank"])
        cached = cache_by_rank.get(rank)
        if not cached:
            identity = unresolved_identity_from_frontier(frontier, "unresolved_target_cache_missing")
            identities.append(apply_identity_adjudication(
                identity, {}, adjudications.get(identity["company"]), corrections.get(identity["company"]),
            ))
            continue
        path, record = cached
        identity = resolve_target_identity(record, str(path.relative_to(ROOT)))
        if identity["company"] != frontier["company"]:
            identity = unresolved_identity_from_frontier(
                frontier, "unresolved_cache_request_company_mismatch"
            )
            record = {}
        identities.append(apply_identity_adjudication(
            identity, record, adjudications.get(identity["company"]), corrections.get(identity["company"]),
        ))
    return identities


def coverage_label(complete: bool, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    return "complete" if complete else "page_capped_partial"


def load_direction_snapshot(handle: str, kind: str) -> dict[str, Any]:
    if kind not in {"following", "followers"}:
        raise ValueError(f"Unsupported direction kind: {kind}")
    v4_path = CACHE_V4 / f"{kind}-{handle}.json"
    if v4_path.exists():
        payload = json.loads(v4_path.read_text(encoding="utf-8"))
        complete = bool(payload.get("complete"))
        return {
            "kind": kind,
            "handle": handle,
            "ids": sorted({str(value) for value in payload.get("ids", [])}),
            "pages": int(payload.get("pages", 0)),
            "complete": complete,
            "coverage_label": coverage_label(complete, payload.get("coverage_label")),
            "cache_file": str(v4_path.relative_to(ROOT)),
            "status": payload.get("status", "cached"),
        }
    ids, complete, pages = load_best_id_cache(CACHE_ROOT, handle, kind)
    return {
        "kind": kind,
        "handle": handle,
        "ids": sorted(ids),
        "pages": pages,
        "complete": complete,
        "coverage_label": coverage_label(complete, "complete" if complete else "partial_or_missing"),
        "cache_file": f"data/cache/rapidx/best_available:{kind}:{handle}",
        "status": "best_available_cache" if pages else "missing",
    }


def add_node(
    nodes: dict[str, dict[str, Any]],
    rest_id: str,
    *,
    role: str,
    evidence: str,
    cache_coverage: str,
    handle: str = "",
    name: str = "",
) -> None:
    if not rest_id:
        return
    node = nodes.setdefault(rest_id, {
        "rest_id": rest_id,
        "handle": "",
        "name": "",
        "roles": set(),
        "evidence": set(),
        "cache_coverage": set(),
    })
    node["roles"].add(role)
    node["evidence"].add(evidence)
    node["cache_coverage"].add(cache_coverage)
    if handle and not node["handle"]:
        node["handle"] = handle
    if name and not node["name"]:
        node["name"] = name


def add_observed_edge(
    adjacency: dict[str, set[str]],
    edge_ledger: dict[tuple[str, str], dict[str, Any]],
    source: str,
    target: str,
    observation: dict[str, Any],
) -> None:
    """Add one positive directed observation; partial-cache absence is never converted to a negative edge."""
    source = str(source)
    target = str(target)
    if not source or not target or source == target:
        return
    adjacency.setdefault(source, set()).add(target)
    ledger = edge_ledger.setdefault((source, target), {
        "source_rest_id": source,
        "target_rest_id": target,
        "relation": "follows",
        "roles": set(),
        "coverage_labels": set(),
        "cache_files": set(),
        "observer_handles": set(),
        "evidence_observations": [],
    })
    ledger["roles"].add(str(observation.get("edge_role") or "observed_follow"))
    ledger["coverage_labels"].add(str(observation.get("coverage_label") or "unknown"))
    ledger["cache_files"].add(str(observation.get("cache_file") or "unknown"))
    if observation.get("observer_handle"):
        ledger["observer_handles"].add(str(observation["observer_handle"]))
    signature = json.dumps(observation, ensure_ascii=False, sort_keys=True)
    if all(json.dumps(item, ensure_ascii=False, sort_keys=True) != signature for item in ledger["evidence_observations"]):
        ledger["evidence_observations"].append(observation)


def add_direction_snapshot(
    adjacency: dict[str, set[str]],
    edge_ledger: dict[tuple[str, str], dict[str, Any]],
    observer_rest_id: str,
    snapshot: dict[str, Any],
    *,
    edge_role_prefix: str,
) -> None:
    kind = snapshot["kind"]
    if kind not in {"following", "followers"}:
        raise ValueError(f"Unsupported direction kind: {kind}")
    for member_id in snapshot["ids"]:
        source, target = (
            (observer_rest_id, member_id) if kind == "following" else (member_id, observer_rest_id)
        )
        add_observed_edge(adjacency, edge_ledger, source, target, {
            "edge_role": f"{edge_role_prefix}_{kind}",
            "cache_kind": kind,
            "direction_assertion": (
                "observer_follows_member" if kind == "following" else "member_follows_observer"
            ),
            "observer_handle": snapshot["handle"],
            "cache_file": snapshot["cache_file"],
            "coverage_label": snapshot["coverage_label"],
            "complete": snapshot["complete"],
            "pages": snapshot["pages"],
            "positive_edge_observed": True,
        })


def build_graph(
    first_degree_rows: list[dict[str, Any]],
    connector_rows: list[dict[str, Any]],
    identities: list[dict[str, Any]],
) -> tuple[
    dict[str, set[str]],
    dict[tuple[str, str], dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    adjacency: dict[str, set[str]] = {}
    edge_ledger: dict[tuple[str, str], dict[str, Any]] = {}
    nodes: dict[str, dict[str, Any]] = {}
    add_node(
        nodes, SOLOMON_ID, role="root", evidence="configured_root_identity",
        cache_coverage="directional_caches_audited", handle=SOLOMON_HANDLE, name="Solomon",
    )
    first_by_handle: dict[str, dict[str, Any]] = {}
    for row in first_degree_rows:
        rest_id = str(row.get("rest_id") or "")
        if not rest_id:
            continue
        handle = str(row.get("handle") or "")
        add_node(
            nodes, rest_id, role="solomon_first_degree", evidence="cached_first_degree_profile",
            cache_coverage=str(row.get("relationship_certainty") or "unknown"),
            handle=handle, name=str(row.get("name") or ""),
        )
        if handle:
            first_by_handle[normalize_handle(handle)] = row

    root_snapshots = {
        kind: load_direction_snapshot(SOLOMON_HANDLE, kind) for kind in ("following", "followers")
    }
    for snapshot in root_snapshots.values():
        add_direction_snapshot(adjacency, edge_ledger, SOLOMON_ID, snapshot, edge_role_prefix="root")

    connector_coverage = []
    unresolved_connectors = []
    for connector in connector_rows:
        handle = str(connector.get("handle") or "")
        profile = first_by_handle.get(normalize_handle(handle))
        if not profile:
            unresolved_connectors.append(handle)
            continue
        connector_id = str(profile["rest_id"])
        add_node(
            nodes, connector_id, role="expanded_connector", evidence="connector_expansion_log",
            cache_coverage="both_directions_requested", handle=handle,
            name=str(profile.get("name") or ""),
        )
        by_kind = {}
        for kind in ("following", "followers"):
            snapshot = load_direction_snapshot(handle, kind)
            add_direction_snapshot(
                adjacency, edge_ledger, connector_id, snapshot, edge_role_prefix="connector",
            )
            logged = connector.get(kind) or {}
            metadata_matches = (
                int(logged.get("count", len(snapshot["ids"]))) == len(snapshot["ids"])
                and bool(logged.get("complete")) == bool(snapshot["complete"])
            )
            by_kind[kind] = {
                "observed_ids": len(snapshot["ids"]),
                "pages": snapshot["pages"],
                "complete": snapshot["complete"],
                "coverage_label": snapshot["coverage_label"],
                "cache_file": snapshot["cache_file"],
                "metadata_matches_expansion_log": metadata_matches,
            }
        connector_coverage.append({
            "handle": handle,
            "rest_id": connector_id,
            "following": by_kind["following"],
            "followers": by_kind["followers"],
        })

    for identity in identities:
        if not identity["technical_identity_confirmed"]:
            continue
        profile = identity["profile"]
        add_node(
            nodes, identity["resolved_rest_id"], role="target_company",
            evidence=str(identity.get("identity_resolution_source") or "exact_rapid_x_identity"),
            cache_coverage="technical_identity_confirmed",
            handle=identity["resolved_handle"], name=identity.get("target_display_name") or identity["company"],
        )
        if profile:
            add_node(
                nodes, identity["resolved_rest_id"], role="resolved_profile",
                evidence="cached_rapid_x_target_profile", cache_coverage="profile_lookup_exact_match",
                handle=profile["handle"], name=identity.get("target_display_name") or identity["company"],
            )

    for source, targets in adjacency.items():
        add_node(
            nodes, source, role="observed_account", evidence="directional_id_cache_membership",
            cache_coverage="positive_edge_observed",
        )
        for target in targets:
            add_node(
                nodes, target, role="observed_account", evidence="directional_id_cache_membership",
                cache_coverage="positive_edge_observed",
            )

    root_coverage_summary = {
        kind: {
            "kind": snapshot["kind"],
            "handle": snapshot["handle"],
            "observed_ids": len(snapshot["ids"]),
            "pages": snapshot["pages"],
            "complete": snapshot["complete"],
            "coverage_label": snapshot["coverage_label"],
            "cache_file": snapshot["cache_file"],
            "status": snapshot["status"],
        }
        for kind, snapshot in root_snapshots.items()
    }
    audit = {
        "root_directional_coverage": root_coverage_summary,
        "connector_directional_coverage": connector_coverage,
        "expanded_connectors_requested": len(connector_rows),
        "expanded_connectors_resolved": len(connector_coverage),
        "expanded_connectors_unresolved": unresolved_connectors,
    }
    return adjacency, edge_ledger, nodes, audit


def enumerate_candidate_paths(
    weak_adjacency: dict[str, set[str]], source: str, target: str, max_hops: int = 3,
) -> list[list[str]]:
    """Enumerate all simple weak paths up to three hops using endpoint intersections."""
    if max_hops < 1:
        return []
    found: set[tuple[str, ...]] = set()
    source_neighbors = weak_adjacency.get(source, set())
    target_neighbors = weak_adjacency.get(target, set())
    if target in source_neighbors:
        found.add((source, target))
    if max_hops >= 2:
        for middle in source_neighbors & target_neighbors:
            if middle not in {source, target}:
                found.add((source, middle, target))
    if max_hops >= 3:
        for right_middle in target_neighbors:
            if right_middle in {source, target}:
                continue
            for left_middle in source_neighbors & weak_adjacency.get(right_middle, set()):
                path = (source, left_middle, right_middle, target)
                if len(set(path)) == len(path):
                    found.add(path)
    return [list(path) for path in sorted(found, key=lambda path: (len(path), path))]


def enumerate_routable_candidate_paths(
    adjacency: dict[str, set[str]], source: str, target: str, max_hops: int = 3,
) -> tuple[list[list[str]], list[list[str]]]:
    weak = relationship_adjacency(adjacency)
    candidates = enumerate_candidate_paths(weak, source, target, max_hops=max_hops)
    routable = [path for path in candidates if is_routable_path(adjacency, path)]
    rejected = [path for path in candidates if not is_routable_path(adjacency, path)]
    return routable, rejected


def hop_label(hops: int) -> tuple[str, str]:
    labels = {1: ("direct", "L1"), 2: ("secondary", "L2"), 3: ("third", "L3")}
    if hops not in labels:
        raise ValueError(f"Unsupported hop count: {hops}")
    return labels[hops]


def node_label(rest_id: str, nodes: dict[str, dict[str, Any]]) -> str:
    node = nodes.get(rest_id, {})
    name = str(node.get("name") or "")
    handle = str(node.get("handle") or "")
    if name and handle:
        return f"{name} (@{handle})"
    if handle:
        return f"@{handle}"
    return name or rest_id


def serialize_edge_ledger(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_rest_id": edge["source_rest_id"],
        "target_rest_id": edge["target_rest_id"],
        "relation": edge["relation"],
        "roles": sorted(edge["roles"]),
        "coverage_labels": sorted(edge["coverage_labels"]),
        "cache_files": sorted(edge["cache_files"]),
        "observer_handles": sorted(edge["observer_handles"]),
        "evidence_observations": edge["evidence_observations"],
    }


def path_edge_details(
    path: list[str],
    adjacency: dict[str, set[str]],
    edge_ledger: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    details = []
    for traversal_from, traversal_to in zip(path, path[1:]):
        kind = edge_kind(adjacency, traversal_from, traversal_to)
        directed_keys = []
        if traversal_to in adjacency.get(traversal_from, set()):
            directed_keys.append((traversal_from, traversal_to))
        if traversal_from in adjacency.get(traversal_to, set()):
            directed_keys.append((traversal_to, traversal_from))
        details.append({
            "traversal_from": traversal_from,
            "traversal_to": traversal_to,
            "relationship_kind": kind,
            "observed_directed_edges": [
                serialize_edge_ledger(edge_ledger[key]) for key in directed_keys if key in edge_ledger
            ],
        })
    return details


def enrich_path(
    path: list[str],
    adjacency: dict[str, set[str]],
    edge_ledger: dict[tuple[str, str], dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    hops = len(path) - 1
    degree, relationship_level = hop_label(hops)
    details = path_edge_details(path, adjacency, edge_ledger)
    coverage_labels = sorted({
        label
        for detail in details
        for edge in detail["observed_directed_edges"]
        for label in edge["coverage_labels"]
    })
    return {
        "path_rest_ids": path,
        "path_labels": [node_label(rest_id, nodes) for rest_id in path],
        "hop_count": hops,
        "x_degree": degree,
        "x_relationship_level": relationship_level,
        "edge_directions": [detail["relationship_kind"] for detail in details],
        "edge_details": details,
        "cache_coverage_labels": coverage_labels,
        "path_strength": path_strength(adjacency, path),
        "operational_x_score": operational_x_score(adjacency, path),
        "graph_reachable": True,
        "human_intro_status": "human_intro_unvalidated",
    }


def sort_enriched_paths(paths: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(paths, key=lambda row: (
        -row["operational_x_score"], -row["path_strength"], row["hop_count"], row["path_rest_ids"],
    ))


def build_target_paths(
    identities: list[dict[str, Any]],
    adjacency: dict[str, set[str]],
    edge_ledger: dict[tuple[str, str], dict[str, Any]],
    nodes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    weak = relationship_adjacency(adjacency)
    rows = []
    for identity in identities:
        common = {
            "priority_rank": identity["priority_rank"],
            "company": identity["company"],
            "technical_x_identity_status": identity["technical_x_identity_status"],
            "target_handle": identity["resolved_handle"] or identity["requested_handle"],
            "target_rest_id": identity["resolved_rest_id"],
            "evidence_scope": "Positive cached follow observations only; cache completeness labels constrain negative inference.",
        }
        if not identity["technical_identity_confirmed"]:
            rows.append({
                **common,
                "graph_reachable": None,
                "graph_reachability_status": "not_evaluable_identity_unconfirmed",
                "x_relationship_level": "unknown",
                "x_degree": "unresolved_identity",
                "hop_count": None,
                "primary_path": None,
                "primary_path_labels": [],
                "primary_path_rest_ids": [],
                "primary_edge_directions": [],
                "primary_path_strength": None,
                "primary_operational_x_score": 0,
                "candidate_path_count": 0,
                "routable_path_count": 0,
                "rejected_shared_interest_v_count": 0,
                "alternative_path_count_total": 0,
                "alternative_paths_retained_count": 0,
                "alternative_paths_truncated_count": 0,
                "alternative_paths": [],
                "human_intro_status": "not_evaluable_identity_unconfirmed",
            })
            continue
        target_id = identity["resolved_rest_id"]
        candidates = enumerate_candidate_paths(weak, SOLOMON_ID, target_id, max_hops=3)
        routable_raw = [path for path in candidates if is_routable_path(adjacency, path)]
        rejected = [path for path in candidates if not is_routable_path(adjacency, path)]
        enriched = sort_enriched_paths(
            enrich_path(path, adjacency, edge_ledger, nodes) for path in routable_raw
        )
        if not enriched:
            rows.append({
                **common,
                "graph_reachable": False,
                "graph_reachability_status": "no_path_in_observed_graph",
                "x_relationship_level": "unknown",
                "x_degree": "no_path_in_audited_graph",
                "hop_count": None,
                "primary_path": None,
                "primary_path_labels": [],
                "primary_path_rest_ids": [],
                "primary_edge_directions": [],
                "primary_path_strength": None,
                "primary_operational_x_score": 0,
                "candidate_path_count": len(candidates),
                "routable_path_count": 0,
                "rejected_shared_interest_v_count": len(rejected),
                "alternative_path_count_total": 0,
                "alternative_paths_retained_count": 0,
                "alternative_paths_truncated_count": 0,
                "alternative_paths": [],
                "human_intro_status": "not_applicable_no_graph_path",
            })
            continue
        primary = enriched[0]
        all_alternatives = enriched[1:]
        alternatives = all_alternatives[:MAX_ALTERNATIVE_PATHS]
        rows.append({
            **common,
            "graph_reachable": True,
            "graph_reachability_status": "reachable_in_observed_graph",
            "x_relationship_level": primary["x_relationship_level"],
            "x_degree": primary["x_degree"],
            "hop_count": primary["hop_count"],
            "primary_path": primary,
            "primary_path_labels": primary["path_labels"],
            "primary_path_rest_ids": primary["path_rest_ids"],
            "primary_edge_directions": primary["edge_directions"],
            "primary_path_strength": primary["path_strength"],
            "primary_operational_x_score": primary["operational_x_score"],
            "candidate_path_count": len(candidates),
            "routable_path_count": len(enriched),
            "rejected_shared_interest_v_count": len(rejected),
            "alternative_path_count_total": len(all_alternatives),
            "alternative_paths_retained_count": len(alternatives),
            "alternative_paths_truncated_count": max(0, len(all_alternatives) - len(alternatives)),
            "alternative_paths": alternatives,
            "human_intro_status": "human_intro_unvalidated",
        })
    return sorted(rows, key=lambda row: row["priority_rank"])


def graphml_text(value: Iterable[str] | str) -> str:
    return "|".join(sorted(value)) if not isinstance(value, str) else value


def export_graphml(
    nodes: dict[str, dict[str, Any]],
    edge_ledger: dict[tuple[str, str], dict[str, Any]],
    path: Path,
) -> None:
    ns = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", ns)
    root = ET.Element(f"{{{ns}}}graphml")
    keys = [
        ("n_label", "node", "label"),
        ("n_handle", "node", "handle"),
        ("n_role", "node", "role"),
        ("n_evidence", "node", "evidence"),
        ("n_coverage", "node", "cache_coverage"),
        ("e_relation", "edge", "relation"),
        ("e_role", "edge", "role"),
        ("e_evidence", "edge", "evidence"),
        ("e_coverage", "edge", "cache_coverage"),
        ("e_observer", "edge", "observer_handle"),
        ("e_count", "edge", "evidence_count"),
    ]
    for key_id, scope, name in keys:
        ET.SubElement(root, f"{{{ns}}}key", {
            "id": key_id, "for": scope, "attr.name": name,
            "attr.type": "int" if key_id == "e_count" else "string",
        })
    graph = ET.SubElement(root, f"{{{ns}}}graph", {
        "id": "Solomon_X_Expanded_v4", "edgedefault": "directed",
    })
    for rest_id in sorted(nodes):
        item = nodes[rest_id]
        node = ET.SubElement(graph, f"{{{ns}}}node", {"id": f"x_{rest_id}"})
        values = {
            "n_label": item.get("name") or item.get("handle") or rest_id,
            "n_handle": item.get("handle") or "",
            "n_role": graphml_text(item["roles"]),
            "n_evidence": graphml_text(item["evidence"]),
            "n_coverage": graphml_text(item["cache_coverage"]),
        }
        for key, value in values.items():
            ET.SubElement(node, f"{{{ns}}}data", {"key": key}).text = str(value)
    for index, key in enumerate(sorted(edge_ledger), 1):
        item = edge_ledger[key]
        edge = ET.SubElement(graph, f"{{{ns}}}edge", {
            "id": f"e{index}", "source": f"x_{key[0]}", "target": f"x_{key[1]}",
        })
        values = {
            "e_relation": "follows",
            "e_role": graphml_text(item["roles"]),
            "e_evidence": graphml_text(item["cache_files"]),
            "e_coverage": graphml_text(item["coverage_labels"]),
            "e_observer": graphml_text(item["observer_handles"]),
            "e_count": len(item["evidence_observations"]),
        }
        for data_key, value in values.items():
            ET.SubElement(edge, f"{{{ns}}}data", {"key": data_key}).text = str(value)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    frontier_rows = json.loads(FRONTIER.read_text(encoding="utf-8"))
    first_degree_rows = json.loads(FIRST_DEGREE.read_text(encoding="utf-8"))
    connector_rows = json.loads(CONNECTOR_LOG.read_text(encoding="utf-8"))
    adjudication_rows = json.loads(ADJUDICATIONS.read_text(encoding="utf-8"))
    correction_rows = (
        json.loads(CORRECTION_RESOLUTIONS.read_text(encoding="utf-8"))
        if CORRECTION_RESOLUTIONS.exists() else []
    )
    if len(frontier_rows) != 51:
        raise ValueError(f"Expected 51 v4 targets, found {len(frontier_rows)}")
    if len(adjudication_rows) != 26:
        raise ValueError(f"Expected 26 official-link identity adjudications, found {len(adjudication_rows)}")

    identities = load_identities(frontier_rows, adjudication_rows, correction_rows)
    adjacency, edge_ledger, nodes, graph_audit = build_graph(
        first_degree_rows, connector_rows, identities,
    )
    target_paths = build_target_paths(identities, adjacency, edge_ledger, nodes)

    identity_json = OUT / "x_target_identities_v4.json"
    identity_csv = OUT / "x_target_identities_v4.csv"
    paths_json = OUT / "solomon_paths_expanded_v4.json"
    paths_csv = OUT / "solomon_paths_expanded_v4.csv"
    graphml = OUT / "solomon_graph_expanded_v4.graphml"
    summary_json = OUT / "x_graph_summary_v4.json"

    write_json(identity_json, identities)
    write_csv(identity_csv, identities, IDENTITY_CSV_FIELDS)
    write_json(paths_json, target_paths)
    write_csv(paths_csv, target_paths, PATH_CSV_FIELDS)
    export_graphml(nodes, edge_ledger, graphml)

    identity_counts = Counter(row["technical_x_identity_status"] for row in identities)
    path_counts = Counter(row["x_degree"] for row in target_paths)
    handle_status_counts = Counter(row["frontier_handle_status"] for row in identities)
    adjudication_counts = Counter(row["identity_adjudication_status"] for row in identities)
    resolution_source_counts = Counter(row["identity_resolution_source"] for row in identities)
    correction_counts = Counter(row.get("status", "") for row in correction_rows)
    edge_role_counts = Counter(
        role for item in edge_ledger.values() for role in item["roles"]
    )
    coverage_counts = Counter(
        label for item in edge_ledger.values() for label in item["coverage_labels"]
    )
    summary = {
        "schema_version": "v4",
        "api_calls_made": 0,
        "root": {"name": "Solomon", "handle": SOLOMON_HANDLE, "rest_id": SOLOMON_ID},
        "identity_resolution": {
            "targets_total": len(identities),
            "frontier_handle_status_counts": dict(sorted(handle_status_counts.items())),
            "technical_identity_status_counts": dict(sorted(identity_counts.items())),
            "technical_identities_confirmed": sum(row["technical_identity_confirmed"] for row in identities),
            "manual_match_required": sum(row["manual_match_required"] for row in identities),
            "official_link_adjudication_status_counts": dict(sorted(adjudication_counts.items())),
            "resolution_source_counts": dict(sorted(resolution_source_counts.items())),
            "correction_resolution_status_counts": dict(sorted(correction_counts.items())),
            "official_link_adjudication_file": str(ADJUDICATIONS.relative_to(ROOT)),
            "correction_resolution_file": (
                str(CORRECTION_RESOLUTIONS.relative_to(ROOT)) if CORRECTION_RESOLUTIONS.exists() else "missing"
            ),
            "rule": (
                "Initial profile lookups require an exact requested-handle match. Official-link "
                "adjudications confirm only when official handle and rest_id exactly match an existing "
                "Rapid X User candidate; corrected handles require a new exact Rapid X profile response. "
                "Unresolved official-link adjudications remain unconfirmed."
            ),
        },
        "graph": {
            "node_count": len(nodes),
            "directed_edge_count": len(edge_ledger),
            "edge_role_observation_counts": dict(sorted(edge_role_counts.items())),
            "edge_cache_coverage_counts": dict(sorted(coverage_counts.items())),
            "direction_definition": {
                "following": "observer -> member",
                "followers": "member -> observer",
                "degree_view": "weak adjacency used only for hop enumeration; every edge retains observed direction",
            },
            "sampling_rule": "A partial cache still proves observed positive edges; absence in a partial cache is never treated as a negative edge.",
            **graph_audit,
        },
        "paths": {
            "targets_total": len(target_paths),
            "degree_counts": dict(sorted(path_counts.items())),
            "graph_reachable_targets": sum(row["graph_reachable"] is True for row in target_paths),
            "graph_unreachable_confirmed_targets": sum(row["graph_reachable"] is False for row in target_paths),
            "identity_unconfirmed_not_evaluable": sum(row["graph_reachable"] is None for row in target_paths),
            "routable_paths_total": sum(row["routable_path_count"] for row in target_paths),
            "shared_interest_v_paths_rejected": sum(row["rejected_shared_interest_v_count"] for row in target_paths),
            "alternative_paths_retained": sum(row["alternative_paths_retained_count"] for row in target_paths),
            "alternative_paths_truncated": sum(row["alternative_paths_truncated_count"] for row in target_paths),
            "max_alternatives_per_target": MAX_ALTERNATIVE_PATHS,
            "human_intro_rule": "Graph reachability is a technical follow-path observation only; every reachable path remains human_intro_unvalidated.",
        },
        "outputs": [
            str(identity_json.relative_to(ROOT)), str(identity_csv.relative_to(ROOT)),
            str(paths_json.relative_to(ROOT)), str(paths_csv.relative_to(ROOT)),
            str(graphml.relative_to(ROOT)), str(summary_json.relative_to(ROOT)),
        ],
    }
    write_json(summary_json, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
