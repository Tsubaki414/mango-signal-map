#!/usr/bin/env python3
"""Build Kartr-style creator-brand observations and campaign clusters from the evidence ledger."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from mangobd.discovery import classify_sponsor_disclosure, cluster_campaigns


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "pilot_v4"


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: json.dumps(row.get(field), ensure_ascii=False) if isinstance(row.get(field), list) else row.get(field, "") for field in fields})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records = json.loads((ROOT / "data" / "pilot" / "sponsorships.json").read_text(encoding="utf-8"))
    enriched = []
    for record in records:
        disclosure_class = classify_sponsor_disclosure(record.get("disclosure", ""))
        if record.get("confidence") == "confirmed" and record.get("cooperation_type") in {"official_case_study", "creative_partner_program", "event_sponsorship"}:
            disclosure_class = "confirmed_program_or_official_campaign"
        enriched.append({**record, "disclosure_class": disclosure_class})

    clusters = cluster_campaigns(enriched, window_days=45)
    matrix: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in enriched:
        matrix[(record["creator_id"], record["project_id"])].append(record)
    matrix_rows = []
    for (creator_id, project_id), items in sorted(matrix.items()):
        matrix_rows.append({
            "creator_id": creator_id,
            "project_id": project_id,
            "observation_count": len(items),
            "first_seen": min(item["content_date"] for item in items),
            "last_seen": max(item["content_date"] for item in items),
            "confirmed_paid_or_program_count": sum(item["disclosure_class"].startswith("confirmed") for item in items),
            "affiliate_only_count": sum(item["disclosure_class"] == "affiliate_or_referral" for item in items),
            "repeat_relationship": len(items) >= 2,
            "evidence_ids": sorted({evidence for item in items for evidence in item.get("evidence_ids", [])}),
        })

    (OUT / "sponsor_observations.json").write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "sponsor_campaign_clusters.json").write_text(json.dumps(clusters, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "creator_brand_matrix.json").write_text(json.dumps(matrix_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(OUT / "sponsor_campaign_clusters.csv", clusters, ["project_id","start_date","end_date","content_count","creator_count","creator_ids","confirmed_total_count","confirmed_paid_count","confirmed_program_count","affiliate_count","campaign_signal"])
    write_csv(OUT / "creator_brand_matrix.csv", matrix_rows, ["creator_id","project_id","observation_count","first_seen","last_seen","confirmed_paid_or_program_count","affiliate_only_count","repeat_relationship","evidence_ids"])
    print(json.dumps({"observations":len(enriched),"clusters":len(clusters),"creator_brand_pairs":len(matrix_rows),"output":str(OUT)},indent=2))


if __name__ == "__main__":
    main()
