"""S5: the HTTP service on recorded fixtures."""

import json
import math
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gapcross.app import Service, create_app
from gapcross.assess import FixedAdvisor
from gapcross.sources import LatLon, SourceUnavailable, Sources, offline_sources

DEMO = "bowl-west-to-campus"
OREGON = "53050628+240469698/straight/E"
WARD = "53050627+240469515/straight/E"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
REROUTE_AT = json.loads((FIXTURES / "extra_routes.json").read_text())["reroutes"][0]["position"]


def _client(tmp_path, sources=None):
    service = Service(sources=sources or offline_sources(), advisor=FixedAdvisor(), source="recorded",
                      reports_path=tmp_path / "drive_reports.sqlite")
    return TestClient(create_app(service))


@pytest.fixture()
def client(tmp_path):
    return _client(tmp_path)


def test_trips_lists_the_demo_trip_and_nothing_internal(client):
    trips = client.get("/trips").json()
    demo = next(t for t in trips if t["id"] == DEMO)
    assert set(demo) == {"id", "name", "origin", "destination"}


def test_default_is_the_assessed_example_route(client):
    route = client.post(f"/trips/{DEMO}/default").json()
    assert route["label"] == "example" and route["source"] == "recorded"
    oregon = next(c for c in route["crossings"] if c["key"] == OREGON)
    assert oregon["assessment"]["status"] == "flagged"


def test_comparison_offers_ward(client):
    body = client.post(f"/trips/{DEMO}/comparison", json={"avoid": True}).json()
    assert body["outcome"] == "improvement"
    assert WARD in {c["key"] for c in body["alternative"]["crossings"]}


def test_comparison_with_avoidance_off_is_refused(client):
    """With avoidance off the app calls only /default."""
    assert client.post(f"/trips/{DEMO}/comparison", json={"avoid": False}).status_code == 422


def test_unknown_trip_is_404(client):
    assert client.post("/trips/nowhere/default").status_code == 404
    assert client.post("/trips/nowhere/comparison", json={"avoid": True}).status_code == 404
    assert client.post("/trips/nowhere/reroute", json={"position": REROUTE_AT}).status_code == 404


def test_route_failure_is_503(tmp_path):
    class Down:
        def route(self, waypoints):
            raise SourceUnavailable("osrm down")

    client = _client(tmp_path, Sources(Down(), offline_sources().records))
    for path, body in ((f"/trips/{DEMO}/default", None), (f"/trips/{DEMO}/comparison", {"avoid": True}),
                       (f"/trips/{DEMO}/reroute", {"position": REROUTE_AT})):
        response = client.post(path, json=body)
        assert response.status_code == 503, path
        assert response.json() == {"detail": "Route couldn't be obtained."}


def test_records_failure_is_check_failed_not_503(tmp_path):
    class Down:
        def snapshot(self, bbox):
            raise SourceUnavailable("overpass down")

    client = _client(tmp_path, Sources(offline_sources().routes, Down()))
    body = client.post(f"/trips/{DEMO}/comparison", json={"avoid": True}).json()
    assert body["outcome"] == "check_failed" and body["alternative"] is None
    assert body["default"]["crossings_checked"] is False


def test_rule_2_default_says_when_its_check_failed(tmp_path):
    """An empty crossings list must not read as "no crossings" when the check could not run."""
    class Down:
        def snapshot(self, bbox):
            raise SourceUnavailable("overpass down")

    route = _client(tmp_path, Sources(offline_sources().routes, Down())).post(f"/trips/{DEMO}/default").json()
    assert route["crossings"] == []
    assert route["crossings_checked"] is False


def test_assessed_routes_say_their_crossings_were_checked(client):
    assert client.post(f"/trips/{DEMO}/default").json()["crossings_checked"] is True
    body = client.post(f"/trips/{DEMO}/comparison", json={"avoid": True}).json()
    assert body["default"]["crossings_checked"] is True
    assert body["alternative"]["crossings_checked"] is True


def test_rule_10_same_request_gives_byte_identical_responses(tmp_path, client):
    other = _client(tmp_path / "second")
    for path, body in ((f"/trips/{DEMO}/default", None), (f"/trips/{DEMO}/comparison", {"avoid": True})):
        first = client.post(path, json=body).content
        assert client.post(path, json=body).content == first, path
        assert other.post(path, json=body).content == first, path


def test_rule_11_reroute_is_unassessed_and_labeled(client):
    route = client.post(f"/trips/{DEMO}/reroute", json={"position": REROUTE_AT}).json()
    assert route["label"] == "rerouted"
    assert route["crossings"] == []
    assert route["crossings_checked"] is False
    start = route["geometry"][0]
    assert _metres(start, REROUTE_AT) < 50


def test_drive_report_is_stored(tmp_path, client):
    report = {"trip_id": DEMO, "route_label": "example", "depart_at": "2026-11-06T17:00:00Z",
              "crossings": [{"key": OREGON, "answer": "as_described"}]}
    assert client.post("/drive-reports", json=report).status_code == 204
    with sqlite3.connect(tmp_path / "drive_reports.sqlite") as db:
        rows = db.execute("SELECT trip_id, route_label, crossing_key, answer FROM drive_report_crossings").fetchall()
    assert rows == [(DEMO, "example", OREGON, "as_described")]


def test_drive_report_for_unknown_trip_is_404(client):
    report = {"trip_id": "nowhere", "route_label": "example", "depart_at": "2026-11-06T17:00:00Z", "crossings": []}
    assert client.post("/drive-reports", json=report).status_code == 404


def test_turns_at_via_points_are_announced_as_turns(client):
    """Oracle: the turn angle at each step, measured here from the route geometry."""
    body = client.post(f"/trips/{DEMO}/comparison", json={"avoid": True}).json()
    for route in (body["default"], body["alternative"]):
        steps = route["steps"]
        assert not any(s["instruction"].endswith("the road") for s in steps)
        for s in steps[1:-1]:
            turn = _turn_at(route["geometry"], s["location"])
            if turn is not None and 60 <= abs(turn) <= 135:
                assert not s["instruction"].startswith("Continue"), (s["instruction"], round(turn))


def _turn_at(geometry, at, reach=20.0):
    pts = [(p["lat"], p["lon"]) for p in geometry]
    k = _closest(pts, (at["lat"], at["lon"]))
    if k == 0 or k == len(pts) - 1:
        return None
    a = _back(pts, k, reach)
    b = _ahead(pts, k, reach)
    return (_bearing(pts[k], b) - _bearing(a, pts[k]) + 540) % 360 - 180


def _closest(pts, p):
    return min(range(len(pts)), key=lambda i: _m(pts[i], p))


def _back(pts, k, reach):
    d, j = 0.0, k
    while j > 0 and d < reach:
        d += _m(pts[j], pts[j - 1])
        j -= 1
    return pts[j]


def _ahead(pts, k, reach):
    d, j = 0.0, k
    while j < len(pts) - 1 and d < reach:
        d += _m(pts[j], pts[j + 1])
        j += 1
    return pts[j]


def _m(a, b):
    kk = 111_320
    return math.hypot((a[0] - b[0]) * kk, (a[1] - b[1]) * kk * math.cos(math.radians(a[0])))


def _metres(a, b):
    return _m((a["lat"], a["lon"]), (b["lat"], b["lon"]))


def _bearing(a, b):
    kk = 111_320
    x = (b[1] - a[1]) * kk * math.cos(math.radians(a[0]))
    return math.degrees(math.atan2(x, (b[0] - a[0]) * kk)) % 360
