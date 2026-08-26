#!/usr/bin/env python3
"""Backfill explicit provenance fields into the hand-researched v4 source data.

This migration is intentionally conservative. It records when a source supports
an atomic claim and flags crypto rows whose historical URL list has not yet been
atomized claim-by-claim. It never upgrades Surf-blocked data to verified.
"""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "pilot_v4"

SECONDARY_DOMAINS = {"businesswire.com", "globenewswire.com", "prnewswire.com", "techcrunch.com", "chainwire.org", "theblock.co"}
PARTNER_DOMAINS = {"a16z.com", "insightpartners.com", "marketplace.crewai.com"}


def domain(url: str) -> str:
    value = urlparse(url).netloc.lower()
    return value[4:] if value.startswith("www.") else value


def fact_status(confidence: str | None) -> str:
    return {"high": "confirmed", "medium": "probable"}.get(str(confidence or "").lower(), "unverified")


def evidence_type(url: str) -> str:
    host = domain(url)
    if host in SECONDARY_DOMAINS:
        return "reputable_secondary"
    if host in PARTNER_DOMAINS:
        return "first_party_partner"
    return "official"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def normalize_longtail(rows: list[dict[str, Any]], verified_at: str) -> list[dict[str, Any]]:
    result = deepcopy(rows)
    for row in result:
        confidence = str(row.get("overall_confidence") or "medium").lower()
        row["last_verified_at"] = row.get("last_verified_at") or verified_at
        row["confidence"] = row.get("confidence") or confidence
        row["fact_status"] = row.get("fact_status") or fact_status(confidence)
        row["evidence_contract_status"] = "atomic_claim_sources"
        for index, source in enumerate(row.get("sources") or [], 1):
            source_confidence = str(source.get("confidence") or confidence).lower()
            source["evidence_id"] = source.get("evidence_id") or f"candidate:{slug(row['company'])}:{index:02d}"
            source["evidence_type"] = source.get("evidence_type") or evidence_type(str(source.get("url") or ""))
            source["fact_status"] = source.get("fact_status") or fact_status(source_confidence)
            source["last_verified_at"] = source.get("last_verified_at") or verified_at
            source["date_basis"] = source.get("date_basis") or (
                "accessed" if str(source.get("date") or "").lower().startswith("checked") else "published_or_source_date"
            )
    return result


def normalize_crypto(rows: list[dict[str, Any]], verified_at: str) -> list[dict[str, Any]]:
    result = deepcopy(rows)
    for row in result:
        # These records were assembled while Surf returned PAID_BALANCE_ZERO.
        # Public-web claims remain useful, but the legacy URL array does not
        # encode an atomic URL→claim mapping, so it must not be called confirmed.
        row["last_verified_at"] = row.get("last_verified_at") or verified_at
        row["confidence"] = row.get("confidence") or "medium"
        row["fact_status"] = row.get("fact_status") or "probable"
        row["evidence_contract_status"] = "record_level_sources_not_atomic_requires_claim_mapping"
        if not row.get("evidence"):
            row["evidence"] = [
                {
                    "evidence_id": f"candidate:{slug(row['company'])}:{index:02d}",
                    "url": url,
                    "claim": "Collected for one or more record-level funding, activity, program, or product claims; exact claim-to-URL mapping remains to be atomized.",
                    "date": row.get("metric_as_of"),
                    "date_basis": "record_metric_period_not_source_publication_date",
                    "last_verified_at": verified_at,
                    "evidence_type": evidence_type(str(url)),
                    "confidence": "low",
                    "fact_status": "unverified",
                }
                for index, url in enumerate(row.get("evidence_urls") or [], 1)
            ]
    return result


def normalize_operators(rows: list[dict[str, Any]], verified_at: str) -> list[dict[str, Any]]:
    result = deepcopy(rows)
    for row in result:
        row_verified = str(row.get("last_verified_at") or verified_at)
        row["last_verified_at"] = row_verified
        row["fact_status"] = row.get("fact_status") or "confirmed"
        row["confidence"] = row.get("confidence") or "high"
        for index, source in enumerate(row.get("operator_evidence") or [], 1):
            confidence = str(source.get("confidence") or "medium").lower()
            source["evidence_id"] = source.get("evidence_id") or f"operator:{slug(row['company'])}:{index:02d}"
            source["fact_status"] = source.get("fact_status") or fact_status(confidence)
            source["last_verified_at"] = source.get("last_verified_at") or row_verified
            source["date_basis"] = source.get("date_basis") or "published_or_accessed_as_labeled"
    return result


def migrate(*, verified_at: str, write: bool) -> dict[str, Any]:
    paths = {
        "longtail": DATA / "longtail_ai_candidates.json",
        "crypto": DATA / "crypto_ai_candidates.json",
        "operators": DATA / "top_operator_routes.json",
    }
    payloads = {name: json.loads(path.read_text(encoding="utf-8")) for name, path in paths.items()}
    migrated = {
        "longtail": normalize_longtail(payloads["longtail"], verified_at),
        "crypto": normalize_crypto(payloads["crypto"], verified_at),
        "operators": normalize_operators(payloads["operators"], verified_at),
    }
    if write:
        for name, path in paths.items():
            path.write_text(json.dumps(migrated[name], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "verified_at": verified_at,
        "write": write,
        "longtail_records": len(migrated["longtail"]),
        "longtail_atomic_evidence": sum(len(row.get("sources") or []) for row in migrated["longtail"]),
        "crypto_records": len(migrated["crypto"]),
        "crypto_non_atomic_evidence": sum(len(row.get("evidence") or []) for row in migrated["crypto"]),
        "operator_records": len(migrated["operators"]),
        "operator_atomic_evidence": sum(len(row.get("operator_evidence") or []) for row in migrated["operators"]),
        "surf_verified_fields": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verified-at", default=date.today().isoformat())
    parser.add_argument("--check", action="store_true", help="Validate the migration without rewriting files")
    args = parser.parse_args()
    print(json.dumps(migrate(verified_at=args.verified_at, write=not args.check), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
