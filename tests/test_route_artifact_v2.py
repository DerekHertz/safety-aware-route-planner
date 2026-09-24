"""Route artifact v2 (ADR-0004, amended): the `preference` carries a
`traffic_basis` — source identifier, snapshot timestamp, profile version —
recording what traffic inputs the route was computed against, and
`schema_version` is 2.

Why it exists (ADR-0010, ADR-0011): an artifact is HALF PERISHABLE. Its
`eta_s` and segment timings go stale while its unsafe counts and tiers stay
reproducible, so a consumer diffing two artifacts must be able to tell
"traffic changed" from "these were computed against different data". The
reproducer params alone cannot say that.

The honest caveat, pinned by `test_as_of_equals_departure_time_under_synthetic`
below: under today's deterministic `[sim]` model traffic is a pure function of
the departure clock, so `as_of` IS `departure_time`. It is not yet independent
information. `profile_version` is, which is why it is here.
"""
import copy
import datetime

import pytest
from fastapi.testclient import TestClient

from pyref.config import Config
from pyref.engine import ROUTE_SCHEMA_VERSION, Router
from sim.snapshot import SYNTHETIC_SOURCE, at_time
from tests.helpers.fixtures import QUIET_DEPARTURE_ISO, line3, unprotected_left_city

CFG = Config.load()
BASIS_KEYS = {"source", "as_of", "profile_version"}
# The quiet hour, so the toy's fast route still takes the unprotected left and
# /route returns both a fast and a safe artifact (ADR-0016; QUIET_DEPARTURE).
DEPARTURE = QUIET_DEPARTURE_ISO


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
        "departure_time": DEPARTURE,
        "safety_enabled": True,
        **extra,
    }


def _reroute_body(pack, ids, preference):
    o, d = ids["s"], ids["a0"]
    return {
        "origin": {"lat": float(pack.node_lat[o]), "lon": float(pack.node_lon[o])},
        "destination": {"lat": float(pack.node_lat[d]), "lon": float(pack.node_lon[d])},
        "preference": preference,
    }


def _routes(client, **extra):
    return {r["kind"]: r for r in
            client.post("/route", json=_route_body(client.pack, client.ids, **extra))
            .json()["routes"]}


# --- the artifact side ------------------------------------------------------

def test_schema_version_is_2(client):
    assert ROUTE_SCHEMA_VERSION == 2
    for r in _routes(client).values():
        assert r["schema_version"] == 2


def test_every_artifact_carries_a_well_formed_traffic_basis(client):
    routes = _routes(client)
    assert len(routes) >= 2
    for r in routes.values():
        basis = r["preference"]["traffic_basis"]
        assert set(basis.keys()) == BASIS_KEYS
        # ADR-0010: the basis is `synthetic` until a real feed lands, and the
        # swap must then be a VALUE change at this key, not a shape change.
        assert basis["source"] == SYNTHETIC_SOURCE == "synthetic"
        assert basis["profile_version"] == CFG.sim_profile_version
        assert basis["profile_version"]  # non-empty


def test_as_of_equals_departure_time_under_synthetic(client):
    """Deliberate, documented duplication — NOT an oversight.

    `sim.profiles.multipliers_at` reads nothing but the departure clock, so the
    instant these traffic inputs were "observed" is the instant they describe.
    ADR-0010 says as much: "Two replans for the same departure time are
    byte-identical, so the diff can never fire."

    This assertion is expected to STOP holding the day a real feed lands: then
    `as_of` is when the feed observed the network, which is not when the user
    plans to leave, and the two fields carry different information.
    """
    for r in _routes(client).values():
        pref = r["preference"]
        assert pref["traffic_basis"]["as_of"] == pref["departure_time"]
        assert pref["departure_time"].startswith(DEPARTURE)


def test_same_edge_shortcircuit_artifact_carries_a_basis():
    """The origin/destination-on-one-edge fast path bypasses
    compute_alternatives entirely — it must still emit a complete v2
    artifact, basis included."""
    pack, a, b_mid, _c = line3()
    router = Router(pack, CFG)
    lat_a, lon_a = float(pack.node_lat[a]), float(pack.node_lon[a])
    lat_b, lon_b = float(pack.node_lat[b_mid]), float(pack.node_lon[b_mid])
    o_lat, o_lon = lat_a + 0.1 * (lat_b - lat_a), lon_a + 0.1 * (lon_b - lon_a)
    d_lat, d_lon = lat_a + 0.6 * (lat_b - lat_a), lon_a + 0.6 * (lon_b - lon_a)
    departure = datetime.datetime(2026, 7, 24, 8, 30)
    out = router.route(o_lat, o_lon, d_lat, d_lon, departure=departure)
    assert len(out) == 1
    assert out[0].schema_version == 2
    basis = out[0].preference["traffic_basis"]
    assert set(basis.keys()) == BASIS_KEYS
    assert basis["source"] == SYNTHETIC_SOURCE
    assert basis["as_of"] == departure


