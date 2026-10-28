"""Route summaries, the Candidate filter, Relaxation, and the Jev Choice.

Everything here reads only the route service's wire artifact (`/route`'s
`routes[]`), never engine internals (ADR-0019 decision 1). The summaries are
also the only numbers an Explanation may quote, which is what makes it
grounded by construction.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from planner.baseline import CandidateSummary
from planner.jev import Jev, JevError, JevReply

KINDS = ("fast", "balanced", "safe")

# Constraint names: the wire values of `PlanRequest.relax` and `Relaxation`.
NO_LEFTS = "no_unprotected_lefts"
NO_CROSSINGS = "no_uncontrolled_crossings"
DETOUR = "detour_budget"
CONSTRAINT_ORDER = (NO_LEFTS, NO_CROSSINGS, DETOUR)


@dataclass(frozen=True)
class Summary:
    """One alternative as a person reads it. Whole minutes on purpose: the
    Explanation and the Choice state both quote these, and nothing else."""
    kind: str
    eta_s: float
    minutes: int
    extra_minutes: int          # vs the fastest alternative in this response
    detour_pct: float
    unprotected_lefts: int
    uncontrolled_crossings: int
    wait_minutes: int

    @property
    def unsafe_total(self) -> int:
        return self.unprotected_lefts + self.uncontrolled_crossings


def summarize(routes: Sequence[dict[str, Any]]) -> list[Summary]:
    fastest = min((float(r["eta_s"]) for r in routes), default=0.0)
    out = []
    for r in routes:
        eta = float(r["eta_s"])
        out.append(Summary(
            kind=str(r["kind"]),
            eta_s=eta,
            minutes=max(1, round(eta / 60)),
            extra_minutes=round((eta - fastest) / 60),
            detour_pct=float(r.get("detour_pct", 0.0)),
            unprotected_lefts=int(r["unsafe"]["unprotected_left"]),
            uncontrolled_crossings=int(r["unsafe"]["uncontrolled_crossing"]),
            wait_minutes=round(float(r.get("control_delay_s", 0.0)) / 60),
        ))
    order = {k: i for i, k in enumerate(KINDS)}
    return sorted(out, key=lambda s: order.get(s.kind, len(order)))


@dataclass(frozen=True)
class Constraints:
    no_unprotected_lefts: bool = False
    no_uncontrolled_crossings: bool = False
    max_extra_minutes: float | None = None
    max_detour_pct: float | None = None

    def active(self) -> list[str]:
        names = []
        if self.no_unprotected_lefts:
            names.append(NO_LEFTS)
        if self.no_uncontrolled_crossings:
            names.append(NO_CROSSINGS)
        if self.max_extra_minutes is not None or self.max_detour_pct is not None:
            names.append(DETOUR)
        return names

    def without(self, name: str) -> Constraints:
        if name == NO_LEFTS:
            return Constraints(False, self.no_uncontrolled_crossings,
                               self.max_extra_minutes, self.max_detour_pct)
        if name == NO_CROSSINGS:
            return Constraints(self.no_unprotected_lefts, False,
                               self.max_extra_minutes, self.max_detour_pct)
        return Constraints(self.no_unprotected_lefts, self.no_uncontrolled_crossings)

    def violations(self, s: Summary) -> list[str]:
        out = []
        if self.no_unprotected_lefts and s.unprotected_lefts > 0:
            out.append(NO_LEFTS)
        if self.no_uncontrolled_crossings and s.uncontrolled_crossings > 0:
            out.append(NO_CROSSINGS)
        if self.max_extra_minutes is not None and s.extra_minutes > self.max_extra_minutes:
            out.append(DETOUR)
        elif self.max_detour_pct is not None and s.detour_pct > self.max_detour_pct + 1e-9:
            out.append(DETOUR)
        return out


def candidates(summaries: Sequence[Summary], c: Constraints) -> list[Summary]:
    return [s for s in summaries if not c.violations(s)]


@dataclass(frozen=True)
class RelaxationProposal:
    constraints: list[str]
    would_allow: list[str] = field(default_factory=list)


def propose_relaxation(summaries: Sequence[Summary], c: Constraints) -> RelaxationProposal:
    """Called when no alternative satisfies every Constraint. Names the single
    Constraint whose downgrade admits the most alternatives (ties go to the
    earlier one in CONSTRAINT_ORDER); if no single one suffices, all of them.
    Never applied here: the user confirms it (CONTEXT.md "Relaxation")."""
    best: tuple[int, str] | None = None
    for name in c.active():
        n = len(candidates(summaries, c.without(name)))
        if n and (best is None or n > best[0]):
            best = (n, name)
    if best is not None:
        allowed = candidates(summaries, c.without(best[1]))
        return RelaxationProposal([best[1]], [s.kind for s in allowed])
    return RelaxationProposal(c.active(), [s.kind for s in summaries])


def baseline_view(cands: Sequence[Summary]) -> list[CandidateSummary]:
    return [CandidateSummary(s.kind, s.eta_s, s.unsafe_total) for s in cands]


# --- the Jev Choice -----------------------------------------------------------


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def describe(s: Summary) -> str:
    extra = ("the fastest route" if s.extra_minutes <= 0
             else f"{_plural(s.extra_minutes, 'minute', 'minutes')} longer than the fastest")
    lefts = _plural(s.unprotected_lefts, "unprotected left turn", "unprotected left turns")
    crossings = _plural(s.uncontrolled_crossings, "uncontrolled crossing",
                        "uncontrolled crossings")
    return (f"The {s.kind} route: {_plural(s.minutes, 'minute', 'minutes')}, {extra}; "
            f"{lefts} and {crossings} of busy roads; about "
            f"{_plural(s.wait_minutes, 'minute', 'minutes')} waiting at intersections.")


def choice_state(request: str, cands: Sequence[Summary]) -> dict[str, Any]:
    return {"request": request, "routes": [describe(s) for s in cands]}


def choice_questions(cands: Sequence[Summary]) -> dict[str, dict[str, Any]]:
    return {"choice": {
        "type": "choice",
        "instructions": "Which route best fits what the traveller asks for in `request`? "
                        "Unprotected left turns and uncontrolled crossings are the risky "
                        "manoeuvres; weigh the traveller's wishes about safety and time "
                        "against each route's facts in `routes`.",
        "criteria": {s.kind: describe(s) for s in cands},
    }}


async def jev_choice(jev: Jev, request: str,
                     cands: Sequence[Summary]) -> tuple[str, float, JevReply]:
    reply = await jev.ask(choice_state(request, cands), choice_questions(cands))
    kind, confidence = reply.choice("choice")
    if kind not in {s.kind for s in cands}:
        raise JevError(f"choice {kind!r} is not a Candidate")
    return kind, confidence, reply
