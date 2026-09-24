"""ADR-0014 step 5: `/geocode?region=` bounds Nominatim per served pack.

Decision 5, as pinned here:

* `region` naming a served pack -> Nominatim bounded to **that pack's** bbox
  (`viewbox` + `bounded=1`), today's behaviour per pack. A pack with a null
  bbox (the sole toy pack) sends no viewbox, as today.
* `region` absent, one pack served -> unchanged: bounded to that pack, and it
  shares its cache entry with `region=<that pack>` (same upstream query).
* `region` absent, several packs served -> **unbounded** upstream, results
  **post-filtered** to points inside any served bbox. Still one upstream
  request (no per-pack fan-out) against the one global token bucket.
* Unknown `region` -> 422 `{"detail": str}`, validated before the cache, the
  bucket and the upstream: a typo costs Nominatim nothing and spends no token.
* The per-process cache keys on `(q, region)`.

The upstream is `httpx.AsyncClient`, replaced by a fake that records the params
it was sent; multi-pack deployments reuse `tests/test_multi_pack_load.py`'s two
disjoint toy metros.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.helpers.fixtures import unprotected_left_city
from tests.test_multi_pack_load import A_BBOX, B_BBOX, TWO, _deploy
from tests.test_multi_pack_load import env as env  # noqa: F401  (fixture re-export)

IN_A = {"display_name": "In A", "lat": "10.005", "lon": "10.01"}
IN_B = {"display_name": "In B", "lat": "20.005", "lon": "20.01"}
NOWHERE = {"display_name": "Nowhere", "lat": "0.0", "lon": "0.0"}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeAsyncClient:
    calls: list[dict] = []
    payload: list[dict] = [IN_A]

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        FakeAsyncClient.calls.append({"url": url, "params": dict(params or {})})
        return FakeResponse(FakeAsyncClient.payload)


class CountingLimiter:
    """Always admits, and counts, so 'spent no token' is observable."""

    def __init__(self):
        self.acquired = 0

    async def acquire(self):
        self.acquired += 1
        return None

    async def aclose(self):
        pass


@pytest.fixture(autouse=True)
def fake_upstream(monkeypatch):
    monkeypatch.delenv("SR_REDIS_URL", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.calls = []
    FakeAsyncClient.payload = [IN_A]
    from api import geocode as geocode_module
    geocode_module._cache.clear()
    yield
    geocode_module._cache.clear()


def _viewbox(bbox):
    west, south, east, north = bbox
    return f"{west},{north},{east},{south}"


def _limiter(client) -> CountingLimiter:
    lim = CountingLimiter()
    client.app.state.app_state.limiter = lim
    return lim


@pytest.fixture()
def two_packs(env):  # noqa: F811
    client = _deploy(env, presets=TWO, build=["metro_a", "metro_b"],
                     regions=["metro_a", "metro_b"])
    with client:
        yield client


@pytest.fixture()
def one_pack(env):  # noqa: F811
    client = _deploy(env, presets=TWO, build=["metro_a"], regions=["metro_a"])
    with client:
        yield client


@pytest.fixture()
def toy(tmp_path, monkeypatch):
    """The sole null-bbox toy pack the rest of the API suite serves."""
    pack, _ids = unprotected_left_city()
    pack.write(tmp_path / "toy")
    monkeypatch.setenv("SR_PACK_DIR", str(tmp_path / "toy"))
    from api.main import create_app
    with TestClient(create_app()) as c:
        yield c


# --- region names a pack --------------------------------------------------------

class TestNamedRegion:
    @pytest.mark.parametrize(("region", "bbox"), [("metro_a", A_BBOX),
                                                  ("metro_b", B_BBOX)])
    def test_bounds_to_the_named_packs_bbox(self, two_packs, region, bbox):
        FakeAsyncClient.payload = [IN_A] if region == "metro_a" else [IN_B]
        resp = two_packs.get("/geocode", params={"q": "main st", "region": region})
        assert resp.status_code == 200
        params = FakeAsyncClient.calls[-1]["params"]
        assert params["viewbox"] == _viewbox(bbox)
        assert params["bounded"] == 1

    def test_named_region_results_are_passed_through(self, two_packs):
        """Bounded means Nominatim already did the filtering; the named-region
        path is today's behaviour and adds no post-filter of its own."""
        FakeAsyncClient.payload = [IN_A]
        body = two_packs.get("/geocode",
                             params={"q": "main st", "region": "metro_a"}).json()
        assert [r["name"] for r in body["results"]] == ["In A"]


# --- region absent, several packs -----------------------------------------------