# --- the basis is produced by the snapshot, not by the API layer ------------

def test_the_snapshot_carries_its_own_basis():
    """ADR-0010 requires the eventual real-feed upgrade to be "a data swap, not
    an architecture change". That holds only if the basis is minted where the
    traffic inputs are — `sim/snapshot.py` — and the engine merely forwards it.
    A basis re-derived in the engine from (cfg, departure) would have to be
    rewritten the day a feed arrives."""
    pack, _ids = unprotected_left_city()
    departure = datetime.datetime(2026, 7, 24, 8, 30)
    snap = at_time(pack, CFG, departure)
    assert snap.basis is not None
    assert snap.basis.source == SYNTHETIC_SOURCE
    assert snap.basis.as_of == departure
    assert snap.basis.profile_version == CFG.sim_profile_version


def _cfg_with(mutate) -> Config:
    raw = copy.deepcopy(CFG.raw)
    mutate(raw)
    return Config(raw=raw, source_path=CFG.source_path)


def test_profile_version_changes_when_the_sim_profiles_change():
    """The half of the basis that is real information TODAY. A hand edit to the
    `[sim]` tables silently moves every eta_s in the system; without this, two
    artifacts computed against different profiles are indistinguishable."""
    def bump(raw):
        raw["sim"]["profiles"]["arterial"]["volume_mult"][8] += 0.01
    assert _cfg_with(bump).sim_profile_version != CFG.sim_profile_version

    def rebase(raw):
        raw["sim"]["base_vph_per_lane"]["primary"] = 601
    assert _cfg_with(rebase).sim_profile_version != CFG.sim_profile_version


def test_profile_version_ignores_config_outside_sim():
    """Scoped to `[sim]` on purpose, not `Config.content_hash()`. A rate-limit
    or CORS edit does not change the traffic inputs, and a basis that moved on
    every config commit would be noise in exactly the diff it exists to serve."""
    def unrelated(raw):
        raw["api"]["route_burst"] = 999
        raw["search"]["snap_k"] = 21
    assert _cfg_with(unrelated).sim_profile_version == CFG.sim_profile_version


def test_profile_version_is_stable_across_loads():
    assert Config.load().sim_profile_version == CFG.sim_profile_version


# --- backward compatibility: a v1 client keeps working ----------------------

def test_reroute_accepts_a_v1_preference_without_traffic_basis(client):
    """The frozen-contract rule (handoff, "Working conventions"): new wire
    shapes must be ADDITIVE. `Preference` is an INPUT on /reroute, carried
    verbatim off a prior artifact, so a client mid-drive holding a v1 artifact
    will POST a v1 preference. Making the field required on input would 422
    that in-flight nav session — the exact thing ADR-0008's reroute exists to
    keep alive."""
    pref = dict(_routes(client)["safe"]["preference"])
    v1_pref = {k: v for k, v in pref.items() if k != "traffic_basis"}
    assert set(v1_pref) == {"level", "lambda", "detour_budget_pct", "departure_time"}

    resp = client.post("/reroute", json=_reroute_body(client.pack, client.ids, v1_pref))
    assert resp.status_code == 200
    art = resp.json()["route"]
    # ...and the replacement is a full v2 artifact: the upgrade happens
    # server-side, so a stale client is dragged forward rather than pinned.
    assert art["schema_version"] == 2
    assert art["preference"]["traffic_basis"]["source"] == SYNTHETIC_SOURCE
    assert art["kind"] == "safe"
    assert art["geometry"] == _routes(client)["safe"]["geometry"]


def test_reroute_recomputes_the_basis_instead_of_echoing_it(client):
    """A carried basis describes the OLD artifact's inputs. The reroute builds a
    fresh snapshot, so its artifact must describe THAT one — otherwise the
    commute planner's disruption diff (ADR-0011) would compare an artifact
    against a basis it never used."""
    pref = dict(_routes(client)["fast"]["preference"])
    pref["traffic_basis"] = {"source": "some-old-feed",
                             "as_of": "2020-01-01T00:00:00",
                             "profile_version": "deadbeefdead"}
    art = client.post(
        "/reroute", json=_reroute_body(client.pack, client.ids, pref)).json()["route"]
    basis = art["preference"]["traffic_basis"]
    assert basis["source"] == SYNTHETIC_SOURCE
    assert basis["profile_version"] == CFG.sim_profile_version
    assert basis["as_of"].startswith(DEPARTURE)


def test_reroute_still_rejects_a_preference_missing_a_required_field(client):
    """Optional-on-input applies to `traffic_basis` alone; the v1 reproducer
    params stay required, so a genuinely malformed preference is still a 422."""
    pref = dict(_routes(client)["safe"]["preference"])
    pref.pop("lambda")
    resp = client.post("/reroute", json=_reroute_body(client.pack, client.ids, pref))
    assert resp.status_code == 422
