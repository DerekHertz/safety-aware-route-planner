"""Explanations are grounded by construction (ADR-0019, 2026-10-27 amendment):
every number in one appears in the summaries or the parse it was built from,
and every route name it uses is one of the alternatives."""
from __future__ import annotations

import asyncio
import itertools
import re

from planner.pipeline import plan
from planner.schemas import PlanRequest
from tests.helpers.planner_fakes import FakeRoutes, ScriptedJev, parse_answers, route

ROUTE_SETS = [
    [route("fast", 600, 2, 1), route("balanced", 660, 0, 1), route("safe", 780, 0, 0)],
    [route("fast", 1500, 0, 3, 1500), route("balanced", 1560, 1, 0, 1500),
     route("safe", 2400, 0, 0, 1500)],
    [route("fast", 420, 1, 0, 420), route("balanced", 420, 1, 0, 420),
     route("safe", 455, 0, 0, 420)],
]
PARSES = [
    dict(tradeoff=2), dict(tradeoff=-2), dict(lefts=0.9, all_lefts=0.9, tradeoff=1),
    dict(crossings=0.9, detour="min_5"), dict(detour="pct_20", departure="night"),
    dict(lefts=0.9, crossings=0.9, detour="min_10"), dict(departure="other", detour="other"),
]


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def test_every_number_and_name_in_an_explanation_is_grounded():
    checked = 0
    for routes, kw, choice in itertools.product(ROUTE_SETS, PARSES, ["fast", "balanced",
                                                                       "safe"]):
        req = PlanRequest(origin={"lat": 37.8, "lon": -122.27},
                          destination={"lat": 37.81, "lon": -122.26},
                          request="r", now_local="2026-10-27T09:30")
        resp, _ = asyncio.run(plan(req, ScriptedJev(parse_answers(**kw), choice=choice),
                                   FakeRoutes(routes)))
        if resp.status == "unavailable":       # Jev chose a non-Candidate
            continue
        allowed: set[str] = set()
        for s in resp.summaries:
            allowed |= {str(s.minutes), str(s.extra_minutes), str(s.unprotected_lefts),
                        str(s.uncontrolled_crossings), str(s.wait_minutes)}
        p = resp.parse
        assert p is not None
        allowed |= _numbers(p.detour)                          # "min_10" -> 10
        if p.departure_time:
            allowed |= _numbers(p.departure_time[11:])         # "02:00"
        text = " ".join(resp.explanation)
        assert _numbers(text) <= allowed, (kw, choice, text)
        named = set(re.findall(r"\b(fast|balanced|safe)\b", text))
        assert named <= {s.kind for s in resp.summaries}
        checked += 1
    assert checked > 40
