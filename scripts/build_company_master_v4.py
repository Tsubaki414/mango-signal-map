#!/usr/bin/env python3
"""Build the flat, sortable 77-company Mango BD decision table.

The normalized SQLite database remains the canonical join layer. This export is
the operator-facing projection requested in the original brief: one row per
company with spend, GTM, operator, sponsorship, and reachability fields kept
separate and traceable.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "pilot_v4"
DATA = ROOT / "data" / "pilot_v4"

MASTER_JSON = OUT / "company_master_v4.json"
MASTER_CSV = OUT / "company_master_v4.csv"
SUMMARY_JSON = OUT / "company_master_summary_v4.json"

# Explicit, adjudicated aliases shared with the normalized graph/database layer.
# Keep this conservative: do not infer aliases from fuzzy string similarity.
KNOWN_COMPANY_ALIASES = {"viggle": "viggleai"}


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def unique(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value in (None, "", [], {}):
            continue
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def rows_from_gtm(payload: Any) -> list[dict[str, Any]]:
    return (payload.get("case_studies") or []) if isinstance(payload, dict) else (payload or [])


def listify(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def non_linkedin_routes(values: Iterable[Any]) -> list[Any]:
    """Exclude legacy LinkedIn routes after the user's explicit scope correction."""
    return [value for value in values if "linkedin" not in str(value).lower()]


def budget_confidence(row: dict[str, Any], legacy: dict[str, Any] | None) -> str:
    if legacy and legacy.get("budget_confidence"):
        inherited = re.sub(r"[^a-z0-9]+", "_", str(legacy["budget_confidence"]).lower()).strip("_")
        if inherited.startswith("legacy_unrevalidated_"):
            return inherited
        return f"legacy_unrevalidated_{inherited}"
    level = row.get("spend_evidence_level")
    official_count = int(row.get("official_source_count") or 0)
    if level == "L3" and official_count:
        return "high_confirmed_spend_mechanism"
    if level == "L3":
        return "medium_explicit_spend_claim"
    if level == "L2":
        return "medium_formal_program_or_repeat_signal"
    if level == "L1":
        return "low_capacity_or_unverified_lead"
    return "legacy_unmapped_requires_revalidation"


