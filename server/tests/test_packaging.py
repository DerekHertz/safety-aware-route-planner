"""The container runs the recorded service on its own: what the iOS session starts on the Mac."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_image_carries_trips_and_fixtures():
    """The service reads server/trips.json and server/fixtures/ at run time."""
    dockerfile = (ROOT / "server/Dockerfile").read_text()
    assert "COPY trips.json" in dockerfile
    assert "COPY fixtures" in dockerfile
    ignored = (ROOT / "server/.dockerignore").read_text().split()
    assert "fixtures" not in ignored and "trips.json" not in ignored


def test_plain_compose_up_needs_no_osrm_graph():
    """osrm needs a prepared graph, so it runs only with `--profile live`."""
    compose = (ROOT / "compose.yaml").read_text()
    osrm = compose[compose.index("\n  osrm:"):]
    assert 'profiles: ["live"]' in osrm
    server = compose[compose.index("\n  server:"):compose.index("\n  osrm:")]
    assert "depends_on" not in server
