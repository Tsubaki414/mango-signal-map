"""One-time (idempotent) migration: import the previously-delivered
Solomon Action Map / AI-company x KOL evidence system
(outputs/pilot_v4/mango_bd_v4.sqlite) into the unified kol_database schema.

Source -> destination mapping:
    companies, company_aliases, company_sources -> Company, CompanyAlias, CompanySource
    operator_identities                          -> Operator
    x_paths (company-account routes) +
    operator_person_paths (person routes)         -> IntroPath (target_type distinguishes the two)
    action_queue                                  -> ActionItem
    sponsor_edges + sponsor_observations          -> SponsorshipEvidence, entity-resolved against
                                                      the existing Creator/SocialAccount tables
    gtm_cases                                     -> GtmCase
    SOLOMON_KOL_COCKPIT.md section 2 (hand-curated,
    not mechanically re-derivable)                -> ConnectorBrief, seeded verbatim below

Entity resolution: a sponsor_edges creator is matched to an existing
Creator by (platform, handle) case-insensitively. On a match, the
evidence links to that Creator (now visibly connected to both the quote
library AND the sponsorship graph). On no match, a new Creator +
SocialAccount is created with creator_class="Unknown" (Needs Review) and
no rate card -- this creator is known from research, not yet quoted --
exactly the "no Mango quote yet" state the product spec calls for.

Safe to re-run, and safe against clobbering human edits made through the
API since the data-integrity sprint:
  - GtmCase/CompanySource/CompanyAlias/ConnectorBrief have no human-editable
    fields, so they're simply deleted and re-inserted from source each run.
  - Company is upserted by company_id: source-owned fields (name, stage,
    score, priority_tier, spend levels, x_handle) always refresh; fields a
    human can edit via PATCH (category, geography, why_now, budget_evidence,
    buyer_or_route, internal_notes) are only filled in when still empty,
    never overwritten once set; last_verified_at is pure human state and is
    never touched here.
  - Operator/IntroPath/ActionItem/SponsorshipEvidence are upserted by a
    stable source key (Operator.source_id, IntroPath.source_id,
    ActionItem.source_id, SponsorshipEvidence.source_id) -- an already-seen
    source row is left completely alone on re-run (an edited role, a
    Confirm/Reject review, an action-item status change, etc. all survive);
    only genuinely new source rows get created. Rows a human added directly
    (source_id=None) are never touched by this script at all.
  - Newly-created Creators from a previous run are matched by (platform,
    handle) on subsequent runs, not duplicated.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db import get_session, init_db  # noqa: E402
from backend.models import (  # noqa: E402
    ActionItem,
    Company,
    CompanyAlias,
    CompanySource,
    ConnectorBrief,
    Creator,
    GtmCase,
    IntroPath,
    Operator,
    SocialAccount,
    SponsorshipEvidence,
)
from scripts.add_intro_path_direction_columns import ensure_intro_path_direction_columns  # noqa: E402
from scripts.backfill_intro_path_directions import direction_provenance as _direction_provenance  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BD_SQLITE_PATH = REPO_ROOT / "outputs" / "pilot_v4" / "mango_bd_v4.sqlite"

PLATFORM_NORMALIZE = {
    "youtube": "YouTube",
    "x": "X",
    "twitter": "X",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}

# Hand-curated in a prior delivery (SOLOMON_KOL_COCKPIT.md section 2): the
# 13 Wave-1 companies compressed into 5 connector conversations, including
# the route overrides ("keep X as the company-account route, but Y->Z is
# the stronger observed operator-person route"). This synthesis is not
# mechanically re-derivable from the graph alone, so it's migrated verbatim
# rather than regenerated.
CONNECTOR_BRIEFS = [
    {
        "connector_name": "Jennie Liu",
        "connector_x_handle": "@jenniekusu",
        "company_ids": ["company:replit", "company:cursor", "company:runway", "company:perplexity", "company:pika"],
        "best_current_action": "Ask one structured question per company: actual person, nature of relationship, last interaction, and willingness to make a contextual intro. For Runway, Jennie -> Runway/Cristobal is only a one-way public-follow research clue; real acquaintance and willingness to introduce remain unconfirmed.",
    },
    {
        "connector_name": "Evie Yang",
        "connector_x_handle": "@0xEvieYang",
        "company_ids": ["company:pixverse", "company:gumloop", "company:sapien"],
        "best_current_action": "Validate PixVerse/Gumloop operators. For Sapien, keep Evie as the company-account route but use Jennie -> Rowan Stone as the stronger observed operator-person route.",
    },
    {
        "connector_name": "Max For AI",
        "connector_x_handle": "@MaxForAI",
        "company_ids": ["company:meshy", "company:openart"],
        "best_current_action": "Validate whether Max knows the creator/partnership owner. Meshy already has a verified operator, Ethan Yuanming Hu, but no observed Solomon->person path.",
    },
    {
        "connector_name": "Starzq",
        "connector_x_handle": "@starzq",
        "company_ids": ["company:talusnetwork", "company:allora"],
        "best_current_action": "Validate the company relationship. For Talus, keep Starzq as the company-account route but use Choc -> Mike Hanono as the stronger observed operator-person route.",
    },
    {
        "connector_name": "Choc",
        "connector_x_handle": "@choc07_",
        "company_ids": ["company:myshell", "company:talusnetwork"],
        "best_current_action": "Ask for MyShell's actual owner and separately validate willingness to connect Solomon to Talus founder Mike Hanono.",
    },
]


def _norm_platform(raw: str | None) -> str:
    if not raw:
        return "Unknown"
    return PLATFORM_NORMALIZE.get(raw.strip().lower(), raw.strip())


def _backfill_source_path_directions(
    path: IntroPath,
    direction_json: str | None,
    raw_json: str | None,
) -> None:
    """Fill only never-migrated direction fields on a source-owned path.

    A non-NULL ``edge_directions`` value (including the explicit JSON value
    ``[]``) means the row has already been migrated or subsequently curated;
    preserve it and its availability metadata.  Human-created paths have no
    source_id and never enter this helper.
    """

    if path.source_id is None or path.edge_directions is not None:
        return
    directions, unavailable, reason = _direction_provenance(direction_json, raw_json)
    path.edge_directions = directions
    path.direction_data_unavailable = unavailable
    path.direction_data_unavailable_reason = reason


def migrate() -> dict[str, int]:
    if not BD_SQLITE_PATH.exists():
        raise SystemExit(f"Source BD SQLite not found at {BD_SQLITE_PATH}")

    # ``create_all`` cannot ALTER an existing SQLite table.  Ensure the new
    # mapped IntroPath columns before SQLAlchemy performs any SELECT against
    # that model; this also makes direct CLI use safe outside entrypoint.sh.
    init_db()
    ensure_intro_path_direction_columns()
    bd = sqlite3.connect(str(BD_SQLITE_PATH))
    bd.row_factory = sqlite3.Row
    session = get_session()
    canonical_company_ids = {row[0] for row in bd.execute("SELECT company_id FROM companies")}

    stats = {
        "companies": 0,
        "aliases": 0,
        "sources": 0,
        "operators": 0,
        "intro_paths": 0,
        "action_items": 0,
        "sponsorship_evidence": 0,
        "gtm_cases": 0,
        "connector_briefs": 0,
        "creators_matched": 0,
        "creators_created": 0,
    }

    # Only the tables with zero human-editable fields get the simple
    # delete-and-reinsert treatment (source SQLite is fully authoritative
    # for these, nothing to lose). Company/Operator/IntroPath/ActionItem/
    # SponsorshipEvidence are upserted by a stable source key instead (see
    # each section below) -- a human edit via the API (category, operator
    # role, action-item status, sponsorship review, ...) must survive a
    # re-run of this script, not get silently clobbered by the next refresh.
    # Only canonical v4 rows are source-authoritative.  Keep provenance and
    # related research attached to locally discovered companies outside the
    # v4 universe; a full-table delete used to erase those 12 companies'
    # CompanySource rows on every refresh.
    for model in (GtmCase, CompanySource, CompanyAlias):
        session.query(model).filter(model.company_id.in_(canonical_company_ids)).delete(synchronize_session=False)
    session.query(ConnectorBrief).delete()
    session.commit()

    # --- Companies: upsert by company_id ---
    # Fields the source can always safely refresh (never exposed via
    # PATCH /api/companies, so no human edit to protect). Fields NOT in
    # this list (category, geography, why_now, budget_evidence,
    # buyer_or_route, internal_notes) are only filled in when currently
    # empty -- a human-provided or human-corrected value is never
    # overwritten by a later source re-import. last_verified_at is pure
    # human state and this migration never touches it at all.
    existing_companies = {c.company_id: c for c in session.query(Company).all()}
    for row in bd.execute("SELECT * FROM companies"):
        raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        company = existing_companies.get(row["company_id"])
        if company is None:
            company = Company(company_id=row["company_id"])
            session.add(company)
            existing_companies[row["company_id"]] = company

        company.name = row["company"]
        company.stage = row["cohort"] or None
        company.score_value = row["score_value"]
        company.score_comparison_group = row["score_comparison_group"]
        company.priority_tier = row["priority_tier"]
        company.spend_evidence_level = row["spend_evidence_level"]
        company.spend_mechanism_level = row["spend_mechanism_level"]
        company.x_handle = (raw.get("x_handle_candidate") or "").lstrip("@") or company.x_handle
        company.raw_json = row["raw_json"]

        if not company.category:
            company.category = row["category"] or None
        if not company.geography:
            company.geography = row["geography"] or None
        if not company.why_now:
            company.why_now = row["why_now"] or None
        if not company.budget_evidence:
            company.budget_evidence = row["budget_evidence"] or None
        if not company.buyer_or_route:
            company.buyer_or_route = row["buyer_or_route"] or raw.get("recommended_next_action")
        if not company.internal_notes:
            company.internal_notes = raw.get("risk")

        stats["companies"] += 1
    session.commit()

    for row in bd.execute("SELECT * FROM company_aliases"):
        session.add(CompanyAlias(company_id=row["company_id"], alias=row["alias"]))
        stats["aliases"] += 1
    for row in bd.execute("SELECT * FROM company_sources"):
        session.add(CompanySource(company_id=row["company_id"], source_url=row["source_url"]))
        stats["sources"] += 1
    session.commit()

    # --- Operators: upsert by source_id (operator_identities.company_id) ---
    # On a match, every human-editable field (name, role, x_handle,
    # identity_confirmed, budget_authority_confirmed, evidence_urls) is left
    # completely alone -- only x_rest_id refreshes, since it's never exposed
    # via PATCH and purely technical. New source_ids create a new row as
    # before; this never deletes an operator (including human-added ones,
    # which have source_id=None and are never touched by this loop at all).
    existing_operators = {o.source_id: o for o in session.query(Operator).filter(Operator.source_id.isnot(None)).all()}
    for row in bd.execute("SELECT * FROM operator_identities WHERE operator_name IS NOT NULL"):
        if row["company_id"] in existing_operators:
            existing_operators[row["company_id"]].x_rest_id = row["x_rest_id"]
            stats["operators"] += 1
            continue
        urls = json.loads(row["official_evidence_urls_json"] or "[]")
        session.add(
            Operator(
                company_id=row["company_id"],
                source_id=row["company_id"],
                name=row["operator_name"],
                role=row["operator_role"],
                x_handle=row["x_handle"],
                x_rest_id=row["x_rest_id"],
                identity_confirmed=bool(row["identity_confirmed"]),
                identity_status=row["identity_status"],
                budget_authority_confirmed=bool(row["budget_authority_confirmed"]),
                evidence_urls="\n".join(urls) if urls else None,
            )
        )
        stats["operators"] += 1
    session.commit()

    # --- Intro paths: company-account routes (x_paths) + operator-person routes ---
    # Upsert by source_id (path_id / operator_path_id); an existing path is
    # left alone (a human may have corrected human_intro_status or added
    # path_labels via PATCH /api/intro-paths).
    existing_paths = {p.source_id: p for p in session.query(IntroPath).filter(IntroPath.source_id.isnot(None)).all()}
    for row in bd.execute("SELECT * FROM x_paths"):
        if row["path_id"] in existing_paths:
            _backfill_source_path_directions(
                existing_paths[row["path_id"]],
                row["edge_directions_json"],
                row["raw_json"],
            )
            stats["intro_paths"] += 1
            continue
        labels = json.loads(row["path_labels_json"] or "[]")
        directions, directions_unavailable, directions_reason = _direction_provenance(
            row["edge_directions_json"], row["raw_json"]
        )
        session.add(
            IntroPath(
                company_id=row["company_id"],
                source_id=row["path_id"],
                target_type="company_account",
                root=row["root"],
                is_primary=bool(row["is_primary"]),
                degree_label=row["degree_label"],
                hop_count=row["hop_count"],
                connector_handle=row["connector_handle"],
                path_labels=" -> ".join(labels) if labels else None,
                edge_directions=directions,
                direction_data_unavailable=directions_unavailable,
                direction_data_unavailable_reason=directions_reason,
                graph_reachable=bool(row["graph_reachable"]),
                human_intro_status=row["human_intro_status"],
                evidence_scope=row["evidence_scope"],
                source_artifact=row["source_artifact"],
            )
        )
        stats["intro_paths"] += 1

    for row in bd.execute("SELECT * FROM operator_person_paths"):
        if row["operator_path_id"] in existing_paths:
            _backfill_source_path_directions(
                existing_paths[row["operator_path_id"]],
                row["primary_edge_directions_json"],
                row["raw_json"],
            )
            stats["intro_paths"] += 1
            continue
        labels = json.loads(row["primary_path_labels_json"] or "[]")
        directions, directions_unavailable, directions_reason = _direction_provenance(
            row["primary_edge_directions_json"], row["raw_json"]
        )
        session.add(
            IntroPath(
                company_id=row["company_id"],
                source_id=row["operator_path_id"],
                target_type="operator_person",
                root=row["root"],
                is_primary=True,
                degree_label=row["degree_label"],
                hop_count=row["hop_count"],
                connector_handle=None,
                path_labels=" -> ".join(labels) if labels else None,
                edge_directions=directions,
                direction_data_unavailable=directions_unavailable,
                direction_data_unavailable_reason=directions_reason,
                graph_reachable=bool(row["graph_reachable"]) if row["graph_reachable"] is not None else False,
                human_intro_status=row["human_intro_status"],
                evidence_scope=None,
                source_artifact=row["source_artifact"],
            )
        )
        stats["intro_paths"] += 1
    session.commit()

    # --- Action items: upsert by source_id (action_queue.action_id) ---
    # An existing item is left alone -- owner/status/due_date/outcome_notes
    # (and the original recommendation text, in case a human edited it) all
    # survive a re-migration untouched.
    existing_actions = {a.source_id: a for a in session.query(ActionItem).filter(ActionItem.source_id.isnot(None)).all()}
    for row in bd.execute("SELECT * FROM action_queue"):
        if row["action_id"] in existing_actions:
            stats["action_items"] += 1
            continue
        session.add(
            ActionItem(
                company_id=row["company_id"],
                source_id=row["action_id"],
                execution_wave=row["execution_wave"],
                action_band=row["action_band"],
                owner=row["owner"],
                primary_next_action=row["primary_next_action"],
                fallback=row["fallback"],
                success_condition=row["success_condition"],
                human_intro_status=row["human_intro_status"],
                budget_authority_confirmed=bool(row["budget_authority_confirmed"]),
            )
        )
        stats["action_items"] += 1
    session.commit()

    # --- GTM cases ---
    # gtm_motion_json holds a single JSON string; the copy/avoid fields hold
    # JSON arrays of strings -- do not treat them the same way, or joining
    # the string field character-by-character silently mangles it.
    for row in bd.execute("SELECT * FROM gtm_cases"):
        motion = json.loads(row["gtm_motion_json"]) if row["gtm_motion_json"] else None
        copy_this = json.loads(row["what_mango_should_copy_json"] or "[]")
        not_this = json.loads(row["what_not_to_copy_json"] or "[]")
        session.add(
            GtmCase(
                company_id=row["company_id"],
                company_name=row["company"],
                category=row["category"],
                maturity_stage=row["maturity_stage"],
                gtm_motion=motion,
                spend_classification=row["spend_classification"],
                what_mango_should_copy="; ".join(copy_this) if copy_this else None,
                what_not_to_copy="; ".join(not_this) if not_this else None,
            )
        )
        stats["gtm_cases"] += 1
    session.commit()

    # --- Sponsorship evidence, with Creator entity resolution ---
    # Upsert by source_id (sponsor_observations.observation_id): an existing
    # row is left completely alone, so a Confirm/Reject review
    # (PATCH /api/sponsorship-evidence) survives a source data refresh.
    obs_by_id = {row["observation_id"]: row for row in bd.execute("SELECT * FROM sponsor_observations")}
    existing_evidence_ids = {
        s for (s,) in session.query(SponsorshipEvidence.source_id).filter(SponsorshipEvidence.source_id.isnot(None)).all()
    }
    for row in bd.execute("SELECT * FROM sponsor_edges"):
        # sponsor_edges is a rollup; re-walk the atomic observations for this
        # (company, creator) pair so each SponsorshipEvidence row stays a
        # single dated, sourced observation rather than a summary.
        matching_obs = [
            o
            for o in obs_by_id.values()
            if o["company_id"] == row["company_id"] and (o["creator_handle_or_channel"] or "") == row["creator_handle_or_channel"]
        ]
        if not matching_obs:
            continue

        new_obs = [o for o in matching_obs if o["observation_id"] not in existing_evidence_ids]
        stats["sponsorship_evidence"] += len(matching_obs) - len(new_obs)
        if not new_obs:
            continue

        platform = _norm_platform(matching_obs[0]["platform"])
        handle = (row["creator_handle_or_channel"] or "").lstrip("@")

        creator = None
        if handle:
            account = (
                session.query(SocialAccount)
                .filter(SocialAccount.platform == platform, SocialAccount.handle.ilike(handle))
                .one_or_none()
            )
            if account:
                creator = account.creator
                stats["creators_matched"] += 1

        if creator is None and handle:
            creator = Creator(display_name=row["creator"] or handle, creator_class="Unknown", creator_class_source="unset")
            session.add(creator)
            session.flush()
            session.add(SocialAccount(creator_id=creator.id, platform=platform, handle=handle))
            stats["creators_created"] += 1

        for obs in new_obs:
            session.add(
                SponsorshipEvidence(
                    company_id=row["company_id"],
                    source_id=obs["observation_id"],
                    creator_id=creator.id if creator else None,
                    creator_name_raw=obs["creator"],
                    creator_handle_raw=obs["creator_handle_or_channel"],
                    platform=platform,
                    content_url=obs["content_url"],
                    content_title=obs["content_title"],
                    published_at=obs["published_at"],
                    disclosure_type=obs["disclosure_type"],
                    evidence_text=obs["evidence_text"],
                    confidence=obs["confidence"],
                )
            )
            stats["sponsorship_evidence"] += 1
    session.commit()

    # --- Connector briefs (hand-curated, seeded verbatim) ---
    for i, brief in enumerate(CONNECTOR_BRIEFS):
        session.add(
            ConnectorBrief(
                connector_name=brief["connector_name"],
                connector_x_handle=brief["connector_x_handle"],
                company_ids=",".join(brief["company_ids"]),
                best_current_action=brief["best_current_action"],
                sort_order=i,
            )
        )
        stats["connector_briefs"] += 1
    session.commit()

    session.close()
    bd.close()
    return stats


if __name__ == "__main__":
    result = migrate()
    print("BD data migration complete:")
    for key, value in result.items():
        print(f"  {key}: {value}")
