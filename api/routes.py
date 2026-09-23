"""POST /route — the core endpoint — and POST /reroute (ADR-0008)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from api.departure import resolve_departure
from api.ratelimit import LimiterUnavailable, client_key, retry_after_header
from api.schemas import (
    RerouteRequest,
    RerouteResponse,
    RouteRequest,
    RouteResponse,
)
from pyref.engine import RoutingError

router = APIRouter()


async def enforce_route_quota(request: Request) -> None:
    """Spend one token from the caller's routing bucket, or refuse with 429.

    A dependency rather than a line in each handler, and `async` rather than
    `def`, because both handlers are sync: FastAPI runs an async dependency on
    the event loop and only then hands the handler to the threadpool, so the
    token is taken before a worker thread — and 3-8 graph searches — is
    committed to. A refusal costs no search, which is the entire point.

    Both endpoints share one bucket and are charged one token each. They spend
    the same resource, so two ceilings would mean the real ceiling is their sum
    and nobody would have written it down. A flat token does over-charge
    `/reroute` (one search against `/route`'s 3-8) and that is absorbed by
    sizing the quota for the union of both call patterns; see config.toml,
    where the numbers are argued from the front-end's actual behaviour. Live
    navigation is the case that must not break: the client self-limits reroutes
    to roughly one per 18 s, which is two orders of magnitude inside the quota.

    **This fails OPEN, and it is the opposite of `GET /geocode`'s answer on
    purpose.** ADR-0013 fails closed because that ceiling enforces a third
    party's usage policy and the penalty for breaching it is a ban on this
    project's User-Agent — unrecoverable on our own timescale. This ceiling
    protects nothing but our own CPU. Failing closed would convert a Redis
    blip into a 503 on the product's core endpoint, on every replica at once,
    which is the product being down; failing open converts the same blip into
    unthrottled routing for its duration, which is a load spike that the
    process survives and that a graph shows afterwards. Given a choice between
    an outage and a load problem, take the load problem.
    """
    state = request.app.state.app_state
    key = client_key(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        state.trusted_proxies,
    )
    try:
        retry_after = await state.route_limiter.acquire(key)
    except LimiterUnavailable:
        return
    if retry_after is not None:
        raise HTTPException(
            429, "too many routing requests",
            headers={"Retry-After": retry_after_header(retry_after)})


def _artifact(r) -> dict:
    """The wire shape of one route artifact (ADR-0004). RouteOut carries the
    nested pieces as plain dicts; pydantic coerces them at the boundary."""
    return {
        "kind": r.kind,
        "geometry": r.geometry,
        "distance_m": r.distance_m,
        "eta_s": r.eta_s,
        "unsafe": r.unsafe,
        "segments": r.segments,
        "unsafe_points": r.unsafe_points,
        "maneuvers": r.maneuvers,
        "detour_pct": r.detour_pct,
        "preference": r.preference,
        "schema_version": r.schema_version,
    }


@router.post("/route", response_model=RouteResponse,
             dependencies=[Depends(enforce_route_quota)])
def route(request: Request, body: RouteRequest) -> RouteResponse:
    # sync endpoint on purpose: FastAPI runs it in a worker thread, keeping
    # the event loop free while the (CPU-bound) search runs
    state = request.app.state.app_state
    # Pack-local wall clock (#63): naive passes through, aware is converted,
    # omitted is "now" in the pack's zone — never the server's UTC clock.
    departure = resolve_departure(body.departure_time, state.pack_tz)
    try:
        routes = state.router.route(
            body.origin.lat, body.origin.lon,
            body.destination.lat, body.destination.lon,
            departure=departure,
            safety_enabled=body.safety_enabled,
            detour_budget_pct=body.detour_budget_pct,
        )
    except RoutingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # model_validate, not the RouteResponse(...) constructor: RouteOut carries
    # `unsafe`/`segments`/`unsafe_points` as plain dicts, and pydantic coerces
    # them into the nested models here. Identical result, but it types the
    # loose input honestly instead of claiming these are already models.
    return RouteResponse.model_validate({"routes": [_artifact(r) for r in routes]})


@router.post("/reroute", response_model=RerouteResponse, response_model_by_alias=True,
             dependencies=[Depends(enforce_route_quota)])
def reroute(request: Request, body: RerouteRequest) -> RerouteResponse:
    # sync on purpose, like /route: FastAPI runs it in a worker thread so the
    # CPU-bound search never blocks the event loop.
    state = request.app.state.app_state
    pref = body.preference
    # pref.traffic_basis is deliberately NOT read. It describes the artifact
    # the client is currently following; this call builds a fresh snapshot and
    # the artifact it returns reports THAT basis. It may also be absent
    # entirely — a client mid-drive can be holding a v1 artifact (ADR-0004 v2;
    # see CarriedPreference in api/schemas.py).
    try:
        art = state.router.reroute(
            body.origin.lat, body.origin.lon,
            body.destination.lat, body.destination.lon,
            level=pref.level,
            lam=pref.lambda_,
            detour_budget_pct=pref.detour_budget_pct,
            departure=resolve_departure(pref.departure_time, state.pack_tz),
        )
    except RoutingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RerouteResponse.model_validate({"route": _artifact(art)})
