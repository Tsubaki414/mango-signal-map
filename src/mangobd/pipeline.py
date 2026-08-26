from __future__ import annotations

import json
from pathlib import Path

from .io import load_records, validate_dataset, write_csv, write_dict_csv, write_graphml, write_sqlite
from .models import Entity, Evidence, Project, Relationship, Sponsorship, to_dict
from .scoring import load_scoring_config, score_projects


def run_pipeline(data_dir: Path, output_dir: Path, scoring_path: Path) -> dict[str, int]:
    evidence = load_records(data_dir / "evidence.json", Evidence)
    entities = load_records(data_dir / "entities.json", Entity)
    relationships = load_records(data_dir / "relationships.json", Relationship)
    projects = load_records(data_dir / "projects.json", Project)
    sponsorships = load_records(data_dir / "sponsorships.json", Sponsorship)
    actions = json.loads((data_dir / "actions.json").read_text(encoding="utf-8"))
    case_studies = json.loads((data_dir / "case_studies.json").read_text(encoding="utf-8"))
    errors = validate_dataset(projects, entities, relationships, sponsorships, evidence)
    if errors:
        raise ValueError("Dataset validation failed:\n- " + "\n- ".join(errors))
    evidence_by_id = {item.id: item for item in evidence}
    projects = score_projects(projects, evidence_by_id, load_scoring_config(scoring_path))
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "projects.csv", projects)
    write_csv(output_dir / "creators_sponsorships.csv", sponsorships)
    write_csv(output_dir / "entities.csv", entities)
    write_csv(output_dir / "relationships.csv", relationships)
    write_csv(output_dir / "evidence.csv", evidence)
    write_dict_csv(output_dir / "priority_actions.csv", actions)
    write_dict_csv(output_dir / "gtm_case_studies.csv", case_studies)
    write_graphml(output_dir / "relationship_graph.graphml", entities, relationships)
    write_sqlite(output_dir / "mango_bd.sqlite", projects, entities, relationships, sponsorships, evidence)
    (output_dir / "pilot.json").write_text(
        json.dumps(
            {
                "projects": [to_dict(item) for item in projects],
                "entities": [to_dict(item) for item in entities],
                "relationships": [to_dict(item) for item in relationships],
                "sponsorships": [to_dict(item) for item in sponsorships],
                "evidence": [to_dict(item) for item in evidence],
                "actions": actions,
                "case_studies": case_studies,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "projects": len(projects),
        "entities": len(entities),
        "relationships": len(relationships),
        "sponsorships": len(sponsorships),
        "evidence": len(evidence),
        "actions": len(actions),
        "case_studies": len(case_studies),
    }
