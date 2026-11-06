"""S1: recorded sources answer offline with the values seen in the access check."""

import pytest

from gapcross.sources import FixtureMissing, LatLon, offline_sources
from gapcross.trips import load_trips

DEMO = "bowl-west-to-campus"
SACRAMENTO_AT_OREGON = {53050628, 240469698}
OREGON_EASTBOUND_STOP = 4363724428


@pytest.fixture(scope="module")
def sources():
    return offline_sources()


@pytest.fixture(scope="module")
def trip():
    return load_trips()[DEMO]


def test_demo_trip_is_defined(trip):
    assert trip.example_via, "the example route must be forced along Oregon"


def test_example_route_crosses_sacramento_at_oregon(sources, trip):
    route = sources.routes.route([trip.origin, *trip.example_via, trip.destination])
    assert 700 <= route.duration_s <= 800
    near = [
        i for s in route.steps for i in s.intersections
        if abs(i.location.lat - 37.85603) < 0.0002 and abs(i.location.lon + 122.27962) < 0.0003
    ]
    assert len(near) == 2, "divided Sacramento appears as two intersections"


def test_via_legs_are_merged_into_one_route(sources, trip):
    route = sources.routes.route([trip.origin, *trip.example_via, trip.destination])
    kinds = [s.maneuver_type for s in route.steps]
    assert kinds[0] == "depart" and kinds[-1] == "arrive"
    assert kinds.count("depart") == 1 and kinds.count("arrive") == 1


def test_snapshot_holds_the_oregon_controls(sources, trip):
    snap = sources.records.snapshot(trip.bbox)
    assert SACRAMENTO_AT_OREGON <= snap.nodes.keys()
    assert snap.nodes[OREGON_EASTBOUND_STOP].tags == {"highway": "stop", "direction": "forward"}
    sacramento = [w for w in snap.ways.values() if w.tags.get("name") == "Sacramento Street"
                  and w.tags.get("highway") == "tertiary"]
    assert sacramento
    assert snap.taken_at is not None


def test_unrecorded_request_fails_loudly(sources):
    with pytest.raises(FixtureMissing):
        sources.routes.route([LatLon(lat=37.0, lon=-122.0), LatLon(lat=37.01, lon=-122.01)])
