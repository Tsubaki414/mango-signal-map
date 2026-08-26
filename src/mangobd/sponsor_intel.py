from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, urlparse

from .discovery import classify_sponsor_disclosure


SPONSOR_INTEL_SCHEMA_VERSION = "1.0.0"

CONFIDENCE_SCORES = {
    "confirmed": 0.95,
    "high_probability": 0.75,
    "lead": 0.45,
}
FACT_STATUS = {
    "confirmed": "confirmed",
    "high_probability": "probable",
    "lead": "unverified",
}


def normalization_key(value: str) -> str:
    """Return a conservative comparison key without inventing brand equivalence."""
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^\w]+", "", normalized, flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class BrandResolution:
    canonical_brand_id: str | None
    status: str
    normalized_alias: str
    candidates: tuple[str, ...] = ()


class BrandAliasResolver:
    """Resolve brand aliases while surfacing ambiguity instead of guessing."""

    def __init__(self) -> None:
        self._canonical_ids: set[str] = set()
        self._aliases: dict[str, set[str]] = defaultdict(set)
        self.display_names: dict[str, str] = {}

    @classmethod
    def from_projects(
        cls,
        projects: Iterable[Mapping[str, Any]],
        extra_aliases: Mapping[str, str] | None = None,
    ) -> BrandAliasResolver:
        resolver = cls()
        for project in projects:
            canonical_id = str(project["id"])
            display_name = str(project.get("company") or canonical_id)
            resolver.register(canonical_id, display_name)
            for alias in project.get("aliases", []) or []:
                resolver.add_alias(str(alias), canonical_id)
        for alias, canonical_id in (extra_aliases or {}).items():
            resolver.add_alias(alias, canonical_id)
        return resolver

    def register(self, canonical_id: str, display_name: str) -> None:
        self._canonical_ids.add(canonical_id)
        self.display_names[canonical_id] = display_name
        self.add_alias(canonical_id, canonical_id)
        self.add_alias(display_name, canonical_id)

    def add_alias(self, alias: str, canonical_id: str) -> None:
        if canonical_id not in self._canonical_ids:
            raise ValueError(f"Alias points to unknown canonical brand: {canonical_id}")
        key = normalization_key(alias)
        if key:
            self._aliases[key].add(canonical_id)

    def resolve(self, raw_brand: str, explicit_id: str | None = None) -> BrandResolution:
        key = normalization_key(raw_brand)
        if explicit_id:
            if explicit_id in self._canonical_ids:
                return BrandResolution(explicit_id, "explicit_canonical_id", key, (explicit_id,))
            return BrandResolution(None, "unknown_explicit_id", key)

        candidates = tuple(sorted(self._aliases.get(key, set())))
        if len(candidates) == 1:
            return BrandResolution(candidates[0], "alias_match", key, candidates)
        if len(candidates) > 1:
            return BrandResolution(None, "ambiguous_alias", key, candidates)
        return BrandResolution(None, "unresolved_alias", key)


@dataclass(frozen=True, slots=True)
class ExtractionProvenance:
    source_record_id: str
    source_type: str
    extractor: str
    extractor_version: str
    matched_excerpt: str
    evidence_ids: tuple[str, ...]
    source_urls: tuple[str, ...]
    evidence_excerpts: tuple[str, ...]
    last_verified_at: str | None
    model: str | None = None
    prompt_version: str | None = None


@dataclass(frozen=True, slots=True)
class SponsorMention:
    observation_id: str
    creator_id: str
    canonical_brand_id: str | None
    raw_brand: str
    brand_resolution_status: str
    content_id: str
    content_title: str
    content_url: str
    observed_date: str
    platform: str
    cooperation_type: str
    disclosure_class: str
    mention_type: str
    confidence: float
    fact_status: str
    start_ts: int | None
    provenance: ExtractionProvenance

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EdgeEvidence:
    observation_id: str
    content_id: str
    content_title: str
    content_url: str
    observed_date: str
    disclosure_class: str
    mention_type: str
    fact_status: str
    confidence: float
    matched_excerpt: str
    start_ts: int | None
    evidence_ids: tuple[str, ...]
    source_urls: tuple[str, ...]
    extractor: str
    extractor_version: str
    last_verified_at: str | None


