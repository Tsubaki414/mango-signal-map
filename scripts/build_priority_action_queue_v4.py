#!/usr/bin/env python3
"""Build graph-adjusted v4 rankings and a non-fabricated BD action queue."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from mangobd.discovery import score_candidate


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "pilot_v4"
CONFIG = ROOT / "config" / "discovery_v4.json"
NEW_RANKINGS = OUT / "new_candidate_rankings_v4.json"
X_PATHS = OUT / "solomon_paths_expanded_v4.json"
X_INTERACTIONS = OUT / "x_path_interactions_v4.json"
MANGO_INTERACTIONS = OUT / "mango_path_interactions_v4.json"
OPERATORS = ROOT / "data" / "pilot_v4" / "top_operator_routes.json"
OPERATOR_PERSON_PATHS = OUT / "operator_person_paths_v4.json"
SPONSORS = ROOT / "data" / "pilot_v4" / "sponsor_observations_expanded.json"
OLD_ACTIONS = ROOT / "outputs" / "pilot_v3" / "solomon_action_queue.json"
V3_RANKINGS = ROOT / "outputs" / "pilot_v3" / "company_rankings_solomon_x.json"
MANGO_PATHS = OUT / "mango_seed_target_paths.json"
LEGACY_PROJECTS = ROOT / "data" / "pilot" / "projects.json"
LEGACY_EVIDENCE = ROOT / "data" / "pilot" / "evidence.json"
LEGACY_EXPANSION = ROOT / "data" / "pilot_v2" / "expanded_companies.json"
V3_PATHS = ROOT / "outputs" / "pilot_v3" / "solomon_x_paths.json"
RAPID_X_CACHE = ROOT / "data" / "cache" / "rapidx"

ADJUSTED_JSON = OUT / "new_candidate_rankings_graph_adjusted_v4.json"
ADJUSTED_CSV = OUT / "new_candidate_rankings_graph_adjusted_v4.csv"
QUEUE_JSON = OUT / "priority_action_queue_v4.json"
QUEUE_CSV = OUT / "priority_action_queue_v4.csv"
SUMMARY_JSON = OUT / "priority_action_queue_summary_v4.json"

HANDLE_RE = re.compile(r"@([A-Za-z0-9_]{1,15})")

CONFIRMED_TECHNICAL_IDENTITY_STATUSES = {
    "confirmed_exact_requested_handle",
    "confirmed_official_link_exact_cached_rapid_candidate",
    "confirmed_corrected_official_handle_exact_rapid_profile",
}


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _handle_from_label(value: str) -> str:
    matches = HANDLE_RE.findall(str(value or ""))
    return matches[-1] if matches else ""


def _positive_following_cache_files(source_handle: str, target_rest_id: str) -> list[str]:
    """Return legacy Rapid X pages that explicitly contain source -> target.

    The v3 graph was built from paginated ``following-ids-*`` responses.  We
    re-check the atomic positive ID observation here instead of treating an
    ``edge_types`` label as sufficient provenance for a directed edge.
    """
    evidence: list[str] = []
    if not source_handle or not target_rest_id:
        return evidence
    for path in sorted(RAPID_X_CACHE.glob(f"following-ids-{source_handle}*.json")):
        payload = load_json(path, {})
        ids = {str(value) for value in payload.get("ids") or []}
        if str(target_rest_id) in ids:
            evidence.append(str(path.relative_to(ROOT)))
    return evidence


def legacy_direction_backfill(path: dict[str, Any] | None) -> dict[str, Any]:
    """Expand a retained v3 weak-path into cache-proven directed observations.

    ``mutual_follow`` becomes two directed observations; ``follows`` follows
    traversal order; and ``followed_by`` reverses it.  If any required positive
    observation cannot be found in the original paginated Rapid X cache, no
    partial direction list is emitted and the record is explicitly unavailable.
    """
    path = path or {}
    rest_ids = [str(value) for value in path.get("path_rest_ids") or []]
    labels = [str(value) for value in path.get("path_labels") or []]
    edge_kinds = [str(value) for value in path.get("edge_types") or []]
    if len(rest_ids) < 2 or len(labels) != len(rest_ids) or len(edge_kinds) != len(rest_ids) - 1:
        return {
            "primary_edge_kinds": edge_kinds,
            "primary_edge_directions": [],
            "primary_edge_rest_id_directions": [],
            "direction_evidence_files": [],
            "direction_data_unavailable": True,
            "direction_data_reason": "Legacy primary path is missing aligned node IDs, labels, or edge kinds.",
        }

    handles = [_handle_from_label(label) for label in labels]
    required: list[tuple[int, int]] = []
    for index, kind in enumerate(edge_kinds):
        if kind == "mutual_follow":
            required.extend([(index, index + 1), (index + 1, index)])
        elif kind == "follows":
            required.append((index, index + 1))
        elif kind == "followed_by":
            required.append((index + 1, index))
        else:
            return {
                "primary_edge_kinds": edge_kinds,
                "primary_edge_directions": [],
                "primary_edge_rest_id_directions": [],
                "direction_evidence_files": [],
                "direction_data_unavailable": True,
                "direction_data_reason": f"Unsupported legacy edge kind: {kind or 'missing'}.",
            }

    directions: list[str] = []
    rest_id_directions: list[str] = []
    evidence_files: list[str] = []
    missing: list[str] = []
    for source_index, target_index in required:
        source_handle = handles[source_index]
        target_handle = handles[target_index]
        evidence = _positive_following_cache_files(source_handle, rest_ids[target_index])
        label = f"@{source_handle} -> @{target_handle}" if source_handle and target_handle else f"{rest_ids[source_index]} -> {rest_ids[target_index]}"
        if not evidence:
            missing.append(label)
            continue
        directions.append(label)
        rest_id_directions.append(f"{rest_ids[source_index]}->{rest_ids[target_index]}")
        evidence_files.extend(evidence)

    if missing:
        return {
            "primary_edge_kinds": edge_kinds,
            "primary_edge_directions": [],
            "primary_edge_rest_id_directions": [],
            "direction_evidence_files": sorted(set(evidence_files)),
            "direction_data_unavailable": True,
            "direction_data_reason": "Missing positive legacy Rapid X cache observation(s): " + ", ".join(missing),
        }
    return {
        "primary_edge_kinds": edge_kinds,
        "primary_edge_directions": directions,
        "primary_edge_rest_id_directions": rest_id_directions,
        "direction_evidence_files": sorted(set(evidence_files)),
        "direction_data_unavailable": False,
        "direction_data_reason": "Backfilled from positive IDs in the original paginated Rapid X following caches; no negative inference was used.",
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def csv_value(value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: csv_value(row.get(field, "")) for field in fields})


def path_handles(path: dict[str, Any]) -> list[str]:
    handles: list[str] = []
    for label in path.get("primary_path_labels") or []:
        matches = HANDLE_RE.findall(str(label))
        handles.append(matches[-1] if matches else "")
    return handles


def interaction_index(artifact: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in artifact.get("observations") or []:
        key = (str(row.get("source_handle", "")).lower(), str(row.get("destination_handle", "")).lower())
        grouped[key].append(row)
    return grouped


def connector_interaction_signal(path: dict[str, Any], index: dict[tuple[str, str], list[dict[str, Any]]]) -> dict[str, Any]:
    handles = path_handles(path)
    if len(handles) < 2 or not handles[0] or not handles[1]:
        return {
            "state": "not_evaluable",
            "connector_handle": "",
            "root_to_connector_count": 0,
            "connector_to_root_count": 0,
            "interaction_types": [],
            "evidence_urls": [],
            "human_intro_status": "human_intro_unvalidated",
        }
    root, connector = handles[:2]
    outbound = index.get((root.lower(), connector.lower()), [])
    inbound = index.get((connector.lower(), root.lower()), [])
    if outbound and inbound:
        state = "reciprocal_public_interaction_observed"
    elif outbound or inbound:
        state = "one_way_public_interaction_observed"
    else:
        state = "follow_path_only_no_interaction_observed_in_bounded_sample"
    observations = [*outbound, *inbound]
    return {
        "state": state,
        "connector_handle": f"@{connector}",
        "root_to_connector_count": len(outbound),
        "connector_to_root_count": len(inbound),
        "interaction_types": sorted({str(row.get("interaction_type")) for row in observations}),
        "evidence_urls": unique(str(row.get("tweet_url") or "") for row in observations),
        "sample_limitation": "Positive hits prove public interactions only. Zero hits do not prove no interaction. Intro willingness remains unvalidated.",
        "human_intro_status": "human_intro_unvalidated",
    }


def mango_interaction_signal(
    target: dict[str, Any] | None,
    index: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    """Summarize public interactions on observed Mango→seed→target paths.

    This is deliberately an evidence annotation, not a relationship score. A
    positive hit proves only that a public interaction was observed; a zero is
    bounded-sample absence and never a negative relationship inference.
    """
    paths = (target or {}).get("mango_secondary_paths") or []
    target_handle = str((target or {}).get("target_handle") or "").lstrip("@")
    if not paths or not target_handle:
        return {
            "state": "not_evaluable",
            "seed_target_interaction_count": 0,
            "root_seed_interaction_count": 0,
            "preferred_connector_handle": "",
            "evidence_urls": [],
            "human_intro_status": "human_intro_unvalidated",
        }

    path_signals: list[dict[str, Any]] = []
    all_observations: list[dict[str, Any]] = []
    for path_index, path in enumerate(paths):
        connector = str(path.get("connector_handle") or "").lstrip("@")
        if not connector:
            continue
        root_to_seed = index.get(("mangolabs_", connector.lower()), [])
        seed_to_root = index.get((connector.lower(), "mangolabs_"), [])
        seed_to_target = index.get((connector.lower(), target_handle.lower()), [])
        target_to_seed = index.get((target_handle.lower(), connector.lower()), [])
        observations = [*root_to_seed, *seed_to_root, *seed_to_target, *target_to_seed]
        all_observations.extend(observations)
        path_signals.append({
            "path_index": path_index,
            "connector_handle": f"@{connector}",
            "root_seed_interaction_count": len(root_to_seed) + len(seed_to_root),
            "seed_target_interaction_count": len(seed_to_target) + len(target_to_seed),
            "interaction_types": sorted({str(row.get("interaction_type")) for row in observations}),
            "evidence_urls": unique(str(row.get("tweet_url") or "") for row in observations),
        })

    preferred = max(
        path_signals,
        key=lambda row: (
            int(row["seed_target_interaction_count"]),
            int(row["root_seed_interaction_count"]),
            -int(row["path_index"]),
        ),
        default=None,
    )
    seed_target_count = sum(int(row["seed_target_interaction_count"]) for row in path_signals)
    root_seed_count = sum(int(row["root_seed_interaction_count"]) for row in path_signals)
    state = (
        "seed_target_public_interaction_observed"
        if seed_target_count
        else "root_seed_public_interaction_observed"
        if root_seed_count
        else "follow_path_only_no_interaction_observed_in_bounded_sample"
    )
    return {
        "state": state,
        "seed_target_interaction_count": seed_target_count,
        "root_seed_interaction_count": root_seed_count,
        "preferred_connector_handle": (preferred or {}).get("connector_handle", ""),
        "preferred_path_index": (preferred or {}).get("path_index"),
        "interaction_types": sorted({str(row.get("interaction_type")) for row in all_observations}),
        "evidence_urls": unique(str(row.get("tweet_url") or "") for row in all_observations),
        "path_signals": path_signals,
        "sample_limitation": "Positive hits prove public interactions only. Zero hits do not prove no interaction. Intro willingness remains unvalidated.",
        "human_intro_status": "human_intro_unvalidated",
    }


def sponsor_index(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        grouped[normalize_name(str(row.get("brand") or ""))].append(row)
    result: dict[str, dict[str, Any]] = {}
    for key, rows in grouped.items():
        paid = [row for row in rows if row.get("disclosure_type") == "paid_sponsorship"]
        affiliate = [row for row in rows if row.get("disclosure_type") == "affiliate"]
        mentions = [row for row in rows if row.get("disclosure_type") not in {"paid_sponsorship", "affiliate"}]
        paid_creators = Counter(normalize_name(str(row.get("creator_handle_or_channel") or row.get("creator") or "")) for row in paid)
        result[key] = {
            "observation_count": len(rows),
            "explicit_paid_count": len(paid),
            "affiliate_count": len(affiliate),
            "mention_only_count": len(mentions),
            "unique_paid_creator_count": len(paid_creators),
            "repeat_paid_creator_count": sum(count >= 2 for count in paid_creators.values()),
            "signal": (
                "repeat_paid_creator_and_multi_creator_activity"
                if any(count >= 2 for count in paid_creators.values()) and len(paid_creators) >= 2
                else "multi_creator_explicit_paid_activity"
                if len(paid_creators) >= 2
                else "single_creator_explicit_paid_activity"
                if paid
                else "affiliate_only"
                if affiliate
                else "mention_only"
            ),
            "evidence_urls": unique(str(row.get("content_url") or "") for row in rows),
            "last_verified_at": max((str(row.get("extracted_at") or row.get("published_at") or "") for row in rows), default=""),
            "interpretation_rule": "Only explicit paid_sponsorship disclosures count as paid. Affiliate and mention records are never promoted.",
        }
    return result


def legacy_evidence_context(
    project: dict[str, Any] | None,
    evidence_by_id: dict[str, dict[str, Any]],
    expansion: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Carry auditable legacy evidence without importing LinkedIn-derived routes.

    Legacy v3 scores remain unmapped. This helper only makes the underlying
    public signals and URLs visible in the action row; it does not upgrade them
    to a current budget-authority confirmation.
    """
    project = project or {}
    expansion = expansion or {}
    evidence = []
    for evidence_id in project.get("evidence_ids") or []:
        row = evidence_by_id.get(str(evidence_id))
        if not row:
            continue
        source_type = str(row.get("source_type") or "").lower()
        source_url = str(row.get("source_url") or "")
        if source_type == "linkedin" or "linkedin.com" in source_url.lower():
            continue
        evidence.append(row)
    signals = unique([
        *(str(value) for value in project.get("budget_signals") or []),
        *(str(value) for value in expansion.get("programs") or []),
    ])
    budget_summary = (
        "Legacy public signals requiring current revalidation: "
        + "; ".join(signals)
        + ". Financing/capacity alone does not prove a discretionary marketing budget."
        if signals
        else "Legacy spend mechanism is unmapped and requires current evidence revalidation."
    )
    verified_dates = unique([
        project.get("last_verified_date"),
        *(row.get("verified_date") for row in evidence),
    ])
    return {
        "budget_summary": budget_summary,
        "evidence_urls": unique([
            *(str(row.get("source_url") or "") for row in evidence),
            *(str(value) for value in expansion.get("sources") or [] if "linkedin.com" not in str(value).lower()),
        ]),
        "evidence_claims": unique([
            *(str(row.get("claim") or "") for row in evidence),
            *(str(value) for value in expansion.get("programs") or []),
        ]),
        "last_verified_at": max((str(value) for value in verified_dates), default="2026-08-24"),
    }


