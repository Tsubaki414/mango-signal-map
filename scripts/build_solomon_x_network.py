#!/usr/bin/env python3
"""Build a Solomon-rooted X degree graph from Rapid X caches only."""

from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from mangobd.x_network import (
    edge_kind,
    enumerate_routable_paths,
    find_user_object,
    load_best_id_cache,
    operational_x_score,
    path_strength,
)


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache" / "rapidx"
DATA = ROOT / "data" / "pilot_v3"
OUT = ROOT / "outputs" / "pilot_v3"
SOLOMON_ID = "1770646033481310208"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "node"


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                field: json.dumps(row.get(field), ensure_ascii=False)
                if isinstance(row.get(field), (list, dict))
                else row.get(field, "")
                for field in fields
            })


def load_known_nodes() -> tuple[dict[str, str], dict[str, str], dict[str, dict]]:
    entities = json.loads((ROOT / "data" / "pilot" / "entities.json").read_text(encoding="utf-8"))
    names: dict[str, str] = {}
    handles: dict[str, str] = {}
    by_handle: dict[str, dict] = {}
    for entity in entities:
        rest_id = str(entity.get("attributes", {}).get("x_rest_id", ""))
        handle = entity.get("handles", {}).get("x", "")
        if not rest_id:
            continue
        names[rest_id] = entity["name"]
        if handle:
            handles[rest_id] = handle
            by_handle[handle.lower()] = entity
    return names, handles, by_handle


def resolve_targets(names: dict[str, str], handles: dict[str, str]) -> list[dict]:
    registry = json.loads((DATA / "target_x_handles.json").read_text(encoding="utf-8"))
    cache_by_handle = {path.stem.removeprefix("target-").lower(): path for path in CACHE.glob("target-*.json")}
    resolved: list[dict] = []
    for target in registry:
        item = dict(target)
        path = cache_by_handle.get(target["handle"].lower())
        user = find_user_object(json.loads(path.read_text(encoding="utf-8"))) if path else None
        if user:
            legacy = user.get("legacy") or {}
            core = user.get("core") or {}
            rest_id = str(user["rest_id"])
            screen_name = legacy.get("screen_name") or core.get("screen_name") or target["handle"]
            display_name = legacy.get("name") or core.get("name") or target["company"]
            item.update({
                "rest_id": rest_id,
                "resolved_handle": screen_name,
                "profile_name": display_name,
                "profile_resolution": "resolved_from_rapid_x_cache",
            })
            names[rest_id] = target["company"]
            handles[rest_id] = screen_name
        else:
            item.update({"rest_id": "", "resolved_handle": "", "profile_name": "", "profile_resolution": "unresolved_requires_rapid_x"})
        resolved.append(item)
    return resolved


def build_adjacency(by_handle: dict[str, dict], targets: list[dict]) -> tuple[dict[str, set[str]], list[dict], list[dict]]:
    adjacency: dict[str, set[str]] = {}
    coverage: list[dict] = []
    for handle_key, entity in sorted(by_handle.items()):
        if handle_key == "mangolabs_":
            continue
        handle = entity.get("handles", {}).get("x", handle_key)
        rest_id = str(entity["attributes"]["x_rest_id"])
        ids, complete, pages = load_best_id_cache(CACHE, handle)
        if not pages:
            continue
        adjacency[rest_id] = ids
        coverage.append({"handle": handle, "rest_id": rest_id, "following_ids": len(ids), "pages": pages, "complete": complete})

    # Mango's 19 public outbound follows were stored as normalized entity profiles.
    mango_id = str(by_handle["mangolabs_"]["attributes"]["x_rest_id"])
    adjacency[mango_id] = {
        str(entity["attributes"]["x_rest_id"])
        for handle, entity in by_handle.items()
        if handle != "mangolabs_" and entity["kind"] in {"person", "creator", "community"}
    }
    coverage.append({"handle": "MangoLabs_", "rest_id": mango_id, "following_ids": len(adjacency[mango_id]), "pages": 1, "complete": True})

    target_coverage: list[dict] = []
    for target in targets:
        rest_id = target.get("rest_id")
        if not rest_id:
            continue
        handle = target.get("resolved_handle") or target["handle"]
        following_ids, following_complete, following_pages = load_best_id_cache(CACHE, handle, "following")
        follower_ids, follower_complete, follower_pages = load_best_id_cache(CACHE, handle, "followers")
        if following_pages:
            adjacency.setdefault(rest_id, set()).update(following_ids)
        # A follower-ID response directly proves follower -> target even when the
        # follower's own outbound set has not been collected.
        for follower_id in follower_ids:
            adjacency.setdefault(follower_id, set()).add(rest_id)
        target_coverage.append({
            "company": target["company"],
            "handle": handle,
            "following_ids": len(following_ids),
            "following_pages": following_pages,
            "following_complete": following_complete,
            "follower_ids": len(follower_ids),
            "follower_pages": follower_pages,
            "follower_complete": follower_complete,
            "follower_coverage_label": "complete" if follower_complete else "sample_or_missing",
        })
    return adjacency, coverage, target_coverage


