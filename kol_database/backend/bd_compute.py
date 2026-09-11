"""Explainable scoring for Opportunities, Creator campaign fit, and
Relationship strength. Per product spec: three independent judgments, never
one black-box number. Every function here returns a label plus the plain
factors that produced it, not just a score.
"""

from __future__ import annotations

import datetime as dt
import json
import re

from .compute import quote_summary
from .campaign_matching import match_creator_to_campaign
from .models import Company, IntroBridge, IntroPath, MEDIA_CHANNEL_CLASSES, NON_CREATOR_CLASSES, Operator


DEGREE_RANK = {"direct": 0, "secondary": 1, "third": 2}

_URL_RE = re.compile(r"https?://[^\s,;，；]+", re.I)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)


def _company_raw(company: Company) -> dict:
    """Best-effort access to the imported record without trusting its shape."""
    try:
        value = json.loads(company.raw_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _degree_rank(p: IntroPath) -> int:
    """87% of operator_person_paths rows in the source data (v4 QA report:
    73/84) carry no degree_label at all -- ranking those as automatically
    worse than every labeled "third" (weakest) path was a real bug: it
    silently buried paths purely for missing metadata, not because they
    were actually weaker. hop_count is far more consistently populated in
    the source, so an unlabeled path falls back to the hop-count-implied
    degree instead of the worst bucket. Only a path missing *both* signals
    lands last -- genuinely unknown, not assumed weak."""
    if p.degree_label in DEGREE_RANK:
        return DEGREE_RANK[p.degree_label]
    if p.hop_count == 1:
        return 0
    if p.hop_count == 2:
        return 1
    if p.hop_count and p.hop_count >= 3:
        return 2
    return 3


def is_material_intro_path(path: IntroPath) -> bool:
    """Whether a row contains an observed graph/path signal.

    `operator_person_paths_v4.json` deliberately contains status rows for
    combinations that could not be evaluated. After migration those rows
    exist as IntroPath objects with graph_reachable=False and no degree,
    hop, labels or connector. Their existence is useful provenance, but it
    is not evidence that a follow edge/path was observed and must not earn
    E1 or render as an alternative route.
    """
    return bool(
        path.graph_reachable
        or path.hop_count is not None
        or (path.degree_label and path.degree_label.strip())
        or (path.path_labels and path.path_labels.strip())
        or (path.connector_handle and path.connector_handle.strip())
    )


def best_intro_path(paths: list[IntroPath], target_type: str | None = None) -> IntroPath | None:
    """Pick the strongest path: reachable beats unreachable, direct beats
    secondary beats third (falling back to hop_count when degree_label is
    missing -- see _degree_rank), is_primary beats alternates. Empty
    not-evaluated status placeholders are not paths and are ignored."""
    candidates = [
        p
        for p in paths
        if (target_type is None or p.target_type == target_type) and is_material_intro_path(p)
    ]
    if not candidates:
        return None

    def sort_key(p: IntroPath):
        return (
            0 if p.graph_reachable else 1,
            _degree_rank(p),
            0 if p.is_primary else 1,
            p.hop_count or 99,
        )

    return sorted(candidates, key=sort_key)[0]


def reachability_summary(company: Company) -> dict:
    """Auditing this sprint's own intro-graph data found that "graph
    reachable" (IntroPath.graph_reachable) is overwhelmingly a ONE-WAY
    follow (a Mango-side connector follows the target; the target does
    not follow back) -- of 74 connector->company/operator edges checked
    by hand, only 1 was a genuine mutual follow. Treating that as
    "reachable" for scoring was a real overclaim: on X, a one-way follow
    gives no messaging privilege and no reason to think the target has
    ever seen the connector's name. That got fixed by requiring a
    verified IntroBridge or an open DM instead (see git history).

    A second overclaim of the exact same shape then crept back in: an
    IntroBridge only proves a third person is mutual with BOTH a
    Mango-side account and the target -- it does NOT prove that person
    actually knows the target personally, is willing to introduce, or
    has even been asked. Labeling that "已确认可触达" (confirmed
    reachable) was the same mistake at a different layer, flagged by a
    from-scratch read-only audit of the live product (2026-08-28).

    A third pass (2026-08-28, second read-only audit) caught a further
    mistake in that split: ranking level-4 (open DM) ABOVE level-3
    (bridge) treated "you can technically send a cold message" as a
    *better* outcome than "a real person might vouch for you" -- but an
    open DM is still cold outreach, and the entire point of this product
    from the very first request has been finding warm introductions
    instead of cold outreach, not ranking cold channels as the ideal
    endpoint. Contactability (can a message physically be sent) and
    relationship warmth (does anyone actually know this target) are two
    different axes; conflating them and putting "cold but sendable" at
    the top inverted the priority this whole feature exists for.

    So the ordering is: a verified bridge candidate (level 4, still
    unconfirmed for real relationship/willingness, but the closest thing
    to a warm path this data can show) outranks an open DM (level 3,
    a real but cold channel -- useful as a fallback, never the target
    outcome). IntroPath data is still never deleted or hidden -- it
    surfaces as "仅为研究线索" (reference signal) at level 1.
    """
    company_path = best_intro_path(company.intro_paths, "company_account")
    person_path = best_intro_path(company.intro_paths, "operator_person")
    identified_operator = next((o for o in company.operators if o.name), None)
    source_matched_operator = next((o for o in company.operators if o.identity_confirmed), None)
    # A human relationship_rejected event retires that exact bridge from
    # every top-level reachability judgment.  The row remains in the audit
    # detail, but cannot keep a company looking warm/actionable.
    verified_bridge = bool(_live_bridge_stages(company))
    # `can_dm` is only executable when the account handle is also known.
    # Partially migrated rows can carry can_dm=True without a handle;
    # treating those as a channel produced literal "@None" in plans.
    dm_open_operator = next((o for o in company.operators if o.can_dm and (o.x_handle or "").strip()), None)

    if verified_bridge:
        level, label = 4, "发现互关引荐候选人（真实关系与引荐意愿尚未核实）"
    elif dm_open_operator:
        level, label = 3, "仅有冷启动联系渠道（私信开放，非暖关系）"
    elif identified_operator:
        level, label = 2, "已识别负责人，触达方式待验证"
    elif company_path or person_path:
        level, label = 1, "仅为研究线索（单向关注，非已验证关系）"
    else:
        level, label = 0, "暂无已知路径"

    return {
        "level": level,
        "label": label,
        "best_company_path": company_path,
        "best_person_path": person_path,
        # Legacy `identity_confirmed` means source-level official-role +
        # exact-X matching for imported v4 rows, not a Mango human review.
        "confirmed_operator": source_matched_operator,
        "identified_operator": identified_operator,
        "source_matched_operator": source_matched_operator,
        "verified_bridge": verified_bridge,
        "dm_open_operator": dm_open_operator,
    }


# ---------------------------------------------------------------------------
# Relationship Stage (E0-E6) -- 2026-08-28, section II of the evidence-loop
# spec. Public-data signals (E1-E3) are always machine-computed and can
# change automatically as new interaction evidence comes in. E4-E6 require
# an actual human execution record (OutreachLog) -- no script may ever
# infer "Mango confirmed this relationship" or "the connector agreed to
# introduce" from public data alone. A DM/email/contact-form channel is
# tracked completely separately (direct_channel_status) and never folds
# into this scale, per the spec: contactability != relationship warmth.
# ---------------------------------------------------------------------------

E_STAGE_LABELS = {
    0: "E0 · 无关系线索",
    1: "E1 · 仅关注/互关",
    2: "E2 · 曾有一次公开互动或共同出席活动",
    3: "E3 · 近期多次双向公开互动或有明确合作背景",
    4: "E4 · Mango 内部人员确认真实认识",
    5: "E5 · 连接人已同意引荐",
    6: "E6 · 引荐已发出且目标方已回应",
}

# How far back an interaction still counts as "recent" for E3 -- roughly
# 18 months. Older-but-real interaction (e.g. a 2021 exchange) still
# earns E2, it just doesn't earn the stronger "still warm" claim of E3.
_RECENT_INTERACTION_WINDOW = dt.timedelta(days=545)

# Only an explicit denial of the candidate relationship disproves that
# relationship. A declined request or no reply is an execution outcome, not
# evidence that the two people do not know each other; those events remain in
# the timeline and may trigger a follow-up/fallback, but must not permanently
# erase the connector from the relationship graph.
_DISPROVE_OUTREACH_STAGES = {"relationship_rejected"}


def _bridge_evidence_stage(bridge: IntroBridge) -> tuple[int, str]:
    """E1-E3 from public interaction evidence alone (see
    InteractionCheck/InteractionEvidence). Never claims "no relationship"
    on a miss -- only "not found within what was searched"."""
    checks = list(getattr(bridge, "interaction_checks", None) or [])
    if not checks:
        return 1, "仅关注/互关，尚未查询公开互动记录"
    evidence = [e for c in checks for e in c.evidence]
    if not evidence:
        return 1, "仅关注/互关，本次公开查询范围内未发现互动（不代表两人不认识）"
    cutoff = dt.datetime.now() - _RECENT_INTERACTION_WINDOW
    recent = [e for e in evidence if e.posted_at and e.posted_at >= cutoff]
    # "Recent + repeated + bidirectional" must be true inside the same
    # 18-month window. Using all-time authors here let an old reverse reply
    # combine with two recent one-way posts and falsely promote the pair to
    # E3 even though the current interaction was not bidirectional.
    recent_authors = {e.author_handle for e in recent if e.author_handle}
    bidirectional = len(recent_authors) >= 2
    if len(recent) >= 2 and bidirectional:
        return 3, f"近 18 个月内发现 {len(recent)} 次双向公开互动"
    return 2, f"发现 {len(evidence)} 次公开互动记录，但不满足「近期 + 双向 + 多次」"


def _apply_outreach_logs(level: int, logs: list) -> tuple[int, bool, str | None]:
    """Advance E4-E6 as a path-attributed state machine.

    A generic/direct `target_replied` event is a real funnel outcome, but it
    is not proof that an introduction happened. E6 therefore requires an
    earlier non-void `intro_made` event for the same bridge. Likewise E4/E5
    events need a target operator or bridge context; an unlinked company note
    cannot silently certify a relationship.
    """
    disproven = False
    disprove_note = None
    intro_made_bridge_ids: set[int] = set()
    for log in sorted((l for l in logs if not l.voided), key=lambda l: l.occurred_at or dt.datetime.min):
        if log.stage in _DISPROVE_OUTREACH_STAGES:
            disproven = True
            when = log.occurred_at.date().isoformat() if log.occurred_at else "未知日期"
            disprove_note = f"{when} 记录：{log.stage}" + (f" -- {log.notes}" if log.notes else "")
        elif log.stage == "relationship_confirmed" and (log.bridge_id is not None or log.operator_id is not None):
            level = max(level, 4)
            disproven, disprove_note = False, None
        elif log.stage in {"intro_accepted", "intro_made"} and log.bridge_id is not None:
            level = max(level, 5)
            if log.stage == "intro_made":
                intro_made_bridge_ids.add(log.bridge_id)
            disproven, disprove_note = False, None
        elif (
            log.stage == "target_replied"
            and log.bridge_id is not None
            and log.bridge_id in intro_made_bridge_ids
        ):
            level = max(level, 6)
            disproven, disprove_note = False, None
    return level, disproven, disprove_note


def bridge_relationship_stage(bridge: IntroBridge, outreach_logs: list) -> dict:
    """Per-candidate E-stage -- this is what the connector comparison UI
    (section III) shows for each of up to 3 candidates side by side,
    instead of the system silently picking one."""
    level, evidence_note = _bridge_evidence_stage(bridge)
    checks = list(getattr(bridge, "interaction_checks", None) or [])
    evidence = [item for check in checks for item in (check.evidence or [])]
    latest_check = max((check.checked_at for check in checks if check.checked_at), default=None)
    latest_evidence = max((item.posted_at for item in evidence if item.posted_at), default=None)
    coverage_notes = sorted({check.coverage_note for check in checks if check.coverage_note})
    event_context = any(
        (item.interaction_type or "").casefold() == "event"
        or any(word in (item.context_text or "").casefold() for word in ("panel", "event", "speaker", "moderator"))
        for item in evidence
    )
    logs = [l for l in outreach_logs if l.bridge_id == bridge.id]
    level, disproven, disprove_note = _apply_outreach_logs(level, logs)
    if evidence:
        verification_status = "interaction_found_relationship_unverified"
    elif checks:
        verification_status = "checked_no_public_interaction_found"
    else:
        verification_status = "unchecked"

    # Public evidence can make a candidate more useful to investigate, but
    # only a human execution record can make an introduction ready. The
    # first ask is deliberately an information check, not an intro request.
    if level >= 4:
        askability, askability_rank, social_cost = "human_confirmed", 4, "low_to_medium"
    elif level >= 3:
        askability, askability_rank, social_cost = "ask_for_context_first", 3, "medium"
    elif level == 2:
        askability, askability_rank, social_cost = "ask_for_context_first", 2, "medium"
    elif checks:
        askability, askability_rank, social_cost = "internal_relationship_check_only", 1, "medium_to_high"
    else:
        askability, askability_rank, social_cost = "verify_evidence_before_asking", 0, "unknown"
    first_ask = (
        f"先向 @{bridge.mango_side_handle} 核实：你是否真的认识/最近联系过 "
        f"@{bridge.bridge_handle}{'（' + bridge.bridge_name + '）' if bridge.bridge_name else ''}？"
        "如果认识，先问 TA 是否了解目标公司和相关负责人；本阶段不要请求引荐。"
    )
    return {
        "bridge_id": bridge.id,
        "bridge_handle": bridge.bridge_handle,
        "bridge_name": bridge.bridge_name,
        "bridge_followers_count": bridge.bridge_followers_count,
        "mango_side_handle": bridge.mango_side_handle,
        "level": level,
        "code": f"E{level}",
        "label": E_STAGE_LABELS[level],
        "evidence_note": evidence_note,
        "relationship_verified": level >= 4,
        "interaction_checked": bool(checks),
        "interaction_check_count": len(checks),
        "interaction_evidence_count": len(evidence),
        "interaction_verification_status": verification_status,
        "latest_interaction_check_at": _iso(latest_check),
        "latest_interaction_at": _iso(latest_evidence),
        "evidence_recency": (
            "recent" if latest_evidence and dt.datetime.now() - latest_evidence <= _RECENT_INTERACTION_WINDOW
            else "historical" if latest_evidence else "none_found" if checks else "unchecked"
        ),
        "event_context_only": event_context,
        "coverage_notes": coverage_notes,
        "askability": askability,
        "askability_rank": askability_rank,
        "social_cost": social_cost,
        "disproven": disproven,
        "disprove_note": disprove_note,
        "suggested_question": first_ask,
        "exact_first_ask": first_ask,
        "fallback": "若内部无人确认真实关系，先把此人留在核实队列；改查官方联系渠道或其他有合作背景的 connector。",
    }


def deduplicate_bridge_stages(bridges: list[IntroBridge], outreach_logs: list) -> list[dict]:
    """One candidate per target company/person, even when both Mango roots
    produced the same mutual-follow row.  Root provenance is retained in
    ``mango_side_handles``; follower count never participates in ordering.
    """
    grouped: dict[str, list[tuple[IntroBridge, dict]]] = {}
    for bridge in bridges:
        key = (bridge.bridge_handle or "").strip().casefold()
        if not key:
            continue
        grouped.setdefault(key, []).append((bridge, bridge_relationship_stage(bridge, outreach_logs)))

    merged: list[dict] = []
    for rows in grouped.values():
        # Strongest relationship/askability wins; stable id only breaks
        # true semantic ties. Never use audience size as a proxy for access.
        rows.sort(
            key=lambda pair: (
                pair[1]["disproven"],
                -pair[1]["level"],
                -pair[1]["askability_rank"],
                pair[0].id or 0,
            )
        )
        selected = dict(rows[0][1])
        stages = [stage for _, stage in rows]
        selected["bridge_ids"] = sorted({bridge.id for bridge, _ in rows if bridge.id is not None})
        selected["live_bridge_ids"] = sorted(
            {bridge.id for bridge, stage in rows if bridge.id is not None and not stage["disproven"]}
        )
        selected["disproven_bridge_ids"] = sorted(
            {bridge.id for bridge, stage in rows if bridge.id is not None and stage["disproven"]}
        )
        selected["mango_side_handles"] = sorted({bridge.mango_side_handle for bridge, _ in rows if bridge.mango_side_handle})
        selected["mango_side_handle"] = selected["mango_side_handles"][0] if selected["mango_side_handles"] else None
        owners = " / ".join(f"@{handle}" for handle in selected["mango_side_handles"]) or "Mango 内部"
        selected["exact_first_ask"] = (
            f"先由候选 Mango owner（{owners}）确认谁真正认识/最近联系过 @{selected['bridge_handle']}；"
            "若确认认识，再询问 TA 对目标公司和相关负责人的了解。本阶段不要请求引荐。"
        )
        selected["suggested_question"] = selected["exact_first_ask"]
        selected["duplicate_source_row_count"] = len(rows)
        selected["interaction_checked"] = any(stage["interaction_checked"] for stage in stages)
        selected["interaction_check_count"] = sum(stage["interaction_check_count"] for stage in stages)
        selected["interaction_evidence_count"] = sum(stage["interaction_evidence_count"] for stage in stages)
        if selected["interaction_evidence_count"]:
            selected["interaction_verification_status"] = "interaction_found_relationship_unverified"
        elif selected["interaction_checked"]:
            selected["interaction_verification_status"] = "checked_no_public_interaction_found"
        else:
            selected["interaction_verification_status"] = "unchecked"
        selected["coverage_notes"] = sorted({note for stage in stages for note in stage["coverage_notes"]})
        merged.append(selected)
    return sorted(
        merged,
        key=lambda stage: (
            stage["disproven"],
            -stage["askability_rank"],
            -stage["level"],
            stage["bridge_handle"].casefold(),
        ),
    )


def _bridge_stages_for_company(company: Company) -> list[dict]:
    bridges = [bridge for operator in company.operators for bridge in (operator.bridges or [])]
    return deduplicate_bridge_stages(bridges, list(company.outreach_logs or []))


def _live_bridge_stages(company: Company) -> list[dict]:
    """Current bridge candidates, excluding explicit human disprovals."""

    return [stage for stage in _bridge_stages_for_company(company) if not stage["disproven"]]


def interaction_audit_summary(companies: list[Company]) -> dict:
    """Coverage denominator for the connector interaction audit.

    The unit is a unique target-company/person pair, not a raw IntroBridge
    row. The same person discovered from both Solomon and Mango roots is one
    candidate with two provenance roots, not two relationships.
    """
    raw_rows = 0
    by_company: dict[str, dict] = {}
    all_pairs: list[dict] = []
    for company in companies:
        bridges = [bridge for operator in company.operators for bridge in (operator.bridges or [])]
        raw_rows += len(bridges)
        stages = deduplicate_bridge_stages(bridges, list(company.outreach_logs or []))
        rows = []
        for stage in stages:
            rows.append(stage)
            all_pairs.append(stage)
        checked = sum(bool(stage["interaction_checked"]) for stage in rows)
        found = sum(stage["interaction_evidence_count"] > 0 for stage in rows)
        total = len(rows)
        if total:
            by_company[company.company_id] = {
                "company_id": company.company_id,
                "company_name": company.name,
                "total_candidates": total,
                "checked": checked,
                "interaction_found": found,
                "no_public_interaction_found": checked - found,
                "unchecked": total - checked,
            }
    checked = sum(bool(stage["interaction_checked"]) for stage in all_pairs)
    found = sum(stage["interaction_evidence_count"] > 0 for stage in all_pairs)
    checked_at = sorted(
        {stage["latest_interaction_check_at"] for stage in all_pairs if stage["latest_interaction_check_at"]}
    )
    directional_query_count = sum(stage["interaction_check_count"] for stage in all_pairs)
    coverage_notes = sorted({note for stage in all_pairs for note in stage["coverage_notes"]})
    return {
        "raw_bridge_rows": raw_rows,
        "unique_company_person_pairs": len(all_pairs),
        "checked": checked,
        "interaction_found": found,
        "no_public_interaction_found": checked - found,
        "unchecked": len(all_pairs) - checked,
        "directional_query_count": directional_query_count,
        "checked_at": checked_at,
        "first_checked_at": checked_at[0] if checked_at else None,
        "last_checked_at": checked_at[-1] if checked_at else None,
        "search_scope": coverage_notes,
        "by_company": sorted(by_company.values(), key=lambda row: (-row["total_candidates"], row["company_name"].casefold())),
    }


def direct_channel_status(company: Company) -> dict | None:
    """The cold/direct fallback channel (currently: confirmed open DM only
    -- email/contact-form aren't modeled in this DB yet, so this never
    fabricates their presence). Tracked in parallel with, never merged
    into, the E0-E6 relationship stage -- see module docstring.

    2026-08-28 bug found on Cursor: this used to also require
    identity_confirmed, which hid Lee Robinson's DM entirely (his role
    identity wasn't human-verified, but can_dm=True is an independently
    checked fact about the account itself -- it doesn't depend on whether
    a human has separately confirmed he's the "right" person for the
    role). identity_confirmed is now a field on the returned fact instead
    of a gate on whether the fact exists at all -- "the DM is open" and
    "we've verified this is the correct person" are different questions,
    and conflating them was hiding a real, usable contact channel."""
    dm_open = next((o for o in company.operators if o.can_dm and (o.x_handle or "").strip()), None)
    if dm_open:
        identity_human_verified = bool(dm_open.identity_human_verified_at)
        role_human_verified = bool(dm_open.role_human_verified_at)
        return {
            "channel": "dm",
            "operator_id": dm_open.id,
            "handle": dm_open.x_handle,
            "name": dm_open.name,
            "note": "X 私信已确认开放",
            "identity_confirmed": dm_open.identity_confirmed,
            "source_identity_matched": dm_open.identity_confirmed,
            # Freshness/source retrieval (`last_verified_at`) is not a
            # human approval event. Identity and role have independent,
            # explicit review timestamps and must stay separate.
            "identity_human_verified": identity_human_verified,
            "role_human_verified": role_human_verified,
            "identity_and_role_human_verified": identity_human_verified and role_human_verified,
        }
    return None


CHANNEL_LABELS = {
    "no_known_channel": "No known channel",
    "public_form": "Public form — cold",
    "public_business_email": "Public business email — cold",
    "x_dm_appears_open": "X DM appears open — cold",
    "other_verified_public_channel": "Other verified public channel — cold",
}


def channel_access(company: Company) -> dict:
    """Dimension 1: physical contactability only, never relationship warmth.

    The imported route text and raw public-contact list are research fields,
    so only literal email addresses / explicit form URLs are exposed. A
    company website on its own is not silently promoted to a contact channel.
    """
    raw = _company_raw(company)
    raw_methods = raw.get("public_contact_methods") or []
    if isinstance(raw_methods, str):
        raw_methods = [raw_methods]
    texts = [company.buyer_or_route or ""] + [str(item) for item in raw_methods if item]
    joined = "\n".join(texts)
    channels: list[dict] = []

    dm = direct_channel_status(company)
    if dm:
        channels.append(
            {
                "type": "x_dm_appears_open",
                "label": CHANNEL_LABELS["x_dm_appears_open"],
                "value": f"@{dm['handle']}",
                "operator_id": dm["operator_id"],
                "verified_at": next(
                    (_iso(o.can_dm_checked_at) for o in company.operators if o.id == dm["operator_id"]), None
                ),
                "cold": True,
            }
        )
    for email in sorted(set(_EMAIL_RE.findall(joined))):
        channels.append(
            {
                "type": "public_business_email",
                "label": CHANNEL_LABELS["public_business_email"],
                "value": email,
                "verified_at": _iso(company.last_verified_at),
                "cold": True,
            }
        )
    for url in sorted(set(_URL_RE.findall(joined))):
        lowered = url.casefold()
        if any(word in lowered for word in ("contact", "partner", "creator", "affiliate", "apply", "form")):
            channels.append(
                {
                    "type": "public_form",
                    "label": CHANNEL_LABELS["public_form"],
                    "value": url,
                    "verified_at": _iso(company.last_verified_at),
                    "cold": True,
                }
            )

    # Deterministic precedence: a named operator's open DM, then business
    # email, then a form. All remain equally cold in relationship terms.
    precedence = {"x_dm_appears_open": 0, "public_business_email": 1, "public_form": 2}
    channels.sort(key=lambda row: (precedence.get(row["type"], 9), row["value"].casefold()))
    primary = channels[0] if channels else None
    return {
        "available": bool(channels),
        "state": primary["type"] if primary else "no_known_channel",
        "label": primary["label"] if primary else CHANNEL_LABELS["no_known_channel"],
        "cold": bool(channels),
        "channels": channels,
    }


OPERATOR_RELEVANCE_LABELS = {
    0: "Unknown",
    1: "Useful contact",
    2: "Influencer",
    3: "Relevant operator",
    4: "Likely budget owner",
    5: "Confirmed decision-maker",
}
_RELEVANT_ROLE_TERMS = (
    "growth", "marketing", "partnership", "community", "developer relations", "devrel",
    "ecosystem", "creator", "affiliate", "business development", "go-to-market", "gtm",
    "chief business officer",
    "增长", "市场", "合作", "社区", "生态", "创作者", "商务",
)
_SENIOR_ROLE_TERMS = ("chief", "head", "vp", "vice president", "director", "lead", "负责人", "总监")
_FOUNDER_ROLE_TERMS = ("founder", "co-founder", "ceo", "创始人", "首席执行官")


def operator_relevance(operator: Operator | None) -> dict:
    """Dimension 4: campaign-budget proximity, separate from identity and reach."""
    if not operator:
        return {"level": 0, "label": OPERATOR_RELEVANCE_LABELS[0], "reason": "No operator identified"}
    role = (operator.role or "").casefold()
    named = bool((operator.name or "").strip() or (operator.x_handle or "").strip())
    relevant = any(term in role for term in _RELEVANT_ROLE_TERMS)
    senior = any(term in role for term in _SENIOR_ROLE_TERMS)
    founder = any(term in role for term in _FOUNDER_ROLE_TERMS)

    if operator.budget_authority_confirmed and operator.budget_authority_verified_at:
        level, reason = 5, "Budget authority was explicitly human-verified"
    elif relevant and senior:
        level, reason = 4, "Senior role is directly relevant to growth/marketing/partnership spend; budget authority remains inferred"
    elif relevant:
        level, reason = 3, "Role is directly relevant to growth, marketing, partnerships, community, DevRel or ecosystem"
    elif founder:
        # A founder may influence a decision, but title alone is not proof
        # that this person owns the current creator/campaign budget.
        level, reason = 2, "Founder/executive may influence the decision; current campaign budget ownership is unverified"
    elif named:
        level, reason = 1, "Named contact, but role is not yet shown to be campaign-relevant"
    else:
        level, reason = 0, "Operator identity is unresolved"
    return {
        "level": level,
        "label": OPERATOR_RELEVANCE_LABELS[level],
        "reason": reason,
        "identity_source_matched": bool(operator.identity_confirmed),
        "identity_human_verified": bool(operator.identity_human_verified_at),
        "role_human_verified": bool(operator.role_human_verified_at),
        "budget_authority_human_verified": bool(
            operator.budget_authority_confirmed and operator.budget_authority_verified_at
        ),
    }


def best_relevant_operator(company: Company) -> tuple[Operator | None, dict]:
    ranked = [(operator_relevance(operator), operator) for operator in company.operators if operator.name or operator.x_handle]
    if not ranked:
        return None, operator_relevance(None)
    ranked.sort(
        key=lambda pair: (
            -pair[0]["level"],
            -int(pair[0]["role_human_verified"]),
            -int(pair[0]["identity_human_verified"]),
            -int(pair[0]["identity_source_matched"]),
            (pair[1].name or pair[1].x_handle or "").casefold(),
        )
    )
    return ranked[0][1], ranked[0][0]


INTRO_READINESS_LABELS = {
    0: "Unverified",
    1: "Mango needs to ask internally",
    2: "Connector confirms knowing target",
    3: "Connector is willing to introduce",
    4: "Introduction sent",
    5: "Target replied",
    6: "Meeting booked",
}


def intro_readiness(company: Company) -> dict:
    """Dimension 3: only human execution records can advance past a queue."""
    live_stages = _live_bridge_stages(company)
    live_bridge_ids = {
        bridge_id
        for stage in live_stages
        for bridge_id in stage.get("live_bridge_ids", stage.get("bridge_ids", []))
    }
    level = 1 if live_stages else 0
    source = "mutual_follow_candidate" if live_stages else None
    latest = None
    intro_made_bridge_ids: set[int] = set()
    for log in _live_outreach_logs(company):
        live_bridge_event = log.bridge_id is not None and log.bridge_id in live_bridge_ids
        direct_operator_event = log.bridge_id is None and log.operator_id is not None
        if log.stage == "relationship_confirmed" and (live_bridge_event or direct_operator_event):
            level, source, latest = max(level, 2), "outreach_log", log
        elif log.stage == "intro_accepted" and live_bridge_event:
            level, source, latest = max(level, 3), "outreach_log", log
        elif log.stage == "intro_made" and live_bridge_event:
            intro_made_bridge_ids.add(log.bridge_id)
            level, source, latest = max(level, 4), "outreach_log", log
        elif log.stage == "target_replied" and log.bridge_id in intro_made_bridge_ids:
            level, source, latest = max(level, 5), "outreach_log", log
        elif log.stage == "meeting_booked" and (
            log.bridge_id in intro_made_bridge_ids or level >= 5
        ):
            level, source, latest = max(level, 6), "outreach_log", log
    return {
        "level": level,
        "code": f"I{level}",
        "label": INTRO_READINESS_LABELS[level],
        "human_confirmed": level >= 2,
        "source": source,
        "latest_event_at": _iso(latest.occurred_at) if latest else None,
        "latest_event_stage": latest.stage if latest else None,
    }


def relationship_evidence(company: Company) -> dict:
    """Dimension 2: observed relationship evidence, not intro willingness."""
    readiness = intro_readiness(company)
    if readiness["level"] >= 2:
        return {
            "level": 6,
            "state": "relationship_confirmed_by_human",
            "label": "Relationship confirmed by Mango employee or connector",
            "relationship_verified": True,
        }
    stages = _live_bridge_stages(company)
    if any(stage["level"] >= 3 for stage in stages):
        return {
            "level": 5,
            "state": "repeated_recent_public_interaction",
            "label": "Repeated/recent public interaction — relationship unverified",
            "relationship_verified": False,
        }
    event = next((stage for stage in stages if stage["interaction_evidence_count"] and stage["event_context_only"]), None)
    if event:
        return {
            "level": 4,
            "state": "shared_event_history",
            "label": "Shared event/history found — private relationship unverified",
            "relationship_verified": False,
        }
    if any(stage["interaction_evidence_count"] for stage in stages):
        return {
            "level": 3,
            "state": "public_interaction_found",
            "label": "Public interaction found — relationship unverified",
            "relationship_verified": False,
        }
    if stages:
        checked = any(stage["interaction_checked"] for stage in stages)
        return {
            "level": 2,
            "state": "mutual_follow_candidate",
            "label": (
                "Mutual-follow candidate — no public X interaction found in the checked search window"
                if checked
                else "Mutual-follow candidate — relationship unverified"
            ),
            "relationship_verified": False,
        }
    if best_intro_path(company.intro_paths):
        return {
            "level": 1,
            "state": "one_way_follow_reference",
            "label": "One-way follow — reference only",
            "relationship_verified": False,
        }
    return {"level": 0, "state": "none_found", "label": "None found", "relationship_verified": False}


def relationship_stage(company: Company) -> dict:
    """Company-level Relationship Stage -- max across every bridge
    candidate's own stage, plus any company-level (not tied to a specific
    bridge) human execution record such as Solomon directly confirming he
    knows the target without a third-party bridge being involved. Never
    represents commercial value (see opportunity_priority) or
    contactability (see direct_channel_status) -- purely "how warm is the
    actual relationship, and how much of that is verified vs. inferred."
    """
    outreach_logs = list(company.outreach_logs or [])
    bridges = [b for o in company.operators for b in getattr(o, "bridges", [])]
    bridge_stages = deduplicate_bridge_stages(bridges, outreach_logs)
    live_bridge_stages = [stage for stage in bridge_stages if not stage["disproven"]]

    base_level = 1 if (live_bridge_stages or best_intro_path(company.intro_paths)) else 0
    company_level_logs = [l for l in outreach_logs if l.bridge_id is None]
    base_level, base_disproven, base_disprove_note = _apply_outreach_logs(base_level, company_level_logs)
    if base_disproven:
        base_level = 0

    candidates = [s for s in live_bridge_stages if s["level"] > 0]
    best = max([base_level] + [s["level"] for s in candidates], default=0)

    return {
        "level": best,
        "code": f"E{best}",
        "label": E_STAGE_LABELS[best],
        "bridges": sorted(bridge_stages, key=lambda s: (s["disproven"], -s["level"], s["bridge_handle"].lower())),
        "base_disproven": base_disproven,
        "base_disprove_note": base_disprove_note,
    }


SPEND_WEIGHT = {"L3": 3, "L2": 2, "L1": 1, "legacy_unmapped": 0}

SPONSORSHIP_EVIDENCE_TYPES = {
    "paid_sponsorship",
    "affiliate",
    "ambassador_long_term_partner",
    "event_podcast_appearance",
    "organic_mention",
    "unknown_commercial_relationship",
}
_SPONSORSHIP_TYPE_ALIASES = {
    "mention": "organic_mention",
    "organic": "organic_mention",
    "unverified": "unknown_commercial_relationship",
    "unknown": "unknown_commercial_relationship",
    "ambassador": "ambassador_long_term_partner",
    "long_term_partner": "ambassador_long_term_partner",
    "event": "event_podcast_appearance",
    "podcast": "event_podcast_appearance",
}


def canonical_sponsorship_type(value: str | None) -> str:
    normalized = (value or "").strip().casefold().replace(" / ", "_").replace("/", "_").replace("-", "_")
    normalized = _SPONSORSHIP_TYPE_ALIASES.get(normalized, normalized)
    return normalized if normalized in SPONSORSHIP_EVIDENCE_TYPES else "unknown_commercial_relationship"


def sponsorship_evidence_semantics(sponsorship) -> dict:
    """Canonical commercial typing with collection and human-review dates.

    `confidence` is extraction confidence. It is not human confirmation;
    `verified_at` is therefore populated only from `reviewed_at`.
    """
    evidence_type = canonical_sponsorship_type(sponsorship.disclosure_type)
    reviewed = sponsorship.review_status == "confirmed"
    if sponsorship.review_status == "rejected":
        commercial_status = "rejected"
    elif evidence_type == "paid_sponsorship":
        commercial_status = "confirmed_paid" if reviewed else "unreviewed_paid_observation"
    elif evidence_type == "affiliate":
        commercial_status = "confirmed_affiliate" if reviewed else "unreviewed_affiliate_observation"
    elif evidence_type == "ambassador_long_term_partner":
        commercial_status = "confirmed_long_term_partner" if reviewed else "unreviewed_long_term_partner_observation"
    else:
        commercial_status = "non_paid_or_unknown"
    return {
        "evidence_type": evidence_type,
        "source_disclosure_type": sponsorship.disclosure_type,
        "commercial_status": commercial_status,
        "confidence": sponsorship.confidence,
        "supporting_note": sponsorship.evidence_text,
        "evidence_date": sponsorship.published_at,
        "collected_at": _iso(sponsorship.created_at),
        "verified_at": _iso(sponsorship.reviewed_at),
    }


def _date_from_unknown(value) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if not value:
        return None
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", str(value))
    if not match:
        return None
    try:
        return dt.date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _is_recent(value, *, days: int = 730) -> bool:
    observed = _date_from_unknown(value)
    return bool(observed and dt.date.today() - observed <= dt.timedelta(days=days))


OPPORTUNITY_VALUE_LABELS = {
    "tier_a": "Tier A · 优先投入",
    "tier_b": "Tier B · 值得准备",
    "tier_c": "Tier C · 观察",
    "archive": "Archive · 暂不投入",
}

EXECUTION_READINESS_LABELS = {
    "contact_now": "现在联系",
    "prepare_proposal": "准备方案",
    "research_buyer_route": "补查买方/路径",
    "watch": "观察",
    "blocked": "阻塞",
}

DECISION_BUCKET_LABELS = {
    "pursue_now": "立即推进",
    "prepare": "下一批准备",
    "watch": "观察/补查",
    "archive": "归档",
}

ASIA_STATUS_LABELS = {
    "confirmed_expansion": "已确认亚洲扩张",
    "active_market": "亚洲市场已有实际动作",
    "strong_signal": "亚洲强信号",
    "lead_requiring_verification": "亚洲线索待核实",
    "no_signal_found": "本轮未发现亚洲市场信号",
    "not_reviewed": "尚未审查",
}


def canonical_decision(company: Company) -> dict:
    """One product-facing conclusion used by every company surface.

    The persisted Priority-15 judgment wins.  The rest of the longlist gets a
    conservative deterministic fallback so a missing v6 row never revives the
    legacy High/Medium/Low labels.  Relationship data affects route/readiness
    flags only; it never promotes opportunity value.
    """
    stored = getattr(company, "canonical_decision", None)
    assessment = getattr(company, "longlist_assessment", None)
    dossier = getattr(company, "research_dossier", None)
    score = (
        dossier.commercial_priority_score
        if dossier
        else assessment.commercial_priority_score
        if assessment
        else company.score_value or 0
    )
    disposition = (assessment.disposition if assessment else "") or ""

    if stored:
        opportunity_value = stored.opportunity_value
        execution_readiness = stored.execution_readiness
        decision_bucket = stored.decision_bucket
        opportunity_reason = stored.opportunity_reason_zh
        why_now = stored.why_now_zh
        what_to_sell = stored.what_to_sell_zh
        buyer = stored.buyer_summary_zh
        primary_route = stored.primary_route_summary_zh
        first_action = stored.first_action_zh
        fallback_1 = stored.fallback_1_zh
        fallback_2 = stored.fallback_2_zh
        try:
            key_unknowns = json.loads(stored.key_unknowns_json or "[]")
        except (TypeError, json.JSONDecodeError):
            key_unknowns = []
        last_verified_at = _iso(stored.last_verified_at)
        decision_source = "priority_15_review"
    else:
        if disposition == "deprioritize":
            opportunity_value = "archive"
        elif score >= 75:
            opportunity_value = "tier_b"
        elif score >= 50 or disposition in {"keep_longlist", "ecosystem_partner"}:
            opportunity_value = "tier_c"
        else:
            opportunity_value = "archive"
        if opportunity_value == "archive":
            execution_readiness, decision_bucket = "blocked", "archive"
        elif opportunity_value == "tier_c":
            execution_readiness, decision_bucket = "watch", "watch"
        else:
            execution_readiness, decision_bucket = "research_buyer_route", "prepare"
        opportunity_reason = (
            "已进入扩展目标库，但仍缺少足够的需求、预算或时机证据。"
            if opportunity_value != "archive"
            else "当前证据不足以支持投入 BD 研究与执行时间。"
        )
        why_now = "当前只有初步时机线索；联系前需补充可验证的商业动作。"
        what_to_sell = "先验证具体分发需求与预算归属，再决定是否制作 Mango 方案。"
        named = next((operator for operator in company.operators if (operator.name or "").strip()), None)
        buyer = f"{named.name} · {named.role or '职责待核实'}" if named else "相关预算负责人尚未识别"
        best_route = next((route for route in company.contact_routes if route.is_best), None)
        primary_route = best_route.label if best_route else "正式商业渠道尚待确认"
        first_action = "补查近期商业动作、相关预算负责人和一条可验证的正式渠道。"
        fallback_1 = "检查官网 partnership、affiliate、community 或 developer program。"
        fallback_2 = "从历史 creator / media 合作证据反查采购流程，不默认请求引荐。"
        key_unknowns = ["真实分发需求与时机", "预算负责人", "可执行正式渠道"]
        last_verified_at = _iso(
            assessment.last_verified_at if assessment and assessment.last_verified_at else company.last_verified_at
        )
        decision_source = "conservative_longlist_fallback"

    asia = getattr(company, "asia_profile", None)
    asia_status = asia.asia_interest_status if asia else "not_reviewed"
    live_sponsorships = [s for s in company.sponsorships if s.review_status != "rejected"]
    commercial_types = {"paid_sponsorship", "affiliate", "ambassador_long_term_partner"}
    confirmed_commercial = any(
        canonical_sponsorship_type(s.disclosure_type) in commercial_types and s.review_status == "confirmed"
        for s in live_sponsorships
    )
    confirmed_gtm = any((e.fact_status or "").casefold() == "confirmed" for e in company.commercial_evidence)
    named_operator = bool(
        (dossier and dossier.operator and (dossier.operator.name or "").strip())
        or any((operator.name or "").strip() for operator in company.operators)
    )
    official_route = next(
        (
            route for route in sorted(
                company.contact_routes,
                key=lambda item: (0 if item.is_best else 1, item.fallback_order or 99, item.id),
            )
            if route.evidence_url and route.route_type in {"Direct", "Cold"}
        ),
        None,
    )
    material_paths = [path for path in company.intro_paths if is_material_intro_path(path)]
    # No current schema row proves Mango employment plus target-company
    # employment.  Keep this strict-false until an attributed employment edge
    # exists; an X follow path must never masquerade as an employee route.
    has_confirmed_employee_path = False

    flags = {
        "needs_buyer": not named_operator,
        "needs_route": not bool(company.contact_routes or channel_access(company)["available"]),
        "has_confirmed_spend_gtm": bool(confirmed_commercial or confirmed_gtm),
        "has_asia_signal": asia_status in {"confirmed_expansion", "active_market", "strong_signal"},
        "has_mango_employee_path": has_confirmed_employee_path,
        "has_official_direct_channel": bool(official_route),
        "has_historical_creator_partnership": any(
            canonical_sponsorship_type(s.disclosure_type) in commercial_types for s in live_sponsorships
        ),
        "has_secondary_research_path": any((p.hop_count or 0) >= 2 for p in material_paths),
    }
    return {
        "opportunity_value": opportunity_value,
        "opportunity_value_label": OPPORTUNITY_VALUE_LABELS[opportunity_value],
        "execution_readiness": execution_readiness,
        "execution_readiness_label": EXECUTION_READINESS_LABELS[execution_readiness],
        "decision_bucket": decision_bucket,
        "decision_bucket_label": DECISION_BUCKET_LABELS[decision_bucket],
        "opportunity_reason": opportunity_reason,
        "why_now": why_now,
        "what_to_sell": what_to_sell,
        "buyer": buyer,
        "primary_route": primary_route,
        "first_action": first_action,
        "fallback_1": fallback_1,
        "fallback_2": fallback_2,
        "key_unknowns": key_unknowns,
        "asia_status": asia_status,
        "asia_status_label": ASIA_STATUS_LABELS[asia_status],
        "flags": flags,
        "decision_source": decision_source,
        "last_verified_at": last_verified_at,
        "internal_sort_score": score,
    }


def opportunity_priority(company: Company) -> dict:
    """Transparent opportunity gates for Solomon's scarce execution time.

    High is intentionally conjunctive: a recent spend/business signal,
    service fit, relevant operator, explicit next step and clearly typed
    route must all exist. A cold route is valid; it simply remains cold.
    Weak graph/funding/follower signals never satisfy a gate by themselves.
    """
    reach = reachability_summary(company)
    spend_weight = SPEND_WEIGHT.get(company.spend_evidence_level, 0)
    # Rejected evidence (a human determined it wasn't real) never counts
    # toward the priority reasoning.
    live_sponsorships = [s for s in company.sponsorships if s.review_status != "rejected"]
    sponsor_count = len(live_sponsorships)
    commercial_sponsorships = [
        s for s in live_sponsorships
        if canonical_sponsorship_type(s.disclosure_type)
        in {"paid_sponsorship", "affiliate", "ambassador_long_term_partner"}
    ]
    paid = [s for s in live_sponsorships if canonical_sponsorship_type(s.disclosure_type) == "paid_sponsorship"]
    paid_count = len(paid)
    confirmed_paid_count = sum(1 for s in paid if s.review_status == "confirmed")
    unreviewed_paid_count = sum(1 for s in paid if s.review_status == "unreviewed")

    operator, operator_status = best_relevant_operator(company)
    access = channel_access(company)
    readiness = intro_readiness(company)
    # E1 is only a mutual-follow verification reference.  It is not a warm
    # route and cannot satisfy the High opportunity route gate.  This matches
    # suggested_bridge_action(), which promotes only E2+ evidence into its
    # relationship-candidate comparison and keeps E1 in verification_queue.
    bridge_candidates = [stage for stage in _live_bridge_stages(company) if stage["level"] >= 2]
    if readiness["level"] >= 3:
        route_type = "confirmed_intro"
    elif bridge_candidates:
        route_type = "warm_candidate"
    elif access["available"]:
        route_type = "cold"
    else:
        route_type = "none"

    recent_confirmed_paid = [s for s in paid if s.review_status == "confirmed" and _is_recent(s.published_at)]
    recent_high_quality_paid_observations = [
        s for s in paid
        if s.review_status == "unreviewed"
        and (s.confidence or 0) >= 0.75
        and bool((s.content_url or "").startswith(("http://", "https://")))
        and _is_recent(s.published_at)
    ]
    recent_active_spend = bool(
        company.spend_evidence_level == "L3"
        and company.spend_mechanism_level == "active_creator_or_partner_budget"
        and _is_recent(company.last_verified_at)
    )
    strong_business_signal = bool(recent_confirmed_paid or recent_high_quality_paid_observations or recent_active_spend)
    raw = _company_raw(company)
    fit_text = " ".join(
        str(value or "")
        for value in (
            company.why_now,
            company.buyer_or_route,
            raw.get("collaboration_angle"),
            raw.get("recommended_offer"),
        )
    ).strip().casefold()
    fit_terms = (
        "creator", "campaign", "marketing", "partnership", "affiliate", "community", "kol",
        "influencer", "podcast", "residency", "创作者", "活动", "市场", "合作", "社区", "提案",
    )
    fit = bool(company.category and any(term in fit_text for term in fit_terms))
    relevant_operator = operator_status["level"] >= 3
    action = min(company.action_items, key=lambda item: item.execution_wave or 99, default=None)
    explicit_next_step = bool(
        action
        and (action.primary_next_action or "").strip()
        and ((action.fallback or "").strip() or route_type != "none")
    )
    clear_route = route_type != "none"
    gates = {
        "recent_business_or_spend_signal": strong_business_signal,
        "mango_service_fit": fit,
        "relevant_operator": relevant_operator,
        "explicit_next_step": explicit_next_step,
        "clear_route_type": clear_route,
    }

    reasons = []
    if confirmed_paid_count:
        reasons.append(f"已有 {confirmed_paid_count} 条人工复核的付费赞助证据——可直接作为 campaign 提案依据")
    if unreviewed_paid_count:
        reasons.append(f"有 {unreviewed_paid_count} 条公开付费赞助观察待人工复核——可作为提案线索，不等同于已确认事实")
    if spend_weight >= 2:
        reasons.append(f"预算证据等级 {company.spend_evidence_level}")
    elif company.priority_tier in ("A", "B"):
        reasons.append(f"历史优先级评级 {company.priority_tier}")
    if sponsor_count and not paid_count:
        reasons.append(f"已记录 {sponsor_count} 条创作者赞助观察记录（非付费）")
    if not reasons:
        reasons.append("目前没有预算或赞助证据支持商业价值判断")
    reasons.append(f"路径类型：{route_type}；关系证据与渠道状态分别呈现")
    missing = [name for name, passed in gates.items() if not passed]
    if missing:
        reasons.append("High 尚缺条件：" + "、".join(missing))

    if all(gates.values()):
        label = "High"
    elif strong_business_signal or spend_weight >= 2 or commercial_sponsorships or company.priority_tier in ("A", "B"):
        label = "Medium"
    else:
        label = "Low"

    return {
        "label": label,
        "reachability": reach,
        "reasons": reasons,
        "gates": gates,
        "route_type": route_type,
        "channel_access": access,
        "intro_readiness": readiness,
        "operator_relevance": operator_status,
        "best_operator": operator,
    }


def _live_outreach_logs(company: Company) -> list:
    """Return non-void execution events in deterministic time order."""
    return sorted(
        (log for log in (company.outreach_logs or []) if not log.voided),
        key=lambda log: (log.occurred_at or dt.datetime.min, log.id or -1),
    )


def _follow_up_due(log, today: dt.date | None = None) -> bool:
    if not log or not log.next_follow_up_date:
        return False
    try:
        return dt.date.fromisoformat(log.next_follow_up_date) <= (today or dt.date.today())
    except ValueError:
        # The legacy column is deliberately free-typed. Bad historical
        # input must not make the entire cockpit unusable.
        return False


def _operator_reference(operator: Operator | None) -> str:
    """Human-readable operator identity without ever emitting `@None`."""
    if not operator:
        return "待确认的目标负责人"
    handle = (operator.x_handle or "").strip()
    name = (operator.name or "").strip()
    if handle and name:
        return f"@{handle}（{name}）"
    if handle:
        return f"@{handle}"
    if name:
        return name
    return "已记录但身份信息待补全的负责人"


def _operator_verification_note(operator: Operator | None) -> str:
    """Describe identity/role review without confusing freshness for QA."""
    if not operator:
        return "（身份/职位待人工复核）"
    identity_verified = bool(operator.identity_human_verified_at)
    role_verified = bool(operator.role_human_verified_at)
    if identity_verified and role_verified:
        return "（身份与职位均已人工复核）"
    if identity_verified:
        return "（身份已人工复核，职位待人工复核）"
    if role_verified:
        return "（职位已人工复核，身份待人工复核）"
    if operator.identity_confirmed:
        return "（官网职位与 X 账号已双源匹配，待人工复核）"
    return "（身份/职位待人工复核）"


def _outreach_subject(company: Company, log) -> str:
    """Resolve an attributed execution event to a concrete person."""
    if log.bridge_id is not None:
        bridge = next(
            (
                bridge
                for operator in company.operators
                for bridge in (getattr(operator, "bridges", None) or [])
                if bridge.id == log.bridge_id
            ),
            None,
        )
        if bridge:
            handle = (bridge.bridge_handle or "").strip()
            name = (bridge.bridge_name or "").strip()
            if handle and name:
                return f"@{handle}（{name}）"
            if handle:
                return f"@{handle}"
            if name:
                return name
    if log.operator_id is not None:
        operator = next((o for o in company.operators if o.id == log.operator_id), None)
        if operator:
            return _operator_reference(operator)
    contacted = (log.contacted_who or "").strip()
    return contacted or "当前联系人"


def _continuation_from_latest_log(
    company: Company,
    latest_log,
    *,
    live_candidates: list[dict],
    direct: dict | None,
    fallback_text: str,
    base_status: str,
) -> tuple[str, list[dict]] | None:
    """Map the latest real execution event to its correct next action.

    This is deliberately event-to-next-action logic rather than a generic
    funnel rank. It stops a completed first touch from rendering again as
    "直接联系" and keeps waiting, follow-up, meeting, proposal, and closed
    states explicit. The full timeline remains available for audit.
    """
    if not latest_log:
        return None

    stage = latest_log.stage
    who = _outreach_subject(company, latest_log)
    due = _follow_up_due(latest_log)
    follow_up_date = (latest_log.next_follow_up_date or "").strip()
    date_phrase = f"（计划跟进日：{follow_up_date}）" if follow_up_date else ""

    if stage == "contact_attempted":
        if due:
            return "需要跟进", [
                {
                    "kind": "follow_up",
                    "label": "首次触达后的跟进",
                    "detail": f"已完成对 {who} 的首次触达且已到跟进日{date_phrase}；发送一次简短、有新增价值的跟进，不要重复首触达话术。",
                }
            ]
        return "等待回复", [
            {
                "kind": "wait_reply",
                "label": "等待首次触达回复",
                "detail": (
                    f"已完成对 {who} 的首次触达，当前等待回复{date_phrase}；在跟进日前不要重复发送。"
                    if follow_up_date
                    else f"已完成对 {who} 的首次触达，当前等待回复；先补一个明确跟进日期，不要重复发送首触达。"
                ),
            }
        ]

    if stage == "connector_replied":
        return "需要跟进", [
            {
                "kind": "qualify_connector",
                "label": "确认 connector 关系与意愿",
                "detail": f"{who} 已回复；继续确认 TA 是否真实认识目标负责人、最近是否有联系，以及是否愿意引荐，不要重新发送首次询问。",
            }
        ]

    if stage == "relationship_confirmed":
        return "需要跟进", [
            {
                "kind": "request_intro_consent",
                "label": "确认引荐意愿",
                "detail": f"{who} 已确认真实认识目标方；下一步确认是否愿意引荐，并给出可直接转发的三句话合作背景。",
            }
        ]

    if stage == "relationship_rejected":
        alternatives = "、".join(f"@{candidate['bridge_handle']}" for candidate in live_candidates)
        if alternatives:
            return "等待内部核实", [
                {
                    "kind": "switch_connector",
                    "label": "切换 connector 候选人",
                    "detail": f"已记录 {who} 与目标方的关系不成立，不再重复询问；改为内部核实其余候选人：{alternatives}。",
                }
            ]
        if direct:
            return "需要跟进", [
                {
                    "kind": "switch_to_direct",
                    "label": "切换到直接渠道",
                    "detail": f"已记录 {who} 不是有效关系路径；停止沿该路径推进，改走已核实开放的 X 私信 @{direct['handle']}。",
                }
            ]
        return ("本周准备" if base_status != "暂缓" else "暂缓"), [
            {
                "kind": "research_new_route",
                "label": "寻找新路径",
                "detail": f"已记录 {who} 与目标方的关系不成立；不再重复询问，转而补查新的负责人、creator 合作方或公司官方渠道。",
            }
        ]

    if stage == "intro_accepted":
        return "需要跟进", [
            {
                "kind": "enable_intro",
                "label": "交付可转发引荐材料",
                "detail": f"{who} 已同意引荐；立即提供可转发的三句话背景、明确 offer 和目标联系人，请 TA 确认引荐时间，不要再重复询问意愿。",
            }
        ]

    if stage == "intro_made":
        if due:
            return "需要跟进", [
                {
                    "kind": "follow_up_intro",
                    "label": "跟进已发出的引荐",
                    "detail": f"{who} 的引荐已发出且已到跟进日{date_phrase}；优先在原线程做一次轻量跟进，不要重新发起引荐。",
                }
            ]
        return "等待回复", [
            {
                "kind": "wait_target_reply",
                "label": "等待目标方回复",
                "detail": (
                    f"{who} 已发出引荐，当前等待目标方回复{date_phrase}；在跟进日前不另起新线程。"
                    if follow_up_date
                    else f"{who} 已发出引荐，当前等待目标方回复；补一个明确跟进日期，在此之前不另起新线程。"
                ),
            }
        ]

    if stage == "target_replied":
        return "需要跟进", [
            {
                "kind": "qualify_target",
                "label": "确认需求并约会",
                "detail": "目标方已回复；在原线程确认预算、时间线和 campaign 目标，并推进一次 20 分钟需求沟通。",
            }
        ]

    if stage == "meeting_booked":
        return "本周准备", [
            {
                "kind": "prepare_meeting",
                "label": "准备已预约会议",
                "detail": f"会议已预约{date_phrase}；准备一页会议简报：目标、预算假设、相关 creator 案例、三个待确认问题和明确会后动作。",
            }
        ]

    if stage == "proposal_requested":
        return "需要跟进", [
            {
                "kind": "send_proposal",
                "label": "制作并发送提案",
                "detail": "目标方已索要提案；按已确认需求制作 campaign 方案、creator shortlist、预算区间和下一次决策时间，并在约定日期内发送。",
            }
        ]

    if stage == "rejected":
        # A connector declining an intro request is not the target company
        # rejecting Mango. Do not close the whole opportunity: first try a
        # different connector, then a truly independent direct channel.
        if latest_log.bridge_id is not None:
            alternatives = [
                candidate
                for candidate in live_candidates
                if candidate["bridge_id"] != latest_log.bridge_id
            ]
            if alternatives:
                names = "、".join(f"@{candidate['bridge_handle']}" for candidate in alternatives)
                return "等待内部核实", [
                    {
                        "kind": "switch_connector",
                        "label": "切换 connector 候选人",
                        "detail": f"{who} 已拒绝本次引荐请求；不再向同一人重复请求，改为内部核实其他候选人：{names}。",
                    }
                ]
            if direct:
                return "需要跟进", [
                    {
                        "kind": "switch_to_direct",
                        "label": "切换到独立直接渠道",
                        "detail": f"{who} 已拒绝本次引荐请求；这不代表目标方已拒绝，停止向该 connector 重复请求，改走独立 X 私信 @{direct['handle']}。",
                    }
                ]
            return ("本周准备" if base_status != "暂缓" else "暂缓"), [
                {
                    "kind": "research_new_route",
                    "label": "寻找新路径",
                    "detail": f"{who} 已拒绝本次引荐请求；不把它误记为公司拒绝，转而补查新的 connector、目标负责人或官方渠道。",
                }
            ]
        return "放弃", [
            {
                "kind": "close_outreach",
                "label": "停止主动外联",
                "detail": "目标方已拒绝；停止当前主动跟进，记录拒绝原因与可重启条件，仅在出现新预算、岗位、产品发布或合作信号时重新评估。",
            }
        ]

    if stage == "no_response":
        if follow_up_date and not due:
            return "等待回复", [
                {
                    "kind": "wait_follow_up_date",
                    "label": "等待既定跟进日",
                    "detail": f"已记录 {who} 暂无回复；等待至 {follow_up_date}，届时只做一次带新增价值的跟进，当前不重复首触达。",
                }
            ]
        same_direct_dm = bool(
            direct
            and latest_log.bridge_id is None
            and latest_log.operator_id is not None
            and latest_log.operator_id == direct.get("operator_id")
            and latest_log.contact_channel == "dm"
        )
        if same_direct_dm:
            alternative = (
                "若仍无回复，不要再次建议同一 X 私信；改查官方 email/contact form 或新的 connector，"
                "若没有新信息或路径，则按停止日期转入观察。"
            )
        elif direct:
            alternative = f"若仍无回复，改走独立 X 私信 @{direct['handle']}。"
        else:
            alternative = f"若仍无回复，{fallback_text}"
        return "需要跟进", [
            {
                "kind": "follow_up_no_response",
                "label": "一次有新增价值的跟进",
                "detail": f"已记录 {who} 暂无回复；执行一次有新增价值的短跟进（引用新案例、明确 offer 或具体问题），并设置停止日期。{alternative}",
            }
        ]

    return None


def suggested_bridge_action(company: Company) -> dict:
    """The stored ActionItem rows predate the bridge-search/interaction-
    evidence work -- their text can name a stale connector while the
    drawer shows fresher data. This surfaces the current best plan as a
    separate, clearly-labeled, freshly-computed suggestion, always in
    sync with live data.

    2026-08-28, section III correction: this used to auto-pick a single
    "best" bridge (by follower count, or a bare "too many, pick one
    yourself" punt above 5 candidates) and treated an open DM as either
    better than or a total substitute for a bridge. Both were wrong.
    Follower count isn't a relevance signal (GAIB's top-follower bridge
    was a generic crypto-trading KOL with zero interaction evidence
    toward the actual target); Sapien's case made the second bug
    concrete: @roxinft (zero public interaction found) was being
    suggested while @calchulus (a real, if 5-year-old, personal exchange
    found) sat unmentioned -- neither should be auto-picked over the
    other. So this never returns a single name. It returns up to three
    tracks in parallel, each independently gated on real evidence: `warm`
    (a comparison of up to 3 bridge candidates ranked by relationship_stage,
    never by followers alone, each carrying its own evidence/disprove
    status and a specific internal-verification question), `direct` (the
    contactability fallback -- an open DM, tracked completely separately
    from relationship warmth), and `fallback` (what to do if neither
    produces anything, scaled to whether this is even worth the effort
    per opportunity_priority). A caller wanting a single compact string
    for a list view can use the returned `text` summary, which always
    describes all tracks that exist rather than silently picking one.

    The latest non-void OutreachLog then advances that route plan into a
    continuation plan. Every company receives a concrete action: lower-
    confidence companies without a channel get an explicit research/
    defer plan rather than `None`, so no list or drawer silently loses its
    next action."""
    opp = opportunity_priority(company)
    outreach_logs = list(company.outreach_logs or [])
    bridges = [b for o in company.operators for b in getattr(o, "bridges", [])]
    bridge_stages = [s for s in deduplicate_bridge_stages(bridges, outreach_logs) if not s["disproven"]]
    # E1 (mutual follow only, whether checked-negative or unchecked) is a
    # verification queue, not a warm-intro ranking. Public interaction or
    # stronger evidence is required to enter the relationship-candidate
    # comparison, and even that remains unverified until a human log says so.
    relationship_candidates = [s for s in bridge_stages if s["level"] >= 2][:3]
    verification_queue = [s for s in bridge_stages if s["level"] == 1][:3]
    live_candidates = relationship_candidates

    direct = direct_channel_status(company)
    # Any operator with a name on file counts as "identified" here -- see
    # direct_channel_status's 2026-08-28 fix for why identity_confirmed
    # must be a displayed fact, not a gate: Cursor's Lee Robinson has
    # can_dm=True but identity_confirmed=False, and this lookup used to
    # require identity_confirmed too, so the text claimed "no known
    # operator at all" for a company with a real name on file.
    known_operator = next((o for o in company.operators if o.name or o.x_handle), None)
    high_value = opp["label"] == "High"

    def direct_detail() -> str:
        direct_operator = next(
            (o for o in company.operators if (o.x_handle or "").strip() == direct["handle"]),
            None,
        )
        verification_note = _operator_verification_note(direct_operator)
        return f"可直接私信 @{direct['handle']}{'（' + direct['name'] + '）' if direct['name'] else ''}{verification_note} -- {direct['note']}"

    fallback_text = (
        "基于历史投放/预算证据准备一份 campaign 提案，走官方渠道或 cold outreach 敲门。"
        if high_value
        else "走官方渠道或 cold outreach，暂不作为首选目标。"
    )

    # Route availability first; current outreach state is applied below.
    # Never emit an orphan fallback step or imply a warm path where only
    # a direct channel exists.
    steps: list[dict]
    recommended_status: str
    if relationship_candidates and direct:
        kind = "connector_comparison"
        names = "、".join(f"@{c['bridge_handle']}（{c['code']}）" for c in relationship_candidates)
        steps = [
            {"kind": "verify_relationship", "label": "先核实关系，再考虑引荐", "detail": f"发现公开互动/关系候选（仍非 confirmed intro）：{names}。先向 Mango owner 核实真实关系并问信息，本阶段不直接请求引荐。"},
            {"kind": "direct_contact", "label": "并行直接联系", "detail": direct_detail()},
            {"kind": "fallback", "label": "Fallback", "detail": f"若以上均无进展：{fallback_text}"},
        ]
        recommended_status = "现在联系" if high_value else "本周准备"
    elif direct and not relationship_candidates:
        kind = "direct_only"
        steps = [{"kind": "direct_contact", "label": "直接联系", "detail": direct_detail()}]
        if verification_queue:
            names = "、".join(f"@{c['bridge_handle']}" for c in verification_queue)
            steps.append(
                {
                    "kind": "verify_candidate",
                    "label": "独立核实互关候选",
                    "detail": f"{names} 仅在 verification queue，不是暖路径；可并行核实 Mango 内部是否真的认识，本阶段不请求引荐。",
                }
            )
        steps.append({"kind": "fallback", "label": "Fallback", "detail": f"若无进展：{fallback_text}"})
        recommended_status = "现在联系" if high_value else "本周准备"
    elif relationship_candidates and not direct:
        kind = "connector_only"
        names = "、".join(f"@{c['bridge_handle']}（{c['code']}）" for c in relationship_candidates)
        steps = [
            {"kind": "verify_relationship", "label": "内部核实关系候选", "detail": f"暂无直接联系渠道；先核实 {names} 的真实关系与近期联系，本阶段只问信息，不直接请求引荐。"},
            {"kind": "fallback", "label": "核实失败后的 fallback", "detail": fallback_text},
        ]
        recommended_status = "等待内部核实" if opp["label"] != "Low" else "暂缓"
    elif verification_queue and not direct:
        kind = "verification_queue_only"
        names = "、".join(f"@{c['bridge_handle']}（{c['interaction_verification_status']}）" for c in verification_queue)
        steps = [
            {
                "kind": "verify_candidate",
                "label": "核实候选，不请求引荐",
                "detail": f"以下仅为互关候选/核查队列，不是暖路径：{names}。先确认 Mango 内部是否真的认识，再了解目标背景。",
            },
            {"kind": "fallback", "label": "Fallback", "detail": fallback_text},
        ]
        recommended_status = "等待内部核实" if opp["label"] != "Low" else "暂缓"
    else:
        who = (
            f"已识别负责人 {_operator_reference(known_operator)}"
            f"{_operator_verification_note(known_operator)}，但没有已知的联系方式（无互关候选人、无私信开放记录）"
            if known_operator
            else "目前没有任何已知关系路径或负责人"
        )
        if high_value:
            kind = "cold_high_value"
            steps = [
                {"kind": "find_operator", "label": "找正确 operator", "detail": who},
                {"kind": "prepare_offer", "label": "准备具体 offer", "detail": "基于历史投放/预算证据准备一份 campaign 提案"},
                {"kind": "find_new_connector", "label": "寻找新的 connector 或官方渠道", "detail": "排查是否有其他曾与这家公司合作过的 creator，或寻找新的连接人线索；否则走官方渠道"},
            ]
            recommended_status = "本周准备"
        else:
            kind = "research_only"
            evidence_detail = (
                "先复核现有预算/赞助线索，并补齐一名实际负责 growth、partnerships 或 creator marketing 的 operator。"
                if opp["label"] == "Medium"
                else "先补充可核验的付费投放、现金 bounty、creator 合作或明确预算信号；在证据出现前不投入主动外联。"
            )
            steps = [
                {"kind": "research_evidence", "label": "补齐商业与负责人证据", "detail": evidence_detail},
                {
                    "kind": "research_route",
                    "label": "记录可执行 fallback",
                    "detail": f"{who}；补查官网团队/partner 页面、历史 creator 合作方和公司官方联系渠道。",
                },
            ]
            recommended_status = "本周准备" if opp["label"] == "Medium" else "暂缓"

    live_logs = _live_outreach_logs(company)
    latest_log = live_logs[-1] if live_logs else None
    continuation = _continuation_from_latest_log(
        company,
        latest_log,
        live_candidates=relationship_candidates or verification_queue,
        direct=direct,
        fallback_text=fallback_text,
        base_status=recommended_status,
    )
    if continuation:
        recommended_status, steps = continuation

    text = "；".join(f"{i + 1}. {s['detail']}" for i, s in enumerate(steps))
    return {
        "kind": kind,
        # Backwards-compatible key: only E2+ relationship candidates live
        # here now. E1 mutual-follow rows moved to verification_queue.
        "warm": relationship_candidates or None,
        "relationship_candidates": relationship_candidates,
        "verification_queue": verification_queue,
        "direct": direct,
        "route_type": opp["route_type"],
        "channel_access": opp["channel_access"],
        "intro_readiness": opp["intro_readiness"],
        "steps": steps,
        "text": text,
        "recommended_execution_status": recommended_status,
        "latest_outreach_stage": latest_log.stage if latest_log else None,
        "latest_outreach_at": latest_log.occurred_at.isoformat() if latest_log and latest_log.occurred_at else None,
    }


def execution_priority(company: Company) -> dict:
    """Axis 3 (section V): what's worth working on TODAY. Neither
    opportunity_priority (is this company valuable) nor relationship_stage
    (how warm is the relationship) alone answers that -- a company can be
    Opportunity: High with no identified operator and no known path at
    all, which is real (see opportunity_priority's own docstring) but
    means there is, today, nothing concrete to execute. The home page's
    "当下最值得联系" list was silently sorting on raw Opportunity Priority
    (50 of 89 companies were "High"), which is a "worth chasing
    eventually" list, not a "do this today" list -- this is what actually
    drives that list now."""
    opp = opportunity_priority(company)
    stage = relationship_stage(company)
    plan = suggested_bridge_action(company)
    # A named operator on file is "identified" for execution purposes even
    # before a human confirms their identity is exactly right -- see
    # direct_channel_status's 2026-08-28 fix for the same distinction.
    has_operator = any(o.name for o in company.operators)
    has_direct_channel = bool(plan and plan.get("direct"))
    has_verified_warm_path = stage["level"] >= 4
    has_unverified_candidate = bool(
        plan and (plan.get("relationship_candidates") or plan.get("verification_queue"))
    ) and not has_verified_warm_path
    has_actionable_channel = has_direct_channel or has_verified_warm_path
    has_offer_ready = any(
        s.disclosure_type == "paid_sponsorship" and s.review_status != "rejected" for s in company.sponsorships
    )
    has_confirmed_offer_evidence = any(
        s.disclosure_type == "paid_sponsorship" and s.review_status == "confirmed" for s in company.sponsorships
    )

    score = 0
    reasons = []
    if opp["label"] == "High":
        score += 2
        reasons.append("商业优先级：高")
    elif opp["label"] == "Medium":
        score += 1
        reasons.append("商业优先级：中")
    else:
        reasons.append("商业优先级：低")

    if has_actionable_channel:
        score += 2
        if has_verified_warm_path and has_direct_channel:
            reasons.append("已有已验证关系路径，也有独立直接联系渠道")
        elif has_verified_warm_path:
            reasons.append("已有已验证关系路径")
        else:
            reasons.append("已有独立直接联系渠道（不代表存在暖关系）")
    elif has_unverified_candidate:
        score += 1
        reasons.append("有引荐候选人，但关系与引荐意愿均待内部核实")
    elif has_operator:
        score += 1
        reasons.append("已识别负责人，但暂无可执行的联系路径")
    else:
        reasons.append("尚未识别负责人，也没有已知路径")

    if has_offer_ready:
        score += 1
        reasons.append(
            "已有人工复核的付费赞助先例，可直接作为提案依据"
            if has_confirmed_offer_evidence
            else "已有公开付费赞助观察，可作为提案线索（待人工复核）"
        )

    live_logs = _live_outreach_logs(company)
    latest_log = live_logs[-1] if live_logs else None

    # The action plan owns the current execution transition. Reading its
    # recommendation here prevents labels such as "现在联系" from being
    # paired with plan copy saying "已触达，等待回复". It also avoids
    # abandoning an entire company merely because one connector candidate
    # was disproven -- that plan now explicitly switches route instead.
    label = plan.get("recommended_execution_status") if plan else None
    if not label:  # defensive compatibility with a caller supplying an old plan
        if score >= 4 and has_actionable_channel:
            label = "现在联系"
        elif has_unverified_candidate and not has_actionable_channel:
            label = "等待内部核实"
        elif score >= 2:
            label = "本周准备"
        else:
            label = "暂缓"
    if latest_log:
        reasons.append(f"最新执行记录：{latest_log.stage}；动态下一步状态：{label}")

    return {
        "label": label,
        "score": score,
        "reasons": reasons,
        "opportunity": opp,
        "relationship_stage": stage,
        "action_plan": plan,
    }


def creator_campaign_fit(creator, company: Company | None = None) -> dict:
    """Explain whether a creator can perform this company's actual brief.

    A generic ``AI`` overlap is deliberately insufficient.  When a company is
    provided, the result includes the matched capability, the exact source
    fields that caused the match, and the proposed role-specific deliverable.
    """
    reasons = []
    # ``RateCard.is_confident`` means the free-text amount was parsed with
    # confidence. It is not a human/current quote confirmation.
    has_quote = any(rc.is_confident for rc in creator.rate_cards)
    reasons.append("已有可用的历史数值报价记录" if has_quote else "暂无可用数值报价——需要先联系")

    if company:
        prior = [s for s in creator.sponsorships if s.company_id == company.company_id] if hasattr(creator, "sponsorships") else []
        if prior:
            paid = [s for s in prior if s.disclosure_type == "paid_sponsorship"]
            confirmed_paid = [s for s in paid if s.review_status == "confirmed"]
            unreviewed_paid = [s for s in paid if s.review_status != "confirmed"]
            non_paid = [s for s in prior if s.disclosure_type != "paid_sponsorship"]
            if confirmed_paid:
                reasons.append(f"有 {len(confirmed_paid)} 条经人工复核的 {company.name} 付费赞助记录")
            if unreviewed_paid:
                reasons.append(f"有 {len(unreviewed_paid)} 条 {company.name} 公开付费赞助观察，尚待人工复核")
            if non_paid:
                reasons.append(f"有 {len(non_paid)} 条 {company.name} 提及/affiliate 观察，不代表已确认合作")

        company_fit = match_creator_to_campaign(creator, company)
        for capability in company_fit["matched_capabilities"][:2]:
            evidence = "；".join(capability["evidence"])
            reasons.append(f"可承担「{capability['label']}」：{evidence}")
        if company_fit["generic_only"]:
            reasons.append("只有通用 AI/Tech 信号，未命中本公司的核心交付能力")
    else:
        company_fit = None

    return {"has_quote": has_quote, "reasons": reasons, "company_fit": company_fit}


# Labels deliberately say "public connection", never "relationship" or
# "knows" -- a 1-hop X follow/mention is graph adjacency, not a personal
# acquaintance. Overclaiming this distinction was flagged as a real risk
# in the source methodology report (a company adjacent by public mention
# alone must not be described as "has a direct relationship with").
RELATIONSHIP_RANK = {
    "direct": ("直接公开关联", 4),
    "secondary": ("经由连接人的单向关注路径", 3),
    "third": ("较弱的多级关联", 2),
}
RELATIONSHIP_RANK_BY_LEVEL = {0: ("较弱的公开关联", 1), 1: ("经由连接人的单向关注路径", 3), 2: ("较弱的多级关联", 2)}


def relationship_strength(path: IntroPath | None) -> dict:
    if path is None or not is_material_intro_path(path):
        return {"label": "仅为冷启动联系", "level": 0, "reason": "没有观察到路径"}
    if not path.graph_reachable:
        return {"label": "较弱 / 未确认", "level": 1, "reason": path.human_intro_status or "仅有研究线索，尚未核实关系"}
    if path.degree_label in RELATIONSHIP_RANK:
        label, level = RELATIONSHIP_RANK[path.degree_label]
    else:
        # No degree_label on record -- fall back to the hop-count-implied
        # degree (see _degree_rank) rather than assuming the weakest tier.
        label, level = RELATIONSHIP_RANK_BY_LEVEL.get(_degree_rank(path), ("Weak public connection", 1))
    reason = path.path_labels or path.human_intro_status or ""
    return {"label": label, "level": level, "reason": reason}


def creator_business_relationships(creator) -> list[dict]:
    """One row per company this creator has sponsorship evidence for,
    aggregated from raw SponsorshipEvidence observations -- never
    fabricated, never rolled up past what's actually on file. Section VII
    (2026-08-28): a single sponsorship is a commercial signal only, never
    proof the creator can introduce the brand's operator personally --
    this is the raw material for creator_connector_candidacy() to screen,
    not a claim about connector potential by itself."""
    by_company: dict[str, list] = {}
    for s in creator.sponsorships:
        if s.review_status == "rejected":
            continue
        by_company.setdefault(s.company_id, []).append(s)

    rows = []
    for company_id, evs in by_company.items():
        paid_observations = [e for e in evs if e.disclosure_type == "paid_sponsorship"]
        confirmed_paid = [e for e in paid_observations if e.review_status == "confirmed"]
        company = evs[0].company
        rows.append(
            {
                "company_id": company_id,
                "company_name": company.name if company else company_id,
                "disclosure_types": sorted({e.disclosure_type for e in evs}),
                "observation_count": len(evs),
                "is_repeat": len(evs) >= 2,
                "has_paid_observation": bool(paid_observations),
                "has_confirmed_paid_evidence": bool(confirmed_paid),
                "confirmed_paid_count": len(confirmed_paid),
                "is_repeat_confirmed_paid": len(confirmed_paid) >= 2,
                "evidence": [
                    {
                        "content_url": e.content_url,
                        "published_at": e.published_at,
                        "disclosure_type": e.disclosure_type,
                        "review_status": e.review_status,
                    }
                    for e in evs
                ],
            }
        )
    rows.sort(key=lambda r: (-r["confirmed_paid_count"], -r["observation_count"]))
    return rows


def creator_connector_candidacy(creator) -> dict:
    """Whether a creator is plausibly worth checking as a *business*
    connector -- not a creator recommendation (see rank_suggested_creators)
    and not a claim that they will introduce anyone. Section VII's
    explicit warning: one sponsorship only proves a commercial
    relationship existed, never that the creator knows or can reach the
    brand's operator personally. This only screens the pool down to
    "repeat paid precedent + Mango already has a usable quote/contact" --
    real interaction evidence between the creator and the sponsor's own
    operator (the strongest signal, same idea as bridge_relationship_stage)
    is not checked here; that requires the same kind of Rapid X
    interaction-check work as Group A's bridges and was out of scope for
    this pass -- see project memory."""
    relationships = creator_business_relationships(creator)
    repeat_paid = [r for r in relationships if r["is_repeat_confirmed_paid"]]
    has_quote = quote_summary(creator)["confident_count"] > 0
    is_candidate = bool(repeat_paid) and has_quote
    return {
        "is_candidate": is_candidate,
        "repeat_paid_relationships": repeat_paid,
        "has_quote": has_quote,
        "reason": (
            "有经人工复核的重复付费合作记录，且 Mango 已有报价/联系方式，值得内部核实是否能作为业务 connector"
            if is_candidate
            else "尚不满足候选条件（需要至少一家公司的两条已复核付费合作证据，且 Mango 已能联系到本人）"
        ),
    }


def sponsor_side_intelligence_prospects(creators: list) -> tuple[list[dict], list[dict]]:
    """Creators/media worth asking for sponsor-side context, never auto-intros.

    This layer answers a different question from the X bridge graph: a
    creator with multiple documented sponsor observations may know how a
    company buys, but the evidence does not establish that they know the
    budget owner or would introduce Mango. Existing Mango contact details
    and a quote lower the cost of an *information* conversation only.
    """
    prospects: list[dict] = []
    media_channels: list[dict] = []
    for creator in creators:
        observations = [
            item for item in (creator.sponsorships or [])
            if item.review_status != "rejected"
            and canonical_sponsorship_type(item.disclosure_type)
            in {"paid_sponsorship", "affiliate", "ambassador_long_term_partner"}
        ]
        by_company: dict[str, list] = {}
        for item in observations:
            by_company.setdefault(item.company_id, []).append(item)
        if len(by_company) < 2:
            continue

        quote = quote_summary(creator)
        contact_types = sorted({contact.method_type for contact in (creator.contacts or []) if contact.value})
        has_quote = quote["confident_count"] > 0
        has_pricing_record = quote["deliverable_count"] > 0
        has_contact = bool(contact_types)
        companies = []
        confirmed_paid_total = 0
        paid_observation_total = 0
        for company_id, items in by_company.items():
            paid = [item for item in items if canonical_sponsorship_type(item.disclosure_type) == "paid_sponsorship"]
            confirmed_paid = [item for item in paid if item.review_status == "confirmed"]
            confirmed_paid_total += len(confirmed_paid)
            paid_observation_total += len(paid)
            company = items[0].company
            companies.append(
                {
                    "company_id": company_id,
                    "company_name": company.name if company else company_id,
                    "observation_count": len(items),
                    "paid_observation_count": len(paid),
                    "confirmed_paid_count": len(confirmed_paid),
                    "evidence_types": sorted({canonical_sponsorship_type(item.disclosure_type) for item in items}),
                    "latest_evidence_date": max((item.published_at or "" for item in items), default="") or None,
                    "evidence_urls": sorted({item.content_url for item in items if item.content_url}),
                }
            )
        companies.sort(key=lambda row: (-row["confirmed_paid_count"], -row["paid_observation_count"], row["company_name"].casefold()))
        names = "、".join(row["company_name"] for row in companies[:4])
        low_cost = has_contact and has_pricing_record
        row = {
            "creator_id": creator.id,
            "creator_name": creator.display_name,
            "creator_handle": creator.primary_handle,
            "creator_class": creator.creator_class,
            "role": "media_or_community_channel" if creator.creator_class in MEDIA_CHANNEL_CLASSES else "possible_company_connector",
            "intro_readiness": "unverified",
            "mango_relationship": {
                "contact_available": has_contact,
                "contact_types": contact_types,
                "pricing_record_available": has_pricing_record,
                "numeric_quote_available": has_quote,
                "quote_min_usd": quote["quote_min_usd"],
                "quote_max_usd": quote["quote_max_usd"],
                "truth": "Operational contact/pricing history on file; personal relationship and intro willingness are not established",
            },
            "companies_potentially_unlocked": companies,
            "company_count": len(companies),
            "paid_observation_count": paid_observation_total,
            "confirmed_paid_count": confirmed_paid_total,
            "why_this_person": f"Documented commercial-type observations across {len(companies)} target companies; may provide buying-context intelligence",
            "exact_first_ask": (
                f"基于你与 {names} 相关的公开内容/合作观察，方便分享一下这类品牌通常最看重什么，以及一般由什么职能发起合作吗？"
                "这一步只请教行业/采购背景，不请求引荐。"
            ),
            "when_intro_is_appropriate": "Only after the creator confirms a current sponsor-side relationship and explicitly offers or accepts an introduction",
            "verification_gaps": [
                "Whether each observation was truly paid (unless review_status=confirmed)",
                "Whether the creator knows a current sponsor-side operator",
                "Whether that relationship is current",
                "Whether the creator is willing and conflict-free to help",
            ],
            "askability": "low_cost_information_ask" if low_cost else "relationship_building_first",
            "social_cost": "low" if low_cost else "medium",
            "fallback": "If they prefer not to discuss sponsor-side contacts, ask only for category-level campaign feedback or treat them as creator/media supply",
        }
        if creator.creator_class in MEDIA_CHANNEL_CLASSES:
            media_channels.append(row)
        elif creator.creator_class not in NON_CREATOR_CLASSES:
            prospects.append(row)

    def sort_key(row: dict):
        mango = row["mango_relationship"]
        return (
            -int(mango["contact_available"] and mango["numeric_quote_available"]),
            -int(mango["contact_available"] and mango["pricing_record_available"]),
            -row["confirmed_paid_count"],
            -row["paid_observation_count"],
            -row["company_count"],
            row["creator_name"].casefold(),
        )

    prospects.sort(key=sort_key)
    media_channels.sort(key=sort_key)
    return prospects, media_channels


def rank_suggested_creators(
    creators: list,
    company: Company,
    *,
    prior_creator_ids: set[int],
    excluded_creator_ids: set[int],
) -> tuple[list[dict], list[dict]]:
    """Filter + rank campaign-creator suggestions for one company. Returns
    (results, media_channels) -- never a single merged list, so a caller
    can't accidentally treat a media/community channel as a creator pick.

    Rules (data-integrity sprint): Non-creator/Irrelevant never appears in
    either list. Media/Community Account never appears in `results` --
    it's a distribution channel, not a creator recommendation, and comes
    back separately. Needs Review (Unknown) creators can still appear in
    `results` (e.g. a real prior sponsor with no classification yet) but
    always sort after every confidently-classified entry. Explicitly
    excluded creators (global or company-scoped) never appear at all.
    """
    results: list[dict] = []
    media_channels: list[dict] = []

    for creator in creators:
        if creator.id in excluded_creator_ids or creator.creator_class in NON_CREATOR_CLASSES:
            continue
        quoted = quote_summary(creator)
        has_relationship = creator.id in prior_creator_ids
        prior_evidence = [
            s
            for s in getattr(creator, "sponsorships", [])
            if s.company_id == company.company_id and s.review_status != "rejected"
        ]
        fit = creator_campaign_fit(creator, company)
        company_fit = fit.get("company_fit") or {}
        if quoted["confident_count"] == 0 and not has_relationship and company_fit.get("generic_only"):
            continue
        # A prior commercial observation remains useful evidence even when the
        # public creator profile is thin.  Everyone else must match at least
        # one role in the company's actual campaign profile; price and the
        # generic word AI cannot manufacture fit.
        if company_fit.get("generic_only") and not has_relationship:
            if creator.creator_class in MEDIA_CHANNEL_CLASSES:
                pass
            else:
                continue
        account = creator.social_accounts[0] if creator.social_accounts else None
        row = {
            "id": creator.id,
            "display_name": creator.display_name,
            "handle": account.handle if account else None,
            "platform": account.platform if account else None,
            "followers": account.followers if account else None,
            "creator_class": creator.creator_class,
            "prior_relationship": has_relationship,
            "prior_evidence_types": sorted({s.disclosure_type for s in prior_evidence}),
            "prior_evidence_review_statuses": sorted({s.review_status or "unreviewed" for s in prior_evidence}),
            "has_confirmed_paid_evidence": any(
                s.disclosure_type == "paid_sponsorship" and s.review_status == "confirmed"
                for s in prior_evidence
            ),
            "needs_review": creator.creator_class == "Unknown",
            "reasons": fit["reasons"],
            "company_fit": company_fit,
            "fit_level": company_fit.get("fit_level"),
            "fit_score": company_fit.get("fit_score", 0),
            "matched_capabilities": company_fit.get("matched_capabilities", []),
            "quote_min_usd": quoted["quote_min_usd"],
            "quote_max_usd": quoted["quote_max_usd"],
            "cpm_min": quoted["cpm_min"],
        }
        (media_channels if creator.creator_class in MEDIA_CHANNEL_CLASSES else results).append(row)

    results.sort(
        key=lambda r: (
            r["needs_review"],
            not r["prior_relationship"],
            -r["fit_score"],
            r["quote_min_usd"] is None,
            r["quote_min_usd"] or float("inf"),
        )
    )
    media_channels.sort(
        key=lambda r: (
            not r["prior_relationship"],
            -r["fit_score"],
            r["quote_min_usd"] is None,
        )
    )
    return results, media_channels
