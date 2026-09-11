"""Mango's own operating surface.

The gap this closes: a client can already Approve a creator, which raises a
询价 task — and until now those tasks had nowhere to go. Feedback flowed in and
stopped. Everything here is the other half of that loop.

Separation from the client API is not a matter of URL prefix. These handlers
read and return the things the client serializers deliberately cannot see:
Mango's cost, review reasons, raw quote text, contact details, task queues.
That is why they live in their own module behind their own token, and why
nothing here imports ``client_safe``.

The one rule that still applies unchanged: **clearing a quote for client
display is a human act.** Nothing in this module auto-approves a price, and
``client_price_usd`` is only ever set by an explicit call carrying an actor.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import root_review
from .db import session_dependency
from .models import (
    QUOTE_STATUSES,
    TASK_STATUSES,
    Brief,
    CandidateItem,
    Client,
    Creator,
    FeedbackEvent,
    InternalTask,
    Quote,
)
from .observation_models import ROOT_REVIEW_STATUSES, ROOT_TYPE_LABELS_ZH, XAccount
from .quotes import price_band

router = APIRouter(prefix="/api/internal", tags=["internal"])


db = session_dependency


# --- request models ----------------------------------------------------------


class TaskUpdate(BaseModel):
    status: str | None = None
    owner: str | None = None
    due_date: dt.date | None = None
    outcome_notes: str | None = None


class TaskCreate(BaseModel):
    kind: str
    title: str
    detail: str | None = None
    brief_id: int | None = None
    creator_id: int | None = None
    quote_id: int | None = None
    owner: str | None = None


class QuoteReview(BaseModel):
    """A human's verdict on one quote.

    ``actor`` is required: a review with no reviewer is not a review, and the
    whole point of the gate is that a person stood behind it.
    """

    actor: str = Field(min_length=1)
    status: str | None = None
    needs_review: bool | None = None
    review_reason: str | None = None
    #: Corrected figures, when the parse got it wrong.
    amount_usd: float | None = None
    currency: str | None = None
    deliverable_raw: str | None = None


class QuotePricing(BaseModel):
    """Set the price a client may be shown, and whether to show it."""

    actor: str = Field(min_length=1)
    client_price_usd: float | None = None
    client_visible: bool | None = None
    note: str | None = None


class RootDecision(BaseModel):
    """A human's verdict on one Root candidate.

    ``actor`` is required for the same reason it is on a quote review: an
    accepted Root becomes the basis of a client-facing 注意力路径 claim, and a
    claim nobody signed is not reviewable later.
    """

    actor: str = Field(min_length=1)
    status: str
    root_type: str | None = None
    verticals: str | None = None
    note: str | None = None


# --- work queue --------------------------------------------------------------


@router.get("/tasks")
def list_tasks(
    status: str | None = Query(default="open"),
    kind: str | None = None,
    owner: str | None = None,
    session: Session = Depends(db),
):
    """Mango's work queue — where client feedback actually lands."""
    query = select(InternalTask).order_by(InternalTask.created_at.desc())
    if status:
        query = query.where(InternalTask.status == status)
    if kind:
        query = query.where(InternalTask.kind == kind)
    if owner:
        query = query.where(InternalTask.owner == owner)

    tasks = session.scalars(query.limit(500)).all()
    return {"count": len(tasks), "tasks": [_task_dict(session, t) for t in tasks]}


def _task_dict(session: Session, task: InternalTask) -> dict:
    creator = session.get(Creator, task.creator_id) if task.creator_id else None
    brief = session.get(Brief, task.brief_id) if task.brief_id else None
    client = session.get(Client, brief.client_id) if brief else None
    return {
        "id": task.id,
        "kind": task.kind,
        "status": task.status,
        "title": task.title,
        "detail": task.detail,
        "owner": task.owner,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "outcome_notes": task.outcome_notes,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "closed_at": task.closed_at.isoformat() if task.closed_at else None,
        # Context Mango needs to act, which the client never sees in reverse.
        "creator": {"id": creator.id, "name": creator.display_name} if creator else None,
        "brief": {"id": brief.id, "name": brief.name} if brief else None,
        "client": {"id": client.id, "name": client.name} if client else None,
    }