@dataclass(frozen=True, slots=True)
class SponsorshipEdge:
    edge_id: str
    source_id: str
    target_id: str
    creator_id: str
    brand_id: str
    relationship_type: str
    direction_semantics: str
    directional: bool
    raw_brand_aliases: tuple[str, ...]
    observation_count: int
    confirmed_observation_count: int
    confirmed_paid_or_program_count: int
    paid_observation_count: int
    program_observation_count: int
    affiliate_observation_count: int
    unverified_observation_count: int
    first_seen: str
    last_seen: str
    dates: tuple[str, ...]
    disclosure_classes: tuple[str, ...]
    mention_types: tuple[str, ...]
    confidence_max: float
    confidence_mean: float
    fact_status: str
    repeat_relationship: bool
    repeat_confirmed_relationship: bool
    brand_creator_count: int
    brand_confirmed_creator_count: int
    multi_creator_signal: bool
    campaign_signal: str
    evidence_ids: tuple[str, ...]
    evidence: tuple[EdgeEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _content_id(content_url: str, source_record_id: str) -> str:
    parsed = urlparse(content_url)
    host = parsed.netloc.casefold().removeprefix("www.")
    if host in {"youtube.com", "m.youtube.com"}:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
        if video_id:
            return f"youtube:{video_id}"
    if host == "youtu.be" and parsed.path.strip("/"):
        return f"youtube:{parsed.path.strip('/').split('/')[0]}"
    if content_url:
        digest = hashlib.sha256(content_url.encode("utf-8")).hexdigest()[:16]
        return f"url:{digest}"
    return f"record:{source_record_id}"


def classify_legacy_record(record: Mapping[str, Any]) -> str:
    """Classify legacy records without promoting affiliate or in-kind programs to paid."""
    cooperation = " ".join(str(record.get("cooperation_type", "")).casefold().split())
    disclosure = str(record.get("disclosure", ""))
    detected = classify_sponsor_disclosure(disclosure)

    program_markers = (
        "event sponsorship",
        "hackathon sponsorship",
        "creative partnership",
        "creative partner",
        "official case study",
        "ambassador program",
    )
    paid_markers = (
        "paid sponsorship",
        "repeat paid",
        "paid launch",
        "paid build",
    )
    affiliate_markers = (
        "affiliate",
        "referral",
        "tracked link",
        "tracked recommendation",
    )

    if any(marker in cooperation for marker in program_markers):
        return "confirmed_program_or_official_campaign"
    if any(marker in cooperation for marker in paid_markers):
        return "confirmed_paid_sponsorship"
    if detected == "confirmed_paid_sponsorship":
        return detected
    if any(marker in cooperation for marker in affiliate_markers):
        return "affiliate_or_referral"
    return detected


def mention_type_for(disclosure_class: str) -> str:
    return {
        "confirmed_paid_sponsorship": "paid_sponsorship",
        "confirmed_program_or_official_campaign": "official_program",
        "affiliate_or_referral": "affiliate",
    }.get(disclosure_class, "unverified_mention")


def mention_from_legacy(
    record: Mapping[str, Any],
    resolver: BrandAliasResolver,
    project_name: str,
    evidence_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> SponsorMention:
    """Adapt one existing sponsorship row into an auditable atomic mention."""
    evidence_by_id = evidence_by_id or {}
    source_record_id = str(record["id"])
    evidence_ids = tuple(sorted({str(value) for value in record.get("evidence_ids", [])}))
    evidence = [evidence_by_id[evidence_id] for evidence_id in evidence_ids if evidence_id in evidence_by_id]
    verified_dates = sorted(
        str(item["verified_date"])
        for item in evidence
        if item.get("verified_date")
    )
    source_urls = _dedupe(
        [str(record.get("content_url", ""))]
        + [str(item.get("source_url", "")) for item in evidence]
    )
    evidence_excerpts = _dedupe(str(item.get("excerpt", "")) for item in evidence)
    raw_brand = str(record.get("raw_brand") or project_name)
    resolution = resolver.resolve(raw_brand, explicit_id=str(record.get("project_id", "")) or None)
    confidence_label = str(record.get("confidence", "lead"))
    disclosure_class = classify_legacy_record(record)

    return SponsorMention(
        observation_id=source_record_id,
        creator_id=str(record["creator_id"]),
        canonical_brand_id=resolution.canonical_brand_id,
        raw_brand=raw_brand,
        brand_resolution_status=resolution.status,
        content_id=_content_id(str(record.get("content_url", "")), source_record_id),
        content_title=str(record.get("content_title", "")),
        content_url=str(record.get("content_url", "")),
        observed_date=str(record["content_date"]),
        platform=str(record.get("platform", "")),
        cooperation_type=str(record.get("cooperation_type", "")),
        disclosure_class=disclosure_class,
        mention_type=mention_type_for(disclosure_class),
        confidence=CONFIDENCE_SCORES.get(confidence_label, 0.4),
        fact_status=FACT_STATUS.get(confidence_label, "unverified"),
        start_ts=None,
        provenance=ExtractionProvenance(
            source_record_id=source_record_id,
            source_type="curated_sponsorship_record",
            extractor="legacy_sponsorship_adapter",
            extractor_version=SPONSOR_INTEL_SCHEMA_VERSION,
            matched_excerpt=str(record.get("disclosure", "")),
            evidence_ids=evidence_ids,
            source_urls=source_urls,
            evidence_excerpts=evidence_excerpts,
            last_verified_at=verified_dates[-1] if verified_dates else None,
        ),
    )


def _campaign_signal(
    repeat_confirmed: bool,
    multi_creator: bool,
    confirmed_commercial_count: int,
) -> str:
    if not confirmed_commercial_count:
        return "affiliate_or_unverified_only"
    if repeat_confirmed and multi_creator:
        return "repeat_and_multi_creator_confirmed"
    if repeat_confirmed:
        return "repeat_confirmed"
    if multi_creator:
        return "multi_creator_confirmed"
    return "single_confirmed_commercial_observation"


def build_sponsorship_edges(mentions: Iterable[SponsorMention]) -> list[SponsorshipEdge]:
    """Collapse atomic mentions while retaining proof, dates and confidence."""
    grouped: dict[tuple[str, str], list[SponsorMention]] = defaultdict(list)
    for mention in mentions:
        if mention.canonical_brand_id:
            grouped[(mention.creator_id, mention.canonical_brand_id)].append(mention)

    brand_creators: dict[str, set[str]] = defaultdict(set)
    brand_confirmed_creators: dict[str, set[str]] = defaultdict(set)
    for (creator_id, brand_id), items in grouped.items():
        brand_creators[brand_id].add(creator_id)
        if any(
            item.fact_status == "confirmed"
            and item.disclosure_class
            in {"confirmed_paid_sponsorship", "confirmed_program_or_official_campaign"}
            for item in items
        ):
            brand_confirmed_creators[brand_id].add(creator_id)

    edges: list[SponsorshipEdge] = []
    for (creator_id, brand_id), items in sorted(grouped.items()):
        ordered = sorted(items, key=lambda item: (item.observed_date, item.observation_id))
        confirmed = [item for item in ordered if item.fact_status == "confirmed"]
        commercial = [
            item
            for item in ordered
            if item.disclosure_class
            in {"confirmed_paid_sponsorship", "confirmed_program_or_official_campaign"}
        ]
        confirmed_commercial = [item for item in commercial if item.fact_status == "confirmed"]
        paid = [item for item in ordered if item.disclosure_class == "confirmed_paid_sponsorship"]
        programs = [
            item
            for item in ordered
            if item.disclosure_class == "confirmed_program_or_official_campaign"
        ]
        affiliates = [item for item in ordered if item.disclosure_class == "affiliate_or_referral"]
        unverified = [item for item in ordered if item.fact_status == "unverified"]
        repeat_confirmed = len(confirmed_commercial) >= 2
        multi_creator = len(brand_confirmed_creators[brand_id]) >= 2
        evidence = tuple(
            EdgeEvidence(
                observation_id=item.observation_id,
                content_id=item.content_id,
                content_title=item.content_title,
                content_url=item.content_url,
                observed_date=item.observed_date,
                disclosure_class=item.disclosure_class,
                mention_type=item.mention_type,
                fact_status=item.fact_status,
                confidence=item.confidence,
                matched_excerpt=item.provenance.matched_excerpt,
                start_ts=item.start_ts,
                evidence_ids=item.provenance.evidence_ids,
                source_urls=item.provenance.source_urls,
                extractor=item.provenance.extractor,
                extractor_version=item.provenance.extractor_version,
                last_verified_at=item.provenance.last_verified_at,
            )
            for item in reversed(ordered)
        )
        confidence_values = [item.confidence for item in ordered]
        fact_status = "confirmed" if confirmed else "probable" if max(confidence_values) >= 0.65 else "unverified"

        edges.append(
            SponsorshipEdge(
                edge_id=f"sponsor:{brand_id}->{creator_id}",
                source_id=brand_id,
                target_id=creator_id,
                creator_id=creator_id,
                brand_id=brand_id,
                relationship_type="creator_brand_commercial_evidence",
                direction_semantics="brand_to_creator",
                directional=True,
                raw_brand_aliases=tuple(sorted({item.raw_brand for item in ordered})),
                observation_count=len(ordered),
                confirmed_observation_count=len(confirmed),
                confirmed_paid_or_program_count=len(confirmed_commercial),
                paid_observation_count=len(paid),
                program_observation_count=len(programs),
                affiliate_observation_count=len(affiliates),
                unverified_observation_count=len(unverified),
                first_seen=ordered[0].observed_date,
                last_seen=ordered[-1].observed_date,
                dates=tuple(sorted({item.observed_date for item in ordered}, reverse=True)),
                disclosure_classes=tuple(sorted({item.disclosure_class for item in ordered})),
                mention_types=tuple(sorted({item.mention_type for item in ordered})),
                confidence_max=round(max(confidence_values), 3),
                confidence_mean=round(fmean(confidence_values), 3),
                fact_status=fact_status,
                repeat_relationship=len(ordered) >= 2,
                repeat_confirmed_relationship=repeat_confirmed,
                brand_creator_count=len(brand_creators[brand_id]),
                brand_confirmed_creator_count=len(brand_confirmed_creators[brand_id]),
                multi_creator_signal=multi_creator,
                campaign_signal=_campaign_signal(
                    repeat_confirmed,
                    multi_creator,
                    len(confirmed_commercial),
                ),
                evidence_ids=tuple(
                    sorted(
                        {
                            evidence_id
                            for item in ordered
                            for evidence_id in item.provenance.evidence_ids
                        }
                    )
                ),
                evidence=evidence,
            )
        )
    return edges


def build_brand_signals(edges: Iterable[SponsorshipEdge]) -> list[dict[str, Any]]:
    grouped: dict[str, list[SponsorshipEdge]] = defaultdict(list)
    for edge in edges:
        grouped[edge.brand_id].append(edge)

    signals: list[dict[str, Any]] = []
    for brand_id, items in sorted(grouped.items()):
        confirmed_creator_count = len(
            {edge.creator_id for edge in items if edge.confirmed_paid_or_program_count > 0}
        )
        repeat_creator_count = sum(edge.repeat_confirmed_relationship for edge in items)
        confirmed_commercial_count = sum(edge.confirmed_paid_or_program_count for edge in items)
        multi_creator = confirmed_creator_count >= 2
        repeat_confirmed = repeat_creator_count >= 1
        signals.append(
            {
                "brand_id": brand_id,
                "creator_count": len({edge.creator_id for edge in items}),
                "confirmed_creator_count": confirmed_creator_count,
                "observation_count": sum(edge.observation_count for edge in items),
                "confirmed_observation_count": sum(edge.confirmed_observation_count for edge in items),
                "confirmed_paid_or_program_count": confirmed_commercial_count,
                "paid_observation_count": sum(edge.paid_observation_count for edge in items),
                "program_observation_count": sum(edge.program_observation_count for edge in items),
                "affiliate_observation_count": sum(edge.affiliate_observation_count for edge in items),
                "repeat_creator_count": repeat_creator_count,
                "repeat_signal": repeat_confirmed,
                "multi_creator_signal": multi_creator,
                "campaign_signal": _campaign_signal(
                    repeat_confirmed,
                    multi_creator,
                    confirmed_commercial_count,
                ),
                "first_seen": min(edge.first_seen for edge in items),
                "last_seen": max(edge.last_seen for edge in items),
                "dates": sorted({value for edge in items for value in edge.dates}, reverse=True),
                "evidence_ids": sorted(
                    {value for edge in items for value in edge.evidence_ids}
                ),
            }
        )
    return signals


def history_gate(metric: str, sample_size: int, minimum_required: int) -> dict[str, Any]:
    return {
        "metric": metric,
        "sample_size": sample_size,
        "minimum_required": minimum_required,
        "status": "ok" if sample_size >= minimum_required else "insufficient_history",
    }


def _eligible_edge(edge: SponsorshipEdge) -> bool:
    return edge.confirmed_paid_or_program_count > 0


def _date_value(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _default_as_of(edges: Sequence[SponsorshipEdge]) -> date:
    if not edges:
        return date(1970, 1, 1)
    return max(_date_value(edge.last_seen) for edge in edges)


def recency_weight(observed_date: str | date, as_of: str | date) -> float:
    age_days = max(0, (_date_value(as_of) - _date_value(observed_date)).days)
    if age_days <= 90:
        return 1.0
    if age_days <= 180:
        return 0.8
    if age_days <= 365:
        return 0.6
    return 0.3


def sponsor_gap(
    edges: Iterable[SponsorshipEdge],
    target_creator_id: str,
    *,
    min_target_brands: int = 1,
    min_shared_brands: int = 2,
    min_peers: int = 2,
    min_supporting_peers: int = 1,
    as_of: str | date | None = None,
    limit: int = 15,
) -> dict[str, Any]:
    """Find confirmed brands used by comparable creators but not the target creator."""
    eligible = [edge for edge in edges if _eligible_edge(edge)]
    creator_edges: dict[str, dict[str, SponsorshipEdge]] = defaultdict(dict)
    for edge in eligible:
        creator_edges[edge.creator_id][edge.brand_id] = edge

    target_edges = creator_edges.get(target_creator_id, {})
    target_brands = set(target_edges)
    target_gate = history_gate("target_confirmed_brands", len(target_brands), min_target_brands)
    minimums = {
        "target_brands": min_target_brands,
        "shared_brands_per_peer": min_shared_brands,
        "peers": min_peers,
        "supporting_peers_per_gap": min_supporting_peers,
    }
    if target_gate["status"] != "ok":
        return {
            "query_type": "sponsor_gap",
            "target_creator_id": target_creator_id,
            "status": "insufficient_history",
            "reason": "target_has_too_few_confirmed_brand_relationships",
            "minimums": minimums,
            "sample": {"target_brand_count": len(target_brands), "eligible_peer_count": 0},
            "peer_basis": "shared_confirmed_brands",
            "peer_ids": [],
            "results": [],
        }

    peers: list[tuple[str, int]] = []
    for creator_id, brand_edges in creator_edges.items():
        if creator_id == target_creator_id:
            continue
        shared = len(target_brands.intersection(brand_edges))
        if shared >= min_shared_brands:
            peers.append((creator_id, shared))
    peers.sort(key=lambda item: (-item[1], item[0]))

    if len(peers) < min_peers:
        return {
            "query_type": "sponsor_gap",
            "target_creator_id": target_creator_id,
            "status": "insufficient_history",
            "reason": "too_few_peer_creators_with_shared_confirmed_brands",
            "minimums": minimums,
            "sample": {
                "target_brand_count": len(target_brands),
                "eligible_peer_count": len(peers),
            },
            "peer_basis": "shared_confirmed_brands",
            "peer_ids": [creator_id for creator_id, _ in peers],
            "results": [],
        }

    peer_ids = [creator_id for creator_id, _ in peers]
    candidates: dict[str, list[tuple[str, SponsorshipEdge]]] = defaultdict(list)
    for peer_id in peer_ids:
        for brand_id, edge in creator_edges[peer_id].items():
            if brand_id not in target_brands:
                candidates[brand_id].append((peer_id, edge))

    as_of_value = _date_value(as_of) if as_of is not None else _default_as_of(eligible)
    results: list[dict[str, Any]] = []
    for brand_id, support in candidates.items():
        supporting_peer_ids = sorted({peer_id for peer_id, _ in support})
        if len(supporting_peer_ids) < min_supporting_peers:
            continue
        most_recent = max(edge.last_seen for _, edge in support)
        weight = recency_weight(most_recent, as_of_value)
        results.append(
            {
                "candidate_brand_id": brand_id,
                "supporting_peer_count": len(supporting_peer_ids),
                "supporting_peer_ids": supporting_peer_ids,
                "most_recent_date": most_recent,
                "recency_weight": weight,
                "score": round(len(supporting_peer_ids) * weight, 3),
                "score_breakdown": {
                    "peer_coverage": len(supporting_peer_ids),
                    "recency_weight": weight,
                },
                "evidence_ids": sorted(
                    {value for _, edge in support for value in edge.evidence_ids}
                ),
            }
        )
    results.sort(key=lambda item: (-item["score"], item["candidate_brand_id"]))
    return {
        "query_type": "sponsor_gap",
        "target_creator_id": target_creator_id,
        "status": "ok",
        "reason": "no_gap_brands" if not results else "",
        "minimums": minimums,
        "sample": {
            "target_brand_count": len(target_brands),
            "eligible_peer_count": len(peers),
        },
        "peer_basis": "shared_confirmed_brands",
        "peer_ids": peer_ids,
        "results": results[:limit],
    }


def creator_gap(
    edges: Iterable[SponsorshipEdge],
    target_brand_id: str,
    competitor_brand_ids: Iterable[str],
    *,
    min_target_creators: int = 1,
    min_competitors: int = 2,
    min_competitor_relationships: int = 2,
    as_of: str | date | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Find creators used by evidence-backed competitors but not the target brand."""
    eligible = [edge for edge in edges if _eligible_edge(edge)]
    brand_edges: dict[str, dict[str, SponsorshipEdge]] = defaultdict(dict)
    for edge in eligible:
        brand_edges[edge.brand_id][edge.creator_id] = edge

    target_creators = set(brand_edges.get(target_brand_id, {}))
    competitors = sorted(
        {
            brand_id
            for brand_id in competitor_brand_ids
            if brand_id != target_brand_id and brand_edges.get(brand_id)
        }
    )
    competitor_relationships = sum(len(brand_edges[brand_id]) for brand_id in competitors)
    minimums = {
        "target_creators": min_target_creators,
        "competitors": min_competitors,
        "competitor_relationships": min_competitor_relationships,
    }
    sample = {
        "target_creator_count": len(target_creators),
        "observed_competitor_count": len(competitors),
        "competitor_relationship_count": competitor_relationships,
    }
    failed_reason = ""
    if len(target_creators) < min_target_creators:
        failed_reason = "target_has_too_few_confirmed_creator_relationships"
    elif len(competitors) < min_competitors:
        failed_reason = "too_few_observed_competitor_brands"
    elif competitor_relationships < min_competitor_relationships:
        failed_reason = "too_few_confirmed_competitor_creator_relationships"
    if failed_reason:
        return {
            "query_type": "creator_gap",
            "target_brand_id": target_brand_id,
            "status": "insufficient_history",
            "reason": failed_reason,
            "competitor_basis": "explicit_competitor_ids",
            "competitor_brand_ids": competitors,
            "minimums": minimums,
            "sample": sample,
            "results": [],
        }

    candidates: dict[str, list[tuple[str, SponsorshipEdge]]] = defaultdict(list)
    for competitor_id in competitors:
        for creator_id, edge in brand_edges[competitor_id].items():
            if creator_id not in target_creators:
                candidates[creator_id].append((competitor_id, edge))

    as_of_value = _date_value(as_of) if as_of is not None else _default_as_of(eligible)
    results: list[dict[str, Any]] = []
    for creator_id, support in candidates.items():
        supporting_competitors = sorted({brand_id for brand_id, _ in support})
        most_recent = max(edge.last_seen for _, edge in support)
        weight = recency_weight(most_recent, as_of_value)
        results.append(
            {
                "candidate_creator_id": creator_id,
                "supporting_competitor_count": len(supporting_competitors),
                "supporting_competitor_ids": supporting_competitors,
                "most_recent_date": most_recent,
                "recency_weight": weight,
                "score": round(len(supporting_competitors) * weight, 3),
                "score_breakdown": {
                    "competitor_coverage": len(supporting_competitors),
                    "recency_weight": weight,
                },
                "evidence_ids": sorted(
                    {value for _, edge in support for value in edge.evidence_ids}
                ),
            }
        )
    results.sort(key=lambda item: (-item["score"], item["candidate_creator_id"]))
    return {
        "query_type": "creator_gap",
        "target_brand_id": target_brand_id,
        "status": "ok",
        "reason": "no_gap_creators" if not results else "",
        "competitor_basis": "explicit_competitor_ids",
        "competitor_brand_ids": competitors,
        "minimums": minimums,
        "sample": sample,
        "results": results[:limit],
    }


def exact_category_competitors(
    projects: Iterable[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Build only exact-category competitor sets; do not invent semantic rivals."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for project in projects:
        grouped[normalization_key(str(project.get("category", "")))].append(str(project["id"]))
    result: dict[str, list[str]] = {}
    for project_ids in grouped.values():
        for project_id in project_ids:
            result[project_id] = sorted(value for value in project_ids if value != project_id)
    return result
