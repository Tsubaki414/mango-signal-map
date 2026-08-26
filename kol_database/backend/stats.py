"""Shared averaging helper for every enrichment adapter (Rapid X, YouTube
Data API v3, ScrapeCreators). See enrichment.py's module docstring for the
full rationale: a trimmed mean (drop the single highest and single lowest
value once there are >=5 data points) keeps one viral post or one dead post
from swinging the average, while staying simple enough to explain in a
sentence to a non-technical BD person.
"""

from __future__ import annotations

import statistics


def trimmed_mean(values: list[float]) -> float | None:
    if not values:
        return None
    trimmed = sorted(values)[1:-1] if len(values) >= 5 else values
    return statistics.mean(trimmed) if trimmed else None


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None
