from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any


CONFIRMED_DISCLOSURES = (
    r"(?<!\w)#(?:ad|sponsored)\b",
    r"\bsponsored by\b",
    r"\bpaid partnership\b",
    r"\bthanks? .{0,60}\bfor sponsoring\b",
)
AFFILIATE_DISCLOSURES = (
    r"\baffiliate link\b",
    r"\bmay earn (?:a )?commission\b",
    r"\buse (?:my )?code\b",
    r"[?&](?:ref|referral|affiliate)=",
)


def classify_sponsor_disclosure(text: str) -> str:
    """Classify disclosure evidence without equating affiliate links with paid sponsorship."""
    normalized = " ".join(text.lower().split())
    if any(re.search(pattern, normalized) for pattern in CONFIRMED_DISCLOSURES):
        return "confirmed_paid_sponsorship"
    if any(re.search(pattern, normalized) for pattern in AFFILIATE_DISCLOSURES):
        return "affiliate_or_referral"
    return "unverified_mention"


def cluster_campaigns(records: list[dict[str, Any]], window_days: int = 45) -> list[dict[str, Any]]:
    """Cluster brand mentions into campaign windows and quantify recurrence across creators."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record["project_id"])].append(record)
    clusters: list[dict[str, Any]] = []
    for project_id, items in grouped.items():
        dated = sorted(items, key=lambda item: item["content_date"])
        current: list[dict[str, Any]] = []
        for item in dated:
            item_date = date.fromisoformat(item["content_date"])
            if current and item_date > date.fromisoformat(current[-1]["content_date"]) + timedelta(days=window_days):
                clusters.append(_summarize_cluster(project_id, current))
                current = []
            current.append(item)
        if current:
            clusters.append(_summarize_cluster(project_id, current))
    return clusters


def _summarize_cluster(project_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    creators = sorted({str(item["creator_id"]) for item in items})
    disclosures = [item.get("disclosure_class") or classify_sponsor_disclosure(item.get("disclosure", "")) for item in items]
    confirmed_total = sum(value.startswith("confirmed") for value in disclosures)
    confirmed_paid = disclosures.count("confirmed_paid_sponsorship")
    confirmed_program = disclosures.count("confirmed_program_or_official_campaign")
    return {
        "project_id": project_id,
        "start_date": min(item["content_date"] for item in items),
        "end_date": max(item["content_date"] for item in items),
        "content_count": len(items),
        "creator_count": len(creators),
        "creator_ids": creators,
        "confirmed_total_count": confirmed_total,
        "confirmed_paid_count": confirmed_paid,
        "confirmed_program_count": confirmed_program,
        "affiliate_count": disclosures.count("affiliate_or_referral"),
        "campaign_signal": (
            "multi_creator_or_repeat_paid"
            if confirmed_total >= 2 or len(creators) >= 2
            else "single_observation"
        ),
    }


def score_candidate(candidate: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Score a discovery candidate while applying explicit fame/identity/evidence penalties."""
    reachability = config["reachability_levels"][candidate["reachability_level"]]
    spend = config["spend_mechanism_levels"][candidate["spend_mechanism_level"]]
    components = {
        "reachability": reachability,
        "spend_mechanism": spend,
        "timing": float(candidate["timing_score"]),
        "fit": float(candidate["fit_score"]),
        "buyer_clarity": float(candidate["buyer_clarity_score"]),
        "evidence_quality": float(candidate["evidence_quality_score"]),
    }
    gross = sum(components[key] * float(config["weights"][key]) for key in components)
    applied_penalties = [name for name in candidate.get("penalties", []) if name in config["penalties"]]
    penalty = sum(float(config["penalties"][name]) for name in applied_penalties)
    score = round(max(0.0, gross - penalty), 1)
    tiers = config["tiers"]
    tier = "act_now" if score >= tiers["act_now"] else "validate" if score >= tiers["validate"] else "watch"
    return {
        **candidate,
        "reachability_score": reachability,
        "spend_mechanism_score": spend,
        "gross_score": round(gross, 1),
        "penalty_score": penalty,
        "applied_penalties": applied_penalties,
        "overall_priority": score,
        "priority_tier": tier,
    }
