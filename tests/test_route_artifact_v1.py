"""Route artifact v1 (ADR-0004): every route artifact carries a `preference`
— the reproducible description of what it was optimized for — plus an explicit
`schema_version`. These are items 1 & 2 of the robust-core milestone (ADR-0008)
and the boundary a nav consumer reroutes from without a silent safety-level
swap (ADR-0002).
"""
import datetime

import pytest
from fastapi.testclient import TestClient

from pyref.config import Config
from pyref.engine import Router
from tests.helpers.fixtures import line3, unprotected_left_city

CFG = Config.load()
PREF_KEYS = {"level", "lambda", "detour_budget_pct", "departure_time"}


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


def _body(pack, ids, **extra):
    o, d = ids["s"], ids["a0"]
    return {
        "origin": {"lat": float(pack.node_lat[o]), "lon": float(pack.node_lon[o])},
        "destination": {"lat": float(pack.node_lat[d]), "lon": float(pack.node_lon[d])},
        "departure_time": "2026-07-24T08:30:00",
        "safety_enabled": True,
        **extra,
    }


def test_every_artifact_carries_preference_and_version(client):
    routes = client.post("/route", json=_body(client.pack, client.ids)).json()["routes"]
    assert len(routes) >= 2
    for r in routes:
        assert r["schema_version"] == 1
        assert set(r["preference"].keys()) == PREF_KEYS
        # the label duplicates `kind` ON PURPOSE (ADR-0004): the artifact must
        # stay self-describing when pulled out of the response array.
        assert r["preference"]["level"] == r["kind"]
        assert r["preference"]["level"] in ("fast", "balanced", "safe")


def test_preference_lambda_matches_the_sweep(client):
    by_kind = {r["kind"]: r for r in
               client.post("/route", json=_body(client.pack, client.ids)).json()["routes"]}
    # the fast route is pure time: lambda 0.
    assert by_kind["fast"]["preference"]["lambda"] == pytest.approx(
        float(CFG["alternatives"]["lambda_fast"]))
    assert by_kind["fast"]["preference"]["lambda"] == 0.0
    # the safe route carries the exact lambda that produced it — meaningless to
    # a UI, but what a nav consumer needs to reproduce the route (ADR-0004).
    assert by_kind["safe"]["preference"]["lambda"] == pytest.approx(
        float(CFG["alternatives"]["lambda_safe"]))


def test_preference_echoes_the_resolved_detour_budget(client):
    # an explicit budget is carried verbatim...
    routes = client.post("/route",
                         json=_body(client.pack, client.ids, detour_budget_pct=1.0)
                         ).json()["routes"]
    assert all(r["preference"]["detour_budget_pct"] == 1.0 for r in routes)
    # ...and an omitted budget carries the RESOLVED config default, never null:
    # a nav consumer must not have to re-derive what the server chose.
    default = float(CFG["alternatives"]["detour_budget_pct"])
    routes = client.post("/route", json=_body(client.pack, client.ids)).json()["routes"]
    assert all(r["preference"]["detour_budget_pct"] == pytest.approx(default)
               for r in routes)


def test_preference_carries_the_departure_basis(client):
    routes = client.post("/route", json=_body(client.pack, client.ids)).json()["routes"]
    # echoes the departure the route was actually planned for (ADR-0004).
    assert all(r["preference"]["departure_time"].startswith("2026-07-24T08:30:00")
               for r in routes)


def test_preference_reproduces_the_same_route(client):
    """The reason preference exists (ADR-0002): re-planning with a route's own
    carried params reproduces that route at that same safety level."""
    first = {r["kind"]: r for r in
             client.post("/route", json=_body(client.pack, client.ids)).json()["routes"]}
    pref = first["safe"]["preference"]
    again = {r["kind"]: r for r in client.post("/route", json=_body(
        client.pack, client.ids,
        departure_time=pref["departure_time"],
        detour_budget_pct=pref["detour_budget_pct"],
    )).json()["routes"]}
    assert again["safe"]["geometry"] == first["safe"]["geometry"]
    assert again["safe"]["preference"] == pref


def test_safety_off_single_fast_artifact_still_has_preference(client):
    routes = client.post("/route",
                         json=_body(client.pack, client.ids, safety_enabled=False)
                         ).json()["routes"]
    assert [r["kind"] for r in routes] == ["fast"]
    assert routes[0]["preference"]["level"] == "fast"
    assert routes[0]["preference"]["lambda"] == 0.0
    assert routes[0]["schema_version"] == 1


def test_same_edge_shortcircuit_artifact_carries_preference():
    """The origin/destination-on-one-edge fast path in Router.route bypasses
    compute_alternatives entirely — it must still emit a complete v1 artifact."""
    pack, a, b_mid, _c = line3()
    router = Router(pack, CFG)
    lat_a, lon_a = float(pack.node_lat[a]), float(pack.node_lon[a])
    lat_b, lon_b = float(pack.node_lat[b_mid]), float(pack.node_lon[b_mid])
    # both points fall on edge A->B, destination downstream of origin.
    o_lat, o_lon = lat_a + 0.1 * (lat_b - lat_a), lon_a + 0.1 * (lon_b - lon_a)
    d_lat, d_lon = lat_a + 0.6 * (lat_b - lat_a), lon_a + 0.6 * (lon_b - lon_a)
    out = router.route(o_lat, o_lon, d_lat, d_lon,
                       departure=datetime.datetime(2026, 7, 24, 8, 30),
                       detour_budget_pct=0.5)
    assert len(out) == 1 and out[0].kind == "fast"
    assert out[0].schema_version == 1
    assert out[0].preference["level"] == "fast"
    assert out[0].preference["lambda"] == 0.0
    assert out[0].preference["detour_budget_pct"] == 0.5
