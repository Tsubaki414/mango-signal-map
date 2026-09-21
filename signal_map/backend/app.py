"""Mango Signal Map API.

Two namespaces on one app, with different auth and *different serializers*:

* ``/api/client/*`` -- the client decision surface. Every response is built by
  :mod:`signal_map.backend.client_safe`, which names each field explicitly.
  Requests are scoped to the client their bearer token resolves to, so one
  client can never read another's briefs, candidates or feedback.
* ``/api/internal/*`` -- Mango's operating surface. Sees costs, review queues
  and tasks. Guarded by a separate token.

The split is enforced at the serializer, not the route: a client route
physically cannot emit ``internal_cost_usd`` because no client serializer
names it. Route-level filtering would be one forgotten line away from a leak.

Tokens come from the environment and the ``clients.api_token`` column. No
credential is ever logged or echoed in a response.
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import client_safe as cs
from .bd_api import router as bd_router
from .projects_api import router as projects_router
from .internal_api import router as internal_router
from .frontrun_api import router as frontrun_router
from .v3_api import router as v3_router
from .budget import line_from_quote, summarize
from .db import init_db, session_dependency
from .models import (
    CONTENT_FORMATS,
    FEEDBACK_ACTIONS,
    PLATFORMS,
    Brief,
    CandidateItem,
    Client,
    Creator,
    FeedbackEvent,
    InternalTask,
    Recommendation,
    RecommendationRun,
)
from .normalize import (
    AUDIENCE_TYPES,
    AUDIENCE_TYPE_LABELS_ZH,
    MARKET_REGIONS,
    MARKET_REGION_LABELS_ZH,
    VERTICALS,
)
from .portfolio import SCENARIOS, build_all
from .recommend import (
    RULE_VERSION,
    _OBJECTIVE_RULES,
    assess,
    attention_signals_by_creator,
    candidate_pool,
    run_recommendation,
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Mango Signal Map", version="0.1.0", lifespan=lifespan)


#: One dependency shared with ``internal_api`` so tests have a single
#: override point -- see ``db.session_dependency``.
db = session_dependency


# --- auth --------------------------------------------------------------------
#
# 实现在 ``auth.py``，两个命名空间共用一份。这里只做重新导出，保持既有
# ``app.current_client`` / ``app.require_internal`` 的引用不变（测试也在用）。

from .auth import current_client, require_internal  # noqa: E402,F401


def _client_brief(session: Session, client: Client, brief_id: int) -> Brief:
    """Load a brief **owned by this client**, or 404.

    404 rather than 403 on someone else's brief: a 403 would confirm the id
    exists, which is itself a leak across clients.
    """
    brief = session.scalar(
        select(Brief).where(Brief.id == brief_id, Brief.client_id == client.id)
    )
    if brief is None:
        raise HTTPException(status_code=404, detail="brief not found")
    return brief


# --- request models ----------------------------------------------------------


class BriefIn(BaseModel):
    name: str
    product_description: str | None = None
    verticals: list[str] = Field(default_factory=list)
    target_markets: list[str] = Field(default_factory=list)
    content_languages: list[str] = Field(default_factory=list)
    target_audiences: list[str] = Field(default_factory=list)
    objectives: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    content_formats: list[str] = Field(default_factory=list)
    total_budget_usd: float | None = None
    per_creator_budget_min_usd: float | None = None
    per_creator_budget_max_usd: float | None = None
    creator_size_preference: str | None = None
    risk_tolerance: str | None = None
    must_include_creator_ids: list[int] = Field(default_factory=list)
    must_exclude_creator_ids: list[int] = Field(default_factory=list)
    notes: str | None = None

    def apply_to(self, brief: Brief) -> Brief:
        def csv(values) -> str | None:
            return ",".join(str(v) for v in values) if values else None

        brief.name = self.name
        brief.product_description = self.product_description
        brief.verticals = csv(self.verticals)
        brief.target_markets = csv(self.target_markets)
        brief.content_languages = csv(self.content_languages)
        brief.target_audiences = csv(self.target_audiences)
        brief.objectives = csv(self.objectives)
        brief.platforms = csv(self.platforms)
        brief.content_formats = csv(self.content_formats)
        brief.total_budget_usd = self.total_budget_usd
        brief.per_creator_budget_min_usd = self.per_creator_budget_min_usd
        brief.per_creator_budget_max_usd = self.per_creator_budget_max_usd
        brief.creator_size_preference = self.creator_size_preference
        brief.risk_tolerance = self.risk_tolerance
        brief.must_include_creator_ids = csv(self.must_include_creator_ids)
        brief.must_exclude_creator_ids = csv(self.must_exclude_creator_ids)
        brief.notes = self.notes
        return brief


class CandidateIn(BaseModel):
    creator_id: int
    quote_id: int | None = None
    selected_content_format: str | None = None
    quantity: int = 1
    client_note: str | None = None


class FeedbackIn(BaseModel):
    creator_id: int
    action: str
    run_id: int | None = None
    reason_code: str | None = None
    reason_text: str | None = None
    actor: str | None = None


# --- client API --------------------------------------------------------------


@app.get("/api/client/filters")
def client_filters(session: Session = Depends(db)):
    """Filter options, restricted to what the data can actually answer.

    Only values that exist on priced, recommendable inventory are offered --
    a filter that returns nothing every time is worse than no filter.
    """
    creators = session.scalars(
        select(Creator).options(selectinload(Creator.quotes))
    ).all()
    pool = [c for c in creators if c.has_priced_quote and c.is_recommendable]

    def present(attr: str, allowed) -> list[dict]:
        seen: set[str] = set()
        for creator in pool:
            for value in (getattr(creator, attr) or "").split(","):
                value = value.strip()
                if value:
                    seen.add(value)
        return [v for v in allowed if v in seen]

    formats = {q.content_format for c in pool for q in c.quotes if q.amount_usd is not None}
    platforms = {q.platform for c in pool for q in c.quotes if q.amount_usd is not None}

    return {
        "verticals": present("verticals", VERTICALS),
        "markets": [
            {"key": k, "label": MARKET_REGION_LABELS_ZH[k]}
            for k in MARKET_REGIONS
            if k in {c.market_region for c in pool}
        ],
        "audiences": [
            {"key": k, "label": AUDIENCE_TYPE_LABELS_ZH[k]}
            for k in AUDIENCE_TYPES
            if k in {a for c in pool for a in (c.audience_types or "").split(",")}
        ],
        "objectives": [
            {"key": k, "label": v["label"]} for k, v in _OBJECTIVE_RULES.items()
        ],
        "platforms": [p for p in PLATFORMS if p in platforms],
        "content_formats": [f for f in CONTENT_FORMATS if f in formats],
        "pool_size": len(pool),
    }


@app.post("/api/client/briefs", status_code=201)
def create_brief(
    payload: BriefIn,
    client: Client = Depends(current_client),
    session: Session = Depends(db),
):
    brief = payload.apply_to(Brief(client_id=client.id, name=payload.name))
    session.add(brief)
    session.commit()
    return cs.brief_public(brief)


@app.get("/api/client/briefs")
def list_briefs(client: Client = Depends(current_client), session: Session = Depends(db)):
    briefs = session.scalars(
        select(Brief).where(Brief.client_id == client.id).order_by(Brief.id.desc())
    ).all()
    return {"briefs": [cs.brief_public(b) for b in briefs]}


@app.get("/api/client/briefs/{brief_id}")
def get_brief(
    brief_id: int, client: Client = Depends(current_client), session: Session = Depends(db)
):
    return cs.brief_public(_client_brief(session, client, brief_id))


@app.put("/api/client/briefs/{brief_id}")
def update_brief(
    brief_id: int,
    payload: BriefIn,
    client: Client = Depends(current_client),
    session: Session = Depends(db),
):
    brief = _client_brief(session, client, brief_id)
    payload.apply_to(brief)
    session.commit()
    return cs.brief_public(brief)


@app.post("/api/client/briefs/{brief_id}/recommendations", status_code=201)
def create_recommendations(
    brief_id: int,
    limit: int = Query(default=30, ge=1, le=100),
    client: Client = Depends(current_client),
    session: Session = Depends(db),
):
    brief = _client_brief(session, client, brief_id)
    run, _, _ = run_recommendation(session, brief, limit=limit, persist=True)
    return _run_payload(session, run)


@app.get("/api/client/runs/{run_id}")
def get_run(
    run_id: int, client: Client = Depends(current_client), session: Session = Depends(db)
):
    run = session.get(RecommendationRun, run_id)
    if run is None or run.brief.client_id != client.id:
        raise HTTPException(status_code=404, detail="run not found")
    return _run_payload(session, run)


def _run_payload(session: Session, run: RecommendationRun) -> dict:
    records = session.scalars(
        select(Recommendation)
        .where(Recommendation.run_id == run.id)
        .order_by(Recommendation.rank)
        .options(
            selectinload(Recommendation.creator).selectinload(Creator.accounts),
            selectinload(Recommendation.quote),
        )
    ).all()
    items = [cs.recommendation_public(r, r.creator, r.quote) for r in records]
    return cs.run_public(run, items)


@app.get("/api/client/briefs/{brief_id}/scenarios")
def brief_scenarios(
    brief_id: int,
    client: Client = Depends(current_client),
    session: Session = Depends(db),
):
    """组合方案与预算比较 — several portfolios from one brief.

    Built from the same assessments the recommendation list uses, so a client
    comparing scenarios is comparing strategies, not different facts.
    """
    brief = _client_brief(session, client, brief_id)
    pool = candidate_pool(session)
    signals = attention_signals_by_creator(session)
    assessments = [assess(c, brief, signals.get(c.id)) for c in pool]
    creators_by_id = {c.id: c for c in pool}

    scenarios = build_all(assessments, creators_by_id, signals, brief.total_budget_usd)
    return {
        "brief_id": brief.id,
        "budget_usd": brief.total_budget_usd,
        "scenarios": [cs.scenario_public(s) for s in scenarios],
    }


@app.get("/api/client/scenarios/meta")
def scenario_meta():
    """The available scenario types and what each optimises for."""
    return {
        "scenarios": [
            {"key": k, "label": v["label"], "thesis": v["thesis"], "tradeoff": v["tradeoff"]}
            for k, v in SCENARIOS.items()
        ]
    }


@app.get("/api/client/creators/{creator_id}")
def get_creator(
    creator_id: int,
    brief_id: int | None = None,
    client: Client = Depends(current_client),
    session: Session = Depends(db),
):
    creator = session.get(Creator, creator_id)
    if creator is None or not creator.is_recommendable or not creator.has_priced_quote:
        raise HTTPException(status_code=404, detail="creator not found")
    if brief_id is not None:
        _client_brief(session, client, brief_id)
    quote = next(
        (q for q in creator.quotes if q.client_visible and q.amount_usd is not None), None
    )
    return cs.creator_card(creator, quote)


# --- candidates --------------------------------------------------------------


@app.get("/api/client/briefs/{brief_id}/candidates")
def list_candidates(
    brief_id: int, client: Client = Depends(current_client), session: Session = Depends(db)
):
    brief = _client_brief(session, client, brief_id)
    items = _active_candidates(session, brief)
    lines = [
        line_from_quote(item.quote, item.creator.display_name, item.quantity, client_view=True)
        for item in items
        if item.quote is not None
    ]
    summary = summarize(lines, brief.total_budget_usd)
    return {
        "candidates": [cs.candidate_public(i) for i in items],
        "budget": cs.budget_public(summary),
    }


def _active_candidates(session: Session, brief: Brief) -> list[CandidateItem]:
    return list(
        session.scalars(
            select(CandidateItem)
            .where(CandidateItem.brief_id == brief.id, CandidateItem.removed_at.is_(None))
            .options(
                selectinload(CandidateItem.creator).selectinload(Creator.accounts),
                selectinload(CandidateItem.quote),
            )
            .order_by(CandidateItem.added_at)
        ).all()
    )


@app.post("/api/client/briefs/{brief_id}/candidates", status_code=201)
def add_candidate(
    brief_id: int,
    payload: CandidateIn,
    client: Client = Depends(current_client),
    session: Session = Depends(db),
):
    brief = _client_brief(session, client, brief_id)
    creator = session.get(Creator, payload.creator_id)
    if creator is None or not creator.is_recommendable:
        raise HTTPException(status_code=404, detail="creator not found")

    existing = session.scalar(
        select(CandidateItem).where(
            CandidateItem.brief_id == brief.id, CandidateItem.creator_id == creator.id
        )
    )
    item = existing or CandidateItem(brief_id=brief.id, creator_id=creator.id)
    item.removed_at = None  # re-adding a previously removed candidate revives it
    item.quote_id = payload.quote_id
    item.selected_content_format = payload.selected_content_format
    item.quantity = max(payload.quantity, 1)
    item.client_note = payload.client_note
    session.add(item)
    session.commit()
    session.refresh(item)
    return cs.candidate_public(item)


@app.delete("/api/client/briefs/{brief_id}/candidates/{creator_id}", status_code=204)
def remove_candidate(
    brief_id: int,
    creator_id: int,
    client: Client = Depends(current_client),
    session: Session = Depends(db),
):
    brief = _client_brief(session, client, brief_id)
    item = session.scalar(
        select(CandidateItem).where(
            CandidateItem.brief_id == brief.id, CandidateItem.creator_id == creator_id
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    # Soft-removed on purpose: the sequence of add/remove is itself feedback,
    # and a hard delete would erase it.
    item.removed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    session.commit()
    return None


# --- feedback ----------------------------------------------------------------


@app.post("/api/client/briefs/{brief_id}/feedback", status_code=201)
def submit_feedback(
    brief_id: int,
    payload: FeedbackIn,
    client: Client = Depends(current_client),
    session: Session = Depends(db),
):
    """Approve / Reject / Question, scoped to 客户 + 项目 + 对象.

    A reject removes the creator **from this brief only**. Nothing here may
    downgrade a creator in the supply layer -- another client's project is
    unaffected.
    """
    brief = _client_brief(session, client, brief_id)
    if payload.action not in FEEDBACK_ACTIONS:
        raise HTTPException(status_code=422, detail=f"action must be one of {FEEDBACK_ACTIONS}")
    creator = session.get(Creator, payload.creator_id)
    if creator is None:
        raise HTTPException(status_code=404, detail="creator not found")

    event = FeedbackEvent(
        brief_id=brief.id,
        creator_id=creator.id,
        run_id=payload.run_id,
        action=payload.action,
        reason_code=payload.reason_code,
        reason_text=payload.reason_text,
        actor=payload.actor,
        actor_side="client",
        rule_version=RULE_VERSION,
    )
    session.add(event)
    session.flush()

    for task in _tasks_for(event, brief, creator):
        session.add(task)

    _apply_to_candidates(session, brief, creator, payload)
    session.commit()
    session.refresh(event)
    return cs.feedback_public(event)


def _tasks_for(event: FeedbackEvent, brief: Brief, creator: Creator) -> list[InternalTask]:
    """Turn client feedback into work on Mango's own surface.

    This is the 反馈回流 requirement: a Question becomes an action item, a
    missing price becomes an inquiry, a missing contact becomes a route-
    building task. Never a second, isolated task system.
    """
    tasks: list[InternalTask] = []
    if event.action == "question":
        tasks.append(
            InternalTask(
                kind="client_question",
                brief_id=brief.id,
                creator_id=creator.id,
                feedback_event_id=event.id,
                title=f"客户提问：{creator.display_name}",
                detail=event.reason_text,
            )
        )
    if event.action in {"approve", "shortlist"}:
        priced = [q for q in creator.quotes if q.amount_usd is not None]
        if any(q.client_price_usd is None for q in priced):
            tasks.append(
                InternalTask(
                    kind="price_inquiry",
                    brief_id=brief.id,
                    creator_id=creator.id,
                    feedback_event_id=event.id,
                    title=f"确认对客报价：{creator.display_name}",
                    detail="客户已通过，需要 Mango 给出可对客的成交价",
                )
            )
        if not creator.contacts:
            tasks.append(
                InternalTask(
                    kind="contact_gap",
                    brief_id=brief.id,
                    creator_id=creator.id,
                    feedback_event_id=event.id,
                    title=f"补充商务路径：{creator.display_name}",
                )
            )
    return tasks


def _apply_to_candidates(
    session: Session, brief: Brief, creator: Creator, payload: FeedbackIn
) -> None:
    if payload.action in {"approve", "shortlist"}:
        existing = session.scalar(
            select(CandidateItem).where(
                CandidateItem.brief_id == brief.id, CandidateItem.creator_id == creator.id
            )
        )
        item = existing or CandidateItem(brief_id=brief.id, creator_id=creator.id)
        item.removed_at = None
        session.add(item)
    elif payload.action in {"reject", "unshortlist"}:
        item = session.scalar(
            select(CandidateItem).where(
                CandidateItem.brief_id == brief.id, CandidateItem.creator_id == creator.id
            )
        )
        if item is not None:
            item.removed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@app.get("/api/client/briefs/{brief_id}/feedback")
def list_feedback(
    brief_id: int, client: Client = Depends(current_client), session: Session = Depends(db)
):
    brief = _client_brief(session, client, brief_id)
    events = session.scalars(
        select(FeedbackEvent)
        .where(FeedbackEvent.brief_id == brief.id)
        .order_by(FeedbackEvent.created_at.desc())
    ).all()
    return {"feedback": [cs.feedback_public(e) for e in events]}


# --- internal API ------------------------------------------------------------


# Mounted with the internal guard applied to every route at once -- a
# per-route decorator is one forgotten line away from an open endpoint.
app.include_router(internal_router, dependencies=[Depends(require_internal)])

# v3 客户面。刻意**不加** require_internal —— 这一层是发给项目方的公开链接，
# 它的防护靠序列化白名单，不靠 token。见 v3_api 模块说明。
app.include_router(v3_router)
# The BD candidate surface sees costs, contacts and supplier identities, so it
# goes behind the same guard. A test enumerates every /api/internal path from
# the OpenAPI schema and asserts 401, so these are covered without anyone
# having to remember to add them.
app.include_router(bd_router, dependencies=[Depends(require_internal)])

# FrontRun 是 Mango 自己的情报面。和 bd_router 一样走内部守卫 —— 挂错命名空间
# 的后果不是权限问题，是把「我们在盯谁」告诉了客户。
app.include_router(frontrun_router, dependencies=[Depends(require_internal)])

# 客户项目面。每条路由自己带 ``Depends(current_client)`` 并按调用方作用域取数，
# 所以这里不加全局依赖 —— 加了会让 401 和 404 的语义混在一起。
app.include_router(projects_router)


@app.get("/health")
def health():
    return {"status": "ok", "rule_version": RULE_VERSION}