def label_path(path: list[str], names: dict[str, str], handles: dict[str, str]) -> list[str]:
    labels = []
    for node in path:
        name = names.get(node, node)
        handle = handles.get(node)
        labels.append(f"{name} (@{handle})" if handle else name)
    return labels


def export_graphml(rows: list[dict], names: dict[str, str], handles: dict[str, str], path: Path) -> None:
    ns = "http://graphml.graphdrawing.org/xmlns"
    root = ET.Element(f"{{{ns}}}graphml")
    for key, attribute in [("label", "label"), ("handle", "handle"), ("kind", "kind"), ("relation", "relation")]:
        ET.SubElement(root, f"{{{ns}}}key", {"id": key, "for": "all", "attr.name": attribute, "attr.type": "string"})
    graph = ET.SubElement(root, f"{{{ns}}}graph", {"id": "Solomon_X_Degree_Graph", "edgedefault": "directed"})
    node_ids = {node for row in rows for node in row["path_rest_ids"]}
    target_ids = {row["target_rest_id"] for row in rows}
    for rest_id in sorted(node_ids):
        node = ET.SubElement(graph, f"{{{ns}}}node", {"id": "x_" + rest_id})
        ET.SubElement(node, f"{{{ns}}}data", {"key": "label"}).text = names.get(rest_id, rest_id)
        ET.SubElement(node, f"{{{ns}}}data", {"key": "handle"}).text = handles.get(rest_id, "")
        kind = "root" if rest_id == SOLOMON_ID else "target_company" if rest_id in target_ids else "connector"
        ET.SubElement(node, f"{{{ns}}}data", {"key": "kind"}).text = kind
    seen: set[tuple[str, str]] = set()
    edge_index = 0
    for row in rows:
        for source, target, relation in zip(row["path_rest_ids"], row["path_rest_ids"][1:], row["edge_types"]):
            if (source, target) in seen:
                continue
            seen.add((source, target))
            edge_index += 1
            edge = ET.SubElement(graph, f"{{{ns}}}edge", {"id": f"e{edge_index}", "source": "x_" + source, "target": "x_" + target})
            ET.SubElement(edge, f"{{{ns}}}data", {"key": "relation"}).text = relation
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def build_rankings(path_summary: dict[str, dict]) -> list[dict]:
    companies = json.loads((ROOT / "outputs" / "pilot_v2" / "company_rankings_v2.json").read_text(encoding="utf-8"))
    rows: list[dict] = []
    for company in companies:
        target = path_summary.get(company["company"], {})
        x_score = int(target.get("operational_x_score", 0))
        non_x_fallback = 20 if company["reachability_level"] == "cold_only" else 42
        reachability = max(non_x_fallback, x_score)
        overall = round(
            reachability * 0.35
            + float(company["budget_score"]) * 0.25
            + float(company["fit_score"]) * 0.20
            + float(company["timing_score"]) * 0.10
            + float(company["evidence_quality_score"]) * 0.10,
            1,
        )
        rows.append({
            "company": company["company"],
            "overall_priority_solomon_x": overall,
            "priority_tier": "A" if overall >= 78 else "B" if overall >= 72 else "C",
            "solomon_reachability_score": reachability,
            "solomon_x_path_score": x_score,
            "x_degree": target.get("x_degree", "unresolved_or_no_path"),
            "x_primary_path": target.get("primary_path_labels", []),
            "x_alternative_path_count": target.get("alternative_path_count", 0),
            "x_profile_coverage": target.get("profile_resolution", "unresolved_requires_rapid_x"),
            "non_x_public_fallback_score": non_x_fallback,
            "budget_score": company["budget_score"],
            "fit_score": company["fit_score"],
            "timing_score": company["timing_score"],
            "evidence_quality_score": company["evidence_quality_score"],
            "recommended_next_action": company["recommended_next_action"],
            "note": "X degree is based on public follow edges only; it does not establish interaction, relationship strength, or willingness to introduce.",
        })
    rows.sort(key=lambda row: row["overall_priority_solomon_x"], reverse=True)
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def build_connector_scores(
    adjacency: dict[str, set[str]],
    targets: list[dict],
    names: dict[str, str],
    handles: dict[str, str],
) -> list[dict]:
    target_by_id = {target["rest_id"]: target["company"] for target in targets if target.get("rest_id")}
    known_connector_ids = {rest_id for rest_id in handles if rest_id not in target_by_id and rest_id != SOLOMON_ID}
    rows = []
    for rest_id in sorted(known_connector_ids):
        outgoing = adjacency.get(rest_id)
        if outgoing is None:
            continue
        mutual_solomon = rest_id in adjacency.get(SOLOMON_ID, set()) and SOLOMON_ID in outgoing
        targets_reached = sorted(target_by_id[target] for target in outgoing if target in target_by_id)
        core_mutuals = sorted(
            handles.get(other, other)
            for other in known_connector_ids
            if other != rest_id and other in outgoing and rest_id in adjacency.get(other, set())
        )
        score = min(100, (40 if mutual_solomon else 15 if rest_id in adjacency.get(SOLOMON_ID, set()) else 0) + min(30, len(targets_reached) * 6) + min(20, len(core_mutuals) * 4) + 10)
        rows.append({
            "connector": names.get(rest_id, rest_id),
            "handle": handles.get(rest_id, ""),
            "rest_id": rest_id,
            "solomon_edge": "mutual_follow" if mutual_solomon else "solomon_follows" if rest_id in adjacency.get(SOLOMON_ID, set()) else "other_observed",
            "following_ids_observed": len(outgoing),
            "resolved_targets_reached": targets_reached,
            "resolved_target_count": len(targets_reached),
            "core_mutual_handles": core_mutuals,
            "core_mutual_count": len(core_mutuals),
            "connector_priority_score": score,
            "human_relationship_status": "requires_solomon_validation",
        })
    return sorted(rows, key=lambda row: (-row["connector_priority_score"], row["connector"]))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    names, handles, by_handle = load_known_nodes()
    targets = resolve_targets(names, handles)
    adjacency, connector_coverage, target_coverage = build_adjacency(by_handle, targets)
    path_rows: list[dict] = []
    summaries: dict[str, dict] = {}

    for target in targets:
        rest_id = target["rest_id"]
        paths = enumerate_routable_paths(adjacency, SOLOMON_ID, rest_id, max_hops=3) if rest_id else []
        enriched = sorted(
            [(path, operational_x_score(adjacency, path), path_strength(adjacency, path)) for path in paths],
            key=lambda item: (len(item[0]), -item[1], -item[2], item[0]),
        )
        if enriched:
            primary_path, primary_score, _ = enriched[0]
            summaries[target["company"]] = {
                "x_degree": {1: "direct", 2: "secondary", 3: "third"}[len(primary_path) - 1],
                "operational_x_score": primary_score,
                "primary_path_labels": label_path(primary_path, names, handles),
                "alternative_path_count": len(enriched) - 1,
                "profile_resolution": target["profile_resolution"],
            }
        else:
            summaries[target["company"]] = {
                "x_degree": "no_path_in_audited_graph" if rest_id else "unresolved",
                "operational_x_score": 0,
                "primary_path_labels": [],
                "alternative_path_count": 0,
                "profile_resolution": target["profile_resolution"],
            }
        for index, (path, score, product) in enumerate(enriched, 1):
            path_rows.append({
                "company": target["company"],
                "target_handle": target["resolved_handle"] or target["handle"],
                "target_rest_id": rest_id,
                "x_degree": {1: "direct", 2: "secondary", 3: "third"}[len(path) - 1],
                "hop_count": len(path) - 1,
                "primary": index == 1,
                "path_rest_ids": path,
                "path_labels": label_path(path, names, handles),
                "edge_types": [edge_kind(adjacency, source, destination) for source, destination in zip(path, path[1:])],
                "path_strength_product": product,
                "operational_x_score": score,
                "intro_value_status": "unverified_follow_graph_only",
                "evidence_scope": "Observed Rapid X follow edges; completeness or sampling is recorded separately in the coverage ledger.",
            })

    rankings = build_rankings(summaries)
    actions = json.loads((DATA / "solomon_actions.json").read_text(encoding="utf-8"))
    connector_scores = build_connector_scores(adjacency, targets, names, handles)
    coverage = {
        "verified_date": "2026-08-24",
        "root": {"name": "Solomon", "handle": "Solomon_Nahhh", "rest_id": SOLOMON_ID},
        "definition": {
            "direct": "One observed X follow edge connects Solomon and the target; direction and mutuality are retained separately.",
            "secondary": "Two observed X follow relationships connect Solomon, connector A and the target; hop degree uses a weak graph while each edge keeps its direction.",
            "third": "Three observed X follow relationships connect Solomon, A, B and the target; hop degree uses a weak graph while each edge keeps its direction.",
        },
        "connector_following_coverage": connector_coverage,
        "target_follow_coverage": target_coverage,
        "target_profiles_total": len(targets),
        "target_profiles_resolved_from_rapid_x": sum(bool(target["rest_id"]) for target in targets),
        "direct_target_paths": sum(summary["x_degree"] == "direct" for summary in summaries.values()),
        "secondary_target_paths": sum(summary["x_degree"] == "secondary" for summary in summaries.values()),
        "third_only_target_paths": sum(summary["x_degree"] == "third" for summary in summaries.values()),
        "known_mutuals_with_solomon_among_audited_nodes": [
            handles.get(node, node)
            for node in sorted(adjacency.get(SOLOMON_ID, set()))
            if SOLOMON_ID in adjacency.get(node, set()) and node in handles
        ],
        "unresolved_connector_rest_ids_on_paths": sorted({
            node
            for row in path_rows
            for node in row["path_rest_ids"][1:-1]
            if node not in names
        }),
        "target_status": [{**target, **summaries[target["company"]]} for target in targets],
        "credential_status": "No non-empty RAPID_X_API_KEY is visible in the process environment or the on-disk .env/.env.example at build time.",
    }

    path_fields = ["company","target_handle","target_rest_id","x_degree","hop_count","primary","path_labels","edge_types","path_strength_product","operational_x_score","intro_value_status","evidence_scope","path_rest_ids"]
    ranking_fields = ["rank","company","overall_priority_solomon_x","priority_tier","solomon_reachability_score","solomon_x_path_score","x_degree","x_primary_path","x_alternative_path_count","x_profile_coverage","non_x_public_fallback_score","budget_score","fit_score","timing_score","evidence_quality_score","recommended_next_action","note"]
    action_fields = ["rank","company","owner","primary_connector","x_degree","primary_path","first_step","validation_questions","mango_wedge","fallback","success_condition"]
    connector_fields = ["connector","handle","rest_id","solomon_edge","following_ids_observed","resolved_targets_reached","resolved_target_count","core_mutual_handles","core_mutual_count","connector_priority_score","human_relationship_status"]
    write_csv(OUT / "solomon_x_paths.csv", path_rows, path_fields)
    write_csv(OUT / "company_rankings_solomon_x.csv", rankings, ranking_fields)
    write_csv(OUT / "solomon_action_queue.csv", actions, action_fields)
    write_csv(OUT / "solomon_connector_scores.csv", connector_scores, connector_fields)
    (OUT / "solomon_x_paths.json").write_text(json.dumps(path_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "company_rankings_solomon_x.json").write_text(json.dumps(rankings, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "solomon_action_queue.json").write_text(json.dumps(actions, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "solomon_connector_scores.json").write_text(json.dumps(connector_scores, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "solomon_x_coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    export_graphml(path_rows, names, handles, OUT / "solomon_x_graph.graphml")
    print(json.dumps({"targets":len(targets),"resolved_targets":coverage["target_profiles_resolved_from_rapid_x"],"paths":len(path_rows),"companies_with_secondary":coverage["secondary_target_paths"],"connectors_scored":len(connector_scores),"actions":len(actions),"output":str(OUT)},indent=2))


if __name__ == "__main__":
    main()
