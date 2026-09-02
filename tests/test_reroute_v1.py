"""Reroute v1 (ADR-0008 "Reroute v1 semantics"): a nav consumer re-invokes the
route service mid-trip with the artifact's carried `preference`, and the service
replans from the current position to the original destination recomputing ONLY
that one safety level — never the full fast/balanced/safe set, never a silent
swap to a different level (the failure ADR-0002's carried-preference rule and
ADR-0008 exist to prevent).

Feature 2 of the robust-core milestone, building on route-artifact v1 (ADR-0004).
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


def _route_body(pack, ids, **extra):
    o, d = ids["s"], ids["a0"]
    return {
        "origin": {"lat": float(pack.node_lat[o]), "lon": float(pack.node_lon[o])},
        "destination": {"lat": float(pack.node_lat[d]), "lon": float(pack.node_lon[d])},
        "departure_time": "2026-07-24T08:30:00",
        "safety_enabled": True,
        **extra,
    }


def _reroute_body(pack, ids, preference, **extra):
    """Reroute from the origin node to the original destination, carrying the
    preference verbatim off a prior /route artifact."""
    o, d = ids["s"], ids["a0"]
    return {
        "origin": {"lat": float(pack.node_lat[o]), "lon": float(pack.node_lon[o])},
        "destination": {"lat": float(pack.node_lat[d]), "lon": float(pack.node_lon[d])},
        "preference": preference,
        **extra,
    }


def _routes(client, **extra):
    return {r["kind"]: r for r in
            client.post("/route", json=_route_body(client.pack, client.ids, **extra))
            .json()["routes"]}


def test_reroute_returns_single_artifact_at_carried_level(client):
    pref = _routes(client)["safe"]["preference"]
    body = _reroute_body(client.pack, client.ids, pref)
    resp = client.post("/reroute", json=body)
    assert resp.status_code == 200
    payload = resp.json()
    # a reroute yields ONE artifact, not the fast/balanced/safe list.
    assert set(payload.keys()) == {"route"}
    art = payload["route"]
    assert art["kind"] == "safe"
    assert art["schema_version"] == 1
    assert set(art["preference"].keys()) == PREF_KEYS
    assert art["preference"]["level"] == "safe"


def test_reroute_preserves_the_safety_level_no_silent_swap(client):
    """The whole point (ADR-0008): rerouting with the SAFE preference must stay
    safe — take the protected detour, zero counted unsafe maneuvers — even
    though a plain /route would hand back the faster unprotected-left route
    first in the list."""
    routes = _routes(client)
    assert routes["fast"]["unsafe"]["total"] >= 1     # fast takes the unprotected left
    assert routes["safe"]["unsafe"]["total"] == 0     # safe detours to the signal

    safe_art = client.post(
        "/reroute", json=_reroute_body(client.pack, client.ids, routes["safe"]["preference"]),
    ).json()["route"]
    assert safe_art["kind"] == "safe"
    assert safe_art["unsafe"]["total"] == 0
    # faithful single-level reproduction: same geometry the sweep produced.
    assert safe_art["geometry"] == routes["safe"]["geometry"]


def test_reroute_with_fast_preference_stays_fast(client):
    fast_pref = _routes(client)["fast"]["preference"]
    fast_art = client.post(
        "/reroute", json=_reroute_body(client.pack, client.ids, fast_pref),
    ).json()["route"]
    assert fast_art["kind"] == "fast"
    assert fast_art["preference"]["lambda"] == 0.0
    assert fast_art["geometry"] == _routes(client)["fast"]["geometry"]


def test_reroute_echoes_the_carried_preference(client):
    pref = _routes(client, detour_budget_pct=1.0)["safe"]["preference"]
    art = client.post(
        "/reroute", json=_reroute_body(client.pack, client.ids, pref),
    ).json()["route"]
    # lambda, budget and the departure basis all come back verbatim (ADR-0004):
    # a reroute reproduces the same level, not a fresh server-default plan.
    assert art["preference"]["lambda"] == pytest.approx(pref["lambda"])
    assert art["preference"]["detour_budget_pct"] == 1.0
    assert art["preference"]["departure_time"].startswith("2026-07-24T08:30:00")


def test_reroute_bad_snap_is_422(client):
    pref = _routes(client)["fast"]["preference"]
    body = _reroute_body(client.pack, client.ids, pref)
    body["origin"] = {"lat": 45.0, "lon": 45.0}     # nowhere near the toy pack
    assert client.post("/reroute", json=body).status_code == 422


def test_reroute_same_edge_shortcircuit_carries_preference():
    """Origin and destination on one directed edge: the engine's short-circuit
    still returns a complete single artifact at the carried level."""
    pack, a, b_mid, _c = line3()
    router = Router(pack, CFG)
    lat_a, lon_a = float(pack.node_lat[a]), float(pack.node_lon[a])
    lat_b, lon_b = float(pack.node_lat[b_mid]), float(pack.node_lon[b_mid])
    o_lat, o_lon = lat_a + 0.1 * (lat_b - lat_a), lon_a + 0.1 * (lon_b - lon_a)
    d_lat, d_lon = lat_a + 0.6 * (lat_b - lat_a), lon_a + 0.6 * (lon_b - lon_a)
    art = router.reroute(o_lat, o_lon, d_lat, d_lon, level="fast", lam=0.0,
                         detour_budget_pct=0.5,
                         departure=datetime.datetime(2026, 7, 24, 8, 30))
    assert art.kind == "fast"
    assert art.schema_version == 1
    assert art.preference["level"] == "fast"
    assert art.preference["detour_budget_pct"] == 0.5


def test_reroute_engine_returns_one_route(client):
    """Engine contract: Router.reroute returns a single RouteOut, not a list."""
    pack, ids = client.pack, client.ids
    router = Router(pack, CFG)
    art = router.reroute(
        float(pack.node_lat[ids["s"]]), float(pack.node_lon[ids["s"]]),
        float(pack.node_lat[ids["a0"]]), float(pack.node_lon[ids["a0"]]),
        level="safe", lam=float(CFG["alternatives"]["lambda_safe"]),
        detour_budget_pct=float(CFG["alternatives"]["detour_budget_pct"]),
        departure=datetime.datetime(2026, 7, 24, 8, 30))
    assert art.kind == "safe"
    assert art.unsafe["total"] == 0