class TestMultiPackUnbounded:
    def test_sends_no_viewbox_and_no_bounded(self, two_packs):
        two_packs.get("/geocode", params={"q": "main st"})
        params = FakeAsyncClient.calls[-1]["params"]
        assert "viewbox" not in params
        assert "bounded" not in params

    def test_drops_results_outside_every_served_bbox(self, two_packs):
        FakeAsyncClient.payload = [NOWHERE, IN_B, NOWHERE, IN_A]
        body = two_packs.get("/geocode", params={"q": "main st"}).json()
        assert [r["name"] for r in body["results"]] == ["In B", "In A"]

    def test_one_upstream_request_and_one_token(self, two_packs):
        """One global bucket, no fan-out per pack (ADR-0014 decision 5)."""
        lim = _limiter(two_packs)
        two_packs.get("/geocode", params={"q": "main st"})
        assert len(FakeAsyncClient.calls) == 1
        assert lim.acquired == 1

    def test_asks_upstream_for_more_than_it_returns(self, two_packs):
        """An unbounded top-5 is usually all outside coverage, so the one
        request asks for more candidates, and the answer is still capped at 5."""
        FakeAsyncClient.payload = [IN_A] * 12
        body = two_packs.get("/geocode", params={"q": "main st"}).json()
        assert int(FakeAsyncClient.calls[-1]["params"]["limit"]) > 5
        assert len(body["results"]) == 5


# --- unknown region -------------------------------------------------------------

class TestUnknownRegion:
    def test_is_422_with_a_string_detail(self, two_packs):
        resp = two_packs.get("/geocode", params={"q": "main st", "region": "atlantis"})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert isinstance(detail, str)
        assert "atlantis" in detail

    def test_spends_no_token_and_never_goes_upstream(self, two_packs):
        lim = _limiter(two_packs)
        two_packs.get("/geocode", params={"q": "main st", "region": "atlantis"})
        assert lim.acquired == 0
        assert FakeAsyncClient.calls == []

    def test_is_422_even_when_the_query_is_cached(self, two_packs):
        two_packs.get("/geocode", params={"q": "main st"})
        resp = two_packs.get("/geocode", params={"q": "main st", "region": "atlantis"})
        assert resp.status_code == 422

    def test_is_422_on_a_single_pack_deployment_too(self, one_pack):
        resp = one_pack.get("/geocode", params={"q": "main st", "region": "metro_b"})
        assert resp.status_code == 422


# --- cache ----------------------------------------------------------------------

class TestCacheKeysOnRegion:
    def test_unbounded_and_bounded_results_do_not_collide(self, two_packs):
        _limiter(two_packs)
        FakeAsyncClient.payload = [IN_A, IN_B]
        unbounded = two_packs.get("/geocode", params={"q": "main st"}).json()
        FakeAsyncClient.payload = [IN_B]
        in_b = two_packs.get("/geocode",
                             params={"q": "main st", "region": "metro_b"}).json()
        assert len(FakeAsyncClient.calls) == 2
        assert [r["name"] for r in unbounded["results"]] == ["In A", "In B"]
        assert [r["name"] for r in in_b["results"]] == ["In B"]

    def test_two_regions_do_not_collide(self, two_packs):
        _limiter(two_packs)
        FakeAsyncClient.payload = [IN_A]
        two_packs.get("/geocode", params={"q": "main st", "region": "metro_a"})
        FakeAsyncClient.payload = [IN_B]
        body = two_packs.get("/geocode",
                             params={"q": "main st", "region": "metro_b"}).json()
        assert len(FakeAsyncClient.calls) == 2
        assert [r["name"] for r in body["results"]] == ["In B"]

    def test_same_q_and_region_is_a_hit(self, two_packs):
        lim = _limiter(two_packs)
        for _ in range(3):
            two_packs.get("/geocode", params={"q": "Main St ", "region": "metro_a"})
        assert len(FakeAsyncClient.calls) == 1
        assert lim.acquired == 1

    def test_single_pack_absent_region_shares_the_named_entry(self, one_pack):
        """With one pack served, 'no region' and 'region=<that pack>' are the
        same upstream query, so they are one cache entry."""
        one_pack.get("/geocode", params={"q": "main st"})
        one_pack.get("/geocode", params={"q": "main st", "region": "metro_a"})
        assert len(FakeAsyncClient.calls) == 1


# --- single pack, unchanged -----------------------------------------------------

class TestSinglePackUnchanged:
    def test_absent_region_bounds_to_the_sole_pack(self, one_pack):
        one_pack.get("/geocode", params={"q": "main st"})
        params = FakeAsyncClient.calls[-1]["params"]
        assert params["viewbox"] == _viewbox(A_BBOX)
        assert params["bounded"] == 1
        assert params["limit"] == 5

    def test_null_bbox_toy_sends_no_viewbox_and_filters_nothing(self, toy):
        FakeAsyncClient.payload = [NOWHERE, IN_A]
        body = toy.get("/geocode", params={"q": "main st"}).json()
        params = FakeAsyncClient.calls[-1]["params"]
        assert "viewbox" not in params and "bounded" not in params
        assert [r["name"] for r in body["results"]] == ["Nowhere", "In A"]

    def test_null_bbox_toy_named_by_region_behaves_the_same(self, toy):
        name = toy.app.state.app_state.registry.names()[0]
        resp = toy.get("/geocode", params={"q": "main st", "region": name})
        assert resp.status_code == 200
        params = FakeAsyncClient.calls[-1]["params"]
        assert "viewbox" not in params and "bounded" not in params
