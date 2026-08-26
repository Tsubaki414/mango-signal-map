from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Confidence = Literal["confirmed", "high_probability", "lead"]
EntityKind = Literal[
    "company", "person", "creator", "mango", "investor", "agency", "media", "community"
]


@dataclass(slots=True)
class Evidence:
    id: str
    claim: str
    source_url: str
    source_title: str
    published_date: str | None
    verified_date: str
    confidence: Confidence
    source_type: str
    excerpt: str = ""


@dataclass(slots=True)
class Entity:
    id: str
    kind: EntityKind
    name: str
    handles: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Relationship:
    id: str
    source_id: str
    target_id: str
    relationship_type: str
    strength: int
    confidence: Confidence
    evidence_ids: list[str]
    observed_date: str
    directional: bool = True


@dataclass(slots=True)
class Project:
    id: str
    company: str
    category: str
    geography: str
    stage: str
    business_model: str
    budget_signals: list[str]
    marketing_channels: list[str]
    historical_campaigns: list[str]
    decision_maker_ids: list[str]
    public_contact_methods: list[str]
    mango_relationship_path: list[str]
    reachability_level: str
    budget_confidence: Confidence
    fit_score: int
    timing_score: int
    evidence_ids: list[str]
    recommended_next_action: str
    last_verified_date: str
    reachability_score: float = 0.0
    budget_score: float = 0.0
    evidence_quality_score: float = 0.0
    overall_priority: float = 0.0


@dataclass(slots=True)
class Sponsorship:
    id: str
    creator_id: str
    project_id: str
    platform: str
    content_title: str
    content_url: str
    content_date: str
    cooperation_type: str
    disclosure: str
    repeated: bool
    audience_fit: str
    relationship_value: str
    contact_method: str
    evidence_ids: list[str]
    confidence: Confidence


def to_dict(value: Any) -> dict[str, Any]:
    return asdict(value)

