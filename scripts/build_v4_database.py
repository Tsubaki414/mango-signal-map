#!/usr/bin/env python3
"""Build the normalized, queryable Mango BD v4 SQLite database."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "pilot_v4"
DEFAULT_DB = OUT / "mango_bd_v4.sqlite"
DEFAULT_SUMMARY = OUT / "database_summary_v4.json"

# Explicit product/display aliases only. These are not fuzzy matches.
KNOWN_COMPANY_ALIASES = {"viggle": "viggleai"}


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def company_id(value: str) -> str:
    key = normalize_name(value)
    if not key:
        raise ValueError(f"Cannot build company ID from {value!r}")
    return f"company:{key}"


def resolve_company_key(value: str, ids: dict[str, str]) -> str:
    key = normalize_name(value)
    if key in ids:
        return key
    canonical = KNOWN_COMPANY_ALIASES.get(key)
    if canonical and canonical in ids:
        return canonical
    raise ValueError(f"Unknown company/brand identity: {value}")


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE companies (
  company_id TEXT PRIMARY KEY,
  company TEXT NOT NULL UNIQUE,
  cohort TEXT,
  cohort_group TEXT NOT NULL,
  category TEXT,
  geography TEXT,
  score_value REAL,
  score_method TEXT,
  score_comparison_group TEXT,
  within_group_rank INTEGER,
  priority_tier TEXT,
  spend_evidence_level TEXT,
  spend_mechanism_level TEXT,
  why_now TEXT,
  budget_evidence TEXT,
  buyer_or_route TEXT,
  surf_status TEXT,
  raw_json TEXT NOT NULL
);

CREATE TABLE company_aliases (
  company_id TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  PRIMARY KEY (company_id, alias)
);

CREATE TABLE company_sources (
  company_id TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  source_url TEXT NOT NULL,
  PRIMARY KEY (company_id, source_url)
);

CREATE TABLE x_identities (
  company_id TEXT PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
  requested_handles_json TEXT NOT NULL,
  technical_identity_confirmed INTEGER NOT NULL CHECK (technical_identity_confirmed IN (0,1)),
  technical_identity_status TEXT NOT NULL,
  target_rest_id TEXT,
  target_handle TEXT,
  confidence TEXT,
  fact_status TEXT,
  last_verified_at TEXT,
  evidence_files_json TEXT NOT NULL,
  raw_json TEXT NOT NULL
);

CREATE TABLE x_nodes (
  graph_node_id TEXT PRIMARY KEY,
  rest_id TEXT NOT NULL,
  label TEXT,
  handle TEXT,
  role TEXT,
  evidence TEXT,
  cache_coverage TEXT
);

CREATE TABLE x_edges (
  graph_edge_id TEXT PRIMARY KEY,
  source_node_id TEXT NOT NULL REFERENCES x_nodes(graph_node_id),
  target_node_id TEXT NOT NULL REFERENCES x_nodes(graph_node_id),
  relation TEXT NOT NULL,
  role TEXT,
  evidence TEXT,
  cache_coverage TEXT,
  observer_handle TEXT,
  evidence_count INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE x_paths (
  path_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  root TEXT NOT NULL CHECK (root IN ('solomon','mango')),
  is_primary INTEGER NOT NULL CHECK (is_primary IN (0,1)),
  degree_label TEXT,
  hop_count INTEGER,
  connector_handle TEXT,
  path_labels_json TEXT NOT NULL,
  path_rest_ids_json TEXT NOT NULL,
  edge_directions_json TEXT NOT NULL,
  path_strength REAL,
  graph_reachable INTEGER NOT NULL CHECK (graph_reachable IN (0,1)),
  human_intro_status TEXT NOT NULL,
  evidence_scope TEXT,
  source_artifact TEXT NOT NULL,
  raw_json TEXT NOT NULL
);

CREATE TABLE x_interactions (
  interaction_id TEXT PRIMARY KEY,
  tweet_id TEXT NOT NULL,
  collection_scopes_json TEXT NOT NULL,
  source_handle TEXT NOT NULL,
  destination_handle TEXT NOT NULL,
  interaction_type TEXT NOT NULL CHECK (interaction_type IN ('reply','mention','quote')),
  tweet_url TEXT NOT NULL,
  created_at TEXT,
  text TEXT,
  fact_status TEXT NOT NULL,
  confidence TEXT NOT NULL,
  human_relationship_inference TEXT NOT NULL,
  raw_cache_files_json TEXT NOT NULL,
  raw_json TEXT NOT NULL
);

CREATE TABLE x_interaction_companies (
  interaction_id TEXT NOT NULL REFERENCES x_interactions(interaction_id) ON DELETE CASCADE,
  company_id TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  PRIMARY KEY (interaction_id, company_id)
);

CREATE TABLE sponsor_observations (
  observation_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  creator TEXT NOT NULL,
  creator_handle_or_channel TEXT,
  platform TEXT NOT NULL,
  content_url TEXT NOT NULL UNIQUE,
  content_title TEXT,
  published_at TEXT,
  disclosure_type TEXT NOT NULL CHECK (disclosure_type IN ('paid_sponsorship','affiliate','mention','unverified')),
  evidence_text TEXT,
  source_type TEXT,
  confidence REAL,
  business_contact_or_route TEXT,
  raw_json TEXT NOT NULL
);

CREATE TABLE sponsor_edges (
  edge_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  creator_id TEXT NOT NULL,
  creator TEXT NOT NULL,
  creator_handle_or_channel TEXT,
  observation_count INTEGER NOT NULL,
  paid_observation_count INTEGER NOT NULL,
  affiliate_observation_count INTEGER NOT NULL,
  mention_observation_count INTEGER NOT NULL,
  paid_signal INTEGER NOT NULL CHECK (paid_signal IN (0,1)),
  repeat_paid INTEGER NOT NULL CHECK (repeat_paid IN (0,1)),
  multi_creator_paid_brand INTEGER NOT NULL CHECK (multi_creator_paid_brand IN (0,1)),
  commercial_status TEXT NOT NULL,
  first_seen TEXT,
  last_seen TEXT,
  raw_json TEXT NOT NULL
);

CREATE TABLE operator_routes (
  company_id TEXT PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
  recommended_operator_name TEXT,
  recommended_operator_role TEXT,
  recommended_operator_status TEXT,
  budget_authority_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (budget_authority_confirmed IN (0,1)),
  public_route_type TEXT,
  public_route_value TEXT,
  why_now TEXT,
  mango_offer_angle TEXT,
  opening_signal TEXT,
  outreach_mode TEXT,
  fallback_route TEXT,
  last_verified_at TEXT,
  raw_json TEXT NOT NULL
);

CREATE TABLE operator_identities (
  company_id TEXT PRIMARY KEY REFERENCES companies(company_id) ON DELETE CASCADE,
  operator_name TEXT,
  operator_role TEXT,
  x_handle TEXT,
  x_rest_id TEXT,
  identity_confirmed INTEGER NOT NULL CHECK (identity_confirmed IN (0,1)),
  identity_status TEXT NOT NULL,
  official_evidence_urls_json TEXT NOT NULL,
  rapid_x_cache_file TEXT,
  last_verified_at TEXT,
  budget_authority_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (budget_authority_confirmed IN (0,1)),
  raw_json TEXT NOT NULL
);

CREATE TABLE operator_person_paths (
  operator_path_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  root TEXT NOT NULL CHECK (root IN ('solomon','mango')),
  identity_confirmed INTEGER NOT NULL CHECK (identity_confirmed IN (0,1)),
  graph_reachable INTEGER CHECK (graph_reachable IN (0,1)),
  degree_label TEXT CHECK (degree_label IS NULL OR degree_label IN ('direct','secondary','third')),
  hop_count INTEGER,
  primary_path_labels_json TEXT NOT NULL,
  primary_path_rest_ids_json TEXT NOT NULL,
  primary_edge_directions_json TEXT NOT NULL,
  human_intro_status TEXT NOT NULL,
  source_artifact TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  UNIQUE (company_id, root)
);

CREATE TABLE action_queue (
  action_id TEXT PRIMARY KEY,
  company_id TEXT NOT NULL REFERENCES companies(company_id) ON DELETE CASCADE,
  execution_wave INTEGER NOT NULL,
  action_band TEXT NOT NULL,
  source_group TEXT NOT NULL,
  within_group_rank INTEGER,
  owner TEXT,
  primary_next_action TEXT NOT NULL,
  fallback TEXT,
  success_condition TEXT,
  graph_reachable INTEGER,
  mango_graph_reachable INTEGER,
  x_degree TEXT,
  human_intro_status TEXT NOT NULL,
  recommended_operator_name TEXT,
  recommended_operator_role TEXT,
  recommended_operator_status TEXT,
  operator_acquisition_next_step TEXT,
  recommended_operator_x_handle TEXT,
  recommended_operator_x_rest_id TEXT,
  spend_last_verified_at TEXT,
  budget_authority_confirmed INTEGER NOT NULL CHECK (budget_authority_confirmed IN (0,1)),
  evidence_urls_json TEXT NOT NULL,
  raw_json TEXT NOT NULL
);

CREATE TABLE gtm_cases (
  case_id TEXT PRIMARY KEY,
  company_id TEXT REFERENCES companies(company_id) ON DELETE SET NULL,
  company TEXT NOT NULL,
  category TEXT,
  maturity_stage TEXT,
  gtm_motion_json TEXT NOT NULL,
  spend_classification TEXT,
  buyer_roles_json TEXT NOT NULL,
  what_mango_should_copy_json TEXT NOT NULL,
  what_not_to_copy_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  last_verified_at TEXT,
  raw_json TEXT NOT NULL
);

CREATE INDEX idx_companies_score_group_rank ON companies(score_comparison_group, within_group_rank);
CREATE INDEX idx_companies_spend ON companies(spend_evidence_level, spend_mechanism_level);
CREATE INDEX idx_x_nodes_handle ON x_nodes(handle);
CREATE INDEX idx_x_edges_source ON x_edges(source_node_id);
CREATE INDEX idx_x_edges_target ON x_edges(target_node_id);
CREATE INDEX idx_x_edges_observer ON x_edges(observer_handle);
CREATE INDEX idx_x_paths_company_root_degree ON x_paths(company_id, root, hop_count);
CREATE INDEX idx_x_interactions_pair ON x_interactions(source_handle, destination_handle);
CREATE INDEX idx_x_interactions_tweet ON x_interactions(tweet_id);
CREATE INDEX idx_sponsor_observations_company_type ON sponsor_observations(company_id, disclosure_type);
CREATE INDEX idx_sponsor_edges_paid ON sponsor_edges(company_id, paid_signal, repeat_paid);
CREATE INDEX idx_actions_wave_band ON action_queue(execution_wave, action_band);
CREATE INDEX idx_operator_person_paths_root_degree ON operator_person_paths(root, degree_label, graph_reachable);

CREATE VIEW v_reachable_targets AS
SELECT c.company, p.root, p.degree_label, p.hop_count, p.connector_handle,
       p.is_primary, p.human_intro_status, p.path_labels_json, p.edge_directions_json
FROM x_paths p JOIN companies c USING(company_id)
WHERE p.graph_reachable = 1;

CREATE VIEW v_paid_sponsor_signals AS
SELECT c.company, COUNT(*) AS creator_brand_edges,
       SUM(e.paid_observation_count) AS paid_observations,
       SUM(e.repeat_paid) AS repeat_paid_edges,
       MAX(e.multi_creator_paid_brand) AS multi_creator_paid_brand
FROM sponsor_edges e JOIN companies c USING(company_id)
WHERE e.paid_signal = 1
GROUP BY c.company;

CREATE VIEW v_actionable_company_summary AS
SELECT a.execution_wave, a.action_band, c.company, c.score_value, c.score_comparison_group,
       c.spend_evidence_level, c.spend_mechanism_level, a.owner,
       a.graph_reachable, a.mango_graph_reachable, a.x_degree,
       a.recommended_operator_name, a.recommended_operator_role,
       a.recommended_operator_status, a.operator_acquisition_next_step,
       a.recommended_operator_x_handle, a.recommended_operator_x_rest_id,
       a.spend_last_verified_at,
       a.budget_authority_confirmed, a.human_intro_status, a.primary_next_action
FROM action_queue a JOIN companies c USING(company_id);

CREATE VIEW v_operator_relationships AS
SELECT c.company, i.operator_name, i.operator_role, i.x_handle, i.x_rest_id,
       i.identity_confirmed, i.identity_status, p.root, p.graph_reachable,
       p.degree_label, p.hop_count, p.primary_path_labels_json,
       p.primary_edge_directions_json, p.human_intro_status
FROM operator_identities i
JOIN companies c USING(company_id)
JOIN operator_person_paths p USING(company_id);
"""


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def load_companies(connection: sqlite3.Connection, universe: list[dict[str, Any]], adjusted: list[dict[str, Any]]) -> dict[str, str]:
    adjusted_by_key = {normalize_name(row["company"]): row for row in adjusted}
    ids: dict[str, str] = {}
    for base in universe:
        key = normalize_name(base["company"])
        row = adjusted_by_key.get(key, base)
        cid = company_id(base["company"])
        if key in ids:
            raise ValueError(f"Duplicate normalized company key: {base['company']}")
        ids[key] = cid
        score = row.get("overall_priority", row.get("score_value"))
        rank = row.get("graph_adjusted_rank", row.get("cohort_rank"))
        connection.execute(
            """INSERT INTO companies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                cid, base["company"], base.get("cohort"), base.get("cohort_group") or "unknown",
                base.get("category") or row.get("category"), base.get("geography") or row.get("geography"),
                score, row.get("score_method"), row.get("score_comparison_group"), rank,
                row.get("priority_tier"), row.get("spend_evidence_level"), row.get("spend_mechanism_level"),
                row.get("why_now") or "", row.get("budget_evidence") or "", row.get("buyer_or_route") or "",
                row.get("surf_status") or base.get("surf_status") or "", compact_json(row),
            ),
        )
        for alias in base.get("aliases") or []:
            connection.execute("INSERT OR IGNORE INTO company_aliases VALUES (?,?)", (cid, str(alias)))
        for alias_key, canonical_key in KNOWN_COMPANY_ALIASES.items():
            if canonical_key == key:
                connection.execute("INSERT OR IGNORE INTO company_aliases VALUES (?,?)", (cid, alias_key))
        for url in row.get("source_urls") or base.get("source_urls") or []:
            if url:
                connection.execute("INSERT OR IGNORE INTO company_sources VALUES (?,?)", (cid, str(url)))
    return ids


def load_x_identities(connection: sqlite3.Connection, artifact: dict[str, Any], ids: dict[str, str]) -> None:
    for row in artifact.get("targets") or []:
        key = normalize_name(row["company"])
        if key not in ids:
            raise ValueError(f"Identity references unknown company: {row['company']}")
        connection.execute(
            """INSERT INTO x_identities VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ids[key], compact_json(row.get("requested_handles") or []), int(bool(row.get("technical_identity_confirmed"))),
                row.get("technical_x_identity_status") or "unknown", row.get("target_rest_id") or None,
                row.get("target_handle") or None, row.get("identity_confidence") or row.get("confidence"),
                row.get("identity_fact_status") or row.get("fact_status"), row.get("identity_last_verified_at") or row.get("last_verified_at"),
                compact_json(row.get("identity_evidence_files") or []), compact_json(row),
            ),
        )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _data_map(element: ET.Element) -> dict[str, str]:
    return {
        str(child.attrib.get("key")): child.text or ""
        for child in element
        if _local_name(child.tag) == "data"
    }


