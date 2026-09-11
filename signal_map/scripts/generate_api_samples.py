"""Generate example API responses for the Lovable frontend.

Runs the real client API against the real database and writes each response to
``signal_map/docs/api_samples/``. Generated rather than hand-written on
purpose: a hand-written sample drifts from the code the moment a field
changes, and a frontend built against a stale contract fails at integration.

Because every payload here is produced by the same client serializers the
production routes use, these files are also a standing check that no internal
field reaches a client -- if one ever did, it would appear in this directory.

Usage::

    python -m signal_map.scripts.generate_api_samples
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from signal_map.backend import app as app_module  # noqa: E402
from signal_map.backend import db as sm_db  # noqa: E402
from signal_map.backend.client_safe import FORBIDDEN_CLIENT_FIELDS  # noqa: E402
from signal_map.backend.models import (  # noqa: E402
    Brief,
    CandidateItem,
    Client,
    FeedbackEvent,
    InternalTask,
)

OUT_DIR = REPO_ROOT / "signal_map" / "docs" / "api_samples"
DEMO_CLIENT = "示例客户 (Lovable 对接用)"

#: A brief broad enough to return a rich batch, so the frontend sees the full
#: shape of an axis list rather than a handful of matches.
DEMO_BRIEF = {
    "name": "AI 开发者工具发布 · 欧美",
    "product_description": "面向开发者的 AI coding agent，首次公开发布",
    "verticals": ["ai", "developer_tools"],
    "target_markets": ["europe_america"],
    "content_languages": ["en"],
    "target_audiences": ["developers", "founders"],
    "objectives": ["credibility", "trial"],
    "platforms": ["X", "YouTube"],
    "content_formats": ["x_thread", "youtube_dedicated"],
    "total_budget_usd": 40000,
    "per_creator_budget_min_usd": 200,
    "per_creator_budget_max_usd": 8000,
    "risk_tolerance": "low",
    "creator_size_preference": "mid",
}


def _demo_client(session) -> Client:
    """A stable demo client, reusing its token across runs."""
    client = session.scalar(select(Client).where(Client.name == DEMO_CLIENT))
    if client is None:
        client = Client(name=DEMO_CLIENT, api_token=f"demo-{secrets.token_hex(12)}")
        session.add(client)
        session.commit()
    elif not client.api_token:
        client.api_token = f"demo-{secrets.token_hex(12)}"
        session.commit()
    return client


def _reset_demo_brief(session, client: Client) -> None:
    """Drop the previous demo brief so samples never accumulate stale state.

    Deletion order matters: ``internal_tasks`` references ``feedback_events``,
    which references ``briefs``. Deleting the brief first, or the feedback
    first, trips the foreign key -- and a failed reset silently leaves the
    previous run's samples on disk, which is how a stale contract reaches a
    frontend.
    """
    brief_ids = [
        b.id for b in session.scalars(select(Brief).where(Brief.client_id == client.id))
    ]
    if not brief_ids:
        return
    session.execute(InternalTask.__table__.delete().where(InternalTask.brief_id.in_(brief_ids)))
    session.execute(FeedbackEvent.__table__.delete().where(FeedbackEvent.brief_id.in_(brief_ids)))
    session.execute(CandidateItem.__table__.delete().where(CandidateItem.brief_id.in_(brief_ids)))
    for brief in session.scalars(select(Brief).where(Brief.id.in_(brief_ids))):
        session.delete(brief)  # runs + recommendations cascade
    session.commit()


def _write(name: str, payload) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _assert_clean(name: str, payload) -> None:
    """Fail loudly rather than write a sample that leaks."""
    blob = json.dumps(payload, ensure_ascii=False)
    for field in FORBIDDEN_CLIENT_FIELDS:
        if f'"{field}"' in blob:
            raise SystemExit(f"{name}: forbidden field {field!r} present in a client payload")


def main() -> None:
    with sm_db.get_session() as session:
        client = _demo_client(session)
        _reset_demo_brief(session, client)
        token = client.api_token

    http = TestClient(app_module.app)
    auth = {"Authorization": f"Bearer {token}"}
    written: list[tuple[str, Path]] = []

    def capture(name: str, response) -> dict:
        if response.status_code >= 400:
            raise SystemExit(f"{name}: HTTP {response.status_code} {response.text[:300]}")
        payload = response.json()
        _assert_clean(name, payload)
        written.append((name, _write(name, payload)))
        return payload

    capture("01_filters", http.get("/api/client/filters"))
    brief = capture("02_brief_created", http.post("/api/client/briefs", json=DEMO_BRIEF, headers=auth))
    brief_id = brief["id"]

    run = capture(
        "03_recommendations",
        http.post(f"/api/client/briefs/{brief_id}/recommendations?limit=12", headers=auth),
    )

    if run["results"]:
        creator_id = run["results"][0]["creator"]["id"]
        capture("04_creator_detail", http.get(f"/api/client/creators/{creator_id}", headers=auth))
        capture(
            "05_candidate_added",
            http.post(
                f"/api/client/briefs/{brief_id}/candidates",
                json={"creator_id": creator_id, "quantity": 1},
                headers=auth,
            ),
        )
        capture(
            "06_feedback_approve",
            http.post(
                f"/api/client/briefs/{brief_id}/feedback",
                json={"creator_id": creator_id, "action": "approve", "run_id": run["run_id"]},
                headers=auth,
            ),
        )
        if len(run["results"]) > 1:
            other = run["results"][1]["creator"]["id"]
            capture(
                "07_feedback_question",
                http.post(
                    f"/api/client/briefs/{brief_id}/feedback",
                    json={
                        "creator_id": other,
                        "action": "question",
                        "reason_text": "这个账号的受众主要在哪些国家？",
                        "run_id": run["run_id"],
                    },
                    headers=auth,
                ),
            )

    capture("08_candidates_with_budget", http.get(f"/api/client/briefs/{brief_id}/candidates", headers=auth))
    capture("12_scenario_meta", http.get("/api/client/scenarios/meta"))
    capture("13_scenarios", http.get(f"/api/client/briefs/{brief_id}/scenarios", headers=auth))
    capture("09_feedback_history", http.get(f"/api/client/briefs/{brief_id}/feedback", headers=auth))

    # Error shapes matter to a frontend as much as success shapes do.
    _write("10_error_unauthorized", {"status": 401, "body": http.get("/api/client/briefs").json()})
    _write(
        "11_error_not_found",
        {"status": 404, "body": http.get("/api/client/creators/999999", headers=auth).json()},
    )

    print(f"demo client token: {token}")
    print(f"wrote {len(written) + 2} samples to {OUT_DIR}")
    for name, path in written:
        print(f"  {name:26} {path.stat().st_size:>7,} bytes")


if __name__ == "__main__":
    main()
