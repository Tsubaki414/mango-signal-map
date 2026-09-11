"""Internal operating-surface tests.

Two things matter more than the CRUD: that **every** internal route is behind
the guard (a per-route decorator is one forgotten line from an open endpoint),
and that the loop actually closes — client feedback raises a task, doing the
work closes it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from signal_map.backend import app as app_module
from signal_map.backend.models import (
    Base,
    Client,
    ContactMethod,
    Creator,
    InternalTask,
    Quote,
    QuoteMessage,
    SocialAccount,
)

INTERNAL = "internal-test-token"


@pytest.fixture
def env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def override():
        session = Local()
        try:
            yield session
        finally:
            session.close()

    # One override point covers both routers -- see db.session_dependency.
    app_module.app.dependency_overrides[app_module.db] = override
    monkeypatch.setenv("SIGNAL_MAP_INTERNAL_TOKEN", INTERNAL)

    with Local() as seed:
        _seed(seed)
    yield TestClient(app_module.app), Local
    app_module.app.dependency_overrides.clear()


def _seed(session: Session) -> None:
    client = Client(name="ACME", api_token="tok-acme")
    session.add(client)
    creator = Creator(
        display_name="Dev Voice", primary_handle="devvoice",
        creator_class="KOL", creator_tier="strategic",
        verticals="ai", market_region="europe_america", languages="en",
        audience_types="developers",
    )
    session.add(creator)
    session.flush()
    session.add(SocialAccount(creator_id=creator.id, platform="X", handle="devvoice", followers=50_000))
    session.add(ContactMethod(creator_id=creator.id, method_type="email", value="a@b.c"))
    message = QuoteMessage(creator_id=creator.id, raw_text="X Thread: $1,300")
    session.add(message)
    session.flush()
    session.add(
        Quote(
            creator_id=creator.id, message_id=message.id,
            deliverable_raw="X Thread", content_format="x_thread", platform="X",
            amount=1300.0, amount_usd=1300.0, currency="USD",
            internal_cost_usd=1300.0, client_price_band="$1,000–2,500",
            status="needs_review", needs_review=True, parse_confidence="medium",
            raw_segment="X Thread: $1,300",
        )
    )
    session.commit()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- the guard ---------------------------------------------------------------


def test_every_internal_route_requires_the_internal_token(env):
    """Enumerated from the app itself, so a route added later is covered
    automatically rather than being remembered."""
    client, _ = env
    # Read from the OpenAPI schema, not ``app.routes``: this FastAPI version
    # wraps an included router in ``_IncludedRouter`` rather than flattening
    # its routes, so walking ``app.routes`` finds nothing and the test would
    # pass vacuously while guarding no endpoint at all.
    schema = app_module.app.openapi()
    internal = {
        (path, method)
        for path, methods in schema["paths"].items()
        if path.startswith("/api/internal")
        for method in methods
    }
    assert internal, "no internal routes registered"

    for path, method in internal:
        probe = (
            path.replace("{task_id}", "1")
            .replace("{quote_id}", "1")
            .replace("{brief_id}", "1")
        )
        # GET/DELETE take no body in this client, so only send one where the
        # verb accepts it.
        kwargs = {"json": {}} if method in {"post", "patch", "put"} else {}
        response = getattr(client, method)(probe, **kwargs)
        assert response.status_code == 401, (method, probe, response.status_code)


def test_a_client_token_cannot_reach_the_internal_surface(env):
    client, _ = env
    for path in ("/api/internal/tasks", "/api/internal/coverage",
                 "/api/internal/quotes/review-queue"):
        assert client.get(path, headers=auth("tok-acme")).status_code == 401


def test_internal_api_fails_closed_without_a_configured_token(env, monkeypatch):
    client, _ = env
    monkeypatch.delenv("SIGNAL_MAP_INTERNAL_TOKEN", raising=False)
    assert client.get("/api/internal/tasks", headers=auth(INTERNAL)).status_code == 503


# --- the loop closes ---------------------------------------------------------


def test_client_approval_raises_a_task_the_internal_queue_can_see(env):
    """The gap this surface exists to close: feedback used to flow in and stop."""
    client, _ = env
    brief = client.post(
        "/api/client/briefs",
        json={"name": "Launch", "verticals": ["ai"], "total_budget_usd": 10000},
        headers=auth("tok-acme"),
    ).json()
    client.post(
        f"/api/client/briefs/{brief['id']}/feedback",
        json={"creator_id": 1, "action": "approve"},
        headers=auth("tok-acme"),
    )

    queue = client.get("/api/internal/tasks", headers=auth(INTERNAL)).json()
    kinds = {t["kind"] for t in queue["tasks"]}
    assert "price_inquiry" in kinds
    # And it carries the context needed to act on it.
    task = next(t for t in queue["tasks"] if t["kind"] == "price_inquiry")
    assert task["creator"]["name"] == "Dev Voice"
    assert task["client"]["name"] == "ACME"


def test_setting_a_client_price_closes_the_pricing_task(env):
    """Otherwise the queue only grows: the work happens in one endpoint while
    the task recording it stays open in another."""
    client, Local = env
    brief = client.post(
        "/api/client/briefs", json={"name": "L", "total_budget_usd": 10000},
        headers=auth("tok-acme"),
    ).json()
    client.post(
        f"/api/client/briefs/{brief['id']}/feedback",
        json={"creator_id": 1, "action": "approve"},
        headers=auth("tok-acme"),
    )
    client.patch(
        "/api/internal/quotes/1/review",
        json={"actor": "fiona", "needs_review": False, "status": "confirmed_valid"},
        headers=auth(INTERNAL),
    )
    client.patch(
        "/api/internal/quotes/1/pricing",
        json={"actor": "fiona", "client_price_usd": 2000.0, "client_visible": True},
        headers=auth(INTERNAL),
    )

    with Local() as session:
        task = session.scalar(select(InternalTask).where(InternalTask.kind == "price_inquiry"))
    assert task.status == "done"
    assert "fiona" in (task.outcome_notes or "")


def test_review_alone_does_not_close_a_pricing_task(env):
    """Reviewing what the creator charges is not deciding what to charge the
    client; only the latter satisfies a 询价 task."""
    client, Local = env
    brief = client.post(
        "/api/client/briefs", json={"name": "L", "total_budget_usd": 10000},
        headers=auth("tok-acme"),
    ).json()
    client.post(
        f"/api/client/briefs/{brief['id']}/feedback",
        json={"creator_id": 1, "action": "approve"},
        headers=auth("tok-acme"),
    )
    client.patch(
        "/api/internal/quotes/1/review",
        json={"actor": "fiona", "needs_review": False},
        headers=auth(INTERNAL),
    )
    with Local() as session:
        task = session.scalar(select(InternalTask).where(InternalTask.kind == "price_inquiry"))
    assert task.status == "open"


# --- quote review ------------------------------------------------------------


def test_review_queue_shows_the_source_text_being_judged(env):
    """The parse is what is under review; hiding what it came from would make
    the review impossible."""
    client, _ = env
    payload = client.get("/api/internal/quotes/review-queue", headers=auth(INTERNAL)).json()
    quote = payload["quotes"][0]
    assert quote["raw_message"] == "X Thread: $1,300"
    assert quote["internal_cost_usd"] == 1300.0  # internal surface may see cost


def test_correcting_the_amount_rederives_the_client_band(env):
    """A corrected figure must not leave a stale band behind -- the band is
    what a client actually sees."""
    client, _ = env
    before = client.get("/api/internal/quotes/review-queue", headers=auth(INTERNAL)).json()
    assert before["quotes"][0]["client_price_band"] == "$1,000–2,500"

    after = client.patch(
        "/api/internal/quotes/1/review",
        json={"actor": "fiona", "amount_usd": 300.0, "needs_review": False},
        headers=auth(INTERNAL),
    ).json()
    assert after["client_price_band"] == "$500 以下"
    assert after["internal_cost_usd"] == 300.0


def test_a_quote_still_flagged_for_review_cannot_be_cleared_for_display(env):
    client, _ = env
    response = client.patch(
        "/api/internal/quotes/1/pricing",
        json={"actor": "fiona", "client_visible": True},
        headers=auth(INTERNAL),
    )
    assert response.status_code == 422
    assert "needs_review" in response.text


def test_a_client_price_below_cost_is_refused_rather_than_absorbed(env):
    client, _ = env
    response = client.patch(
        "/api/internal/quotes/1/pricing",
        json={"actor": "fiona", "client_price_usd": 100.0},
        headers=auth(INTERNAL),
    )
    assert response.status_code == 422
    assert "below" in response.text.lower()


def test_pricing_requires_an_actor(env):
    """A review with no reviewer is not a review."""
    client, _ = env
    response = client.patch(
        "/api/internal/quotes/1/pricing",
        json={"client_price_usd": 2000.0},
        headers=auth(INTERNAL),
    )
    assert response.status_code == 422


# --- task queue --------------------------------------------------------------


def test_closing_a_task_stamps_a_time(env):
    client, _ = env
    task = client.post(
        "/api/internal/tasks",
        json={"kind": "other", "title": "check something"},
        headers=auth(INTERNAL),
    ).json()
    done = client.patch(
        f"/api/internal/tasks/{task['id']}",
        json={"status": "done", "outcome_notes": "handled"},
        headers=auth(INTERNAL),
    ).json()
    assert done["status"] == "done"
    assert done["closed_at"] is not None


def test_invalid_task_status_is_rejected(env):
    client, _ = env
    task = client.post(
        "/api/internal/tasks", json={"kind": "other", "title": "t"}, headers=auth(INTERNAL)
    ).json()
    response = client.patch(
        f"/api/internal/tasks/{task['id']}", json={"status": "vanished"},
        headers=auth(INTERNAL),
    )
    assert response.status_code == 422


def test_summary_reports_queue_depth(env):
    client, _ = env
    client.post(
        "/api/internal/tasks", json={"kind": "price_review", "title": "a"},
        headers=auth(INTERNAL),
    )
    summary = client.get("/api/internal/tasks/summary", headers=auth(INTERNAL)).json()
    assert summary["open_total"] >= 1
    assert "price_review" in summary["by_kind"]


# --- pipeline + coverage -----------------------------------------------------


def test_pipeline_shows_contacts_that_the_client_api_never_returns(env):
    client, _ = env
    brief = client.post(
        "/api/client/briefs", json={"name": "L", "total_budget_usd": 10000},
        headers=auth("tok-acme"),
    ).json()
    client.post(
        f"/api/client/briefs/{brief['id']}/candidates",
        json={"creator_id": 1}, headers=auth("tok-acme"),
    )
    pipeline = client.get(
        f"/api/internal/briefs/{brief['id']}/pipeline", headers=auth(INTERNAL)
    ).json()
    assert pipeline["candidates"][0]["contacts"][0]["value"] == "a@b.c"


def test_coverage_counts_the_gaps_rather_than_only_showing_them_per_card(env):
    client, _ = env
    coverage = client.get("/api/internal/coverage", headers=auth(INTERNAL)).json()
    assert coverage["priced_creators"] == 1
    assert coverage["quotes_needing_review"] == 1
    assert coverage["quotes_with_client_price"] == 0
