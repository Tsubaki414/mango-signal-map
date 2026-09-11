"""API tests, weighted toward the isolation guarantees.

The routes are the easy part. What matters is that no client response can
carry an internal field, and that one client cannot read another's data --
so most of this file is adversarial rather than happy-path.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session, sessionmaker

from signal_map.backend import app as app_module
from signal_map.backend.client_safe import FORBIDDEN_CLIENT_FIELDS
from signal_map.backend.models import (
    AttentionSignal,
    Base,
    Client,
    ContactMethod,
    Creator,
    InternalTask,
    ProcurementRoute,
    Quote,
    QuoteMessage,
    SocialAccount,
)
from signal_map.backend.observation_models import XAccount

INTERNAL_TOKEN = "internal-test-token"


@pytest.fixture
def env(monkeypatch):
    # StaticPool is required: plain "sqlite://" hands every new connection its
    # own empty in-memory database, so the tables created here would be
    # invisible to the request-scoped sessions below.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Local = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        session = Local()
        try:
            yield session
        finally:
            session.close()

    app_module.app.dependency_overrides[app_module.db] = override_db
    monkeypatch.setenv("SIGNAL_MAP_INTERNAL_TOKEN", INTERNAL_TOKEN)

    with Local() as seed:
        _seed(seed)

    yield TestClient(app_module.app), Local
    app_module.app.dependency_overrides.clear()


def _seed(session: Session) -> None:
    acme = Client(name="ACME", api_token="tok-acme", internal_notes="pays late")
    rival = Client(name="Rival", api_token="tok-rival")
    session.add_all([acme, rival])
    session.flush()

    creator = Creator(
        display_name="Dev Voice",
        primary_handle="devvoice",
        creator_class="KOL",
        creator_tier="strategic",
        verticals="ai,developer_tools",
        market_region="europe_america",
        market_region_basis="language",
        languages="en",
        audience_types="developers",
        audience_types_basis="llm",
        audience_types_evidence='developers:"ships code"',
        internal_notes="SENSITIVE-INTERNAL-NOTE",
    )
    session.add(creator)
    session.flush()

    session.add(
        SocialAccount(
            creator_id=creator.id, platform="X", handle="devvoice", followers=120_000, is_primary=True
        )
    )
    session.add(
        ContactMethod(creator_id=creator.id, method_type="email", value="secret@private.example")
    )
    session.add(
        ProcurementRoute(
            creator_id=creator.id,
            route_type="agency",
            counterparty_name="SECRET AGENCY LTD",
            provides_quotes=True,
        )
    )
    message = QuoteMessage(
        creator_id=creator.id, raw_text="Thread $1,300 (via manager)", source="kol_direct"
    )
    session.add(message)
    session.flush()
    session.add(
        Quote(
            creator_id=creator.id,
            message_id=message.id,
            deliverable_raw="X Thread",
            content_format="x_thread",
            platform="X",
            amount=1300.0,
            amount_usd=1300.0,
            currency="USD",
            status="confirmed_valid",
            needs_review=False,
            parse_confidence="high",
            internal_cost_usd=1300.0,
            client_price_usd=None,
            client_price_band="$1,000–2,500",
            client_visible=True,
            raw_segment="Thread $1,300",
        )
    )

    # An accepted Root, with a reviewer's private note and a machine
    # suggestion attached. The axis is *meant* to name the Root and its type on
    # a client card -- so the note and the suggestion basis sit right beside
    # values that legitimately travel outward, which is precisely the shape
    # that leaks. Scanned below.
    session.add(
        XAccount(
            rest_id="root-1",
            handle="realroot",
            display_name="Real Root",
            bio="Managing Partner",
            followers=900_000,
            following=3_000,
            roles="root",
            root_type="investor",
            root_review_status="accepted",
            root_reviewed_by="REVIEWER-PRIVATE-NAME",
            root_note="ROOT-REVIEWER-NOTE",
            root_suggestion_basis="ROOT-SUGGESTION-BASIS",
            root_suggested_by="llm:some-model",
        )
    )
    session.add(
        XAccount(rest_id="roster-1", handle="devvoice", roles="roster", creator_id=creator.id)
    )
    session.flush()
    session.add(
        AttentionSignal(
            source_node="root-1",
            target_node="roster-1",
            source_creator_id=creator.id,
            platform="X",
            signal_type="follow",
            direction="source_to_target",
            observation_count=1,
            raw_evidence="@realroot follows @devvoice",
            confidence="research_lead",
            human_confirmed=False,
        )
    )
    session.commit()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def make_brief(client: TestClient, token: str = "tok-acme", **overrides) -> dict:
    payload = {
        "name": "Launch",
        "verticals": ["ai"],
        "target_markets": ["europe_america"],
        "content_languages": ["en"],
        "target_audiences": ["developers"],
        "objectives": ["credibility"],
        "platforms": ["X"],
        "content_formats": ["x_thread"],
        "total_budget_usd": 20000,
        "per_creator_budget_max_usd": 5000,
    }
    payload.update(overrides)
    response = client.post("/api/client/briefs", json=payload, headers=auth(token))
    assert response.status_code == 201, response.text
    return response.json()


def walk_strings(node) -> list[str]:
    """Every string anywhere in a response, for leak scanning."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append(str(key))
            found.extend(walk_strings(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(walk_strings(item))
    elif node is not None:
        found.append(str(node))
    return found


# --- auth --------------------------------------------------------------------


def test_client_api_requires_a_token(env):
    client, _ = env
    assert client.get("/api/client/briefs").status_code == 401


def test_invalid_token_is_rejected(env):
    client, _ = env
    assert client.get("/api/client/briefs", headers=auth("nope")).status_code == 401


def test_internal_token_does_not_open_the_client_api(env):
    client, _ = env
    assert client.get("/api/client/briefs", headers=auth(INTERNAL_TOKEN)).status_code == 401


def test_client_token_cannot_reach_the_internal_api(env):
    client, _ = env
    assert client.get("/api/internal/tasks", headers=auth("tok-acme")).status_code == 401
    assert (
        client.get("/api/internal/quotes/review-queue", headers=auth("tok-acme")).status_code == 401
    )


def test_internal_api_fails_closed_when_unconfigured(env, monkeypatch):
    client, _ = env
    monkeypatch.delenv("SIGNAL_MAP_INTERNAL_TOKEN", raising=False)
    assert client.get("/api/internal/tasks", headers=auth(INTERNAL_TOKEN)).status_code == 503


# --- cross-client isolation --------------------------------------------------


def test_one_client_cannot_read_anothers_brief(env):
    client, _ = env
    brief = make_brief(client, "tok-acme")
    response = client.get(f"/api/client/briefs/{brief['id']}", headers=auth("tok-rival"))
    # 404 not 403: a 403 would confirm the id exists, which is itself a leak.
    assert response.status_code == 404


def test_brief_list_is_scoped_to_the_caller(env):
    client, _ = env
    make_brief(client, "tok-acme")
    assert client.get("/api/client/briefs", headers=auth("tok-rival")).json()["briefs"] == []


def test_one_client_cannot_read_anothers_run(env):
    client, _ = env
    brief = make_brief(client, "tok-acme")
    run = client.post(
        f"/api/client/briefs/{brief['id']}/recommendations", headers=auth("tok-acme")
    ).json()
    assert client.get(f"/api/client/runs/{run['run_id']}", headers=auth("tok-rival")).status_code == 404


def test_one_client_cannot_write_feedback_on_anothers_brief(env):
    client, _ = env
    brief = make_brief(client, "tok-acme")
    response = client.post(
        f"/api/client/briefs/{brief['id']}/feedback",
        json={"creator_id": 1, "action": "approve"},
        headers=auth("tok-rival"),
    )
    assert response.status_code == 404


# --- field-level leak scanning -----------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "1300",
        # Comma-formatted too: the budget axis rendered the cost as "$1,300"
        # inside a prose string, and a bare "1300" check sailed straight past
        # it. That was a real leak found only when samples were eyeballed.
        "1,300",
        "$1,300",
        "SECRET AGENCY LTD",
        "secret@private.example",
        "SENSITIVE-INTERNAL-NOTE",
        "Thread $1,300",
        # Root review. The axis names the Root and its type to the client on
        # purpose, so everything else attached to that same row -- who reviewed
        # it, what they privately noted, what the model argued -- is one field
        # away from riding along.
        "ROOT-REVIEWER-NOTE",
        "REVIEWER-PRIVATE-NAME",
        "ROOT-SUGGESTION-BASIS",
        "llm:some-model",
    ],
)
def test_no_client_response_leaks_an_internal_value(env, secret):
    """Scans every string in every client payload for known-secret values."""
    client, _ = env
    brief = make_brief(client, "tok-acme")
    run = client.post(
        f"/api/client/briefs/{brief['id']}/recommendations", headers=auth("tok-acme")
    ).json()
    client.post(
        f"/api/client/briefs/{brief['id']}/candidates",
        json={"creator_id": 1},
        headers=auth("tok-acme"),
    )
    payloads = [
        run,
        # Scenarios were added after this test and leaked cost through a
        # utilisation note until it was scanned too. Every new client endpoint
        # belongs in this list.
        client.get(f"/api/client/briefs/{brief['id']}/scenarios", headers=auth("tok-acme")).json(),
        client.get("/api/client/filters").json(),
        client.get(f"/api/client/briefs/{brief['id']}", headers=auth("tok-acme")).json(),
        client.get("/api/client/creators/1", headers=auth("tok-acme")).json(),
        client.get(
            f"/api/client/briefs/{brief['id']}/candidates", headers=auth("tok-acme")
        ).json(),
    ]
    for payload in payloads:
        assert secret not in walk_strings(payload), f"leaked {secret!r} in {payload}"


