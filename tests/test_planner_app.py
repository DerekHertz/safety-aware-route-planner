"""`/v1/plan` over HTTP: tester tokens, the spend cap, errors."""
from __future__ import annotations

import datetime

from fastapi.testclient import TestClient

from commute import tokens
from commute.store import TraceStore
from planner.app import create_app
from planner.routes import RouteServiceError
from planner.spend import SpendMeter
from tests.helpers.planner_fakes import FakeRoutes, ScriptedJev, parse_answers

BODY = {"origin": {"lat": 37.8, "lon": -122.27}, "destination": {"lat": 37.81, "lon": -122.26},
        "request": "safest way please", "now_local": "2026-10-27T09:30"}


def _client(tmp_path, meter=None, routes=None, require_token=True):
    app = create_app(jev=ScriptedJev(parse_answers(), choice="safe"),
                     routes=routes or FakeRoutes(), meter=meter or SpendMeter(1.0),
                     require_token=require_token, db_path=tmp_path / "c.sqlite3")
    return TestClient(app)


def _token(tmp_path) -> str:
    store = TraceStore(tmp_path / "c.sqlite3")
    store.init_schema()
    return tokens.issue(store, "tester").token


def test_a_tester_token_is_required(tmp_path):
    with _client(tmp_path) as c:
        assert c.post("/v1/plan", json=BODY).status_code == 401
        bad = c.post("/v1/plan", json=BODY, headers={"Authorization": "Bearer srt_nope"})
        assert bad.status_code == 401
        ok = c.post("/v1/plan", json=BODY,
                    headers={"Authorization": f"Bearer {_token(tmp_path)}"})
        assert ok.status_code == 200
        assert ok.json()["choice"] == "safe"


def test_local_dev_can_skip_tokens(tmp_path):
    with _client(tmp_path, require_token=False) as c:
        assert c.post("/v1/plan", json=BODY).status_code == 200
        assert c.get("/health").json() == {"status": "ok", "jev_configured": True}


def test_the_daily_spend_cap_is_a_503_and_resets_the_next_day(tmp_path):
    day = [datetime.date(2026, 10, 27)]
    meter = SpendMeter(0.00005, today=lambda: day[0])   # just over one plan's cost
    with _client(tmp_path, meter=meter, require_token=False) as c:
        assert c.post("/v1/plan", json=BODY).status_code == 200     # spends 2000 tokens
        assert c.post("/v1/plan", json=BODY).status_code == 503
        day[0] = datetime.date(2026, 10, 28)
        assert c.post("/v1/plan", json=BODY).status_code == 200


def test_a_validation_error_never_echoes_the_request_text(tmp_path):
    with _client(tmp_path, require_token=False) as c:
        resp = c.post("/v1/plan", json={**BODY, "request": "x" * 501})
        assert resp.status_code == 422
        assert resp.json()["detail"].startswith("request:")
        assert "xxxx" not in resp.text


def test_a_route_service_refusal_passes_through(tmp_path):
    routes = FakeRoutes(error=RouteServiceError(422, "outside every served region"))
    with _client(tmp_path, routes=routes, require_token=False) as c:
        resp = c.post("/v1/plan", json=BODY)
        assert resp.status_code == 422
        assert resp.json() == {"detail": "outside every served region"}
