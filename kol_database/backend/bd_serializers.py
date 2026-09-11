from __future__ import annotations

import datetime as dt
import json
import re

from .bd_compute import (
    best_intro_path,
    canonical_decision,
    channel_access,
    execution_priority,
    intro_readiness,
    is_material_intro_path,
    operator_relevance,
    opportunity_priority,
    relationship_evidence,
    relationship_stage,
    relationship_strength,
    sponsorship_evidence_semantics,
    suggested_bridge_action,
)
from .compute import quote_summary
from .models import ActionItem, Company, ConnectorBrief, IntroPath, Operator, OutreachLog, SponsorshipEvidence
from .release_schedule import due_date_source, planned_work_type


_TERMINAL_ACTION_STATUSES = frozenset({"done", "blocked"})
_TERMINAL_OUTREACH_STAGES = frozenset({"relationship_rejected", "rejected"})
_ACTIVE_OUTREACH_CONTINUATION_STAGES = frozenset(
    {
        "contact_attempted",
        "connector_replied",
        "relationship_confirmed",
        "intro_accepted",
        "intro_made",
        "target_replied",
        "meeting_booked",
        "proposal_requested",
        "no_response",
    }
)


def action_workflow_status(action: ActionItem | None) -> str | None:
    """Persisted ActionItem workflow state, never a priority label.

    Historical rows created before the status column was introduced can be
    NULL.  They behaved as open work at import time, so retain that explicit
    compatibility default while keeping a company with no ActionItem as
    ``None``.
    """

    if action is None:
        return None
    return (action.status or "open").strip().casefold() or "open"


def _valid_action_due_date(action: ActionItem) -> dt.date | None:
    try:
        return dt.date.fromisoformat((action.due_date or "").strip())
    except (TypeError, ValueError):
        return None


def _newest_record_key(action: ActionItem) -> tuple:
    created = action.created_at
    components = (
        created.year,
        created.month,
        created.day,
        created.hour,
        created.minute,
        created.second,
        created.microsecond,
    ) if created else (0, 0, 0, 0, 0, 0, 0)
    return (bool(created), components, action.id or 0)


def current_action_item(company: Company) -> ActionItem | None:
    """Choose the one persisted ActionItem represented by summary fields.

    Active work always wins over completed/blocked history.  Within active
    work, the earliest valid due date wins, followed by in-progress over open
    and then the lower execution wave; newest creation/id only breaks a
    semantic tie.  If every item is terminal, the newest terminal record is
    returned so its final workflow state remains visible without resurrecting
    an older completed task as current work.
    """

    actions = list(company.action_items or [])
    if not actions:
        return None
    active = [action for action in actions if action_workflow_status(action) not in _TERMINAL_ACTION_STATUSES]
    if not active:
        return max(actions, key=_newest_record_key)

    def active_key(action: ActionItem) -> tuple:
        due = _valid_action_due_date(action)
        newest = _newest_record_key(action)
        return (
            0 if due else 1,
            due or dt.date.max,
            0 if action_workflow_status(action) == "in_progress" else 1,
            action.execution_wave if action.execution_wave is not None else 999,
            0 if newest[0] else 1,
            tuple(-part for part in newest[1]),
            -newest[2],
        )

    return min(active, key=active_key)


def latest_nonvoid_outreach(company: Company) -> OutreachLog | None:
    return max(
        (log for log in (company.outreach_logs or []) if not log.voided),
        key=lambda log: (log.occurred_at or dt.datetime.min, log.id or 0),
        default=None,
    )


def has_active_outreach_continuation(company: Company) -> bool:
    """Whether persisted outreach history has a real unfinished next leg.

    This is intentionally based only on the latest non-void OutreachLog,
    never on ``execution_priority``.  Rejected/relationship-rejected rows are
    terminal for this predicate; a new non-void event is required to reopen
    the operational queue.
    """

    latest = latest_nonvoid_outreach(company)
    return bool(latest and latest.stage in _ACTIVE_OUTREACH_CONTINUATION_STAGES)


def _comparable_record_time(value: dt.datetime | None) -> dt.datetime | None:
    """Normalize persisted timestamps before comparing source chronology.

    SQLite records are normally naive UTC datetimes, while tests/imports can
    provide timezone-aware values.  Comparing the two directly raises, so use
    naive UTC solely for source-precedence decisions.
    """

    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


def terminal_outreach_supersedes_action(
    outreach: OutreachLog | None,
    action: ActionItem | None,
) -> bool:
    """Whether a newer terminal execution fact retires an active action.

    A rejected relationship/path or target outcome is an attributed human
    fact.  If it happened after the currently selected ActionItem was created,
    the old action must not reappear as current work merely because its status
    was never manually closed.  An older terminal event does not override a
    newer action that may have been deliberately created in response to it.
    """

    if (
        outreach is None
        or action is None
        or outreach.stage not in _TERMINAL_OUTREACH_STAGES
    ):
        return False
    outreach_time = _comparable_record_time(outreach.occurred_at or outreach.created_at)
    action_time = _comparable_record_time(action.created_at)
    return bool(outreach_time and action_time and outreach_time > action_time)


