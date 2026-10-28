"""The Trip planner pipeline end to end, against a scripted Jev and `/route`."""
from __future__ import annotations

import asyncio

from planner.pipeline import plan
from planner.schemas import PlanRequest
from tests.helpers.planner_fakes import THREE, FakeRoutes, ScriptedJev, parse_answers, route

NOW = "2026-10-27T09:30"   # a Tuesday


def _req(text: str = "safest way please", **kw) -> PlanRequest:
    return PlanRequest(origin={"lat": 37.8, "lon": -122.27},
                       destination={"lat": 37.81, "lon": -122.26},
                       request=text, now_local=NOW, **kw)


def _run(req, jev, routes):
    return asyncio.run(plan(req, jev, routes))[0]


def test_a_plain_safety_request_uses_one_route_call_and_jev_chooses():
    routes, jev = FakeRoutes(), ScriptedJev(parse_answers(tradeoff=2), choice="safe")
    resp = _run(_req(), jev, routes)
    assert resp.status == "ok"
    assert resp.choice == "safe" and resp.choice_source == "jev"
    assert resp.rule_choice == "safe"
    assert len(routes.calls) == 1          # the speculative call was reused
    assert "departure_time" not in routes.calls[0]
    assert resp.explanation[0].startswith("Chose safe: 13 min, 3 min longer than fast")
    assert "You put safety first." in resp.explanation
    assert len(jev.calls) == 2             # parse + choice
    assert resp.timing.jev_input_tokens == 2000


def test_a_constraint_filters_candidates_and_names_what_it_ruled_out():
    jev = ScriptedJev(parse_answers(lefts=0.9, tradeoff=0), choice="balanced")
    resp = _run(_req("no unprotected lefts"), jev, FakeRoutes())
    assert [s.candidate for s in resp.summaries] == [False, True, True]
    assert resp.summaries[0].violates == ["no_unprotected_lefts"]
    assert resp.choice == "balanced"
    assert "Ruled out by your limits: fast." in resp.explanation
    assert any(s.startswith("Kept to your limits: no unprotected left turns")
               for s in resp.explanation)


def test_a_minute_budget_is_converted_against_the_fastest_eta_and_replanned():
    routes = FakeRoutes()
    jev = ScriptedJev(parse_answers(tradeoff=2, detour="min_10"), choice="safe")
    resp = _run(_req("safest but no more than 10 minutes longer"), jev, routes)
    assert len(routes.calls) == 2
    assert routes.calls[1]["detour_budget_pct"] == round(600 / 600, 4)
    assert resp.parse.detour_budget_pct == 1.0
    assert resp.choice == "safe"


def test_a_tight_budget_with_a_rule_is_infeasible_and_proposes_a_relaxation():
    # balanced has a crossing, safe is 3 min longer than a 2-minute budget allows.
    jev = ScriptedJev(parse_answers(crossings=0.9, detour="min_5", tradeoff=0))
    tight = [route("fast", 600, 2, 1), route("balanced", 660, 0, 1),
             route("safe", 1020, 0, 0)]
    resp = _run(_req("no uncontrolled crossings, at most 5 minutes extra"), jev,
                FakeRoutes(tight))
    assert resp.status == "infeasible" and resp.choice is None
    assert resp.relaxation is not None
    assert resp.relaxation.constraints == ["no_uncontrolled_crossings"]
    assert resp.relaxation.would_allow == ["fast", "balanced"]
    assert resp.explanation[0] == "No route meets all of your limits."
    assert len(jev.calls) == 1             # no Choice is asked for


def test_confirming_the_relaxation_turns_the_rule_into_a_tradeoff():
    jev = ScriptedJev(parse_answers(crossings=0.9, detour="min_5", tradeoff=0),
                      choice="balanced")
    tight = [route("fast", 600, 2, 1), route("balanced", 660, 0, 1),
             route("safe", 1020, 0, 0)]
    resp = _run(_req("no uncontrolled crossings, at most 5 minutes extra",
                     relax=["no_uncontrolled_crossings"]), jev, FakeRoutes(tight))
    assert resp.status == "ok" and resp.choice == "balanced"
    assert resp.rule_choice == "balanced"   # tradeoff 0 -> +1 -> still balanced
    assert any(s.startswith("As you confirmed") for s in resp.explanation)


def test_one_candidate_needs_no_jev_choice():
    jev = ScriptedJev(parse_answers(lefts=0.9, crossings=0.9))
    resp = _run(_req("no lefts, no uncontrolled crossings"), jev, FakeRoutes())
    assert resp.choice == "safe" and resp.choice_source == "only_candidate"
    assert len(jev.calls) == 1


def test_a_departure_bucket_resolves_to_the_next_weekday_peak():
    routes = FakeRoutes()
    jev = ScriptedJev(parse_answers(departure="weekday_pm_peak"), choice="safe")
    resp = _run(_req("leaving after work"), jev, routes)
    assert routes.calls[1]["departure_time"] == "2026-10-27T17:30"
    assert resp.parse.departure_time == "2026-10-27T17:30"
    assert any("(Tue 17:30)" in s for s in resp.explanation)


def test_unsupported_clauses_are_quoted_verbatim():
    jev = ScriptedJev(parse_answers(beyond=0.9, clause_yes=frozenset({1})), choice="safe")
    resp = _run(_req("safest route, and avoid the freeway"), jev, FakeRoutes())
    assert resp.parse.unsupported == ["avoid the freeway"]
    assert 'Could not apply: "avoid the freeway".' in resp.explanation


def test_a_jev_failure_is_the_plain_comparison():
    routes = FakeRoutes()
    resp = _run(_req(), ScriptedJev(parse_answers(), fail=True), routes)
    assert resp.status == "unavailable" and resp.choice is None and resp.parse is None
    assert resp.routes == THREE and len(routes.calls) == 1


def test_a_choice_failure_is_also_the_plain_comparison():
    resp = _run(_req(), ScriptedJev(parse_answers(tradeoff=0), choice=None), FakeRoutes())
    assert resp.status == "unavailable" and resp.choice is None
    assert resp.parse is not None


def test_no_jev_at_all_is_the_plain_comparison():
    resp = _run(_req(), None, FakeRoutes())
    assert resp.status == "unavailable"
