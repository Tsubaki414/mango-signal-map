#!/usr/bin/env python3
"""Build the compact, typed Mango BD v4 business knowledge graph.

This graph deliberately differs from the large X-network GraphML.  It joins the
business entities and only the X accounts/edges that participate in retained
routes.  It never turns a follow, an interaction, or a path into an assertion
that a human is willing to make an introduction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "pilot_v4"
DATA = ROOT / "data" / "pilot_v4"

SOURCE_FILES = {
    "companies": OUT / "all_company_universe_v4.json",
    "target_identities": OUT / "mango_seed_target_paths.json",
    "mango_seeds": OUT / "mango_seed_network_audit.json",
    "solomon_v4_paths": OUT / "solomon_paths_expanded_v4.json",
    "solomon_v3_paths": ROOT / "outputs" / "pilot_v3" / "solomon_x_paths.json",
    "mango_paths": OUT / "mango_seed_target_paths.json",
    "solomon_interactions": OUT / "x_path_interactions_v4.json",
    "mango_interactions": OUT / "mango_path_interactions_v4.json",
    "sponsor_observations": OUT / "sponsor_expanded_observations.json",
    "sponsor_edges": OUT / "sponsor_expanded_edges.json",
    "operator_routes": DATA / "top_operator_routes.json",
    "operator_person_paths": OUT / "operator_person_paths_v4.json",
    "gtm_cases": DATA / "gtm_case_studies_v4.json",
}

SCHEMA_VERSION = "mango-bd-business-graph-v4.2"
FACT_STATUSES = {"confirmed", "probable", "unverified"}
CONFIDENCES = {"high", "medium", "low"}
# Explicit product/display alias already adjudicated by the v4 database layer.
# This is intentionally not a fuzzy-matching rule.
KNOWN_COMPANY_ALIASES = {"viggle": "viggleai"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def slug(value: str) -> str:
    raw = str(value).casefold().strip().lstrip("@")
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if ascii_slug:
        return ascii_slug
    return f"u-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def artifact_ref(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def file_verified_at(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def artifact_observed_at(reference: str, fallback: str) -> str:
    path = Path(str(reference or ""))
    if not path.is_absolute():
        path = ROOT / path
    return file_verified_at(path) if path.exists() else fallback


def latest_timestamp(*values: str | None) -> str:
    present = [str(value) for value in values if value]
    return max(present) if present else ""


def text_confidence(value: Any) -> str:
    if isinstance(value, (int, float)):
        return "high" if value >= 0.9 else "medium" if value >= 0.7 else "low"
    normalized = str(value or "").casefold()
    return normalized if normalized in CONFIDENCES else "medium"


def evidence(
    source: str,
    observed_at: str,
    evidence_type: str,
    claim: str,
    *,
    source_url: str = "",
    evidence_id: str = "",
) -> dict[str, str]:
    return {
        "evidence_id": evidence_id or source,
        "source": source,
        "source_url": source_url,
        "observed_at": observed_at,
        "evidence_type": evidence_type,
        "claim": claim,
    }


class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        self.paths: list[dict[str, Any]] = []
        self.handle_to_x_id: dict[str, str] = {}
        self.company_by_normalized_name: dict[str, str] = {}

    def add_node(
        self,
        node_id: str,
        node_type: str,
        label: str,
        *,
        aliases: Iterable[str] = (),
        properties: dict[str, Any] | None = None,
        evidence_items: list[dict[str, Any]] | None = None,
    ) -> str:
        aliases_clean = sorted({str(item) for item in aliases if item and str(item) != label})
        if node_id in self.nodes:
            node = self.nodes[node_id]
            if node["node_type"] != node_type:
                raise ValueError(f"node type conflict for {node_id}: {node['node_type']} vs {node_type}")
            if node["label"] == node_id and label != node_id:
                node["label"] = label
            node["aliases"] = sorted(set(node["aliases"]) | set(aliases_clean))
            node["properties"].update(properties or {})
            self._extend_unique(node["evidence"], evidence_items or [])
            return node_id
        self.nodes[node_id] = {
            "node_id": node_id,
            "node_type": node_type,
            "label": label,
            "aliases": aliases_clean,
            "properties": properties or {},
            "evidence": evidence_items or [],
        }
        return node_id

    @staticmethod
    def _extend_unique(target: list[Any], additions: Iterable[Any]) -> None:
        seen = {compact(item) for item in target}
        for item in additions:
            key = compact(item)
            if key not in seen:
                target.append(item)
                seen.add(key)

    def add_edge(
        self,
        edge_id: str,
        source: str,
        target: str,
        edge_type: str,
        edge_category: str,
        *,
        fact_status: str,
        confidence: str,
        last_verified_at: str,
        evidence_items: list[dict[str, Any]],
        provenance: list[dict[str, Any]],
        properties: dict[str, Any] | None = None,
        merge: bool = False,
    ) -> str:
        if source not in self.nodes or target not in self.nodes:
            raise ValueError(f"edge endpoint missing for {edge_id}: {source} -> {target}")
        if fact_status not in FACT_STATUSES:
            raise ValueError(f"invalid fact_status {fact_status!r}")
        if confidence not in CONFIDENCES:
            raise ValueError(f"invalid confidence {confidence!r}")
        if not evidence_items or not provenance or not last_verified_at:
            raise ValueError(f"edge {edge_id} lacks evidence/provenance/freshness")
        if edge_id in self.edges:
            existing = self.edges[edge_id]
            same = (existing["source"], existing["target"], existing["edge_type"]) == (source, target, edge_type)
            if not merge or not same:
                raise ValueError(f"duplicate edge id {edge_id}")
            self._extend_unique(existing["evidence"], evidence_items)
            self._extend_unique(existing["provenance"], provenance)
            existing["last_verified_at"] = latest_timestamp(existing["last_verified_at"], last_verified_at)
            existing["properties"].update(properties or {})
            return edge_id
        self.edges[edge_id] = {
            "edge_id": edge_id,
            "source": source,
            "target": target,
            "edge_type": edge_type,
            "edge_category": edge_category,
            "fact_status": fact_status,
            "confidence": confidence,
            "last_verified_at": last_verified_at,
            "evidence": evidence_items,
            "provenance": provenance,
            "properties": properties or {},
        }
        return edge_id

    def ensure_x_account(self, rest_id: str, label: str = "", handle: str = "", **properties: Any) -> str:
        rest_id = str(rest_id or "").strip()
        handle = str(handle or "").strip().lstrip("@")
        if not handle and label:
            matches = re.findall(r"@([A-Za-z0-9_]{1,15})", label)
            handle = matches[-1] if matches else ""
        if rest_id:
            node_id = f"x_account:{rest_id}"
        elif handle:
            known = self.handle_to_x_id.get(handle.casefold())
            node_id = known or f"x_account_handle:{handle.casefold()}"
        else:
            raise ValueError("X account requires a REST ID or handle")
        display = label.strip() or (f"@{handle}" if handle else f"X account {rest_id}")
        if " (@" in display:
            display = display.rsplit(" (@", 1)[0]
        self.add_node(node_id, "x_account", display, properties={"rest_id": rest_id, "handle": handle, **properties})
        if handle:
            self.handle_to_x_id[handle.casefold()] = node_id
        return node_id

    def add_follow(
        self,
        source_rest_id: str,
        target_rest_id: str,
        *,
        source_label: str = "",
        target_label: str = "",
        last_verified_at: str,
        evidence_items: list[dict[str, Any]],
        provenance: list[dict[str, Any]],
        properties: dict[str, Any] | None = None,
    ) -> str:
        source = self.ensure_x_account(source_rest_id, source_label)
        target = self.ensure_x_account(target_rest_id, target_label)
        edge_id = f"edge:follows:{source}:{target}"
        return self.add_edge(
            edge_id, source, target, "FOLLOWS", "public_social",
            fact_status="confirmed", confidence="high", last_verified_at=last_verified_at,
            evidence_items=evidence_items, provenance=provenance, properties=properties, merge=True,
        )


def build_companies(graph: GraphBuilder, companies: list[dict[str, Any]]) -> dict[str, str]:
    ids: dict[str, str] = {}
    source = artifact_ref(SOURCE_FILES["companies"])
    verified = file_verified_at(SOURCE_FILES["companies"])
    for row in companies:
        company_id = f"company:{slug(row['company'])}"
        ids[normalize_name(row["company"])] = company_id
        graph.company_by_normalized_name[normalize_name(row["company"])] = company_id
        for alias in row.get("aliases") or []:
            graph.company_by_normalized_name[normalize_name(alias)] = company_id
        graph.add_node(
            company_id, "company", row["company"], aliases=row.get("aliases") or [],
            properties={
                key: row.get(key) for key in (
                    "category", "cohort", "cohort_group", "priority_tier", "score_value",
                    "score_method", "spend_evidence_level", "surf_status", "why_now", "budget_evidence",
                )
            },
            evidence_items=[evidence(source, verified, "canonical_dataset", "Company is present in the 77-company v4 universe.")],
        )
    for alias_key, canonical_key in KNOWN_COMPANY_ALIASES.items():
        company_id = ids.get(canonical_key)
        if not company_id:
            raise ValueError(f"Known alias target is absent: {canonical_key}")
        graph.company_by_normalized_name[alias_key] = company_id
        graph.nodes[company_id]["aliases"] = sorted(set(graph.nodes[company_id]["aliases"]) | {"Viggle"})
    return ids


def build_target_identities(graph: GraphBuilder, company_ids: dict[str, str], artifact: dict[str, Any]) -> int:
    source = artifact_ref(SOURCE_FILES["target_identities"])
    count = 0
    for row in artifact.get("targets") or []:
        if row.get("technical_identity_confirmed") is not True or not row.get("target_rest_id"):
            continue
        company_id = company_ids[normalize_name(row["company"])]
        x_id = graph.ensure_x_account(
            str(row["target_rest_id"]), handle=str(row.get("target_handle") or ""),
            technical_identity_status=row.get("technical_x_identity_status"),
        )
        verified = row.get("identity_last_verified_at") or row.get("last_verified_at") or file_verified_at(SOURCE_FILES["target_identities"])
        evidence_files = row.get("identity_evidence_files") or [source]
        evidence_items = [
            evidence(str(item), verified, row.get("identity_evidence_type") or "first_party_social", "Exact technical X identity match.")
            for item in evidence_files
        ]
        graph.add_edge(
            f"edge:has_x_account:{company_id}:{x_id}", company_id, x_id, "HAS_X_ACCOUNT", "entity_identity",
            fact_status="confirmed", confidence="high", last_verified_at=verified,
            evidence_items=evidence_items,
            provenance=[{"source_artifact": source, "source_record_id": row["company"], "method": "exact Rapid X handle/REST-ID match"}],
            properties={"identity_status": row.get("technical_x_identity_status")},
        )
        count += 1
    return count


def build_mango_people(graph: GraphBuilder, artifact: dict[str, Any]) -> int:
    source = artifact_ref(SOURCE_FILES["mango_seeds"])
    org_id = graph.add_node("organization:mango-labs", "organization", "Mango Labs")
    count = 0
    for row in artifact.get("seeds") or []:
        verified = row.get("last_verified_at") or file_verified_at(SOURCE_FILES["mango_seeds"])
        x_id = graph.ensure_x_account(str(row.get("rest_id") or ""), handle=str(row.get("handle") or ""))
        ev = [evidence(source, verified, row.get("evidence_type") or "first_party_social", "Exact seed-account identity observed in the Mango seed audit.", evidence_id=row["entity_id"])]
        prov = [{"source_artifact": source, "source_record_id": row["entity_id"], "method": "seed profile exact-handle audit"}]
        if row["entity_id"] == "mango":
            graph.add_edge(
                f"edge:has_x_account:{org_id}:{x_id}", org_id, x_id, "HAS_X_ACCOUNT", "entity_identity",
                fact_status="confirmed", confidence="high", last_verified_at=verified,
                evidence_items=ev, provenance=prov,
            )
        else:
            person_id = graph.add_node(f"person:mango-seed:{slug(row['entity_id'])}", "person", row["name"])
            graph.add_edge(
                f"edge:has_x_account:{person_id}:{x_id}", person_id, x_id, "HAS_X_ACCOUNT", "entity_identity",
                fact_status="confirmed", confidence="high", last_verified_at=verified,
                evidence_items=ev, provenance=prov,
            )
            graph.add_edge(
                f"edge:audit_seed:{org_id}:{x_id}", org_id, x_id, "AUDIT_SEED_ACCOUNT", "administrative_scope",
                fact_status="confirmed", confidence="high", last_verified_at=verified,
                evidence_items=ev, provenance=prov,
                properties={"does_not_assert": ["employment", "team_membership", "introduction_willingness"]},
            )
        count += 1
    return count


def label_map(rest_ids: list[str], labels: list[str]) -> dict[str, str]:
    return {str(rest_id): str(labels[index]) for index, rest_id in enumerate(rest_ids) if index < len(labels)}


def path_record(
    path_id: str,
    root: str,
    company_id: str,
    row: dict[str, Any],
    edge_ids: list[str],
    source: str,
    primary: bool,
    verified: str,
) -> dict[str, Any]:
    return {
        "path_id": path_id,
        "root": root,
        "company_id": company_id,
        "company_context_id": company_id,
        "target_entity_id": company_id,
        "path_target_type": "company_x_account",
        "primary": primary,
        "hop_count": row.get("hop_count") or row.get("x_degree"),
        "degree_label": row.get("x_degree") if isinstance(row.get("x_degree"), str) else row.get("x_relationship_level"),
        "path_node_ids": [f"x_account:{item}" for item in row.get("path_rest_ids") or []],
        "edge_ids": edge_ids,
        "human_intro_status": row.get("human_intro_status") or "human_intro_unvalidated",
        "introduction_willingness_evidence": [],
        "fact_status": "confirmed",
        "confidence": "high",
        "last_verified_at": verified,
        "evidence": [evidence(source, verified, "first_party_social", "Retained graph path built from positive observed directed X follow edges.", evidence_id=path_id)],
        "provenance": [{"source_artifact": source, "source_record_id": path_id, "method": "directed path materialization"}],
        "properties": {
            "path_labels": row.get("path_labels") or [],
            "path_strength": row.get("path_strength") or row.get("path_strength_product"),
            "operational_x_score": row.get("operational_x_score"),
            "evidence_scope": row.get("evidence_scope"),
        },
    }


def add_v4_solomon_path_edges(graph: GraphBuilder, company_ids: dict[str, str], rows: list[dict[str, Any]]) -> int:
    source_path = SOURCE_FILES["solomon_v4_paths"]
    source = artifact_ref(source_path)
    verified = file_verified_at(source_path)
    path_count = 0
    for company_row in rows:
        if company_row.get("graph_reachable") is not True:
            continue
        company_id = company_ids[normalize_name(company_row["company"])]
        paths = [(True, company_row.get("primary_path"))] + [(False, item) for item in company_row.get("alternative_paths") or []]
        alt_index = 0
        for primary, row in paths:
            if not row:
                continue
            alt_index += 0 if primary else 1
            path_id = f"path:solomon:v4:{slug(company_row['company'])}:{'primary' if primary else f'alternative-{alt_index:02d}'}"
            rest_ids = [str(item) for item in row.get("path_rest_ids") or []]
            labels = [str(item) for item in row.get("path_labels") or []]
            labels_by_id = label_map(rest_ids, labels)
            edge_ids: list[str] = []
            for detail in row.get("edge_details") or []:
                for observed in detail.get("observed_directed_edges") or []:
                    src = str(observed["source_rest_id"])
                    dst = str(observed["target_rest_id"])
                    caches = observed.get("cache_files") or [source]
                    ev = [
                        evidence(str(cache), verified, "first_party_social", f"Rapid X positive observation: {src} follows {dst}.")
                        for cache in caches
                    ]
                    edge_ids.append(graph.add_follow(
                        src, dst, source_label=labels_by_id.get(src, ""), target_label=labels_by_id.get(dst, ""),
                        last_verified_at=verified, evidence_items=ev,
                        provenance=[{"source_artifact": source, "source_record_id": path_id, "method": "positive cached Rapid X follow observation"}],
                        properties={"coverage_labels": observed.get("coverage_labels") or [], "roles": observed.get("roles") or []},
                    ))
            graph.paths.append(path_record(path_id, "solomon", company_id, row, list(dict.fromkeys(edge_ids)), source, primary, verified))
            path_count += 1
    return path_count


def add_legacy_solomon_paths(graph: GraphBuilder, company_ids: dict[str, str], rows: list[dict[str, Any]]) -> int:
    source_path = SOURCE_FILES["solomon_v3_paths"]
    source = artifact_ref(source_path)
    verified = file_verified_at(source_path)
    count = 0
    for index, row in enumerate(rows, 1):
        company_id = company_ids.get(normalize_name(row["company"]))
        if not company_id:
            continue
        rest_ids = [str(item) for item in row.get("path_rest_ids") or []]
        labels = [str(item) for item in row.get("path_labels") or []]
        labels_by_id = label_map(rest_ids, labels)
        edge_ids: list[str] = []
        path_id = f"path:solomon:v3:{slug(row['company'])}:{index:03d}"
        for hop_index, kind in enumerate(row.get("edge_types") or []):
            if hop_index + 1 >= len(rest_ids):
                break
            src, dst = rest_ids[hop_index], rest_ids[hop_index + 1]
            directions = [(src, dst), (dst, src)] if kind == "mutual_follow" else [(src, dst)]
            for observed_src, observed_dst in directions:
                edge_ids.append(graph.add_follow(
                    observed_src, observed_dst,
                    source_label=labels_by_id.get(observed_src, ""), target_label=labels_by_id.get(observed_dst, ""),
                    last_verified_at=verified,
                    evidence_items=[evidence(source, verified, "first_party_social", f"Legacy Rapid X positive follow observation: {observed_src} follows {observed_dst}.", evidence_id=path_id)],
                    provenance=[{"source_artifact": source, "source_record_id": path_id, "method": "legacy retained directed path"}],
                    properties={"legacy_v3": True},
                ))
        graph.paths.append(path_record(path_id, "solomon", company_id, row, list(dict.fromkeys(edge_ids)), source, bool(row.get("primary")), verified))
        count += 1
    return count


def add_mango_paths(graph: GraphBuilder, company_ids: dict[str, str], artifact: dict[str, Any]) -> int:
    source_path = SOURCE_FILES["mango_paths"]
    source = artifact_ref(source_path)
    fallback_verified = artifact.get("generated_at") or file_verified_at(source_path)
    count = 0
    for target in artifact.get("targets") or []:
        company_id = company_ids[normalize_name(target["company"])]
        routes = list(target.get("mango_direct_paths") or []) + list(target.get("mango_secondary_paths") or [])
        for index, row in enumerate(routes, 1):
            rest_ids = [str(item) for item in row.get("path_rest_ids") or []]
            labels = [str(item) for item in row.get("path_labels") or []]
            labels_by_id = label_map(rest_ids, labels)
            verified = row.get("last_verified_at") or fallback_verified
            path_id = f"path:mango:{slug(target['company'])}:{index:03d}"
            edge_ids: list[str] = []
            for direction in row.get("edge_rest_id_directions") or []:
                if "->" not in direction:
                    continue
                src, dst = (part.strip() for part in direction.split("->", 1))
                edge_ids.append(graph.add_follow(
                    src, dst, source_label=labels_by_id.get(src, ""), target_label=labels_by_id.get(dst, ""),
                    last_verified_at=verified,
                    evidence_items=[evidence(item, verified, "first_party_social", f"Rapid X positive observation: {src} follows {dst}.", evidence_id=path_id) for item in row.get("evidence_files") or [source]],
                    provenance=[{"source_artifact": source, "source_record_id": path_id, "method": "positive cached Rapid X follow observation"}],
                    properties={"route_root": "mango"},
                ))
            normalized_row = dict(row)
            normalized_row.setdefault("hop_count", row.get("x_degree"))
            graph.paths.append(path_record(path_id, "mango", company_id, normalized_row, list(dict.fromkeys(edge_ids)), source, index == 1, verified))
            count += 1
    return count


def build_interactions(graph: GraphBuilder, artifacts: list[tuple[str, Path, dict[str, Any]]]) -> int:
    count = 0
    type_map = {
        "mention": "MENTIONED",
        "reply": "REPLIED_TO",
        "quote": "QUOTED",
        "quote_post": "QUOTED",
    }
    for scope, path, artifact in artifacts:
        source_artifact = artifact_ref(path)
        verified = artifact.get("audited_at") or file_verified_at(path)
        for row in artifact.get("observations") or []:
            source_handle = str(row["source_handle"]).lstrip("@")
            target_handle = str(row["destination_handle"]).lstrip("@")
            source_id = graph.ensure_x_account("", handle=source_handle)
            target_id = graph.ensure_x_account("", handle=target_handle)
            edge_type = type_map.get(str(row.get("interaction_type") or "").casefold(), "PUBLIC_INTERACTION")
            interaction_id = str(row.get("interaction_id") or f"{scope}:{source_handle}:{target_handle}:{row['tweet_id']}")
            source_url = str(row.get("tweet_url") or "")
            edge_id = f"edge:interaction:{slug(interaction_id)}:{hashlib.sha1(interaction_id.encode()).hexdigest()[:10]}"
            existing = graph.edges.get(edge_id)
            scopes = sorted(set((existing or {}).get("properties", {}).get("collection_scopes") or []) | {scope})
            companies = sorted(set((existing or {}).get("properties", {}).get("companies") or []) | set(row.get("companies") or []))
            graph.add_edge(
                edge_id,
                source_id, target_id, edge_type, "public_social_interaction",
                fact_status="confirmed", confidence=text_confidence(row.get("confidence")), last_verified_at=verified,
                evidence_items=[evidence(source_artifact, row.get("created_at") or verified, "first_party_social", "Observed public X interaction in the bounded Rapid X query sample.", source_url=source_url, evidence_id=interaction_id)],
                provenance=[{"source_artifact": source_artifact, "source_record_id": interaction_id, "method": "bounded Rapid X interaction query"}],
                properties={
                    "tweet_id": str(row["tweet_id"]), "interaction_type_raw": row.get("interaction_type"),
                    "collection_scopes": scopes, "companies": companies,
                    "human_relationship_inference": row.get("human_relationship_inference") or "none",
                    "does_not_assert": ["relationship_strength", "introduction_willingness"],
                }, merge=True,
            )
            count += existing is None
    return count


def build_sponsor_layer(graph: GraphBuilder, observations: list[dict[str, Any]], aggregate_edges: list[dict[str, Any]]) -> tuple[int, int]:
    obs_source = artifact_ref(SOURCE_FILES["sponsor_observations"])
    edge_source = artifact_ref(SOURCE_FILES["sponsor_edges"])
    for row in observations:
        creator_id = graph.add_node(
            str(row.get("creator_handle_or_channel") and f"creator:{row['platform']}:{slug(row['creator_handle_or_channel'])}" or f"creator:{slug(row['creator'])}"),
            "creator", row["creator"], properties={"platform": row["platform"], "handle_or_channel": row.get("creator_handle_or_channel")},
        )
        company_id = graph.company_by_normalized_name.get(normalize_name(row["brand"]))
        if not company_id:
            raise ValueError(f"Sponsor brand is not explicitly matched to company universe: {row['brand']}")
        obs_id = graph.add_node(
            f"sponsor_observation:{row['id']}", "sponsor_observation", row["content_title"],
            properties={
                "observation_id": row["id"], "platform": row["platform"], "content_url": row["content_url"],
                "published_at": row["published_at"], "disclosure_type": row["disclosure_type"],
                "evidence_text": row["evidence_text"], "evidence_location": row["evidence_location"],
            },
            evidence_items=[evidence(obs_source, row.get("extracted_at") or row["published_at"], row["source_type"], row["evidence_text"], source_url=row["content_url"], evidence_id=row["id"])],
        )
        verified = row.get("extracted_at") or row["published_at"]
        ev = [evidence(obs_source, row["published_at"], row["source_type"], row["evidence_text"], source_url=row["content_url"], evidence_id=row["id"])]
        prov = [{"source_artifact": obs_source, "source_record_id": row["id"], "method": row["extraction_method"]}]
        graph.add_edge(
            f"edge:creator_published:{row['id']}", creator_id, obs_id, "PUBLISHED_SPONSOR_OBSERVATION", "sponsor_evidence",
            fact_status="confirmed", confidence=text_confidence(row["confidence"]), last_verified_at=verified,
            evidence_items=ev, provenance=prov,
        )
        graph.add_edge(
            f"edge:observation_brand:{row['id']}", obs_id, company_id, "OBSERVATION_CONCERNS_BRAND", "sponsor_evidence",
            fact_status="confirmed", confidence=text_confidence(row["confidence"]), last_verified_at=verified,
            evidence_items=ev, provenance=prov,
        )

    for row in aggregate_edges:
        creator_id = f"creator:{row['platform']}:{slug(row['creator_handle_or_channel'])}"
        company_id = graph.company_by_normalized_name[normalize_name(row["brand"])]
        if row["commercial_status"] == "confirmed_paid_present":
            source_id, target_id, edge_type = company_id, creator_id, "PAID_CREATOR_SPONSORSHIP"
        elif row["commercial_status"] == "affiliate_only":
            source_id, target_id, edge_type = creator_id, company_id, "AFFILIATE_REFERRAL"
        else:
            source_id, target_id, edge_type = creator_id, company_id, "MENTIONED_BRAND"
        verified = row.get("last_seen") or file_verified_at(SOURCE_FILES["sponsor_edges"])
        evidence_items = [
            evidence(edge_source, item.get("published_at") or verified, item.get("source_type") or "first_party_creator_description", item.get("evidence_text") or "Creator-brand observation.", source_url=item.get("content_url") or "", evidence_id=item.get("observation_id") or "")
            for item in row.get("evidence") or []
        ]
        graph.add_edge(
            str(row["edge_id"]), source_id, target_id, edge_type, "creator_brand_commercial",
            fact_status="confirmed", confidence=text_confidence(row.get("confidence_max")), last_verified_at=verified,
            evidence_items=evidence_items,
            provenance=[{"source_artifact": edge_source, "source_record_id": row["edge_id"], "method": "disclosure-aware creator-brand aggregation"}],
            properties={key: row.get(key) for key in (
                "commercial_status", "observation_count", "paid_observation_count", "affiliate_observation_count",
                "mention_observation_count", "repeat_paid", "multi_creator_paid_brand", "observation_ids",
            )},
        )
    return len(observations), len(aggregate_edges)


def operator_target(graph: GraphBuilder, route: dict[str, Any], operator: dict[str, Any], rank: str) -> str:
    if operator.get("name"):
        if operator.get("status") != "confirmed_official_role_and_rapid_x_exact_profile":
            raise ValueError(
                f"Named operator bypassed official-source + Rapid X exact-profile contract: {route['company']}"
            )
        rest_id = str(operator.get("x_rest_id") or "").strip()
        if not rest_id:
            raise ValueError(f"Named operator lacks verified X REST ID: {route['company']}")
        return graph.add_node(
            f"person:operator:x-{rest_id}", "person", str(operator["name"]),
            properties={
                "public_role": operator.get("role"), "operator_status": operator.get("status"),
                "x_handle": operator.get("x_handle"), "x_rest_id": operator.get("x_rest_id"),
                "budget_authority_confirmed": False,
            },
        )
    return graph.add_node(
        f"operator_role:{slug(route['company'])}:{rank}", "operator_role", str(operator.get("role") or "Operator role to find"),
        properties={"operator_status": operator.get("status") or "role_to_find", "company": route["company"]},
    )


def build_operator_routes(graph: GraphBuilder, routes: list[dict[str, Any]]) -> int:
    source = artifact_ref(SOURCE_FILES["operator_routes"])
    count = 0
    for route in routes:
        company_id = graph.company_by_normalized_name[normalize_name(route["company"])]
        evidence_items = [
            evidence(source, item.get("date") or route["last_verified_at"], item.get("source_type") or "official", item["claim"], source_url=item.get("url") or "")
            for item in route.get("operator_evidence") or []
        ]
        if not evidence_items:
            public_route = route.get("public_business_route") or {}
            evidence_items = [evidence(
                source, route["last_verified_at"], "official_route_research",
                "Public route is retained for operator acquisition; no named person is asserted.",
                source_url=public_route.get("source_url") or "",
            )]
        for rank, key in (("recommended", "recommended_operator"), ("alternate", "alternate_operator")):
            operator = route.get(key) or {}
            if not operator:
                continue
            target = operator_target(graph, route, operator, rank)
            named = bool(operator.get("name"))
            fact_status = "confirmed" if named else "probable"
            confidence = "high" if named else "medium"
            graph.add_edge(
                f"edge:operator_route:{slug(route['company'])}:{rank}", company_id, target,
                "PUBLIC_OPERATOR_ROUTE" if rank == "recommended" else "PUBLIC_ALTERNATE_OPERATOR_ROUTE", "operator_route",
                fact_status=fact_status, confidence=confidence, last_verified_at=route["last_verified_at"],
                evidence_items=evidence_items,
                provenance=[{"source_artifact": source, "source_record_id": f"{route['company']}:{rank}", "method": "first-party operator/role route research"}],
                properties={
                    "route_rank": rank, "role": operator.get("role"), "status": operator.get("status"),
                    "decision_relevance": operator.get("decision_relevance"), "public_business_route": route.get("public_business_route"),
                    "operator_acquisition_next_step": operator.get("acquisition_next_step"),
                    "budget_authority_confirmed": False, "opening_signal": route.get("opening_signal"),
                },
            )
            count += 1
    return count


def _operator_person_id(route: dict[str, Any]) -> str | None:
    operator = route.get("recommended_operator") or {}
    if not operator.get("name"):
        return None
    rest_id = str(operator.get("x_rest_id") or "").strip()
    if not rest_id:
        raise ValueError(f"Named operator lacks verified X REST ID: {route['company']}")
    return f"person:operator:x-{rest_id}"


def add_operator_person_layer(
    graph: GraphBuilder, routes: list[dict[str, Any]], artifact: dict[str, Any],
) -> dict[str, int]:
    """Add verified person identities and their independent Solomon/Mango paths."""
    source_path = SOURCE_FILES["operator_person_paths"]
    source = artifact_ref(source_path)
    route_source = artifact_ref(SOURCE_FILES["operator_routes"])
    fallback_verified = artifact.get("generated_at") or file_verified_at(source_path)
    routes_by_company = {normalize_name(row["company"]): row for row in routes}
    records_by_company = {
        normalize_name(row["company"]): row for row in artifact.get("records") or []
    }
    if set(routes_by_company) != set(records_by_company):
        raise ValueError(
            "Operator route/person-path company sets differ: "
            f"routes_only={sorted(set(routes_by_company) - set(records_by_company))}, "
            f"paths_only={sorted(set(records_by_company) - set(routes_by_company))}"
        )
    counts = {"operator_person_identity_edges": 0, "solomon_operator_person_paths": 0, "mango_operator_person_paths": 0}

    for record in artifact.get("records") or []:
        key = normalize_name(record["company"])
        route = routes_by_company.get(key)
        if not route:
            raise ValueError(f"Operator person path has no route record: {record['company']}")
        person = record.get("operator_person") or {}
        person_id = _operator_person_id(route)
        if not person.get("identity_confirmed"):
            if person_id:
                raise ValueError(f"Unconfirmed person was named in operator route: {record['company']}")
            continue
        if not person_id or person_id not in graph.nodes:
            raise ValueError(f"Confirmed operator person node missing: {record['company']}")
        operator = route.get("recommended_operator") or {}
        identity = route.get("operator_identity") or {}
        gates = identity.get("gates") or {}
        if identity.get("confirmed") is not True or not gates or not all(gates.values()):
            raise ValueError(f"Confirmed person lacks aligned successful identity gates: {record['company']}")
        if (
            normalize_name(str(person.get("name") or "")) != normalize_name(str(operator.get("name") or ""))
            or
            str(person.get("x_rest_id") or "") != str(operator.get("x_rest_id") or "")
            or normalize_name(str(person.get("x_handle") or "")) != normalize_name(str(operator.get("x_handle") or ""))
        ):
            raise ValueError(f"Operator route/person identity mismatch: {record['company']}")
        verified = route.get("last_verified_at") or fallback_verified
        x_id = graph.ensure_x_account(
            str(person["x_rest_id"]), handle=str(person["x_handle"]),
            identity_scope="operator_person", company=record["company"],
        )
        role_urls = set(identity.get("official_role_evidence_urls") or [])
        identity_evidence = [
            evidence(
                route_source, item.get("date") or verified,
                item.get("source_type") or "official_company_source", item.get("claim") or "Official role evidence.",
                source_url=item.get("url") or "",
            )
            for item in route.get("operator_evidence") or []
            if item.get("url") in role_urls
        ]
        if not identity_evidence:
            raise ValueError(f"Confirmed operator has no exact passed official-role evidence: {record['company']}")
        identity_evidence.append(evidence(
            source, verified, "rapid_x_exact_profile",
            "Rapid X exact profile passed deterministic handle, person-name, and company-association gates.",
            evidence_id=str((route.get("operator_identity") or {}).get("rapid_x_cache_file") or record["company"]),
        ))
        graph.add_edge(
            f"edge:person_has_x_account:{person_id}:{x_id}", person_id, x_id,
            "PERSON_HAS_X_ACCOUNT", "person_identity",
            fact_status="confirmed", confidence="high", last_verified_at=verified,
            evidence_items=identity_evidence,
            provenance=[
                {"source_artifact": route_source, "source_record_id": record["company"], "method": "official/company current-role evidence"},
                {"source_artifact": source, "source_record_id": record["company"], "method": "Rapid X exact profile identity contract"},
            ],
            properties={
                "company": record["company"], "public_role": person.get("role"),
                "does_not_assert": ["budget_authority", "relationship_strength", "introduction_willingness"],
            },
        )
        counts["operator_person_identity_edges"] += 1

        company_id = graph.company_by_normalized_name[key]
        solomon = record.get("solomon_to_operator_person") or {}
        if solomon.get("graph_reachable") is True and solomon.get("primary_path"):
            path_rows = [(True, solomon["primary_path"])] + [
                (False, row) for row in solomon.get("alternative_paths") or []
            ]
            alt_index = 0
            for primary, path_row in path_rows:
                if not primary:
                    alt_index += 1
                path_id = (
                    f"path:solomon:operator:{slug(record['company'])}:primary"
                    if primary else f"path:solomon:operator:{slug(record['company'])}:alternative-{alt_index:02d}"
                )
                rest_ids = [str(item) for item in path_row.get("path_rest_ids") or []]
                labels = [str(item) for item in path_row.get("path_labels") or []]
                labels_by_id = label_map(rest_ids, labels)
                edge_ids: list[str] = []
                for detail in path_row.get("edge_details") or []:
                    for observed in detail.get("observed_directed_edges") or []:
                        src, dst = str(observed["source_rest_id"]), str(observed["target_rest_id"])
                        caches = observed.get("cache_files") or [source]
                        observed_at = max(
                            artifact_observed_at(str(cache), verified) for cache in caches
                        )
                        edge_ids.append(graph.add_follow(
                            src, dst, source_label=labels_by_id.get(src, ""), target_label=labels_by_id.get(dst, ""),
                            last_verified_at=observed_at,
                            evidence_items=[
                                evidence(
                                    str(cache), artifact_observed_at(str(cache), observed_at), "first_party_social",
                                    f"Rapid X positive observation: {src} follows {dst}."
                                )
                                for cache in caches
                            ],
                            provenance=[{"source_artifact": source, "source_record_id": path_id, "method": "operator-person positive cached follow observation"}],
                            properties={"path_target_type": "operator_person"},
                        ))
                if not rest_ids or rest_ids[-1] != str(person["x_rest_id"]):
                    raise ValueError(f"Solomon operator path does not terminate at person X account: {record['company']}")
                if len(rest_ids) > 1 and len(edge_ids) < len(rest_ids) - 1:
                    raise ValueError(f"Solomon operator path lacks a positive observed edge for each hop: {record['company']}")
                path_verified = max(
                    [
                        artifact_observed_at(str(cache), verified)
                        for detail in path_row.get("edge_details") or []
                        for observed in detail.get("observed_directed_edges") or []
                        for cache in observed.get("cache_files") or []
                    ] or [verified]
                )
                path = path_record(path_id, "solomon", company_id, path_row, list(dict.fromkeys(edge_ids)), source, primary, path_verified)
                path.update({
                    "company_context_id": company_id,
                    "target_entity_id": person_id,
                    "path_target_type": "operator_person",
                })
                path["properties"].update({"path_target_type": "operator_person", "operator_person_node_id": person_id})
                graph.paths.append(path)
                counts["solomon_operator_person_paths"] += 1

        mango = record.get("mango_to_operator_person") or {}
        mango_rows = list(mango.get("mango_direct_paths") or []) + list(mango.get("mango_secondary_paths") or [])
        for index, path_row in enumerate(mango_rows, 1):
            path_id = f"path:mango:operator:{slug(record['company'])}:{index:03d}"
            rest_ids = [str(item) for item in path_row.get("path_rest_ids") or []]
            labels = [str(item) for item in path_row.get("path_labels") or []]
            labels_by_id = label_map(rest_ids, labels)
            edge_ids: list[str] = []
            for direction in path_row.get("edge_rest_id_directions") or []:
                if "->" not in direction:
                    continue
                src, dst = (part.strip() for part in direction.split("->", 1))
                caches = path_row.get("evidence_files") or [source]
                observed_at = max(
                    artifact_observed_at(str(cache), path_row.get("last_verified_at") or verified)
                    for cache in caches
                )
                edge_ids.append(graph.add_follow(
                    src, dst, source_label=labels_by_id.get(src, ""), target_label=labels_by_id.get(dst, ""),
                    last_verified_at=observed_at,
                    evidence_items=[
                        evidence(
                            str(cache), artifact_observed_at(str(cache), observed_at), "first_party_social",
                            f"Rapid X positive observation: {src} follows {dst}."
                        )
                        for cache in caches
                    ],
                    provenance=[{"source_artifact": source, "source_record_id": path_id, "method": "operator-person positive cached follow observation"}],
                    properties={"path_target_type": "operator_person"},
                ))
            normalized_path = dict(path_row)
            normalized_path.setdefault("hop_count", path_row.get("x_degree"))
            if not rest_ids or rest_ids[-1] != str(person["x_rest_id"]):
                raise ValueError(f"Mango operator path does not terminate at person X account: {record['company']}")
            if len(rest_ids) > 1 and len(edge_ids) < len(rest_ids) - 1:
                raise ValueError(f"Mango operator path lacks a positive observed edge for each hop: {record['company']}")
            path = path_record(
                path_id, "mango", company_id, normalized_path, list(dict.fromkeys(edge_ids)),
                source, index == 1, path_row.get("last_verified_at") or verified,
            )
            path.update({
                "company_context_id": company_id,
                "target_entity_id": person_id,
                "path_target_type": "operator_person",
            })
            path["properties"].update({"path_target_type": "operator_person", "operator_person_node_id": person_id})
            graph.paths.append(path)
            counts["mango_operator_person_paths"] += 1
    return counts


def build_gtm_cases(graph: GraphBuilder, artifact: dict[str, Any]) -> tuple[int, int, int]:
    source = artifact_ref(SOURCE_FILES["gtm_cases"])
    channel_edges = 0
    mechanism_edges = 0
    for case in artifact.get("case_studies") or []:
        company_id = graph.company_by_normalized_name.get(normalize_name(case["company"]))
        if not company_id:
            # Kaito is a researched comparator, not one of the 77 target-universe
            # companies. Keep that distinction explicit instead of silently
            # inflating the target company count.
            company_id = graph.add_node(
                f"reference_company:{slug(case['company'])}", "reference_company", case["company"],
                properties={"scope": "GTM comparator; not in 77-company target universe"},
            )
        case_id = graph.add_node(
            f"gtm_case:{slug(case['company'])}", "gtm_case", f"{case['company']} GTM case",
            properties={
                "category": case.get("category"), "maturity_stage": case.get("maturity_stage"),
                "gtm_motion": case.get("gtm_motion"), "budget_spend_evidence": case.get("budget_spend_evidence"),
                "buyer_roles": case.get("buyer_roles") or [], "measurement_signals": case.get("measurement_signals") or [],
            },
        )
        verified = case["last_verified_at"]
        source_evidence = [
            evidence(source, item.get("date") or verified, item.get("source_type") or "official", item["claim"], source_url=item.get("url") or "")
            for item in case.get("evidence") or []
        ]
        provenance = [{"source_artifact": source, "source_record_id": case["company"], "method": "primary-source GTM case synthesis"}]
        graph.add_edge(
            f"edge:has_gtm_case:{company_id}:{case_id}", company_id, case_id, "HAS_GTM_CASE", "gtm_evidence",
            fact_status="confirmed", confidence="high", last_verified_at=verified,
            evidence_items=source_evidence, provenance=provenance,
        )
        for channel in case.get("channels") or []:
            channel_id = graph.add_node(f"gtm_channel:{slug(channel)}", "gtm_channel", channel)
            graph.add_edge(
                f"edge:gtm_channel:{case_id}:{slug(channel)}", case_id, channel_id, "USES_CHANNEL", "gtm_mechanic",
                fact_status="probable", confidence="medium", last_verified_at=verified,
                evidence_items=source_evidence, provenance=provenance,
                properties={"interpretation": "structured synthesis from the cited case evidence"},
            )
            channel_edges += 1
        for index, mechanic in enumerate(case.get("creator_or_partner_mechanics") or [], 1):
            mechanic_id = graph.add_node(f"partner_mechanism:{slug(case['company'])}:{index:02d}", "partner_mechanism", mechanic)
            graph.add_edge(
                f"edge:partner_mechanism:{case_id}:{index:02d}", case_id, mechanic_id, "USES_PARTNER_MECHANISM", "gtm_mechanic",
                fact_status="probable", confidence="medium", last_verified_at=verified,
                evidence_items=source_evidence, provenance=provenance,
                properties={"interpretation": "case-specific synthesized operating mechanism"},
            )
            mechanism_edges += 1
    return len(artifact.get("case_studies") or []), channel_edges, mechanism_edges


def validate_graph(graph: GraphBuilder) -> None:
    for edge in graph.edges.values():
        if edge["source"] not in graph.nodes or edge["target"] not in graph.nodes:
            raise ValueError(f"dangling edge {edge['edge_id']}")
        required = ("evidence", "provenance", "fact_status", "confidence", "last_verified_at")
        if any(not edge.get(field) for field in required):
            raise ValueError(f"incomplete evidence contract on {edge['edge_id']}")
    edge_ids = set(graph.edges)
    for path in graph.paths:
        if any(edge_id not in edge_ids for edge_id in path["edge_ids"]):
            raise ValueError(f"path references missing edge: {path['path_id']}")
        if any(node_id not in graph.nodes for node_id in path["path_node_ids"]):
            raise ValueError(f"path references missing node: {path['path_id']}")
        if path["human_intro_status"] != "human_intro_unvalidated":
            raise ValueError(f"unsupported intro assertion: {path['path_id']}")


def graph_schema() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "graph_model": "typed directed multigraph",
        "required_edge_fields": [
            "edge_id", "source", "target", "edge_type", "edge_category", "fact_status",
            "confidence", "last_verified_at", "evidence", "provenance", "properties",
        ],
        "fact_status_enum": sorted(FACT_STATUSES),
        "confidence_enum": sorted(CONFIDENCES),
        "node_types": [
            "company", "reference_company", "organization", "person", "x_account", "creator", "sponsor_observation",
            "operator_role", "gtm_case", "gtm_channel", "partner_mechanism",
            "investor", "accelerator", "agency",
        ],
        "instantiation_rule": "Supported node types are instantiated only when a source artifact provides evidence. No investor, accelerator, or agency is inferred.",
        "edge_types": {
            "HAS_X_ACCOUNT": "Exact entity-to-account identity mapping; not a follow or affiliation edge.",
            "PERSON_HAS_X_ACCOUNT": "Strict official-role + Rapid X exact-profile mapping for an operator person; separate from the company account.",
            "AUDIT_SEED_ACCOUNT": "Administrative collection scope only; does not assert employment or team membership.",
            "FOLLOWS": "One observed directed public X follow edge.",
            "MENTIONED": "One observed public X mention.",
            "REPLIED_TO": "One observed public X reply.",
            "QUOTED": "One observed public X quote post.",
            "PUBLIC_INTERACTION": "Other bounded public X interaction observation.",
            "INTRODUCTION_WILLINGNESS": "Reserved for explicit human validation; zero instances are expected in the current data.",
            "PUBLISHED_SPONSOR_OBSERVATION": "Creator-to-observation evidence linkage.",
            "OBSERVATION_CONCERNS_BRAND": "Observation-to-company evidence linkage.",
            "PAID_CREATOR_SPONSORSHIP": "Confirmed paid disclosure; directed company to creator.",
            "AFFILIATE_REFERRAL": "Affiliate-only evidence; directed creator to company and never promoted to paid sponsorship.",
            "MENTIONED_BRAND": "Mention-only evidence; directed creator to company and never promoted to paid sponsorship.",
            "PUBLIC_OPERATOR_ROUTE": "Recommended named operator or evidence-backed role to find; not proof of budget authority.",
            "PUBLIC_ALTERNATE_OPERATOR_ROUTE": "Alternate named operator or evidence-backed role to find.",
            "HAS_GTM_CASE": "Company to researched GTM case.",
            "USES_CHANNEL": "Case to synthesized channel.",
            "USES_PARTNER_MECHANISM": "Case to synthesized creator/partner operating mechanism.",
        },
        "separation_rules": {
            "follow": "Only FOLLOWS edges encode public graph reachability; direction is preserved.",
            "interaction": "Interaction edges remain event observations and do not strengthen or replace FOLLOWS edges.",
            "company_vs_operator": "Company-account paths and verified operator-person paths terminate at different X nodes and are never substituted for one another.",
            "introduction": "No INTRODUCTION_WILLINGNESS edge is emitted without explicit human confirmation; every retained route remains human_intro_unvalidated.",
            "shared_interest": "A V-shaped shared follow is not a routable path and is not materialized.",
        },
    }


def build_summary(graph: GraphBuilder, input_counts: dict[str, int]) -> dict[str, Any]:
    nodes_by_type = Counter(node["node_type"] for node in graph.nodes.values())
    edges_by_type = Counter(edge["edge_type"] for edge in graph.edges.values())
    edges_by_category = Counter(edge["edge_category"] for edge in graph.edges.values())
    paths_by_root = Counter(path["root"] for path in graph.paths)
    paths_by_degree = Counter(str(path["degree_label"]) for path in graph.paths)
    return {
        "schema_version": SCHEMA_VERSION,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "path_count": len(graph.paths),
        "nodes_by_type": dict(sorted(nodes_by_type.items())),
        "edges_by_type": dict(sorted(edges_by_type.items())),
        "edges_by_category": dict(sorted(edges_by_category.items())),
        "paths_by_root": dict(sorted(paths_by_root.items())),
        "paths_by_degree": dict(sorted(paths_by_degree.items())),
        "introduction_willingness_edge_count": edges_by_type.get("INTRODUCTION_WILLINGNESS", 0),
        "human_intro_validated_path_count": sum(path["human_intro_status"] != "human_intro_unvalidated" for path in graph.paths),
        "unsupported_entity_instances": {
            kind: nodes_by_type.get(kind, 0) for kind in ("investor", "accelerator", "agency")
        },
        "input_coverage": input_counts,
        "integrity": {
            "dangling_edge_count": 0,
            "paths_with_missing_nodes": 0,
            "paths_with_missing_edges": 0,
            "edges_missing_evidence_contract": 0,
        },
    }


def input_manifest() -> list[dict[str, Any]]:
    manifest = []
    for role, path in sorted(SOURCE_FILES.items()):
        payload = path.read_bytes()
        manifest.append({
            "role": role,
            "source_artifact": artifact_ref(path),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "last_modified_at": file_verified_at(path),
        })
    return manifest


def write_graphml(path: Path, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    namespace = "http://graphml.graphdrawing.org/xmlns"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}graphml")
    node_keys = {"node_type": "string", "label": "string", "aliases_json": "string", "properties_json": "string", "evidence_json": "string"}
    edge_keys = {
        "edge_type": "string", "edge_category": "string", "fact_status": "string", "confidence": "string",
        "last_verified_at": "string", "evidence_json": "string", "provenance_json": "string", "properties_json": "string",
    }
    for key, attr_type in node_keys.items():
        ET.SubElement(root, f"{{{namespace}}}key", id=f"n_{key}", **{"for": "node", "attr.name": key, "attr.type": attr_type})
    for key, attr_type in edge_keys.items():
        ET.SubElement(root, f"{{{namespace}}}key", id=f"e_{key}", **{"for": "edge", "attr.name": key, "attr.type": attr_type})
    graph_element = ET.SubElement(root, f"{{{namespace}}}graph", id="mango_bd_business_v4", edgedefault="directed")
    for node in nodes:
        element = ET.SubElement(graph_element, f"{{{namespace}}}node", id=node["node_id"])
        values = {
            "node_type": node["node_type"], "label": node["label"], "aliases_json": compact(node["aliases"]),
            "properties_json": compact(node["properties"]), "evidence_json": compact(node["evidence"]),
        }
        for key, value in values.items():
            ET.SubElement(element, f"{{{namespace}}}data", key=f"n_{key}").text = value
    for edge in edges:
        element = ET.SubElement(graph_element, f"{{{namespace}}}edge", id=edge["edge_id"], source=edge["source"], target=edge["target"])
        values = {
            "edge_type": edge["edge_type"], "edge_category": edge["edge_category"], "fact_status": edge["fact_status"],
            "confidence": edge["confidence"], "last_verified_at": edge["last_verified_at"],
            "evidence_json": compact(edge["evidence"]), "provenance_json": compact(edge["provenance"]),
            "properties_json": compact(edge["properties"]),
        }
        for key, value in values.items():
            ET.SubElement(element, f"{{{namespace}}}data", key=f"e_{key}").text = value
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def write_csvs(output_dir: Path, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    with (output_dir / "business_knowledge_graph_v4_nodes.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["node_id", "node_type", "label", "aliases_json", "properties_json", "evidence_json"])
        writer.writeheader()
        for node in nodes:
            writer.writerow({
                "node_id": node["node_id"], "node_type": node["node_type"], "label": node["label"],
                "aliases_json": compact(node["aliases"]), "properties_json": compact(node["properties"]), "evidence_json": compact(node["evidence"]),
            })
    with (output_dir / "business_knowledge_graph_v4_edges.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = [
            "edge_id", "source", "target", "edge_type", "edge_category", "fact_status", "confidence",
            "last_verified_at", "evidence_json", "provenance_json", "properties_json",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for edge in edges:
            writer.writerow({**{key: edge[key] for key in fields[:8]}, "evidence_json": compact(edge["evidence"]), "provenance_json": compact(edge["provenance"]), "properties_json": compact(edge["properties"])})


def build(output_dir: Path = OUT) -> dict[str, Any]:
    graph = GraphBuilder()
    companies = read_json(SOURCE_FILES["companies"])
    company_ids = build_companies(graph, companies)
    target_identities = read_json(SOURCE_FILES["target_identities"])
    mango_seed_audit = read_json(SOURCE_FILES["mango_seeds"])
    input_counts: dict[str, int] = {
        "companies": len(companies),
        "confirmed_target_x_identities": build_target_identities(graph, company_ids, target_identities),
        "mango_seed_accounts": build_mango_people(graph, mango_seed_audit),
        "solomon_v4_paths": add_v4_solomon_path_edges(graph, company_ids, read_json(SOURCE_FILES["solomon_v4_paths"])),
        "solomon_v3_paths": add_legacy_solomon_paths(graph, company_ids, read_json(SOURCE_FILES["solomon_v3_paths"])),
        "mango_paths": add_mango_paths(graph, company_ids, target_identities),
    }
    interaction_artifacts = [
        ("solomon_primary_paths", SOURCE_FILES["solomon_interactions"], read_json(SOURCE_FILES["solomon_interactions"])),
        ("mango_seed_paths", SOURCE_FILES["mango_interactions"], read_json(SOURCE_FILES["mango_interactions"])),
    ]
    input_counts["x_interaction_observations"] = sum(len(payload.get("observations") or []) for _scope, _path, payload in interaction_artifacts)
    input_counts["x_interactions"] = build_interactions(graph, interaction_artifacts)
    sponsor_counts = build_sponsor_layer(
        graph, read_json(SOURCE_FILES["sponsor_observations"]), read_json(SOURCE_FILES["sponsor_edges"])
    )
    input_counts["sponsor_observations"] = sponsor_counts[0]
    input_counts["creator_brand_edges"] = sponsor_counts[1]
    input_counts["sponsor_brands"] = len({row["brand"] for row in read_json(SOURCE_FILES["sponsor_edges"])})
    input_counts["sponsor_creators"] = len({row["creator_id"] for row in read_json(SOURCE_FILES["sponsor_edges"])})
    operator_routes = read_json(SOURCE_FILES["operator_routes"])
    input_counts["operator_route_records"] = len(operator_routes)
    input_counts["operator_relationship_edges"] = build_operator_routes(graph, operator_routes)
    input_counts.update(add_operator_person_layer(
        graph, operator_routes, read_json(SOURCE_FILES["operator_person_paths"])
    ))
    gtm_counts = build_gtm_cases(graph, read_json(SOURCE_FILES["gtm_cases"]))
    input_counts.update({"gtm_cases": gtm_counts[0], "gtm_channel_edges": gtm_counts[1], "gtm_partner_mechanism_edges": gtm_counts[2]})

    validate_graph(graph)
    nodes = sorted(graph.nodes.values(), key=lambda item: item["node_id"])
    edges = sorted(graph.edges.values(), key=lambda item: item["edge_id"])
    paths = sorted(graph.paths, key=lambda item: item["path_id"])
    summary = build_summary(graph, input_counts)
    schema = graph_schema()
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "description": "Compact typed business graph. Follow, interaction, path reachability, and introduction willingness are separate concepts.",
        "schema": schema,
        "summary": summary,
        "input_manifest": input_manifest(),
        "nodes": nodes,
        "edges": edges,
        "paths": paths,
    }
    (output_dir / "business_knowledge_graph_v4.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "business_knowledge_graph_v4_schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "business_knowledge_graph_v4_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_graphml(output_dir / "business_knowledge_graph_v4.graphml", nodes, edges)
    write_csvs(output_dir, nodes, edges)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUT)
    args = parser.parse_args()
    print(json.dumps(build(args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