def best_action(actions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not actions:
        return None
    return sorted(
        actions,
        key=lambda row: (
            int(row.get("execution_wave") or 99),
            int(row.get("within_group_rank") or 10_000),
            str(row.get("source_group") or ""),
        ),
    )[0]


def build_master() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    universe = load_json(OUT / "all_company_universe_v4.json")
    alias_to_company_key = {
        normalize_name(alias): normalize_name(row["company"])
        for row in universe
        for alias in row.get("aliases") or []
    }
    alias_to_company_key.update(KNOWN_COMPANY_ALIASES)
    adjusted = {normalize_name(row["company"]): row for row in load_json(OUT / "new_candidate_rankings_graph_adjusted_v4.json")}
    solomon_paths = {normalize_name(row["company"]): row for row in load_json(OUT / "solomon_paths_expanded_v4.json")}
    mango_payload = load_json(OUT / "mango_seed_target_paths.json")
    mango_targets = {normalize_name(row["company"]): row for row in mango_payload.get("targets") or []}
    operators = {normalize_name(row["company"]): row for row in load_json(DATA / "top_operator_routes.json", [])}
    gtm_cases = {normalize_name(row["company"]): row for row in rows_from_gtm(load_json(DATA / "gtm_case_studies_v4.json", []))}
    legacy_projects = {normalize_name(row["company"]): row for row in load_json(ROOT / "data" / "pilot" / "projects.json", [])}
    legacy_evidence = {row["id"]: row for row in load_json(ROOT / "data" / "pilot" / "evidence.json", [])}
    expanded_v2 = {normalize_name(row["company"]): row for row in load_json(ROOT / "data" / "pilot_v2" / "expanded_companies.json", [])}

    action_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in load_json(OUT / "priority_action_queue_v4.json", []):
        action_groups[normalize_name(row["company"])].append(row)

    sponsor_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in load_json(OUT / "sponsor_expanded_observations.json", []):
        observed_key = normalize_name(row.get("brand") or "")
        sponsor_groups[alias_to_company_key.get(observed_key, observed_key)].append(row)

    rows: list[dict[str, Any]] = []
    for base in universe:
        key = normalize_name(base["company"])
        ranked = adjusted.get(key, base)
        solomon = solomon_paths.get(key, {})
        mango = mango_targets.get(key, {})
        operator = operators.get(key, {})
        gtm = gtm_cases.get(key, {})
        legacy = legacy_projects.get(key, {})
        legacy_sources = [
            legacy_evidence[evidence_id]
            for evidence_id in legacy.get("evidence_ids") or []
            if evidence_id in legacy_evidence and str(legacy_evidence[evidence_id].get("source_type") or "").lower() != "linkedin"
        ]
        expansion = expanded_v2.get(key, {})
        sponsor_rows = sponsor_groups.get(key, [])
        action = best_action(action_groups.get(key, [])) or {}
        recommended = operator.get("recommended_operator") or {}
        alternate = operator.get("alternate_operator") or {}
        operator_identity = operator.get("operator_identity") or {}
        public_route = operator.get("public_business_route") or {}
        if recommended.get("name") and recommended.get("status") != "confirmed_official_role_and_rapid_x_exact_profile":
            raise ValueError(f"Named operator bypassed strict identity contract: {base['company']}")

        raw = base.get("raw_record") or {}
        programs = unique([
            *listify(ranked.get("programs_or_campaigns")),
            *listify(expansion.get("programs")),
            *(item.get("name") for item in gtm.get("campaigns_programs") or []),
        ])
        marketing_channels = unique([
            *listify(legacy.get("marketing_channels")),
            *listify(gtm.get("channels")),
            *(row.get("platform") for row in sponsor_rows),
        ])
        historical_campaigns = unique([
            *listify(legacy.get("historical_campaigns")),
            *(
                {
                    "title": row.get("content_title"),
                    "platform": row.get("platform"),
                    "published_at": row.get("published_at"),
                    "disclosure_type": row.get("disclosure_type"),
                    "url": row.get("content_url"),
                }
                for row in sponsor_rows
            ),
        ])
        decision_makers = unique([
            {
                "name": recommended.get("name"),
                "role": recommended.get("role"),
                "status": recommended.get("status"),
                "x_handle": recommended.get("x_handle"),
                "x_rest_id": recommended.get("x_rest_id"),
                "identity_evidence_contract": "official/company current-role source + Rapid X exact profile",
                "official_role_evidence_urls": operator_identity.get("official_role_evidence_urls") or [],
                "official_role_evidence_ids": operator_identity.get("official_role_evidence_ids") or [],
                "rapid_x_cache_file": operator_identity.get("rapid_x_cache_file"),
                "rapid_x_request_cache_file": operator_identity.get("rapid_x_request_cache_file"),
                "budget_authority_confirmed": False,
            } if recommended.get("name") else None,
            {
                "name": alternate.get("name"),
                "role": alternate.get("role"),
                "status": alternate.get("status"),
                "budget_authority_confirmed": False,
            } if alternate.get("name") else None,
        ])
        contact_methods = non_linkedin_routes(unique([
            {"type": public_route.get("type"), "value": public_route.get("value"), "source_url": public_route.get("source_url")}
            if public_route.get("value") else None,
            ranked.get("buyer_or_route"),
            *listify(legacy.get("public_contact_methods")),
        ]))

        solomon_path_labels = ranked.get("x_primary_path") or solomon.get("primary_path_labels") or base.get("x_primary_path") or []
        solomon_edge_directions = (
            ranked.get("x_primary_edge_directions")
            or solomon.get("primary_edge_directions")
            or action.get("primary_edge_directions")
            or []
        )
        solomon_edge_kinds = action.get("primary_edge_kinds") or []
        direction_data_unavailable = action.get("direction_data_unavailable")
        if direction_data_unavailable is None:
            direction_data_unavailable = bool(solomon_path_labels) and not bool(solomon_edge_directions)
        direction_data_reason = action.get("direction_data_reason") or (
            "No directed observation is available for the non-empty Solomon path."
            if direction_data_unavailable
            else ""
        )
        mango_paths = mango.get("mango_secondary_paths") or []
        mango_signal = ranked.get("mango_interaction_signal") or action.get("mango_interaction_signal") or {}
        solomon_signal = ranked.get("connector_interaction_signal") or action.get("connector_interaction_signal") or {}

        source_urls = non_linkedin_routes(unique([
            *listify(ranked.get("source_urls")),
            *(item.get("url") for item in operator.get("operator_evidence") or []),
            *(item.get("url") for item in gtm.get("evidence") or []),
            *(row.get("content_url") for row in sponsor_rows),
            *listify(expansion.get("sources")),
            *(item.get("source_url") for item in legacy_sources),
        ]))
        last_verified_candidates = unique([
            operator.get("last_verified_at"),
            gtm.get("last_verified_at"),
            legacy.get("last_verified_date"),
            *(item.get("verified_date") for item in legacy_sources),
            solomon.get("identity_last_verified_at"),
            mango.get("identity_last_verified_at"),
            *(row.get("extracted_at") for row in sponsor_rows),
        ])
        last_verified_at = max((str(value) for value in last_verified_candidates), default="2026-08-25")
        inherited_budget = legacy or expansion
        budget_revalidation_needed = bool(inherited_budget and inherited_budget.get("budget_confidence"))
        spend_verified_candidates = unique([
            action.get("spend_last_verified_at"),
            legacy.get("last_verified_date") if inherited_budget else None,
            *((item.get("verified_date") for item in legacy_sources) if inherited_budget else ()),
            ranked.get("last_verified_at") if not inherited_budget else None,
            raw.get("last_verified_at") if not inherited_budget else None,
        ])
        spend_last_verified_at = max(
            (str(value) for value in spend_verified_candidates),
            default=str(ranked.get("last_verified_at") or legacy.get("last_verified_date") or ""),
        )

        fit_score = ranked.get("fit_score", raw.get("fit_score", expansion.get("fit_score")))
        reachability_score = ranked.get("reachability_score", raw.get("solomon_reachability_score", expansion.get("reachability_score")))
        solomon_reachable = ranked.get("graph_reachable")
        if solomon_reachable is None:
            solomon_reachable = bool(solomon_path_labels) and str(ranked.get("x_degree") or base.get("x_degree")) in {"direct", "secondary", "third"}
        row = {
            "company": base["company"],
            "aliases": base.get("aliases") or [],
            "cohort": base.get("cohort"),
            "score_comparison_group": ranked.get("score_comparison_group"),
            "within_group_rank": ranked.get("graph_adjusted_rank", base.get("cohort_rank")),
            "global_rank": None,
            "score_comparable_across_groups": False,
            "category": ranked.get("category") or legacy.get("category") or expansion.get("category") or "unknown",
            "geography": ranked.get("geography") or legacy.get("geography") or "unknown",
            "stage": legacy.get("stage") or gtm.get("maturity_stage") or "unknown",
            "product_business_model": legacy.get("business_model") or "unknown_not_in_current_evidence",
            "why_now": ranked.get("why_now") or action.get("why_now") or "",
            "spend_evidence_level": ranked.get("spend_evidence_level"),
            "spend_mechanism_level": ranked.get("spend_mechanism_level"),
            "budget_evidence": ranked.get("budget_evidence") or action.get("budget_evidence") or "",
            "budget_confidence": budget_confidence(ranked, inherited_budget),
            "spend_last_verified_at": spend_last_verified_at,
            "revalidation_needed": budget_revalidation_needed,
            "revalidation_reason": (
                "Legacy budget-confidence label was inherited without v4 evidence-contract revalidation."
                if budget_revalidation_needed
                else ""
            ),
            "budget_authority_confirmed": False,
            "declared_programs_or_campaigns": programs,
            "known_marketing_channels": marketing_channels,
            "historical_campaigns": historical_campaigns,
            "sponsor_observation_count": len(sponsor_rows),
            "explicit_paid_sponsor_observation_count": sum(row.get("disclosure_type") == "paid_sponsorship" for row in sponsor_rows),
            "decision_makers": decision_makers,
            "recommended_operator_status": recommended.get("status") or action.get("recommended_operator_status") or "not_researched_current_queue",
            "operator_acquisition_next_step": recommended.get("acquisition_next_step") or operator.get("operator_acquisition_next_step") or action.get("operator_acquisition_next_step") or "",
            "official_candidate_operator": operator.get("official_candidate_operator"),
            "operator_identity": operator_identity,
            "public_contact_methods": contact_methods,
            "solomon_graph_reachable": solomon_reachable,
            "solomon_x_degree": ranked.get("x_degree", base.get("x_degree")),
            "solomon_primary_path": solomon_path_labels,
            "solomon_primary_edge_kinds": solomon_edge_kinds,
            "solomon_primary_edge_directions": solomon_edge_directions,
            "solomon_primary_edge_rest_id_directions": action.get("primary_edge_rest_id_directions") or [],
            "solomon_direction_evidence_files": action.get("direction_evidence_files") or [],
            "solomon_direction_data_unavailable": direction_data_unavailable,
            "solomon_direction_data_reason": direction_data_reason,
            "solomon_public_interaction_signal": solomon_signal,
            "mango_graph_reachable": mango.get("graph_reachable", False),
            "mango_x_degree": mango.get("x_degree"),
            "mango_secondary_paths": mango_paths,
            "mango_public_interaction_signal": mango_signal,
            "company_account_relationship": operator.get("company_account_relationship") or action.get("company_account_relationship") or {},
            "operator_person_relationship": operator.get("operator_person_relationship") or action.get("operator_person_relationship") or {},
            "human_intro_status": "human_intro_unvalidated",
            "reachability_score": reachability_score,
            "fit_score": fit_score,
            "overall_priority": ranked.get("overall_priority", base.get("score_value")),
            "priority_tier": ranked.get("priority_tier", base.get("priority_tier")),
            "recommended_next_action": action.get("primary_next_action") or ranked.get("recommended_next_action") or base.get("recommended_next_action") or "research_or_watch",
            "action_queue_status": "queued" if action else "not_selected_current_queue",
            "action_owner": action.get("owner") or "",
            "fallback_route": action.get("fallback") or "",
            "unknowns": unique([
                *listify(operator.get("unknowns")),
                *listify(gtm.get("unknowns")),
                ranked.get("risk"),
                "Human introduction willingness is not validated." if (solomon_path_labels or mango_paths) else None,
            ]),
            "source_urls": source_urls,
            "evidence_artifacts": unique([
                "outputs/pilot_v4/all_company_universe_v4.json",
                "outputs/pilot_v4/new_candidate_rankings_graph_adjusted_v4.json" if key in adjusted else None,
                "outputs/pilot_v4/solomon_paths_expanded_v4.json" if key in solomon_paths else None,
                "outputs/pilot_v4/mango_seed_target_paths.json",
                "outputs/pilot_v4/mango_path_interactions_v4.json" if mango_paths else None,
                "data/pilot_v4/top_operator_routes.json" if operator else None,
                "outputs/pilot_v4/operator_x_identity_resolutions_v4.json" if operator else None,
                "outputs/pilot_v4/operator_person_paths_v4.json" if operator else None,
                "data/pilot_v4/gtm_case_studies_v4.json" if gtm else None,
                "outputs/pilot_v4/sponsor_expanded_observations.json" if sponsor_rows else None,
            ]),
            "last_verified_at": last_verified_at,
            "surf_status": ranked.get("surf_status") or base.get("surf_status"),
            "data_limitations": unique([
                "Legacy v3 and v4 scores are not numerically comparable.",
                "X follows and public interactions do not prove a person-level relationship or willingness to introduce.",
                "LinkedIn-derived degree/contact data is intentionally excluded from this v4 projection.",
                "Surf fields are unverified while the account reports PAID_BALANCE_ZERO." if "crypto" in str(base.get("cohort") or "") else None,
            ]),
        }
        rows.append(row)

    rows.sort(key=lambda row: (str(row["score_comparison_group"]), int(row.get("within_group_rank") or 10_000), row["company"].lower()))
    summary = {
        "schema_version": "v4",
        "company_count": len(rows),
        "cohort_counts": dict(sorted((
            (cohort, sum(row["cohort"] == cohort for row in rows))
            for cohort in {row["cohort"] for row in rows}
        ))),
        "queued_company_count": sum(row["action_queue_status"] == "queued" for row in rows),
        "solomon_graph_reachable_count": sum(row["solomon_graph_reachable"] is True for row in rows),
        "mango_graph_reachable_count": sum(row["mango_graph_reachable"] is True for row in rows),
        "named_operator_company_count": sum(bool(row["decision_makers"]) for row in rows),
        "queued_named_operator_company_count": sum(
            row["action_queue_status"] == "queued" and bool(row["decision_makers"]) for row in rows
        ),
        "queued_unresolved_operator_with_specific_acquisition_path_count": sum(
            row["action_queue_status"] == "queued"
            and not row["decision_makers"]
            and bool(row.get("operator_acquisition_next_step"))
            for row in rows
        ),
        "solomon_operator_person_reachable_count": sum(
            ((row.get("operator_person_relationship") or {}).get("solomon") or {}).get("graph_reachable") is True
            for row in rows
        ),
        "mango_operator_person_reachable_count": sum(
            ((row.get("operator_person_relationship") or {}).get("mango") or {}).get("graph_reachable") is True
            for row in rows
        ),
        "legacy_budget_revalidation_needed_count": sum(row["revalidation_needed"] is True for row in rows),
        "legacy_bare_high_confidence_conflict_count": sum(
            row["cohort"] == "existing_v3_ranked"
            and row["budget_confidence"] in {"confirmed", "high_probability"}
            for row in rows
        ),
        "directed_solomon_path_unavailable_count": sum(
            row["solomon_direction_data_unavailable"] is True for row in rows
        ),
        "sponsor_observation_company_count": sum(row["sponsor_observation_count"] > 0 for row in rows),
        "surf_verified_company_count": 0,
        "relationship_rule": "Graph reachability, public interaction, person-level relationship, and intro willingness are separate states.",
        "score_rule": "Sort only within score_comparison_group; global_rank is intentionally null.",
        "outputs": [str(MASTER_JSON.relative_to(ROOT)), str(MASTER_CSV.relative_to(ROOT))],
    }
    return rows, summary


def write_outputs(rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    MASTER_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = list(rows[0]) if rows else []
    with MASTER_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })


def main() -> None:
    rows, summary = build_master()
    if len(rows) != 77 or len({normalize_name(row["company"]) for row in rows}) != 77:
        raise ValueError("Company master must contain exactly 77 unique normalized companies")
    if any(row["global_rank"] is not None or row["score_comparable_across_groups"] for row in rows):
        raise ValueError("Cross-cohort global ranking is forbidden")
    if any("linkedin" in json.dumps(row["public_contact_methods"], ensure_ascii=False).lower() for row in rows):
        raise ValueError("LinkedIn route leaked into v4 company master")
    write_outputs(rows, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
