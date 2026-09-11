"""Apply the quote-grounded Top-5 sales packet and buyer-map layer.

This is additive and idempotent. It never creates creator prices or modifies
relationship evidence. Stable company and entry keys make it safe on every
Railway boot.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import Company, CompanyBuyerMapEntry, CompanySalesPacket  # noqa: E402


APP_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = APP_ROOT / "data" / "pilot_v7" / "top5_sales_packets_v7.json"

BUYER_TYPES = {
    "campaign_owner", "partnership_owner", "execution_owner", "economic_buyer",
    "regional_owner", "procurement_route", "measurement_owner", "executive_escalation",
    "operator_candidate", "regional_route", "fallback_program",
}
ROUTE_TYPES = {"Direct", "Intermediary", "Warm candidate", "Cold"}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _read_payload() -> dict:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    packets = payload.get("packets") or []
    entries = payload.get("buyer_map_entries") or []
    company_ids = [row["company_id"] for row in packets]
    if len(packets) != 5 or len(set(company_ids)) != 5:
        raise RuntimeError("v7 sales-packet contract requires exactly five unique companies")
    if len(entries) != 25:
        raise RuntimeError("v7 buyer-map contract requires exactly 25 entries")
    for company_id in company_ids:
        company_entries = [row for row in entries if row["company_id"] == company_id]
        if len(company_entries) != 5:
            raise RuntimeError(f"{company_id} must have exactly five buyer-map entries")
        if not any(row["buyer_type"] == "economic_buyer" for row in company_entries):
            raise RuntimeError(f"{company_id} is missing an explicit economic-buyer truth state")
    for row in packets:
        if len(row.get("qualification_questions") or []) < 5:
            raise RuntimeError(f"{row['company_id']} needs five qualification questions")
        if len(row.get("switch_rules") or []) < 3:
            raise RuntimeError(f"{row['company_id']} needs three fallback switch rules")
    for row in entries:
        if row["company_id"] not in company_ids:
            raise RuntimeError(f"buyer map references a non-Top-5 company: {row}")
        if row["buyer_type"] not in BUYER_TYPES or row["route_type"] not in ROUTE_TYPES:
            raise RuntimeError(f"invalid buyer-map enum: {row}")
        if row["verification_status"].startswith("official") and not row.get("source_url"):
            raise RuntimeError(f"official buyer-map claims need a source URL: {row['entry_key']}")
    return payload


def apply() -> dict:
    payload = _read_payload()
    snapshot_at = dt.datetime.fromisoformat(payload["snapshot_at"])
    init_db()
    stats = {"packets_created": 0, "packets_updated": 0, "buyers_created": 0, "buyers_updated": 0}
    session = get_session()
    try:
        company_ids = {row["company_id"] for row in payload["packets"]}
        known = {row[0] for row in session.query(Company.company_id).filter(Company.company_id.in_(company_ids)).all()}
        if known != company_ids:
            raise RuntimeError(f"v7 references missing companies: {sorted(company_ids - known)}")

        for row in payload["packets"]:
            packet = session.get(CompanySalesPacket, row["company_id"])
            if packet is None:
                packet = CompanySalesPacket(company_id=row["company_id"])
                session.add(packet)
                stats["packets_created"] += 1
            else:
                stats["packets_updated"] += 1
            for field in (
                "packet_version", "offer_name_zh", "commercial_thesis_zh", "audience_zh",
                "email_subject_en", "email_body_en", "x_dm_en", "evidence_note_zh", "confidence",
            ):
                setattr(packet, field, row[field])
            packet.deliverables_json = _json(row.get("deliverables") or [])
            packet.measurement_json = _json(row.get("measurement") or [])
            packet.qualification_questions_json = _json(row.get("qualification_questions") or [])
            packet.switch_rules_json = _json(row.get("switch_rules") or [])
            packet.last_verified_at = snapshot_at

        for row in payload["buyer_map_entries"]:
            entry = (
                session.query(CompanyBuyerMapEntry)
                .filter(
                    CompanyBuyerMapEntry.company_id == row["company_id"],
                    CompanyBuyerMapEntry.entry_key == row["entry_key"],
                )
                .one_or_none()
            )
            if entry is None:
                entry = CompanyBuyerMapEntry(company_id=row["company_id"], entry_key=row["entry_key"])
                session.add(entry)
                stats["buyers_created"] += 1
            else:
                stats["buyers_updated"] += 1
            for field in (
                "buyer_type", "name", "role", "relevance_zh", "verification_status",
                "contact_method", "route_type", "source_url", "evidence_date", "confidence",
                "next_step_zh", "fallback_order",
            ):
                setattr(entry, field, row.get(field))
            entry.last_verified_at = snapshot_at

        session.commit()
        return stats
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    print(json.dumps(apply(), ensure_ascii=False, sort_keys=True))