@router.get("/tasks/summary")
def task_summary(session: Session = Depends(db)):
    """Queue depth by kind and status — what is piling up."""
    rows = session.execute(
        select(InternalTask.kind, InternalTask.status, func.count())
        .group_by(InternalTask.kind, InternalTask.status)
    ).all()
    summary: dict[str, dict[str, int]] = {}
    for kind, status, count in rows:
        summary.setdefault(kind, {})[status] = count
    oldest = session.scalar(
        select(func.min(InternalTask.created_at)).where(InternalTask.status == "open")
    )
    return {
        "by_kind": summary,
        "open_total": sum(
            counts.get("open", 0) for counts in summary.values()
        ),
        "oldest_open_at": oldest.isoformat() if oldest else None,
    }


@router.post("/tasks", status_code=201)
def create_task(payload: TaskCreate, session: Session = Depends(db)):
    """A task Mango raised directly, not from client feedback."""
    task = InternalTask(
        kind=payload.kind,
        title=payload.title,
        detail=payload.detail,
        brief_id=payload.brief_id,
        creator_id=payload.creator_id,
        quote_id=payload.quote_id,
        owner=payload.owner,
    )
    session.add(task)
    session.commit()
    return _task_dict(session, task)


@router.patch("/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, session: Session = Depends(db)):
    task = session.get(InternalTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if payload.status is not None:
        if payload.status not in TASK_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be in {TASK_STATUSES}")
        task.status = payload.status
        # Closing stamps a time so queue age is measurable rather than guessed.
        task.closed_at = (
            dt.datetime.now(dt.UTC).replace(tzinfo=None)
            if payload.status in {"done", "cancelled"}
            else None
        )
    for attr in ("owner", "due_date", "outcome_notes"):
        value = getattr(payload, attr)
        if value is not None:
            setattr(task, attr, value)
    session.commit()
    return _task_dict(session, task)


# --- quote review ------------------------------------------------------------


@router.get("/quotes/review-queue")
def review_queue(
    limit: int = Query(default=100, le=500),
    session: Session = Depends(db),
):
    """Quotes blocked from client display, with everything needed to judge them.

    Shows the verbatim source text: the parse is what is under review, so
    hiding what it was parsed *from* would make the review impossible.
    """
    quotes = session.scalars(
        select(Quote)
        .where(Quote.needs_review)
        .options(selectinload(Quote.creator), selectinload(Quote.message))
        .order_by(Quote.id)
        .limit(limit)
    ).all()
    return {"count": len(quotes), "quotes": [_quote_dict(q) for q in quotes]}


def _quote_dict(quote: Quote) -> dict:
    return {
        "id": quote.id,
        "creator": {
            "id": quote.creator.id,
            "name": quote.creator.display_name,
            "handle": quote.creator.primary_handle,
        },
        "deliverable_raw": quote.deliverable_raw,
        "content_format": quote.content_format,
        "platform": quote.platform,
        "is_package": quote.is_package,
        # Internal figures. Present here and nowhere on the client side.
        "internal_cost_usd": quote.internal_cost_usd,
        "amount": quote.amount,
        "currency": quote.currency,
        "client_price_usd": quote.client_price_usd,
        "client_price_band": quote.client_price_band,
        "client_visible": quote.client_visible,
        "status": quote.status,
        "needs_review": quote.needs_review,
        "review_reason": quote.review_reason,
        "parse_confidence": quote.parse_confidence,
        "parse_notes": quote.parse_notes,
        # The evidence the review is actually about.
        "raw_segment": quote.raw_segment,
        "raw_message": quote.message.raw_text if quote.message else None,
        "quote_source": quote.message.source if quote.message else None,
    }


@router.patch("/quotes/{quote_id}/review")
def review_quote(quote_id: int, payload: QuoteReview, session: Session = Depends(db)):
    """Record a human's verdict on a parsed quote.

    Correcting ``amount_usd`` re-derives the client band, so a corrected figure
    cannot leave a stale band behind — that band is what a client sees.
    """
    quote = session.get(Quote, quote_id)
    if quote is None:
        raise HTTPException(status_code=404, detail="quote not found")
    if payload.status is not None and payload.status not in QUOTE_STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be in {QUOTE_STATUSES}")

    if payload.amount_usd is not None:
        quote.amount_usd = payload.amount_usd
        quote.internal_cost_usd = payload.amount_usd
        quote.client_price_band = price_band(payload.amount_usd)
        quote.parse_confidence = "high"  # a human read it
    if payload.currency is not None:
        quote.currency = payload.currency
    if payload.deliverable_raw is not None:
        quote.deliverable_raw = payload.deliverable_raw
    if payload.status is not None:
        quote.status = payload.status
    if payload.needs_review is not None:
        quote.needs_review = payload.needs_review
    quote.review_reason = payload.review_reason
    quote.updated_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)

    _close_tasks_for_quote(session, quote, payload.actor, "报价已复核")
    session.commit()
    return _quote_dict(quote)


@router.patch("/quotes/{quote_id}/pricing")
def set_pricing(quote_id: int, payload: QuotePricing, session: Session = Depends(db)):
    """Set the client-facing price, and whether it may be shown.

    Deliberately a separate call from review: reviewing *what the creator
    charges* and deciding *what to charge the client* are different judgments
    by potentially different people, and collapsing them would let a parse fix
    silently publish a price.
    """
    quote = session.get(Quote, quote_id)
    if quote is None:
        raise HTTPException(status_code=404, detail="quote not found")

    if payload.client_price_usd is not None:
        if quote.internal_cost_usd is not None and payload.client_price_usd < quote.internal_cost_usd:
            # Below cost is legal but almost never intended, so it is refused
            # rather than absorbed silently. Override by clearing the cost.
            raise HTTPException(
                status_code=422,
                detail="client price is below Mango's recorded cost; confirm before setting",
            )
        quote.client_price_usd = payload.client_price_usd
    if payload.client_visible is not None:
        if payload.client_visible and quote.needs_review:
            raise HTTPException(
                status_code=422,
                detail="quote still flagged needs_review; review it before clearing for display",
            )
        quote.client_visible = payload.client_visible
    if payload.note is not None:
        quote.client_visibility_note = payload.note
    quote.updated_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)

    if payload.client_price_usd is not None:
        _close_tasks_for_quote(session, quote, payload.actor, "对客报价已确定")
    session.commit()
    return _quote_dict(quote)