def _split_lines(value: str | None) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _company_evidence_contract(company: Company) -> dict:
    try:
        raw = json.loads(company.raw_json or "{}")
    except (json.JSONDecodeError, TypeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    contract_status = raw.get("evidence_contract_status")
    historical = bool(
        raw.get("cohort_group") == "existing_v3"
        or "legacy" in str(contract_status or "").casefold()
        or "revalidation" in str(contract_status or "").casefold()
    )
    evidence_records = raw.get("evidence_records") or []
    if not isinstance(evidence_records, list):
        evidence_records = []
    public_records = []
    for item in evidence_records:
        if not isinstance(item, dict):
            continue
        public_records.append(
            {
                "evidence_id": item.get("evidence_id"),
                "claim": item.get("claim"),
                "url": item.get("url"),
                "date": item.get("date"),
                "date_basis": item.get("date_basis"),
                "last_verified_at": item.get("last_verified_at"),
                "evidence_type": item.get("evidence_type"),
                "confidence": item.get("confidence"),
                "fact_status": item.get("fact_status"),
            }
        )
    record_last_verified_at = company.last_verified_at.isoformat() if company.last_verified_at else None
    return {
        # Company.last_verified_at is a record/source freshness timestamp in
        # the imported dataset. It is not proof that a Mango human reviewed
        # the company, operator identity, role or budget evidence.
        "record_last_verified_at": record_last_verified_at,
        "source_snapshot_last_verified_at": raw.get("last_verified_at"),
        "freshness_status": (
            "historical_reference_revalidation_needed"
            if historical
            else "record_timestamp_present"
            if record_last_verified_at
            else "source_snapshot_only_revalidation_needed"
        ),
        "human_review_status": "not_recorded",
        "revalidation_needed": historical or not bool(record_last_verified_at),
        "historical_reference": historical,
        "evidence_contract_status": contract_status,
        "record_confidence": raw.get("confidence"),
        "fact_status": raw.get("fact_status"),
        "source_urls": raw.get("source_urls") if isinstance(raw.get("source_urls"), list) else [],
        "evidence_records": public_records,
    }


_SYMBOLIC_DIRECTIONS = {"follows", "followed_by", "mutual_follow"}
_EXPLICIT_DIRECTION_RE = re.compile(r"^\s*@?([A-Za-z0-9_]+)\s*->\s*@?([A-Za-z0-9_]+)\s*$")


def _direction_values(p: IntroPath) -> tuple[list[str], str | None]:
    """Return validated raw direction values plus any parse error.

    A malformed human-edited payload must not break the entire company
    endpoint.  It is exposed as unavailable with a precise reason instead.
    """

    if not p.edge_directions:
        return [], None
    try:
        values = json.loads(p.edge_directions)
    except (json.JSONDecodeError, TypeError):
        return [], "存储的逐边 follow 方向不是有效 JSON，需人工修复；未从节点顺序推断。"
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        return [], "存储的逐边 follow 方向不是字符串数组，需人工修复；未从节点顺序推断。"
    return values, None


def _label_handle(label: str) -> str | None:
    matches = re.findall(r"@([A-Za-z0-9_]+)", label or "")
    return matches[-1].casefold() if matches else None


def _direction_steps(path_labels: list[str], directions: list[str]) -> list[dict]:
    """Normalize both v4 direction encodings into adjacent path hops."""

    if len(path_labels) < 2:
        return []

    # Solomon-root paths use exactly one symbolic relationship per hop.
    symbolic_per_hop = len(directions) == len(path_labels) - 1 and all(
        value in _SYMBOLIC_DIRECTIONS for value in directions
    )

    # Mango-root paths preserve observed atomic follow edges.  Mutual
    # follows therefore appear as two explicit strings for one logical hop.
    explicit_edges: set[tuple[str, str]] = set()
    for value in directions:
        match = _EXPLICIT_DIRECTION_RE.match(value)
        if match:
            explicit_edges.add((match.group(1).casefold(), match.group(2).casefold()))

    steps = []
    for index, (left, right) in enumerate(zip(path_labels, path_labels[1:])):
        relationship = "unknown"
        from_follows_to: bool | None = None
        to_follows_from: bool | None = None
        evidence: list[str] = []

        if symbolic_per_hop:
            relationship = directions[index]
            evidence = [directions[index]]
            if relationship == "mutual_follow":
                from_follows_to = True
                to_follows_from = True
            elif relationship == "follows":
                from_follows_to = True
                to_follows_from = False
            elif relationship == "followed_by":
                from_follows_to = False
                to_follows_from = True
        else:
            left_handle = _label_handle(left)
            right_handle = _label_handle(right)
            if left_handle and right_handle:
                forward = (left_handle, right_handle) in explicit_edges
                reverse = (right_handle, left_handle) in explicit_edges
                from_follows_to = forward if forward or reverse else None
                to_follows_from = reverse if forward or reverse else None
                if forward and reverse:
                    relationship = "mutual_follow"
                elif forward:
                    relationship = "follows"
                elif reverse:
                    relationship = "followed_by"
                evidence = [
                    value
                    for value in directions
                    if (match := _EXPLICIT_DIRECTION_RE.match(value))
                    and {
                        match.group(1).casefold(),
                        match.group(2).casefold(),
                    }
                    == {left_handle, right_handle}
                ]

        steps.append(
            {
                "hop": index + 1,
                "from_label": left,
                "to_label": right,
                "relationship": relationship,
                "from_follows_to": from_follows_to,
                "to_follows_from": to_follows_from,
                "source_values": evidence,
            }
        )
    return steps


def _path_json(p: IntroPath | None) -> dict | None:
    if p is None:
        return None
    labels = [label.strip() for label in (p.path_labels or "").split(" -> ") if label.strip()]
    directions, parse_error = _direction_values(p)
    steps = _direction_steps(labels, directions)
    all_hops_resolved = bool(steps) and all(step["relationship"] != "unknown" for step in steps)
    unavailable = bool(p.direction_data_unavailable) or bool(parse_error) or not directions or not all_hops_resolved
    unavailable_reason = parse_error or p.direction_data_unavailable_reason
    if directions and steps and not all_hops_resolved and not unavailable_reason:
        unavailable_reason = "方向证据未能与每个相邻路径节点完整匹配；未匹配跳保持未知。"
    if unavailable and not unavailable_reason:
        unavailable_reason = "尚未录入逐边 follow 方向；不得从路径节点顺序推断。"
    return {
        "id": p.id,
        "target_type": p.target_type,
        "root": p.root,
        "degree_label": p.degree_label,
        "hop_count": p.hop_count,
        "connector_handle": p.connector_handle,
        "path_labels": p.path_labels,
        "edge_directions": directions,
        "direction_steps": steps,
        "direction_data_available": not unavailable,
        "direction_data_unavailable": unavailable,
        "direction_data_unavailable_reason": unavailable_reason,
        "graph_reachable": p.graph_reachable,
        "human_intro_status": p.human_intro_status,
        "is_primary": p.is_primary,
    }


def _interaction_evidence_json(e) -> dict:
    return {
        "id": e.id,
        "tweet_id": e.tweet_id,
        "tweet_url": e.tweet_url,
        "posted_at": e.posted_at.isoformat() if e.posted_at else None,
        "author_handle": e.author_handle,
        "recipient_handle": e.recipient_handle,
        "context_text": e.context_text,
        "interaction_type": e.interaction_type,
        "is_auditable": e.is_auditable,
    }


def _interaction_check_json(c) -> dict:
    return {
        "id": c.id,
        "from_handle": c.from_handle,
        "to_handle": c.to_handle,
        "query_string": c.query_string,
        "checked_at": c.checked_at.isoformat() if c.checked_at else None,
        "coverage_note": c.coverage_note,
        "evidence": [_interaction_evidence_json(e) for e in c.evidence],
    }


def _bridge_json(b) -> dict:
    return {
        "id": b.id,
        "bridge_handle": b.bridge_handle,
        "bridge_name": b.bridge_name,
        "bridge_bio": b.bridge_bio,
        "bridge_followers_count": b.bridge_followers_count,
        "mango_side_handle": b.mango_side_handle,
        "confidence_note": b.confidence_note,
        "discovered_at": b.discovered_at.isoformat() if b.discovered_at else None,
        # Deprecated flat summary from the first (2026-08-28) interaction
        # check batch -- kept for that batch, superseded by
        # interaction_checks below for anything auditable per-tweet.
        "interaction_checked_at": b.interaction_checked_at.isoformat() if b.interaction_checked_at else None,
        "interaction_count_recent": b.interaction_count_recent,
        "most_recent_interaction_at": b.most_recent_interaction_at.isoformat() if b.most_recent_interaction_at else None,
        "interaction_sample_text": b.interaction_sample_text,
        "interaction_checks": [_interaction_check_json(c) for c in b.interaction_checks],
    }


def _outreach_log_json(log) -> dict:
    return {
        "id": log.id,
        "company_id": log.company_id,
        "operator_id": log.operator_id,
        "bridge_id": log.bridge_id,
        "intro_path_id": log.intro_path_id,
        "linked_action_item_id": log.linked_action_item_id,
        "owner": log.owner,
        "contacted_who": log.contacted_who,
        "contact_channel": log.contact_channel,
        "evidence_url": log.evidence_url,
        "stage": log.stage,
        "notes": log.notes,
        "occurred_at": log.occurred_at.isoformat() if log.occurred_at else None,
        "next_follow_up_date": log.next_follow_up_date,
        "voided": log.voided,
        "voided_reason": log.voided_reason,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _operator_json(o: Operator) -> dict:
    identity_status = o.identity_status or ""
    dm_status = "unknown"
    if o.can_dm_checked_at:
        dm_status = "verified_open" if o.can_dm else "checked_not_open"
    relationship_verified = any(
        not log.voided
        and log.operator_id == o.id
        and log.stage in {"relationship_confirmed", "intro_accepted", "intro_made", "target_replied"}
        for log in (getattr(getattr(o, "company", None), "outreach_logs", None) or [])
    )
    relevance = operator_relevance(o)
    return {
        "id": o.id,
        "name": o.name,
        "role": o.role,
        "x_handle": o.x_handle,
        "identity_confirmed": o.identity_confirmed,
        "identity_status": o.identity_status,
        # Independent contact-verification facts for the UI. The imported
        # legacy field above is source matching, not a human-review flag.
        "identity_source_matched": bool(o.identity_confirmed),
        "identity_human_verified": bool(o.identity_human_verified_at),
        "human_identity_verified": bool(o.identity_human_verified_at),
        "identity_human_verified_at": o.identity_human_verified_at.isoformat() if o.identity_human_verified_at else None,
        "identity_confirmed_metadata": {
            "deprecated": True,
            "semantic": "official_role_and_rapid_x_exact_profile_source_match",
            "does_not_mean": "mango_human_identity_verification",
            "replacement_field": "identity_source_matched",
        },
        "role_human_verified": bool(o.role_human_verified_at),
        "role_human_verified_at": o.role_human_verified_at.isoformat() if o.role_human_verified_at else None,
        "x_account_verified": "rapid_x_exact_profile" in identity_status,
        "dm_status": dm_status,
        "public_contact_status": "unknown",
        "budget_authority_confirmed": o.budget_authority_confirmed,
        "budget_authority_verified_at": o.budget_authority_verified_at.isoformat() if o.budget_authority_verified_at else None,
        "budget_influence_status": "verified" if o.budget_authority_confirmed and o.budget_authority_verified_at else "not_verified",
        "operator_relevance": relevance,
        "mango_relationship_verified": relationship_verified,
        "evidence_urls": _split_lines(o.evidence_urls),
        "last_verified_at": o.last_verified_at.isoformat() if o.last_verified_at else None,
        "can_dm": o.can_dm,
        "can_dm_checked_at": o.can_dm_checked_at.isoformat() if o.can_dm_checked_at else None,
        "bridges": [_bridge_json(b) for b in o.bridges],
    }


def _non_rejected(company: Company) -> list:
    # Rejected sponsorship evidence is kept (audit trail, visible in the
    # full sponsorships list) but must not count toward priority/coverage
    # numbers -- a human has already determined it's not real evidence.
    return [s for s in company.sponsorships if s.review_status != "rejected"]


def _safe_json_list(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _commercial_route_json(route) -> dict:
    return {
        "id": route.id,
        "route_key": route.route_key,
        "route_type": route.route_type,
        "label": route.label,
        "route_detail": route.route_detail,
        "why_this_route": route.why_this_route,
        "required_first_step": route.required_first_step,
        "supporting_evidence": route.supporting_evidence,
        "evidence_url": route.evidence_url,
        "confidence": route.confidence,
        "is_best": route.is_best,
        "fallback_order": route.fallback_order,
        "last_verified_at": route.last_verified_at.isoformat() if route.last_verified_at else None,
    }


def _commercial_evidence_json(evidence) -> dict:
    return {
        "evidence_id": evidence.evidence_id,
        "claim": evidence.claim,
        "url": evidence.source_url,
        "date": evidence.evidence_date,
        "date_basis": evidence.date_basis,
        "evidence_type": evidence.evidence_type,
        "confidence": evidence.confidence,
        "fact_status": evidence.fact_status,
        "last_verified_at": evidence.last_verified_at.isoformat() if evidence.last_verified_at else None,
    }


def asia_intelligence_json(company: Company) -> dict:
    profile = company.asia_profile
    if not profile:
        return {
            "status": "not_reviewed",
            "status_label": "尚未审查",
            "reviewed": False,
            "evidence": [],
        }
    return {
        "status": profile.asia_interest_status,
        "status_label": canonical_decision(company)["asia_status_label"],
        "reviewed": True,
        "market_context": profile.market_context,
        "target_markets": _safe_json_list(profile.asia_target_markets_json),
        "signal_types": _safe_json_list(profile.asia_signal_types_json),
        "signal_summary": profile.asia_signal_summary,
        "confidence": profile.confidence,
        "asia_operator": profile.asia_operator,
        "localization_status": profile.localization_status,
        "local_partner_or_customer": profile.local_partner_or_customer,
        "regional_creator_activity": profile.regional_creator_activity,
        "mango_asia_fit": profile.mango_asia_fit,
        "recommended_market_entry_angle": profile.recommended_market_entry_angle,
        "review_scope": profile.review_scope,
        "last_verified_at": profile.last_verified_at.isoformat() if profile.last_verified_at else None,
        "evidence": [
            {
                "evidence_id": evidence.evidence_id,
                "target_markets": _safe_json_list(evidence.target_markets_json),
                "signal_type": evidence.signal_type,
                "summary": evidence.summary,
                "source_url": evidence.source_url,
                "evidence_date": evidence.evidence_date,
                "collected_at": evidence.collected_at.isoformat() if evidence.collected_at else None,
                "confidence": evidence.confidence,
                "fact_status": evidence.fact_status,
                "last_verified_at": evidence.last_verified_at.isoformat() if evidence.last_verified_at else None,
            }
            for evidence in sorted(company.asia_evidence, key=lambda row: row.evidence_id)
        ],
    }


def commercial_dossier_json(company: Company) -> dict | None:
    dossier = company.research_dossier
    if not dossier:
        return None
    routes = sorted(
        company.contact_routes,
        key=lambda route: (0 if route.is_best else 1, route.fallback_order or 99, route.id),
    )
    return {
        "priority_rank": dossier.priority_rank,
        "commercial_priority_score": dossier.commercial_priority_score,
        "icp_type": dossier.icp_type,
        "business_model": dossier.business_model,
        "target_customer": dossier.target_customer,
        "why_company": dossier.why_company,
        "why_now": dossier.why_now,
        "commercialization_evidence": dossier.commercialization_evidence,
        "budget_spend_signals": dossier.budget_spend_signals,
        "gtm_summary": dossier.gtm_summary,
        "marketing_channels": _safe_json_list(dossier.marketing_channels_json),
        "historical_campaigns": _safe_json_list(dossier.historical_campaigns_json),
        "creators_media_communities": _safe_json_list(dossier.creators_media_communities_json),
        "repeated_activity": dossier.repeated_activity,
        "operator": _operator_json(dossier.operator) if dossier.operator else None,
        "operator_role_relevance": dossier.operator_role_relevance,
        "public_contact_method": dossier.public_contact_method,
        "mango_service_fit": dossier.mango_service_fit,
        "mango_offer": dossier.mango_offer,
        "opening_angle": dossier.opening_angle,
        "confidence": dossier.confidence,
        "fact_status": dossier.fact_status,
        "key_unknowns": _safe_json_list(dossier.key_unknowns_json),
        "routes": [_commercial_route_json(route) for route in routes],
        "evidence": [
            _commercial_evidence_json(evidence)
            for evidence in sorted(company.commercial_evidence, key=lambda evidence: evidence.evidence_id)
        ],
        "last_verified_at": dossier.last_verified_at.isoformat() if dossier.last_verified_at else None,
    }


def company_summary(company: Company) -> dict:
    # Section V (2026-08-28): three completely independent judgments, never
    # collapsed into one number. `priority` = pure business value
    # ("商业优先级" in the UI). `stage`/`reachability_level` = relationship
    # warmth (E0-E6), which does NOT feed the priority label. `exec_p` =
    # what's actually worth doing today, which is the only one of the
    # three the home page's "当下最值得联系" list should sort on.
    priority = opportunity_priority(company)
    stage = relationship_stage(company)
    exec_p = execution_priority(company)
    confirmed_operator = priority["reachability"]["confirmed_operator"]
    identified_operator = priority["reachability"]["identified_operator"]
    best_operator = priority.get("best_operator")
    decision = canonical_decision(company)
    # Current work is one of three mutually exclusive sources.  Never splice
    # computed copy together with owner/due/status from a different historical
    # ActionItem: that made a dynamic cold-DM suggestion look like Solomon had
    # already owned and scheduled it.  Raw ActionItem fields remain exposed
    # below for history/audit, independently of these current-work fields.
    suggested = suggested_bridge_action(company)
    action = current_action_item(company)
    active_action = (
        action
        if action and action_workflow_status(action) not in _TERMINAL_ACTION_STATUSES
        else None
    )
    latest_outreach = latest_nonvoid_outreach(company)
    active_outreach = latest_outreach if has_active_outreach_continuation(company) else None
    terminal_outreach = (
        latest_outreach
        if terminal_outreach_supersedes_action(latest_outreach, active_action)
        else None
    )
    live_sponsorships = _non_rejected(company)
    paid = [s for s in live_sponsorships if s.disclosure_type == "paid_sponsorship"]
    paid_count = len(paid)
    release_internal_check = bool(
        active_action
        and active_outreach is None
        and terminal_outreach is None
        and due_date_source(company.company_id, active_action.due_date) == "release_planning_default"
        and planned_work_type(company.company_id) == "internal_relationship_check"
    )
    computed_fallback = next(
        (step["detail"] for step in (suggested or {}).get("steps", []) if step.get("kind") == "fallback"),
        None,
    )
    suggested_terminal_fallback = computed_fallback or next(
        (step.get("detail") for step in reversed((suggested or {}).get("steps", [])) if step.get("detail")),
        None,
    )
    if active_outreach:
        current_work_kind = "outreach_follow_up"
        current_workflow_status = "follow_up_scheduled"
        current_next_action = suggested["text"] if suggested else None
        current_owner = active_outreach.owner
        current_due_date = active_outreach.next_follow_up_date
        current_fallback = computed_fallback
        current_work_created_at = (
            active_outreach.created_at.isoformat()
            if active_outreach.created_at
            else active_outreach.occurred_at.isoformat() if active_outreach.occurred_at else None
        )
        next_action_source = "live_outreach_continuation"
        current_source_type = "outreach_log"
        current_source_id = active_outreach.id
        current_source_occurred_at = (
            active_outreach.occurred_at.isoformat() if active_outreach.occurred_at else None
        )
    elif terminal_outreach:
        # A newer attributed terminal fact retires the older still-open action
        # as current work without destroying either audit record.  The raw
        # ActionItem remains exposed below, while the decision summary uses the
        # terminal outcome and the newly-computed stop/switch-route guidance.
        current_work_kind = "outreach_terminal_outcome"
        current_workflow_status = terminal_outreach.stage
        current_next_action = suggested["text"] if suggested else None
        current_owner = terminal_outreach.owner
        current_due_date = None
        current_fallback = suggested_terminal_fallback
        current_work_created_at = (
            terminal_outreach.occurred_at.isoformat()
            if terminal_outreach.occurred_at
            else terminal_outreach.created_at.isoformat() if terminal_outreach.created_at else None
        )
        next_action_source = "newer_terminal_outreach"
        current_source_type = "outreach_log"
        current_source_id = terminal_outreach.id
        current_source_occurred_at = (
            terminal_outreach.occurred_at.isoformat() if terminal_outreach.occurred_at else None
        )
    elif active_action:
        current_work_kind = "release_internal_check" if release_internal_check else "action_item"
        current_workflow_status = action_workflow_status(active_action)
        current_next_action = active_action.primary_next_action
        current_owner = active_action.owner
        current_due_date = active_action.due_date
        current_fallback = active_action.fallback
        current_work_created_at = active_action.created_at.isoformat() if active_action.created_at else None
        next_action_source = "scheduled_internal_verification" if release_internal_check else "stored_action_item"
        current_source_type = "action_item"
        current_source_id = active_action.id
        current_source_occurred_at = None
    else:
        current_work_kind = "unscheduled_research_suggestion"
        current_workflow_status = None
        current_next_action = suggested["text"] if suggested else None
        current_owner = None
        current_due_date = None
        current_fallback = computed_fallback
        current_work_created_at = None
        next_action_source = "unscheduled_research_suggestion"
        current_source_type = "computed_research_suggestion"
        current_source_id = None
        current_source_occurred_at = None

    has_scheduled_action = bool(active_outreach or (active_action and terminal_outreach is None))
    current_due_date_source = (
        "outreach_follow_up"
        if active_outreach and current_due_date
        else due_date_source(company.company_id, current_due_date)
        if active_action and terminal_outreach is None
        else None
    )
    current_planned_work_type = (
        "outreach_follow_up"
        if active_outreach
        else "terminal_outreach_outcome"
        if terminal_outreach
        else "internal_relationship_check"
        if release_internal_check
        else "planned_work_item"
        if active_action
        else "unscheduled_research_suggestion"
    )
    return {
        "company_id": company.company_id,
        "name": company.name,
        # category/geography display prefers the Chinese translation;
        # company.category itself stays English on the ORM object because
        # bd_compute.creator_campaign_fit() matches it against Creator.categories.
        "category": company.category_zh or company.category,
        "geography": company.geography_zh or company.geography,
        "segment": company.stage,
        "priority_tier": company.priority_tier,
        # This is the only product-facing conclusion. Legacy priority fields
        # remain below for archive/API compatibility, never for primary UI.
        "canonical_decision": decision,
        "asia_intelligence": asia_intelligence_json(company),
        "sales_packet_available": bool(company.sales_packet),
        "spend_evidence_level": company.spend_evidence_level,
        "priority": priority["label"],
        "priority_reasons": priority["reasons"],
        "priority_gates": priority.get("gates"),
        "route_type": priority.get("route_type"),
        "channel_access": channel_access(company),
        "relationship_evidence": relationship_evidence(company),
        "intro_readiness": intro_readiness(company),
        "operator_relevance": priority.get("operator_relevance"),
        "best_relevant_operator": _operator_json(best_operator) if best_operator else None,
        # Relationship Stage (E0-E6) -- independent of `priority` above.
        "reachability_level": stage["level"],
        "reachability_label": stage["label"],
        "relationship_stage_code": stage["code"],
        "execution_priority": exec_p["label"],
        "execution_priority_score": exec_p["score"],
        "execution_priority_reasons": exec_p["reasons"],
        "confirmed_operator": _operator_json(confirmed_operator) if confirmed_operator else None,
        "confirmed_operator_metadata": {
            "deprecated": True,
            "semantic": "best_operator_with_legacy_identity_source_match",
            "does_not_mean": "mango_human_identity_verification",
            "replacement_field": "best_relevant_operator",
        },
        "identified_operator": _operator_json(identified_operator) if identified_operator else None,
        "sponsorship_count": len(live_sponsorships),
        "paid_sponsorship_count": paid_count,
        "paid_sponsorship_confirmed_count": sum(1 for s in paid if s.review_status == "confirmed"),
        "paid_sponsorship_unreviewed_count": sum(1 for s in paid if s.review_status == "unreviewed"),
        "action_id": action.id if action else None,
        "action_status": action_workflow_status(action),
        "action_fallback": action.fallback if action else None,
        "action_wave": action.execution_wave if action else None,
        "action_created_at": action.created_at.isoformat() if action and action.created_at else None,
        "has_active_outreach_continuation": bool(active_outreach),
        # Computed research guidance is useful, but it is not an owned,
        # scheduled ActionItem. Consumers must keep those states distinct.
        "has_scheduled_action": has_scheduled_action,
        "action_record_status": (
            "outreach_follow_up"
            if active_outreach
            else "terminal_outreach_outcome"
            if terminal_outreach
            else "scheduled_action_item" if active_action
            else "unscheduled_research_suggestion"
        ),
        "current_work_kind": current_work_kind,
        "current_workflow_status": current_workflow_status,
        "current_fallback": current_fallback,
        "current_work_created_at": current_work_created_at,
        "current_source_type": current_source_type,
        "current_source_id": current_source_id,
        "current_source_occurred_at": current_source_occurred_at,
        "next_action": current_next_action,
        "next_action_source": next_action_source,
        "next_action_is_fresh": bool(active_outreach or terminal_outreach or not active_action),
        # Compact route-type for the opportunities table's "最佳可用路径"
        # column -- same suggested_bridge_action.kind the drawer already
        # shows in full, just labeled short here (see ROUTE_KIND_ZH on
        # the frontend for the actual Chinese labels).
        "best_route_kind": suggested["kind"] if suggested else None,
        "due_date": current_due_date,
        "due_date_status": "scheduled" if current_due_date else "待排期",
        "due_date_source": current_due_date_source,
        "planned_work_type": current_planned_work_type,
        "owner": current_owner,
        "x_handle": company.x_handle,
        "last_verified_at": company.last_verified_at.isoformat() if company.last_verified_at else None,
        "evidence_contract": _company_evidence_contract(company),
        "commercial_research": (
            {
                "disposition": company.longlist_assessment.disposition,
                "icp_type": company.longlist_assessment.icp_type,
                "commercial_priority_score": company.longlist_assessment.commercial_priority_score,
                "discovery_paths": _safe_json_list(company.longlist_assessment.discovery_paths_json),
                "keep_reason": company.longlist_assessment.keep_reason,
                "downgrade_reason": company.longlist_assessment.downgrade_reason,
                "confidence": company.longlist_assessment.confidence,
                "fact_status": company.longlist_assessment.fact_status,
                "is_priority": company.research_dossier is not None,
                "priority_rank": company.research_dossier.priority_rank if company.research_dossier else None,
                "why_company": company.research_dossier.why_company if company.research_dossier else None,
                "why_now": company.research_dossier.why_now if company.research_dossier else company.why_now,
                "mango_offer": company.research_dossier.mango_offer if company.research_dossier else None,
                "operator": (
                    _operator_json(company.research_dossier.operator)
                    if company.research_dossier and company.research_dossier.operator
                    else None
                ),
                "operator_role_relevance": (
                    company.research_dossier.operator_role_relevance
                    if company.research_dossier
                    else None
                ),
                "best_route": next(
                    (
                        _commercial_route_json(route)
                        for route in company.contact_routes
                        if route.is_best
                    ),
                    None,
                ),
                "last_verified_at": (
                    company.longlist_assessment.last_verified_at.isoformat()
                    if company.longlist_assessment.last_verified_at
                    else None
                ),
            }
            if company.longlist_assessment
            else None
        ),
        "created_at": company.created_at.isoformat() if company.created_at else None,
    }


def company_detail(company: Company, *, include_internal: bool = False) -> dict:
    base = company_summary(company)
    company_path = best_intro_path(company.intro_paths, "company_account")
    person_path = best_intro_path(company.intro_paths, "operator_person")
    base.update(
        {
            "why_now": company.why_now,
            "budget_evidence": company.budget_evidence,
            "buyer_or_route": company.buyer_or_route,
            "internal_notes": company.internal_notes if include_internal else None,
            "internal_notes_available": bool(company.internal_notes) if include_internal else False,
            "aliases": [a.alias for a in company.aliases],
            "sources": [s.source_url for s in company.sources],
            "operators": [_operator_json(o) for o in company.operators],
            "best_company_path": _path_json(company_path),
            "best_person_path": _path_json(person_path),
            "person_relationship_strength": relationship_strength(person_path),
            "company_relationship_strength": relationship_strength(company_path),
            # Every path on record, not just the one "best" pick -- a
            # single point of failure (one connector, one route) is a real
            # risk the source methodology report calls out explicitly.
            # Sorted reachable-first but otherwise left for a human to
            # compare (which connector, which root, which hop count).
            "all_intro_paths": [
                _path_json(p)
                for p in sorted(
                    (path for path in company.intro_paths if is_material_intro_path(path)),
                    key=lambda p: (p.target_type, 0 if p.graph_reachable else 1),
                )[:20]
            ],
            "suggested_bridge_action": suggested_bridge_action(company),
            "relationship_stage_detail": relationship_stage(company),
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
                    "status": a.status,
                    "due_date": a.due_date,
                    "due_date_status": "scheduled" if a.due_date else "待排期",
                    "due_date_source": due_date_source(a.company_id, a.due_date),
                    "planned_work_type": planned_work_type(a.company_id),
                    "outcome_notes": a.outcome_notes,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in company.action_items
            ],
            "sponsorships": [sponsorship_json(s) for s in company.sponsorships],
            "outreach_logs": [_outreach_log_json(log) for log in company.outreach_logs],
            "solomon_review": (
                {
                    "decision": company.solomon_review.decision,
                    "intro_path_confirmed_real": company.solomon_review.intro_path_confirmed_real,
                    "creator_suggestions_sellable": company.solomon_review.creator_suggestions_sellable,
                    "missing_info": company.solomon_review.missing_info,
                    "solomon_notes": company.solomon_review.solomon_notes,
                    "next_action": company.solomon_review.next_action,
                    "reviewed_at": company.solomon_review.reviewed_at.isoformat() if company.solomon_review.reviewed_at else None,
                }
                if company.solomon_review
                else None
            ),
            "solomon_review_history": [
                {
                    "id": h.id,
                    "decision": h.decision,
                    "intro_path_confirmed_real": h.intro_path_confirmed_real,
                    "creator_suggestions_sellable": h.creator_suggestions_sellable,
                    "missing_info": h.missing_info,
                    "solomon_notes": h.solomon_notes,
                    "next_action": h.next_action,
                    "recorded_at": h.recorded_at.isoformat() if h.recorded_at else None,
                }
                for h in company.solomon_review_history
            ],
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
            "commercial_dossier": commercial_dossier_json(company),
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
    semantics = sponsorship_evidence_semantics(s)
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
        "evidence_type": semantics["evidence_type"],
        "source_disclosure_type": semantics["source_disclosure_type"],
        "commercial_status": semantics["commercial_status"],
        "evidence_date": semantics["evidence_date"],
        "supporting_note": semantics["supporting_note"],
        "collected_at": semantics["collected_at"],
        "verified_at": semantics["verified_at"],
        "evidence_text": s.evidence_text,
        "confidence": s.confidence,
        "review_status": s.review_status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
        "reviewed_note": s.reviewed_note,
    }


def connector_brief_json(brief: ConnectorBrief, companies_by_id: dict[str, Company]) -> dict:
    ids = [c.strip() for c in brief.company_ids.split(",") if c.strip()]
    companies = [companies_by_id[cid] for cid in ids if cid in companies_by_id]
    return {
        "id": brief.id,
        "connector_name": brief.connector_name,
        "connector_x_handle": brief.connector_x_handle,
        # ConnectorBrief is a legacy hand-curated research snapshot. Its
        # old action prose can contain claims superseded by live E0-E6 and
        # OutreachLog data, so never expose it as a current recommendation.
        "best_current_action": None,
        "archive_note": "历史研究快照：需按当前关系阶段与执行记录重新核实",
        "companies": [{"company_id": c.company_id, "name": c.name, "priority_tier": c.priority_tier} for c in companies],
    }
