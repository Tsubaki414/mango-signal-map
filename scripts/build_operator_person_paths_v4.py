#!/usr/bin/env python3
"""Build person-level Solomon/Mango X paths for strictly verified operators.

Company-account reachability and operator-person reachability are different
facts.  This builder evaluates the latter against the same cached, directed X
edge ledgers used by the v4 company graph.  Unverified people remain explicitly
not evaluable; an operator's employer is never treated as a relationship edge.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_mango_seed_audit import (
    SEED_CACHE,
    audit_seed,
    build_target_relationships as build_mango_paths,
    load_direction_snapshot as load_mango_snapshot,
)
from build_v4_x_graph import (
    CONNECTOR_LOG,
    FIRST_DEGREE,
    build_graph as build_solomon_graph,
    build_target_paths as build_solomon_paths,
)


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs" / "pilot_v4" / "operator_x_identity_resolutions_v4.json"
OUTPUT = ROOT / "outputs" / "pilot_v4" / "operator_person_paths_v4.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_company(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def resolution_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("resolutions"), list):
        return payload["resolutions"]
    if isinstance(payload, list):
        return payload
    raise ValueError("Operator identity resolution artifact has no resolutions list")


def solomon_identity(row: dict[str, Any], rank: int) -> dict[str, Any]:
    profile = row.get("returned_profile") or {}
    candidate = row.get("candidate_operator") or {}
    confirmed = bool(row.get("confirmed"))
    return {
        "priority_rank": rank,
        "company": row.get("company"),
        "target_display_name": candidate.get("name") or row.get("company"),
        "technical_x_identity_status": row.get("status"),
        "technical_identity_confirmed": confirmed,
        "requested_handle": str(candidate.get("x_handle") or ""),
        "resolved_rest_id": str(profile.get("rest_id") or "") if confirmed else "",
        "resolved_handle": str(profile.get("handle") or "") if confirmed else "",
        "profile_name": str(profile.get("name") or "") if confirmed else "",
        "profile": profile if confirmed else None,
        "operator_name": candidate.get("name"),
        "operator_role": candidate.get("role"),
    }


def mango_target(row: dict[str, Any]) -> dict[str, Any]:
    profile = row.get("returned_profile") or {}
    candidate = row.get("candidate_operator") or {}
    confirmed = bool(row.get("confirmed"))
    company = str(row.get("company") or "")
    handle = str(profile.get("handle") or candidate.get("x_handle") or "")
    return {
        "company_key": normalize_company(company),
        "company": company,
        "aliases": [],
        "cohorts": ["operator_person"],
        "requested_handles": [handle] if handle else [],
        "technical_x_identity_status": row.get("status"),
        "technical_identity_confirmed": confirmed,
        "target_rest_id": str(profile.get("rest_id") or "") if confirmed else "",
        "target_handle": handle if confirmed else "",
        "identity_last_verified_at": row.get("last_verified_at"),
        "operator_name": candidate.get("name"),
        "operator_role": candidate.get("role"),
    }


def build_mango_runtime() -> list[dict[str, Any]]:
    profiles = json.loads((SEED_CACHE / "profiles.json").read_text(encoding="utf-8"))
    runtime = []
    for profile in profiles:
        handle = str(profile.get("handle") or "")
        following = load_mango_snapshot(SEED_CACHE, handle, "following")
        followers = load_mango_snapshot(SEED_CACHE, handle, "followers")
        runtime.append({
            "audit": audit_seed(profile, following, followers),
            "following": following,
            "followers": followers,
        })
    return runtime


def compact_solomon(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "graph_reachable", "graph_reachability_status", "x_relationship_level", "x_degree",
            "hop_count", "primary_path", "primary_path_labels", "primary_path_rest_ids",
            "primary_edge_directions", "primary_path_strength", "primary_operational_x_score",
            "candidate_path_count", "routable_path_count", "rejected_shared_interest_v_count",
            "alternative_paths_retained_count", "alternative_paths", "human_intro_status", "evidence_scope",
        )
    }


def compact_mango(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "graph_reachable", "graph_reachability_status", "x_relationship_level", "x_degree",
            "direct_seed_edges", "mango_direct_paths", "mango_secondary_paths",
            "rejected_shared_interest_v_count", "relationship_coverage_complete",
            "non_observation_is_not_negative_evidence", "human_intro_status", "last_verified_at",
            "fact_status", "confidence", "evidence_scope",
        )
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    rows = resolution_rows(payload)
    solomon_identities = [solomon_identity(row, index) for index, row in enumerate(rows, 1)]

    first_degree = json.loads(FIRST_DEGREE.read_text(encoding="utf-8"))
    connectors = json.loads(CONNECTOR_LOG.read_text(encoding="utf-8"))
    adjacency, edge_ledger, nodes, graph_audit = build_solomon_graph(
        first_degree, connectors, solomon_identities,
    )
    solomon_rows = build_solomon_paths(solomon_identities, adjacency, edge_ledger, nodes)
    solomon_by_company = {normalize_company(row["company"]): row for row in solomon_rows}

    mango_rows = build_mango_paths([mango_target(row) for row in rows], build_mango_runtime())
    mango_by_company = {normalize_company(row["company"]): row for row in mango_rows}

    records = []
    for row in rows:
        company = str(row.get("company") or "")
        key = normalize_company(company)
        candidate = row.get("candidate_operator") or {}
        profile = row.get("returned_profile") or {}
        records.append({
            "company": company,
            "operator_person": {
                "name": candidate.get("name") if row.get("confirmed") else None,
                "role": candidate.get("role") if row.get("confirmed") else None,
                "x_handle": profile.get("handle") if row.get("confirmed") else None,
                "x_rest_id": profile.get("rest_id") if row.get("confirmed") else None,
                "identity_status": row.get("status"),
                "identity_confirmed": bool(row.get("confirmed")),
            },
            "solomon_to_operator_person": compact_solomon(solomon_by_company[key]),
            "mango_to_operator_person": compact_mango(mango_by_company[key]),
            "separation_rule": (
                "These paths terminate at the verified person's X account. Company-account paths are "
                "stored separately and are not copied or inferred here."
            ),
        })

    artifact = {
        "schema_version": "operator-person-paths-v4.1",
        "generated_at": utc_now(),
        "root_scope": "Solomon and @MangoLabs_ paths over cached directed Rapid X follow observations",
        "identity_source": str(args.input.relative_to(ROOT)),
        "summary": {
            "company_count": len(records),
            "operator_identity_confirmed_count": sum(item["operator_person"]["identity_confirmed"] for item in records),
            "solomon_operator_reachable_count": sum(item["solomon_to_operator_person"]["graph_reachable"] is True for item in records),
            "mango_operator_reachable_count": sum(item["mango_to_operator_person"]["graph_reachable"] is True for item in records),
            "solomon_graph_node_count": len(nodes),
            "solomon_graph_directed_edge_count": len(edge_ledger),
            "solomon_connector_coverage": graph_audit,
        },
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**artifact["summary"], "output": str(args.output.relative_to(ROOT))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