def _close_tasks_for_quote(session: Session, quote: Quote, actor: str, outcome: str) -> None:
    """Close the pricing/review tasks this action satisfies.

    Without this the queue only ever grows: the work gets done in one endpoint
    while the task recording it stays open in another.
    """
    tasks = session.scalars(
        select(InternalTask).where(
            InternalTask.status == "open",
            InternalTask.kind.in_(["price_inquiry", "price_review"]),
            InternalTask.creator_id == quote.creator_id,
        )
    ).all()
    for task in tasks:
        # A pricing task is only satisfied once a client price actually exists.
        if task.kind == "price_inquiry" and quote.client_price_usd is None:
            continue
        task.status = "done"
        task.outcome_notes = f"{outcome}（{actor}）"
        task.closed_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)


# --- Root review ---------------------------------------------------------------


@router.get("/roots/summary")
def root_summary(session: Session = Depends(db)):
    """How much of the Root layer a person has actually stood behind.

    Declared before ``/roots/{handle}`` on purpose — otherwise FastAPI matches
    "summary" as a handle and this endpoint becomes unreachable.
    """
    return root_review.review_summary(session)


@router.get("/roots/review-queue")
def root_review_queue(
    status: str = Query(default="pending"),
    limit: int = Query(default=50, le=300),
    min_creators: int = Query(default=1, ge=1),
    session: Session = Depends(db),
):
    """Root candidates awaiting a person, each with the evidence to judge it.

    Returns flags rather than a score. The candidate ranking that produced this
    queue cannot separate a genuine industry figure from a mutual-follow peer —
    see ``root_review`` — so the reviewer is shown the figures and decides.
    """
    if status and status not in ROOT_REVIEW_STATUSES:
        raise HTTPException(
            status_code=422, detail=f"status must be in {ROOT_REVIEW_STATUSES}"
        )
    rows = root_review.candidate_rows(
        session, status=status, limit=limit, min_creators=min_creators
    )
    return {
        "count": len(rows),
        "root_types": [
            {"key": key, "label_zh": label} for key, label in ROOT_TYPE_LABELS_ZH.items()
        ],
        "candidates": [_root_dict(row) for row in rows],
    }


