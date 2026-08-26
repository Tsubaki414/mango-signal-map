#!/usr/bin/env python3
"""Build evidence-preserving sponsor intelligence from expanded observations.

This pipeline intentionally keeps paid sponsorship, affiliate, mention, and
unverified observations separate.  It does not infer a paid relationship from
affiliate links, product coverage, campaign-shaped URLs, or creator overlap.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime
from itertools import combinations
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from mangobd.sponsor_intel import normalization_key


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "pilot_v4" / "sponsor_observations_expanded.json"
DEFAULT_OUT = ROOT / "outputs" / "pilot_v4"
SCHEMA_VERSION = "sponsor-expanded-v4.1.0"

DISCLOSURE_TYPES = (
    "paid_sponsorship",
    "affiliate",
    "mention",
    "unverified",
)

OBSERVATION_FIELDS = [
    "id",
    "creator",
    "creator_handle_or_channel",
    "brand",
    "platform",
    "content_url",
    "content_title",
    "published_at",
    "disclosure_type",
    "evidence_text",
    "evidence_location",
    "source_type",
    "extraction_method",
    "extracted_at",
    "confidence",
    "business_contact_or_route",
    "notes",
]

EDGE_FIELDS = [
    "edge_id",
    "brand_id",
    "brand",
    "creator_id",
    "creator",
    "creator_handle_or_channel",
    "platform",
    "relationship_type",
    "relationship_directions",
    "observation_count",
    "paid_observation_count",
    "affiliate_observation_count",
    "mention_observation_count",
    "unverified_observation_count",
    "paid_signal",
    "repeat_paid",
    "affiliate_only",
    "mention_or_unverified_only",
    "commercial_status",
    "brand_paid_creator_count",
    "multi_creator_paid_brand",
    "first_seen",
    "last_seen",
    "first_paid_date",
    "latest_paid_date",
    "dates",
    "paid_dates",
    "affiliate_dates",
    "mention_dates",
    "unverified_dates",
    "disclosure_types",
    "confidence_max",
    "confidence_mean",
    "observation_ids",
    "business_contact_or_routes",
    "evidence",
]

BRAND_SIGNAL_FIELDS = [
    "brand_id",
    "brand",
    "creator_edge_count",
    "unique_paid_creators",
    "unique_affiliate_creators",
    "affiliate_only_creator_count",
    "unique_mention_creators",
    "unique_unverified_creators",
    "observation_count",
    "paid_observation_count",
    "affiliate_observation_count",
    "mention_observation_count",
    "unverified_observation_count",
    "repeat_paid_creator_count",
    "repeat_paid",
    "multi_creator_paid",
    "activation_signal",
    "first_seen",
    "latest_observed_date",
    "latest_paid_date",
    "paid_creator_ids",
    "affiliate_creator_ids",
    "mention_creator_ids",
    "unverified_creator_ids",
    "observation_ids",
]

CREATOR_BRIDGE_FIELDS = [
    "creator_id",
    "creator",
    "creator_handle_or_channel",
    "platform",
    "bridge_signal",
    "paid_brand_count",
    "paid_observation_count",
    "repeat_paid_brand_count",
    "bridge_pair_count",
    "bridge_strength",
    "first_paid_date",
    "latest_paid_date",
    "paid_brand_ids",
    "paid_brands",
    "brand_pairs",
    "observation_ids",
    "content_urls",
]

SUMMARY_FIELDS = [
    "schema_version",
    "data_as_of",
    "source_extracted_at",
    "counts",
    "disclosure_distribution",
    "edge_commercial_status_distribution",
    "top_brands_by_unique_paid_creators",
    "repeat_paid_edges",
    "creator_bridges",
]

MANIFEST_FIELDS = [
    "schema_version",
    "data_as_of",
    "source_extracted_at",
    "input",
    "outputs",
    "counts",
    "methods",
    "invariants",
    "limitations",
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


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_cell(row.get(field)) for field in fields})


def _parse_date(value: str, field: str, observation_id: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field} for {observation_id}: {value!r}") from exc


def _parse_datetime(value: str, field: str, observation_id: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field} for {observation_id}: {value!r}") from exc


def _stable_entity_id(prefix: str, value: str) -> str:
    key = normalization_key(value)
    if not key:
        raise ValueError(f"Cannot build stable {prefix} ID from {value!r}")
    return f"{prefix}:{key}"


def validate_observations(records: Any) -> list[dict[str, Any]]:
    """Validate and deterministically order the source observations."""
    if not isinstance(records, list):
        raise ValueError("Expanded sponsor observations must be a JSON array")

    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    required = set(OBSERVATION_FIELDS)
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Observation at index {index} is not an object")
        missing = sorted(required.difference(raw))
        if missing:
            raise ValueError(f"Observation at index {index} is missing fields: {missing}")

        row = {field: raw[field] for field in OBSERVATION_FIELDS}
        observation_id = str(row["id"])
        if not observation_id or observation_id in seen_ids:
            raise ValueError(f"Duplicate or empty observation id: {observation_id!r}")
        seen_ids.add(observation_id)

        url = str(row["content_url"])
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"Invalid content_url for {observation_id}: {url!r}")
        if url in seen_urls:
            raise ValueError(f"Duplicate content_url: {url}")
        seen_urls.add(url)

        disclosure = str(row["disclosure_type"])
        if disclosure not in DISCLOSURE_TYPES:
            raise ValueError(
                f"Invalid disclosure_type for {observation_id}: {disclosure!r}"
            )

        confidence = row["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError(f"Confidence must be numeric for {observation_id}")
        if not 0.0 <= float(confidence) <= 1.0:
            raise ValueError(f"Confidence must be within [0, 1] for {observation_id}")
        row["confidence"] = float(confidence)

        published = _parse_date(str(row["published_at"]), "published_at", observation_id)
        extracted = _parse_datetime(
            str(row["extracted_at"]), "extracted_at", observation_id
        )
        if published > extracted.date():
            raise ValueError(
                f"published_at is later than extracted_at for {observation_id}"
            )

        for field in (
            "creator",
            "creator_handle_or_channel",
            "brand",
            "platform",
            "content_title",
            "evidence_text",
            "evidence_location",
            "source_type",
            "extraction_method",
        ):
            if not str(row[field]).strip():
                raise ValueError(f"Empty {field} for {observation_id}")
        validated.append(row)

    return sorted(
        validated,
        key=lambda row: (
            normalization_key(str(row["brand"])),
            normalization_key(str(row["creator_handle_or_channel"])),
            str(row["published_at"]),
            str(row["id"]),
        ),
    )


def _creator_id(row: Mapping[str, Any]) -> str:
    platform = normalization_key(str(row["platform"]))
    handle = str(row["creator_handle_or_channel"])
    return _stable_entity_id(f"creator:{platform}", handle)


def _brand_id(row: Mapping[str, Any]) -> str:
    return _stable_entity_id("brand", str(row["brand"]))


def _evidence_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "observation_id": row["id"],
        "disclosure_type": row["disclosure_type"],
        "published_at": row["published_at"],
        "confidence": row["confidence"],
        "content_url": row["content_url"],
        "content_title": row["content_title"],
        "evidence_text": row["evidence_text"],
        "evidence_location": row["evidence_location"],
        "source_type": row["source_type"],
        "extraction_method": row["extraction_method"],
        "extracted_at": row["extracted_at"],
    }


def _commercial_status(counts: Counter[str]) -> str:
    if counts["paid_sponsorship"]:
        return "confirmed_paid_present"
    if counts["affiliate"]:
        if counts["mention"] or counts["unverified"]:
            return "affiliate_and_nonpaid_only"
        return "affiliate_only"
    if counts["mention"]:
        return "mention_only"
    return "unverified_only"


def build_creator_brand_edges(
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate atomic rows without allowing non-paid classes into paid counts."""
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in observations:
        grouped[(_brand_id(row), _creator_id(row))].append(row)

    edges: list[dict[str, Any]] = []
    for (brand_id, creator_id), rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: (str(row["published_at"]), str(row["id"])))
        latest = ordered[-1]
        counts = Counter(str(row["disclosure_type"]) for row in ordered)
        dates_by_type = {
            disclosure: sorted(
                {
                    str(row["published_at"])
                    for row in ordered
                    if row["disclosure_type"] == disclosure
                }
            )
            for disclosure in DISCLOSURE_TYPES
        }
        paid_count = counts["paid_sponsorship"]
        affiliate_count = counts["affiliate"]
        mention_count = counts["mention"]
        unverified_count = counts["unverified"]
        routes = sorted(
            {
                str(row["business_contact_or_route"])
                for row in ordered
                if row.get("business_contact_or_route")
            }
        )
        edges.append(
            {
                "edge_id": f"edge:creator_brand:{brand_id}:{creator_id}",
                "brand_id": brand_id,
                "brand": latest["brand"],
                "creator_id": creator_id,
                "creator": latest["creator"],
                "creator_handle_or_channel": latest["creator_handle_or_channel"],
                "platform": latest["platform"],
                "relationship_type": "creator_brand_evidence_aggregate",
                "relationship_directions": {
                    "paid_sponsorship": "brand_to_creator",
                    "affiliate": "creator_to_brand_referral",
                    "mention": "creator_to_brand_mention",
                    "unverified": "not_assigned",
                },
                "observation_count": len(ordered),
                "paid_observation_count": paid_count,
                "affiliate_observation_count": affiliate_count,
                "mention_observation_count": mention_count,
                "unverified_observation_count": unverified_count,
                "paid_signal": paid_count > 0,
                "repeat_paid": paid_count >= 2,
                "affiliate_only": (
                    paid_count == 0
                    and affiliate_count > 0
                    and mention_count == 0
                    and unverified_count == 0
                ),
                "mention_or_unverified_only": (
                    paid_count == 0
                    and affiliate_count == 0
                    and (mention_count > 0 or unverified_count > 0)
                ),
                "commercial_status": _commercial_status(counts),
                "brand_paid_creator_count": 0,
                "multi_creator_paid_brand": False,
                "first_seen": min(str(row["published_at"]) for row in ordered),
                "last_seen": max(str(row["published_at"]) for row in ordered),
                "first_paid_date": min(dates_by_type["paid_sponsorship"], default=None),
                "latest_paid_date": max(dates_by_type["paid_sponsorship"], default=None),
                "dates": sorted({str(row["published_at"]) for row in ordered}),
                "paid_dates": dates_by_type["paid_sponsorship"],
                "affiliate_dates": dates_by_type["affiliate"],
                "mention_dates": dates_by_type["mention"],
                "unverified_dates": dates_by_type["unverified"],
                "disclosure_types": [
                    disclosure for disclosure in DISCLOSURE_TYPES if counts[disclosure]
                ],
                "confidence_max": max(float(row["confidence"]) for row in ordered),
                "confidence_mean": round(
                    fmean(float(row["confidence"]) for row in ordered), 4
                ),
                "observation_ids": [str(row["id"]) for row in ordered],
                "business_contact_or_routes": routes,
                "evidence": [_evidence_row(row) for row in ordered],
            }
        )

    paid_creator_counts = Counter(
        edge["brand_id"] for edge in edges if edge["paid_observation_count"] > 0
    )
    for edge in edges:
        edge["brand_paid_creator_count"] = paid_creator_counts[edge["brand_id"]]
        edge["multi_creator_paid_brand"] = (
            paid_creator_counts[edge["brand_id"]] >= 2
        )
    return sorted(
        edges,
        key=lambda edge: (
            normalization_key(str(edge["brand"])),
            normalization_key(str(edge["creator_handle_or_channel"])),
        ),
    )


