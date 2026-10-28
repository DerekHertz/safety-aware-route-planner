"""`/v1/plan` wire shapes, mirrored by hand in `web/lib/types.ts`.

Names are distinct from the route service's on purpose: `scripts/dump_openapi.py`
merges both services' schemas into one document and refuses a name the two
define differently. `PlanPoint` has `LatLon`'s fields and maps onto the same
TypeScript interface.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ConstraintName = Literal["no_unprotected_lefts", "no_uncontrolled_crossings",
                         "detour_budget"]
MAX_REQUEST_CHARS = 500


class PlanPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PlanRequest(BaseModel):
    origin: PlanPoint
    destination: PlanPoint
    # The Request: how, not where. Never stored, never logged.
    request: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)
    # The client's local wall clock, ISO 8601 without an offset. Departure
    # buckets ("weekday morning peak") resolve against it; evals freeze it.
    now_local: str | None = None
    # Constraints the user confirmed downgrading to Tradeoffs (a Relaxation).
    relax: list[ConstraintName] = Field(default_factory=list)


class TripParse(BaseModel):
    """What the Request was read as. Buckets, not free numbers: code maps them."""
    no_unprotected_lefts: bool
    no_uncontrolled_crossings: bool
    narrowed_all_lefts: bool
    tradeoff: int                     # -2 (speed) .. +2 (safety)
    detour: str                       # a bucket: "none", "min_10", "pct_20", "other", ...
    detour_budget_pct: float | None   # what was sent to /route
    departure: str                    # "now", "weekday_am_peak", ..., "other"
    departure_time: str | None        # what was sent to /route (pack-local, naive)
    unsupported: list[str]            # the Request's own clauses, verbatim
    # Anything Unsupported at all: a quoted clause, the whole-Request Noul, or a
    # detour or departure no bucket expresses. The Explanation names each.
    has_unsupported: bool


class RouteSummary(BaseModel):
    kind: str
    minutes: int
    extra_minutes: int
    unprotected_lefts: int
    uncontrolled_crossings: int
    wait_minutes: int
    candidate: bool
    violates: list[ConstraintName]


class Relaxation(BaseModel):
    """Proposed, never applied: resend with `relax` set to confirm it."""
    constraints: list[ConstraintName]
    would_allow: list[str]


class PlanTiming(BaseModel):
    total_ms: float
    jev_ms: float
    route_ms: float
    jev_input_tokens: int
    jev_cost_usd: float


class PlanResponse(BaseModel):
    # ok: a Choice was made. infeasible: no Candidate; see `relaxation`.
    # unavailable: Jev failed or is not configured; the plain comparison.
    status: Literal["ok", "infeasible", "unavailable"]
    # The route service's alternatives, verbatim (`RouteAlternative`).
    routes: list[dict[str, Any]]
    summaries: list[RouteSummary]
    parse: TripParse | None
    choice: str | None
    choice_source: Literal["jev", "only_candidate"] | None
    choice_confidence: float | None
    # The frozen rule baseline's pick among the same Candidates, for evals and
    # blind comparison; the UI does not show it next to the Choice.
    rule_choice: str | None
    explanation: list[str]
    relaxation: Relaxation | None
    timing: PlanTiming


class PlannerHealth(BaseModel):
    status: str
    jev_configured: bool