def load_graphml(connection: sqlite3.Connection, path: Path, batch_size: int = 5000) -> tuple[int, int]:
    nodes: list[tuple[Any, ...]] = []
    edges: list[tuple[Any, ...]] = []
    node_count = 0
    edge_count = 0
    for _event, element in ET.iterparse(path, events=("end",)):
        kind = _local_name(element.tag)
        if kind == "node":
            data = _data_map(element)
            node_id = str(element.attrib["id"])
            nodes.append((
                node_id, node_id[2:] if node_id.startswith("x_") else node_id,
                data.get("n_label"), data.get("n_handle"), data.get("n_role"),
                data.get("n_evidence"), data.get("n_coverage"),
            ))
            node_count += 1
            if len(nodes) >= batch_size:
                connection.executemany("INSERT INTO x_nodes VALUES (?,?,?,?,?,?,?)", nodes)
                nodes.clear()
        elif kind == "edge":
            # GraphML serializes nodes before edges, but the final node batch may
            # still be buffered. Flush it before enforcing edge foreign keys.
            if nodes:
                connection.executemany("INSERT INTO x_nodes VALUES (?,?,?,?,?,?,?)", nodes)
                nodes.clear()
            data = _data_map(element)
            edges.append((
                str(element.attrib["id"]), str(element.attrib["source"]), str(element.attrib["target"]),
                data.get("e_relation") or "follows", data.get("e_role"), data.get("e_evidence"),
                data.get("e_coverage"), data.get("e_observer"), int(data.get("e_count") or 1),
            ))
            edge_count += 1
            if len(edges) >= batch_size:
                connection.executemany("INSERT INTO x_edges VALUES (?,?,?,?,?,?,?,?,?)", edges)
                edges.clear()
        if kind in {"node", "edge"}:
            element.clear()
    if nodes:
        connection.executemany("INSERT INTO x_nodes VALUES (?,?,?,?,?,?,?)", nodes)
    if edges:
        connection.executemany("INSERT INTO x_edges VALUES (?,?,?,?,?,?,?,?,?)", edges)
    return node_count, edge_count


