"""Backfill only IntroPath follow-direction provenance from a baked source.

This is intentionally narrower than ``migrate_bd_data.py`` so it is safe to
run on every boot against an existing persistent database.  It never updates
path labels, reachability, human intro status, or any other research/human
field, and only fills rows whose ``edge_directions`` is still NULL.

Locally the authoritative v4 SQLite is preferred.  Production images do not
contain that 46 MB research database, so they fall back to the freshly baked
``data/kol.seed.db`` shipped with each deployment; that seed contains the same
source_id -> direction mapping and can repair an older persistent volume.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import BASE_DIR, DB_PATH  # noqa: E402
from scripts.add_intro_path_direction_columns import ensure_intro_path_direction_columns  # noqa: E402

REPO_ROOT = BASE_DIR.parent
V4_SOURCE_PATH = REPO_ROOT / "outputs" / "pilot_v4" / "mango_bd_v4.sqlite"
BAKED_SEED_PATH = BASE_DIR / "data" / "kol.seed.db"


def direction_provenance(
    direction_json: str | None,
    raw_json: str | None,
) -> tuple[str, bool, str | None]:
    """Validate and preserve a v4 source direction payload losslessly."""

    serialized = direction_json or "[]"
    try:
        directions = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid edge-direction JSON in v4 source: {serialized!r}") from exc
    if not isinstance(directions, list) or any(not isinstance(item, str) for item in directions):
        raise ValueError(f"Edge-direction payload must be a JSON string array: {serialized!r}")
    if directions:
        return serialized, False, None

    raw = json.loads(raw_json or "{}")
    status = raw.get("graph_reachability_status")
    if status in {"not_evaluable_identity_unconfirmed", "not_evaluable_technical_x_identity_unresolved"}:
        reason = "源记录未能确认目标 X 身份，无法评估或生成逐边 follow 方向。"
    elif status in {"no_path_in_observed_graph", "no_observed_routable_path_in_available_cache"}:
        reason = "源记录在已审计图/可用缓存中未观察到可路由路径，因此没有逐边 follow 方向；未命中不代表关系不存在。"
    else:
        reason = "源记录未提供逐边 follow 方向；不得从路径节点顺序推断关注方向。"
    return serialized, True, reason


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _read_direction_snapshot(source_path: str | Path) -> list[tuple[str, str, bool, str | None]]:
    source = sqlite3.connect(str(source_path))
    source.row_factory = sqlite3.Row
    try:
        tables = _table_names(source)
        rows: list[tuple[str, str, bool, str | None]] = []
        if {"x_paths", "operator_person_paths"}.issubset(tables):
            for row in source.execute("SELECT path_id, edge_directions_json, raw_json FROM x_paths"):
                values = direction_provenance(row["edge_directions_json"], row["raw_json"])
                rows.append((row["path_id"], *values))
            for row in source.execute(
                "SELECT operator_path_id, primary_edge_directions_json, raw_json FROM operator_person_paths"
            ):
                values = direction_provenance(row["primary_edge_directions_json"], row["raw_json"])
                rows.append((row["operator_path_id"], *values))
            return rows

        if "intro_paths" in tables:
            columns = {row[1] for row in source.execute("PRAGMA table_info(intro_paths)")}
            required = {
                "source_id",
                "edge_directions",
                "direction_data_unavailable",
                "direction_data_unavailable_reason",
            }
            if not required.issubset(columns):
                raise RuntimeError(f"Baked seed lacks direction columns: {sorted(required - columns)}")
            for row in source.execute(
                "SELECT source_id, edge_directions, direction_data_unavailable, "
                "direction_data_unavailable_reason FROM intro_paths "
                "WHERE source_id IS NOT NULL AND edge_directions IS NOT NULL"
            ):
                rows.append(
                    (
                        row["source_id"],
                        row["edge_directions"],
                        bool(row["direction_data_unavailable"]),
                        row["direction_data_unavailable_reason"],
                    )
                )
            return rows

        raise RuntimeError(f"No supported direction source tables in {source_path}")
    finally:
        source.close()


def _default_source_path() -> Path:
    for candidate in (V4_SOURCE_PATH, BAKED_SEED_PATH):
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    raise RuntimeError("No v4 graph or baked seed is available for IntroPath direction backfill")


def backfill_intro_path_directions(
    db_path: str | Path = DB_PATH,
    source_path: str | Path | None = None,
) -> dict[str, int | str]:
    ensure_intro_path_direction_columns(db_path)
    resolved_source = Path(source_path) if source_path is not None else _default_source_path()
    snapshot = _read_direction_snapshot(resolved_source)

    destination = sqlite3.connect(str(db_path))
    try:
        updated = 0
        for source_id, directions, unavailable, reason in snapshot:
            cursor = destination.execute(
                "UPDATE intro_paths SET edge_directions = ?, direction_data_unavailable = ?, "
                "direction_data_unavailable_reason = ? "
                "WHERE source_id = ? AND source_id IS NOT NULL AND edge_directions IS NULL",
                (directions, int(unavailable), reason, source_id),
            )
            updated += cursor.rowcount
        destination.commit()
        remaining = destination.execute(
            "SELECT COUNT(*) FROM intro_paths WHERE source_id IS NOT NULL AND edge_directions IS NULL"
        ).fetchone()[0]
    finally:
        destination.close()

    return {
        "source_rows": len(snapshot),
        "updated": updated,
        "remaining_source_rows_without_payload": remaining,
        "source": str(resolved_source),
    }


def main() -> None:
    stats = backfill_intro_path_directions()
    print(
        "intro_paths direction backfill: "
        f"source_rows={stats['source_rows']}, updated={stats['updated']}, "
        f"remaining={stats['remaining_source_rows_without_payload']}, source={stats['source']}"
    )
    if stats["remaining_source_rows_without_payload"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
