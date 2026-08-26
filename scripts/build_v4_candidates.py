#!/usr/bin/env python3
"""Build the v4 candidate rankings and the conservative 77-company universe."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mangobd.discovery import score_candidate


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "pilot_v4"
OUT = ROOT / "outputs" / "pilot_v4"
CONFIG = ROOT / "config" / "discovery_v4.json"
EXISTING_V3 = ROOT / "outputs" / "pilot_v3" / "company_rankings_solomon_x.json"

EXPECTED_EXISTING = 26
EXPECTED_LONGTAIL = 34
EXPECTED_CRYPTO = 17
EXPECTED_NEW = EXPECTED_LONGTAIL + EXPECTED_CRYPTO
EXPECTED_UNIVERSE = EXPECTED_EXISTING + EXPECTED_NEW

V4_SCORE_METHOD = "v4_evidence_routing_weighted_no_fame_or_funding_points"
V4_SCORE_GROUP = "v4_new_candidates"
V3_SCORE_METHOD = "v3_solomon_x_weighted_preserved"
V3_SCORE_GROUP = "v3_existing_solomon_x"

SECONDARY_SOURCE_DOMAINS = {
    "businesswire.com",
    "chainwire.org",
    "globenewswire.com",
    "prnewswire.com",
    "techcrunch.com",
    "theblock.co",
}

FIRST_PARTY_PARTNER_DOMAINS = {
    "a16z.com",
    "insightpartners.com",
    "marketplace.crewai.com",
}

EXPLICIT_SPEND_MAP = {
    "L3_explicit_cash": "active_creator_or_partner_budget",
    "L3_explicit_cash_terms_unverified": "active_creator_or_partner_budget",
    "L2_committed_co_sell": "formal_paid_program",
    "L2_committed_channel_activation": "formal_paid_program",
    "L2_committed_in_kind": "formal_paid_program",
    "L2_planned_revenue_share": "formal_paid_program",
    "L1_capacity_signal_only": "funding_or_token_value_only",
}

NEW_CSV_FIELDS = [
    "rank", "company", "aliases", "category", "geography", "cohort", "cohort_group",
    "score_method", "score_comparison_group", "score_comparable_across_groups",
    "priority_tier", "overall_priority", "gross_score", "penalty_score",
    "reachability_level", "reachability_score", "spend_evidence_level",
    "spend_evidence_subtype", "spend_mechanism_level", "spend_mechanism_score",
    "timing_score", "fit_score", "buyer_clarity_score", "evidence_quality_score",
    "why_now", "budget_evidence", "budget_interpretation", "programs_or_campaigns",
    "buyer_or_route", "collaboration_angle", "x_handle_candidate",
    "x_handle_resolution_status", "x_handle_status_detail", "x_relationship_level",
    "x_degree", "source_count", "official_source_count", "secondary_source_count",
    "first_party_partner_source_count",
    "source_coverage_status", "source_domains", "source_urls", "source_type",
    "surf_status", "risk", "normalization_status", "applied_penalties",
    "last_verified_at", "confidence", "fact_status", "evidence_contract_status",
]

UNIVERSE_CSV_FIELDS = [
    "universe_order", "company", "aliases", "cohort", "cohort_group", "cohort_rank",
    "global_rank", "score_value", "score_method", "score_comparison_group",
    "score_comparable_within_group", "score_comparable_across_groups", "priority_tier",
    "category", "geography", "spend_evidence_level", "spend_mechanism_level",
    "x_handle_candidate", "x_handle_resolution_status", "x_handle_status_detail",
    "x_relationship_level", "x_degree", "source_count", "official_source_count",
    "secondary_source_count", "first_party_partner_source_count", "source_coverage_status", "source_domains", "source_urls",
    "source_type", "surf_status", "why_now", "budget_evidence", "buyer_or_route",
    "recommended_next_action", "risk",
    "last_verified_at", "confidence", "fact_status", "evidence_contract_status",
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


def source_urls(record: dict[str, Any]) -> list[str]:
    sources = record.get("sources") or record.get("evidence_urls") or []
    urls: list[str] = []
    for source in sources:
        value = source.get("url") if isinstance(source, dict) else source
        if value and str(value) not in urls:
            urls.append(str(value))
    return urls


def source_domain(url: str) -> str:
    domain = urlparse(url).netloc.lower().split(":", 1)[0]
    return domain[4:] if domain.startswith("www.") else domain


def source_coverage(urls: list[str]) -> dict[str, Any]:
    domains = sorted({source_domain(url) for url in urls if source_domain(url)})
    secondary = sum(
        any(domain == known or domain.endswith(f".{known}") for known in SECONDARY_SOURCE_DOMAINS)
        for domain in (source_domain(url) for url in urls)
    )
    first_party_partner = sum(
        any(domain == known or domain.endswith(f".{known}") for known in FIRST_PARTY_PARTNER_DOMAINS)
        for domain in (source_domain(url) for url in urls)
    )
    official = len(urls) - secondary - first_party_partner
    if not urls:
        status = "missing"
    elif official == 0 and first_party_partner == 0:
        status = "secondary_only"
    elif len(urls) == 1 and official:
        status = "single_official_source"
    elif len(urls) == 1:
        status = "single_first_party_partner_source"
    else:
        status = "multi_source_with_official_or_first_party_partner"
    return {
        "source_count": len(urls),
        "official_source_count": official,
        "secondary_source_count": secondary,
        "first_party_partner_source_count": first_party_partner,
        "source_domains": domains,
        "source_coverage_status": status,
    }


def combined_text(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False).lower()


def infer_spend_level(record: dict[str, Any]) -> str:
    allowed = {
        "repeated_paid_campaign", "active_creator_or_partner_budget", "formal_paid_program",
        "affiliate_or_referral_only", "funding_or_token_value_only", "unknown",
    }
    explicit = record.get("spend_mechanism_level")
    if explicit in allowed:
        return str(explicit)
    if explicit in EXPLICIT_SPEND_MAP:
        return EXPLICIT_SPEND_MAP[str(explicit)]
    text = combined_text(record)
    if any(term in text for term in ("repeated paid", "repeat campaign", "multi-creator paid")):
        return "repeated_paid_campaign"
    if any(term in text for term in ("cash campaign", "paid creator", "paid campaign", "cash pool", "usdc payout")):
        return "active_creator_or_partner_budget"
    if any(term in text for term in ("creator program", "accelerator", "grant", "hackathon", "partner program", "ambassador", "co-marketing")):
        return "formal_paid_program"
    if any(term in text for term in ("affiliate", "referral", "token reward", "token incentive", "yapper", "airdrop")):
        return "affiliate_or_referral_only"
    if any(term in text for term in ("funding", "raised", "financing", "tvl", "tokenomics", "cloud credits")):
        return "funding_or_token_value_only"
    return "unknown"


def spend_evidence(record: dict[str, Any], normalized_level: str) -> tuple[str, str]:
    explicit = str(record.get("spend_mechanism_level") or "")
    if explicit.startswith(("L1_", "L2_", "L3_")):
        return explicit[:2], explicit
    if normalized_level in {"repeated_paid_campaign", "active_creator_or_partner_budget"}:
        return "L3", f"inferred_{normalized_level}"
    if normalized_level in {"formal_paid_program", "affiliate_or_referral_only"}:
        return "L2", f"inferred_{normalized_level}"
    return "L1", f"inferred_{normalized_level}"


def infer_timing(record: dict[str, Any]) -> int:
    # Funding amount and funding date are intentionally excluded from scoring.
    timing_text = " ".join(str(record.get(field) or "") for field in (
        "why_now", "live_metric", "metric_as_of", "programs_or_campaigns",
    )).lower()
    if "2026" in timing_text:
        return 88
    if "2025" in timing_text:
        return 78
    if "2024" in timing_text:
        return 64
    return 60


def infer_fit(record: dict[str, Any]) -> int:
    category = str(record.get("category", "")).lower()
    if any(term in category for term in ("creator", "video", "ugc", "design", "consumer", "voice")):
        return 92
    if any(term in category for term in ("agent", "developer", "automation", "app builder")):
        return 84
    if any(term in category for term in ("data", "compute", "inference", "model", "3d")):
        return 74
    return 70


def infer_buyer_clarity(record: dict[str, Any]) -> int:
    route = record.get("buyer_or_route") or record.get("public_route") or ""
    if isinstance(route, list):
        route = " ".join(str(value) for value in route)
    route_text = str(route).lower()
    if re.search(r"\b(head|vp|director|manager|founder)\b", route_text):
        return 82
    if any(term in route_text for term in ("creator", "partner", "accelerator", "affiliate", "referral")):
        return 68
    if any(term in route_text for term in ("contact", "apply", "form", "email", "sales", "@")):
        return 58
    return 40


def parse_public_x_handle(record: dict[str, Any]) -> tuple[str, str, str]:
    raw = str(record.get("x_handle_candidate") or record.get("x_handle") or "").strip()
    if not raw:
        return "", "missing", "missing_requires_search"
    match = re.search(r"@([A-Za-z0-9_]{1,15})", raw)
    if not match:
        return "", "verify", "malformed_public_candidate_requires_verification"
    handle = f"@{match.group(1)}"
    if "verify" in raw.lower():
        return handle, "verify", "public_candidate_requires_rapid_x_verification"
    return handle, "resolved", "public_candidate_recorded_not_graph_audited"


def x_relationship_level(x_degree: str) -> str:
    degree = x_degree.lower()
    if degree in {"primary", "direct", "first", "1st", "l1"}:
        return "L1"
    if degree in {"secondary", "second", "2nd", "l2"}:
        return "L2"
    if degree in {"third", "tertiary", "3rd", "l3"}:
        return "L3"
    return "unknown"


def identity_keys(company: str, aliases: list[str]) -> list[str]:
    keys: list[str] = []
    for value in [company, *aliases]:
        key = re.sub(r"[^a-z0-9]", "", value.lower())
        if key and key not in keys:
            keys.append(key)
    return keys


def normalize_new(record: dict[str, Any], cohort: str, origin: str, config: dict[str, Any]) -> dict[str, Any]:
    urls = source_urls(record)
    coverage = source_coverage(urls)
    aliases = [str(value) for value in record.get("aliases", [])]
    x_handle, handle_status, handle_detail = parse_public_x_handle(record)
    buyer_route = record.get("buyer_or_route") or record.get("public_route") or ""
    reachability_level = "public_business_route" if buyer_route else "unresolved"
    spend_level = infer_spend_level(record)
    spend_tier, spend_subtype = spend_evidence(record, spend_level)
    evidence_quality = min(
        95,
        55
        + coverage["official_source_count"] * 10
        + coverage["first_party_partner_source_count"] * 8
        + coverage["secondary_source_count"] * 4,
    )
    penalties = []
    if handle_status != "resolved":
        penalties.append("identity_unresolved")
    if spend_level == "funding_or_token_value_only":
        penalties.append("funding_only_budget_claim")
    candidate = {
        "company": record["company"],
        "aliases": aliases,
        "company_identity_keys": identity_keys(record["company"], aliases),
        "category": record.get("category", ""),
        "geography": record.get("geography", "global_or_unknown"),
        "cohort": cohort,
        "cohort_group": "new_v4",
        "discovery_origin": origin,
        "score_method": V4_SCORE_METHOD,
        "score_comparison_group": V4_SCORE_GROUP,
        "score_comparable_within_group": True,
        "score_comparable_across_groups": False,
        "global_rank": None,
        "why_now": record.get("why_now") or record.get("why_longtail") or "",
        "budget_evidence": record.get("budget_evidence") or record.get("funding_signal") or "",
        "budget_interpretation": record.get("budget_interpretation", ""),
        "programs_or_campaigns": record.get("programs_or_campaigns") or record.get("live_metric") or "",
        "buyer_or_route": buyer_route,
        "collaboration_angle": record.get("collaboration_angle", ""),
        "x_handle_candidate": x_handle,
        "x_handle_resolution_status": handle_status,
        "x_handle_status": handle_status,
        "x_handle_status_detail": handle_detail,
        "x_relationship_level": "unknown",
        "x_degree": "not_audited",
        "reachability_level": reachability_level,
        "spend_evidence_level": spend_tier,
        "spend_evidence_subtype": spend_subtype,
        "spend_mechanism_level": spend_level,
        "timing_score": infer_timing(record),
        "fit_score": infer_fit(record),
        "buyer_clarity_score": infer_buyer_clarity(record),
        "evidence_quality_score": evidence_quality,
        "penalties": penalties,
        "source_urls": urls,
        **coverage,
        "source_type": record.get("source_type", "web_research"),
        "funding_source_type": record.get("funding_source_type", "not_separately_classified"),
        "surf_status": record.get("surf_status", "not_used_for_non_crypto_candidate"),
        "risk": record.get("risk", ""),
        "last_verified_at": record.get("last_verified_at"),
        "confidence": record.get("confidence") or record.get("overall_confidence"),
        "fact_status": record.get("fact_status") or "unverified",
        "evidence_contract_status": record.get("evidence_contract_status") or "not_migrated",
        "evidence_records": record.get("sources") or record.get("evidence") or [],
        "normalization_status": "method_inferred_requires_bd_review",
        "fame_score_used": False,
        "funding_score_used": False,
        "input_bd_score": record.get("bd_score"),
        "input_bd_score_used": False,
        "score_basis_note": "Fame, follower count, funding amount, token value, and TVL add zero points.",
        "raw_record": record,
    }
    return score_candidate(candidate, config)


def extract_v3_target_handle(record: dict[str, Any]) -> str:
    path = record.get("x_primary_path") or []
    if not path:
        return ""
    matches = re.findall(r"@([A-Za-z0-9_]{1,15})", str(path[-1]))
    return f"@{matches[-1]}" if matches else ""


def normalize_existing(record: dict[str, Any]) -> dict[str, Any]:
    profile_coverage = str(record.get("x_profile_coverage") or "")
    resolved = profile_coverage == "resolved_from_rapid_x_cache"
    handle = extract_v3_target_handle(record)
    if resolved:
        handle_status = "resolved"
        handle_detail = "resolved_from_rapid_x_cache" if handle else "resolved_from_rapid_x_cache_handle_not_carried_in_ranking_artifact"
    else:
        handle_status = "missing"
        handle_detail = "missing_in_v3_ranking_requires_search"
    x_degree = str(record.get("x_degree") or "unresolved")
    score = float(record["overall_priority_solomon_x"])
    company = str(record["company"])
    return {
        "company": company,
        "aliases": [],
        "company_identity_keys": identity_keys(company, []),
        "category": "",
        "geography": "",
        "cohort": "existing_v3_ranked",
        "cohort_group": "existing_v3",
        "cohort_rank": int(record["rank"]),
        "global_rank": None,
        "score_value": score,
        "score_method": V3_SCORE_METHOD,
        "score_comparison_group": V3_SCORE_GROUP,
        "score_comparable_within_group": True,
        "score_comparable_across_groups": False,
        "priority_tier": record.get("priority_tier", ""),
        "overall_priority_solomon_x": score,
        "spend_evidence_level": "legacy_unmapped",
        "spend_mechanism_level": "legacy_budget_score_preserved",
        "x_handle_candidate": handle,
        "x_handle_resolution_status": handle_status,
        "x_handle_status": handle_status,
        "x_handle_status_detail": handle_detail,
        "x_relationship_level": x_relationship_level(x_degree),
        "x_degree": x_degree,
        "x_primary_path": record.get("x_primary_path", []),
        "x_alternative_path_count": record.get("x_alternative_path_count", 0),
        "x_profile_coverage": profile_coverage,
        "source_count": None,
        "official_source_count": None,
        "secondary_source_count": None,
        "first_party_partner_source_count": None,
        "source_domains": [],
        "source_urls": [],
        "source_coverage_status": "not_carried_in_v3_ranking_artifact",
        "source_type": "legacy_v3_ranking_artifact",
        "surf_status": "not_used_legacy_v3",
        "why_now": "",
        "budget_evidence": "",
        "buyer_or_route": "",
        "recommended_next_action": record.get("recommended_next_action", ""),
        "risk": record.get("note", ""),
        "last_verified_at": "2026-08-24",
        "confidence": "legacy_unmapped",
        "fact_status": "unverified",
        "evidence_contract_status": "legacy_ranking_sources_not_carried_requires_revalidation",
        "funding_score_used": None,
        "fame_score_used": None,
        "score_basis_note": "Legacy v3 score preserved without recomputation; do not compare it numerically with v4 scores.",
        "raw_record": record,
    }


def deduplicate_conservatively(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    owners: dict[str, str] = {}
    kept: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for row in rows:
        keys = row["company_identity_keys"]
        matched = sorted({owners[key] for key in keys if key in owners})
        if matched:
            duplicates.append({
                "company": row["company"],
                "matched_existing_companies": matched,
                "matched_identity_keys": [key for key in keys if key in owners],
            })
            continue
        kept.append(row)
        for key in keys:
            owners[key] = row["company"]
    return kept, duplicates


def universe_row_from_new(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "cohort_rank": row["rank"], "score_value": row["overall_priority"], "recommended_next_action": ""}


def count_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def score_range(rows: list[dict[str, Any]], field: str) -> dict[str, float]:
    values = [float(row[field]) for row in rows]
    return {"min": min(values), "max": max(values)}


def nested_counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(field)) for row in rows).items()))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    longtail_path = DATA / "longtail_ai_candidates.json"
    crypto_path = DATA / "crypto_ai_candidates.json"
    longtail_raw = json.loads(longtail_path.read_text(encoding="utf-8"))
    crypto_raw = json.loads(crypto_path.read_text(encoding="utf-8"))
    existing_raw = json.loads(EXISTING_V3.read_text(encoding="utf-8"))

    if len(existing_raw) != EXPECTED_EXISTING:
        raise ValueError(f"Expected {EXPECTED_EXISTING} existing records, found {len(existing_raw)}")
    if len(longtail_raw) != EXPECTED_LONGTAIL:
        raise ValueError(f"Expected {EXPECTED_LONGTAIL} long-tail records, found {len(longtail_raw)}")
    if len(crypto_raw) != EXPECTED_CRYPTO:
        raise ValueError(f"Expected {EXPECTED_CRYPTO} crypto-AI records, found {len(crypto_raw)}")

    new_rows = [
        *(normalize_new(record, "new_longtail_ai_v4", "longtail_ai_web", config) for record in longtail_raw),
        *(normalize_new(record, "new_crypto_ai_v4", "crypto_ai_public_sources_surf_blocked", config) for record in crypto_raw),
    ]
    new_rows.sort(key=lambda row: (-row["overall_priority"], row["company"].lower()))
    new_rows, new_duplicate_audit = deduplicate_conservatively(new_rows)
    for rank, row in enumerate(new_rows, 1):
        row["rank"] = rank

    existing_rows = [normalize_existing(record) for record in existing_raw]
    universe_rows, universe_duplicate_audit = deduplicate_conservatively([
        *existing_rows, *(universe_row_from_new(row) for row in new_rows),
    ])
    for order, row in enumerate(universe_rows, 1):
        row["universe_order"] = order

    if len(new_rows) != EXPECTED_NEW:
        raise ValueError(f"Expected {EXPECTED_NEW} deduplicated new records, found {len(new_rows)}")
    if len(universe_rows) != EXPECTED_UNIVERSE:
        raise ValueError(f"Expected {EXPECTED_UNIVERSE} universe records, found {len(universe_rows)}")
    if new_duplicate_audit or universe_duplicate_audit:
        raise ValueError(f"Unexpected conservative identity collision: new={new_duplicate_audit}, universe={universe_duplicate_audit}")

    new_json = OUT / "new_candidate_rankings_v4.json"
    new_csv = OUT / "new_candidate_rankings_v4.csv"
    universe_json = OUT / "all_company_universe_v4.json"
    universe_csv = OUT / "all_company_universe_v4.csv"
    frontier_json = DATA / "x_target_frontier.json"
    summary_json = OUT / "company_universe_summary.json"

    write_json(new_json, new_rows)
    write_csv(new_csv, new_rows, NEW_CSV_FIELDS)
    write_json(universe_json, universe_rows)
    write_csv(universe_csv, universe_rows, UNIVERSE_CSV_FIELDS)

    frontier = []
    for row in new_rows:
        action = {
            "resolved": "profile_lookup_then_graph_audit",
            "verify": "verify_candidate_handle_then_graph_audit",
            "missing": "search_and_manually_match_then_graph_audit",
        }[row["x_handle_resolution_status"]]
        frontier.append({
            "priority_rank": row["rank"],
            "company": row["company"],
            "aliases": row["aliases"],
            "handle": row["x_handle_candidate"],
            "handle_status": row["x_handle_resolution_status"],
            "handle_status_detail": row["x_handle_status_detail"],
            "x_relationship_level": "unknown",
            "x_degree": "not_audited",
            "resolution_action": action,
            "source_origin": row["discovery_origin"],
            "cohort": row["cohort"],
            "score_value": row["overall_priority"],
            "score_method": row["score_method"],
            "source_count": row["source_count"],
            "source_coverage_status": row["source_coverage_status"],
        })
    write_json(frontier_json, frontier)

    new_score_range = score_range(new_rows, "overall_priority")
    existing_score_range = score_range(existing_rows, "score_value")
    if not (0 <= new_score_range["min"] <= new_score_range["max"] <= 100):
        raise ValueError(f"v4 score out of range: {new_score_range}")
    if not (0 <= existing_score_range["min"] <= existing_score_range["max"] <= 100):
        raise ValueError(f"v3 score out of range: {existing_score_range}")
    surf_statuses = [str(row.get("surf_status", "")).lower() for row in universe_rows]
    if any(status == "verified" or status.startswith("surf_verified") for status in surf_statuses):
        raise ValueError("A record was incorrectly marked Surf-verified")

    json_counts = {
        str(new_json.relative_to(ROOT)): len(json.loads(new_json.read_text(encoding="utf-8"))),
        str(universe_json.relative_to(ROOT)): len(json.loads(universe_json.read_text(encoding="utf-8"))),
        str(frontier_json.relative_to(ROOT)): len(json.loads(frontier_json.read_text(encoding="utf-8"))),
    }
    csv_counts = {
        str(new_csv.relative_to(ROOT)): count_csv_rows(new_csv),
        str(universe_csv.relative_to(ROOT)): count_csv_rows(universe_csv),
    }
    expected_json_counts = {
        str(new_json.relative_to(ROOT)): EXPECTED_NEW,
        str(universe_json.relative_to(ROOT)): EXPECTED_UNIVERSE,
        str(frontier_json.relative_to(ROOT)): EXPECTED_NEW,
    }
    expected_csv_counts = {
        str(new_csv.relative_to(ROOT)): EXPECTED_NEW,
        str(universe_csv.relative_to(ROOT)): EXPECTED_UNIVERSE,
    }
    if json_counts != expected_json_counts:
        raise ValueError(f"JSON row-count mismatch: {json_counts}")
    if csv_counts != expected_csv_counts:
        raise ValueError(f"CSV row-count mismatch: {csv_counts}")

    source_covered_new = [row for row in new_rows if row["source_count"] > 0]
    summary = {
        "schema_version": "v4",
        "generated_date": date.today().isoformat(),
        "counts": {
            "existing_v3": len(existing_rows),
            "new_longtail_ai_v4": sum(row["cohort"] == "new_longtail_ai_v4" for row in new_rows),
            "new_crypto_ai_v4": sum(row["cohort"] == "new_crypto_ai_v4" for row in new_rows),
            "new_total": len(new_rows),
            "all_company_universe": len(universe_rows),
        },
        "deduplication": {
            "method": "normalized exact company names plus explicit aliases only; slash-separated product labels were not split",
            "new_identity_collisions": new_duplicate_audit,
            "universe_identity_collisions": universe_duplicate_audit,
            "duplicate_count": 0,
        },
        "score_comparability": {
            "global_rank_created": False,
            "reason": "The preserved v3 Solomon-X score and the v4 evidence-routing score use different methods and are not numerically comparable.",
            "groups": {
                V3_SCORE_GROUP: {
                    "method": V3_SCORE_METHOD,
                    "records": len(existing_rows),
                    "score_range": existing_score_range,
                    "treatment": "preserved_without_recomputation",
                },
                V4_SCORE_GROUP: {
                    "method": V4_SCORE_METHOD,
                    "records": len(new_rows),
                    "score_range": new_score_range,
                    "treatment": "fame, follower count, funding amount, token value, and TVL add zero points",
                },
            },
        },
        "relationship_levels": {
            "semantic_scope": "pre_graph_baseline_preserving_legacy_v3_labels_and_marking_new_targets_unknown",
            "definitions": {
                "L1": "direct company or operator adjacency",
                "L2": "one intermediary, equivalent to secondary",
                "L3": "two intermediaries, equivalent to third-degree",
                "unknown": "not audited, unresolved, or no path in the audited graph",
            },
            "all_universe_counts": nested_counts(universe_rows, "x_relationship_level"),
            "note": "These counts are the pre-collection universe baseline, not the current Rapid X graph result. See company_master_summary_v4.json, x_graph_summary_v4.json, and mango_seed_target_paths.json for post-adjudication reachability.",
        },
        "spend_evidence_levels": {
            "definitions": {
                "L1": "capacity or financing signal only; no public committed program spend",
                "L2": "committed program, channel, in-kind support, co-sell, or planned revenue share",
                "L3": "explicit cash, commission, or active creator/partner budget",
                "legacy_unmapped": "v3 budget score preserved; insufficient fields in the ranking artifact for conservative remapping",
            },
            "new_candidate_counts": nested_counts(new_rows, "spend_evidence_level"),
            "all_universe_counts": nested_counts(universe_rows, "spend_evidence_level"),
        },
        "x_handle_resolution": {
            "semantic_scope": "pre_collection_source_stage_handle_candidates_not_post_rapid_x_identity_results",
            "new_candidate_counts": nested_counts(new_rows, "x_handle_resolution_status"),
            "all_universe_counts": nested_counts(universe_rows, "x_handle_resolution_status"),
            "note": "These are source-stage handle candidate states before the Rapid X collection/adjudication pass. They are not post-collection technical identity counts. See x_graph_summary_v4.json and x_target_identities_v4.json for the current Rapid X result.",
        },
        "source_coverage": {
            "new_candidates_with_at_least_one_source": len(source_covered_new),
            "new_candidate_rate": round(len(source_covered_new) / len(new_rows), 4),
            "new_candidate_status_counts": nested_counts(new_rows, "source_coverage_status"),
            "all_universe_status_counts": nested_counts(universe_rows, "source_coverage_status"),
            "existing_v3_note": "The v3 ranking artifact does not carry source URLs; those rows are marked not_carried_in_v3_ranking_artifact rather than zero-evidence.",
        },
        "surf": {
            "verified_records": 0,
            "status": "not_used_for_verification_paid_balance_zero",
            "note": "Public-source crypto-AI records remain explicitly unverified by Surf; no Surf query was made by this builder.",
        },
        "validation": {
            "json_data_rows": json_counts,
            "csv_data_rows_excluding_header": csv_counts,
            "duplicate_check": "passed_zero_conservative_identity_collisions",
            "score_range_check": "passed_all_scores_between_0_and_100",
            "surf_verification_check": "passed_zero_records_marked_verified",
        },
        "outputs": [
            str(new_csv.relative_to(ROOT)),
            str(new_json.relative_to(ROOT)),
            str(universe_csv.relative_to(ROOT)),
            str(universe_json.relative_to(ROOT)),
            str(frontier_json.relative_to(ROOT)),
            str(summary_json.relative_to(ROOT)),
        ],
    }
    write_json(summary_json, summary)
    json.loads(summary_json.read_text(encoding="utf-8"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