def _first_connector(labels: list[str]) -> str | None:
    if len(labels) < 3:
        return None
    matches = re.findall(r"@([A-Za-z0-9_]{1,15})", str(labels[1]))
    return f"@{matches[-1]}" if matches else None


def insert_path(
    connection: sqlite3.Connection,
    *,
    path_id: str,
    cid: str,
    root: str,
    primary: bool,
    degree: str | None,
    hop_count: int | None,
    labels: list[str],
    rest_ids: list[str],
    directions: list[str],
    strength: float | None,
    human_intro_status: str,
    evidence_scope: str,
    source_artifact: str,
    raw: dict[str, Any],
) -> None:
    connection.execute(
        "INSERT INTO x_paths VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            path_id, cid, root, int(primary), degree, hop_count, _first_connector(labels), compact_json(labels),
            compact_json(rest_ids), compact_json(directions), strength, 1, human_intro_status,
            evidence_scope, source_artifact, compact_json(raw),
        ),
    )


def load_paths(connection: sqlite3.Connection, ids: dict[str, str]) -> None:
    new_paths = load_json(OUT / "solomon_paths_expanded_v4.json")
    for company_row in new_paths:
        if company_row.get("graph_reachable") is not True:
            continue
        key = normalize_name(company_row["company"])
        primary = company_row.get("primary_path") or {}
        insert_path(
            connection, path_id=f"solomon:new:{key}:primary", cid=ids[key], root="solomon", primary=True,
            degree=primary.get("x_degree"), hop_count=primary.get("hop_count"), labels=primary.get("path_labels") or [],
            rest_ids=primary.get("path_rest_ids") or [], directions=primary.get("edge_directions") or [],
            strength=primary.get("path_strength"), human_intro_status=primary.get("human_intro_status") or "human_intro_unvalidated",
            evidence_scope=company_row.get("evidence_scope") or "", source_artifact="outputs/pilot_v4/solomon_paths_expanded_v4.json", raw=primary,
        )
        for index, alternative in enumerate(company_row.get("alternative_paths") or [], 1):
            insert_path(
                connection, path_id=f"solomon:new:{key}:alternative:{index:02d}", cid=ids[key], root="solomon", primary=False,
                degree=alternative.get("x_degree"), hop_count=alternative.get("hop_count"), labels=alternative.get("path_labels") or [],
                rest_ids=alternative.get("path_rest_ids") or [], directions=alternative.get("edge_directions") or [],
                strength=alternative.get("path_strength"), human_intro_status=alternative.get("human_intro_status") or "human_intro_unvalidated",
                evidence_scope=company_row.get("evidence_scope") or "", source_artifact="outputs/pilot_v4/solomon_paths_expanded_v4.json", raw=alternative,
            )

    legacy_paths = load_json(ROOT / "outputs" / "pilot_v3" / "solomon_x_paths.json", [])
    for index, row in enumerate(legacy_paths, 1):
        key = normalize_name(row["company"])
        if key not in ids:
            continue
        insert_path(
            connection, path_id=f"solomon:legacy:{key}:{index:03d}", cid=ids[key], root="solomon", primary=bool(row.get("primary")),
            degree=row.get("x_degree"), hop_count=row.get("hop_count"), labels=row.get("path_labels") or [],
            rest_ids=row.get("path_rest_ids") or [], directions=row.get("edge_types") or [],
            strength=row.get("path_strength_product"), human_intro_status="human_intro_unvalidated",
            evidence_scope=row.get("evidence_scope") or "", source_artifact="outputs/pilot_v3/solomon_x_paths.json", raw=row,
        )

    mango = load_json(OUT / "mango_seed_target_paths.json")
    for target in mango.get("targets") or []:
        key = normalize_name(target["company"])
        if key not in ids:
            continue
        for index, row in enumerate(target.get("mango_secondary_paths") or [], 1):
            insert_path(
                connection, path_id=f"mango:{key}:{index:03d}", cid=ids[key], root="mango", primary=index == 1,
                degree="secondary", hop_count=int(row.get("x_degree") or 2), labels=row.get("path_labels") or [],
                rest_ids=row.get("path_rest_ids") or [], directions=row.get("edge_directions") or [], strength=None,
                human_intro_status=row.get("human_intro_status") or "human_intro_unvalidated",
                evidence_scope=row.get("evidence_scope") or "", source_artifact="outputs/pilot_v4/mango_seed_target_paths.json", raw=row,
            )


