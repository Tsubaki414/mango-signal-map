from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Evidence, Project


def load_scoring_config(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def score_projects(
    projects: list[Project], evidence: dict[str, Evidence], config: dict[str, Any]
) -> list[Project]:
    weights = config["weights"]
    confidence = config["confidence_multipliers"]
    reach_levels = config["reachability_levels"]
    for project in projects:
        project.reachability_score = float(reach_levels[project.reachability_level])
        signal_count = min(len(project.budget_signals), 5)
        base_budget = 45 + signal_count * 11
        project.budget_score = min(100.0, base_budget) * confidence[project.budget_confidence]
        related = [evidence[eid] for eid in project.evidence_ids if eid in evidence]
        confirmed = sum(1 for item in related if item.confidence == "confirmed")
        primary = sum(1 for item in related if item.source_type in {"official", "rapidx", "youtube_disclosure"})
        project.evidence_quality_score = min(100.0, 35 + confirmed * 8 + primary * 7)
        project.overall_priority = round(
            project.reachability_score * weights["reachability"]
            + project.budget_score * weights["budget"]
            + project.fit_score * weights["fit"]
            + project.timing_score * weights["timing"]
            + project.evidence_quality_score * weights["evidence_quality"],
            1,
        )
    return sorted(projects, key=lambda item: item.overall_priority, reverse=True)

