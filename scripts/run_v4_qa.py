#!/usr/bin/env python3
"""Run reproducible structural, semantic, database, and secret-leak QA for v4."""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "pilot_v4"
REPORT_JSON = OUT / "QA_REPORT_v4.json"
REPORT_MD = OUT / "QA_REPORT_v4.md"
SNAPSHOT_TIMEZONE = ZoneInfo("Asia/Shanghai")
CONFIRMED_OPERATOR_STATUS = "confirmed_official_role_and_rapid_x_exact_profile"
EXPECTED_ACTION_QUEUE_SIZE = 42
PRIOR_NAMED_OPERATOR_COUNT = 6
PRIOR_LEGACY_BUDGET_CONFLICT_COUNT = 26
LEGACY_DIRECTION_BACKFILL_COMPANIES = {"Replit", "Cursor", "Runway", "Perplexity", "Pika"}
ALLOWED_DEGREE_LABELS = {None, "direct", "secondary", "third"}
REQUIRED_OPERATOR_IDENTITY_GATES = {
    "candidate_status_writeable",
    "official_current_role_evidence",
    "candidate_x_handle_present",
    "rapid_x_exact_handle",
    "deterministic_person_identity_match",
    "company_association",
    "non_linkedin_evidence_only",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _nonempty(value: Any) -> bool:
    return bool(str(value or "").strip())


def _company_index(rows: list[dict[str, Any]], artifact: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        company = str(row.get("company") or "").strip()
        key = normalized(company)
        if not key:
            raise ValueError(f"{artifact} contains a row without a company")
        if key in indexed:
            raise ValueError(f"{artifact} contains duplicate company: {company}")
        indexed[key] = row
    return indexed


def _nested_key_values(value: Any, wanted_key: str) -> list[Any]:
    matches: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == wanted_key:
                matches.append(child)
            matches.extend(_nested_key_values(child, wanted_key))
    elif isinstance(value, list):
        for child in value:
            matches.extend(_nested_key_values(child, wanted_key))
    return matches


def _assert_no_semantic_promotion(value: Any, artifact: str) -> None:
    invalid_budget = [item for item in _nested_key_values(value, "budget_authority_confirmed") if item is not False]
    if invalid_budget:
        raise ValueError(f"{artifact} promotes unconfirmed budget authority: {invalid_budget[:3]}")

    invalid_intro: list[Any] = []
    for item in _nested_key_values(value, "human_intro_status"):
        if item is True:
            invalid_intro.append(item)
            continue
        text = str(item or "").strip().lower()
        if text in {"validated", "confirmed", "human_intro_validated", "true", "yes"}:
            invalid_intro.append(item)
    if invalid_intro:
        raise ValueError(f"{artifact} promotes human introduction willingness: {invalid_intro[:3]}")


def validate_degree_labels(labels: list[Any], artifact: str) -> dict[str, int]:
    invalid = sorted({str(value) for value in labels if value not in ALLOWED_DEGREE_LABELS})
    if invalid:
        raise ValueError(f"{artifact} has invalid degree_label values: {invalid}")
    return {
        "rows": len(labels),
        "null": sum(value is None for value in labels),
        "direct": sum(value == "direct" for value in labels),
        "secondary": sum(value == "secondary" for value in labels),
        "third": sum(value == "third" for value in labels),
    }


def validate_operator_semantics(
    routes: list[dict[str, Any]],
    identity_artifact: dict[str, Any],
    person_path_artifact: dict[str, Any],
    actions: list[dict[str, Any]],
    master: list[dict[str, Any]],
) -> dict[str, Any]:
    """Enforce the operator evidence gate and company/person path separation.

    The current strict count is deliberately derived from the resolution artifact,
    while the QA still requires an improvement over the audited 6/77 baseline.
    That makes the report snapshot-aware without allowing a silent regression.
    """
    resolutions = identity_artifact.get("resolutions") or []
    person_records = person_path_artifact.get("records") or []
    route_index = _company_index(routes, "top_operator_routes.json")
    action_index = _company_index(actions, "priority_action_queue_v4.json")
    resolution_index = _company_index(resolutions, "operator_x_identity_resolutions_v4.json")
    person_index = _company_index(person_records, "operator_person_paths_v4.json")
    master_index = _company_index(master, "company_master_v4.json")

    if len(route_index) != EXPECTED_ACTION_QUEUE_SIZE or len(action_index) != EXPECTED_ACTION_QUEUE_SIZE:
        raise ValueError(f"operator route/action counts must both be 42: {len(route_index)}/{len(action_index)}")
    route_keys = set(route_index)
    for artifact, keys in (
        ("priority action queue", set(action_index)),
        ("operator identity resolutions", set(resolution_index)),
        ("operator person paths", set(person_index)),
    ):
        if keys != route_keys:
            raise ValueError(
                f"{artifact} company set differs from routes: "
                f"missing={sorted(route_keys - keys)}, extra={sorted(keys - route_keys)}"
            )
    if not route_keys.issubset(master_index):
        raise ValueError(f"operator routes missing from company master: {sorted(route_keys - set(master_index))}")

    identity_summary = identity_artifact.get("summary") or {}
    person_summary = person_path_artifact.get("summary") or {}
    confirmed_keys = {key for key, row in resolution_index.items() if row.get("confirmed") is True}
    confirmed_count = len(confirmed_keys)
    if confirmed_count <= PRIOR_NAMED_OPERATOR_COUNT:
        raise ValueError(
            f"strict named-operator count regressed or failed to improve: {confirmed_count} <= {PRIOR_NAMED_OPERATOR_COUNT}"
        )
    if identity_summary.get("candidate_company_count") != EXPECTED_ACTION_QUEUE_SIZE:
        raise ValueError("operator identity summary does not cover all 42 action companies")
    if identity_summary.get("confirmed_operator_count") != confirmed_count:
        raise ValueError("operator identity summary confirmed count disagrees with records")

    named_route_keys = {
        key for key, row in route_index.items()
        if _nonempty((row.get("recommended_operator") or {}).get("name"))
    }
    named_action_keys = {key for key, row in action_index.items() if _nonempty(row.get("recommended_operator_name"))}
    named_master_keys = {
        key for key in route_keys
        if master_index[key].get("decision_makers")
    }
    if named_route_keys != confirmed_keys or named_action_keys != confirmed_keys or named_master_keys != confirmed_keys:
        raise ValueError(
            "strict named operator set is inconsistent across resolution/route/action/master artifacts: "
            f"resolution={sorted(confirmed_keys)}, route={sorted(named_route_keys)}, "
            f"action={sorted(named_action_keys)}, master={sorted(named_master_keys)}"
        )

    forbidden_artifacts = {
        "operator routes": routes,
        "operator identity resolutions": identity_artifact,
        "operator person paths": person_path_artifact,
        "action queue": actions,
    }
    for artifact, payload in forbidden_artifacts.items():
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        if "linkedin.com" in serialized:
            raise ValueError(f"LinkedIn URL leaked into {artifact}")
        if "role_to_find" in serialized:
            raise ValueError(f"generic role_to_find placeholder leaked into {artifact}")
        _assert_no_semantic_promotion(payload, artifact)

    for key in route_keys:
        route = route_index[key]
        action = action_index[key]
        resolution = resolution_index[key]
        person_record = person_index[key]
        master_row = master_index[key]
        operator = route.get("recommended_operator") or {}
        operator_identity = route.get("operator_identity") or {}
        company_relationship = route.get("company_account_relationship") or {}
        person_relationship = route.get("operator_person_relationship") or {}

        if company_relationship.get("target_type") != "company_x_account":
            raise ValueError(f"{route['company']}: company-account relationship target type is invalid")
        if person_relationship.get("target_type") != "operator_person_x_account":
            raise ValueError(f"{route['company']}: operator-person relationship target type is invalid")
        if action.get("company_account_relationship", {}).get("target_type") != "company_x_account":
            raise ValueError(f"{route['company']}: action queue lost company-account relationship typing")
        if action.get("operator_person_relationship", {}).get("target_type") != "operator_person_x_account":
            raise ValueError(f"{route['company']}: action queue lost operator-person relationship typing")
        if master_row.get("company_account_relationship", {}).get("target_type") != "company_x_account":
            raise ValueError(f"{route['company']}: company master lost company-account relationship typing")
        if master_row.get("operator_person_relationship", {}).get("target_type") != "operator_person_x_account":
            raise ValueError(f"{route['company']}: company master lost operator-person relationship typing")

        person = person_record.get("operator_person") or {}
        if key in confirmed_keys:
            gates = resolution.get("gates") or {}
            missing_or_failed_gates = sorted(
                gate for gate in REQUIRED_OPERATOR_IDENTITY_GATES if gates.get(gate) is not True
            )
            profile = resolution.get("returned_profile") or {}
            if missing_or_failed_gates or resolution.get("status") != CONFIRMED_OPERATOR_STATUS:
                raise ValueError(f"{route['company']}: confirmed identity lacks strict gates: {missing_or_failed_gates}")
            if not resolution.get("official_role_evidence_urls") or not _nonempty(profile.get("handle")) or not _nonempty(profile.get("rest_id")):
                raise ValueError(f"{route['company']}: confirmed identity lacks official evidence or exact Rapid X identity")
            expected_handle = str(profile["handle"]).lstrip("@").lower()
            expected_rest_id = str(profile["rest_id"])
            if (
                operator.get("status") != CONFIRMED_OPERATOR_STATUS
                or operator_identity.get("confirmed") is not True
                or operator_identity.get("status") != CONFIRMED_OPERATOR_STATUS
                or any((operator_identity.get("gates") or {}).get(gate) is not True for gate in REQUIRED_OPERATOR_IDENTITY_GATES)
                or not _nonempty(operator.get("name"))
                or str(operator.get("x_handle") or "").lstrip("@").lower() != expected_handle
                or str(operator.get("x_rest_id") or "") != expected_rest_id
            ):
                raise ValueError(f"{route['company']}: route does not preserve the strict resolved identity")
            if (
                action.get("recommended_operator_status") != CONFIRMED_OPERATOR_STATUS
                or str(action.get("recommended_operator_x_handle") or "").lstrip("@").lower() != expected_handle
                or str(action.get("recommended_operator_x_rest_id") or "") != expected_rest_id
                or _nonempty(action.get("operator_acquisition_next_step"))
            ):
                raise ValueError(f"{route['company']}: action queue does not preserve the strict resolved identity")
            decision_makers = master_row.get("decision_makers") or []
            if len(decision_makers) != 1:
                raise ValueError(f"{route['company']}: expected exactly one strict decision-maker record")
            decision_maker = decision_makers[0]
            if (
                decision_maker.get("status") != CONFIRMED_OPERATOR_STATUS
                or str(decision_maker.get("x_handle") or "").lstrip("@").lower() != expected_handle
                or str(decision_maker.get("x_rest_id") or "") != expected_rest_id
            ):
                raise ValueError(f"{route['company']}: master decision-maker identity disagrees with Rapid X")
            if (
                person.get("identity_confirmed") is not True
                or str(person.get("x_handle") or "").lstrip("@").lower() != expected_handle
                or str(person.get("x_rest_id") or "") != expected_rest_id
            ):
                raise ValueError(f"{route['company']}: person-path target identity disagrees with Rapid X")
        else:
            status = str(operator.get("status") or "")
            step = str(operator.get("acquisition_next_step") or "").strip()
            if _nonempty(operator.get("name")) or _nonempty(operator.get("x_handle")) or _nonempty(operator.get("x_rest_id")):
                raise ValueError(f"{route['company']}: unresolved identity was written as a named/X-resolved operator")
            if not status.startswith("acquisition_required_via_") or not step:
                raise ValueError(f"{route['company']}: unresolved operator lacks a specific acquisition status/step")
            if action.get("recommended_operator_status") != status or str(action.get("operator_acquisition_next_step") or "").strip() != step:
                raise ValueError(f"{route['company']}: unresolved acquisition route is not preserved in the action queue")
            if any(_nonempty(action.get(field)) for field in ("recommended_operator_name", "recommended_operator_x_handle", "recommended_operator_x_rest_id")):
                raise ValueError(f"{route['company']}: action queue promotes an unresolved person")
            if master_row.get("decision_makers"):
                raise ValueError(f"{route['company']}: company master promotes an unresolved person")
            if person.get("identity_confirmed") is not False:
                raise ValueError(f"{route['company']}: unresolved person path is not marked identity_confirmed=false")

        if operator.get("budget_authority_confirmed") is not False:
            raise ValueError(f"{route['company']}: operator budget authority was promoted")

    solomon_reachable = sum(
        (row.get("solomon_to_operator_person") or {}).get("graph_reachable") is True
        for row in person_records
    )
    mango_reachable = sum(
        (row.get("mango_to_operator_person") or {}).get("graph_reachable") is True
        for row in person_records
    )
    if person_summary.get("operator_identity_confirmed_count") != confirmed_count:
        raise ValueError("operator-person path summary identity count disagrees with strict resolution artifact")
    if person_summary.get("solomon_operator_reachable_count") != solomon_reachable:
        raise ValueError("Solomon operator-person reach summary disagrees with path records")
    if person_summary.get("mango_operator_reachable_count") != mango_reachable:
        raise ValueError("Mango operator-person reach summary disagrees with path records")
    for label, relationship_key, expected in (
        ("route Solomon", "solomon", solomon_reachable),
        ("route Mango", "mango", mango_reachable),
    ):
        actual = sum(
            ((row.get("operator_person_relationship") or {}).get(relationship_key) or {}).get("graph_reachable") is True
            for row in routes
        )
        if actual != expected:
            raise ValueError(f"{label} reach count disagrees with operator-person path artifact: {actual}/{expected}")

    unresolved_count = EXPECTED_ACTION_QUEUE_SIZE - confirmed_count
    return {
        "operator_routes": len(routes),
        "action_queue_records": len(actions),
        "strict_named_operators": confirmed_count,
        "strict_named_operator_denominator": len(master),
        "unresolved_with_specific_acquisition_path": unresolved_count,
        "solomon_operator_person_reachable": solomon_reachable,
        "mango_operator_person_reachable": mango_reachable,
        "linkedin_evidence_urls": 0,
        "generic_role_placeholders": 0,
        "budget_authority_confirmed": 0,
        "human_intro_validated": 0,
    }


def verification_date(value: Any) -> date | None:
    """Normalize evidence timestamps to the project snapshot timezone.

    Rapid X caches often use UTC while the snapshot is dated in Asia/Shanghai.
    Comparing raw ISO date prefixes would incorrectly mark a 17:00 UTC
    observation as one day old, so offset-aware timestamps are localized first.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(SNAPSHOT_TIMEZONE)
        return parsed.date()
    except ValueError:
        return None


def high_confidence_spend(row: dict[str, Any]) -> bool:
    spend_level = str(row.get("spend_evidence_level") or "").strip().lower()
    budget = str(row.get("budget_confidence") or "").strip().lower()
    return (
        spend_level in {"l3", "confirmed", "high"}
        or budget in {"confirmed", "high_probability", "high"}
        or budget.startswith("high_")
        or budget.startswith("legacy_unrevalidated_confirmed")
        or budget.startswith("legacy_unrevalidated_high_probability")
    )


def revalidation_violations(
    rows: list[dict[str, Any]],
    snapshot: date,
    artifact: str,
) -> list[dict[str, Any]]:
    """Find high-confidence rows that are stale/malformed without an explicit flag."""
    violations: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not high_confidence_spend(row):
            continue
        spend_verified_value = row.get("spend_last_verified_at") or row.get("last_verified_at")
        verified = verification_date(spend_verified_value)
        stale_or_unknown = verified is None or verified < snapshot
        if stale_or_unknown and row.get("revalidation_needed") is not True:
            violations.append({
                "artifact": artifact,
                "row": index,
                "company": row.get("company") or row.get("project") or "",
                "spend_evidence_level": row.get("spend_evidence_level"),
                "budget_confidence": row.get("budget_confidence"),
                "last_verified_at": row.get("last_verified_at"),
                "spend_last_verified_at": row.get("spend_last_verified_at"),
                "reason": "missing_or_invalid_last_verified_at" if verified is None else "verified_before_snapshot",
            })
    return violations


def graphml_counts(path: Path) -> tuple[int, int]:
    nodes = edges = 0
    for _event, element in ET.iterparse(path, events=("end",)):
        kind = element.tag.rsplit("}", 1)[-1]
        nodes += kind == "node"
        edges += kind == "edge"
        if kind in {"node", "edge"}:
            element.clear()
    return nodes, edges


def canonical_json_files() -> list[Path]:
    return sorted((ROOT / "data" / "pilot_v4").glob("*.json")) + sorted(OUT.glob("*.json"))


def canonical_csv_files() -> list[Path]:
    return sorted(OUT.glob("*.csv"))


def secret_findings() -> list[dict[str, str]]:
    patterns = {
        "surf_live_key": re.compile(r"sk-surf-[A-Za-z0-9_-]{12,}"),
        "rapid_key_assignment": re.compile(
            r"(?im)^\s*(?:RAPID_X_API_KEY|X_RAPIDAPI_KEY)\s*=\s*['\"]?([A-Za-z0-9_-]{16,})"
        ),
        "rapid_header_literal": re.compile(r"(?i)x-rapidapi-key\s*[:=]\s*['\"]?[A-Za-z0-9_-]{16,}"),
    }
    allowed_suffixes = {".py", ".md", ".toml", ".json", ".csv", ".txt", ".example", ".gitignore"}
    excluded_dirs = {".git", "__pycache__", ".pytest_cache", "cache"}
    findings: list[dict[str, str]] = []
    for directory, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in excluded_dirs]
        for filename in filenames:
            path = Path(directory) / filename
            if path == ROOT / ".env" or path.suffix.lower() not in allowed_suffixes:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for rule, pattern in patterns.items():
                if pattern.search(text):
                    findings.append({"path": str(path.relative_to(ROOT)), "rule": rule})
    return findings


def run_checks() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(
        name: str,
        operation: Callable[[], Any],
        report_details: Callable[[Any], Any] | None = None,
    ) -> Any:
        try:
            details = operation()
            checks.append({
                "name": name,
                "status": "pass",
                "details": report_details(details) if report_details else details,
            })
            return details
        except Exception as error:  # QA must record every failure before exiting.
            checks.append({"name": name, "status": "fail", "details": f"{type(error).__name__}: {error}"})
            return None

    def validate_json() -> dict[str, int]:
        files = canonical_json_files()
        for path in files:
            load_json(path)
        return {"files": len(files)}

    def validate_csv() -> dict[str, int]:
        files = canonical_csv_files()
        total_rows = 0
        for path in files:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if not reader.fieldnames or any(not field for field in reader.fieldnames):
                    raise ValueError(f"missing/empty header: {path.relative_to(ROOT)}")
                for row_number, row in enumerate(reader, 2):
                    if None in row:
                        raise ValueError(f"column-width mismatch: {path.relative_to(ROOT)}:{row_number}")
                    total_rows += 1
        return {"files": len(files), "rows": total_rows}

    check("canonical_json_parse", validate_json)
    check("canonical_csv_shape", validate_csv)

    compact_row_count = lambda rows: {"rows": len(rows)}
    universe = check(
        "company_universe_load",
        lambda: load_json(OUT / "all_company_universe_v4.json"),
        compact_row_count,
    ) or []
    master = check(
        "company_master_load",
        lambda: load_json(OUT / "company_master_v4.json"),
        compact_row_count,
    ) or []
    adjusted = load_json(OUT / "new_candidate_rankings_graph_adjusted_v4.json")
    actions = load_json(OUT / "priority_action_queue_v4.json")
    sponsor_observations = load_json(OUT / "sponsor_expanded_observations.json")
    sponsor_edges = load_json(OUT / "sponsor_expanded_edges.json")
    solomon_interactions = load_json(OUT / "x_path_interactions_v4.json")
    mango_interactions = load_json(OUT / "mango_path_interactions_v4.json")
    operator_routes = load_json(ROOT / "data" / "pilot_v4" / "top_operator_routes.json")
    operator_identities = load_json(OUT / "operator_x_identity_resolutions_v4.json")
    operator_person_paths = load_json(OUT / "operator_person_paths_v4.json")

    def validate_company_sets() -> dict[str, int]:
        universe_keys = [normalized(row["company"]) for row in universe]
        master_keys = [normalized(row["company"]) for row in master]
        if len(universe_keys) != 77 or len(set(universe_keys)) != 77:
            raise ValueError("universe is not 77 unique normalized companies")
        if universe_keys and set(universe_keys) != set(master_keys):
            raise ValueError("company master does not match universe")
        if len(adjusted) != 51 or not actions:
            raise ValueError(f"unexpected adjusted/actions counts: {len(adjusted)}/{len(actions)}")
        required_action_fields = ("owner", "why_now", "budget_evidence", "opening_signal", "primary_next_action", "fallback", "success_condition")
        incomplete = [
            row.get("company")
            for row in actions
            if any(not str(row.get(field) or "").strip() for field in required_action_fields)
            or not (row.get("validation_questions") or [])
        ]
        if incomplete:
            raise ValueError(f"actions missing execution fields: {incomplete}")
        if any("linkedin.com" in str(url).lower() for row in actions for url in row.get("evidence_urls") or []):
            raise ValueError("LinkedIn-derived evidence leaked into v4 action queue")
        legacy_actions = [row for row in actions if str(row.get("source_group") or "").startswith("v3_existing")]
        if any("see v3 evidence" in str(row.get("budget_evidence") or "").lower() for row in legacy_actions):
            raise ValueError("legacy action still delegates budget evidence to an external table")
        return {
            "universe": len(universe),
            "master": len(master),
            "new_adjusted": len(adjusted),
            "actions": len(actions),
            "complete_action_records": len(actions),
            "legacy_actions_with_embedded_public_evidence": len(legacy_actions),
        }

    check("company_set_and_action_counts", validate_company_sets)

    def validate_relationship_semantics() -> dict[str, int]:
        reachable_master = [row for row in master if row.get("solomon_graph_reachable") is True or row.get("mango_graph_reachable") is True]
        invalid_intro = [row["company"] for row in master if row.get("human_intro_status") != "human_intro_unvalidated"]
        if invalid_intro:
            raise ValueError(f"intro status promoted without human evidence: {invalid_intro}")
        if any(row.get("budget_authority_confirmed") is not False for row in master):
            raise ValueError("budget authority promoted without confirmation")
        if any(row.get("global_rank") is not None or row.get("score_comparable_across_groups") for row in master):
            raise ValueError("cross-cohort rank/comparability leak")
        legacy_rows = [row for row in master if row.get("cohort") == "existing_v3_ranked"]
        bare_legacy_budget = [
            row["company"] for row in legacy_rows
            if row.get("budget_confidence") in {"confirmed", "high_probability"}
            or not str(row.get("budget_confidence") or "").startswith("legacy_unrevalidated_")
        ]
        if bare_legacy_budget:
            raise ValueError(f"legacy budget confidence lacks unrevalidated namespace: {bare_legacy_budget}")
        unmarked_legacy_budget = [row["company"] for row in legacy_rows if row.get("revalidation_needed") is not True]
        if unmarked_legacy_budget:
            raise ValueError(f"legacy budget confidence lacks revalidation marker: {unmarked_legacy_budget}")
        ambiguous_directions = [
            row["company"] for row in master
            if row.get("solomon_primary_path")
            and not row.get("solomon_primary_edge_directions")
            and row.get("solomon_direction_data_unavailable") is not True
        ]
        if ambiguous_directions:
            raise ValueError(f"non-empty Solomon path has ambiguous empty direction data: {ambiguous_directions}")
        return {
            "reachable_companies": len(reachable_master),
            "human_intro_validated": 0,
            "budget_authority_confirmed": 0,
            "legacy_budget_records_namespaced_unrevalidated": len(legacy_rows),
            "legacy_bare_high_confidence_conflicts": 0,
            "ambiguous_empty_direction_records": 0,
        }

    check("relationship_and_budget_semantics", validate_relationship_semantics)

    operator_semantics = check(
        "operator_identity_and_route_semantics",
        lambda: validate_operator_semantics(
            operator_routes,
            operator_identities,
            operator_person_paths,
            actions,
            master,
        ),
    ) or {}

    def validate_spend_freshness_revalidation() -> dict[str, Any]:
        summary = load_json(OUT / "company_universe_summary.json")
        snapshot_text = str(summary.get("generated_date") or "")
        try:
            snapshot = date.fromisoformat(snapshot_text)
        except ValueError as error:
            raise ValueError(f"invalid/missing company-universe snapshot date: {snapshot_text!r}") from error
        artifacts = {
            "all_company_universe_v4.json": universe,
            "new_candidate_rankings_v4.json": load_json(OUT / "new_candidate_rankings_v4.json"),
            "new_candidate_rankings_graph_adjusted_v4.json": adjusted,
            "priority_action_queue_v4.json": actions,
            "company_master_v4.json": master,
        }
        violations = [
            violation
            for artifact, rows in artifacts.items()
            for violation in revalidation_violations(rows, snapshot, artifact)
        ]
        if violations:
            raise ValueError(f"high-confidence spend freshness/revalidation violations: {violations}")
        high_confidence_rows = [
            row
            for rows in artifacts.values()
            for row in rows
            if high_confidence_spend(row)
        ]
        stale_or_unknown = [
            row
            for row in high_confidence_rows
            if verification_date(row.get("spend_last_verified_at") or row.get("last_verified_at")) is None
            or verification_date(row.get("spend_last_verified_at") or row.get("last_verified_at")) < snapshot
        ]
        return {
            "snapshot_date": snapshot.isoformat(),
            "snapshot_timezone": str(SNAPSHOT_TIMEZONE),
            "artifacts_checked": len(artifacts),
            "high_confidence_records_checked": len(high_confidence_rows),
            "stale_or_unknown_high_confidence_records": len(stale_or_unknown),
            "stale_records_with_required_revalidation_marker": sum(
                row.get("revalidation_needed") is True for row in stale_or_unknown
            ),
            "violations": 0,
        }

    check("high_confidence_spend_freshness_revalidation", validate_spend_freshness_revalidation)

    def validate_sponsor_semantics() -> dict[str, int]:
        urls = [row["content_url"] for row in sponsor_observations]
        if len(sponsor_observations) != 52 or len(set(urls)) != 52 or len(sponsor_edges) != 50:
            raise ValueError("unexpected sponsor counts or duplicate content URLs")
        promoted = [row["edge_id"] for row in sponsor_edges if row.get("paid_signal") and not row.get("paid_observation_count")]
        if promoted:
            raise ValueError(f"affiliate/mention promoted to paid: {promoted}")
        return {
            "observations": len(sponsor_observations),
            "edges": len(sponsor_edges),
            "explicit_paid": sum(row.get("disclosure_type") == "paid_sponsorship" for row in sponsor_observations),
        }

    check("sponsor_evidence_semantics", validate_sponsor_semantics)

    def validate_interactions() -> dict[str, int]:
        solomon_rows = solomon_interactions.get("observations") or []
        mango_rows = mango_interactions.get("observations") or []
        rows = [*solomon_rows, *mango_rows]
        semantic_ids = [row.get("interaction_id") for row in rows]
        if any(not value for value in semantic_ids):
            raise ValueError("missing interaction_id")
        signatures: dict[str, tuple[str, str, str, str, str]] = {}
        duplicate_observations = 0
        for row in rows:
            interaction_id = str(row["interaction_id"])
            signature = (
                str(row["tweet_id"]), str(row["source_handle"]).lower(),
                str(row["destination_handle"]).lower(), str(row["interaction_type"]), str(row["tweet_url"]),
            )
            if interaction_id in signatures:
                duplicate_observations += 1
                if signatures[interaction_id] != signature:
                    raise ValueError(f"conflicting duplicate interaction_id: {interaction_id}")
            else:
                signatures[interaction_id] = signature
        return {
            "solomon_observations": len(solomon_rows),
            "mango_observations": len(mango_rows),
            "total_observations": len(rows),
            "unique_semantic_interactions": len(signatures),
            "cross_scope_duplicate_observations": duplicate_observations,
        }

    check("x_interaction_identity_semantics", validate_interactions)

    graph_details: dict[str, Any] = {}
    for path in sorted(OUT.glob("*.graphml")):
        graph_details[path.name] = check(
            f"graphml_parse:{path.name}",
            lambda path=path: dict(zip(("nodes", "edges"), graphml_counts(path))),
        )

    def validate_database() -> dict[str, Any]:
        database = OUT / "mango_bd_v4.sqlite"
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            expected_tables = (
                "companies", "x_nodes", "x_edges", "x_paths", "x_interactions",
                "sponsor_observations", "sponsor_edges", "operator_routes",
                "operator_identities", "operator_person_paths", "action_queue", "gtm_cases",
            )
            present_tables = {
                row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            missing_tables = sorted(set(expected_tables) - present_tables)
            if missing_tables:
                raise ValueError(f"missing required SQLite tables: {missing_tables}")
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in expected_tables
            }
            degree_details = {
                table: validate_degree_labels(
                    [row[0] for row in connection.execute(f"SELECT degree_label FROM {table}")],
                    f"SQLite {table}",
                )
                for table in ("x_paths", "operator_person_paths")
            }
        finally:
            connection.close()
        if integrity != "ok" or foreign_keys:
            raise ValueError(f"integrity={integrity}, fk_violations={len(foreign_keys)}")
        expected = {
            "companies": 77,
            "x_interactions": len({
                row["interaction_id"]
                for artifact in (solomon_interactions, mango_interactions)
                for row in (artifact.get("observations") or [])
            }),
            "sponsor_observations": len(sponsor_observations),
            "sponsor_edges": len(sponsor_edges),
            "operator_routes": len(operator_routes),
            "operator_identities": len(operator_identities.get("resolutions") or []),
            "operator_person_paths": 2 * len(operator_person_paths.get("records") or []),
            "action_queue": len(actions),
            "gtm_cases": 8,
        }
        mismatch = {key: {"expected": value, "actual": counts[key]} for key, value in expected.items() if counts[key] != value}
        if mismatch:
            raise ValueError(f"count mismatch: {mismatch}")
        return {
            "integrity": integrity,
            "foreign_key_violations": 0,
            "counts": counts,
            "degree_label_domain": degree_details,
        }

    check("sqlite_integrity_and_counts", validate_database)

    def validate_security() -> dict[str, Any]:
        findings = secret_findings()
        env = ROOT / ".env"
        mode = oct(env.stat().st_mode & 0o777) if env.exists() else "missing"
        ignore_lines = {line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()}
        if findings or mode != "0o600" or ".env" not in ignore_lines:
            raise ValueError({"finding_count": len(findings), "env_mode": mode, "env_ignored": ".env" in ignore_lines})
        return {"high_risk_secret_findings": 0, "env_mode": mode, "env_ignored": True}

    check("credential_hygiene", validate_security)

    failures = [row for row in checks if row["status"] == "fail"]
    legacy_budget_conflicts_after = sum(
        row.get("cohort") == "existing_v3_ranked"
        and row.get("budget_confidence") in {"confirmed", "high_probability"}
        for row in master
    )
    direction_backfilled = sorted(
        row["company"] for row in master
        if row.get("company") in LEGACY_DIRECTION_BACKFILL_COMPANIES
        and row.get("solomon_primary_path")
        and row.get("solomon_primary_edge_directions")
        and row.get("solomon_direction_data_unavailable") is False
    )
    freshness_check = next(
        (row for row in checks if row["name"] == "high_confidence_spend_freshness_revalidation"),
        {"status": "fail"},
    )
    operator_check = next(
        (row for row in checks if row["name"] == "operator_identity_and_route_semantics"),
        {"status": "fail"},
    )
    audit_remediation = {
        "operator_identification": {
            "before": f"{PRIOR_NAMED_OPERATOR_COUNT}/{len(master)}",
            "after": f"{operator_semantics.get('strict_named_operators', 0)}/{len(master)}",
            "action_queue_after": f"{operator_semantics.get('strict_named_operators', 0)}/{len(actions)}",
            "strict_gate_status": operator_check["status"],
        },
        "legacy_budget_confidence_conflicts": {
            "before": PRIOR_LEGACY_BUDGET_CONFLICT_COUNT,
            "after": legacy_budget_conflicts_after,
        },
        "legacy_direction_backfill": {
            "before_missing": len(LEGACY_DIRECTION_BACKFILL_COMPANIES),
            "after_missing": len(LEGACY_DIRECTION_BACKFILL_COMPANIES - set(direction_backfilled)),
            "backfilled_companies": direction_backfilled,
        },
        "freshness_revalidation_gate": {
            "status": freshness_check["status"],
            "rule": (
                "stale or missing spend_last_verified_at (falling back to last_verified_at) on "
                "high-confidence spend requires revalidation_needed=true"
            ),
        },
    }
    return {
        "schema_version": "v4",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if not failures else "fail",
        "check_count": len(checks),
        "passed": len(checks) - len(failures),
        "failed": len(failures),
        "checks": checks,
        "audit_remediation": audit_remediation,
        "limitations": [
            "This is structural and semantic QA; it does not re-fetch every mutable external source.",
            "Binary screenshots are not text-scanned. Local Screenshot*.png files are ignored because a retired credential was shown in a diagnostic screenshot.",
            "Surf remains unverified because the service returned PAID_BALANCE_ZERO.",
        ],
    }


def write_report(report: dict[str, Any]) -> None:
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# MangoBD v4 QA Report",
        "",
        f"Status: **{report['status'].upper()}**  ",
        f"Generated: `{report['generated_at']}`  ",
        f"Checks: {report['passed']} passed, {report['failed']} failed.",
        "",
        "## Audit remediation summary",
        "",
        (
            "- P0 operator identification: "
            f"`{report['audit_remediation']['operator_identification']['before']}` → "
            f"`{report['audit_remediation']['operator_identification']['after']}` across the 77-company master; "
            f"action queue coverage is `{report['audit_remediation']['operator_identification']['action_queue_after']}` "
            "under the official-role + Rapid X exact-profile gate."
        ),
        (
            "- P1 legacy `budget_confidence` conflicts: "
            f"`{report['audit_remediation']['legacy_budget_confidence_conflicts']['before']}` → "
            f"`{report['audit_remediation']['legacy_budget_confidence_conflicts']['after']}`."
        ),
        (
            "- P2 legacy direction gaps: "
            f"`{report['audit_remediation']['legacy_direction_backfill']['before_missing']}` → "
            f"`{report['audit_remediation']['legacy_direction_backfill']['after_missing']}`; "
            f"backfilled `{', '.join(report['audit_remediation']['legacy_direction_backfill']['backfilled_companies'])}`."
        ),
        (
            "- P3 freshness/revalidation gate: "
            f"`{report['audit_remediation']['freshness_revalidation_gate']['status']}` — "
            f"{report['audit_remediation']['freshness_revalidation_gate']['rule']}."
        ),
        "",
        "## Checks",
        "",
    ]
    for row in report["checks"]:
        mark = "PASS" if row["status"] == "pass" else "FAIL"
        lines.append(f"- `{mark}` — `{row['name']}`: {json.dumps(row['details'], ensure_ascii=False, sort_keys=True)}")
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {value}" for value in report["limitations"])
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_checks()
    write_report(report)
    print(json.dumps({key: report[key] for key in ("status", "check_count", "passed", "failed")}, ensure_ascii=False, indent=2))
    if report["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