def load_interactions(connection: sqlite3.Connection, ids: dict[str, str]) -> None:
    sources = (
        ("solomon_primary_paths", OUT / "x_path_interactions_v4.json"),
        ("mango_seed_paths", OUT / "mango_path_interactions_v4.json"),
    )
    for collection_scope, path in sources:
        artifact = load_json(path, {"observations": []})
        for row in artifact.get("observations") or []:
            tweet_id = str(row["tweet_id"])
            source = str(row["source_handle"])
            destination = str(row["destination_handle"])
            interaction_id = str(
                row.get("interaction_id")
                or f"{collection_scope}:{source.lower()}:{destination.lower()}:{tweet_id}"
            )
            cache_files = row.get("raw_cache_files")
            if cache_files is None:
                cache_files = [row["raw_cache_file"]] if row.get("raw_cache_file") else []
            existing = connection.execute(
                "SELECT tweet_id,source_handle,destination_handle,interaction_type,tweet_url,"
                "collection_scopes_json,raw_cache_files_json,raw_json FROM x_interactions WHERE interaction_id=?",
                (interaction_id,),
            ).fetchone()
            if existing:
                core = (tweet_id, source, destination, row["interaction_type"], row["tweet_url"])
                if tuple(existing[:5]) != core:
                    raise ValueError(f"Conflicting duplicate interaction_id: {interaction_id}")
                scopes = sorted(set(json.loads(existing[5])) | {collection_scope})
                caches = sorted(set(json.loads(existing[6])) | {str(value) for value in cache_files})
                raw_payload = json.loads(existing[7])
                observations = raw_payload.get("observations") if isinstance(raw_payload, dict) else None
                if observations is None:
                    observations = [raw_payload]
                if compact_json(row) not in {compact_json(value) for value in observations}:
                    observations.append(row)
                connection.execute(
                    "UPDATE x_interactions SET collection_scopes_json=?,raw_cache_files_json=?,raw_json=? WHERE interaction_id=?",
                    (compact_json(scopes), compact_json(caches), compact_json({"observations": observations}), interaction_id),
                )
            else:
                connection.execute(
                    "INSERT INTO x_interactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        interaction_id, tweet_id, compact_json([collection_scope]), source, destination,
                        row["interaction_type"], row["tweet_url"], row.get("created_at"), row.get("text"),
                        row.get("fact_status") or "observed_public_x_interaction",
                        row.get("confidence") or "high", row.get("human_relationship_inference") or "not_inferred",
                        compact_json(cache_files), compact_json({"observations": [row]}),
                    ),
                )
            for company in row.get("companies") or []:
                key = normalize_name(company)
                if key in ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO x_interaction_companies VALUES (?,?)",
                        (interaction_id, ids[key]),
                    )


