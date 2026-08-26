#!/usr/bin/env python3
"""Normalize the two independently researched operator waves into one 42-row artifact."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WAVE1 = Path("/tmp/mangobd_operator_wave1.json")
DEFAULT_WAVE2 = Path("/tmp/mangobd_operator_wave2.json")
OUTPUT = ROOT / "data" / "pilot_v4" / "operator_research_candidates_v4.json"
OUT = ROOT / "outputs" / "pilot_v4"

SECONDARY_HOSTS = {
    "businesswire.com", "chainwire.org", "techcrunch.com", "youtube.com",
    "paypal-corp.com", "newsroom.paypal-corp.com", "prnewswire.com",
}


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def host(url: str) -> str:
    return (urlparse(url).hostname or "").casefold().removeprefix("www.")


def is_secondary_host(value: str) -> bool:
    hostname = host(value)
    return any(hostname == item or hostname.endswith(f".{item}") for item in SECONDARY_HOSTS)


def normalize_evidence_dates(rows: list[dict[str, Any]]) -> None:
    """Keep evidence dates machine-comparable and preserve qualifier provenance."""
    for row in rows:
        for item in row.get("operator_evidence") or []:
            raw = str(item.get("date") or row.get("last_verified_at") or "").strip()
            match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
            if not match:
                raise ValueError(f"Non-ISO operator evidence date for {row.get('company')}: {raw!r}")
            normalized_date = match.group(0)
            if raw != normalized_date:
                item["date_basis"] = raw
            item["date"] = normalized_date


def normalize_wave1(row: dict[str, Any], aliases: list[str]) -> dict[str, Any]:
    candidate = dict(row.get("candidate_operator") or {})
    candidate["x_handle"] = row.get("candidate_x_handle")
    evidence = [
        {
            **item,
            "claim": item.get("exact_claim") or item.get("claim") or "",
            "date": item.get("published_date") or row.get("last_verified_at"),
        }
        for item in row.get("official_role_evidence") or []
    ]
    official_domain = next(
        (host(item.get("url") or "") for item in evidence if not is_secondary_host(item.get("url") or "")), ""
    )
    handle_url = str(row.get("handle_source_url") or "")
    handle_claim = str(row.get("handle_source_claim") or "")
    handle_host = host(handle_url)
    handle_is_company_controlled = bool(
        handle_url
        and (
            handle_host == official_domain or handle_host.endswith(f".{official_domain}")
            or (
                handle_host in {"youtube.com", "youtu.be"}
                and row["company"].casefold() in handle_claim.casefold()
                and "channel" in handle_claim.casefold()
            )
        )
    )
    if handle_is_company_controlled:
        evidence.append({
            "url": handle_url,
            "claim": handle_claim,
            "date": row.get("last_verified_at"),
            "source_type": (
                "company_controlled_youtube_handle_link"
                if handle_host in {"youtube.com", "youtu.be"}
                else "company_controlled_handle_link"
            ),
            "fact_status": "confirmed_current_company_source",
            "confidence": "high",
        })
    route = row.get("public_business_route") or {}
    return {
        "company": row["company"],
        "wave": int(row.get("wave") or 1),
        "company_aliases": aliases,
        "candidate_operator": candidate,
        "candidate_x_handle": row.get("candidate_x_handle"),
        "handle_source": {
            "url": row.get("handle_source_url"),
            "claim": row.get("handle_source_claim"),
            "status": row.get("x_verification_status"),
        },
        "operator_evidence": evidence,
        "official_domain": official_domain,
        "public_business_route": {
            "type": route.get("type"), "value": route.get("value"), "source_url": route.get("source_url"),
        },
        "acquisition_next_step": row.get("acquisition_next_step"),
        "last_verified_at": row.get("last_verified_at"),
        "confidence": row.get("confidence"),
        "status": row.get("status"),
        "research_source_wave": "wave_1",
    }


def wave2_evidence(row: dict[str, Any]) -> list[dict[str, Any]]:
    row_fact = str(row.get("fact_status") or "").casefold()
    row_status = str(row.get("status") or "").casefold()
    stale = any(marker in f"{row_fact} {row_status}" for marker in (
        "historical", "legacy", "current_role_revalidation", "current-title", "current_title",
    ))
    output = []
    for item in row.get("official_company_source") or []:
        url = str(item.get("url") or "")
        evidence_type = str(item.get("evidence_type") or "").casefold()
        if is_secondary_host(url) or "partner_first_party" in evidence_type or "issuer_press_release" in evidence_type:
            source_type = "reputable_secondary_or_partner_source"
        else:
            source_type = "official_company_source"
        role_like = any(
            token in str(item.get("exact_claim") or "").casefold()
            for token in ("founder", "chief", "head", "ceo", "growth", "community", "partnership", "creator", "contributor", "strategy")
        )
        if source_type != "official_company_source":
            fact_status = "probable_secondary_requires_company_confirmation"
        elif stale:
            fact_status = "confirmed_historical_role_requires_current_revalidation"
        else:
            fact_status = "confirmed_current_company_source"
        output.append({
            "url": url,
            "claim": item.get("exact_claim") or "",
            "date": item.get("published_date") or row.get("last_verified_at"),
            "source_type": source_type,
            "fact_status": fact_status,
            "confidence": item.get("confidence"),
        })
    tie = row.get("company_source_x_tie") or {}
    if tie.get("url"):
        output.append({
            "url": tie["url"],
            "claim": tie.get("exact_claim") or "",
            "date": row.get("last_verified_at"),
            "source_type": "official_company_x_identity_link",
            "fact_status": "confirmed_current_company_source",
            "confidence": "high",
        })
    return output


def normalize_wave2(row: dict[str, Any], aliases: list[str]) -> dict[str, Any]:
    candidate = dict(row.get("candidate_operator") or {})
    candidate["x_handle"] = row.get("candidate_x_handle")
    evidence = wave2_evidence(row)
    route = row.get("public_business_route") or {}
    route_url = str(route.get("url") or "")
    value = route_url.removeprefix("mailto:") if route_url else str(route.get("route") or "")
    return {
        "company": row["company"],
        "wave": 2,
        "company_aliases": aliases,
        "candidate_operator": candidate,
        "candidate_x_handle": row.get("candidate_x_handle"),
        "handle_source": row.get("candidate_x_handle_source") or {},
        "operator_evidence": evidence,
        "official_domain": next((host(item["url"]) for item in evidence if item["source_type"] == "official_company_source"), ""),
        "public_business_route": {
            "type": f"official_{route.get('route_type') or 'company_route'}",
            "value": value,
            "source_url": route_url,
            "description": route.get("route"),
        },
        "acquisition_next_step": row.get("acquisition_next_step"),
        "last_verified_at": row.get("last_verified_at"),
        "confidence": row.get("confidence"),
        "status": row.get("status"),
        "research_source_wave": "wave_2",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wave1", type=Path, default=DEFAULT_WAVE1)
    parser.add_argument("--wave2", type=Path, default=DEFAULT_WAVE2)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    wave1 = read(args.wave1)
    wave2_payload = read(args.wave2)
    wave2 = wave2_payload.get("records") if isinstance(wave2_payload, dict) else wave2_payload
    universe = read(OUT / "all_company_universe_v4.json")
    aliases_by_company = {
        normalize(row["company"]): row.get("aliases") or [] for row in universe
    }
    rows = [
        *[normalize_wave1(row, aliases_by_company.get(normalize(row["company"]), [])) for row in wave1],
        *[normalize_wave2(row, aliases_by_company.get(normalize(row["company"]), [])) for row in wave2],
    ]
    normalize_evidence_dates(rows)
    queue = read(OUT / "priority_action_queue_v4.json")
    row_keys = [normalize(row["company"]) for row in rows]
    queue_keys = [normalize(row["company"]) for row in queue]
    if len(rows) != 42 or len(set(row_keys)) != 42 or set(row_keys) != set(queue_keys):
        raise ValueError({
            "rows": len(rows), "unique": len(set(row_keys)),
            "missing": sorted(set(queue_keys) - set(row_keys)),
            "extra": sorted(set(row_keys) - set(queue_keys)),
        })
    scoped_urls = [
        str(value)
        for row in rows
        for value in [
            *((item.get("url") for item in row.get("operator_evidence") or [])),
            (row.get("handle_source") or {}).get("url"),
            (row.get("public_business_route") or {}).get("source_url"),
        ]
        if value
    ]
    if any("linkedin.com" in value.casefold() for value in scoped_urls):
        raise ValueError("LinkedIn evidence leaked into normalized operator research")
    rows.sort(key=lambda row: (row["wave"], queue_keys.index(normalize(row["company"]))))
    artifact = {
        "schema_version": "operator-research-candidates-v4.1",
        "snapshot_date": "2026-08-25",
        "scope": "42 priority-action companies; official/company role research plus non-LinkedIn public handle discovery",
        "identity_rule": "Candidate names are not decision makers until official current-role evidence and a Rapid X exact profile pass deterministic gates.",
        "summary": {
            "company_count": len(rows),
            "wave_1_count": sum(row["wave"] == 1 for row in rows),
            "wave_2_count": sum(row["wave"] == 2 for row in rows),
            "named_candidate_count": sum(bool((row.get("candidate_operator") or {}).get("name")) for row in rows),
            "candidate_handle_count": sum(bool(row.get("candidate_x_handle")) for row in rows),
            "specific_acquisition_path_count": sum(bool(row.get("acquisition_next_step")) for row in rows),
        },
        "records": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**artifact["summary"], "output": str(args.output.relative_to(ROOT))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