def _root_dict(row: root_review.RootCandidate) -> dict:
    account = row.account
    share = row.roster_share_of_following
    return {
        "rest_id": account.rest_id,
        "handle": account.handle,
        "display_name": account.display_name,
        "bio": account.bio,
        "profile_url": f"https://x.com/{account.handle}" if account.handle else None,
        "followers": account.followers,
        "following": account.following,
        "listed_count": account.listed_count,
        # The edges under review, named. A reviewer judging "is this a Root"
        # needs to see *whose* attention path is at stake.
        "creators_followed": row.creators_followed,
        "creator_names": row.creator_names,
        "interaction_count": row.interaction_count,
        "roster_share_of_following": round(share, 5) if share is not None else None,
        "flags": [flag.as_dict() for flag in row.flags],
        # Suggestion and decision, side by side and never merged.
        "suggested_type": row.suggested_type,
        "suggested_type_label_zh": (
            ROOT_TYPE_LABELS_ZH.get(row.suggested_type) if row.suggested_type else None
        ),
        "suggestion_basis": row.suggestion_basis,
        "suggested_by": account.root_suggested_by,
        "review_status": account.root_review_status,
        "root_type": account.root_type,
        "root_verticals": account.root_verticals,
        "reviewed_by": account.root_reviewed_by,
        "reviewed_at": account.root_reviewed_at.isoformat() if account.root_reviewed_at else None,
        "note": account.root_note,
    }


@router.get("/roots/{handle}")
def root_detail(handle: str, session: Session = Depends(db)):
    """One candidate in full, including how it was surfaced in the first place."""
    account = _account_by_handle(session, handle)
    rows = root_review.candidate_rows(
        session, status="", limit=1, min_creators=1, rest_ids=[account.rest_id]
    )
    row = rows[0] if rows else None
    if row is None:
        # No observed edge: the account exists but nothing links it to the
        # roster, so there is nothing to review yet.
        row = root_review.RootCandidate(
            account=account, creators_followed=0, creator_names=[],
            interaction_count=0, flags=[], suggested_type=account.root_suggested_type,
            suggestion_basis=account.root_suggestion_basis,
        )
    payload = _root_dict(row)

    centrality = root_review.centrality_for(session, account.id)
    payload["surfaced_by"] = (
        {
            "roster_followers": centrality.roster_followers,
            "in_vertical_followers": centrality.in_vertical_followers,
            "audience_concentration": centrality.audience_concentration,
            "computed_date": centrality.computed_date.isoformat(),
            "caveat": "roster centrality 只反映 Mango 签下的人，不代表行业地位",
        }
        if centrality
        else None
    )
    return payload


