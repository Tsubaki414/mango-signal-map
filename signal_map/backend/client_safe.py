"""Client-safe serialization.

One database does not mean one permission set. Everything a client can see is
built **here**, by naming each field explicitly. Nothing is filtered out in the
frontend, and nothing is filtered out by blocklist.

Why allowlist and not blocklist: a blocklist is a promise that every future
column will be remembered. It won't be. Adding ``supplier_floor_usd`` to
``Quote`` next month must not silently start shipping it to clients, and with
an allowlist it cannot -- the new column simply isn't named here.

**Never client-visible, under any circumstance:**
supplier floor and Mango's cost/margin · private contact details · supplier or
manager identity · negotiation records · outreach logs · path-1 商务触达 data
(who at Mango knows whom) · internal reachability grades · internal notes ·
unverified research · another client's feedback · risk investigations ·
rejection reasons · research sources.

The functions below take ORM objects and return plain dicts. They never take a
"fields" argument and never accept a caller-supplied projection -- the shape is
fixed here so it can be tested here.
"""

from __future__ import annotations

import json
from typing import Any

from .budget import BudgetSummary
from .models import (
    CREATOR_TIER_LABELS_ZH,
    Brief,
    CandidateItem,
    Creator,
    FeedbackEvent,
    Quote,
    RecommendationRun,
    SocialAccount,
)
from .normalize import (
    AUDIENCE_TYPE_LABELS_ZH,
    MARKET_REGION_LABELS_ZH,
    follower_tier,
)

#: Rendered wherever a number would imply a precision Mango cannot honour.
PRICE_TBD = "价格待 Mango 确认"

#: Fields that must never appear in any client payload. Not used for
#: filtering -- the allowlist already handles that -- but asserted in tests, so
#: a leak fails the suite rather than reaching a client.
FORBIDDEN_CLIENT_FIELDS = frozenset(
    {
        "internal_cost_usd",
        "internal_notes",
        "amount_usd",
        "amount",
        "amount_min",
        "amount_max",
        "raw_quote_text",
        "raw_text",
        "raw_segment",
        "contacts",
        "contact_methods",
        "value",  # ContactMethod.value
        "procurement_routes",
        "counterparty_name",
        "source_detail",
        "review_reason",
        "parse_notes",
        "source_ref",
        "source_system",
        "margin",
        "markup",
    }
)


def _primary_account(creator: Creator) -> SocialAccount | None:
    """The account a client card should lead with."""
    accounts = list(creator.accounts)
    if not accounts:
        return None
    explicit = next((a for a in accounts if a.is_primary), None)
    if explicit:
        return explicit
    return max(accounts, key=lambda a: a.followers or 0)


def _labels(values: str | None, mapping: dict[str, str]) -> list[str]:
    return [mapping.get(v.strip(), v.strip()) for v in (values or "").split(",") if v.strip()]


def _loads(raw: str | None) -> Any:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


# --- quote -------------------------------------------------------------------


def quote_public(quote: Quote | None) -> dict | None:
    """A quote as a client may see it.

    Emits a **band**, never the figure. ``amount_usd`` is what the creator
    asked Mango for -- Mango's cost -- and is deliberately absent from this
    dict entirely rather than merely unset.
    """
    if quote is None or not quote.client_visible:
        return None
    exact = quote.client_display_price
    return {
        "deliverable": quote.deliverable_raw,
        "content_format": quote.content_format,
        "platform": quote.platform,
        "quantity": quote.quantity,
        "is_package": bool(quote.is_package),
        # Exactly one of these carries a number, and only when Mango set one.
        "price_exact_usd": exact,
        "price_band": quote.client_display_band if exact is None else None,
        "price_label": (f"${exact:,.0f}" if exact is not None else quote.client_display_band)
        or PRICE_TBD,
        "status": quote.status,
        "status_label": _quote_status_label(quote),
        "note": "套餐价，不能与单条价格直接相加" if quote.is_package else None,
    }


