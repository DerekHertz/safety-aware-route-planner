"""ADR-0014 step 3: `/route` and `/reroute` pick the pack by coordinates.

A request goes to the served pack whose bbox contains **both** endpoints
(decision 2). Otherwise it is a 422 with the existing `{"detail": str}` shape,
refused **before any search runs**, and the route-quota token is still spent:
`enforce_route_quota` runs first, as a dependency, so a refusal is not a free
probe of coverage.

The two-pack cases run on the toy metros of `tests/helpers/multi_pack.py`.
Each served `Router` gets a spy on `route` and `reroute`, so "routed on A, not
B" and "no search on a 422" are observed calls, not inferences from a body.
The single-pack, null-bbox toy (`SR_PACK_DIR`) must keep today's behaviour: a
far-away point is a snap failure, not "outside every served region".

The last test is the real-pack one: a Berkeley O/D through the registry is
byte-identical to routing directly on a `Router` over the same pack.
"""
from __future__ import annotations

import datetime

import pytest
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from api.routes import _artifact
from api.schemas import RouteResponse
from pyref.config import Config
from pyref.engine import Router
from pyref.graph import GraphPack
from tests.helpers.fixtures import unprotected_left_city
from tests.helpers.multi_pack import A_BBOX, B_BBOX, TWO, build_toy, toy_endpoints, write_config
from tests.helpers.packs import real_pack, skip_reason

DEPARTURE = "2026-07-24T08:30:00"
NOWHERE = (0.5, 0.5)          # inside neither toy metro, nor any real preset

A0, A1 = toy_endpoints(A_BBOX)
B0, B1 = toy_endpoints(B_BBOX)


def _pt(p: tuple[float, float]) -> dict:
    return {"lat": p[0], "lon": p[1]}


def _route_body(o, d) -> dict:
    return {"origin": _pt(o), "destination": _pt(d),
            "departure_time": DEPARTURE, "safety_enabled": True}


def _reroute_body(o, d) -> dict:
    return {"origin": _pt(o), "destination": _pt(d),
            "preference": {"level": "fast", "lambda": 0.0,
                           "detour_budget_pct": 0.25,
                           "departure_time": DEPARTURE}}


class Spend:
    """A route limiter that always admits and records every token taken."""

    def __init__(self):
        self.keys: list[str] = []

    async def acquire(self, key):
        self.keys.append(key)
        return None

    async def aclose(self):
        pass


def _spy(router: Router, calls: list[str], name: str) -> None:
    """Count `route`/`reroute` on this Router instance, still running the real
    search, so a spied call is observably the one the handler made."""
    for method in ("route", "reroute"):
        real = getattr(router, method)

        def counting(*a, _real=real, _method=method, **kw):
            calls.append(f"{name}.{_method}")
            return _real(*a, **kw)
        setattr(router, method, counting)


@pytest.fixture()
def two_metros(tmp_path, monkeypatch):
    """A deployment serving metro_a and metro_b, every search spied on and
    every quota token recorded."""
    monkeypatch.delenv("SR_PACK_DIR", raising=False)
    monkeypatch.delenv("SR_REGIONS", raising=False)
    import api.main
    monkeypatch.setattr(api.main, "ensure_packs", lambda *a, **kw: [])
    cfg_path = write_config(tmp_path, presets=TWO, regions=["metro_a", "metro_b"])
    monkeypatch.setenv("SR_CONFIG", str(cfg_path))
    cfg = Config.load(cfg_path)
    for name in ("metro_a", "metro_b"):
        build_toy(cfg, name, tmp_path / "packs")
    from api.main import create_app
    with TestClient(create_app()) as client:
        state = client.app.state.app_state
        calls: list[str] = []
        for name in state.registry.names():
            _spy(state.registry[name].router, calls, name)
        spend = Spend()
        state.route_limiter = spend
        client.calls, client.spend = calls, spend
        yield client


# --- both endpoints in one pack -----------------------------------------------------

def test_route_with_both_endpoints_in_a_routes_on_a_only(two_metros):
    c = two_metros
    resp = c.post("/route", json=_route_body(A0, A1))
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == {"routes"}
    assert c.calls == ["metro_a.route"]
    assert len(c.spend.keys) == 1


def test_route_with_both_endpoints_in_b_routes_on_b_only(two_metros):
    """Not just "the first pack": the second of two is reachable too."""
    c = two_metros
    assert c.post("/route", json=_route_body(B0, B1)).status_code == 200
    assert c.calls == ["metro_b.route"]


def test_reroute_with_both_endpoints_in_a_reroutes_on_a_only(two_metros):
    c = two_metros
    resp = c.post("/reroute", json=_reroute_body(A0, A1))
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == {"route"}
    assert c.calls == ["metro_a.reroute"]
    assert len(c.spend.keys) == 1


def test_route_departure_is_resolved_in_the_selected_packs_zone(two_metros):
    """An aware departure is converted into the zone of the pack the request
    landed on — metro_b is New York — not the first served pack's."""
    c = two_metros
    body = _route_body(B0, B1)
    body["departure_time"] = "2026-07-24T12:30:00+00:00"     # 08:30 EDT
    art = c.post("/route", json=body).json()["routes"][0]
    assert art["preference"]["departure_time"].startswith("2026-07-24T08:30:00")