def load_sponsors(connection: sqlite3.Connection, ids: dict[str, str]) -> None:
    for row in load_json(OUT / "sponsor_expanded_observations.json"):
        key = resolve_company_key(row["brand"], ids)
        connection.execute(
            "INSERT INTO sponsor_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["id"], ids[key], row["creator"], row.get("creator_handle_or_channel"), row["platform"],
                row["content_url"], row.get("content_title"), row.get("published_at"), row["disclosure_type"],
                row.get("evidence_text"), row.get("source_type"), row.get("confidence"),
                row.get("business_contact_or_route"), compact_json(row),
            ),
        )
    for row in load_json(OUT / "sponsor_expanded_edges.json"):
        key = resolve_company_key(row["brand"], ids)
        connection.execute(
            "INSERT INTO sponsor_edges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["edge_id"], ids[key], row["creator_id"], row["creator"], row.get("creator_handle_or_channel"),
                row["observation_count"], row["paid_observation_count"], row["affiliate_observation_count"],
                row["mention_observation_count"], int(bool(row["paid_signal"])), int(bool(row["repeat_paid"])),
                int(bool(row["multi_creator_paid_brand"])), row["commercial_status"], row.get("first_seen"),
                row.get("last_seen"), compact_json(row),
            ),
        )


def load_operators(connection: sqlite3.Connection, ids: dict[str, str]) -> None:
    for row in load_json(ROOT / "data" / "pilot_v4" / "top_operator_routes.json", []):
        key = normalize_name(row["company"])
        recommended = row.get("recommended_operator") or {}
        public_route = row.get("public_business_route") or {}
        connection.execute(
            "INSERT INTO operator_routes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ids[key], recommended.get("name"), recommended.get("role"), recommended.get("status"), 0,
                public_route.get("type"), public_route.get("value"), row.get("why_now"), row.get("mango_offer_angle"),
                row.get("opening_signal"), row.get("outreach_mode"), row.get("fallback_route"),
                row.get("last_verified_at"), compact_json(row),
            ),
        )
        identity = row.get("operator_identity") or {}
        profile = identity.get("profile") or {}
        evidence_urls = [
            str(value) for value in identity.get("official_role_evidence_urls") or [] if value
        ]
        connection.execute(
            "INSERT INTO operator_identities VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ids[key], recommended.get("name"), recommended.get("role"),
                recommended.get("x_handle") or profile.get("handle"),
                recommended.get("x_rest_id") or profile.get("rest_id"),
                int(bool(identity.get("confirmed"))), identity.get("status") or "not_evaluated",
                compact_json(evidence_urls), identity.get("rapid_x_cache_file"), row.get("last_verified_at"),
                0, compact_json(identity),
            ),
        )