def graph_adjust_candidate(
    candidate: dict[str, Any],
    path: dict[str, Any],
    interactions: dict[tuple[str, str], list[dict[str, Any]]],
    config: dict[str, Any],
    mango_target: dict[str, Any] | None = None,
    mango_interactions: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    row = deepcopy(candidate)
    confirmed = path.get("technical_x_identity_status") in CONFIRMED_TECHNICAL_IDENTITY_STATUSES
    reachable = path.get("graph_reachable") is True
    if reachable:
        reachability_level = {
            "direct": "x_direct_company_follow",
            "secondary": "x_secondary_company_follow",
            "third": "x_third_routable",
        }.get(str(path.get("x_degree") or ""), "x_third_routable")
    elif row.get("buyer_or_route"):
        reachability_level = "public_business_route"
    else:
        reachability_level = "unresolved"
    penalties = [penalty for penalty in row.get("penalties", []) if penalty != "identity_unresolved"]
    if not confirmed:
        penalties.append("identity_unresolved")
    row.update(
        {
            "pre_graph_rank": candidate.get("rank"),
            "pre_graph_overall_priority": candidate.get("overall_priority"),
            "reachability_level": reachability_level,
            "penalties": unique(penalties),
            "x_handle_resolution_status": "confirmed_exact_rapid_x" if confirmed else "identity_unconfirmed",
            "technical_x_identity_status": path.get("technical_x_identity_status"),
            "technical_identity_confirmed": confirmed,
            "graph_reachable": path.get("graph_reachable"),
            "graph_reachability_status": path.get("graph_reachability_status"),
            "x_relationship_level": path.get("x_relationship_level"),
            "x_degree": path.get("x_degree"),
            "x_primary_path": path.get("primary_path_labels") or [],
            "x_primary_edge_directions": path.get("primary_edge_directions") or [],
            "x_primary_path_strength": path.get("primary_path_strength"),
            "x_routable_path_count": path.get("routable_path_count", 0),
            "x_path_alternatives_retained": path.get("alternative_paths_retained_count", 0),
            "x_shared_interest_false_paths_rejected": path.get("rejected_shared_interest_v_count", 0),
            "human_intro_status": path.get("human_intro_status", "human_intro_unvalidated"),
            "connector_interaction_signal": connector_interaction_signal(path, interactions),
            "mango_graph_reachable": (mango_target or {}).get("graph_reachable"),
            "mango_x_degree": (mango_target or {}).get("x_degree"),
            "mango_secondary_paths": (mango_target or {}).get("mango_secondary_paths") or [],
            "mango_interaction_signal": mango_interaction_signal(mango_target, mango_interactions or {}),
            "mango_human_intro_status": (mango_target or {}).get("human_intro_status", "not_evaluated"),
            "graph_score_note": "Structural X reachability is scored; public interaction evidence is shown separately and does not imply willingness to introduce.",
        }
    )
    rescored = score_candidate(row, config)
    rescored["score_method"] = "v4_graph_adjusted_evidence_routing_no_fame_or_funding_points"
    rescored["score_comparison_group"] = "v4_new_candidates_graph_adjusted"
    return rescored


def operator_fields(operator: dict[str, Any] | None) -> dict[str, Any]:
    if not operator:
        return {
            "recommended_operator_name": "",
            "recommended_operator_role": "Partnerships / Creator Growth owner",
            "recommended_operator_status": "role_to_find",
            "recommended_operator_x_handle": "",
            "recommended_operator_x_rest_id": "",
            "operator_acquisition_next_step": "Find the current operator through the company's official route, then validate with an official/company source and Rapid X exact profile.",
            "official_candidate_operator": None,
            "company_account_relationship": {},
            "operator_person_relationship": {},
            "budget_authority_confirmed": False,
            "public_business_route": {},
            "opening_signal": "",
            "mango_offer_angle": "",
            "outreach_mode": "",
            "fallback_route": "",
            "operator_unknowns": ["Named current operator and budget authority are not yet verified."],
            "operator_evidence_urls": [],
            "last_verified_at": "",
        }
    recommended = operator.get("recommended_operator") or {}
    public_route = operator.get("public_business_route") or {}
    return {
        "recommended_operator_name": recommended.get("name") or "",
        "recommended_operator_role": recommended.get("role") or "",
        "recommended_operator_status": recommended.get("status") or "role_to_find",
        "recommended_operator_x_handle": recommended.get("x_handle") or "",
        "recommended_operator_x_rest_id": recommended.get("x_rest_id") or "",
        "operator_acquisition_next_step": recommended.get("acquisition_next_step") or operator.get("operator_acquisition_next_step") or "",
        "official_candidate_operator": operator.get("official_candidate_operator"),
        "company_account_relationship": operator.get("company_account_relationship") or {},
        "operator_person_relationship": operator.get("operator_person_relationship") or {},
        "budget_authority_confirmed": False,
        "public_business_route": public_route,
        "opening_signal": operator.get("opening_signal") or "",
        "mango_offer_angle": operator.get("mango_offer_angle") or "",
        "outreach_mode": operator.get("outreach_mode") or "",
        "fallback_route": operator.get("fallback_route") or "",
        "operator_unknowns": operator.get("unknowns") or [],
        "operator_evidence_urls": unique(str(item.get("url") or "") for item in operator.get("operator_evidence") or []),
        "last_verified_at": operator.get("last_verified_at") or "",
    }


def apply_operator_route(action: dict[str, Any], operator: dict[str, Any]) -> dict[str, Any]:
    """Apply the same strict operator route to every queue source group."""
    fields = operator_fields(operator)
    action.update({
        "recommended_operator_name": fields["recommended_operator_name"],
        "recommended_operator_role": fields["recommended_operator_role"],
        "recommended_operator_status": fields["recommended_operator_status"],
        "recommended_operator_x_handle": fields["recommended_operator_x_handle"],
        "recommended_operator_x_rest_id": fields["recommended_operator_x_rest_id"],
        "operator_acquisition_next_step": fields["operator_acquisition_next_step"],
        "official_candidate_operator": fields["official_candidate_operator"],
        "company_account_relationship": fields["company_account_relationship"],
        "operator_person_relationship": fields["operator_person_relationship"],
        "budget_authority_confirmed": False,
        "operator_evidence_urls": fields["operator_evidence_urls"],
    })
    action["evidence_urls"] = unique([
        *(action.get("evidence_urls") or []), *fields["operator_evidence_urls"],
    ])
    action["unknowns"] = unique([
        *(action.get("unknowns") or []), *fields["operator_unknowns"],
    ])
    if fields["public_business_route"]:
        action["public_business_route"] = fields["public_business_route"]
    if fields["opening_signal"]:
        action["opening_signal"] = fields["opening_signal"]
    if fields["mango_offer_angle"]:
        action["mango_wedge"] = fields["mango_offer_angle"]
    if fields["fallback_route"]:
        action["fallback"] = fields["fallback_route"]
    return action


def action_band(row: dict[str, Any], has_operator_research: bool) -> tuple[int, str]:
    if row.get("graph_reachable") is True and row.get("spend_evidence_level") == "L3":
        return 1, "validate_warm_connector_and_pitch"
    if row.get("graph_reachable") is True:
        return 2, "validate_warm_connector_then_route"
    if has_operator_research and row.get("spend_evidence_level") == "L3":
        return 1, "use_confirmed_public_program_route"
    if has_operator_research:
        return 2, "use_public_route_and_find_budget_owner"
    return 3, "research_or_watch"


def next_action(row: dict[str, Any], operator: dict[str, Any]) -> str:
    path = row.get("x_primary_path") or []
    if row.get("graph_reachable") is True and row.get("x_degree") == "direct" and len(path) == 2:
        target = path[-1]
        fallback = (operator.get("public_business_route") or {}).get("value") or row.get("buyer_or_route") or "official company route"
        return (
            f"Solomon 以已观察到的 direct X follow/公开互动为上下文直接联系 {target}，先询问当前 Partnerships / Growth owner；"
            f"不要把公司账号邻接当成人际关系。若 3 个工作日无响应，转走 {fallback}。"
        )
    if row.get("graph_reachable") is True and len(path) >= 3:
        connector = connector_interaction_signal_from_row(row).get("connector_handle") or path[1]
        target = row.get("x_handle_candidate") or path[-1]
        fallback = (operator.get("public_business_route") or {}).get("value") or row.get("buyer_or_route") or "official company route"
        return (
            f"Solomon 先向 {connector} 核实其与 {target} 的 person-level 关系、最近互动及是否愿意做 contextual intro；"
            f"若只是账号关注，则同日改走 {fallback}，不继续假设暖介绍。"
        )
    if operator.get("outreach_mode"):
        return str(operator["outreach_mode"])
    route = row.get("buyer_or_route") or "official company contact route"
    return f"向 {route} 提交一页可量化 pilot，并要求转给 Partnerships / Creator Growth 的实际 owner。"


def connector_interaction_signal_from_row(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("connector_interaction_signal")
    return value if isinstance(value, dict) else {}


def evidence_contract_fields(
    *,
    row: dict[str, Any],
    operator: dict[str, Any] | None = None,
    sponsor: dict[str, Any] | None = None,
    mango_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    types = ["public_web"]
    if row.get("graph_reachable") is True or row.get("mango_graph_reachable") is True:
        types.append("rapid_x_follow_graph")
    solomon_signal = connector_interaction_signal_from_row(row)
    if "interaction_observed" in str(solomon_signal.get("state") or ""):
        types.append("rapid_x_public_interaction")
    if "interaction_observed" in str((mango_signal or {}).get("state") or ""):
        types.append("rapid_x_public_interaction")
    if (sponsor or {}).get("observation_count"):
        types.append("youtube_creator_disclosure")
    if operator:
        types.append("official_operator_or_program_source")
    dates = unique([
        row.get("last_verified_at"),
        (operator or {}).get("last_verified_at"),
        (sponsor or {}).get("last_verified_at"),
    ])
    return {
        "last_verified_at": max((str(value) for value in dates), default=date.today().isoformat()),
        # Spend freshness must not move forward merely because an unrelated
        # operator/profile or sponsorship record was refreshed.
        "spend_last_verified_at": (
            row.get("spend_last_verified_at") or row.get("last_verified_at") or ""
        ),
        "evidence_types": unique(types),
        "fact_status_rollup": [
            "confirmed_public_evidence_where_cited",
            "unverified_human_intro_willingness",
            "unverified_budget_authority",
        ],
        "recommendation_confidence": "medium_requires_operator_and_intro_validation",
    }


def new_action_row(
    row: dict[str, Any],
    operator: dict[str, Any] | None,
    sponsor: dict[str, Any] | None,
) -> dict[str, Any]:
    fields = operator_fields(operator)
    mango_signal = row.get("mango_interaction_signal") or {}
    contract = evidence_contract_fields(row=row, operator=operator, sponsor=sponsor, mango_signal=mango_signal)
    wave, band = action_band(row, operator is not None)
    public_route = fields["public_business_route"] or {
        "type": "candidate_public_route",
        "value": row.get("buyer_or_route") or "",
        "source_url": "",
    }
    sources = unique([
        *(str(value) for value in row.get("source_urls") or []),
        *fields["operator_evidence_urls"],
        *connector_interaction_signal_from_row(row).get("evidence_urls", []),
        *((row.get("mango_interaction_signal") or {}).get("evidence_urls") or []),
        *((sponsor or {}).get("evidence_urls") or []),
    ])
    return {
        "execution_wave": wave,
        "action_band": band,
        "source_group": "v4_new_graph_adjusted",
        "within_group_rank": row.get("graph_adjusted_rank"),
        "global_rank": None,
        "company": row["company"],
        "owner": "Solomon" if row.get("graph_reachable") is True else "Mango BD",
        "why_now": row.get("why_now") or "",
        "spend_evidence_level": row.get("spend_evidence_level"),
        "spend_mechanism_level": row.get("spend_mechanism_level"),
        "budget_evidence": row.get("budget_evidence") or "",
        "sponsor_signal": sponsor or {
            "signal": "not_observed_in_bounded_youtube_sample",
            "interpretation_rule": "Sampling absence is not evidence that the brand never sponsors creators.",
        },
        "recommended_operator_name": fields["recommended_operator_name"],
        "recommended_operator_role": fields["recommended_operator_role"],
        "recommended_operator_status": fields["recommended_operator_status"],
        "budget_authority_confirmed": False,
        "public_business_route": public_route,
        "x_degree": row.get("x_degree"),
        "graph_reachable": row.get("graph_reachable"),
        "primary_path": row.get("x_primary_path") or [],
        "primary_edge_directions": row.get("x_primary_edge_directions") or [],
        "connector_interaction_signal": connector_interaction_signal_from_row(row),
        "mango_graph_reachable": row.get("mango_graph_reachable"),
        "mango_secondary_paths": row.get("mango_secondary_paths") or [],
        "mango_interaction_signal": row.get("mango_interaction_signal") or {},
        "human_intro_status": "human_intro_unvalidated",
        "opening_signal": fields["opening_signal"] or row.get("why_now") or "",
        "mango_wedge": fields["mango_offer_angle"] or row.get("collaboration_angle") or "A small, attributable creator/partner pilot tied to qualified activations rather than impressions alone.",
        "primary_next_action": next_action(row, fields),
        "fallback": fields["fallback_route"] or f"Use {public_route.get('value') or 'the official route'} and request routing to the current owner.",
        "success_condition": "Named current budget-adjacent operator plus either connector-confirmed intro willingness or acknowledgment through the official intake route.",
        "validation_questions": [
            "Who is the current person-level owner?",
            "What spend mechanism is actually available: paid brief, affiliate, in-kind, or no budget?",
            "If using the X connector, when did they last interact and will they make this specific introduction?",
        ],
        "score_value": row.get("overall_priority"),
        "score_method": row.get("score_method"),
        "score_comparable_across_groups": False,
        "unknowns": unique([*fields["operator_unknowns"], str(row.get("risk") or "")]),
        "evidence_urls": sources,
        **contract,
    }


def legacy_action_row(
    row: dict[str, Any],
    ranking: dict[str, Any] | None = None,
    mango_target: dict[str, Any] | None = None,
    mango_signal: dict[str, Any] | None = None,
    legacy_context: dict[str, Any] | None = None,
    sponsor: dict[str, Any] | None = None,
    legacy_primary_path: dict[str, Any] | None = None,
) -> dict[str, Any]:
    signal = mango_signal or {"state": "not_evaluable"}
    legacy_context = legacy_context or {}
    direction = legacy_direction_backfill(legacy_primary_path)
    row_for_contract = {
        **row,
        "graph_reachable": True,
        "mango_graph_reachable": (mango_target or {}).get("graph_reachable"),
        "last_verified_at": legacy_context.get("last_verified_at") or "2026-08-24",
        "spend_last_verified_at": legacy_context.get("last_verified_at") or "2026-08-24",
    }
    return {
        "execution_wave": 1,
        "action_band": "preserved_v3_warm_path_validation",
        "source_group": "v3_existing_solomon_x",
        "within_group_rank": row.get("rank"),
        "global_rank": None,
        "company": row.get("company"),
        "owner": row.get("owner", "Solomon"),
        "why_now": "Preserved from the prior high-priority Solomon-X queue; v3 and v4 numeric scores are not comparable.",
        "spend_evidence_level": "legacy_unmapped",
        "spend_mechanism_level": "legacy_budget_score_preserved",
        "budget_evidence": legacy_context.get("budget_summary") or "Legacy spend mechanism is unmapped and requires current evidence revalidation.",
        "sponsor_signal": sponsor or {"signal": "not_observed_in_bounded_youtube_sample"},
        "recommended_operator_name": "",
        "recommended_operator_role": "Growth / Partnerships / Creator operator to validate",
        "recommended_operator_status": "role_to_find_or_validate",
        "budget_authority_confirmed": False,
        "public_business_route": {"type": "v3_fallback", "value": row.get("fallback") or ""},
        "x_degree": row.get("x_degree"),
        "graph_reachable": True,
        "primary_path": (legacy_primary_path or {}).get("path_labels") or row.get("primary_path") or "",
        **direction,
        "connector_interaction_signal": {"state": "not_reaudited_in_this_v4_interaction_sample"},
        "mango_graph_reachable": (mango_target or {}).get("graph_reachable"),
        "mango_secondary_paths": (mango_target or {}).get("mango_secondary_paths") or [],
        "mango_interaction_signal": signal,
        "human_intro_status": "human_intro_unvalidated",
        "opening_signal": row.get("first_step") or "",
        "mango_wedge": row.get("mango_wedge") or "",
        "primary_next_action": row.get("first_step") or "",
        "fallback": row.get("fallback") or "",
        "success_condition": row.get("success_condition") or "",
        "validation_questions": row.get("validation_questions") or [],
        "score_value": (ranking or {}).get("overall_priority_solomon_x"),
        "score_method": "v3_action_priority_preserved_without_numeric_merge",
        "score_comparable_across_groups": False,
        "unknowns": ["Current operator, spend mechanism, and connector willingness require validation."],
        "evidence_urls": unique([
            *(legacy_context.get("evidence_urls") or []),
            *((mango_signal or {}).get("evidence_urls") or []),
            *((sponsor or {}).get("evidence_urls") or []),
        ]),
        **evidence_contract_fields(row=row_for_contract, sponsor=sponsor, mango_signal=signal),
    }


def mango_only_legacy_action_row(
    target: dict[str, Any],
    ranking: dict[str, Any],
    mango_signal: dict[str, Any] | None = None,
    legacy_context: dict[str, Any] | None = None,
    sponsor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paths = target.get("mango_secondary_paths") or []
    preferred_index = (mango_signal or {}).get("preferred_path_index")
    primary = paths[preferred_index] if isinstance(preferred_index, int) and preferred_index < len(paths) else paths[0]
    connector = f"@{primary.get('connector_handle')}"
    target_handle = f"@{target.get('target_handle')}"
    signal = mango_signal or {"state": "not_evaluable"}
    legacy_context = legacy_context or {}
    row_for_contract = {
        "graph_reachable": True,
        "mango_graph_reachable": True,
        "last_verified_at": max([
            *(str(path.get("last_verified_at") or "") for path in paths),
            str(legacy_context.get("last_verified_at") or "2026-08-24"),
        ]),
        "spend_last_verified_at": legacy_context.get("last_verified_at") or "2026-08-24",
    }
    return {
        "execution_wave": 2,
        "action_band": "mango_seed_warm_path_validation",
        "source_group": "v3_existing_mango_seed_graph",
        "within_group_rank": ranking.get("rank"),
        "global_rank": None,
        "company": target.get("company"),
        "owner": f"Mango BD（先核实 connector {connector}）",
        "why_now": "The v4 Mango seed audit found a positive two-hop X path that was not carried into the five-row v3 action queue.",
        "spend_evidence_level": "legacy_unmapped",
        "spend_mechanism_level": "legacy_budget_score_preserved",
        "budget_evidence": legacy_context.get("budget_summary") or "Legacy spend mechanism is unmapped; the X path alone is not budget evidence.",
        "sponsor_signal": sponsor or {"signal": "not_observed_in_bounded_youtube_sample"},
        "recommended_operator_name": "",
        "recommended_operator_role": "Growth / Partnerships / Creator operator to validate",
        "recommended_operator_status": "role_to_find_or_validate",
        "budget_authority_confirmed": False,
        "public_business_route": {"type": "requires_route_revalidation", "value": ""},
        "x_degree": "secondary",
        "graph_reachable": True,
        "primary_path": primary.get("path_labels") or [],
        "primary_edge_directions": primary.get("edge_directions") or [],
        "connector_interaction_signal": {"state": "not_applicable_use_mango_interaction_signal"},
        "mango_graph_reachable": True,
        "mango_secondary_paths": paths,
        "mango_interaction_signal": signal,
        "human_intro_status": "human_intro_unvalidated",
        "opening_signal": f"Use the observed {connector} → {target_handle} follow edge only as a reason to ask a relationship-validation question.",
        "mango_wedge": "Prepare a small attributable pilot only after a current budget-adjacent operator is identified.",
        "primary_next_action": f"Ask {connector} whether they know a person at {target_handle}, when they last interacted, and whether this specific introduction is appropriate; a brand-account follow alone is insufficient.",
        "fallback": "If the connector cannot validate a person-level relationship, find and use the company's current official partnerships or creator route.",
        "success_condition": "Named current operator plus connector-confirmed contextual introduction willingness or a verified official intake route.",
        "validation_questions": [
            "Is the edge only an account follow, or is there a person-level relationship?",
            "When was the last real interaction?",
            "Who controls the relevant creator/partner spend mechanism?",
        ],
        "score_value": ranking.get("overall_priority_solomon_x"),
        "score_method": "v3_score_preserved_with_v4_mango_seed_path",
        "score_comparable_across_groups": False,
        "unknowns": ["Current operator, spend mechanism, public route, and connector willingness require validation."],
        "evidence_urls": unique([
            *(legacy_context.get("evidence_urls") or []),
            *((mango_signal or {}).get("evidence_urls") or []),
            *((sponsor or {}).get("evidence_urls") or []),
        ]),
        **evidence_contract_fields(row=row_for_contract, sponsor=sponsor, mango_signal=signal),
    }


def select_new_actions(rows: list[dict[str, Any]], operator_companies: set[str], top_n: int = 12) -> list[dict[str, Any]]:
    selected = {
        normalize_name(row["company"])
        for row in rows
        if int(row.get("pre_graph_rank") or 10_000) <= top_n or row.get("graph_reachable") is True
    }
    selected.update(operator_companies)
    return [row for row in rows if normalize_name(row["company"]) in selected]


def main() -> None:
    config = load_json(CONFIG)
    candidates = load_json(NEW_RANKINGS)
    paths = {normalize_name(row["company"]): row for row in load_json(X_PATHS)}
    interaction_artifact = load_json(X_INTERACTIONS, {"observations": []})
    interactions = interaction_index(interaction_artifact)
    mango_interactions = interaction_index(load_json(MANGO_INTERACTIONS, {"observations": []}))
    operators_raw = load_json(OPERATORS, [])
    operators = {normalize_name(row["company"]): row for row in operators_raw}
    sponsors = sponsor_index(load_json(SPONSORS, []))
    mango_artifact = load_json(MANGO_PATHS, {"targets": [], "summary": {}})
    mango_targets = {normalize_name(row["company"]): row for row in mango_artifact.get("targets") or []}
    v3_rankings = {normalize_name(row["company"]): row for row in load_json(V3_RANKINGS, [])}
    legacy_projects = {normalize_name(row["company"]): row for row in load_json(LEGACY_PROJECTS, [])}
    legacy_evidence = {str(row["id"]): row for row in load_json(LEGACY_EVIDENCE, [])}
    legacy_expansion = {normalize_name(row["company"]): row for row in load_json(LEGACY_EXPANSION, [])}
    v3_primary_paths = {
        normalize_name(row["company"]): row
        for row in load_json(V3_PATHS, [])
        if row.get("primary") is True
    }

    adjusted = []
    for candidate in candidates:
        key = normalize_name(candidate["company"])
        if key not in paths:
            raise ValueError(f"Missing X path audit for {candidate['company']}")
        row = graph_adjust_candidate(candidate, paths[key], interactions, config, mango_targets.get(key), mango_interactions)
        row["sponsor_signal"] = sponsors.get(key, {
            "signal": "not_observed_in_bounded_youtube_sample",
            "interpretation_rule": "Sampling absence is not evidence that the brand never sponsors creators.",
        })
        adjusted.append(row)
    adjusted.sort(key=lambda row: (-float(row["overall_priority"]), row["company"].lower()))
    for rank, row in enumerate(adjusted, 1):
        row["graph_adjusted_rank"] = rank

    selected = select_new_actions(adjusted, set(operators))
    old_actions = load_json(OLD_ACTIONS, [])
    old_action_keys = {normalize_name(row["company"]) for row in old_actions}
    queue = [
        *(new_action_row(row, operators.get(normalize_name(row["company"])), sponsors.get(normalize_name(row["company"]))) for row in selected),
        *(
            legacy_action_row(
                row,
                v3_rankings.get(normalize_name(row["company"])),
                mango_targets.get(normalize_name(row["company"])),
                mango_interaction_signal(mango_targets.get(normalize_name(row["company"])), mango_interactions),
                legacy_evidence_context(
                    legacy_projects.get(normalize_name(row["company"])),
                    legacy_evidence,
                    legacy_expansion.get(normalize_name(row["company"])),
                ),
                sponsors.get(normalize_name(row["company"])),
                v3_primary_paths.get(normalize_name(row["company"])),
            )
            for row in old_actions
        ),
        *(
            mango_only_legacy_action_row(
                target,
                v3_rankings[key],
                mango_interaction_signal(target, mango_interactions),
                legacy_evidence_context(legacy_projects.get(key), legacy_evidence, legacy_expansion.get(key)),
                sponsors.get(key),
            )
            for key, target in mango_targets.items()
            if target.get("graph_reachable") is True
            and key in v3_rankings
            and key not in old_action_keys
        ),
    ]
    queue_keys = {normalize_name(row["company"]) for row in queue}
    missing_operator_routes = sorted(queue_keys - set(operators))
    extra_operator_routes = sorted(set(operators) - queue_keys)
    if missing_operator_routes or extra_operator_routes:
        raise ValueError(
            f"Operator routes must exactly cover the 42-company queue; missing={missing_operator_routes}, extra={extra_operator_routes}"
        )
    queue = [apply_operator_route(row, operators[normalize_name(row["company"])]) for row in queue]
    queue.sort(key=lambda row: (int(row["execution_wave"]), row["source_group"], int(row.get("within_group_rank") or 10_000), row["company"].lower()))

    adjusted_fields = [
        "graph_adjusted_rank", "pre_graph_rank", "company", "overall_priority", "pre_graph_overall_priority",
        "priority_tier", "spend_evidence_level", "spend_mechanism_level", "reachability_level",
        "reachability_score", "technical_x_identity_status", "graph_reachable", "x_relationship_level",
        "x_degree", "x_primary_path", "x_primary_edge_directions", "connector_interaction_signal",
        "mango_graph_reachable", "mango_x_degree", "mango_secondary_paths", "mango_interaction_signal", "human_intro_status",
        "buyer_or_route", "why_now", "budget_evidence", "sponsor_signal",
        "source_urls", "score_method", "score_comparison_group", "score_comparable_across_groups",
    ]
    queue_fields = [
        "execution_wave", "action_band", "source_group", "within_group_rank", "global_rank", "company",
        "owner", "why_now", "spend_evidence_level", "spend_mechanism_level", "budget_evidence",
        "sponsor_signal", "recommended_operator_name", "recommended_operator_role",
        "recommended_operator_status", "recommended_operator_x_handle", "recommended_operator_x_rest_id",
        "operator_acquisition_next_step", "official_candidate_operator", "budget_authority_confirmed",
        "public_business_route", "company_account_relationship", "operator_person_relationship", "x_degree",
        "graph_reachable", "primary_path", "primary_edge_kinds", "primary_edge_directions",
        "primary_edge_rest_id_directions", "direction_evidence_files", "direction_data_unavailable",
        "direction_data_reason", "connector_interaction_signal",
        "mango_graph_reachable", "mango_secondary_paths", "mango_interaction_signal", "human_intro_status", "opening_signal",
        "mango_wedge", "primary_next_action", "fallback",
        "success_condition", "validation_questions", "score_value", "score_method",
        "score_comparable_across_groups", "unknowns", "evidence_urls",
        "operator_evidence_urls",
        "last_verified_at", "spend_last_verified_at", "evidence_types", "fact_status_rollup", "recommendation_confidence",
    ]
    write_json(ADJUSTED_JSON, adjusted)
    write_csv(ADJUSTED_CSV, adjusted, adjusted_fields)
    write_json(QUEUE_JSON, queue)
    write_csv(QUEUE_CSV, queue, queue_fields)

    summary = {
        "schema_version": "v4",
        "generated_date": date.today().isoformat(),
        "new_candidates_graph_adjusted": len(adjusted),
        "queue_actions": len(queue),
        "new_actions": sum(row["source_group"] == "v4_new_graph_adjusted" for row in queue),
        "preserved_v3_actions": sum(row["source_group"] == "v3_existing_solomon_x" for row in queue),
        "additional_v3_mango_seed_actions": sum(row["source_group"] == "v3_existing_mango_seed_graph" for row in queue),
        "complete_action_records": sum(
            all(str(row.get(field) or "").strip() for field in ("owner", "why_now", "budget_evidence", "opening_signal", "primary_next_action", "fallback", "success_condition"))
            and bool(row.get("validation_questions"))
            for row in queue
        ),
        "legacy_actions_with_embedded_public_evidence": sum(
            str(row.get("source_group") or "").startswith("v3_existing")
            and bool(row.get("evidence_urls"))
            for row in queue
        ),
        "budget_authority_confirmed": sum(row.get("budget_authority_confirmed") is True for row in queue),
        "named_operator_company_count": sum(bool(row.get("recommended_operator_name")) for row in queue),
        "unresolved_operator_with_specific_acquisition_path_count": sum(
            not row.get("recommended_operator_name") and bool(row.get("operator_acquisition_next_step"))
            for row in queue
        ),
        "solomon_operator_person_reachable_count": sum(
            ((row.get("operator_person_relationship") or {}).get("solomon") or {}).get("graph_reachable") is True
            for row in queue
        ),
        "mango_operator_person_reachable_count": sum(
            ((row.get("operator_person_relationship") or {}).get("mango") or {}).get("graph_reachable") is True
            for row in queue
        ),
        "human_intro_validated": sum(row.get("human_intro_status") == "human_intro_validated" for row in queue),
        "graph_reachable_new_candidates": sum(row.get("graph_reachable") is True for row in adjusted),
        "mango_graph_reachable_companies": (mango_artifact.get("summary") or {}).get("companies_graph_reachable_from_mango", 0),
        "mango_direct_target_paths": (mango_artifact.get("summary") or {}).get("mango_direct_path_count", 0),
        "interaction_signal_counts": dict(sorted(Counter(connector_interaction_signal_from_row(row)["state"] for row in adjusted if row.get("graph_reachable") is True).items())),
        "mango_interaction_signal_counts": dict(sorted(Counter((row.get("mango_interaction_signal") or {}).get("state", "not_evaluable") for row in queue if row.get("mango_graph_reachable") is True).items())),
        "queue_wave_counts": dict(sorted(Counter(str(row["execution_wave"]) for row in queue).items())),
        "score_comparability_rule": "v3 preserved actions and v4 graph-adjusted scores are separate groups. No global numeric rank is created.",
        "budget_rule": "Funding, fame, followers, token value, and TVL add zero points. Affiliate and mention observations do not become paid sponsorship.",
        "relationship_rule": "Graph reachability, public interaction, and human introduction willingness are separate states.",
        "outputs": [
            str(ADJUSTED_JSON.relative_to(ROOT)), str(ADJUSTED_CSV.relative_to(ROOT)),
            str(QUEUE_JSON.relative_to(ROOT)), str(QUEUE_CSV.relative_to(ROOT)),
        ],
    }
    write_json(SUMMARY_JSON, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
