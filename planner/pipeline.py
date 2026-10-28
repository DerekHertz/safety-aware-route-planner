"""Request -> parse -> `/route` -> Candidate filter -> Choice -> Explanation.

The Trip planner is never worse than the app without it (ADR-0019): any Jev
failure, timeout, or a missing key returns the route service's plain
comparison with no Choice.

Latency: `/route` with default inputs starts *alongside* the parse, on the
bet that most Requests change neither the departure nor the detour budget.
When the parse does change them, that first answer is still used (its
fastest ETA turns a minute budget into `detour_budget_pct`) and one more
`/route` call is made.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from planner import explain
from planner.baseline import rule_choice
from planner.choose import (
    DETOUR,
    NO_CROSSINGS,
    NO_LEFTS,
    Constraints,
    Summary,
    baseline_view,
    candidates,
    jev_choice,
    propose_relaxation,
    summarize,
)
from planner.jev import Jev, JevError, JevReply
from planner.parse import DETOUR_BUCKETS, Parse, departure_time, parse_request
from planner.routes import RouteClient
from planner.schemas import (
    PlanRequest,
    PlanResponse,
    PlanTiming,
    Relaxation,
    RouteSummary,
    TripParse,
)

log = logging.getLogger("planner")

MAX_BUDGET_PCT = 2.0      # RouteRequest.detour_budget_pct's own bound


@dataclass
class _Meter:
    t0: float = field(default_factory=time.perf_counter)
    jev_ms: float = 0.0
    route_ms: float = 0.0
    tokens: int = 0
    cost: float = 0.0

    def jev(self, reply: JevReply) -> None:
        self.jev_ms += reply.latency_ms
        self.tokens += reply.input_tokens
        self.cost += reply.cost_usd

    def timing(self) -> PlanTiming:
        return PlanTiming(total_ms=round((time.perf_counter() - self.t0) * 1000, 1),
                          jev_ms=round(self.jev_ms, 1), route_ms=round(self.route_ms, 1),
                          jev_input_tokens=self.tokens, jev_cost_usd=self.cost)


def apply_relaxation(parse: Parse, relax: Sequence[str]) -> int:
    """A relaxed Constraint becomes a Tradeoff: one level toward what it
    protected (safety for the manoeuvre rules, speed for the detour budget)."""
    t = parse.tradeoff
    if (NO_LEFTS in relax and parse.no_unprotected_lefts) or \
            (NO_CROSSINGS in relax and parse.no_uncontrolled_crossings):
        t += 1
    if DETOUR in relax and DETOUR_BUCKETS.get(parse.detour) is not None:
        t -= 1
    return max(-2, min(2, t))


def constraints_for(parse: Parse, relax: Sequence[str]) -> Constraints:
    spec = None if DETOUR in relax else DETOUR_BUCKETS.get(parse.detour)
    return Constraints(
        no_unprotected_lefts=parse.no_unprotected_lefts and NO_LEFTS not in relax,
        no_uncontrolled_crossings=parse.no_uncontrolled_crossings
        and NO_CROSSINGS not in relax,
        max_extra_minutes=spec[1] if spec and spec[0] == "min" else None,
        max_detour_pct=spec[1] if spec and spec[0] == "pct" else None,
    )


def budget_pct(c: Constraints, first: Sequence[dict[str, Any]]) -> float | None:
    if c.max_detour_pct is not None:
        return min(MAX_BUDGET_PCT, c.max_detour_pct)
    if c.max_extra_minutes is not None:
        fastest = min(float(r["eta_s"]) for r in first)
        if fastest > 0:
            return min(MAX_BUDGET_PCT, c.max_extra_minutes * 60 / fastest)
    return None


def _route_body(req: PlanRequest, departure: datetime.datetime | None,
                pct: float | None) -> dict[str, Any]:
    body: dict[str, Any] = {"origin": req.origin.model_dump(),
                            "destination": req.destination.model_dump(),
                            "safety_enabled": True}
    if departure is not None:
        body["departure_time"] = departure.isoformat(timespec="minutes")
    if pct is not None:
        body["detour_budget_pct"] = round(pct, 4)
    return body


def _summaries(summaries: Sequence[Summary], c: Constraints | None) -> list[RouteSummary]:
    out = []
    for s in summaries:
        v = c.violations(s) if c else []
        out.append(RouteSummary(kind=s.kind, minutes=s.minutes,
                                extra_minutes=s.extra_minutes,
                                unprotected_lefts=s.unprotected_lefts,
                                uncontrolled_crossings=s.uncontrolled_crossings,
                                wait_minutes=s.wait_minutes, candidate=not v,
                                violates=v))  # type: ignore[arg-type]
    return out


def _now_local(req: PlanRequest) -> datetime.datetime:
    if req.now_local:
        try:
            return datetime.datetime.fromisoformat(req.now_local).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.datetime.now()


async def plan(req: PlanRequest, jev: Jev | None, routes: RouteClient,
               forwarded_for: str | None = None) -> tuple[PlanResponse, _Meter]:
    m = _Meter()

    async def call_route(body: dict[str, Any]) -> list[dict[str, Any]]:
        t0 = time.perf_counter()
        try:
            return await routes.route(body, forwarded_for)
        finally:
            m.route_ms += (time.perf_counter() - t0) * 1000

    first_task = asyncio.create_task(call_route(_route_body(req, None, None)))
    parse: Parse | None = None
    if jev is not None:
        try:
            parse, reply = await parse_request(jev, req.request)
            m.jev(reply)
        except JevError as exc:
            log.warning("plan: parse failed: %s", exc)
    try:
        first = await first_task
    except BaseException:
        first_task.cancel()
        raise

    if parse is None:
        return _unavailable(first, None, m), m

    relax = list(req.relax)
    c = constraints_for(parse, relax)
    tradeoff = apply_relaxation(parse, relax)
    departure_at = departure_time(parse.departure, _now_local(req))
    pct = budget_pct(c, first)
    routes_out = first
    if departure_at is not None or pct is not None:
        routes_out = await call_route(_route_body(req, departure_at, pct))

    wire_parse = TripParse(
        no_unprotected_lefts=parse.no_unprotected_lefts,
        no_uncontrolled_crossings=parse.no_uncontrolled_crossings,
        narrowed_all_lefts=parse.narrowed_all_lefts, tradeoff=parse.tradeoff,
        detour=parse.detour, detour_budget_pct=None if pct is None else round(pct, 4),
        departure=parse.departure,
        departure_time=None if departure_at is None
        else departure_at.isoformat(timespec="minutes"),
        unsupported=list(parse.unsupported), has_unsupported=parse.has_unsupported)

    summaries = summarize(routes_out)
    cands = candidates(summaries, c)
    rule = rule_choice(tradeoff, baseline_view(cands))
    if not cands:
        proposal = propose_relaxation(summaries, c)
        resp = PlanResponse(
            status="infeasible", routes=routes_out, summaries=_summaries(summaries, c),
            parse=wire_parse, choice=None, choice_source=None, choice_confidence=None,
            rule_choice=None,
            explanation=explain.explain_infeasible(parse, proposal, relax, departure_at),
            relaxation=Relaxation(constraints=proposal.constraints,  # type: ignore[arg-type]
                                  would_allow=proposal.would_allow),
            timing=m.timing())
        return resp, m

    if len(cands) == 1:
        kind, confidence, source = cands[0].kind, 1.0, "only_candidate"
    else:
        assert jev is not None    # a parse exists, so Jev answered it
        try:
            kind, confidence, reply = await jev_choice(jev, req.request, cands)
            m.jev(reply)
            source = "jev"
        except JevError as exc:
            log.warning("plan: choice failed: %s", exc)
            return _unavailable(routes_out, wire_parse, m), m

    chosen = next(s for s in cands if s.kind == kind)
    resp = PlanResponse(
        status="ok", routes=routes_out, summaries=_summaries(summaries, c),
        parse=wire_parse, choice=kind, choice_source=source,  # type: ignore[arg-type]
        choice_confidence=round(confidence, 3), rule_choice=rule,
        explanation=explain.explain_choice(parse, chosen, summaries, cands, relax,
                                           departure_at),
        relaxation=None, timing=m.timing())
    return resp, m


def _unavailable(routes_out: list[dict[str, Any]], wire_parse: TripParse | None,
                 m: _Meter) -> PlanResponse:
    return PlanResponse(
        status="unavailable", routes=routes_out,
        summaries=_summaries(summarize(routes_out), None), parse=wire_parse,
        choice=None, choice_source=None, choice_confidence=None, rule_choice=None,
        explanation=list(explain.UNAVAILABLE), relaxation=None, timing=m.timing())