def load_operator_person_paths(connection: sqlite3.Connection, ids: dict[str, str]) -> None:
    artifact = load_json(OUT / "operator_person_paths_v4.json", {"records": []})
    for row in artifact.get("records") or []:
        key = normalize_name(row["company"])
        if key not in ids:
            raise ValueError(f"Operator person path references unknown company: {row['company']}")
        person = row.get("operator_person") or {}
        for root, field in (("solomon", "solomon_to_operator_person"), ("mango", "mango_to_operator_person")):
            path = row.get(field) or {}
            primary = path.get("primary_path") or {}
            if root == "mango" and not primary:
                routes = list(path.get("mango_direct_paths") or []) + list(path.get("mango_secondary_paths") or [])
                primary = routes[0] if routes else {}
            degree = path.get("x_degree") or primary.get("x_degree") or primary.get("x_relationship_level")
            hop_count = path.get("hop_count") or primary.get("hop_count")
            if hop_count is None and isinstance(degree, int):
                hop_count = degree
            if isinstance(degree, int):
                degree_label = {1: "direct", 2: "secondary", 3: "third"}.get(degree)
            else:
                normalized_degree = str(degree or "").strip().casefold()
                degree_label = normalized_degree if normalized_degree in {"direct", "secondary", "third"} else None
            labels = primary.get("path_labels") or path.get("primary_path_labels") or []
            rest_ids = primary.get("path_rest_ids") or path.get("primary_path_rest_ids") or []
            directions = primary.get("edge_directions") or path.get("primary_edge_directions") or []
            graph_reachable = path.get("graph_reachable")
            connection.execute(
                "INSERT INTO operator_person_paths VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"operator:{root}:{key}", ids[key], root, int(bool(person.get("identity_confirmed"))),
                    None if graph_reachable is None else int(bool(graph_reachable)),
                    degree_label,
                    int(hop_count) if hop_count not in (None, "") else None,
                    compact_json(labels), compact_json(rest_ids), compact_json(directions),
                    path.get("human_intro_status") or "not_evaluable_operator_identity_unconfirmed",
                    "outputs/pilot_v4/operator_person_paths_v4.json", compact_json(path),
                ),
            )


