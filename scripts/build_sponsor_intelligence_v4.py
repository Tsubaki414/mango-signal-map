#!/usr/bin/env python3
"""Build evidence-rich creator-brand intelligence from the existing pilot ledger."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from mangobd.sponsor_intel import (
    SPONSOR_INTEL_SCHEMA_VERSION,
    BrandAliasResolver,
    build_brand_signals,
    build_sponsorship_edges,
    creator_gap,
    exact_category_competitors,
    mention_from_legacy,
    sponsor_gap,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "pilot"
OUT = ROOT / "outputs" / "pilot_v4"


MENTION_FIELDS = [
    "observation_id",
    "creator_id",
    "canonical_brand_id",
    "raw_brand",
    "brand_resolution_status",
    "content_id",
    "content_title",
    "content_url",
    "observed_date",
    "platform",
    "cooperation_type",
    "disclosure_class",
    "mention_type",
    "confidence",
    "fact_status",
    "start_ts",
    "provenance",
]

EDGE_FIELDS = [
    "edge_id",
    "source_id",
    "target_id",
    "creator_id",
    "brand_id",
    "relationship_type",
    "direction_semantics",
    "directional",
    "raw_brand_aliases",
    "observation_count",
    "confirmed_observation_count",
    "confirmed_paid_or_program_count",
    "paid_observation_count",
    "program_observation_count",
    "affiliate_observation_count",
    "unverified_observation_count",
    "first_seen",
    "last_seen",
    "dates",
    "disclosure_classes",
    "mention_types",
    "confidence_max",
    "confidence_mean",
    "fact_status",
    "repeat_relationship",
    "repeat_confirmed_relationship",
    "brand_creator_count",
    "brand_confirmed_creator_count",
    "multi_creator_signal",
    "campaign_signal",
    "evidence_ids",
    "evidence",
]

BRAND_SIGNAL_FIELDS = [
    "brand_id",
    "creator_count",
    "confirmed_creator_count",
    "observation_count",
    "confirmed_observation_count",
    "confirmed_paid_or_program_count",
    "paid_observation_count",
    "program_observation_count",
    "affiliate_observation_count",
    "repeat_creator_count",
    "repeat_signal",
    "multi_creator_signal",
    "campaign_signal",
    "first_seen",
    "last_seen",
    "dates",
    "evidence_ids",
]

SPONSOR_GAP_FIELDS = [
    "query_type",
    "target_creator_id",
    "status",
    "reason",
    "peer_basis",
    "target_brand_count",
    "eligible_peer_count",
    "peer_ids",
    "minimums",
    "candidate_brand_id",
    "supporting_peer_count",
    "supporting_peer_ids",
    "most_recent_date",
    "recency_weight",
    "score",
    "score_breakdown",
    "evidence_ids",
]

CREATOR_GAP_FIELDS = [
    "query_type",
    "target_brand_id",
    "status",
    "reason",
    "competitor_basis",
    "competitor_brand_ids",
    "target_creator_count",
    "observed_competitor_count",
    "competitor_relationship_count",
    "minimums",
    "candidate_creator_id",
    "supporting_competitor_count",
    "supporting_competitor_ids",
    "most_recent_date",
    "recency_weight",
    "score",
    "score_breakdown",
    "evidence_ids",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _csv_cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if value is None:
        return ""
    return value


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_cell(row.get(field)) for field in fields})


def flatten_sponsor_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gap in gaps:
        sample = gap.get("sample", {})
        base = {
            "query_type": gap["query_type"],
            "target_creator_id": gap["target_creator_id"],
            "status": gap["status"],
            "reason": gap.get("reason", ""),
            "peer_basis": gap.get("peer_basis", ""),
            "target_brand_count": sample.get("target_brand_count", 0),
            "eligible_peer_count": sample.get("eligible_peer_count", 0),
            "peer_ids": gap.get("peer_ids", []),
            "minimums": gap.get("minimums", {}),
        }
        results = gap.get("results", [])
        if results:
            rows.extend({**base, **result} for result in results)
        else:
            rows.append(base)
    return rows


def flatten_creator_gaps(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gap in gaps:
        sample = gap.get("sample", {})
        base = {
            "query_type": gap["query_type"],
            "target_brand_id": gap["target_brand_id"],
            "status": gap["status"],
            "reason": gap.get("reason", ""),
            "competitor_basis": gap.get("competitor_basis", ""),
            "competitor_brand_ids": gap.get("competitor_brand_ids", []),
            "target_creator_count": sample.get("target_creator_count", 0),
            "observed_competitor_count": sample.get("observed_competitor_count", 0),
            "competitor_relationship_count": sample.get("competitor_relationship_count", 0),
            "minimums": gap.get("minimums", {}),
        }
        results = gap.get("results", [])
        if results:
            rows.extend({**base, **result} for result in results)
        else:
            rows.append(base)
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records = read_json(DATA / "sponsorships.json")
    projects = read_json(DATA / "projects.json")
    evidence = read_json(DATA / "evidence.json")
    evidence_by_id = {item["id"]: item for item in evidence}
    project_by_id = {item["id"]: item for item in projects}
    resolver = BrandAliasResolver.from_projects(projects)

    mentions = []
    for record in records:
        project = project_by_id.get(record["project_id"])
        if project is None:
            raise ValueError(f"Unknown project_id in sponsorship record {record['id']}: {record['project_id']}")
        mentions.append(
            mention_from_legacy(
                record,
                resolver,
                project_name=project["company"],
                evidence_by_id=evidence_by_id,
            )
        )

    mention_rows = [mention.to_dict() for mention in mentions]
    edges = build_sponsorship_edges(mentions)
    edge_rows = [edge.to_dict() for edge in edges]
    brand_signals = build_brand_signals(edges)
    data_as_of = max(
        [mention.provenance.last_verified_at or mention.observed_date for mention in mentions],
        default="1970-01-01",
    )

    creator_ids = sorted({mention.creator_id for mention in mentions})
    sponsor_gaps = [
        sponsor_gap(
            edges,
            creator_id,
            min_target_brands=1,
            min_shared_brands=2,
            min_peers=2,
            min_supporting_peers=1,
            as_of=data_as_of,
        )
        for creator_id in creator_ids
    ]

    competitor_map = exact_category_competitors(projects)
    creator_gaps = []
    for project_id in sorted(project_by_id):
        result = creator_gap(
            edges,
            project_id,
            competitor_map.get(project_id, []),
            min_target_creators=1,
            min_competitors=2,
            min_competitor_relationships=2,
            as_of=data_as_of,
        )
        result["competitor_basis"] = "exact_normalized_project_category"
        creator_gaps.append(result)

    sponsor_gap_rows = flatten_sponsor_gaps(sponsor_gaps)
    creator_gap_rows = flatten_creator_gaps(creator_gaps)
    sponsor_gap_status = Counter(item["status"] for item in sponsor_gaps)
    creator_gap_status = Counter(item["status"] for item in creator_gaps)
    manifest = {
        "schema_version": SPONSOR_INTEL_SCHEMA_VERSION,
        "data_as_of": data_as_of,
        "input": {
            "sponsorship_records": "data/pilot/sponsorships.json",
            "projects": "data/pilot/projects.json",
            "evidence": "data/pilot/evidence.json",
        },
        "counts": {
            "mentions": len(mentions),
            "resolved_mentions": sum(mention.canonical_brand_id is not None for mention in mentions),
            "unresolved_mentions": sum(mention.canonical_brand_id is None for mention in mentions),
            "creator_brand_edges": len(edges),
            "brand_signals": len(brand_signals),
            "repeat_confirmed_edges": sum(edge.repeat_confirmed_relationship for edge in edges),
            "edges_in_multi_creator_brands": sum(edge.multi_creator_signal for edge in edges),
            "multi_creator_brands": sum(
                bool(signal["multi_creator_signal"]) for signal in brand_signals
            ),
            "sponsor_gap_queries": len(sponsor_gaps),
            "sponsor_gap_status": dict(sorted(sponsor_gap_status.items())),
            "creator_gap_queries": len(creator_gaps),
            "creator_gap_status": dict(sorted(creator_gap_status.items())),
        },
        "methods": {
            "brand_resolution": "Explicit project_id first; conservative normalized alias match second; ambiguity is never guessed.",
            "commercial_edge_eligibility": "Only confirmed paid sponsorship or confirmed program/official campaign evidence enters gap algorithms. Affiliate-only evidence is retained but excluded.",
            "sponsor_gap": "Peers must share at least two confirmed brands; at least two peers are required.",
            "creator_gap": "Competitors are accepted only from exact normalized project-category equality in this build; at least two observed competitors are required.",
            "recency": "1.0 at <=90 days, 0.8 at <=180, 0.6 at <=365, otherwise 0.3.",
        },
        "limitations": [
            "The source ledger is a 13-record curated pilot, not a broad YouTube census.",
            "No semantic competitor relationships are invented; exact category strings in the current project table are too sparse for creator-gap output.",
            "Current creators do not have enough cross-brand history for sponsor-gap output, so insufficient_history is the correct result.",
            "X reachability remains a separate graph layer and is not inferred from sponsorship evidence.",
            "Affiliate/referral evidence is not promoted to confirmed paid spend.",
        ],
    }

    write_json(OUT / "sponsor_intelligence_mentions.json", mention_rows)
    write_csv(OUT / "sponsor_intelligence_mentions.csv", mention_rows, MENTION_FIELDS)
    write_json(OUT / "sponsor_intelligence_edges.json", edge_rows)
    write_csv(OUT / "sponsor_intelligence_edges.csv", edge_rows, EDGE_FIELDS)
    write_json(OUT / "sponsor_intelligence_brand_signals.json", brand_signals)
    write_csv(
        OUT / "sponsor_intelligence_brand_signals.csv",
        brand_signals,
        BRAND_SIGNAL_FIELDS,
    )
    write_json(OUT / "sponsor_intelligence_sponsor_gaps.json", sponsor_gaps)
    write_csv(
        OUT / "sponsor_intelligence_sponsor_gaps.csv",
        sponsor_gap_rows,
        SPONSOR_GAP_FIELDS,
    )
    write_json(OUT / "sponsor_intelligence_creator_gaps.json", creator_gaps)
    write_csv(
        OUT / "sponsor_intelligence_creator_gaps.csv",
        creator_gap_rows,
        CREATOR_GAP_FIELDS,
    )
    write_json(OUT / "sponsor_intelligence_manifest.json", manifest)
    write_csv(
        OUT / "sponsor_intelligence_manifest.csv",
        [
            {
                "schema_version": manifest["schema_version"],
                "data_as_of": manifest["data_as_of"],
                "counts": manifest["counts"],
                "methods": manifest["methods"],
                "limitations": manifest["limitations"],
            }
        ],
        ["schema_version", "data_as_of", "counts", "methods", "limitations"],
    )
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