def test_the_leak_fixture_actually_reaches_the_root_axis(env):
    """Guards the scan above from passing vacuously.

    The Root secrets are only meaningful if the accepted Root genuinely renders
    on a client card. If a change stopped the axis naming Roots at all, the
    parametrised scan would still pass -- while testing nothing. So assert the
    thing that *should* be visible really is.
    """
    client, _ = env
    brief = make_brief(client, "tok-acme")
    run = client.post(
        f"/api/client/briefs/{brief['id']}/recommendations", headers=auth("tok-acme")
    ).json()
    text = "\n".join(walk_strings(run))
    assert "已确认 Root（投资人）" in text
    assert "@realroot" in text


def test_no_client_payload_renders_an_exact_cost_in_prose(env, Local=None):
    """The leak class that field-name allowlists cannot catch.

    ``internal_cost_usd`` was appearing *inside* generated sentences -- the
    budget axis, the reasons list, and the matched-preferences list all
    formatted it as "$1,300". Checks every rendering of the number, because
    the first version of this test looked for "1300" and sailed past "$1,300".
    """
    client, Local = env
    brief = make_brief(client, "tok-acme")
    run = client.post(
        f"/api/client/briefs/{brief['id']}/recommendations", headers=auth("tok-acme")
    ).json()

    with Local() as session:
        costs = [q.internal_cost_usd for q in session.query(Quote).all() if q.internal_cost_usd]
    assert costs, "fixture must have a cost to leak"

    blob = json.dumps(run, ensure_ascii=False)
    for cost in costs:
        for rendering in (f"{cost:,.0f}", f"{cost:.0f}", f"${cost:,.0f}", f"${cost:.0f}"):
            # A band may legitimately start with the same digits ("$1,000–2,500"),
            # so only flag a rendering that is not immediately followed by a dash.
            for match in __import__("re").finditer(__import__("re").escape(rendering), blob):
                tail = blob[match.end() : match.end() + 1]
                assert tail in "–-", (
                    f"exact cost {rendering!r} rendered in a client payload: "
                    f"{blob[max(0, match.start() - 60): match.end() + 30]}"
                )


