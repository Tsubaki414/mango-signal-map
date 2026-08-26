#!/usr/bin/env python3
"""Build 42 action-company operator routes under the strict v4 identity contract."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "pilot_v4"
OUT = ROOT / "outputs" / "pilot_v4"
RESEARCH = DATA / "operator_research_candidates_v4.json"
RESOLUTIONS = OUT / "operator_x_identity_resolutions_v4.json"
PERSON_PATHS = OUT / "operator_person_paths_v4.json"
ACTION_QUEUE = OUT / "priority_action_queue_v4.json"
EXISTING = DATA / "top_operator_routes.json"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def unique(values: Iterable[Any]) -> list[Any]:
    output: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value in (None, "", [], {}):
            continue
        key = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        if key not in seen:
            seen.add(key)
            output.append(value)
    return output


def list_rows(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            if isinstance(payload.get(key), list):
                return payload[key]
    raise ValueError(f"Expected a row list under one of {keys}")


def index(rows: Iterable[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = normalize(row.get("company"))
        if not key or key in output:
            raise ValueError(f"Missing/duplicate {label} company: {row.get('company')!r}")
        output[key] = row
    return output


def candidate_block(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("candidate_operator", "operator_candidate", "recommended_operator"):
        if isinstance(row.get(key), dict):
            return row[key]
    return {
        "name": row.get("candidate_name") or row.get("operator_name"),
        "role": row.get("candidate_role") or row.get("operator_role"),
        "decision_relevance": row.get("decision_relevance"),
        "x_handle": row.get("candidate_x_handle") or row.get("x_handle"),
    }


def evidence_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    values = row.get("operator_evidence") or row.get("official_role_evidence") or row.get("evidence") or []
    return [
        {
            **item,
            "claim": item.get("claim") or item.get("exact_claim") or item.get("evidence_text") or "",
        }
        for item in values
        if isinstance(item, dict) and "linkedin" not in json.dumps(item).casefold()
    ]


def public_route(research: dict[str, Any], existing: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    for value in (
        research.get("public_business_route"), research.get("public_route"),
        existing.get("public_business_route"), action.get("public_business_route"),
    ):
        if isinstance(value, dict) and (value.get("value") or value.get("source_url")):
            return value
    return {
        "type": "official_company_contact_route_to_validate",
        "value": action.get("fallback") or "official company contact/program route",
        "source_url": "",
    }


def acquisition_step(
    research: dict[str, Any], route: dict[str, Any], company: str, role: str,
) -> tuple[str, str]:
    explicit = str(
        research.get("acquisition_next_step")
        or research.get("operator_acquisition_next_step")
        or research.get("next_step_if_unresolved")
        or ""
    ).strip()
    route_type = normalize(route.get("type")) or "official_company_route"
    route_value = str(route.get("value") or route.get("source_url") or "the official company contact route").strip()
    if not explicit:
        explicit = (
            f"Use {route_value} and ask the reviewer to identify the current {role} for {company}; "
            "then require an official/company role source and a Rapid X exact-profile check before naming the person."
        )
    status = f"acquisition_required_via_{route_type}: {explicit}"
    return status, explicit


def official_candidate(research: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any] | None:
    candidate = candidate_block(research)
    if not candidate.get("name"):
        return None
    return {
        "name": candidate.get("name"),
        "role": candidate.get("role"),
        "decision_relevance": candidate.get("decision_relevance"),
        "x_handle_candidate": candidate.get("x_handle") or research.get("candidate_x_handle"),
        "status": (
            "promoted_to_recommended_after_strict_verification"
            if resolution.get("confirmed") else "research_candidate_not_written_as_named_operator"
        ),
        "identity_resolution_status": resolution.get("status") or "not_evaluated",
        "failed_identity_gates": resolution.get("failed_gates") or [],
    }


def company_account_relationship(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_type": "company_x_account",
        "solomon": {
            "graph_reachable": action.get("graph_reachable"),
            "x_degree": action.get("x_degree"),
            "primary_path": action.get("primary_path") or [],
            "primary_edge_directions": action.get("primary_edge_directions") or [],
            "direction_data_unavailable": bool(action.get("direction_data_unavailable")),
            "human_intro_status": action.get("human_intro_status") or "human_intro_unvalidated",
        },
        "mango": {
            "graph_reachable": action.get("mango_graph_reachable"),
            "paths": action.get("mango_secondary_paths") or [],
            "human_intro_status": action.get("human_intro_status") or "human_intro_unvalidated",
        },
        "inference_rule": "A company-account path is not a person-level relationship or proof of introduction willingness.",
    }


def canonical_route(
    action: dict[str, Any], research: dict[str, Any], resolution: dict[str, Any],
    person_path: dict[str, Any], existing: dict[str, Any],
) -> dict[str, Any]:
    company = str(action["company"])
    candidate = candidate_block(research)
    role = str(candidate.get("role") or (existing.get("recommended_operator") or {}).get("role") or "Growth / Partnerships / Creator operator")
    route = public_route(research, existing, action)
    acquisition_status, next_step = acquisition_step(research, route, company, role)
    confirmed = bool(resolution.get("confirmed"))
    profile = resolution.get("returned_profile") or {}
    recommended = {
        "name": candidate.get("name") if confirmed else None,
        "role": role,
        "decision_relevance": candidate.get("decision_relevance") or research.get("decision_relevance") or (
            "Role is budget-adjacent but current budget authority remains unconfirmed."
        ),
        "status": "confirmed_official_role_and_rapid_x_exact_profile" if confirmed else acquisition_status,
        "acquisition_next_step": None if confirmed else next_step,
        "x_handle": profile.get("handle") if confirmed else None,
        "x_rest_id": profile.get("rest_id") if confirmed else None,
        "identity_resolution_status": resolution.get("status") or "not_evaluated",
        "budget_authority_confirmed": False,
    }
    # One resolution artifact exists per company in this pass.  Legacy
    # alternates are omitted rather than attaching the recommended candidate's
    # evidence to a different person/role.
    alternate = None
    evidence = evidence_rows(research) or evidence_rows(existing)
    last_verified = str(
        resolution.get("last_verified_at") or research.get("last_verified_at")
        or existing.get("last_verified_at") or action.get("last_verified_at") or date.today().isoformat()
    )
    return {
        "company": company,
        "action_wave": action.get("execution_wave"),
        "why_now": research.get("why_now") or existing.get("why_now") or action.get("why_now") or "",
        "recommended_operator": recommended,
        "alternate_operator": alternate,
        "official_candidate_operator": official_candidate(research, resolution),
        "operator_identity": {
            "confirmed": confirmed,
            "status": resolution.get("status") or "not_evaluated",
            "profile": profile if confirmed else None,
            "failed_gates": resolution.get("failed_gates") or [],
            "gates": resolution.get("gates") or {},
            "rapid_x_cache_file": resolution.get("cache_file") or "",
            "rapid_x_request_cache_file": resolution.get("rapid_x_request_cache_file") or "",
            "official_role_evidence_urls": resolution.get("official_role_evidence_urls") or [],
            "official_role_evidence_ids": resolution.get("official_role_evidence_ids") or [],
            "official_handle_evidence_urls": resolution.get("official_handle_evidence_urls") or [],
            "official_role_verified_at": resolution.get("official_role_verified_at"),
            "rapid_x_profile_observed_at": resolution.get("rapid_x_profile_observed_at"),
            "evaluated_at": resolution.get("evaluated_at"),
            "evidence_contract": resolution.get("evidence_contract") or "official/company source + Rapid X exact profile",
        },
        "company_account_relationship": company_account_relationship(action),
        "operator_person_relationship": {
            "target_type": "operator_person_x_account",
            "solomon": person_path.get("solomon_to_operator_person") or {},
            "mango": person_path.get("mango_to_operator_person") or {},
            "separation_rule": person_path.get("separation_rule") or "Person and company-account paths are separate facts.",
        },
        "public_business_route": route,
        "operator_acquisition_next_step": None if confirmed else next_step,
        "operator_evidence": evidence,
        "mango_offer_angle": research.get("mango_offer_angle") or existing.get("mango_offer_angle") or action.get("mango_wedge") or "",
        "outreach_mode": research.get("outreach_mode") or existing.get("outreach_mode") or "Use the verified public route; do not infer a private relationship.",
        "opening_signal": research.get("opening_signal") or existing.get("opening_signal") or action.get("opening_signal") or "",
        "fallback_route": research.get("fallback_route") or existing.get("fallback_route") or action.get("fallback") or "",
        "last_verified_at": last_verified,
        "unknowns": unique([
            *(research.get("unknowns") or []), *(existing.get("unknowns") or []),
            "Current budget authority is not confirmed.",
            None if confirmed else "Named operator remains withheld until all identity gates pass.",
        ]),
        "fact_status": "confirmed_identity_budget_unverified" if confirmed else "operator_identity_unresolved_route_confirmed_where_cited",
        "confidence": "high_identity_only" if confirmed else "medium_route_low_person_identity",
    }


def validate(routes: list[dict[str, Any]], queue: list[dict[str, Any]], resolutions: dict[str, dict[str, Any]]) -> None:
    if len(routes) != 42 or len(queue) != 42:
        raise ValueError(f"Expected 42 route/action records, got routes={len(routes)}, actions={len(queue)}")
    if {normalize(row["company"]) for row in routes} != {normalize(row["company"]) for row in queue}:
        raise ValueError("Operator route and action queue company sets differ")
    for route in routes:
        key = normalize(route["company"])
        recommended = route["recommended_operator"]
        if recommended.get("name"):
            resolution = resolutions.get(key) or {}
            if not resolution.get("confirmed") or recommended.get("status") != "confirmed_official_role_and_rapid_x_exact_profile":
                raise ValueError(f"Named operator bypassed strict identity contract: {route['company']}")
        else:
            if not str(recommended.get("status") or "").startswith("acquisition_required_via_"):
                raise ValueError(f"Unresolved route lacks specific acquisition status: {route['company']}")
            if not recommended.get("acquisition_next_step"):
                raise ValueError(f"Unresolved route lacks acquisition next step: {route['company']}")
            if "role_to_find" in str(recommended.get("status") or ""):
                raise ValueError(f"Legacy operator placeholder leaked into route: {route['company']}")
        public_route = route.get("public_business_route") or {}
        route_type = str(public_route.get("type") or "").strip()
        route_value = str(public_route.get("value") or "").strip()
        route_source = str(public_route.get("source_url") or "").strip()
        generic_values = {"official company contact route", "official route", "company route", ""}
        if not route_type or route_value.casefold() in generic_values:
            raise ValueError(f"Operator route is not actionable/specific: {route['company']}")
        if not route_source and not (
            route_value.startswith(("https://", "http://"))
            or re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", route_value)
        ):
            raise ValueError(f"Operator route lacks source URL or exact email/URL: {route['company']}")
        scoped_urls = [
            *(str(item.get("url") or "") for item in route.get("operator_evidence") or []),
            str((route.get("public_business_route") or {}).get("source_url") or ""),
        ]
        if any("linkedin.com" in value.casefold() for value in scoped_urls):
            raise ValueError(f"LinkedIn evidence leaked into operator route: {route['company']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=EXISTING)
    args = parser.parse_args()

    queue = read_json(ACTION_QUEUE)
    research = index(list_rows(read_json(RESEARCH), ("companies", "operator_candidates", "records")), "research")
    resolution_payload = read_json(RESOLUTIONS)
    resolutions = index(list_rows(resolution_payload, ("resolutions", "records")), "resolution")
    person_payload = read_json(PERSON_PATHS)
    person_paths = index(list_rows(person_payload, ("records",)), "person path")
    existing = index(read_json(EXISTING, []), "existing route")

    queue_keys = {normalize(row["company"]) for row in queue}
    if set(research) != queue_keys:
        missing = sorted(queue_keys - set(research))
        extra = sorted(set(research) - queue_keys)
        raise ValueError(f"Operator research must cover every action company; missing={missing}, extra={extra}")
    routes = [
        canonical_route(
            action, research[normalize(action["company"])], resolutions[normalize(action["company"])],
            person_paths[normalize(action["company"])], existing.get(normalize(action["company"]), {}),
        )
        for action in queue
    ]
    validate(routes, queue, resolutions)
    args.output.write_text(json.dumps(routes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "route_count": len(routes),
        "named_operator_company_count": sum(bool(row["recommended_operator"].get("name")) for row in routes),
        "unresolved_with_specific_acquisition_path_count": sum(not row["recommended_operator"].get("name") and bool(row["operator_acquisition_next_step"]) for row in routes),
        "wave_1_route_count": sum(int(row.get("action_wave") or 0) == 1 for row in routes),
        "output": str(args.output.relative_to(ROOT)),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
