"""Real-data check (not a data migration): for each (bridge, target) pair
in PAIRS, run TWO separate directional X searches -- from:bridge to:target
and from:target to:bridge -- via Rapid X's search endpoint, never one
OR'd query, per the 2026-08-28 spec's explicit requirement to avoid
direction ambiguity about which side actually produced a match. Every
match found is stored as an independently auditable InteractionEvidence
row (tweet id, clickable URL, timestamp, author/recipient, interaction
type, and the minimal context text) attached to an InteractionCheck row
that records the exact query string, when it ran, and the API's known
coverage limits -- "not found" must always be re-derivable as "not found
within this specific query", never presented as "these two don't know
each other".

This is a genuinely different, stronger signal than
IntroBridge.mutual_with_target (which only proves both accounts follow
the target -- not that they've ever spoken). Still not proof of a real
relationship or willingness to introduce -- a human still has to judge
that -- but it turns "unverified mutual-follow candidate" into either
"some public back-and-forth exists" (with a link to verify it) or "zero
interaction ever found" (within this query's coverage), which is real
information either way.

Writes InteractionCheck/InteractionEvidence rows directly onto the local
DB, then snapshots to data/interaction_evidence_snapshot.json for
scripts/apply_interaction_evidence_snapshot.py to sync onto the Railway
volume (a plain redeploy does not touch existing data -- see project
memory).

Usage: python3 scripts/check_bridge_interaction_evidence.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mangobd.rapid_x import RapidXClient  # noqa: E402

from backend.db import get_session  # noqa: E402
from backend.models import IntroBridge, InteractionCheck, InteractionEvidence  # noqa: E402

COVERAGE_NOTE = (
    "基于 X 搜索接口单次查询结果（search_type=Latest, count=20），接口对历史推文的索引范围不透明、"
    "不保证完整覆盖；未命中仅代表本次查询范围内未发现，不代表两人不认识或历史上从无互动。"
)

# (bridge_id, bridge_handle, target_handle, company_id) -- bridge_id is
# the LOCAL DB's IntroBridge.id, resolved by hand from the top-by-followers
# candidates for each Group A company (Olas/Sapien/GAIB/Heurist), per the
# 2026-08-28 audit. company_id + target_handle carried into the snapshot
# so scripts/apply_interaction_evidence_snapshot.py can re-match on the
# live DB, where auto-increment ids can differ.
PAIRS = [
    (6, "cheryldchan", "david_enim", "company:olas"),
    (5, "roxinft", "RowanRK6", "company:sapien"),
    (4, "calchulus", "RowanRK6", "company:sapien"),
    (65, "cyrilxuq", "Mathilda_Sun_", "company:gaib"),
    (64, "UnicornBitcoin", "Mathilda_Sun_", "company:gaib"),
    (24, "momochenming", "Mathilda_Sun_", "company:gaib"),
    (57, "lijiuer1", "Mathilda_Sun_", "company:gaib"),
    (73, "BroLeon", "Mathilda_Sun_", "company:gaib"),
    (39, "KuiGas", "Mathilda_Sun_", "company:gaib"),
    (12, "yuyue_chris", "Mathilda_Sun_", "company:gaib"),
    (30, "xincctnnq", "Mathilda_Sun_", "company:gaib"),
    (8, "xingpt", "0takuwaifu", "company:heurist"),
    (7, "RaccoonHKG", "0takuwaifu", "company:heurist"),
    (10, "mrdudu", "0takuwaifu", "company:heurist"),
    (9, "zepump", "0takuwaifu", "company:heurist"),
]


def _parse_twitter_date(raw: str | None) -> dt.datetime | None:
    if not raw:
        return None
    try:
        return dt.datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y").replace(tzinfo=None)
    except ValueError:
        return None


def _extract_evidence(search_result: dict, from_handle: str, to_handle: str) -> list[dict]:
    rows = []
    instructions = search_result.get("result", {}).get("timeline", {}).get("instructions", [])
    for block in instructions:
        for entry in block.get("entries", []):
            content = entry.get("content", {})
            if content.get("entryType") != "TimelineTimelineItem":
                continue
            item = content.get("itemContent", {})
            tw = item.get("tweet_results", {}).get("result", {})
            legacy = tw.get("legacy", {})
            if not legacy:
                continue
            tweet_id = tw.get("rest_id") or legacy.get("id_str")
            if legacy.get("in_reply_to_screen_name", "").lower() == to_handle.lower():
                interaction_type = "reply"
            elif legacy.get("is_quote_status"):
                interaction_type = "quote"
            else:
                interaction_type = "mention"
            rows.append(
                {
                    "tweet_id": tweet_id,
                    "tweet_url": f"https://x.com/{from_handle}/status/{tweet_id}" if tweet_id else None,
                    "posted_at": _parse_twitter_date(legacy.get("created_at")),
                    "author_handle": from_handle,
                    "recipient_handle": to_handle,
                    "context_text": (legacy.get("full_text") or "")[:280] or None,
                    "interaction_type": interaction_type,
                    "is_auditable": tweet_id is not None,
                }
            )
    return rows


def main() -> None:
    client = RapidXClient(env_file=str(ROOT / ".env"), cache_dir=str(ROOT / "data" / "cache" / "rapidx"))
    session = get_session()
    snapshot_checks = []

    try:
        for bridge_id, bridge_handle, target_handle, company_id in PAIRS:
            bridge = session.get(IntroBridge, bridge_id)
            if bridge is None or bridge.bridge_handle != bridge_handle:
                print(f"[skip] IntroBridge {bridge_id} not found or handle mismatch, expected {bridge_handle}")
                continue

            # Clear any prior checks for this bridge -- re-running is meant
            # to refresh, not accumulate duplicate audit rows for the same pair.
            for old_check in list(bridge.interaction_checks):
                session.delete(old_check)
            session.flush()

            for from_h, to_h in ((bridge_handle, target_handle), (target_handle, bridge_handle)):
                query = f"from:{from_h} to:{to_h}"
                try:
                    result = client.search(query, search_type="Latest", count=20)
                except Exception as exc:  # noqa: BLE001
                    print(f"[error] {query}: {exc}")
                    continue

                evidence_rows = _extract_evidence(result, from_h, to_h)
                check = InteractionCheck(
                    bridge_id=bridge.id,
                    from_handle=from_h,
                    to_handle=to_h,
                    query_string=query,
                    coverage_note=COVERAGE_NOTE,
                )
                for row in evidence_rows:
                    check.evidence.append(InteractionEvidence(**row))
                session.add(check)

                print(f"{query:45s} : {len(evidence_rows)} hit(s)")
                snapshot_checks.append(
                    {
                        "company_id": company_id,
                        "target_handle": target_handle,
                        "bridge_handle": bridge_handle,
                        "from_handle": from_h,
                        "to_handle": to_h,
                        "query_string": query,
                        "coverage_note": COVERAGE_NOTE,
                        "evidence": [
                            {**row, "posted_at": row["posted_at"].isoformat() if row["posted_at"] else None}
                            for row in evidence_rows
                        ],
                    }
                )
                time.sleep(0.4)

        session.commit()
    finally:
        session.close()

    snapshot_path = Path(__file__).resolve().parent.parent / "data" / "interaction_evidence_snapshot.json"
    snapshot_path.write_text(json.dumps({"checks": snapshot_checks}, ensure_ascii=False, indent=2))
    print(f"\nwrote {len(snapshot_checks)} check rows to {snapshot_path}")


if __name__ == "__main__":
    main()
