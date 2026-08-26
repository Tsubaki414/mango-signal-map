from __future__ import annotations

from .bd_compute import best_intro_path, opportunity_priority, relationship_strength
from .compute import quote_summary
from .models import Company, ConnectorBrief, IntroPath, Operator, SponsorshipEvidence


def _path_json(p: IntroPath | None) -> dict | None:
    if p is None:
        return None
    return {
        "target_type": p.target_type,
        "root": p.root,
        "degree_label": p.degree_label,
        "hop_count": p.hop_count,
        "connector_handle": p.connector_handle,
        "path_labels": p.path_labels,
        "graph_reachable": p.graph_reachable,
        "human_intro_status": p.human_intro_status,
        "is_primary": p.is_primary,
    }


def _operator_json(o: Operator) -> dict:
    return {
        "id": o.id,
        "name": o.name,
        "role": o.role,
        "x_handle": o.x_handle,
        "identity_confirmed": o.identity_confirmed,
        "identity_status": o.identity_status,
        "budget_authority_confirmed": o.budget_authority_confirmed,
    }


def company_summary(company: Company) -> dict:
    priority = opportunity_priority(company)
    reach = priority["reachability"]
    action = max(company.action_items, key=lambda a: -(a.execution_wave or 99), default=None)
    paid_count = sum(1 for s in company.sponsorships if s.disclosure_type == "paid_sponsorship")
    return {
        "company_id": company.company_id,
        "name": company.name,
        "category": company.category,
        "geography": company.geography,
        "segment": company.stage,
        "priority_tier": company.priority_tier,
        "spend_evidence_level": company.spend_evidence_level,
        "priority": priority["label"],
        "priority_reasons": priority["reasons"],
        "reachability_level": reach["level"],
        "reachability_label": reach["label"],
        "confirmed_operator": _operator_json(reach["confirmed_operator"]) if reach["confirmed_operator"] else None,
        "sponsorship_count": len(company.sponsorships),
        "paid_sponsorship_count": paid_count,
        "action_wave": action.execution_wave if action else None,
        "next_action": action.primary_next_action if action else None,
        "x_handle": company.x_handle,
    }


def company_detail(company: Company) -> dict:
    base = company_summary(company)
    company_path = best_intro_path(company.intro_paths, "company_account")
    person_path = best_intro_path(company.intro_paths, "operator_person")
    base.update(
        {
            "why_now": company.why_now,
            "budget_evidence": company.budget_evidence,
            "buyer_or_route": company.buyer_or_route,
            "internal_notes": company.internal_notes,
            "aliases": [a.alias for a in company.aliases],
            "sources": [s.source_url for s in company.sources],
            "operators": [_operator_json(o) for o in company.operators],
            "best_company_path": _path_json(company_path),
            "best_person_path": _path_json(person_path),
            "all_intro_paths": [_path_json(p) for p in sorted(company.intro_paths, key=lambda p: (p.target_type, 0 if p.graph_reachable else 1))[:20]],
            "action_items": [
                {
                    "id": a.id,
                    "execution_wave": a.execution_wave,
                    "action_band": a.action_band,
                    "owner": a.owner,
                    "primary_next_action": a.primary_next_action,
                    "fallback": a.fallback,
                    "success_condition": a.success_condition,
                    "human_intro_status": a.human_intro_status,
                }
                for a in company.action_items
            ],
            "sponsorships": [sponsorship_json(s) for s in company.sponsorships],
            "gtm_case": (
                {
                    "gtm_motion": company.gtm_case.gtm_motion,
                    "spend_classification": company.gtm_case.spend_classification,
                    "what_mango_should_copy": company.gtm_case.what_mango_should_copy,
                    "what_not_to_copy": company.gtm_case.what_not_to_copy,
                }
                if company.gtm_case
                else None
            ),
        }
    )
    return base


def sponsorship_json(s: SponsorshipEvidence) -> dict:
    creator_summary = None
    if s.creator:
        qs = quote_summary(s.creator)
        creator_summary = {
            "id": s.creator.id,
            "display_name": s.creator.display_name,
            "creator_class": s.creator.creator_class,
            "has_quote": qs["confident_count"] > 0,
        }
    return {
        "id": s.id,
        "company_id": s.company_id,
        "company_name": s.company.name if s.company else None,
        "creator_id": s.creator_id,
        "creator_name": s.creator_name_raw,
        "creator_handle": s.creator_handle_raw,
        "creator": creator_summary,
        "platform": s.platform,
        "content_url": s.content_url,
        "content_title": s.content_title,
        "published_at": s.published_at,
        "disclosure_type": s.disclosure_type,
        "evidence_text": s.evidence_text,
        "confidence": s.confidence,
    }


def connector_brief_json(brief: ConnectorBrief, companies_by_id: dict[str, Company]) -> dict:
    ids = [c.strip() for c in brief.company_ids.split(",") if c.strip()]
    companies = [companies_by_id[cid] for cid in ids if cid in companies_by_id]
    return {
        "id": brief.id,
        "connector_name": brief.connector_name,
        "connector_x_handle": brief.connector_x_handle,
        "best_current_action": brief.best_current_action,
        "companies": [{"company_id": c.company_id, "name": c.name, "priority_tier": c.priority_tier} for c in companies],
    }
