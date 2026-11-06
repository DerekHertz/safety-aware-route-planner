"""S3: assessment rules over records, with the Jev seam stubbed by fixed answers.

Expected statuses come from the recorded snapshot read by hand (node ids below) or from an
independent scan in this file, never from the code under test.
"""

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from gapcross.assess import Advice, FixedAdvisor, JevAnswer, assess_route
from gapcross.sources import LatLon, SourceUnavailable, offline_sources
from gapcross.trips import load_trips

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DEMO = "bowl-west-to-campus"

SACRAMENTO_AT_OREGON = "53050628+240469698/straight/E"
SACRAMENTO_AT_WARD = "53050627+240469515/straight/E"
SHATTUCK_AT_PARKER = "53085614+259581494/left_onto/E"  # stop on Parker ~20 m west, eastbound
OREGON_EASTBOUND_STOP = 4363724428
CONTROLS = {"stop", "give_way", "traffic_signals"}


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
def example_raw(sources, trip):
    return sources.routes.route([trip.origin, *trip.example_via, trip.destination])


@pytest.fixture(scope="module")
def dohr_ward_raw(sources):
    routes = json.loads((FIXTURES / "extra_routes.json").read_text())["routes"]
    return sources.routes.route([LatLon(**p) for p in routes[0]["waypoints"]])


@pytest.fixture(scope="module")
def bare(snapshot):
    """The real snapshot with every control record within 60 m of Shattuck × Parker removed:
    no corridor crossing lacks records entirely, so the no-record case is made here."""
    junctions = [snapshot.nodes[n].location for n in (53085614, 259581494)]
    nodes = {
        i: replace(n, tags={}) if n.tags.get("highway") in CONTROLS
        and min(_metres(n.location, j) for j in junctions) <= 60 else n
        for i, n in snapshot.nodes.items()
    }
    stripped = replace(snapshot, nodes=nodes)

    class Bare:
        def snapshot(self, bbox):
            return stripped

    return Bare()


def test_shattuck_at_parker_is_flagged_on_its_parker_stop(dohr_ward_raw, sources, trip):
    result = assess_route(dohr_ward_raw, sources.records, trip.bbox, FixedAdvisor())
    assert _by_key(result)[SHATTUCK_AT_PARKER].assessment.status == "flagged"


def _by_key(result):
    return {c.key: c for c in result.crossings}


def test_oregon_is_flagged_on_its_stop_sign(example_raw, sources, trip):
    result = assess_route(example_raw, sources.records, trip.bbox, FixedAdvisor())
    oregon = _by_key(result)[SACRAMENTO_AT_OREGON].assessment
    assert oregon.status == "flagged"
    refs = {e.ref for e in oregon.evidence if not e.is_lead}
    assert f"node/{OREGON_EASTBOUND_STOP}" in refs


def test_ward_is_unflagged_on_its_signals(dohr_ward_raw, sources, trip):
    result = assess_route(dohr_ward_raw, sources.records, trip.bbox, FixedAdvisor())
    ward = _by_key(result)[SACRAMENTO_AT_WARD].assessment
    assert ward.status == "unflagged"
    assert {e.ref for e in ward.evidence} >= {"node/53050627", "node/240469515"}


def test_junction_with_no_control_record_is_unknown(dohr_ward_raw, bare, trip):
    result = assess_route(dohr_ward_raw, bare, trip.bbox, FixedAdvisor())
    assert _by_key(result)[SHATTUCK_AT_PARKER].assessment.status == "unknown"


def test_rule_1_no_record_is_never_unflagged(example_raw, dohr_ward_raw, bare, trip):
    """Rule 1. The oracle is an independent radius scan of the snapshot for any control node."""
    snapshot = bare.snapshot(trip.bbox)
    checked = 0
    for raw in (example_raw, dohr_ward_raw):
        result = assess_route(raw, bare, trip.bbox, FixedAdvisor())
        for c in result.crossings:
            nodes = [int(n) for n in c.key.split("/")[0].split("+")]
            if not any(_controls_within(snapshot, n, 40) for n in nodes):
                checked += 1
                assert c.assessment.status == "unknown", c.key
        statuses = [c.assessment.status for c in result.crossings]
        assert result.flag_count == statuses.count("flagged")
        assert result.unknown_count == statuses.count("unknown")
    assert checked >= 1, "the oracle must find at least one crossing without records"


