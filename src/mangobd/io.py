from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import fields
from pathlib import Path
from typing import Any, Iterable, TypeVar

from .models import Entity, Evidence, Project, Relationship, Sponsorship

T = TypeVar("T")


def load_records(path: str | Path, record_type: type[T]) -> list[T]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [record_type(**row) for row in rows]


def validate_dataset(
    projects: list[Project],
    entities: list[Entity],
    relationships: list[Relationship],
    sponsorships: list[Sponsorship],
    evidence: list[Evidence],
) -> list[str]:
    errors: list[str] = []
    entity_ids = {item.id for item in entities}
    project_ids = {item.id for item in projects}
    evidence_ids = {item.id for item in evidence}
    if len(entity_ids) != len(entities):
        errors.append("duplicate entity ids")
    if len(project_ids) != len(projects):
        errors.append("duplicate project ids")
    if len(evidence_ids) != len(evidence):
        errors.append("duplicate evidence ids")
    for edge in relationships:
        if edge.source_id not in entity_ids or edge.target_id not in entity_ids:
            errors.append(f"relationship {edge.id} has missing endpoint")
        missing = set(edge.evidence_ids) - evidence_ids
        if missing:
            errors.append(f"relationship {edge.id} missing evidence {sorted(missing)}")
    for project in projects:
        missing_people = set(project.decision_maker_ids) - entity_ids
        missing_evidence = set(project.evidence_ids) - evidence_ids
        if missing_people:
            errors.append(f"project {project.id} missing decision makers {sorted(missing_people)}")
        if missing_evidence:
            errors.append(f"project {project.id} missing evidence {sorted(missing_evidence)}")
    for item in sponsorships:
        if item.creator_id not in entity_ids or item.project_id not in project_ids:
            errors.append(f"sponsorship {item.id} has missing creator or project")
        missing = set(item.evidence_ids) - evidence_ids
        if missing:
            errors.append(f"sponsorship {item.id} missing evidence {sorted(missing)}")
    return errors


def _serialize(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def write_csv(path: Path, rows: Iterable[Any]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    names = [item.name for item in fields(rows[0])]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _serialize(getattr(row, name)) for name in names})


def write_dict_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    names = list(rows[0])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _serialize(row.get(name)) for name in names})


def write_graphml(path: Path, entities: list[Entity], relationships: list[Relationship]) -> None:
    from xml.sax.saxutils import escape

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
        '<key id="name" for="node" attr.name="name" attr.type="string"/>',
        '<key id="kind" for="node" attr.name="kind" attr.type="string"/>',
        '<key id="rel" for="edge" attr.name="relationship_type" attr.type="string"/>',
        '<key id="strength" for="edge" attr.name="strength" attr.type="int"/>',
        '<graph id="MangoBD" edgedefault="directed">',
    ]
    for entity in entities:
        lines.append(
            f'<node id="{escape(entity.id)}"><data key="name">{escape(entity.name)}</data>'
            f'<data key="kind">{escape(entity.kind)}</data></node>'
        )
    for edge in relationships:
        lines.append(
            f'<edge id="{escape(edge.id)}" source="{escape(edge.source_id)}" target="{escape(edge.target_id)}">'
            f'<data key="rel">{escape(edge.relationship_type)}</data>'
            f'<data key="strength">{edge.strength}</data></edge>'
        )
    lines.extend(["</graph>", "</graphml>"])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_sqlite(
    path: Path,
    projects: list[Project],
    entities: list[Entity],
    relationships: list[Relationship],
    sponsorships: list[Sponsorship],
    evidence: list[Evidence],
) -> None:
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    try:
        for name, rows in {
            "projects": projects,
            "entities": entities,
            "relationships": relationships,
            "sponsorships": sponsorships,
            "evidence": evidence,
        }.items():
            if not rows:
                continue
            column_names = [item.name for item in fields(rows[0])]
            columns = ", ".join(f'"{column}" TEXT' for column in column_names)
            connection.execute(f'CREATE TABLE "{name}" ({columns})')
            placeholders = ", ".join("?" for _ in column_names)
            values = [[_serialize(getattr(row, col)) for col in column_names] for row in rows]
            connection.executemany(f'INSERT INTO "{name}" VALUES ({placeholders})', values)
        connection.commit()
    finally:
        connection.close()