def load_actions(connection: sqlite3.Connection, ids: dict[str, str]) -> None:
    for index, row in enumerate(load_json(OUT / "priority_action_queue_v4.json"), 1):
        key = normalize_name(row["company"])
        action_id = f"action:{int(row['execution_wave'])}:{normalize_name(row['source_group'])}:{key}:{index:03d}"
        connection.execute(
            "INSERT INTO action_queue VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                action_id, ids[key], row["execution_wave"], row["action_band"], row["source_group"],
                row.get("within_group_rank"), row.get("owner"), row.get("primary_next_action") or "",
                row.get("fallback"), row.get("success_condition"),
                None if row.get("graph_reachable") is None else int(bool(row.get("graph_reachable"))),
                None if row.get("mango_graph_reachable") is None else int(bool(row.get("mango_graph_reachable"))),
                str(row.get("x_degree") or ""), row.get("human_intro_status") or "human_intro_unvalidated",
                row.get("recommended_operator_name"), row.get("recommended_operator_role"),
                row.get("recommended_operator_status"), row.get("operator_acquisition_next_step"),
                row.get("recommended_operator_x_handle"), row.get("recommended_operator_x_rest_id"),
                row.get("spend_last_verified_at"), int(bool(row.get("budget_authority_confirmed"))),
                compact_json(row.get("evidence_urls") or []), compact_json(row),
            ),
        )


def load_gtm_cases(connection: sqlite3.Connection, ids: dict[str, str]) -> int:
    path = ROOT / "data" / "pilot_v4" / "gtm_case_studies_v4.json"
    if not path.exists():
        return 0
    payload = load_json(path)
    rows = payload.get("case_studies") or [] if isinstance(payload, dict) else payload
    for index, row in enumerate(rows, 1):
        key = normalize_name(row["company"])
        connection.execute(
            "INSERT INTO gtm_cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"gtm:{key}:{index:02d}", ids.get(key), row["company"], row.get("category"), row.get("maturity_stage"),
                compact_json(row.get("gtm_motion") or row.get("gtm_motions") or []),
                (row.get("budget_spend_evidence") or {}).get("classification")
                or row.get("budget_spend_evidence_classification") or row.get("spend_classification"),
                compact_json(row.get("buyer_roles") or []), compact_json(row.get("what_mango_should_copy") or []),
                compact_json(row.get("what_not_to_copy") or []), compact_json(row.get("evidence") or []),
                row.get("last_verified_at"), compact_json(row),
            ),
        )
    return connection.execute("SELECT COUNT(*) FROM gtm_cases").fetchone()[0]


