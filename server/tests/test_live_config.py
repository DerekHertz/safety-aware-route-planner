"""S6: live adapters are configurable by environment; the compose stack serves OSRM. Offline."""

from pathlib import Path

import pytest

from gapcross.sources import PUBLIC_OSRM, PUBLIC_OVERPASS, live_sources

ROOT = Path(__file__).resolve().parents[2]


def test_defaults_are_the_public_servers(monkeypatch):
    monkeypatch.delenv("GAPCROSS_OSRM_URL", raising=False)
    monkeypatch.delenv("GAPCROSS_OVERPASS_URL", raising=False)
    s = live_sources()
    assert s.routes.base_url == PUBLIC_OSRM
    assert s.records.url == PUBLIC_OVERPASS


def test_environment_selects_self_hosted_osrm_and_overpass(monkeypatch):
    monkeypatch.setenv("GAPCROSS_OSRM_URL", "http://osrm:5000/")
    monkeypatch.setenv("GAPCROSS_OVERPASS_URL", "http://overpass.local/api/interpreter")
    s = live_sources()
    assert s.routes.base_url == "http://osrm:5000"
    assert s.records.url == "http://overpass.local/api/interpreter"


def test_explicit_argument_beats_environment(monkeypatch):
    monkeypatch.setenv("GAPCROSS_OSRM_URL", "http://env:5000")
    assert live_sources(osrm_url="http://arg:5000").routes.base_url == "http://arg:5000"


def test_compose_defines_an_osrm_service_and_points_the_server_at_it():
    text = (ROOT / "compose.yaml").read_text()
    assert "osrm:" in text and "osrm/osrm-backend" in text
    assert "GAPCROSS_OSRM_URL" in text


def test_prepare_script_and_data_dir_are_in_place():
    assert (ROOT / "server/osrm/prepare.sh").exists()
    assert "server/osrm/data/" in (ROOT / ".gitignore").read_text()


@pytest.mark.live
def test_live_compare_finishes_inside_the_deadline():
    """The S6 check, on the self-hosted OSRM with the recorded corridor snapshot held."""
    import time

    from gapcross.app import CorridorRecords
    from gapcross.assess import FixedAdvisor
    from gapcross.compare import compare
    from gapcross.sources import Sources, offline_sources
    from gapcross.trips import load_trips

    trip = load_trips()["bowl-west-to-campus"]
    records = CorridorRecords(offline_sources().records)
    records.snapshot(trip.bbox)
    start = time.monotonic()
    result = compare(trip, Sources(live_sources().routes, records), FixedAdvisor(), source="osrm")
    assert time.monotonic() - start < 10.0
    assert result.outcome == "improvement"


@pytest.mark.live
def test_local_osrm_routes_the_demo_trip():
    from gapcross.trips import load_trips
    trip = load_trips()["bowl-west-to-campus"]
    route = live_sources().routes.route([trip.origin, trip.destination])
    assert 300 < route.duration_s < 1500