# --- the 422 contract ----------------------------------------------------------------

REFUSALS = [
    pytest.param(A0, B1, "origin and destination are in different regions "
                 "(metro_a, metro_b); routing across regions is not supported",
                 id="different-packs"),
    pytest.param(B0, A1, "origin and destination are in different regions "
                 "(metro_b, metro_a); routing across regions is not supported",
                 id="different-packs-reversed"),
    pytest.param(NOWHERE, A1, "origin is outside every served region",
                 id="origin-outside"),
    pytest.param(A0, NOWHERE, "destination is outside every served region",
                 id="destination-outside"),
    pytest.param(NOWHERE, NOWHERE, "origin is outside every served region",
                 id="both-outside"),
]


@pytest.mark.parametrize(("o", "d", "detail"), REFUSALS)
def test_route_refusal_is_422_before_any_search_and_spends_the_token(two_metros, o, d, detail):
    c = two_metros
    resp = c.post("/route", json=_route_body(o, d))
    assert resp.status_code == 422
    assert resp.json() == {"detail": detail}
    assert c.calls == []
    assert len(c.spend.keys) == 1


@pytest.mark.parametrize(("o", "d", "detail"), REFUSALS)
def test_reroute_refusal_is_422_before_any_search_and_spends_the_token(two_metros, o, d, detail):
    c = two_metros
    resp = c.post("/reroute", json=_reroute_body(o, d))
    assert resp.status_code == 422
    assert resp.json() == {"detail": detail}
    assert c.calls == []
    assert len(c.spend.keys) == 1


def test_a_refusal_is_still_a_refusal_when_over_quota(two_metros):
    """The quota runs first: an exhausted caller gets 429, not a free answer
    about coverage."""
    c = two_metros

    class Refuse:
        async def acquire(self, key):
            return 7.0

        async def aclose(self):
            pass
    c.app.state.app_state.route_limiter = Refuse()
    resp = c.post("/route", json=_route_body(A0, B1))
    assert resp.status_code == 429
    assert c.calls == []


# --- a single null-bbox pack is unchanged ---------------------------------------------

@pytest.fixture()
def pinned_toy(tmp_path, monkeypatch):
    pack, _ = unprotected_left_city()
    pack.write(tmp_path / "toy")
    monkeypatch.setenv("SR_PACK_DIR", str(tmp_path / "toy"))
    from api.main import create_app
    with TestClient(create_app()) as client:
        assert client.app.state.app_state.registry.only().bbox is None
        client.pack = pack
        yield client


@pytest.mark.parametrize("path, body", [("/route", _route_body), ("/reroute", _reroute_body)])
def test_single_null_bbox_pack_far_point_is_still_a_snap_failure(pinned_toy, path, body):
    pack = pinned_toy.pack
    near = (float(pack.node_lat[0]), float(pack.node_lon[0]))
    resp = pinned_toy.post(path, json=body(NOWHERE, near))
    assert resp.status_code == 422
    assert resp.json() == {"detail": "origin is too far from any drivable road"}


# --- real pack: the registry changes nothing about the artifact ------------------------

# Downtown Berkeley to Temescal: both well inside the berkeley_oakland bbox.
BERKELEY_O = (37.8703, -122.2680)
BERKELEY_D = (37.8330, -122.2620)


def test_real_pack_through_the_registry_is_byte_identical_to_a_direct_router(
        tmp_path, monkeypatch):
    pack_dir = real_pack("berkeley_oakland")
    if pack_dir is None:
        pytest.skip(skip_reason("berkeley_oakland"))
    monkeypatch.delenv("SR_PACK_DIR", raising=False)
    monkeypatch.delenv("SR_REGIONS", raising=False)
    import api.main
    monkeypatch.setattr(api.main, "ensure_packs", lambda *a, **kw: [])
    cfg_path = write_config(tmp_path, presets={}, regions=["berkeley_oakland"],
                            pack_root=pack_dir.parent)
    monkeypatch.setenv("SR_CONFIG", str(cfg_path))

    body = _route_body(BERKELEY_O, BERKELEY_D)      # naive: no zone conversion
    from api.main import create_app
    with TestClient(create_app()) as client:
        assert client.app.state.app_state.registry.names() == ["berkeley_oakland"]
        resp = client.post("/route", json=body)
    assert resp.status_code == 200, resp.text

    cfg = Config.load(cfg_path)
    direct = Router(GraphPack.load(pack_dir), cfg).route(
        *BERKELEY_O, *BERKELEY_D,
        departure=datetime.datetime(2026, 7, 24, 8, 30),
        safety_enabled=True, detour_budget_pct=None)
    model = RouteResponse.model_validate({"routes": [_artifact(r) for r in direct]})
    expected = JSONResponse(jsonable_encoder(model)).body
    assert len(resp.json()["routes"]) >= 1
    assert resp.content == expected
