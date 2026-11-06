"""S2: movement interpretation turns a route into ordered movements and crossings.

Expected keys are written out from the OSM node ids seen in the recorded snapshot, not computed
by the code under test.
"""

import json
import math
from pathlib import Path

import pytest

from gapcross.interpret import interpret
from gapcross.sources import LatLon, offline_sources
from gapcross.trips import load_trips

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DEMO = "bowl-west-to-campus"

SACRAMENTO_AT_OREGON = "53050628+240469698/straight/E"
SACRAMENTO_AT_WARD = "53050627+240469515/straight/E"
OREGON_AT_DOHR = 53064667
DOHR_AT_WARD = 53103724


@pytest.fixture(scope="module")
def sources():
    return offline_sources()


@pytest.fixture(scope="module")
def trip():
    return load_trips()[DEMO]


@pytest.fixture(scope="module")
def snapshot(sources, trip):
    return sources.records.snapshot(trip.bbox)


@pytest.fixture(scope="module")
def example(sources, trip, snapshot):
    raw = sources.routes.route([trip.origin, *trip.example_via, trip.destination])
    return interpret(raw, snapshot)


@pytest.fixture(scope="module")
def dohr_ward(sources, snapshot):
    routes = json.loads((FIXTURES / "extra_routes.json").read_text())["routes"]
    waypoints = [LatLon(**p) for p in routes[0]["waypoints"]]
    return interpret(sources.routes.route(waypoints), snapshot)


def _nodes(crossing) -> set[int]:
    return {int(n) for n in crossing.key.split("/")[0].split("+")}


def test_example_route_crosses_sacramento_once(example):
    sacramento = [c for c in example.crossings if c.main_street == "Sacramento Street"]
    assert [c.key for c in sacramento] == [SACRAMENTO_AT_OREGON]


def test_divided_sacramento_is_one_crossing_of_two_movements(example):
    crossing = next(c for c in example.crossings if c.key == SACRAMENTO_AT_OREGON)
    assert [m.node_id for m in crossing.movements] == [53050628, 240469698]
    assert crossing.kind == "straight"
    assert 45 <= crossing.approach_bearing <= 112.5  # eastbound on Oregon


def test_dohr_ward_route_crosses_at_ward_not_dohr(dohr_ward):
    keys = [c.key for c in dohr_ward.crossings]
    assert SACRAMENTO_AT_WARD in keys
    assert SACRAMENTO_AT_OREGON not in keys
    for c in dohr_ward.crossings:
        assert not _nodes(c) & {OREGON_AT_DOHR, DOHR_AT_WARD}, c.key


def test_turn_at_a_via_point_is_still_a_movement(dohr_ward):
    """The via point sits on the Oregon/Dohr junction, where OSRM gives no approach bearing."""
    at_dohr = [m for m in dohr_ward.movements if m.node_id == OREGON_AT_DOHR]
    assert len(at_dohr) == 1
    assert at_dohr[0].turn == "left"


def test_left_onto_divided_san_pablo_is_one_left_onto_crossing(example):
    crossing = next(c for c in example.crossings if c.main_street == "San Pablo Avenue")
    assert crossing.key == "53117370+243673082/left_onto/E"


def test_right_turns_are_movements_but_never_crossings(example):
    rights = {m.node_id for m in example.movements if m.turn == "right"}
    assert 243673085 in rights  # San Pablo right onto Russell
    for c in example.crossings:
        assert not _nodes(c) & rights, c.key


def test_crossings_are_in_route_order(example, dohr_ward):
    for result in (example, dohr_ward):
        along = [c.along_m for c in result.crossings]
        assert along == sorted(along)
        assert len({c.key for c in result.crossings}) == len(result.crossings)


def test_movements_match_junctions_within_3_m_only(sources, trip, snapshot):
    """Shift the whole route 5 m north. The Sacramento junctions are then over 3 m away and drop
    out; any junction still matched must be within 3 m of a shifted intersection. (The corridor
    has junctions under 5 m apart, so some other junction can legitimately match.)"""
    raw = sources.routes.route([trip.origin, *trip.example_via, trip.destination])
    moved = _shift(raw, 5 / 111_320)
    result = interpret(moved, snapshot)
    matched = {m.node_id for m in result.movements}
    assert not matched & {53050628, 240469698}
    assert not any(c.main_street == "Sacramento Street" for c in result.crossings)
    points = [i.location for s in moved.steps for i in s.intersections]
    for m in result.movements:
        node = snapshot.nodes[m.node_id].location
        assert min(_metres(node, q) for q in points) <= 3.0, m.node_id


def _metres(a, b):
    k = 111_320
    return math.hypot((a.lat - b.lat) * k, (a.lon - b.lon) * k * math.cos(math.radians(a.lat)))


def _shift(raw, dlat):
    from dataclasses import replace

    def p(x):
        return LatLon(lat=x.lat + dlat, lon=x.lon)

    return replace(
        raw,
        geometry=tuple(p(x) for x in raw.geometry),
        steps=tuple(
            replace(s, location=p(s.location),
                    intersections=tuple(replace(i, location=p(i.location)) for i in s.intersections))
            for s in raw.steps
        ),
    )
