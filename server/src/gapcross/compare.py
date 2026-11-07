"""Comparison: the default route, at most one alternative, and an outcome with a templated summary.

The improvement test (rule 3) compares only the crossings the two routes do not share: a crossing
on both routes is the same crossing with the same truth, so it counts the same on each. An
alternative is an improvement if its own crossings, counting every unknown as a flag, have fewer
flags than the default's own known flags. Candidates are ranked by (flags + unknowns, flags, ETA)
over the whole route.
"""

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from .assess import Advisor, assess_route
from .interpret import _bearing, _Polyline, _turn
from .search import Target, crosses_as_intended, targets
from .sources import BBox, RawRoute, RecordSnapshot, RecordSource, Sources, SourceUnavailable
from .trips import TripPlan
from .types import Comparison, Crossing, LatLon, Maneuver, Route, Step

DEADLINE_S = 10.0
CANDIDATE_LIMIT = 8  # provisional (S6): OSRM is ~20 ms a route; the snapshot and Jev set the limit
BUDGET_SHARE = 0.2
BUDGET_CAP_S = 300.0

Outcome = Literal["improvement", "no_improvement", "over_budget", "incomplete", "check_failed"]

TEMPLATES: dict[str, str] = {
    "improvement": "This alternative crosses {main_street} at {cross_street} instead, for about {added} more.",
    "improvement_partial": "Best found within this search: cross {main_street} at {cross_street}, "
                           "for about {added} more.",
    "no_flags": "No crossing on this route was flagged.",
    "no_improvement": "No alternative with fewer flagged crossings was found nearby.",
    "over_budget": "No improvement was found within the added-time budget.",
    "incomplete": "The search did not finish in time, and no alternative was found before it stopped.",
    "check_failed": "Crossings couldn't be checked.",
    "depart": "Start on {street}",
    "depart_toward": "Start toward {street}",
    "arrive": "Arrive at your destination",
    "turn": "Turn {modifier} onto {street}",
    "uturn": "Make a U-turn onto {street}",
    "end of road": "At the end of the road, turn {modifier} onto {street}",
    "roundabout": "At the roundabout, exit onto {street}",
    "keep": "Keep {modifier} onto {street}",
    "continue": "Continue on {street}",
    "minute": "1 minute",
    "minutes": "{n} minutes",
    "under_minute": "under a minute",
}


@dataclass(frozen=True)
class Assessed:
    crossings: tuple[Crossing, ...]
    eta_s: float
    failed: bool = False


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    chosen: int | None
    search_complete: bool
    added_s: float | None
    budget_s: float


def budget_s(default_eta_s: float) -> float:
    return min(BUDGET_SHARE * default_eta_s, BUDGET_CAP_S)


def decide(default: Assessed, candidates: Sequence[Assessed], search_complete: bool) -> Decision:
    budget = budget_s(default.eta_s)
    if default.failed or any(c.assessment.status == "check_failed" for c in default.crossings):
        return Decision("check_failed", None, search_complete, None, budget)
    if not any(c.assessment.status == "flagged" for c in default.crossings):
        return Decision("no_improvement", None, True, None, budget)

    default_keys = {c.key for c in default.crossings}
    improving = []
    for i, cand in enumerate(candidates):
        if cand.failed or any(c.assessment.status == "check_failed" for c in cand.crossings):
            continue
        cand_keys = {c.key for c in cand.crossings}
        default_known = _count(default.crossings, ("flagged",), exclude=cand_keys)
        cand_worst = _count(cand.crossings, ("flagged", "unknown"), exclude=default_keys)
        if cand_worst < default_known:
            improving.append(i)
    within = [i for i in improving if candidates[i].eta_s - default.eta_s <= budget]
    if within:
        best = min(within, key=lambda i: (
            _count(candidates[i].crossings, ("flagged", "unknown")),
            _count(candidates[i].crossings, ("flagged",)),
            candidates[i].eta_s,
        ))
        return Decision("improvement", best, search_complete, candidates[best].eta_s - default.eta_s, budget)
    if improving:
        return Decision("over_budget", None, search_complete, None, budget)
    if not search_complete:
        return Decision("incomplete", None, False, None, budget)
    return Decision("no_improvement", None, True, None, budget)