def table_counts(connection: sqlite3.Connection, tables: Iterable[str]) -> dict[str, int]:
    return {table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in tables}


def build_database(db_path: Path, summary_path: Path) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp = db_path.with_suffix(db_path.suffix + ".building")
    if temp.exists():
        temp.unlink()
    connection = sqlite3.connect(temp)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        create_schema(connection)
        universe = load_json(OUT / "all_company_universe_v4.json")
        adjusted = load_json(OUT / "new_candidate_rankings_graph_adjusted_v4.json")
        ids = load_companies(connection, universe, adjusted)
        mango = load_json(OUT / "mango_seed_target_paths.json")
        load_x_identities(connection, mango, ids)
        node_count, edge_count = load_graphml(connection, OUT / "solomon_graph_expanded_v4.graphml")
        load_paths(connection, ids)
        load_interactions(connection, ids)
        load_sponsors(connection, ids)
        load_operators(connection, ids)
        load_operator_person_paths(connection, ids)
        load_actions(connection, ids)
        gtm_count = load_gtm_cases(connection, ids)
        generated_at = datetime.now(timezone.utc).isoformat()
        metadata = {
            "schema_version": "v4",
            "generated_at": generated_at,
            "x_graph_source": "outputs/pilot_v4/solomon_graph_expanded_v4.graphml",
            "surf_verified_fields": 0,
            "surf_status": "paid_balance_zero",
            "relationship_rule": "graph reachability and human intro willingness are separate",
        }
        connection.executemany("INSERT INTO metadata VALUES (?,?)", [(key, compact_json(value) if not isinstance(value, str) else value) for key, value in metadata.items()])
        connection.commit()

        counts = table_counts(connection, [
            "companies", "company_aliases", "company_sources", "x_identities", "x_nodes", "x_edges",
            "x_paths", "x_interactions", "x_interaction_companies", "sponsor_observations", "sponsor_edges",
            "operator_routes", "operator_identities", "operator_person_paths", "action_queue", "gtm_cases",
        ])
        expected_interactions = len({
            str(row.get("interaction_id") or "")
            for path in (OUT / "x_path_interactions_v4.json", OUT / "mango_path_interactions_v4.json")
            for row in (load_json(path, {"observations": []}).get("observations") or [])
        })
        expected = {
            "companies": 77,
            "x_identities": 77,
            "x_nodes": node_count,
            "x_edges": edge_count,
            "x_interactions": expected_interactions,
            "sponsor_observations": len(load_json(OUT / "sponsor_expanded_observations.json")),
            "sponsor_edges": len(load_json(OUT / "sponsor_expanded_edges.json")),
            "operator_routes": len(load_json(ROOT / "data" / "pilot_v4" / "top_operator_routes.json", [])),
            "operator_identities": len(load_json(ROOT / "data" / "pilot_v4" / "top_operator_routes.json", [])),
            "operator_person_paths": 2 * len(load_json(ROOT / "data" / "pilot_v4" / "top_operator_routes.json", [])),
            "action_queue": len(load_json(OUT / "priority_action_queue_v4.json")),
            "gtm_cases": 8 if (ROOT / "data" / "pilot_v4" / "gtm_case_studies_v4.json").exists() else 0,
        }
        mismatches = {key: {"expected": value, "actual": counts.get(key)} for key, value in expected.items() if counts.get(key) != value}
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        paid_leak = connection.execute(
            "SELECT COUNT(*) FROM sponsor_edges WHERE paid_signal = 1 AND paid_observation_count = 0"
        ).fetchone()[0]
        if mismatches or integrity != "ok" or foreign_key_violations or paid_leak:
            raise ValueError({"count_mismatches": mismatches, "integrity": integrity, "foreign_key_violations": foreign_key_violations, "paid_leak": paid_leak})
        summary = {
            "schema_version": "v4",
            "generated_at": generated_at,
            "database": str(db_path.relative_to(ROOT)),
            "counts": counts,
            "quality": {
                "integrity_check": integrity,
                "foreign_key_violation_count": len(foreign_key_violations),
                "count_mismatches": mismatches,
                "affiliate_or_mention_promoted_to_paid": False,
            },
            "views": ["v_reachable_targets", "v_paid_sponsor_signals", "v_actionable_company_summary", "v_operator_relationships"],
        }
    finally:
        connection.close()
    temp.replace(db_path)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    print(json.dumps(build_database(args.database, args.summary), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
