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

Safe to re-run: companies/operators/intro paths/action items/sponsorship
evidence/gtm cases are deleted and re-inserted from source each run
(the source SQLite is the source of truth); newly-created Creators from a
previous run are matched by handle on subsequent runs, not duplicated.
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
        "best_current_action": "Ask one structured question per company: actual person, nature of relationship, last interaction, and willingness to make a contextual intro. Runway already has a verified Jennie -> Cristobal path.",
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


def migrate() -> dict[str, int]:
    if not BD_SQLITE_PATH.exists():
        raise SystemExit(f"Source BD SQLite not found at {BD_SQLITE_PATH}")

    init_db()
    bd = sqlite3.connect(str(BD_SQLITE_PATH))
    bd.row_factory = sqlite3.Row
    session = get_session()

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

    # Clear and re-import the BD tables (source SQLite is authoritative);
    # Creator/SocialAccount rows are never deleted here, only matched or
    # created, so the quote library is untouched.
    for model in (SponsorshipEvidence, ActionItem, IntroPath, Operator, GtmCase, CompanySource, CompanyAlias, ConnectorBrief, Company):
        session.query(model).delete()
    session.commit()

    # --- Companies ---
    for row in bd.execute("SELECT * FROM companies"):
        raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        company = Company(
            company_id=row["company_id"],
            name=row["company"],
            category=row["category"] or None,
            geography=row["geography"] or None,
            stage=row["cohort"] or None,
            score_value=row["score_value"],
            score_comparison_group=row["score_comparison_group"],
            priority_tier=row["priority_tier"],
            spend_evidence_level=row["spend_evidence_level"],
            spend_mechanism_level=row["spend_mechanism_level"],
            why_now=row["why_now"] or None,
            budget_evidence=row["budget_evidence"] or None,
            buyer_or_route=row["buyer_or_route"] or raw.get("recommended_next_action"),
            x_handle=(raw.get("x_handle_candidate") or "").lstrip("@") or None,
            internal_notes=raw.get("risk"),
            raw_json=row["raw_json"],
        )
        session.add(company)
        stats["companies"] += 1
    session.commit()

    for row in bd.execute("SELECT * FROM company_aliases"):
        session.add(CompanyAlias(company_id=row["company_id"], alias=row["alias"]))
        stats["aliases"] += 1
    for row in bd.execute("SELECT * FROM company_sources"):
        session.add(CompanySource(company_id=row["company_id"], source_url=row["source_url"]))
        stats["sources"] += 1
    session.commit()

    # --- Operators ---
    for row in bd.execute("SELECT * FROM operator_identities WHERE operator_name IS NOT NULL"):
        urls = json.loads(row["official_evidence_urls_json"] or "[]")
        session.add(
            Operator(
                company_id=row["company_id"],
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
    for row in bd.execute("SELECT * FROM x_paths"):
        labels = json.loads(row["path_labels_json"] or "[]")
        session.add(
            IntroPath(
                company_id=row["company_id"],
                target_type="company_account",
                root=row["root"],
                is_primary=bool(row["is_primary"]),
                degree_label=row["degree_label"],
                hop_count=row["hop_count"],
                connector_handle=row["connector_handle"],
                path_labels=" -> ".join(labels) if labels else None,
                graph_reachable=bool(row["graph_reachable"]),
                human_intro_status=row["human_intro_status"],
                evidence_scope=row["evidence_scope"],
                source_artifact=row["source_artifact"],
            )
        )
        stats["intro_paths"] += 1

    for row in bd.execute("SELECT * FROM operator_person_paths"):
        labels = json.loads(row["primary_path_labels_json"] or "[]")
        session.add(
            IntroPath(
                company_id=row["company_id"],
                target_type="operator_person",
                root=row["root"],
                is_primary=True,
                degree_label=row["degree_label"],
                hop_count=row["hop_count"],
                connector_handle=None,
                path_labels=" -> ".join(labels) if labels else None,
                graph_reachable=bool(row["graph_reachable"]) if row["graph_reachable"] is not None else False,
                human_intro_status=row["human_intro_status"],
                evidence_scope=None,
                source_artifact=row["source_artifact"],
            )
        )
        stats["intro_paths"] += 1
    session.commit()

    # --- Action items ---
    for row in bd.execute("SELECT * FROM action_queue"):
        session.add(
            ActionItem(
                company_id=row["company_id"],
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
    obs_by_id = {row["observation_id"]: row for row in bd.execute("SELECT * FROM sponsor_observations")}
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

        for obs in matching_obs:
            session.add(
                SponsorshipEvidence(
                    company_id=row["company_id"],
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