def compare(trip: TripPlan, sources: Sources, advisor: Advisor, clock: Callable[[], float] = time.monotonic,
            deadline_s: float = DEADLINE_S, limit: int = CANDIDATE_LIMIT,
            source: Literal["osrm", "recorded"] = "recorded") -> Comparison:
    """Raises SourceUnavailable when the default route can't be obtained (HTTP 503)."""
    raw = sources.routes.route([trip.origin, *trip.example_via, trip.destination])
    start = clock()
    records = _OnceRecords(sources.records)
    default = assess_route(raw, records, trip.bbox, advisor)
    default_a = Assessed(default.crossings, raw.duration_s, default.status == "check_failed")

    found: list[tuple[Assessed, RawRoute, Target]] = []
    complete = True
    if not default_a.failed and default.flag_count:
        snapshot = records.snapshot(trip.bbox)
        for target in targets(trip, raw, default.interpretation, default.crossings, snapshot, limit):
            if clock() - start >= deadline_s:
                complete = False
                break
            try:
                cand_raw = sources.routes.route(list(target.waypoints))
            except SourceUnavailable:
                complete = False
                continue
            cand = assess_route(cand_raw, records, trip.bbox, advisor)
            if cand.interpretation is None or not crosses_as_intended(target, cand.interpretation):
                continue
            found.append((Assessed(cand.crossings, cand_raw.duration_s, cand.status == "check_failed"),
                          cand_raw, target))

    decision = decide(default_a, [f[0] for f in found], complete)
    alternative = None
    if decision.chosen is not None:
        a, cand_raw, target = found[decision.chosen]
        alternative = _route(cand_raw, a.crossings, "provider", source, checked=True)
    return Comparison(
        default=_route(raw, () if default_a.failed else default.crossings, "example", source,
                       checked=not default_a.failed),
        alternative=alternative,
        outcome=decision.outcome,
        search_complete=decision.search_complete,
        budget_s=decision.budget_s,
        added_s=decision.added_s,
        summary=_summary(decision, default_a, found),
    )


def _count(crossings: Sequence[Crossing], statuses: tuple[str, ...], exclude: set[str] = frozenset()) -> int:
    return sum(c.assessment.status in statuses for c in crossings if c.key not in exclude)


def _summary(d: Decision, default: Assessed, found) -> str:
    if d.outcome == "improvement":
        target = found[d.chosen][2]
        name = "improvement" if d.search_complete else "improvement_partial"
        return TEMPLATES[name].format(main_street=target.main_street, cross_street=target.cross_street,
                                      added=_duration(d.added_s))
    if d.outcome == "no_improvement" and not any(c.assessment.status == "flagged" for c in default.crossings):
        return TEMPLATES["no_flags"]
    return TEMPLATES[d.outcome]


def _duration(seconds: float) -> str:
    minutes = math.ceil(max(seconds, 0) / 60)
    if seconds < 60:
        return TEMPLATES["under_minute"]
    return TEMPLATES["minute"] if minutes == 1 else TEMPLATES["minutes"].format(n=minutes)


def reroute(position: LatLon, trip: TripPlan, sources: Sources,
            source: Literal["osrm", "recorded"] = "recorded") -> Route:
    """An unassessed route from where the driver is, labeled rerouted (rule 11).
    Raises SourceUnavailable when the route can't be obtained (HTTP 503)."""
    raw = sources.routes.route([position, trip.destination])
    return _route(raw, (), "rerouted", source, checked=False)