def test_scenario_notes_never_expose_spend_by_subtraction(env):
    """A note saying "仅用掉预算的 7%" beside a visible budget hands over the
    exact internal cost. Neither the figure nor the percentage may appear."""
    import re as _re

    client, _ = env
    brief = make_brief(client, "tok-acme")
    payload = client.get(
        f"/api/client/briefs/{brief['id']}/scenarios", headers=auth("tok-acme")
    ).json()

    for scenario in payload["scenarios"]:
        for note in scenario["notes"]:
            assert "$" not in note, note
            assert not _re.search(r"\d+\s*%", note), note


def test_no_client_response_carries_a_forbidden_field_name(env):
    client, _ = env
    brief = make_brief(client, "tok-acme")
    run = client.post(
        f"/api/client/briefs/{brief['id']}/recommendations", headers=auth("tok-acme")
    ).json()
    creator = client.get("/api/client/creators/1", headers=auth("tok-acme")).json()

    for payload in (run, creator):
        keys = set(walk_strings(payload))
        assert not (keys & FORBIDDEN_CLIENT_FIELDS), keys & FORBIDDEN_CLIENT_FIELDS


def test_client_sees_a_band_never_the_cost(env):
    client, _ = env
    creator = client.get("/api/client/creators/1", headers=auth("tok-acme")).json()
    quote = creator["quote"]
    assert quote["price_band"] == "$1,000–2,500"
    assert quote["price_exact_usd"] is None
    assert "1300" not in json.dumps(quote)


def test_rank_score_is_never_exposed(env):
    """No surface may show a bare score, so the client layer never gets one."""
    client, _ = env
    brief = make_brief(client, "tok-acme")
    run = client.post(
        f"/api/client/briefs/{brief['id']}/recommendations", headers=auth("tok-acme")
    ).json()
    assert "rank_score" not in walk_strings(run)


# --- behaviour ---------------------------------------------------------------


def test_recommendation_returns_every_axis(env):
    client, _ = env
    brief = make_brief(client, "tok-acme")
    run = client.post(
        f"/api/client/briefs/{brief['id']}/recommendations", headers=auth("tok-acme")
    ).json()
    from signal_map.backend.recommend import AXES

    assert run["results"]
    assert len(run["results"][0]["axes"]) == len(AXES)
    assert run["results"][0]["reasons"]


