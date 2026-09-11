"""Apply Chinese translations of BD research prose produced this session.

Reads three JSON files (companies, action_items+sponsorship_evidence, misc)
and writes translated text back onto the corresponding rows by primary key.

category/geography are the one exception: they write to category_zh/
geography_zh (see models.Company docstring) rather than overwriting the
English column, since bd_compute.creator_campaign_fit() matches company
category against Creator.categories in English. Every other field here is
overwritten in place -- there's no functional coupling on that free text,
just display.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import (  # noqa: E402
    ActionItem,
    Company,
    ConnectorBrief,
    GtmCase,
    Operator,
    SolomonReview,
    SponsorshipEvidence,
)

TR_DIR = Path("/tmp")


def apply_companies(session, path: Path) -> int:
    if not path.exists():
        return 0
    rows = json.loads(path.read_text())
    n = 0
    for row in rows:
        c = session.get(Company, row["id"])
        if c is None:
            print(f"  [skip] company not found: {row['id']}")
            continue
        if row.get("category") is not None:
            c.category_zh = row["category"]
        if row.get("geography") is not None:
            c.geography_zh = row["geography"]
        for field in ("why_now", "budget_evidence", "buyer_or_route", "internal_notes"):
            if row.get(field) is not None:
                setattr(c, field, row[field])
        n += 1
    return n


def apply_actions_evidence(session, path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    data = json.loads(path.read_text())
    n_a = 0
    for row in data.get("action_items", []):
        a = session.get(ActionItem, row["id"])
        if a is None:
            print(f"  [skip] action_item not found: {row['id']}")
            continue
        for field in ("primary_next_action", "fallback", "success_condition", "outcome_notes"):
            if row.get(field) is not None:
                setattr(a, field, row[field])
        n_a += 1

    n_e = 0
    for row in data.get("sponsorship_evidence", []):
        e = session.get(SponsorshipEvidence, row["id"])
        if e is None:
            print(f"  [skip] sponsorship_evidence not found: {row['id']}")
            continue
        for field in ("evidence_text", "reviewed_note"):
            if row.get(field) is not None:
                setattr(e, field, row[field])
        n_e += 1
    return n_a, n_e


def apply_misc(session, path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    stats = {}

    n = 0
    for row in data.get("operators", []):
        o = session.get(Operator, row["id"])
        if o is None:
            continue
        if row.get("role") is not None:
            o.role = row["role"]
        n += 1
    stats["operators"] = n

    n = 0
    for row in data.get("gtm_cases", []):
        g = session.get(GtmCase, row["id"])
        if g is None:
            continue
        for field in ("maturity_stage", "gtm_motion", "spend_classification", "what_mango_should_copy", "what_not_to_copy"):
            if row.get(field) is not None:
                setattr(g, field, row[field])
        n += 1
    stats["gtm_cases"] = n

    n = 0
    for row in data.get("connector_briefs", []):
        cb = session.get(ConnectorBrief, row["id"])
        if cb is None:
            continue
        if row.get("best_current_action") is not None:
            cb.best_current_action = row["best_current_action"]
        n += 1
    stats["connector_briefs"] = n

    n = 0
    for row in data.get("solomon_reviews", []):
        sr = session.get(SolomonReview, row["id"])
        if sr is None:
            continue
        for field in ("missing_info", "solomon_notes", "next_action"):
            if row.get(field) is not None:
                setattr(sr, field, row[field])
        n += 1
    stats["solomon_reviews"] = n

    return stats


def main():
    init_db()
    session = get_session()

    n_companies = apply_companies(session, TR_DIR / "tr_companies_zh.json")
    n_actions, n_evidence = apply_actions_evidence(session, TR_DIR / "tr_actions_evidence_zh.json")
    misc_stats = apply_misc(session, TR_DIR / "tr_misc_zh.json")

    session.commit()
    session.close()

    print(f"companies: {n_companies}")
    print(f"action_items: {n_actions}")
    print(f"sponsorship_evidence: {n_evidence}")
    print(f"misc: {misc_stats}")


if __name__ == "__main__":
    main()
