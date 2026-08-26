"""Mango KOL database -- FastAPI backend.

Serves the JSON API under /api/* and the static vanilla-JS frontend at /.
Filtering/sorting is done in Python over an eager-loaded creator list: at
~300 creators this is simpler and fast enough, and avoids building a second
query DSL on top of SQLAlchemy for a dataset this size.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import joinedload

from . import classify as classify_mod
from .bd_api import router as bd_router
from .compute import primary_account
from .db import get_session, init_db
from .enrichment import enrich_x_account
from .models import (
    CREATOR_CLASSES,
    Creator,
    PROMOTION_LEVELS,
    RateCard,
    Shortlist,
    ShortlistItem,
    SocialAccount,
    SponsorshipEvidence,
)
from .rapidx_client import RapidXClient, RapidXError
from .scrapecreators_client import ScrapeCreatorsClient, ScrapeCreatorsError
from .scrapecreators_enrichment import (
    enrich_instagram_account,
    enrich_tiktok_account,
    enrich_youtube_account_sc,
)
from .serializers import creator_detail, creator_summary, tab_for_creator
from .youtube_client import YouTubeClient, YouTubeError
from .youtube_enrichment import enrich_youtube_account

app = FastAPI(title="Mango KOL database")
app.include_router(bd_router)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _sort_nulls_last(rows: list[dict], key_fn, reverse: bool) -> list[dict]:
    """Sort by key_fn, but rows where key_fn returns None (no data, e.g. no
    Estimated CPM yet) always land at the end -- for both directions.
    Otherwise ascending sort would put "no data" ahead of the cheapest real
    quote, reading as if it were the cheapest option."""
    with_value = [r for r in rows if key_fn(r) is not None]
    without_value = [r for r in rows if key_fn(r) is None]
    with_value.sort(key=key_fn, reverse=reverse)
    return with_value + without_value


def _load_all_creators(session) -> list[Creator]:
    return (
        session.query(Creator)
        .options(
            joinedload(Creator.social_accounts),
            joinedload(Creator.contacts),
            joinedload(Creator.rate_cards),
            joinedload(Creator.campaigns),
        )
        .all()
    )


# ---------------------------------------------------------------------------
# Directory: list / filter / sort
# ---------------------------------------------------------------------------


def _filtered_sorted_rows(
    session,
    *,
    tab: str,
    search: str | None,
    creator_class: list[str] | None,
    platform: list[str] | None,
    region: list[str] | None,
    language: list[str] | None,
    category: str | None,
    followers_min: int | None,
    followers_max: int | None,
    avg_views_min: float | None,
    avg_views_max: float | None,
    engagement_min: float | None,
    engagement_max: float | None,
    quote_min: float | None,
    quote_max: float | None,
    promotion_level: list[str] | None,
    contact: str | None,
    sort: str,
    order: str,
) -> list[dict]:
    """Shared by the paginated directory listing and the unpaginated export
    endpoint, so "export the current view" always matches what the table
    actually shows -- same filters, same sort, just no page slice."""
    creators = _load_all_creators(session)
    rows = [creator_summary(c) for c in creators]

    rows = [r for r in rows if r["tab"] == tab]

    if search:
        needle = search.strip().lower()
        rows = [
            r
            for r in rows
            if needle in (r["display_name"] or "").lower()
            or needle in (r["handle"] or "").lower()
            or needle in " ".join(r["categories"]).lower()
        ]
    if creator_class:
        rows = [r for r in rows if r["creator_class"] in creator_class]
    if platform:
        rows = [r for r in rows if r["platform"] in platform]
    if region:
        rows = [r for r in rows if r["region"] in region]
    if language:
        rows = [r for r in rows if r["language"] in language]
    if category:
        needle = category.strip().lower()
        rows = [r for r in rows if any(needle == c.lower() for c in r["categories"])]
    if followers_min is not None:
        rows = [r for r in rows if (r["followers"] or 0) >= followers_min]
    if followers_max is not None:
        rows = [r for r in rows if r["followers"] is not None and r["followers"] <= followers_max]
    if avg_views_min is not None:
        rows = [r for r in rows if (r["avg_views"] or 0) >= avg_views_min]
    if avg_views_max is not None:
        rows = [r for r in rows if r["avg_views"] is not None and r["avg_views"] <= avg_views_max]
    if engagement_min is not None:
        rows = [r for r in rows if (r["engagement_rate"] or 0) >= engagement_min]
    if engagement_max is not None:
        rows = [r for r in rows if r["engagement_rate"] is not None and r["engagement_rate"] <= engagement_max]
    if quote_min is not None:
        rows = [r for r in rows if r["quote_min_usd"] is not None and r["quote_min_usd"] >= quote_min]
    if quote_max is not None:
        rows = [r for r in rows if r["quote_max_usd"] is not None and r["quote_max_usd"] <= quote_max]
    if promotion_level:
        rows = [r for r in rows if r["promotion_level"] in promotion_level]
    if contact == "email":
        rows = [r for r in rows if r["has_email"]]
    elif contact == "telegram":
        rows = [r for r in rows if r["has_telegram"]]

    # Raw (possibly-None) key extractors -- None means "no data", not "zero".
    sort_keys = {
        "followers": lambda r: r["followers"],
        "avg_views": lambda r: r["avg_views"],
        "engagement_rate": lambda r: r["engagement_rate"],
        "quote": lambda r: r["quote_min_usd"],
        "cpm": lambda r: r["cpm_min"],
        "creator_class": lambda r: r["creator_class"] or "",
        "promotion_level": lambda r: {"Low": 0, "Medium": 1, "High": 2}.get(r["promotion_level"]),
        "name": lambda r: (r["display_name"] or "").lower(),
    }
    key_fn = sort_keys.get(sort, sort_keys["followers"])
    return _sort_nulls_last(rows, key_fn, reverse=(order == "desc"))


class _CreatorFilters:
    """Groups the ~20 shared query params so both endpoints below declare
    them once via FastAPI's dependency injection instead of duplicating the
    same 20-argument signature twice."""

    def __init__(
        self,
        tab: str = Query("strategic", pattern="^(strategic|distribution|needs_review)$"),
        search: str | None = None,
        creator_class: list[str] | None = Query(None),
        platform: list[str] | None = Query(None),
        region: list[str] | None = Query(None),
        language: list[str] | None = Query(None),
        category: str | None = None,
        followers_min: int | None = None,
        followers_max: int | None = None,
        avg_views_min: float | None = None,
        avg_views_max: float | None = None,
        engagement_min: float | None = None,
        engagement_max: float | None = None,
        quote_min: float | None = None,
        quote_max: float | None = None,
        promotion_level: list[str] | None = Query(None),
        contact: str | None = None,
        sort: str = "followers",
        order: str = "desc",
    ) -> None:
        self.kwargs = dict(
            tab=tab,
            search=search,
            creator_class=creator_class,
            platform=platform,
            region=region,
            language=language,
            category=category,
            followers_min=followers_min,
            followers_max=followers_max,
            avg_views_min=avg_views_min,
            avg_views_max=avg_views_max,
            engagement_min=engagement_min,
            engagement_max=engagement_max,
            quote_min=quote_min,
            quote_max=quote_max,
            promotion_level=promotion_level,
            contact=contact,
            sort=sort,
            order=order,
        )


@app.get("/api/creators")
def list_creators(filters: _CreatorFilters = Depends(), page: int = 1, page_size: int = 50):
    session = get_session()
    try:
        rows = _filtered_sorted_rows(session, **filters.kwargs)
        total = len(rows)
        start = (page - 1) * page_size
        page_rows = rows[start : start + page_size]
        return {"total": total, "page": page, "page_size": page_size, "results": page_rows}
    finally:
        session.close()


@app.get("/api/creators/export.csv")
def export_creators_csv(filters: _CreatorFilters = Depends()):
    """Export every creator matching the current tab + filters (not just
    the shortlist) -- addresses a real workflow gap: sharing "all Strategic
    KOLs under $500" with a stakeholder shouldn't require adding everyone
    to a shortlist first."""
    session = get_session()
    try:
        rows = _filtered_sorted_rows(session, **filters.kwargs)
    finally:
        session.close()

    fieldnames = [
        "Creator", "Handle", "Platform", "Class", "Confidence", "Promotion level",
        "Categories", "Region", "Language", "Followers", "Avg views", "Engagement rate (%)",
        "Quote min (USD)", "Quote max (USD)", "Est. CPM min", "Est. CPM max", "Has email", "Has telegram",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow(
            {
                "Creator": r["display_name"],
                "Handle": r["handle"] or "",
                "Platform": r["platform"] or "",
                "Class": r["creator_class"],
                "Confidence": r["classification_confidence"] or "",
                "Promotion level": r["promotion_level"] or "",
                "Categories": ", ".join(r["categories"]),
                "Region": r["region"] or "",
                "Language": r["language"] or "",
                "Followers": r["followers"] if r["followers"] is not None else "",
                "Avg views": r["avg_views"] if r["avg_views"] is not None else "",
                "Engagement rate (%)": r["engagement_rate"] if r["engagement_rate"] is not None else "",
                "Quote min (USD)": r["quote_min_usd"] if r["quote_min_usd"] is not None else "",
                "Quote max (USD)": r["quote_max_usd"] if r["quote_max_usd"] is not None else "",
                "Est. CPM min": r["cpm_min"] if r["cpm_min"] is not None else "",
                "Est. CPM max": r["cpm_max"] if r["cpm_max"] is not None else "",
                "Has email": "yes" if r["has_email"] else "",
                "Has telegram": "yes" if r["has_telegram"] else "",
            }
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="mango_kol_{filters.kwargs["tab"]}.csv"'},
    )


@app.get("/api/meta/status")
def integration_status():
    """Whether Rapid X / YouTube / ScrapeCreators / OpenAI credentials are
    configured, so the frontend can grey out enrich/classify actions instead
    of letting the user hit a 400 on click with no warning."""
    return {
        "rapid_x_configured": RapidXClient().is_configured,
        "youtube_configured": YouTubeClient().is_configured or ScrapeCreatorsClient().is_configured,
        "scrapecreators_configured": ScrapeCreatorsClient().is_configured,
        "openai_configured": classify_mod.is_configured(),
    }


@app.get("/api/meta/filters")
def filter_options():
    """Distinct values actually present in the data, so filter dropdowns
    never offer an option with zero matching creators."""
    session = get_session()
    try:
        creators = _load_all_creators(session)
        platforms, regions, languages, categories = set(), set(), set(), set()
        for c in creators:
            for a in c.social_accounts:
                if a.platform:
                    platforms.add(a.platform)
            if c.region:
                regions.add(c.region)
            if c.language:
                languages.add(c.language)
            if c.categories:
                categories.update(x.strip() for x in c.categories.split(",") if x.strip())
        tab_counts: dict[str, int] = {"strategic": 0, "distribution": 0, "needs_review": 0}
        for c in creators:
            tab_counts[tab_for_creator(c)] += 1
        return {
            "creator_classes": CREATOR_CLASSES,
            "promotion_levels": PROMOTION_LEVELS,
            "platforms": sorted(platforms),
            "regions": sorted(regions),
            "languages": sorted(languages),
            "categories": sorted(categories),
            "tab_counts": tab_counts,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Creator detail + manual edits
# ---------------------------------------------------------------------------


@app.get("/api/creators/{creator_id}")
def get_creator(creator_id: int):
    session = get_session()
    try:
        creator = _get_creator_or_404(session, creator_id)
        return creator_detail(creator)
    finally:
        session.close()


class CreatorEdit(BaseModel):
    creator_class: str | None = None
    promotion_level: str | None = None
    region: str | None = None
    language: str | None = None
    categories: list[str] | None = None
    internal_notes: str | None = None


def _get_creator_or_404(session, creator_id: int) -> Creator:
    # populate_existing() forces a re-read from the DB even if this Creator
    # (and its collections) is already in the session's identity map from an
    # earlier query in this same request -- otherwise a re-fetch right after
    # a commit (e.g. in enrich/edit handlers) can silently return stale data.
    creator = (
        session.query(Creator)
        .populate_existing()
        .options(
            joinedload(Creator.social_accounts),
            joinedload(Creator.contacts),
            joinedload(Creator.rate_cards),
            joinedload(Creator.campaigns),
            joinedload(Creator.sponsorships).joinedload(SponsorshipEvidence.company),
        )
        .filter(Creator.id == creator_id)
        .one_or_none()
    )
    if creator is None:
        raise HTTPException(404, f"Creator {creator_id} not found")
    return creator


@app.patch("/api/creators/{creator_id}")
def edit_creator(creator_id: int, edit: CreatorEdit):
    session = get_session()
    try:
        creator = _get_creator_or_404(session, creator_id)
        if edit.creator_class is not None:
            if edit.creator_class not in CREATOR_CLASSES:
                raise HTTPException(400, f"Invalid creator_class: {edit.creator_class}")
            creator.creator_class = edit.creator_class
            creator.creator_class_source = "manual"
            creator.creator_class_locked = True
            creator.creator_class_reason = "Manually set by Mango team."
        if edit.promotion_level is not None:
            if edit.promotion_level not in PROMOTION_LEVELS:
                raise HTTPException(400, f"Invalid promotion_level: {edit.promotion_level}")
            creator.promotion_level = edit.promotion_level
            creator.promotion_level_source = "manual"
            creator.promotion_level_locked = True
        if edit.region is not None:
            creator.region = edit.region or None
        if edit.language is not None:
            creator.language = edit.language or None
        if edit.categories is not None:
            creator.categories = ",".join(c.strip() for c in edit.categories if c.strip()) or None
        if edit.internal_notes is not None:
            creator.internal_notes = edit.internal_notes or None
        session.commit()
        return creator_detail(creator)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Enrichment (Rapid X + optional GPT classification)
# ---------------------------------------------------------------------------


def _classify_account(creator: Creator, account: SocialAccount, platform_label: str) -> dict[str, Any]:
    """Run GPT classification for one enriched account and apply the result
    to the creator, respecting manual locks. Returns an entry dict with
    either classified=True or a classify_error message."""
    entry: dict[str, Any] = {}
    try:
        payload = {
            "platform": platform_label,
            "handle": account.handle,
            "display_name": account.display_name_x or creator.display_name,
            # The sheet's own display_name sometimes carries the outreach
            # team's own annotation (e.g. "(agency-run AI virtual influencer,
            # not a real person)") that account.display_name_x -- the live
            # scraped name -- doesn't have. Surface it separately so that
            # signal is never silently dropped just because the account also
            # has a normal-looking scraped profile.
            "sheet_display_name": creator.display_name if creator.display_name != (account.display_name_x or creator.display_name) else None,
            "internal_notes": creator.internal_notes,
            "bio": account.bio,
            "followers": account.followers,
            "avg_views": account.avg_views,
            "engagement_rate": account.engagement_rate,
            "original_repost_ratio": account.original_repost_ratio,
            "promotional_content_ratio": account.promotional_content_ratio,
            "posting_frequency": account.posting_frequency,
            # Sort by post_id (stable, content-derived) rather than relying on
            # SQLite's unordered row iteration: recent_content is deleted and
            # re-inserted on every enrichment run, and an unstable order here
            # changes the rendered prompt text on every re-run even when the
            # underlying data is identical, which silently defeats the GPT
            # response cache (classify.py keys on the exact prompt string) and
            # burns real OpenAI calls for no reason.
            "recent_texts": [c.text for c in sorted(account.recent_content, key=lambda c: c.post_id or "")],
        }
        gpt_result = classify_mod.classify_account(payload)
        if not creator.creator_class_locked:
            creator.creator_class = gpt_result.get("creator_class", "Unknown")
            creator.creator_class_reason = gpt_result.get("creator_class_reason")
            creator.creator_class_source = "auto"
            creator.classification_confidence = gpt_result.get("classification_confidence")
        if not creator.promotion_level_locked:
            creator.promotion_level = gpt_result.get("promotion_level")
            creator.promotion_level_source = "auto"
        if gpt_result.get("categories"):
            creator.categories = ",".join(gpt_result["categories"])
        if gpt_result.get("region") and not creator.region:
            creator.region = gpt_result["region"]
        if gpt_result.get("language") and not creator.language:
            creator.language = gpt_result["language"]
        account.content_summary = gpt_result.get("content_summary")
        entry["classified"] = True
    except classify_mod.ClassifyError as exc:
        entry["classify_error"] = str(exc)
    return entry


def _enrich_one_account(session, creator: Creator, account, rapid_client, youtube_client, sc_client) -> dict[str, Any]:
    """Dispatch one SocialAccount to the right adapter by platform. X always
    goes through Rapid X. YouTube prefers ScrapeCreators (what's actually
    configured) and falls back to the official YouTube Data API v3 adapter
    if that key is ever added instead. Instagram/TikTok only have a
    ScrapeCreators path."""
    entry: dict[str, Any] = {"social_account_id": account.id, "handle": account.handle, "platform": account.platform}
    platform_upper = account.platform.upper()

    try:
        if platform_upper in {"X", "TWITTER", "X(TWITTER)"}:
            if not rapid_client.is_configured:
                entry["status"], entry["error"] = "skipped", "RAPID_X_API_KEY not set."
                return entry
            enrich_x_account(session, account, rapid_client)
            entry["status"] = "ok"

        elif platform_upper == "YOUTUBE":
            if sc_client.is_configured:
                signals = enrich_youtube_account_sc(session, account, sc_client)
            elif youtube_client.is_configured:
                signals = enrich_youtube_account(session, account, youtube_client)
            else:
                entry["status"], entry["error"] = "skipped", "Neither SCRAPECREATORS_API_KEY nor YOUTUBE_API_KEY is set."
                return entry
            if signals.get("country") and not creator.region:
                creator.region = signals["country"]
            if signals.get("language") and not creator.language:
                creator.language = signals["language"]
            entry["status"] = "ok"

        elif platform_upper == "INSTAGRAM":
            if not sc_client.is_configured:
                entry["status"], entry["error"] = "skipped", "SCRAPECREATORS_API_KEY not set."
                return entry
            enrich_instagram_account(session, account, sc_client)
            entry["status"] = "ok"

        elif platform_upper == "TIKTOK":
            if not sc_client.is_configured:
                entry["status"], entry["error"] = "skipped", "SCRAPECREATORS_API_KEY not set."
                return entry
            signals = enrich_tiktok_account(session, account, sc_client)
            if signals.get("language") and not creator.language:
                creator.language = signals["language"]
            entry["status"] = "ok"

        else:
            entry["status"], entry["error"] = "skipped", f"No enrichment adapter for platform {account.platform!r}."
    except (RapidXError, YouTubeError, ScrapeCreatorsError) as exc:
        entry["status"], entry["error"] = "error", str(exc)

    return entry


def _enrich_creator_accounts(
    session, creator: Creator, rapid_client: RapidXClient, youtube_client: YouTubeClient, sc_client: ScrapeCreatorsClient, do_classify: bool
) -> dict[str, Any]:
    result: dict[str, Any] = {"creator_id": creator.id, "accounts": []}
    for account in creator.social_accounts:
        if not account.handle:
            continue
        entry = _enrich_one_account(session, creator, account, rapid_client, youtube_client, sc_client)
        if entry["status"] == "ok" and do_classify and not creator.creator_class_locked:
            entry.update(_classify_account(creator, account, account.platform))
        result["accounts"].append(entry)
    session.commit()
    return result


@app.post("/api/creators/{creator_id}/enrich")
def enrich_creator(creator_id: int, classify: bool = True):
    rapid_client = RapidXClient()
    youtube_client = YouTubeClient()
    sc_client = ScrapeCreatorsClient()
    if not any([rapid_client.is_configured, youtube_client.is_configured, sc_client.is_configured]):
        raise HTTPException(
            400, "No enrichment key is set (RAPID_X_API_KEY / YOUTUBE_API_KEY / SCRAPECREATORS_API_KEY). Add at least one to the repo-root .env, then retry."
        )
    session = get_session()
    try:
        creator = _get_creator_or_404(session, creator_id)
        return _enrich_creator_accounts(session, creator, rapid_client, youtube_client, sc_client, do_classify=classify)
    finally:
        session.close()


class BatchEnrichRequest(BaseModel):
    creator_ids: list[int]
    classify: bool = True


@app.post("/api/enrich/batch")
def enrich_batch(req: BatchEnrichRequest):
    if len(req.creator_ids) > 50:
        raise HTTPException(400, "Batch enrich is capped at 50 creators per call to protect API quota.")
    rapid_client = RapidXClient()
    youtube_client = YouTubeClient()
    sc_client = ScrapeCreatorsClient()
    if not any([rapid_client.is_configured, youtube_client.is_configured, sc_client.is_configured]):
        raise HTTPException(400, "No enrichment key is set (RAPID_X_API_KEY / YOUTUBE_API_KEY / SCRAPECREATORS_API_KEY). Add at least one to the repo-root .env, then retry.")
    session = get_session()
    try:
        results = []
        for cid in req.creator_ids:
            creator = session.query(Creator).options(joinedload(Creator.social_accounts)).filter(Creator.id == cid).one_or_none()
            if creator is None:
                results.append({"creator_id": cid, "status": "not_found"})
                continue
            results.append(_enrich_creator_accounts(session, creator, rapid_client, youtube_client, sc_client, do_classify=req.classify))
        return {"results": results}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Shortlists
# ---------------------------------------------------------------------------


class ShortlistCreate(BaseModel):
    name: str
    client_or_campaign: str | None = None
    budget_usd: float | None = None
    company_id: str | None = None
    objective: str | None = None
    target_audience: str | None = None
    region_pref: str | None = None
    language_pref: str | None = None
    platforms_pref: str | None = None
    timing: str | None = None


class ShortlistEdit(BaseModel):
    name: str | None = None
    client_or_campaign: str | None = None
    budget_usd: float | None = None
    company_id: str | None = None
    objective: str | None = None
    target_audience: str | None = None
    region_pref: str | None = None
    language_pref: str | None = None
    platforms_pref: str | None = None
    timing: str | None = None


class ShortlistItemCreate(BaseModel):
    creator_id: int
    rate_card_id: int | None = None
    quote_usd_override: float | None = None
    notes: str | None = None


class ShortlistItemEdit(BaseModel):
    rate_card_id: int | None = None
    quote_usd_override: float | None = None
    notes: str | None = None


def _shortlist_or_404(session, shortlist_id: int) -> Shortlist:
    # populate_existing(): see _get_creator_or_404 -- same identity-map
    # staleness risk when this is called again right after a commit.
    shortlist = (
        session.query(Shortlist)
        .populate_existing()
        .options(
            joinedload(Shortlist.items).joinedload(ShortlistItem.creator).joinedload(Creator.social_accounts),
            joinedload(Shortlist.items).joinedload(ShortlistItem.creator).joinedload(Creator.rate_cards),
            joinedload(Shortlist.company),
        )
        .filter(Shortlist.id == shortlist_id)
        .one_or_none()
    )
    if shortlist is None:
        raise HTTPException(404, f"Shortlist {shortlist_id} not found")
    return shortlist


def _item_quote_usd(item: ShortlistItem) -> float | None:
    if item.quote_usd_override is not None:
        return item.quote_usd_override
    if item.rate_card is not None:
        return item.rate_card.quote_amount_usd
    return None


def _shortlist_detail(shortlist: Shortlist) -> dict:
    items = []
    kol_spend = 0.0
    koc_spend = 0.0
    total_spend = 0.0
    unpriced = 0
    for item in shortlist.items:
        creator = item.creator
        quote = _item_quote_usd(item)
        tab = tab_for_creator(creator)
        if quote is not None:
            total_spend += quote
            if tab == "distribution":
                koc_spend += quote
            else:
                kol_spend += quote
        else:
            unpriced += 1
        account = primary_account(creator)
        items.append(
            {
                "item_id": item.id,
                "creator_id": creator.id,
                "display_name": creator.display_name,
                "handle": account.handle if account else None,
                "platform": account.platform if account else None,
                "creator_class": creator.creator_class,
                "tab": tab,
                "rate_card_id": item.rate_card_id,
                "deliverable": item.rate_card.deliverable if item.rate_card else None,
                "quote_usd": quote,
                "quote_is_override": item.quote_usd_override is not None,
                "notes": item.notes,
                "rate_cards": [
                    {
                        "id": rc.id,
                        "deliverable": rc.deliverable,
                        "quote_amount_usd": rc.quote_amount_usd,
                        "is_confident": rc.is_confident,
                    }
                    for rc in creator.rate_cards
                ],
            }
        )
    budget = shortlist.budget_usd
    return {
        "id": shortlist.id,
        "name": shortlist.name,
        "client_or_campaign": shortlist.client_or_campaign,
        "budget_usd": budget,
        "company_id": shortlist.company_id,
        "company_name": shortlist.company.name if shortlist.company else None,
        "objective": shortlist.objective,
        "target_audience": shortlist.target_audience,
        "region_pref": shortlist.region_pref,
        "language_pref": shortlist.language_pref,
        "platforms_pref": shortlist.platforms_pref,
        "timing": shortlist.timing,
        "created_at": shortlist.created_at.isoformat() if shortlist.created_at else None,
        "updated_at": shortlist.updated_at.isoformat() if shortlist.updated_at else None,
        "selected_count": len(items),
        "unpriced_count": unpriced,
        "kol_spend_usd": round(kol_spend, 2),
        "koc_spend_usd": round(koc_spend, 2),
        "total_spend_usd": round(total_spend, 2),
        "remaining_budget_usd": round(budget - total_spend, 2) if budget is not None else None,
        "over_budget": (budget is not None and total_spend > budget),
        "items": items,
    }


@app.get("/api/shortlists")
def list_shortlists():
    session = get_session()
    try:
        shortlists = session.query(Shortlist).options(joinedload(Shortlist.items), joinedload(Shortlist.company)).all()
        return [
            {
                "id": s.id,
                "name": s.name,
                "client_or_campaign": s.client_or_campaign,
                "budget_usd": s.budget_usd,
                "company_id": s.company_id,
                "company_name": s.company.name if s.company else None,
                "selected_count": len(s.items),
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in shortlists
        ]
    finally:
        session.close()


@app.post("/api/shortlists")
def create_shortlist(body: ShortlistCreate):
    session = get_session()
    try:
        shortlist = Shortlist(
            name=body.name,
            client_or_campaign=body.client_or_campaign,
            budget_usd=body.budget_usd,
            company_id=body.company_id or None,
            objective=body.objective,
            target_audience=body.target_audience,
            region_pref=body.region_pref,
            language_pref=body.language_pref,
            platforms_pref=body.platforms_pref,
            timing=body.timing,
        )
        session.add(shortlist)
        session.commit()
        session.refresh(shortlist)
        return _shortlist_detail(_shortlist_or_404(session, shortlist.id))
    finally:
        session.close()


@app.get("/api/shortlists/{shortlist_id}")
def get_shortlist(shortlist_id: int):
    session = get_session()
    try:
        return _shortlist_detail(_shortlist_or_404(session, shortlist_id))
    finally:
        session.close()


@app.patch("/api/shortlists/{shortlist_id}")
def edit_shortlist(shortlist_id: int, body: ShortlistEdit):
    session = get_session()
    try:
        shortlist = _shortlist_or_404(session, shortlist_id)
        if body.name is not None:
            shortlist.name = body.name
        if body.client_or_campaign is not None:
            shortlist.client_or_campaign = body.client_or_campaign or None
        if body.budget_usd is not None:
            shortlist.budget_usd = body.budget_usd
        if body.company_id is not None:
            shortlist.company_id = body.company_id or None
        if body.objective is not None:
            shortlist.objective = body.objective or None
        if body.target_audience is not None:
            shortlist.target_audience = body.target_audience or None
        if body.region_pref is not None:
            shortlist.region_pref = body.region_pref or None
        if body.language_pref is not None:
            shortlist.language_pref = body.language_pref or None
        if body.platforms_pref is not None:
            shortlist.platforms_pref = body.platforms_pref or None
        if body.timing is not None:
            shortlist.timing = body.timing or None
        session.commit()
        return _shortlist_detail(_shortlist_or_404(session, shortlist_id))
    finally:
        session.close()


@app.delete("/api/shortlists/{shortlist_id}")
def delete_shortlist(shortlist_id: int):
    session = get_session()
    try:
        shortlist = _shortlist_or_404(session, shortlist_id)
        session.delete(shortlist)
        session.commit()
        return {"deleted": shortlist_id}
    finally:
        session.close()


@app.post("/api/shortlists/{shortlist_id}/items")
def add_shortlist_item(shortlist_id: int, body: ShortlistItemCreate):
    session = get_session()
    try:
        shortlist = _shortlist_or_404(session, shortlist_id)
        creator = _get_creator_or_404(session, body.creator_id)
        existing = next((i for i in shortlist.items if i.creator_id == creator.id), None)
        if existing:
            raise HTTPException(409, "Creator is already on this shortlist")

        rate_card_id = body.rate_card_id
        if rate_card_id is None:
            confident = [rc for rc in creator.rate_cards if rc.is_confident]
            if confident:
                rate_card_id = min(confident, key=lambda rc: rc.quote_amount_usd or 0).id

        item = ShortlistItem(
            shortlist_id=shortlist.id,
            creator_id=creator.id,
            rate_card_id=rate_card_id,
            quote_usd_override=body.quote_usd_override,
            notes=body.notes,
        )
        session.add(item)
        session.commit()
        return _shortlist_detail(_shortlist_or_404(session, shortlist_id))
    finally:
        session.close()


@app.patch("/api/shortlists/{shortlist_id}/items/{item_id}")
def edit_shortlist_item(shortlist_id: int, item_id: int, body: ShortlistItemEdit):
    session = get_session()
    try:
        item = session.query(ShortlistItem).filter(
            ShortlistItem.id == item_id, ShortlistItem.shortlist_id == shortlist_id
        ).one_or_none()
        if item is None:
            raise HTTPException(404, "Shortlist item not found")
        if body.rate_card_id is not None:
            item.rate_card_id = body.rate_card_id
        if body.quote_usd_override is not None:
            item.quote_usd_override = body.quote_usd_override
        if body.notes is not None:
            item.notes = body.notes or None
        session.commit()
        return _shortlist_detail(_shortlist_or_404(session, shortlist_id))
    finally:
        session.close()


@app.delete("/api/shortlists/{shortlist_id}/items/{item_id}")
def delete_shortlist_item(shortlist_id: int, item_id: int):
    session = get_session()
    try:
        item = session.query(ShortlistItem).filter(
            ShortlistItem.id == item_id, ShortlistItem.shortlist_id == shortlist_id
        ).one_or_none()
        if item is None:
            raise HTTPException(404, "Shortlist item not found")
        session.delete(item)
        session.commit()
        return _shortlist_detail(_shortlist_or_404(session, shortlist_id))
    finally:
        session.close()


def _shortlist_rows_for_export(detail: dict) -> list[dict]:
    return [
        {
            "Creator": item["display_name"],
            "Handle": item["handle"] or "",
            "Platform": item["platform"] or "",
            "Class": item["creator_class"],
            "Deliverable": item["deliverable"] or "",
            "Quote (USD)": item["quote_usd"] if item["quote_usd"] is not None else "",
            "Notes": item["notes"] or "",
        }
        for item in detail["items"]
    ]


@app.get("/api/shortlists/{shortlist_id}/export.csv")
def export_shortlist_csv(shortlist_id: int):
    session = get_session()
    try:
        detail = _shortlist_detail(_shortlist_or_404(session, shortlist_id))
    finally:
        session.close()
    rows = _shortlist_rows_for_export(detail)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["Creator"])
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    filename = f"{detail['name'].replace(' ', '_')}_shortlist.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/shortlists/{shortlist_id}/export.tsv")
def export_shortlist_tsv(shortlist_id: int):
    """Tab-separated text meant for pasting straight into Google Sheets."""
    session = get_session()
    try:
        detail = _shortlist_detail(_shortlist_or_404(session, shortlist_id))
    finally:
        session.close()
    rows = _shortlist_rows_for_export(detail)
    if not rows:
        return {"tsv": ""}
    headers = list(rows[0].keys())
    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join(str(row[h]) for h in headers))
    return {"tsv": "\n".join(lines)}


@app.get("/api/shortlists/{shortlist_id}/export.xlsx")
def export_shortlist_xlsx(shortlist_id: int):
    import openpyxl

    session = get_session()
    try:
        detail = _shortlist_detail(_shortlist_or_404(session, shortlist_id))
    finally:
        session.close()
    rows = _shortlist_rows_for_export(detail)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Shortlist"
    if rows:
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row[h] for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"{detail['name'].replace(' ', '_')}_shortlist.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