def test_rule_2_records_unavailable_fails_the_check(example_raw, trip):
    class Down:
        def snapshot(self, bbox):
            raise SourceUnavailable("overpass down")

    result = assess_route(example_raw, Down(), trip.bbox, FixedAdvisor())
    assert result.status == "check_failed"
    assert result.flag_count is None and result.unknown_count is None
    assert all(c.assessment.status == "check_failed" for c in result.crossings)


def test_rule_2_uncovered_junction_fails_the_check(example_raw, sources, trip):
    moved = _shift(example_raw, 5 / 111_320)
    result = assess_route(moved, sources.records, trip.bbox, FixedAdvisor())
    assert result.status == "check_failed"
    assert result.flag_count is None


def test_rule_2_jev_unavailable_fails_the_check(dohr_ward_raw, bare, trip):
    class JevDown:
        def advise(self, state, questions):
            raise SourceUnavailable("jev down")

    result = assess_route(dohr_ward_raw, bare, trip.bbox, JevDown())
    assert _by_key(result)[SHATTUCK_AT_PARKER].assessment.status == "check_failed"
    assert _by_key(result)[SACRAMENTO_AT_WARD].assessment.status == "unflagged"
    assert result.status == "check_failed" and result.flag_count is None


def _stops_for_us(probability, gate_passed):
    return FixedAdvisor(Advice(
        answers={f"{SHATTUCK_AT_PARKER}.approach_control": JevAnswer("stops_for_us", probability)},
        model_version="jev-test", gate_passed=gate_passed,
    ))


def test_rule_8_a_lead_never_changes_a_status(dohr_ward_raw, bare, trip):
    result = assess_route(dohr_ward_raw, bare, trip.bbox, _stops_for_us(0.85, gate_passed=True))
    shattuck = _by_key(result)[SHATTUCK_AT_PARKER].assessment
    assert shattuck.status == "unknown"
    jev = [e for e in shattuck.evidence if e.source == "jev"]
    assert jev and all(e.is_lead for e in jev)


def test_rule_9_jev_counts_only_after_its_gate(dohr_ward_raw, bare, trip):
    before = assess_route(dohr_ward_raw, bare, trip.bbox, _stops_for_us(0.95, gate_passed=False))
    assert _by_key(before)[SHATTUCK_AT_PARKER].assessment.status == "unknown"

    after = assess_route(dohr_ward_raw, bare, trip.bbox, _stops_for_us(0.95, gate_passed=True))
    shattuck = _by_key(after)[SHATTUCK_AT_PARKER].assessment
    # The scan found nothing that stops Shattuck's traffic, so a stop for us makes a gap crossing.
    assert shattuck.status == "flagged"
    assert any(e.source == "jev" and not e.is_lead for e in shattuck.evidence)


def test_jev_is_asked_once_per_route_and_only_open_questions(dohr_ward_raw, bare, trip):
    calls = []

    class Recording:
        def advise(self, state, questions):
            calls.append(tuple(questions))
            return Advice(answers={}, model_version="jev-test", gate_passed=False)

    result = assess_route(dohr_ward_raw, bare, trip.bbox, Recording())
    assert len(calls) == 1
    asked = {q.rsplit(".", 1)[0] for q in calls[0]}
    unknown = {c.key for c in result.crossings if c.assessment.status == "unknown"}
    assert SHATTUCK_AT_PARKER in asked
    assert asked <= unknown
    assert SACRAMENTO_AT_WARD not in asked


def test_no_open_questions_means_no_jev_call(sources, trip):
    calls = []

    class Recording:
        def advise(self, state, questions):
            calls.append(questions)
            return Advice(answers={}, model_version="jev-test", gate_passed=False)

    raw = sources.routes.route([trip.origin, trip.destination])  # every crossing settled by records
    result = assess_route(raw, sources.records, trip.bbox, Recording())
    assert result.crossings and all(c.assessment.status != "unknown" for c in result.crossings)
    assert calls == []


def _controls_within(snapshot, node_id, radius_m):
    here = snapshot.nodes[node_id].location
    for n in snapshot.nodes.values():
        if n.tags.get("highway") in CONTROLS and _metres(here, n.location) <= radius_m:
            return True
    return False


def _metres(a, b):
    k = 111_320
    return math.hypot((a.lat - b.lat) * k, (a.lon - b.lon) * k * math.cos(math.radians(a.lat)))


def _shift(raw, dlat):
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