def _quote_status_label(quote: Quote) -> str:
    return {
        "confirmed_valid": "报价已确认",
        "confirmed_may_expire": "报价已确认，可能需要更新",
        "historical": "历史成交价",
        "public": "公开报价",
        "no_quote": "对方未报价",
    }.get(quote.status, "报价待 Mango 确认")


# --- creator -----------------------------------------------------------------


def creator_card(creator: Creator, quote: Quote | None = None) -> dict:
    """The client-facing creator card.

    Carries the *basis* of every derived judgment, so a card can say
    依据：内容语言 rather than implying Mango measured something.
    """
    account = _primary_account(creator)
    followers = account.followers if account else None
    return {
        "id": creator.id,
        "name": creator.display_name,
        "handle": creator.primary_handle or (account.handle if account else None),
        "avatar_url": account.avatar_url if account else None,
        "profile_url": account.profile_url if account else None,
        "platform": account.platform if account else None,
        "followers": followers,
        "follower_tier": follower_tier(followers),
        "creator_tier": creator.creator_tier,
        "creator_tier_label": CREATOR_TIER_LABELS_ZH.get(creator.creator_tier, "待分类"),
        "verticals": _csv_list(creator.verticals),
        "languages": _csv_list(creator.languages),
        "market_region": creator.market_region,
        "market_region_label": MARKET_REGION_LABELS_ZH.get(creator.market_region or "", "市场待确认"),
        "market_region_basis": creator.market_region_basis,
        "audience_types": _csv_list(creator.audience_types),
        "audience_type_labels": _labels(creator.audience_types, AUDIENCE_TYPE_LABELS_ZH)
        or ["人群待确认"],
        "audience_types_basis": creator.audience_types_basis,
        # The quoted evidence behind the audience call. Client-safe: it is the
        # creator's own public words, not Mango research.
        "audience_evidence": creator.audience_types_evidence,
        "content_summary": account.content_summary if account else None,
        "quote": quote_public(quote),
        # Never the supplier, never the route, never who at Mango knows whom.
        "procurement": "由 Mango 对接",
    }


def _csv_list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


# --- recommendation ----------------------------------------------------------


def recommendation_public(record, creator: Creator, quote: Quote | None) -> dict:
    """One recommended creator, with all twelve judgments.

    ``rank_score`` is deliberately absent: the product rule is that no surface
    may show a bare score, so the client layer never receives one.
    """
    return {
        "rank": record.rank,
        "creator": creator_card(creator, quote),
        "axes": [
            {
                "key": axis.get("key"),
                "label": axis.get("label_zh"),
                "verdict": axis.get("verdict"),
                # ``client_detail`` when the axis provides one, never the
                # internal ``detail`` -- the budget axis's internal wording
                # quotes Mango's cost inside a prose string, which no
                # field-name allowlist would catch.
                "detail": axis.get("client_detail") or axis.get("detail"),
            }
            for axis in _loads(record.axis_scores_json)
        ],
        "matched": _loads(record.matched_preferences_json),
        "unmet": _loads(record.unmet_conditions_json),
        "missing": _loads(record.missing_data_json),
        "reasons": _loads(record.reasons_json),
        "risks": _loads(record.risk_flags_json),
        "next_step": record.next_step,
    }


def run_public(run: RecommendationRun, items: list[dict]) -> dict:
    return {
        "run_id": run.id,
        "brief_id": run.brief_id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "returned_count": run.returned_count,
        "candidate_pool_size": run.candidate_pool_size,
        # Told plainly rather than silently ignored.
        "unsupported_filters": _loads(run.unsupported_filters),
        "rule_version": run.rule_version,
        "results": items,
    }


# --- budget ------------------------------------------------------------------


