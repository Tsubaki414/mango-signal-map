"""Versioned default work schedule for the 2026-08-29 Solomon release.

These dates are planning defaults, not evidence dates and not claims that a
contact attempt has happened. A human-edited due date always wins: the apply
script writes NULL rows only. The three release-day items are deliberately
low-risk internal relationship checks, not cold-contact instructions.
"""

from __future__ import annotations

import datetime as dt


RELEASE_DATE = dt.date(2026, 8, 29)
TODAY_INTERNAL_CHECKS = (
    "company:replit",
    "company:cursor",
    "company:perplexity",
)

WAVE_1_REMAINDER = (
    "company:runway",
    "company:pika",
    "company:pixverse",
    "company:gumloop",
    "company:meshy",
    "company:openart",
    "company:talusnetwork",
    "company:myshell",
    "company:tavus",
    "company:alloranetwork",
    "company:tripoai",
    "company:photoroom",
    "company:sapien",
    "company:kittl",
    "company:retellai",
    "company:viggleai",
    "company:beeble",
    "company:colossyan",
)
WAVE_1_DAILY_CAPACITY = (3, 3, 3, 3, 2, 2, 2)

WAVE_2 = (
    "company:higgsfield",
    "company:wisprflow",
    "company:lumaai",
    "company:genspark",
    "company:krea",
    "company:notion",
    "company:v0vercel",
    "company:vapi",
    "company:pinai",
    "company:kiteai",
    "company:olas",
    "company:giza",
    "company:inferencelabs",
    "company:gaib",
    "company:hyperbolic",
    "company:heurist",
    "company:openledger",
    "company:recraft",
    "company:synthflowai",
    "company:gopher",
    "company:vana",
)
WAVE_2_DAILY_CAPACITY = (3, 3, 3, 3, 3, 3, 3)


def _stagger(company_ids: tuple[str, ...], capacities: tuple[int, ...], first_offset: int) -> dict[str, str]:
    if sum(capacities) != len(company_ids):
        raise ValueError("release schedule capacity does not match company count")
    result: dict[str, str] = {}
    position = 0
    for day_index, capacity in enumerate(capacities):
        due = (RELEASE_DATE + dt.timedelta(days=first_offset + day_index)).isoformat()
        for company_id in company_ids[position : position + capacity]:
            result[company_id] = due
        position += capacity
    return result


RELEASE_DEFAULT_DUE_DATES = {
    **{company_id: RELEASE_DATE.isoformat() for company_id in TODAY_INTERNAL_CHECKS},
    **_stagger(WAVE_1_REMAINDER, WAVE_1_DAILY_CAPACITY, 1),
    **_stagger(WAVE_2, WAVE_2_DAILY_CAPACITY, 8),
}


def due_date_source(company_id: str, due_date: str | None) -> str | None:
    if not due_date:
        return None
    if RELEASE_DEFAULT_DUE_DATES.get(company_id) == due_date:
        return "release_planning_default"
    return "human_or_external_schedule"


def planned_work_type(company_id: str) -> str:
    return "internal_relationship_check" if company_id in TODAY_INTERNAL_CHECKS else "planned_work_item"