def _activation_signal(
    *, unique_paid_creators: int, repeat_paid_creator_count: int
) -> str:
    if unique_paid_creators >= 2 and repeat_paid_creator_count:
        return "repeat_and_multi_creator_paid"
    if unique_paid_creators >= 2:
        return "multi_creator_paid"
    if repeat_paid_creator_count:
        return "repeat_paid_single_creator"
    if unique_paid_creators == 1:
        return "single_creator_paid"
    return "affiliate_mention_or_unverified_only"


def build_brand_activation_signals(
    edges: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for edge in edges:
        grouped[str(edge["brand_id"])].append(edge)

    signals: list[dict[str, Any]] = []
    for brand_id, brand_edges in sorted(grouped.items()):
        paid_creators = sorted(
            str(edge["creator_id"])
            for edge in brand_edges
            if int(edge["paid_observation_count"]) > 0
        )
        affiliate_creators = sorted(
            str(edge["creator_id"])
            for edge in brand_edges
            if int(edge["affiliate_observation_count"]) > 0
        )
        mention_creators = sorted(
            str(edge["creator_id"])
            for edge in brand_edges
            if int(edge["mention_observation_count"]) > 0
        )
        unverified_creators = sorted(
            str(edge["creator_id"])
            for edge in brand_edges
            if int(edge["unverified_observation_count"]) > 0
        )
        repeat_count = sum(bool(edge["repeat_paid"]) for edge in brand_edges)
        latest_paid_dates = [
            str(edge["latest_paid_date"])
            for edge in brand_edges
            if edge.get("latest_paid_date")
        ]
        signals.append(
            {
                "brand_id": brand_id,
                "brand": brand_edges[0]["brand"],
                "creator_edge_count": len(brand_edges),
                "unique_paid_creators": len(paid_creators),
                "unique_affiliate_creators": len(affiliate_creators),
                "affiliate_only_creator_count": sum(
                    int(edge["paid_observation_count"]) == 0
                    and int(edge["affiliate_observation_count"]) > 0
                    for edge in brand_edges
                ),
                "unique_mention_creators": len(mention_creators),
                "unique_unverified_creators": len(unverified_creators),
                "observation_count": sum(
                    int(edge["observation_count"]) for edge in brand_edges
                ),
                "paid_observation_count": sum(
                    int(edge["paid_observation_count"]) for edge in brand_edges
                ),
                "affiliate_observation_count": sum(
                    int(edge["affiliate_observation_count"]) for edge in brand_edges
                ),
                "mention_observation_count": sum(
                    int(edge["mention_observation_count"]) for edge in brand_edges
                ),
                "unverified_observation_count": sum(
                    int(edge["unverified_observation_count"]) for edge in brand_edges
                ),
                "repeat_paid_creator_count": repeat_count,
                "repeat_paid": repeat_count > 0,
                "multi_creator_paid": len(paid_creators) >= 2,
                "activation_signal": _activation_signal(
                    unique_paid_creators=len(paid_creators),
                    repeat_paid_creator_count=repeat_count,
                ),
                "first_seen": min(str(edge["first_seen"]) for edge in brand_edges),
                "latest_observed_date": max(
                    str(edge["last_seen"]) for edge in brand_edges
                ),
                "latest_paid_date": max(latest_paid_dates, default=None),
                "paid_creator_ids": paid_creators,
                "affiliate_creator_ids": affiliate_creators,
                "mention_creator_ids": mention_creators,
                "unverified_creator_ids": unverified_creators,
                "observation_ids": sorted(
                    {
                        str(observation_id)
                        for edge in brand_edges
                        for observation_id in edge["observation_ids"]
                    }
                ),
            }
        )
    return sorted(
        signals,
        key=lambda signal: (
            -int(signal["unique_paid_creators"]),
            -int(signal["paid_observation_count"]),
            normalization_key(str(signal["brand"])),
        ),
    )


def build_creator_bridge_signals(
    edges: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return only creators with paid evidence for at least two brands."""
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for edge in edges:
        if int(edge["paid_observation_count"]) > 0:
            grouped[str(edge["creator_id"])].append(edge)

    bridges: list[dict[str, Any]] = []
    for creator_id, paid_edges in grouped.items():
        if len(paid_edges) < 2:
            continue
        ordered_edges = sorted(
            paid_edges, key=lambda edge: normalization_key(str(edge["brand"]))
        )
        pair_rows = [
            {
                "brand_ids": [str(left["brand_id"]), str(right["brand_id"])],
                "brands": [str(left["brand"]), str(right["brand"])],
            }
            for left, right in combinations(ordered_edges, 2)
        ]
        paid_observation_ids = sorted(
            {
                str(evidence["observation_id"])
                for edge in ordered_edges
                for evidence in edge["evidence"]
                if evidence["disclosure_type"] == "paid_sponsorship"
            }
        )
        paid_urls = sorted(
            {
                str(evidence["content_url"])
                for edge in ordered_edges
                for evidence in edge["evidence"]
                if evidence["disclosure_type"] == "paid_sponsorship"
            }
        )
        repeat_brand_count = sum(bool(edge["repeat_paid"]) for edge in ordered_edges)
        first = ordered_edges[0]
        bridges.append(
            {
                "creator_id": creator_id,
                "creator": first["creator"],
                "creator_handle_or_channel": first["creator_handle_or_channel"],
                "platform": first["platform"],
                "bridge_signal": "cross_brand_confirmed_paid_creator",
                "paid_brand_count": len(ordered_edges),
                "paid_observation_count": sum(
                    int(edge["paid_observation_count"]) for edge in ordered_edges
                ),
                "repeat_paid_brand_count": repeat_brand_count,
                "bridge_pair_count": len(pair_rows),
                "bridge_strength": round(
                    len(ordered_edges) + (0.5 * repeat_brand_count), 2
                ),
                "first_paid_date": min(
                    str(edge["first_paid_date"]) for edge in ordered_edges
                ),
                "latest_paid_date": max(
                    str(edge["latest_paid_date"]) for edge in ordered_edges
                ),
                "paid_brand_ids": [str(edge["brand_id"]) for edge in ordered_edges],
                "paid_brands": [str(edge["brand"]) for edge in ordered_edges],
                "brand_pairs": pair_rows,
                "observation_ids": paid_observation_ids,
                "content_urls": paid_urls,
            }
        )
    return sorted(
        bridges,
        key=lambda bridge: (
            -int(bridge["paid_brand_count"]),
            -int(bridge["paid_observation_count"]),
            normalization_key(str(bridge["creator_handle_or_channel"])),
        ),
    )


def build_summary(
    observations: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    brand_signals: Sequence[Mapping[str, Any]],
    creator_bridges: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    disclosures = Counter(str(row["disclosure_type"]) for row in observations)
    edge_statuses = Counter(str(edge["commercial_status"]) for edge in edges)
    repeat_edges = [
        {
            "edge_id": edge["edge_id"],
            "brand": edge["brand"],
            "creator": edge["creator"],
            "creator_handle_or_channel": edge["creator_handle_or_channel"],
            "paid_observation_count": edge["paid_observation_count"],
            "paid_dates": edge["paid_dates"],
        }
        for edge in edges
        if edge["repeat_paid"]
    ]
    top_brands = [
        {
            "brand_id": signal["brand_id"],
            "brand": signal["brand"],
            "unique_paid_creators": signal["unique_paid_creators"],
            "paid_observation_count": signal["paid_observation_count"],
            "repeat_paid_creator_count": signal["repeat_paid_creator_count"],
            "latest_paid_date": signal["latest_paid_date"],
        }
        for signal in brand_signals
    ]
    bridge_summary = [
        {
            "creator_id": bridge["creator_id"],
            "creator": bridge["creator"],
            "creator_handle_or_channel": bridge["creator_handle_or_channel"],
            "paid_brand_count": bridge["paid_brand_count"],
            "paid_brands": bridge["paid_brands"],
            "paid_observation_count": bridge["paid_observation_count"],
        }
        for bridge in creator_bridges
    ]
    counts = {
        "atomic_observations": len(observations),
        "unique_content_urls": len({str(row["content_url"]) for row in observations}),
        "unique_brands": len({str(row["brand"]) for row in observations}),
        "unique_creators": len({_creator_id(row) for row in observations}),
        "creator_brand_edges": len(edges),
        "brand_activation_signals": len(brand_signals),
        "creator_bridge_signals": len(creator_bridges),
        "repeat_paid_edges": len(repeat_edges),
        "multi_creator_paid_brands": sum(
            bool(signal["multi_creator_paid"]) for signal in brand_signals
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "data_as_of": max(str(row["published_at"]) for row in observations),
        "source_extracted_at": max(str(row["extracted_at"]) for row in observations),
        "counts": counts,
        "disclosure_distribution": {
            disclosure: disclosures[disclosure] for disclosure in DISCLOSURE_TYPES
        },
        "edge_commercial_status_distribution": dict(sorted(edge_statuses.items())),
        "top_brands_by_unique_paid_creators": top_brands,
        "repeat_paid_edges": repeat_edges,
        "creator_bridges": bridge_summary,
    }


def build_manifest(
    observations: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    brand_signals: Sequence[Mapping[str, Any]],
    creator_bridges: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    source_paid = sum(
        row["disclosure_type"] == "paid_sponsorship" for row in observations
    )
    edge_paid = sum(int(edge["paid_observation_count"]) for edge in edges)
    bridge_paid_ids = {
        observation_id
        for bridge in creator_bridges
        for observation_id in bridge["observation_ids"]
    }
    paid_ids = {
        str(row["id"])
        for row in observations
        if row["disclosure_type"] == "paid_sponsorship"
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "data_as_of": summary["data_as_of"],
        "source_extracted_at": summary["source_extracted_at"],
        "input": "data/pilot_v4/sponsor_observations_expanded.json",
        "outputs": {
            "observations": [
                "outputs/pilot_v4/sponsor_expanded_observations.json",
                "outputs/pilot_v4/sponsor_expanded_observations.csv",
            ],
            "creator_brand_edges": [
                "outputs/pilot_v4/sponsor_expanded_edges.json",
                "outputs/pilot_v4/sponsor_expanded_edges.csv",
            ],
            "brand_activation_signals": [
                "outputs/pilot_v4/sponsor_expanded_brand_signals.json",
                "outputs/pilot_v4/sponsor_expanded_brand_signals.csv",
            ],
            "creator_bridge_signals": [
                "outputs/pilot_v4/sponsor_expanded_creator_bridges.json",
                "outputs/pilot_v4/sponsor_expanded_creator_bridges.csv",
            ],
            "summary": [
                "outputs/pilot_v4/sponsor_expanded_summary.json",
                "outputs/pilot_v4/sponsor_expanded_summary.csv",
            ],
            "manifest": [
                "outputs/pilot_v4/sponsor_expanded_manifest.json",
                "outputs/pilot_v4/sponsor_expanded_manifest.csv",
            ],
        },
        "counts": summary["counts"],
        "methods": {
            "atomic_observations": "Source rows are retained one per content URL with source, publication date, extraction date, disclosure, evidence text, and confidence.",
            "creator_brand_edges": "Rows are grouped by normalized brand and platform-scoped creator handle. Paid, affiliate, mention, and unverified counts remain independent.",
            "repeat_paid": "True only when one creator-brand edge has at least two distinct paid_sponsorship observations.",
            "brand_activation": "Unique paid creators and multi-creator signals use only explicit paid_sponsorship rows.",
            "creator_bridge": "A bridge requires explicit paid_sponsorship evidence for the same creator across at least two brands; affiliate and mention rows never qualify.",
            "entity_ids": "Stable normalized IDs are derived conservatively from brand display name and platform-scoped creator handle; display values remain available.",
        },
        "invariants": {
            "unique_observation_ids": len({row["id"] for row in observations})
            == len(observations),
            "unique_content_urls": len({row["content_url"] for row in observations})
            == len(observations),
            "edge_observations_equal_source": sum(
                int(edge["observation_count"]) for edge in edges
            )
            == len(observations),
            "paid_edge_counts_equal_paid_source_rows": edge_paid == source_paid,
            "affiliate_and_mentions_never_increment_paid": all(
                int(edge["paid_observation_count"])
                == sum(
                    evidence["disclosure_type"] == "paid_sponsorship"
                    for evidence in edge["evidence"]
                )
                for edge in edges
            ),
            "bridges_reference_paid_observations_only": bridge_paid_ids.issubset(
                paid_ids
            ),
            "brand_signal_paid_counts_equal_source": sum(
                int(signal["paid_observation_count"]) for signal in brand_signals
            )
            == source_paid,
        },
        "limitations": [
            "The input is a targeted public YouTube evidence sample, not a complete sponsorship census.",
            "An explicit 'sponsored by' disclosure confirms a sponsorship relationship but does not reveal cash amount, payment form, or deliverables.",
            "Lower-confidence affiliate rows remain affiliate evidence and never become paid sponsorship evidence.",
            "Creator bridge signals show shared paid creator history, not a human introduction path or willingness to introduce.",
            "No X/Twitter relationship data is used or inferred in this build.",
        ],
    }


def build_products(records: Any) -> dict[str, Any]:
    observations = validate_observations(records)
    if not observations:
        raise ValueError("Expanded sponsor observations cannot be empty")
    edges = build_creator_brand_edges(observations)
    brand_signals = build_brand_activation_signals(edges)
    creator_bridges = build_creator_bridge_signals(edges)
    summary = build_summary(observations, edges, brand_signals, creator_bridges)
    manifest = build_manifest(
        observations, edges, brand_signals, creator_bridges, summary
    )
    if not all(manifest["invariants"].values()):
        failed = [
            name for name, passed in manifest["invariants"].items() if not passed
        ]
        raise AssertionError(f"Sponsor expanded invariants failed: {failed}")
    return {
        "observations": observations,
        "edges": edges,
        "brand_signals": brand_signals,
        "creator_bridges": creator_bridges,
        "summary": summary,
        "manifest": manifest,
    }


def write_outputs(products: Mapping[str, Any], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        ("observations", "observations", OBSERVATION_FIELDS),
        ("edges", "edges", EDGE_FIELDS),
        ("brand_signals", "brand_signals", BRAND_SIGNAL_FIELDS),
        ("creator_bridges", "creator_bridges", CREATOR_BRIDGE_FIELDS),
    ]
    written: list[Path] = []
    for filename_suffix, product_key, fields in specs:
        json_path = out_dir / f"sponsor_expanded_{filename_suffix}.json"
        csv_path = out_dir / f"sponsor_expanded_{filename_suffix}.csv"
        write_json(json_path, products[product_key])
        write_csv(csv_path, products[product_key], fields)
        written.extend([json_path, csv_path])

    for filename_suffix, fields in (
        ("summary", SUMMARY_FIELDS),
        ("manifest", MANIFEST_FIELDS),
    ):
        json_path = out_dir / f"sponsor_expanded_{filename_suffix}.json"
        csv_path = out_dir / f"sponsor_expanded_{filename_suffix}.csv"
        value = products[filename_suffix]
        write_json(json_path, value)
        write_csv(csv_path, [value], fields)
        written.extend([json_path, csv_path])
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    products = build_products(read_json(args.input))
    written = write_outputs(products, args.out)
    print(
        json.dumps(
            {
                **products["summary"]["counts"],
                "disclosure_distribution": products["summary"][
                    "disclosure_distribution"
                ],
                "written_files": [str(path) for path in written],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