def test_unsupported_filters_are_reported_to_the_client(env):
    client, _ = env
    brief = make_brief(client, "tok-acme", creator_size_preference="mid")
    run = client.post(
        f"/api/client/briefs/{brief['id']}/recommendations", headers=auth("tok-acme")
    ).json()
    assert run["unsupported_filters"]


def test_filters_only_offer_values_present_in_priced_inventory(env):
    client, _ = env
    filters = client.get("/api/client/filters").json()
    assert filters["pool_size"] == 1
    assert "ai" in filters["verticals"]
    assert "crypto" not in filters["verticals"]


def test_approve_creates_a_candidate_and_an_internal_task(env):
    """反馈回流: a client approval becomes work on Mango's own surface."""
    client, Local = env
    brief = make_brief(client, "tok-acme")
    response = client.post(
        f"/api/client/briefs/{brief['id']}/feedback",
        json={"creator_id": 1, "action": "approve"},
        headers=auth("tok-acme"),
    )
    assert response.status_code == 201

    candidates = client.get(
        f"/api/client/briefs/{brief['id']}/candidates", headers=auth("tok-acme")
    ).json()
    assert len(candidates["candidates"]) == 1

    with Local() as session:
        kinds = {t.kind for t in session.query(InternalTask).all()}
    # No client price is set, so approving must raise the pricing task.
    assert "price_inquiry" in kinds


def test_question_becomes_an_internal_action_item(env):
    client, Local = env
    brief = make_brief(client, "tok-acme")
    client.post(
        f"/api/client/briefs/{brief['id']}/feedback",
        json={"creator_id": 1, "action": "question", "reason_text": "受众地区?"},
        headers=auth("tok-acme"),
    )
    with Local() as session:
        task = session.query(InternalTask).filter_by(kind="client_question").one()
    assert "受众地区?" == task.detail


def test_reject_removes_from_this_brief_only(env):
    """A reject must not downgrade the creator in the supply layer."""
    client, Local = env
    brief = make_brief(client, "tok-acme")
    client.post(
        f"/api/client/briefs/{brief['id']}/feedback",
        json={"creator_id": 1, "action": "approve"},
        headers=auth("tok-acme"),
    )
    client.post(
        f"/api/client/briefs/{brief['id']}/feedback",
        json={"creator_id": 1, "action": "reject", "reason_code": "off_topic"},
        headers=auth("tok-acme"),
    )
    candidates = client.get(
        f"/api/client/briefs/{brief['id']}/candidates", headers=auth("tok-acme")
    ).json()
    assert candidates["candidates"] == []

    with Local() as session:
        creator = session.get(Creator, 1)
        assert creator.creator_tier == "strategic"  # untouched
        assert creator.is_recommendable


def test_feedback_history_is_append_only(env):
    client, _ = env
    brief = make_brief(client, "tok-acme")
    for action in ("approve", "reject", "approve"):
        client.post(
            f"/api/client/briefs/{brief['id']}/feedback",
            json={"creator_id": 1, "action": action},
            headers=auth("tok-acme"),
        )
    events = client.get(
        f"/api/client/briefs/{brief['id']}/feedback", headers=auth("tok-acme")
    ).json()["feedback"]
    assert len(events) == 3


def test_invalid_feedback_action_is_rejected(env):
    client, _ = env
    brief = make_brief(client, "tok-acme")
    response = client.post(
        f"/api/client/briefs/{brief['id']}/feedback",
        json={"creator_id": 1, "action": "destroy"},
        headers=auth("tok-acme"),
    )
    assert response.status_code == 422


def test_client_budget_shows_tbd_while_no_client_price_is_set(env):
    """The internal cost total is never substituted for a client total."""
    client, _ = env
    brief = make_brief(client, "tok-acme")
    client.post(
        f"/api/client/briefs/{brief['id']}/candidates",
        json={"creator_id": 1, "quote_id": 1},
        headers=auth("tok-acme"),
    )
    budget = client.get(
        f"/api/client/briefs/{brief['id']}/candidates", headers=auth("tok-acme")
    ).json()["budget"]
    assert budget["total_usd"] is None
    assert budget["total_label"] == "价格待 Mango 确认"
    assert budget["needs_mango_confirmation"] is True


def test_internal_review_queue_may_show_cost(env):
    """The internal surface is allowed what the client surface is not."""
    client, _ = env
    response = client.get(
        "/api/internal/quotes/review-queue", headers=auth(INTERNAL_TOKEN)
    )
    assert response.status_code == 200
    assert "quotes" in response.json()
