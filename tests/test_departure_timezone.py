"""Departure time is resolved in the served pack's timezone (ADR-0014 decision
7, issue #63).

`sim.profiles.multipliers_at` reads only `departure.hour/.minute/.second`, so
whatever datetime reaches it IS the traffic hour. Before this, `/route`
defaulted a missing `departure_time` to a naive `datetime.now()` — the server's
clock, UTC in the container — and a Bay Area request was priced 7-8 hours off.

The contract pinned here:

- **naive** input is already pack-local wall-clock time and passes through
  unchanged (what the web client sends; today's behaviour);
- **aware** input is an instant: converted into the pack's zone, then made
  naive, so the profile reads the pack-local hour;
- **omitted** input is "now" in the pack's zone, made naive.

The cost model therefore still sees a naive local clock, and the artifact
echoes that resolved clock (`preference.departure_time` and, under the
synthetic model, `traffic_basis.as_of`): it is what priced the route.
"""
from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from api.departure import pack_timezone, resolve_departure
from pyref.config import DEFAULT_CONFIG_PATH, Config
from tests.helpers.fixtures import unprotected_left_city

UTC = datetime.UTC
LA = ZoneInfo("America/Los_Angeles")
# 00:30 UTC on 24 Sep 2026 is 17:30 PDT on 23 Sep: evening peak in Berkeley,
# the dead of night on a UTC server clock. The exact shape of #63.
SERVER_NOW = datetime.datetime(2026, 9, 24, 0, 30, tzinfo=UTC)
LA_LOCAL = datetime.datetime(2026, 9, 23, 17, 30)


# --- the pure resolver ----------------------------------------------------------

class TestResolveDeparture:
    def test_naive_passes_through_unchanged(self):
        naive = datetime.datetime(2026, 9, 23, 8, 15, 30)
        out = resolve_departure(naive, LA, now=lambda: SERVER_NOW)
        assert out == naive and out.tzinfo is None

    def test_aware_is_converted_into_the_pack_zone_then_made_naive(self):
        out = resolve_departure(SERVER_NOW, LA, now=lambda: SERVER_NOW)
        assert out == LA_LOCAL and out.tzinfo is None

    def test_aware_in_a_third_zone_is_converted_too(self):
        tokyo = SERVER_NOW.astimezone(ZoneInfo("Asia/Tokyo"))
        assert resolve_departure(tokyo, LA, now=lambda: SERVER_NOW) == LA_LOCAL

    def test_none_is_now_in_the_pack_zone(self):
        """#63 itself: a UTC server clock must yield the LA local hour."""
        out = resolve_departure(None, LA, now=lambda: SERVER_NOW)
        assert out == LA_LOCAL and out.tzinfo is None

    def test_none_honours_dst(self):
        winter = datetime.datetime(2026, 1, 15, 1, 30, tzinfo=UTC)
        out = resolve_departure(None, LA, now=lambda: winter)
        assert out == datetime.datetime(2026, 1, 14, 17, 30)  # PST, UTC-8

    def test_default_clock_is_real_now(self):
        before = datetime.datetime.now(LA).replace(tzinfo=None)
        out = resolve_departure(None, LA)
        after = datetime.datetime.now(LA).replace(tzinfo=None)
        assert before <= out <= after


# --- where the zone comes from --------------------------------------------------

def _cfg(presets: dict) -> Config:
    base = Config.load(DEFAULT_CONFIG_PATH)
    raw = {**base.raw, "region": {**base.raw["region"], "presets": presets}}
    return Config(raw=raw, source_path=base.source_path)


class TestPackTimezone:
    def test_every_shipped_preset_has_a_valid_timezone(self):
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        for name in cfg["region"]["presets"]:
            assert pack_timezone(cfg, name, allow_unconfigured=False) == LA

    def test_preset_without_timezone_is_fatal(self):
        cfg = _cfg({"metro": {"bbox": [0, 0, 1, 1]}})
        with pytest.raises(ValueError, match="metro.*timezone"):
            pack_timezone(cfg, "metro", allow_unconfigured=True)

    def test_preset_with_unknown_zone_is_fatal(self):
        cfg = _cfg({"metro": {"bbox": [0, 0, 1, 1], "timezone": "Mars/Olympus"}})
        with pytest.raises(ValueError, match="Mars/Olympus"):
            pack_timezone(cfg, "metro", allow_unconfigured=True)

    def test_unconfigured_pack_is_fatal_in_production(self):
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        for region in ("toy", None):
            with pytest.raises(ValueError, match="preset"):
                pack_timezone(cfg, region, allow_unconfigured=False)

    def test_unconfigured_pack_under_sr_pack_dir_serves_utc(self):
        """The toy-pack rule: a pack named by SR_PACK_DIR whose region is not a
        config preset (the API tests' `region: "toy"`) has no zone to look up,
        and UTC is the one zone that means "no local time"."""
        cfg = Config.load(DEFAULT_CONFIG_PATH)
        for region in ("toy", None):
            assert pack_timezone(cfg, region, allow_unconfigured=True) == ZoneInfo("UTC")