def budget_public(summary: BudgetSummary) -> dict:
    """Budget as a client may see it.

    ``total_usd`` here is only ever a total of **client** prices. While Mango
    has set no client prices, that is None and the label reads 价格待 Mango 确认
    -- the internal cost total is never substituted.
    """
    client_priced = [line for line in summary.lines if line.client_visible_price is not None]
    total = round(sum(line.client_visible_price for line in client_priced), 2) if client_priced else None
    return {
        "line_count": len(summary.lines),
        "priced_line_count": len(client_priced),
        "total_usd": total,
        "total_label": (f"${total:,.0f}" if total is not None else PRICE_TBD),
        "budget_usd": summary.budget_usd,
        "needs_mango_confirmation": True if total is None else summary.needs_mango_confirmation,
        "notes": summary.notes,
    }


# --- brief / candidates / feedback ------------------------------------------


def brief_public(brief: Brief) -> dict:
    return {
        "id": brief.id,
        "name": brief.name,
        "product_description": brief.product_description,
        "verticals": _csv_list(brief.verticals),
        "target_markets": _csv_list(brief.target_markets),
        "content_languages": _csv_list(brief.content_languages),
        "target_audiences": _csv_list(brief.target_audiences),
        "objectives": _csv_list(brief.objectives),
        "platforms": _csv_list(brief.platforms),
        "content_formats": _csv_list(brief.content_formats),
        "total_budget_usd": brief.total_budget_usd,
        "per_creator_budget_min_usd": brief.per_creator_budget_min_usd,
        "per_creator_budget_max_usd": brief.per_creator_budget_max_usd,
        "risk_tolerance": brief.risk_tolerance,
        "notes": brief.notes,
        "updated_at": brief.updated_at.isoformat() if brief.updated_at else None,
    }


def candidate_public(item: CandidateItem) -> dict:
    return {
        "id": item.id,
        "creator": creator_card(item.creator, item.quote),
        "selected_content_format": item.selected_content_format,
        "quantity": item.quantity,
        "client_note": item.client_note,
        "added_at": item.added_at.isoformat() if item.added_at else None,
    }


def feedback_public(event: FeedbackEvent) -> dict:
    """A client's own feedback. Scoping to their brief is the caller's job."""
    return {
        "id": event.id,
        "creator_id": event.creator_id,
        "action": event.action,
        "reason_code": event.reason_code,
        "reason_text": event.reason_text,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


# --- portfolio scenarios -----------------------------------------------------


def scenario_public(scenario) -> dict:
    """One 组合方案 as a client may see it.

    Carries the trade-off as prominently as the picks. A scenario presented
    without what it gives up is a recommendation pretending to be a choice,
    and the whole point of showing several is that the client makes the call.
    """
    return {
        "key": scenario.key,
        "label": scenario.label,
        "thesis": scenario.thesis,
        "tradeoff": scenario.tradeoff,
        "size": scenario.size,
        "picks": [
            {
                "creator_id": pick.creator_id,
                "name": pick.creator_name,
                # 传播角色, assigned from stored fields only.
                "role": pick.role,
                # What this pick added that the set did not already have --
                # the reason it is in the portfolio at all.
                "adds": {
                    "audiences": pick.adds_audiences,
                    "markets": pick.adds_markets,
                    "formats": pick.adds_formats,
                    "roots": pick.adds_roots,
                },
            }
            for pick in scenario.picks
        ],
        "coverage": {
            "audiences": scenario.audiences,
            "markets": scenario.markets,
            "content_formats": scenario.formats,
            "roots_reached": scenario.roots_reached,
            # Reported, not hidden: some repetition is deliberate, and the
            # client is entitled to see how much of it they are buying.
            "audience_overlap": scenario.audience_overlap,
        },
        "budget": budget_public(scenario.budget) if scenario.budget else None,
        "risks": scenario.risks,
        "alternates": scenario.alternates,
        # client_notes, never notes: the internal version quotes exact
        # spend, which is recoverable by subtraction beside a visible budget.
        "notes": scenario.client_notes,
    }
