"""API contract lock: exact JSON key sets on a toy pack served through the
real FastAPI app (SR_PACK_DIR override). A future mobile client relies on
this shape — if a key changes, this test must be updated CONSCIOUSLY."""
import os

import pytest
from fastapi.testclient import TestClient

from tests.helpers.fixtures import unprotected_left_city


@pytest.fixture()
def client(tmp_path, monkeypatch):
    pack, ids = unprotected_left_city()
    pack.write(tmp_path / "toy")
    monkeypatch.setenv("SR_PACK_DIR", str(tmp_path / "toy"))
    from api.main import create_app
    app = create_app()
    with TestClient(app) as c:
        c.ids = ids
        c.pack = pack
        yield c


def _route_body(pack, ids, safety=True):
    # node coords double as origin/destination points (perfect snaps)
    o = ids["s"]
    d = ids["a0"]
    return {
        "origin": {"lat": float(pack.node_lat[o]), "lon": float(pack.node_lon[o])},
        "destination": {"lat": float(pack.node_lat[d]), "lon": float(pack.node_lon[d])},
        "departure_time": "2026-07-24T08:30:00",
        "safety_enabled": safety,
    }


def test_route_contract_shape(client):
    resp = client.post("/route", json=_route_body(client.pack, client.ids))
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"routes"}
    assert 1 <= len(data["routes"]) <= 3
    for r in data["routes"]:
        assert set(r.keys()) == {"kind", "geometry", "distance_m", "eta_s",
                                 "unsafe", "segments", "unsafe_points"}
        assert r["kind"] in ("fast", "balanced", "safe")
        assert r["geometry"]["type"] == "LineString"
        assert len(r["geometry"]["coordinates"]) >= 2
        assert set(r["unsafe"].keys()) == {"unprotected_left",
                                           "uncontrolled_crossing", "total"}
        for seg in r["segments"]:
            assert set(seg.keys()) == {"geometry", "tier"}
            assert seg["tier"] in ("safe", "caution", "unsafe")
        for pt in r["unsafe_points"]:
            assert set(pt.keys()) == {"lon", "lat", "type"}
            assert pt["type"] in ("unprotected_left", "uncontrolled_crossing")


def test_route_scenario_through_api(client):
    data = client.post("/route",
                       json=_route_body(client.pack, client.ids)).json()
    by_kind = {r["kind"]: r for r in data["routes"]}
    assert by_kind["fast"]["unsafe"]["unprotected_left"] == 1
    assert by_kind["safe"]["unsafe"]["total"] == 0
    assert by_kind["fast"]["eta_s"] < by_kind["safe"]["eta_s"]


def test_safety_toggle_off_single_route(client):
    data = client.post("/route",
                       json=_route_body(client.pack, client.ids, safety=False)).json()
    assert [r["kind"] for r in data["routes"]] == ["fast"]
    # counters are still REPORTED with the toggle off (documented semantics)
    assert data["routes"][0]["unsafe"]["unprotected_left"] == 1


def test_unsnappable_origin_is_422(client):
    body = _route_body(client.pack, client.ids)
    body["origin"] = {"lat": 10.0, "lon": 10.0}
    resp = client.post("/route", json=body)
    assert resp.status_code == 422


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