# --- through the API ------------------------------------------------------------

@pytest.fixture()
def toy_env(tmp_path, monkeypatch):
    pack, ids = unprotected_left_city()
    monkeypatch.setenv("SR_PACK_DIR", str(tmp_path / "toy"))
    return tmp_path, pack, ids


def _client(toy_env, region: str | None = None):
    tmp_path, pack, ids = toy_env
    if region is not None:
        pack.meta["region"] = region
    pack.write(tmp_path / "toy")
    from api.main import create_app
    return TestClient(create_app()), pack, ids


def _body(pack, ids, departure):
    o, d = ids["s"], ids["a0"]
    body = {
        "origin": {"lat": float(pack.node_lat[o]), "lon": float(pack.node_lon[o])},
        "destination": {"lat": float(pack.node_lat[d]), "lon": float(pack.node_lon[d])},
        "safety_enabled": True,
    }
    if departure is not None:
        body["departure_time"] = departure
    return body


def _fast(client, body):
    resp = client.post("/route", json=body)
    assert resp.status_code == 200, resp.text
    return next(r for r in resp.json()["routes"] if r["kind"] == "fast")


def test_toy_pack_serves_utc(toy_env):
    client, _, _ = _client(toy_env)
    with client:
        assert client.app.state.app_state.registry.only().tz == ZoneInfo("UTC")


def test_preset_pack_serves_its_config_zone(toy_env):
    client, _, _ = _client(toy_env, region="berkeley_small")
    with client:
        assert client.app.state.app_state.registry.only().tz == LA


def test_served_preset_without_timezone_fails_startup(toy_env, tmp_path, monkeypatch):
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    stripped = "\n".join(line for line in text.splitlines()
                         if not line.startswith("timezone"))
    assert stripped != text
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(stripped, encoding="utf-8")
    monkeypatch.setenv("SR_CONFIG", str(cfg_path))
    client, _, _ = _client(toy_env, region="berkeley_small")
    with pytest.raises(ValueError, match="berkeley_small.*timezone"), client:
        pass


def test_issue_63_omitted_departure_is_priced_at_the_pack_local_hour(
        toy_env, monkeypatch):
    """Server clock at 00:30 UTC; the pack is in LA. The route must be priced
    (and labelled) at 17:30 local — identical to an explicit naive 17:30 — and
    NOT at 00:30, which is what the old naive `datetime.now()` fed the
    profile on a UTC container."""
    import api.departure
    monkeypatch.setattr(api.departure, "_utc_now", lambda: SERVER_NOW)
    client, pack, ids = _client(toy_env, region="berkeley_small")
    with client:
        omitted = _fast(client, _body(pack, ids, None))
        local = _fast(client, _body(pack, ids, "2026-09-23T17:30:00"))
        utc_wall = _fast(client, _body(pack, ids, "2026-09-24T00:30:00"))
    # the test is only meaningful if the profile tells the two hours apart
    assert local["eta_s"] != utc_wall["eta_s"]
    assert omitted["eta_s"] == local["eta_s"]
    # the artifact echoes the resolved local clock that priced it
    assert omitted["preference"]["departure_time"] == "2026-09-23T17:30:00"
    assert omitted["preference"]["traffic_basis"]["as_of"] == "2026-09-23T17:30:00"


def test_aware_departure_is_converted_and_echoed_naive_local(toy_env):
    client, pack, ids = _client(toy_env, region="berkeley_small")
    with client:
        aware = _fast(client, _body(pack, ids, "2026-09-24T00:30:00Z"))
        local = _fast(client, _body(pack, ids, "2026-09-23T17:30:00"))
    assert aware["eta_s"] == local["eta_s"]
    assert aware["preference"]["departure_time"] == "2026-09-23T17:30:00"
    assert aware["preference"]["traffic_basis"]["as_of"] == "2026-09-23T17:30:00"


def test_naive_departure_is_echoed_unchanged(toy_env):
    client, pack, ids = _client(toy_env, region="berkeley_small")
    with client:
        r = _fast(client, _body(pack, ids, "2026-09-23T08:15:00"))
    assert r["preference"]["departure_time"] == "2026-09-23T08:15:00"


def test_reroute_converts_an_aware_carried_departure(toy_env):
    client, pack, ids = _client(toy_env, region="berkeley_small")
    with client:
        art = _fast(client, _body(pack, ids, "2026-09-23T17:30:00"))
        pref = {**art["preference"], "departure_time": "2026-09-24T00:30:00+00:00"}
        o, d = ids["s"], ids["a0"]
        resp = client.post("/reroute", json={
            "origin": {"lat": float(pack.node_lat[o]), "lon": float(pack.node_lon[o])},
            "destination": {"lat": float(pack.node_lat[d]),
                            "lon": float(pack.node_lon[d])},
            "preference": pref,
        })
    assert resp.status_code == 200, resp.text
    route = resp.json()["route"]
    assert route["preference"]["departure_time"] == "2026-09-23T17:30:00"
    assert route["eta_s"] == art["eta_s"]
