"""The Trip planner's HTTP surface (ADR-0019).

    uvicorn planner.app:app --port 8200

    GET  /health     liveness, and whether Jev is configured; no token
    POST /v1/plan    a Request for a Trip -> routes, Choice, Explanation

Configuration, all from the environment:

    TYPESAFE_API_KEY            Jev's key. Unset: every plan is "unavailable",
                                the plain comparison, which is the failure path.
    SR_PLANNER_ROUTE_URL        the route service (default http://localhost:8000)
    SR_PLANNER_REQUIRE_TOKEN    "0" to skip tester tokens in local dev; on by default
    SR_COMMUTE_DB               where the tester tokens live (shared with commute/)
    SR_PLANNER_DAILY_USD        the daily Jev spend cap; past it, 503 (default 1.0)
    SR_PLANNER_JEV_TIMEOUT_S    per Jev call (default 2.5)
    SR_PLANNER_CORS_ORIGINS     comma-separated (default http://localhost:3000)

**No Request text is stored or logged.** Logs hold latency, token counts and
parse-category counts only.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from commute import tokens
from commute.app import describe_errors
from commute.store import TraceStore, resolve_db_path
from planner.jev import HttpJev, Jev
from planner.pipeline import plan
from planner.routes import HttpRouteClient, RouteClient, RouteServiceError
from planner.schemas import PlannerHealth, PlanRequest, PlanResponse
from planner.spend import SpendMeter

log = logging.getLogger("planner")

_bearer = HTTPBearer(auto_error=False,
                     description="A tester token, minted by `python -m commute.tokens issue`.")


def current_tester(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> tokens.Tester | None:
    store: TraceStore | None = request.app.state.token_store
    if store is None:
        return None
    if credentials is None:
        raise HTTPException(401, "missing bearer tester token",
                            headers={"WWW-Authenticate": "Bearer"})
    tester = tokens.authenticate(store, credentials.credentials)
    if tester is None:
        raise HTTPException(401, "unknown or revoked tester token",
                            headers={"WWW-Authenticate": "Bearer"})
    return tester


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    """`{"detail": str}` naming the field, never echoing its value: for this
    service the value can be the Request text."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    return JSONResponse({"detail": describe_errors(errors) or "invalid request"},
                        status_code=422)


def _cors_origins() -> list[str]:
    env = os.environ.get("SR_PLANNER_CORS_ORIGINS")
    if env:
        return [o.strip() for o in env.split(",") if o.strip()]
    return ["http://localhost:3000"]


def create_app(jev: Jev | None = None, routes: RouteClient | None = None,
               meter: SpendMeter | None = None, require_token: bool | None = None,
               db_path: str | os.PathLike[str] | None = None,
               jev_from_env: bool = True) -> FastAPI:
    """Builds the app without touching the network or disk. Tests pass a
    `ReplayJev` and a fake `RouteClient`."""
    if jev is None and jev_from_env:
        jev = HttpJev.from_env()
    if require_token is None:
        require_token = os.environ.get("SR_PLANNER_REQUIRE_TOKEN", "1") != "0"
    token_store = TraceStore(resolve_db_path(db_path)) if require_token else None
    route_client = routes or HttpRouteClient()
    spend = meter or SpendMeter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if token_store is not None:
            token_store.init_schema()
        yield

    app = FastAPI(title="Trip planner", version="1", lifespan=lifespan)
    app.state.token_store = token_store
    app.add_exception_handler(RequestValidationError, _validation_error)

    @app.get("/health", response_model=PlannerHealth)
    def health() -> PlannerHealth:
        return PlannerHealth(status="ok", jev_configured=jev is not None)

    @app.post("/v1/plan", response_model=PlanResponse, responses={
        401: {"description": "Missing, unknown or revoked tester token."},
        422: {"description": "Invalid body, or the route service refused the Trip."},
        502: {"description": "The route service failed."},
        503: {"description": "The daily Jev spend cap is reached."}})
    async def plan_trip(body: PlanRequest, request: Request,
                        tester: Annotated[Any, Depends(current_tester)]) -> PlanResponse:
        if spend.exhausted():
            raise HTTPException(503, "the Trip planner's daily budget is spent; "
                                     "try again tomorrow")
        peer = request.client.host if request.client else None
        try:
            resp, m = await plan(body, jev, route_client, forwarded_for=peer)
        except RouteServiceError as exc:
            raise HTTPException(exc.status, exc.detail) from exc
        spend.add(m.cost)
        p = resp.parse
        log.info("plan status=%s total_ms=%.0f jev_ms=%.0f route_ms=%.0f tokens=%d "
                 "lefts=%s crossings=%s tradeoff=%s detour=%s departure=%s "
                 "unsupported=%d relax=%d choice=%s",
                 resp.status, resp.timing.total_ms, m.jev_ms, m.route_ms, m.tokens,
                 p and p.no_unprotected_lefts, p and p.no_uncontrolled_crossings,
                 p and p.tradeoff, p and p.detour, p and p.departure,
                 len(p.unsupported) if p else 0, len(body.relax), resp.choice)
        return resp

    app.add_middleware(
        CORSMiddleware, allow_origins=_cors_origins(),
        allow_origin_regex=os.environ.get("SR_PLANNER_CORS_ORIGIN_REGEX") or None,
        allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
    return app


app = create_app()
