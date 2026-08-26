#!/usr/bin/env python3
"""Merge v1 targets with expanded v2 companies and export degree-aware routing data."""

from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "outputs" / "pilot"
DATA = ROOT / "data" / "pilot_v2"
OUT = ROOT / "outputs" / "pilot_v2"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), (list, dict)) else row.get(key, "") for key in fields})


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "node"


def merged_companies() -> list[dict]:
    existing = read_csv(V1 / "projects.csv")
    merged: list[dict] = []
    for project in existing:
        score = float(project["overall_priority"])
        reachability = project["reachability_level"]
        reachability_score = float(project["reachability_score"])
        if project["company"] == "Gamma":
            reachability = "verified_third_degree"
            reachability_score = 52.0
            score = round(
                reachability_score * 0.35
                + float(project["budget_score"]) * 0.25
                + float(project["fit_score"]) * 0.20
                + float(project["timing_score"]) * 0.10
                + float(project["evidence_quality_score"]) * 0.10,
                1,
            )
        merged.append({
            "company": project["company"],
            "category": project["category"],
            "source_set": "pilot_v1_corrected",
            "reachability_level": reachability,
            "reachability_score": reachability_score,
            "budget_score": float(project["budget_score"]),
            "fit_score": float(project["fit_score"]),
            "timing_score": float(project["timing_score"]),
            "evidence_quality_score": float(project["evidence_quality_score"]),
            "overall_priority": score,
            "priority_tier": "A" if score >= 78 else "B" if score >= 72 else "C",
            "budget_confidence": project["budget_confidence"],
            "linkedin_best_degree": "see degree audit",
            "operators": json.loads(project["decision_maker_ids"]),
            "programs": json.loads(project["historical_campaigns"]),
            "mango_wedge": project["recommended_next_action"],
            "recommended_next_action": project["recommended_next_action"],
            "sources": json.loads(project["evidence_ids"]),
        })
    merged.extend(json.loads((DATA / "expanded_companies.json").read_text(encoding="utf-8")))
    merged.sort(key=lambda row: float(row["overall_priority"]), reverse=True)
    for index, row in enumerate(merged, 1):
        row["rank"] = index
    return merged


def relationship_paths(audit: list[dict], x_audit: dict) -> list[dict]:
    rows = []
    for item in audit:
        connectors = item["mutual_connectors"] or [""]
        rows.append({
            "platform": "LinkedIn",
            "company": item["company"],
            "target_person": item["person"],
            "target_role": item["role"],
            "degree": item["degree"],
            "connector": " | ".join(connectors),
            "route_status": item["route_status"],
            "current_role_confirmed": item["current_role_confirmed"],
            "note": item["note"],
        })
    for handle in x_audit["confirmed_findings"]["mango_mutuals_among_fully_audited_seeds"]:
        rows.append({"platform":"X","company":"Mango network","target_person":handle,"target_role":"Mango seed","degree":"direct mutual","connector":"","route_status":"confirmed_reciprocal_follow","current_role_confirmed":"","note":"Public reciprocal follow only; relationship strength and intro willingness remain unverified."})
    for company in x_audit["confirmed_findings"]["seed_to_resolved_target_matches"]["jenniekusu"]:
        rows.append({"platform":"X","company":company,"target_person":"Jennie Liu","target_role":"Connector candidate","degree":"secondary weak signal","connector":"Mango mutual on X","route_status":"x_follow_only","current_role_confirmed":"","note":"Jennie follows the company account; no interaction or intro willingness established."})
    return rows


def write_graphml(paths: list[dict], path: Path) -> None:
    ns = "http://graphml.graphdrawing.org/xmlns"
    root = ET.Element(f"{{{ns}}}graphml")
    for key_id, name in [("label", "label"), ("kind", "kind"), ("relation", "relation"), ("platform", "platform"), ("status", "status")]:
        ET.SubElement(root, f"{{{ns}}}key", {"id": key_id, "for": "all", "attr.name": name, "attr.type": "string"})
    graph = ET.SubElement(root, f"{{{ns}}}graph", {"id": "MangoBD_v2", "edgedefault": "directed"})
    nodes: dict[str, tuple[str, str]] = {"mango": ("Mango Labs", "organization"), "linkedin_root": ("Authenticated LinkedIn account", "network_root")}
    edges = []
    for index, row in enumerate(paths, 1):
        target_id = "person_" + slug(row["target_person"])
        nodes[target_id] = (row["target_person"], "person")
        if row["platform"] == "LinkedIn" and row["connector"]:
            for connector_index, connector in enumerate(row["connector"].split(" | "), 1):
                connector_id = "connector_" + slug(connector)
                nodes[connector_id] = (connector, "connector")
                edges.append((f"e{index}a{connector_index}", "linkedin_root", connector_id, "first_degree", "LinkedIn", "connector visible"))
                edges.append((f"e{index}b{connector_index}", connector_id, target_id, row["degree"], "LinkedIn", row["route_status"]))
        elif row["platform"] == "X" and row["company"] == "Mango network":
            edges.append((f"e{index}", "mango", target_id, "mutual_follow", "X", row["route_status"]))
        else:
            edges.append((f"e{index}", "mango", target_id, row["degree"], row["platform"], row["route_status"]))
    for node_id, (label, kind) in nodes.items():
        node = ET.SubElement(graph, f"{{{ns}}}node", {"id": node_id})
        ET.SubElement(node, f"{{{ns}}}data", {"key": "label"}).text = label
        ET.SubElement(node, f"{{{ns}}}data", {"key": "kind"}).text = kind
    for edge_id, source, target, relation, platform, status in edges:
        edge = ET.SubElement(graph, f"{{{ns}}}edge", {"id": edge_id, "source": source, "target": target})
        ET.SubElement(edge, f"{{{ns}}}data", {"key": "relation"}).text = relation
        ET.SubElement(edge, f"{{{ns}}}data", {"key": "platform"}).text = platform
        ET.SubElement(edge, f"{{{ns}}}data", {"key": "status"}).text = status
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    companies = merged_companies()
    audit = json.loads((DATA / "linkedin_degree_audit.json").read_text(encoding="utf-8"))
    x_audit = json.loads((DATA / "x_network_audit.json").read_text(encoding="utf-8"))
    paths = relationship_paths(audit, x_audit)
    company_fields = ["rank","company","category","source_set","priority_tier","overall_priority","reachability_level","reachability_score","linkedin_best_degree","budget_score","fit_score","timing_score","evidence_quality_score","budget_confidence","operators","programs","mango_wedge","recommended_next_action","sources"]
    path_fields = ["platform","company","target_person","target_role","degree","connector","route_status","current_role_confirmed","note"]
    write_csv(OUT / "company_rankings_v2.csv", companies, company_fields)
    write_csv(OUT / "relationship_paths_v2.csv", paths, path_fields)
    (OUT / "company_rankings_v2.json").write_text(json.dumps(companies, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "relationship_paths_v2.json").write_text(json.dumps(paths, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "x_network_audit.json").write_text(json.dumps(x_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    write_graphml(paths, OUT / "relationship_graph_v2.graphml")
    print(json.dumps({"companies":len(companies),"expanded":len([x for x in companies if x["source_set"]=="expansion_v2"]),"relationship_paths":len(paths),"output":str(OUT)}, indent=2))


if __name__ == "__main__":
    main()