@router.patch("/roots/{handle}")
def decide_root(handle: str, payload: RootDecision, session: Session = Depends(db)):
    """Accept or reject a Root candidate. The only path that sets ``root_type``.

    Accepting one changes what a client is told: an accepted Root's follow
    signal reads 已确认 Root 关注, an unreviewed one stays 候选. That is why
    this is a deliberate call with an actor and never a batch job.
    """
    account = _account_by_handle(session, handle)
    try:
        root_review.decide(
            session,
            account,
            actor=payload.actor,
            status=payload.status,
            root_type=payload.root_type,
            verticals=payload.verticals,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return root_detail(handle, session)


def _account_by_handle(session: Session, handle: str) -> XAccount:
    """Look up by handle or rest_id — a reviewer works in handles."""
    cleaned = handle.lstrip("@")
    account = session.scalar(select(XAccount).where(XAccount.handle == cleaned))
    if account is None:
        account = session.scalar(select(XAccount).where(XAccount.rest_id == cleaned))
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return account


# --- pipeline view -----------------------------------------------------------


@router.get("/briefs/{brief_id}/pipeline")
def brief_pipeline(brief_id: int, session: Session = Depends(db)):
    """Everything Mango needs for one client project, in one call.

    Includes contact details and cost — the internal counterpart to the
    client's candidate list.
    """
    brief = session.get(Brief, brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found")
    client = session.get(Client, brief.client_id)

    candidates = session.scalars(
        select(CandidateItem)
        .where(CandidateItem.brief_id == brief.id, CandidateItem.removed_at.is_(None))
        .options(
            selectinload(CandidateItem.creator).selectinload(Creator.contacts),
            selectinload(CandidateItem.quote),
        )
    ).all()

    feedback = session.scalars(
        select(FeedbackEvent)
        .where(FeedbackEvent.brief_id == brief.id)
        .order_by(FeedbackEvent.created_at.desc())
        .limit(50)
    ).all()

    tasks = session.scalars(
        select(InternalTask).where(
            InternalTask.brief_id == brief.id, InternalTask.status == "open"
        )
    ).all()

    return {
        "brief": {"id": brief.id, "name": brief.name, "budget_usd": brief.total_budget_usd},
        "client": {"id": client.id, "name": client.name} if client else None,
        "candidates": [
            {
                "creator_id": item.creator.id,
                "name": item.creator.display_name,
                "quantity": item.quantity,
                "client_note": item.client_note,
                "internal_cost_usd": item.quote.internal_cost_usd if item.quote else None,
                "client_price_usd": item.quote.client_price_usd if item.quote else None,
                "client_visible": item.quote.client_visible if item.quote else None,
                # Internal only, forever.
                "contacts": [
                    {"type": c.method_type, "value": c.value} for c in item.creator.contacts
                ],
            }
            for item in candidates
        ],
        "open_tasks": [_task_dict(session, t) for t in tasks],
        "recent_feedback": [
            {
                "creator_id": event.creator_id,
                "action": event.action,
                "reason_code": event.reason_code,
                "reason_text": event.reason_text,
                "at": event.created_at.isoformat() if event.created_at else None,
            }
            for event in feedback
        ],
    }


@router.get("/coverage")
def data_coverage(session: Session = Depends(db)):
    """What the supply data is missing — the internal health view.

    Surfaces gaps as counts so they can be worked down, rather than only
    appearing as 待确认 on individual client cards.
    """
    priced = select(Quote.creator_id).where(Quote.amount_usd.is_not(None)).distinct().subquery()
    total = session.scalar(select(func.count()).select_from(priced)) or 0

    def missing(column) -> int:
        return session.scalar(
            select(func.count())
            .select_from(Creator)
            .join(priced, Creator.id == priced.c.creator_id)
            .where(column.is_(None))
        ) or 0

    return {
        "priced_creators": total,
        "missing_market_region": missing(Creator.market_region),
        "missing_audience_types": missing(Creator.audience_types),
        "missing_verticals": missing(Creator.verticals),
        "quotes_needing_review": session.scalar(
            select(func.count()).select_from(Quote).where(Quote.needs_review)
        ),
        "quotes_client_visible": session.scalar(
            select(func.count()).select_from(Quote).where(Quote.client_visible)
        ),
        "quotes_with_client_price": session.scalar(
            select(func.count()).select_from(Quote).where(Quote.client_price_usd.is_not(None))
        ),
        "creators_without_contact": session.scalar(
            select(func.count())
            .select_from(Creator)
            .join(priced, Creator.id == priced.c.creator_id)
            .where(~Creator.contacts.any())
        ),
    }
