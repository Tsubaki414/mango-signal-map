#!/usr/bin/env python3
"""Verify public operator candidates with exact Rapid X profile lookups.

The input is a research-candidate artifact, not a list of asserted decision
makers.  A person is confirmed only when all four deterministic gates pass:

1. a current role is supported by an official/company-controlled source;
2. a candidate X handle is recorded with public provenance;
3. Rapid X returns exactly that handle; and
4. the returned profile deterministically matches the person's name and the
   company association (profile text/domain or an official source naming the
   exact handle).

No fuzzy matching, LinkedIn evidence, or alternate X data source is used.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from mangobd.rapid_x import RapidXClient
from mangobd.x_network import find_user_objects


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "data" / "pilot_v4" / "operator_research_candidates_v4.json"
CACHE = ROOT / "data" / "cache" / "rapidx" / "v4" / "operator-identities"
OUTPUT = ROOT / "outputs" / "pilot_v4" / "operator_x_identity_resolutions_v4.json"

OFFICIAL_SOURCE_PREFIXES = (
    "official_company_", "company_controlled_", "first_party_company_",
    "official_project_",
)
SECONDARY_HOSTS = {
    "linkedin.com", "techcrunch.com", "crunchbase.com", "forbes.com", "bloomberg.com",
    "businesswire.com", "prnewswire.com", "medium.com", "substack.com", "youtube.com",
}
GENERIC_COMPANY_TOKENS = {
    "ai", "labs", "lab", "network", "company", "official", "team", "blog", "app",
    "www", "com", "org", "net", "io", "co", "inc", "the",
}
SHORT_ROLE_TOKENS = {"ceo", "cmo", "cro", "cso", "cto", "cbo", "vp", "bd"}
NON_WRITEABLE_STATUS_MARKERS = (
    "first_name_only", "surname unresolved", "name unresolved", "not_writeable", "not writeable",
    "title_incomplete", "historical", "current_role_revalidation", "current-role revalidation",
    "alias", "full_identity_required", "full identity required", "issuer_pending",
)


def normalize_handle(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def normalize_text(value: Any) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def word_tokens(value: Any) -> list[str]:
    return re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE)


def explicit_role_categories(value: Any) -> set[str]:
    """Return only titles that are explicitly asserted, never activity keywords.

    A post mentioning a partnership, distribution, or a financing series does
    not by itself establish that its author is a partnerships operator or a
    founder.  These patterns intentionally require title-shaped language.
    """
    text = str(value or "").casefold().replace("–", "-").replace("—", "-")
    categories: set[str] = set()
    patterns = {
        "founder": r"\b(?:co[ -]?)?founder\b",
        "chief_executive": r"\b(?:co[ -]?)?(?:ceo|chief executive officer)\b",
        "chief_officer": r"\bchief\s+[a-z][a-z -]{1,40}\s+officer\b",
        "head": r"\bhead\s+of\b",
        "director": r"\b(?:senior\s+)?director\s+of\b",
        "vice_president": r"\b(?:vp|vice president)\b",
        "lead": r"\b(?:[a-z][a-z -]{1,40}\s+lead|leads?\s+(?:the\s+)?)\b",
        "managing_director": r"\bmanaging director\b",
    }
    for category, pattern in patterns.items():
        if re.search(pattern, text):
            categories.add(category)
    return categories


def safe_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "operator"


def file_observed_at(path: Path, fallback: str) -> str:
    if not path.exists():
        return fallback
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def list_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("companies", "operator_candidates", "records"):
            if isinstance(payload.get(key), list):
                return payload[key]
    raise ValueError("Operator candidate artifact must be a list or contain a supported list key")


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
    values = (
        row.get("operator_evidence") or row.get("official_role_evidence")
        or row.get("evidence") or []
    )
    normalized = []
    for item in values:
        if not isinstance(item, dict):
            continue
        normalized.append({
            **item,
            "claim": item.get("claim") or item.get("exact_claim") or item.get("evidence_text") or "",
        })
    return normalized


def is_official_evidence(item: dict[str, Any], row: dict[str, Any] | None = None) -> bool:
    source_type = str(item.get("source_type") or item.get("source_class") or "").casefold().replace("-", "_")
    url = str(item.get("url") or "")
    host_match = re.match(r"https?://(?:www\.)?([^/:?#]+)", url, flags=re.I)
    host = host_match.group(1).casefold() if host_match else ""
    blocked_secondary = any(host == blocked or host.endswith(f".{blocked}") for blocked in SECONDARY_HOSTS)
    explicit_company_x = source_type.startswith(("official_company_x_", "company_controlled_x_")) and host in {"x.com", "twitter.com"}
    explicit_company_youtube = source_type.startswith("company_controlled_youtube_") and host in {
        "youtube.com", "youtu.be",
    }
    allowed_domains = {
        str(value or "").casefold().removeprefix("https://").removeprefix("http://").split("/", 1)[0].removeprefix("www.")
        for value in (
            *((row or {}).get("official_domains") or []),
            (row or {}).get("official_domain"),
        )
        if value
    }
    controlled_host = any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)
    return bool(
        source_type.startswith(OFFICIAL_SOURCE_PREFIXES)
        and url.startswith(("https://", "http://"))
        and "linkedin" not in source_type
        and (explicit_company_x or explicit_company_youtube or (controlled_host and not blocked_secondary))
    )


def official_role_evidence(row: dict[str, Any], name: str, role: str) -> list[dict[str, Any]]:
    name_key = normalize_text(name)
    role_categories = explicit_role_categories(role)
    matches: list[dict[str, Any]] = []
    for item in evidence_rows(row):
        if not is_official_evidence(item, row):
            continue
        fact_status = str(item.get("fact_status") or "").casefold()
        if (
            not fact_status.startswith("confirmed")
            or any(marker in fact_status for marker in (
                "historical", "requires_current_revalidation", "stale", "unverified", "probable",
            ))
        ):
            continue
        claim = str(item.get("claim") or item.get("evidence_text") or "")
        claim_categories = explicit_role_categories(claim)
        if (
            name_key
            and role_categories
            and person_name_matches(name, claim)
            and bool(role_categories & claim_categories)
        ):
            matches.append(item)
    return matches


def official_handle_evidence(row: dict[str, Any], handle: str) -> list[dict[str, Any]]:
    key = normalize_handle(handle)
    matches: list[dict[str, Any]] = []
    if not key:
        return matches
    for item in evidence_rows(row):
        if not is_official_evidence(item, row):
            continue
        blob = " ".join(str(item.get(field) or "") for field in ("claim", "evidence_text", "url"))
        if re.search(rf"(?<![a-z0-9_])@?{re.escape(key)}(?![a-z0-9_])", blob, flags=re.I):
            matches.append(item)
    return matches


def normalize_user(user: dict[str, Any]) -> dict[str, Any]:
    legacy = user.get("legacy") if isinstance(user.get("legacy"), dict) else {}
    core = user.get("core") if isinstance(user.get("core"), dict) else {}
    entities = legacy.get("entities") if isinstance(legacy.get("entities"), dict) else {}
    url_entity = entities.get("url") if isinstance(entities.get("url"), dict) else {}
    urls = url_entity.get("urls") if isinstance(url_entity.get("urls"), list) else []
    expanded_urls = [
        str(item.get("expanded_url") or item.get("url") or "")
        for item in urls if isinstance(item, dict)
    ]
    rest_id = user.get("rest_id") or user.get("id_str") or user.get("id")
    return {
        "rest_id": str(rest_id) if rest_id is not None else "",
        "handle": str(user.get("screen_name") or legacy.get("screen_name") or core.get("screen_name") or ""),
        "name": str(user.get("name") or legacy.get("name") or core.get("name") or ""),
        "description": str(user.get("description") or legacy.get("description") or ""),
        "location": str(user.get("location") or legacy.get("location") or ""),
        "expanded_urls": expanded_urls,
        "followers_count": user.get("followers_count", legacy.get("followers_count")),
        "following_count": user.get("following_count", user.get("friends_count", legacy.get("friends_count"))),
        "verified": bool(user.get("verified") or user.get("is_blue_verified") or legacy.get("verified")),
    }


def response_users(payload: Any) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for user in find_user_objects(payload):
        normalized = normalize_user(user)
        if normalized["rest_id"]:
            by_id[normalized["rest_id"]] = normalized
    return sorted(by_id.values(), key=lambda item: (normalize_handle(item["handle"]), item["rest_id"]))


def person_name_matches(candidate_name: str, profile_name: str) -> bool:
    """Deterministic name matching; deliberately no edit distance or fuzzy rules."""
    candidate_tokens = word_tokens(candidate_name)
    profile_tokens = word_tokens(profile_name)
    if len(candidate_tokens) < 2:
        # A single first name cannot uniquely identify an operator.
        return False
    candidate_key = normalize_text(candidate_name)
    profile_key = normalize_text(profile_name)
    if candidate_key == profile_key:
        return True
    # Allow middle names/initials and profile suffixes while requiring the exact
    # first and last candidate tokens in order.
    try:
        positions = [profile_tokens.index(candidate_tokens[0])]
        start = positions[0] + 1
        for token in candidate_tokens[1:]:
            position = profile_tokens.index(token, start)
            positions.append(position)
            start = position + 1
        return bool(positions)
    except ValueError:
        return False


def company_terms(row: dict[str, Any]) -> set[str]:
    values: Iterable[Any] = [row.get("company"), *(row.get("company_aliases") or [])]
    terms: set[str] = set()
    for value in values:
        full = normalize_text(value)
        if len(full) >= 3 and full not in GENERIC_COMPANY_TOKENS:
            terms.add(full)
        terms.update(
            token for token in word_tokens(value)
            if len(token) >= 3 and token not in GENERIC_COMPANY_TOKENS
        )
    for value in (row.get("official_domain"), *(row.get("official_domains") or [])):
        host = str(value or "").casefold().removeprefix("https://").removeprefix("http://").split("/", 1)[0]
        labels = [part for part in host.split(".") if part and part not in GENERIC_COMPANY_TOKENS]
        if labels:
            term = normalize_text(labels[-2] if len(labels) >= 2 else labels[0])
            if len(term) >= 3:
                terms.add(term)
    return terms


def profile_company_matches(row: dict[str, Any], profile: dict[str, Any]) -> bool:
    blob = " ".join([
        str(profile.get("description") or ""),
        str(profile.get("location") or ""),
        *[str(value) for value in profile.get("expanded_urls") or []],
    ])
    blob_tokens = set(word_tokens(blob))
    normalized_tokens = {normalize_text(token) for token in blob_tokens}
    compact_mentions = {
        normalize_text(value)
        for value in re.findall(r"@[A-Za-z0-9_]+|https?://[^\s]+", blob, flags=re.I)
    }
    return any(term in normalized_tokens or term in compact_mentions for term in company_terms(row))


def resolution_record(
    row: dict[str, Any], payload: Any, cache_file: str, verified_at: str,
) -> dict[str, Any]:
    candidate = candidate_block(row)
    name = str(candidate.get("name") or "").strip()
    role = str(candidate.get("role") or "").strip()
    handle = str(candidate.get("x_handle") or row.get("candidate_x_handle") or row.get("x_handle") or "").strip()
    exact_handle = normalize_handle(handle)
    profiles = response_users(payload)
    exact = [profile for profile in profiles if normalize_handle(profile["handle"]) == exact_handle]
    role_evidence = official_role_evidence(row, name, role)
    handle_evidence = official_handle_evidence(row, handle)
    profile = exact[0] if len(exact) == 1 else None
    exact_full_name_match = bool(profile and person_name_matches(name, profile["name"]))
    candidate_name_tokens = word_tokens(name)
    profile_name_tokens = word_tokens(profile.get("name") if profile else "")
    official_tie_abbreviated_name_match = bool(
        profile and handle_evidence and len(candidate_name_tokens) >= 2
        and profile_name_tokens and profile_name_tokens[0] == candidate_name_tokens[0]
    )
    gates = {
        "candidate_status_writeable": candidate_is_writeable(row),
        "official_current_role_evidence": bool(role_evidence),
        "candidate_x_handle_present": bool(exact_handle),
        "rapid_x_exact_handle": len(exact) == 1,
        "deterministic_person_identity_match": exact_full_name_match or official_tie_abbreviated_name_match,
        "company_association": bool(profile and (profile_company_matches(row, profile) or handle_evidence)),
        "non_linkedin_evidence_only": all("linkedin" not in json.dumps(item).casefold() for item in evidence_rows(row)),
    }
    confirmed = all(gates.values())
    failed = [key for key, passed in gates.items() if not passed]
    if confirmed:
        status = "confirmed_official_role_and_rapid_x_exact_profile"
    elif not name:
        status = "unresolved_no_named_candidate"
    elif not exact_handle:
        status = "unresolved_no_candidate_x_handle"
    elif len(exact) == 0:
        status = "unresolved_rapid_x_handle_mismatch_or_empty"
    elif len(exact) > 1:
        status = "unresolved_multiple_exact_profiles"
    else:
        status = "unresolved_identity_gate_failed"
    return {
        "company": row.get("company"),
        "candidate_operator": {**candidate, "name": name or None, "role": role or None, "x_handle": handle or None},
        "confirmed": confirmed,
        "status": status,
        "failed_gates": failed,
        "gates": gates,
        "matching_details": {
            "exact_full_name_match": exact_full_name_match,
            "official_handle_tie_resolves_abbreviated_profile_name": official_tie_abbreviated_name_match,
        },
        "returned_profile": profile,
        "rapid_x_candidate_count": len(profiles),
        "rapid_x_candidate_handles": [f"@{item['handle']}" for item in profiles if item["handle"]],
        "official_role_evidence_urls": [item.get("url") for item in role_evidence],
        "official_role_evidence_ids": [
            item.get("evidence_id") or item.get("url") for item in role_evidence
        ],
        "official_handle_evidence_urls": [item.get("url") for item in handle_evidence],
        "cache_file": cache_file,
        "official_role_verified_at": max(
            [str(item.get("last_verified_at") or item.get("date") or "") for item in role_evidence]
            or [str(row.get("last_verified_at") or "")]
        ),
        "rapid_x_profile_observed_at": verified_at,
        "last_verified_at": verified_at,
        "evidence_contract": (
            "Named operator requires official/company current-role evidence plus an exact Rapid X "
            "profile, deterministic person-name match, and deterministic company association."
        ),
    }


def failure_without_request(row: dict[str, Any], status: str, verified_at: str, note: str = "") -> dict[str, Any]:
    candidate = candidate_block(row)
    if status == "unresolved_candidate_name_not_strictly_writeable":
        failed_gates = ["candidate_status_writeable"]
    elif not (candidate.get("x_handle") or row.get("candidate_x_handle")):
        failed_gates = ["candidate_x_handle_present"]
    else:
        failed_gates = ["rapid_x_exact_handle"]
    return {
        "company": row.get("company"),
        "candidate_operator": candidate,
        "confirmed": False,
        "status": status,
        "failed_gates": failed_gates,
        "gates": {"candidate_status_writeable": False} if "candidate_status_writeable" in failed_gates else {},
        "returned_profile": None,
        "rapid_x_candidate_count": 0,
        "rapid_x_candidate_handles": [],
        "official_role_evidence_urls": [],
        "official_handle_evidence_urls": [],
        "cache_file": "",
        "last_verified_at": verified_at,
        "notes": note,
        "evidence_contract": "No named operator is written until every identity gate passes.",
    }


def row_with_candidate_handle(row: dict[str, Any], handle: str) -> dict[str, Any]:
    enriched = dict(row)
    for key in ("candidate_operator", "operator_candidate", "recommended_operator"):
        if isinstance(row.get(key), dict):
            enriched[key] = {**row[key], "x_handle": handle}
            return enriched
    enriched["candidate_x_handle"] = handle
    return enriched


def candidate_is_writeable(row: dict[str, Any]) -> bool:
    candidate = candidate_block(row)
    name = str(candidate.get("name") or "").strip()
    status_blob = " ".join([
        str(row.get("status") or ""), str(row.get("x_verification_status") or ""), name,
    ]).casefold()
    return bool(
        len(word_tokens(name)) >= 2
        and not any(marker in status_blob for marker in NON_WRITEABLE_STATUS_MARKERS)
    )


def discover_exact_handle(
    client: RapidXClient, row: dict[str, Any], *, force: bool,
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Discover one handle only from exact full-name + company-profile matches.

    Search is discovery, never confirmation.  The returned handle must still be
    sent through ``get_user`` and every identity gate in ``resolution_record``.
    """
    candidate = candidate_block(row)
    name = str(candidate.get("name") or "").strip()
    company = str(row.get("company") or "").strip()
    if not name or len(word_tokens(name)) < 2:
        return "", {}, []
    query = f'"{name}" "{company}"'
    payload = client.search(
        query, search_type="Top", count=40, force=force, cache_ttl_seconds=86_400 * 30,
    )
    profiles = response_users(payload)
    matches = [
        profile for profile in profiles
        if profile.get("handle")
        and person_name_matches(name, profile.get("name") or "")
        and profile_company_matches(row, profile)
    ]
    unique_handles = {normalize_handle(profile["handle"]): profile for profile in matches}
    if len(unique_handles) != 1:
        return "", payload, matches
    profile = next(iter(unique_handles.values()))
    return str(profile["handle"]), payload, matches


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    rows = list_rows(json.loads(args.input.read_text(encoding="utf-8")))
    CACHE.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    client = RapidXClient(cache_dir=CACHE / "requests", env_file=args.env_file)
    resolutions: list[dict[str, Any]] = []
    seen_companies: set[str] = set()
    for index, row in enumerate(rows, 1):
        company = str(row.get("company") or "").strip()
        key = normalize_text(company)
        if not key or key in seen_companies:
            raise ValueError(f"Missing or duplicate company at input row {index}: {company!r}")
        seen_companies.add(key)
        candidate = candidate_block(row)
        name = str(candidate.get("name") or "").strip()
        handle = str(candidate.get("x_handle") or row.get("candidate_x_handle") or row.get("x_handle") or "").strip()
        evaluated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        if not name or not candidate_is_writeable(row):
            failure = failure_without_request(
                row,
                "unresolved_no_named_candidate" if not name else "unresolved_candidate_name_not_strictly_writeable",
                evaluated_at,
            )
            failure["evaluated_at"] = evaluated_at
            resolutions.append(failure)
            continue
        discovery: dict[str, Any] = {
            "attempted": False, "query_kind": "rapid_x_search_exact_name_and_company",
            "selected_handle": "", "exact_name_company_match_count": 0, "cache_file": "",
        }
        if not handle:
            discovery["attempted"] = True
            discovery_cache = CACHE / f"{index:02d}-{safe_slug(company)}-handle-discovery.json"
            try:
                handle, discovery_payload, discovery_matches = discover_exact_handle(
                    client, row, force=args.force,
                )
                discovery_cache.write_text(json.dumps({
                    "request": {"company": company, "candidate_name": name},
                    "result_type": "operator_handle_discovery_not_identity_confirmation",
                    "evaluated_at": evaluated_at,
                    "response": discovery_payload,
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                discovery.update({
                    "selected_handle": handle,
                    "exact_name_company_match_count": len({normalize_handle(item["handle"]) for item in discovery_matches}),
                    "candidate_handles": [f"@{item['handle']}" for item in discovery_matches],
                    "cache_file": str(discovery_cache.relative_to(ROOT)),
                    "selection_rule": "unique deterministic exact full-name + company-profile association; final exact profile lookup still required",
                })
            except Exception as exc:
                discovery["error"] = str(exc)
            if not handle:
                failure = failure_without_request(row, "unresolved_no_unique_deterministic_x_handle", evaluated_at)
                failure["handle_discovery"] = discovery
                failure["evaluated_at"] = evaluated_at
                resolutions.append(failure)
                continue
            row = row_with_candidate_handle(row, handle)
        cache_path = CACHE / f"{index:02d}-{safe_slug(company)}-{safe_slug(handle)}.json"
        try:
            rapid_request_cache = client._cache_path("user", {"username": handle.lstrip("@")})
            payload = client.get_user(handle, force=args.force, cache_ttl_seconds=86_400 * 30)
            observed_at = file_observed_at(rapid_request_cache, evaluated_at)
            cache_path.write_text(json.dumps({
                "request": {"company": company, "candidate_name": name, "handle": handle},
                "result_type": "operator_exact_profile_lookup",
                "rapid_x_response_observed_at": observed_at,
                "evaluated_at": evaluated_at,
                "rapid_x_request_cache_file": str(rapid_request_cache.relative_to(ROOT)),
                "response": payload,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result = resolution_record(row, payload, str(cache_path.relative_to(ROOT)), observed_at)
            result["handle_discovery"] = discovery
            result["rapid_x_request_cache_file"] = str(rapid_request_cache.relative_to(ROOT))
            result["evaluated_at"] = evaluated_at
            resolutions.append(result)
        except Exception as exc:
            failure = failure_without_request(row, "rapid_x_lookup_failed", evaluated_at, str(exc))
            failure["handle_discovery"] = discovery
            failure["evaluated_at"] = evaluated_at
            resolutions.append(failure)

    counts = Counter(row["status"] for row in resolutions)
    artifact = {
        "schema_version": "operator-x-identity-resolution-v4.1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "evidence_contract": "official/company current-role source + Rapid X exact profile + deterministic name/company match",
        "x_source_policy": "Rapid X only; no LinkedIn and no alternate X/Twitter backend",
        "summary": {
            "candidate_company_count": len(rows),
            "named_candidate_count": sum(bool(candidate_block(row).get("name")) for row in rows),
            "rapid_x_lookup_count": sum(bool(row.get("cache_file")) for row in resolutions),
            "confirmed_operator_count": sum(bool(row["confirmed"]) for row in resolutions),
            "status_counts": dict(sorted(counts.items())),
        },
        "resolutions": resolutions,
    }
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**artifact["summary"], "output": str(args.output.relative_to(ROOT))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
