"""API contract lock: exact JSON key sets on a toy pack served through the
real FastAPI app (SR_PACK_DIR override). A future mobile client relies on
this shape — if a key changes, this test must be updated CONSCIOUSLY."""

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


def _route_body(pack, ids, safety=True, **extra):
    # node coords double as origin/destination points (perfect snaps)
    o = ids["s"]
    d = ids["a0"]
    return {
        "origin": {"lat": float(pack.node_lat[o]), "lon": float(pack.node_lon[o])},
        "destination": {"lat": float(pack.node_lat[d]), "lon": float(pack.node_lon[d])},
        "departure_time": "2026-07-24T08:30:00",
        "safety_enabled": safety,
        **extra,
    }


def test_route_contract_shape(client):
    resp = client.post("/route", json=_route_body(client.pack, client.ids))
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"routes"}
    assert 1 <= len(data["routes"]) <= 3
    for r in data["routes"]:
        assert set(r.keys()) == {"kind", "geometry", "distance_m", "eta_s",
                                 "unsafe", "segments", "unsafe_points",
                                 "detour_pct"}
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
    # the fastest route in the response defines the baseline
    assert by_kind["fast"]["detour_pct"] == 0.0
    assert by_kind["safe"]["detour_pct"] > 0.0


def test_detour_budget_caps_how_far_the_safe_route_may_go(client):
    """A generous budget keeps the safer, slower route; a budget below what
    that route costs drops it, leaving only the fastest one."""
    body = _route_body(client.pack, client.ids, detour_budget_pct=1.0)
    generous = client.post("/route", json=body).json()["routes"]
    by_kind = {r["kind"]: r for r in generous}
    assert by_kind["safe"]["unsafe"]["total"] == 0
    assert by_kind["safe"]["detour_pct"] > 0.0      # it does cost something

    body = _route_body(client.pack, client.ids, detour_budget_pct=0.0)
    stingy = client.post("/route", json=body).json()["routes"]
    assert [r["kind"] for r in stingy] == ["fast"]
    assert stingy[0]["detour_pct"] == 0.0


def test_negative_detour_budget_is_422(client):
    body = _route_body(client.pack, client.ids, detour_budget_pct=-0.5)
    assert client.post("/route", json=body).status_code == 422


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


def test_meta_shape(client):
    """Additive endpoint the front-end uses for the GPS coverage check."""
    resp = client.get("/meta")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"region", "bbox", "num_edges"}
    assert data["region"] == "toy"
    # toy packs declare no bbox -> null disables the client coverage check
    assert data["bbox"] is None
    assert data["num_edges"] == client.pack.num_edges


def test_meta_bbox_present_for_real_region(tmp_path, monkeypatch):
    """A pack built from a configured region exposes its bbox as
    [west, south, east, north] so the client can range-check a GPS fix."""
    from pyref.config import Config
    from tests.helpers.toy_graphs import GraphBuilder

    cfg = Config.load()
    b = GraphBuilder(cfg)
    n0 = b.node(37.87, -122.27)
    n1 = b.node(37.871, -122.269)
    b.edge(n0, n1, length_m=200.0)
    # build under a real region preset so cfg.bbox() resolves
    b.build(region="berkeley_small").write(tmp_path / "bk")
    monkeypatch.setenv("SR_PACK_DIR", str(tmp_path / "bk"))

    from api.main import create_app
    with TestClient(create_app()) as c:
        data = c.get("/meta").json()
        assert data["region"] == "berkeley_small"
        assert data["bbox"] == list(cfg.bbox("berkeley_small"))
        west, south, east, north = data["bbox"]
        assert west < east and south < north