def _route(raw: RawRoute, crossings: Sequence[Crossing], label, source, *, checked: bool) -> Route:
    return Route(label=label, source=source, geometry=list(raw.geometry), distance_m=raw.distance_m,
                 eta_s=raw.duration_s, steps=_steps(raw), crossings=list(crossings),
                 crossings_checked=checked)


MIN_STEP_M = 5.0  # shorter steps are leg-split stubs, not maneuvers


def _steps(raw: RawRoute) -> list[Step]:
    """Wire steps. A via point splits OSRM's legs, which leaves a bare `continue` where the driver
    may actually turn: its turn is measured from the route geometry. Straight continuations on
    the same street are folded into the step before them. A step shorter than MIN_STEP_M, which a
    leg split on a corner leaves before the real turn, is folded too: it would announce a second
    turn where the driver makes one."""
    line = _Polyline(raw.geometry)
    named = [s.name for s in raw.steps]
    out: list[dict] = []
    along = 0.0
    for i, s in enumerate(raw.steps):
        kind, modifier = s.maneuver_type, s.modifier
        if kind == "continue" and modifier is None and 0 < i < len(raw.steps) - 1:
            kind, modifier = _turn_from_geometry(line, s.location)
        street = s.name or next((n for n in named[i + 1:] if n), "")
        straight_on = kind in ("continue", "new name") and modifier in (None, "straight")
        stub = 0 < i < len(raw.steps) - 1 and s.distance_m < MIN_STEP_M and kind != "arrive"
        if out and (stub or straight_on and out[-1]["street"] == street) and out[-1]["type"] != "arrive":
            out[-1]["distance_m"] += s.distance_m
        else:
            out.append({"type": kind, "modifier": modifier, "street": street, "distance_m": s.distance_m,
                        "along_m": along, "location": s.location, "depart_unnamed": kind == "depart" and not s.name})
        along += s.distance_m
    return [
        Step(instruction=_instruction(o["type"], o["modifier"], o["street"], o["depart_unnamed"]),
             maneuver=Maneuver(type=o["type"], modifier=o["modifier"]), street=o["street"],
             distance_m=o["distance_m"], along_m=o["along_m"], location=o["location"])
        for o in out
    ]


def _turn_from_geometry(line: _Polyline, at: LatLon) -> tuple[str, str | None]:
    along = line.locate(at)
    if along < 1 or along > line.length - 1:
        return "continue", None
    here = line.point_at(along)
    turn = _turn(_bearing(line.point_at(along - 20), here), _bearing(here, line.point_at(along + 20)))
    if turn == "straight":
        return "continue", "straight"
    if turn == "u_turn":
        return "turn", "uturn"
    return "turn", turn


def _instruction(kind: str, modifier: str | None, street: str, depart_unnamed: bool = False) -> str:
    if kind == "depart":
        return TEMPLATES["depart_toward" if depart_unnamed else "depart"].format(street=street)
    if kind == "arrive":
        return TEMPLATES["arrive"]
    if modifier == "uturn":
        return TEMPLATES["uturn"].format(street=street)
    if kind in ("roundabout", "rotary", "exit roundabout", "exit rotary", "roundabout turn"):
        return TEMPLATES["roundabout"].format(street=street)
    if kind in ("turn", "end of road") and modifier and modifier != "straight":
        return TEMPLATES[kind].format(modifier=modifier, street=street)
    if kind in ("fork", "merge", "on ramp", "off ramp") and modifier and modifier != "straight":
        return TEMPLATES["keep"].format(modifier=modifier.replace("slight ", ""), street=street)
    return TEMPLATES["continue"].format(street=street)


class _OnceRecords:
    """One snapshot per comparison: every route in it is assessed against the same records."""

    def __init__(self, inner: RecordSource):
        self.inner = inner
        self._snap: dict[BBox, RecordSnapshot] = {}

    def snapshot(self, bbox: BBox) -> RecordSnapshot:
        if bbox not in self._snap:
            self._snap[bbox] = self.inner.snapshot(bbox)
        return self._snap[bbox]
