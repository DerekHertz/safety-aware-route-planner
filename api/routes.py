"""POST /route — the core endpoint."""
from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException, Request

from api.schemas import RouteRequest, RouteResponse
from pyref.engine import RoutingError

router = APIRouter()


@router.post("/route", response_model=RouteResponse)
def route(request: Request, body: RouteRequest) -> RouteResponse:
    # sync endpoint on purpose: FastAPI runs it in a worker thread, keeping
    # the event loop free while the (CPU-bound) search runs
    state = request.app.state.app_state
    departure = body.departure_time or datetime.datetime.now()
    try:
        routes = state.router.route(
            body.origin.lat, body.origin.lon,
            body.destination.lat, body.destination.lon,
            departure=departure,
            safety_enabled=body.safety_enabled,
        )
    except RoutingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return RouteResponse(routes=[
        {
            "kind": r.kind,
            "geometry": r.geometry,
            "distance_m": r.distance_m,
            "eta_s": r.eta_s,
            "unsafe": r.unsafe,
            "segments": r.segments,
            "unsafe_points": r.unsafe_points,
        }
        for r in routes
    ])
