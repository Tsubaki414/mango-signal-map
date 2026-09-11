"""Apply the Solomon-facing canonical decision and Asia-review layer.

This migration is additive and idempotent.  It never rewrites legacy
research, relationship, outreach, sponsorship, operator, or campaign data.
The stable company/evidence keys make it safe to run on every Railway boot.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import (  # noqa: E402
    Company,
    CompanyAsiaEvidence,
    CompanyAsiaProfile,
    CompanyDecision,
)


APP_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = APP_ROOT / "data" / "pilot_v6" / "commercial_decisions_and_asia_v6.json"
SNAPSHOT_AT = dt.datetime(2026, 8, 30, 0, 0, 0)

OPPORTUNITY_VALUES = {"tier_a", "tier_b", "tier_c", "archive"}
EXECUTION_READINESS = {"contact_now", "prepare_proposal", "research_buyer_route", "watch", "blocked"}
DECISION_BUCKETS = {"pursue_now", "prepare", "watch", "archive"}
ASIA_STATUSES = {
    "confirmed_expansion",
    "active_market",
    "strong_signal",
    "lead_requiring_verification",
    "no_signal_found",
    "not_reviewed",
}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _read_payload() -> dict:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    decisions = payload.get("decisions") or []
    reviews = payload.get("asia_reviews") or []
    if len(decisions) != 15 or len(reviews) != 15:
        raise RuntimeError("v6 contract requires exactly 15 decisions and 15 Asia reviews")
    decision_ids = [row["company_id"] for row in decisions]
    review_ids = [row["company_id"] for row in reviews]
    if len(set(decision_ids)) != 15 or set(decision_ids) != set(review_ids):
        raise RuntimeError("v6 decision and Asia-review company sets must match exactly")
    for row in decisions:
        if row["opportunity_value"] not in OPPORTUNITY_VALUES:
            raise RuntimeError(f"invalid opportunity value: {row}")
        if row["execution_readiness"] not in EXECUTION_READINESS:
            raise RuntimeError(f"invalid execution readiness: {row}")
        if row["decision_bucket"] not in DECISION_BUCKETS:
            raise RuntimeError(f"invalid decision bucket: {row}")
        if len(row.get("key_unknowns") or []) < 1:
            raise RuntimeError(f"decision needs explicit unknowns: {row['company_id']}")
    for row in reviews:
        if row["asia_interest_status"] not in ASIA_STATUSES:
            raise RuntimeError(f"invalid Asia status: {row}")
        if not row.get("review_scope"):
            raise RuntimeError(f"Asia review scope is required: {row['company_id']}")
    return payload


def apply() -> dict:
    payload = _read_payload()
    init_db()
    stats = {"decisions_created": 0, "decisions_updated": 0, "asia_profiles_created": 0,
             "asia_profiles_updated": 0, "asia_evidence_created": 0, "asia_evidence_updated": 0}
    session = get_session()
    try:
        company_ids = {row["company_id"] for row in payload["decisions"]}
        known = {row[0] for row in session.query(Company.company_id).filter(Company.company_id.in_(company_ids)).all()}
        missing = sorted(company_ids - known)
        if missing:
            raise RuntimeError(f"v6 references missing companies: {missing}")

        for row in payload["decisions"]:
            decision = session.get(CompanyDecision, row["company_id"])
            if decision is None:
                decision = CompanyDecision(company_id=row["company_id"])
                session.add(decision)
                stats["decisions_created"] += 1
            else:
                stats["decisions_updated"] += 1
            for field in (
                "opportunity_value", "execution_readiness", "decision_bucket",
                "opportunity_reason_zh", "why_now_zh", "what_to_sell_zh",
                "buyer_summary_zh", "primary_route_summary_zh", "first_action_zh",
                "fallback_1_zh", "fallback_2_zh",
            ):
                setattr(decision, field, row[field])
            decision.key_unknowns_json = _json(row.get("key_unknowns") or [])
            decision.last_verified_at = SNAPSHOT_AT

        for row in payload["asia_reviews"]:
            profile = session.get(CompanyAsiaProfile, row["company_id"])
            if profile is None:
                profile = CompanyAsiaProfile(company_id=row["company_id"])
                session.add(profile)
                stats["asia_profiles_created"] += 1
            else:
                stats["asia_profiles_updated"] += 1
            for field in (
                "asia_interest_status", "market_context", "asia_signal_summary",
                "confidence", "asia_operator", "localization_status",
                "local_partner_or_customer", "regional_creator_activity",
                "mango_asia_fit", "recommended_market_entry_angle", "review_scope",
            ):
                setattr(profile, field, row.get(field))
            profile.asia_target_markets_json = _json(row.get("asia_target_markets") or [])
            profile.asia_signal_types_json = _json(row.get("asia_signal_types") or [])
            profile.last_verified_at = SNAPSHOT_AT

            for evidence_row in row.get("evidence") or []:
                evidence = (
                    session.query(CompanyAsiaEvidence)
                    .filter(CompanyAsiaEvidence.evidence_id == evidence_row["evidence_id"])
                    .one_or_none()
                )
                if evidence is None:
                    evidence = CompanyAsiaEvidence(evidence_id=evidence_row["evidence_id"])
                    session.add(evidence)
                    stats["asia_evidence_created"] += 1
                else:
                    stats["asia_evidence_updated"] += 1
                evidence.company_id = row["company_id"]
                evidence.target_markets_json = _json(evidence_row.get("target_markets") or [])
                evidence.signal_type = evidence_row["signal_type"]
                evidence.summary = evidence_row["summary"]
                evidence.source_url = evidence_row["source_url"]
                evidence.evidence_date = evidence_row.get("evidence_date")
                evidence.collected_at = SNAPSHOT_AT
                evidence.confidence = evidence_row["confidence"]
                evidence.fact_status = evidence_row["fact_status"]
                evidence.last_verified_at = SNAPSHOT_AT

        session.commit()
        return stats
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    print(json.dumps(apply(), ensure_ascii=False, sort_keys=True))
