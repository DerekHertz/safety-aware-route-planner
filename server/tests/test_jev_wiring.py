"""S7 wired to S2/S3: the Jev advisor behind assessment, and the corridor pre-run and gate."""

import logging

import pytest

from gapcross.assess import assess_route
from gapcross.jev import AnswerCache, GateResult, JevAdvisor, offline_jev
from gapcross.prerun import corridor, prerun
from gapcross.sources import offline_sources
from gapcross.trips import load_trips
from test_jev import FakeJev, sure

DEMO = "bowl-west-to-campus"
OREGON = "53050628+240469698/straight/E"
WARD = "53050627+240469515/straight/E"


@pytest.fixture(scope="module")
def trips():
    return load_trips()


@pytest.fixture(scope="module")
def split(trips):
    return corridor(offline_sources(), trips)


def test_corridor_settles_oregon_and_ward_with_the_rules_answers(split):
    settled, _ = split
    rules = {s.key: dict(s.rule_answers) for s in settled}
    assert rules[OREGON] == {"approach_control": "does_not_stop"}  # flagged: Sacramento does not stop
    assert rules[WARD] == {"approach_control": "stops_for_us"}  # unflagged: signals


def test_corridor_counts_match_an_independent_assessment(split, trips):
    """Oracle: run the service's own assessment over the same routes and count statuses here."""
    from gapcross.compare import _OnceRecords
    from gapcross.search import targets
    from gapcross.assess import FixedAdvisor

    sources = offline_sources()
    trip = trips[DEMO]
    records = _OnceRecords(sources.records)
    default = sources.routes.route([trip.origin, *trip.example_via, trip.destination])
    routes = [sources.routes.route([trip.origin, trip.destination]), default]
    d = assess_route(default, records, trip.bbox, FixedAdvisor())
    routes += [sources.routes.route(list(t.waypoints))
               for t in targets(trip, default, d.interpretation, d.crossings, records.snapshot(trip.bbox), 8)]
    statuses = {}
    for raw in routes:
        for c in assess_route(raw, records, trip.bbox, FixedAdvisor()).crossings:
            statuses.setdefault(c.key, c.assessment.status)
    settled, open_ = split
    assert {s.key for s in settled} == {k for k, s in statuses.items() if s in ("flagged", "unflagged")}
    assert {o.key for o in open_} <= {k for k, s in statuses.items() if s == "unknown"}


def test_advisor_cache_hits_skip_jev(split):
    _, open_ = split
    settled, _ = split
    cache = AnswerCache(":memory:")
    jev = FakeJev()
    advisor = JevAdvisor(jev, cache)
    state = {s.key: dict(s.evidence) for s in settled[:3]}
    questions = [f"{k}.approach_control" for k in state]
    first = advisor.advise(state, questions)
    assert len(jev.calls) == 1 and set(first.answers) == set(questions)
    second = advisor.advise(state, questions)
    assert len(jev.calls) == 1, "a full cache hit must not call Jev"
    assert second.answers == first.answers


def test_advisor_reports_the_gate_for_the_answering_model_version():
    cache = AnswerCache(":memory:")
    jev = FakeJev(script={"k.approach_control": sure("stops_for_us")})
    advisor = JevAdvisor(jev, cache)
    assert advisor.advise({"k": {"e": 1}}, ["k.approach_control"]).gate_passed is False
    cache.record_gate(jev.model, GateResult(True, jev.version, 1, 1, 1, 0, 0))
    assert advisor.advise({"k": {"e": 1}}, ["k.approach_control"]).gate_passed is True


def test_recorded_corridor_prerun_logs_the_gate_with_counts(split, caplog):
    """The corridor pre-run replayed from answers recorded against the live API."""
    settled, open_ = split
    cache = AnswerCache(":memory:")
    with caplog.at_level(logging.INFO, logger="gapcross.jev"):
        result = prerun(offline_jev(), cache, settled, open_)
    gate = result.gate
    assert gate.settled == len(settled)
    assert gate.agree + gate.disagree + gate.abstain == len(settled)  # one question per settled crossing
    assert any(r.message.startswith("jev gate ") and f"settled={len(settled)}" in r.message for r in caplog.records)
    assert cache.gate_passed(gate.model_version) is gate.passed
