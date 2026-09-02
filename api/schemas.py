"""Pydantic models — the FROZEN /route contract (a future React Native /
PWA client reuses it unchanged; `unsafe_points` is the one contract-additive
extension, used for map markers).

Kept in sync by hand with web/lib/types.ts.
"""
from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class LatLon(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class RouteRequest(BaseModel):
    origin: LatLon
    destination: LatLon
    departure_time: datetime.datetime | None = None  # default: server "now"
    safety_enabled: bool = True
    # How much longer the "safe" route may be in exchange for avoiding every
    # counted unsafe maneuver outright. None = the config default; 0 disables
    # the hard avoid and leaves the lambda sweep in charge.
    detour_budget_pct: float | None = Field(default=None, ge=0.0, le=2.0)


class UnsafeCounts(BaseModel):
    unprotected_left: int
    uncontrolled_crossing: int
    total: int


class Segment(BaseModel):
    geometry: dict            # GeoJSON LineString
    tier: str                 # "safe" | "caution" | "unsafe"


class UnsafePoint(BaseModel):
    lon: float
    lat: float
    type: str                 # "unprotected_left" | "uncontrolled_crossing"


class Maneuver(BaseModel):
    type: str                 # "left" | "right" | "uturn"
    angle_deg: float
    offset_m: float
    lon: float
    lat: float


class Preference(BaseModel):
    """The reproducible description of what a route was optimized for (ADR-0004):
    the human-meaningful safety-level label PLUS the resolved reproducer params.
    A nav consumer replays these to reroute at the SAME safety level (ADR-0002)
    instead of silently falling back to a time-only route.

    `lambda` is a Python keyword, so the field is `lambda_` with a wire alias;
    FastAPI serializes response models by alias, so the JSON/TS key is `lambda`.
    """
    level: str                # "fast" | "balanced" | "safe" (== RouteAlternative.kind)
    lambda_: float = Field(alias="lambda")   # the safety weight that produced it
    detour_budget_pct: float  # RESOLVED (config default when the request omitted it)
    departure_time: datetime.datetime        # the departure basis the route used

    model_config = {"populate_by_name": True}


class RouteAlternative(BaseModel):
    kind: str                 # "fast" | "balanced" | "safe"
    geometry: dict            # GeoJSON LineString
    distance_m: float
    eta_s: float
    unsafe: UnsafeCounts
    segments: list[Segment]
    unsafe_points: list[UnsafePoint]
    maneuvers: list[Maneuver]
    detour_pct: float         # extra time vs the fastest route in this response
    preference: Preference    # how to reproduce/reroute this route (ADR-0004)
    schema_version: int       # artifact contract version; bumped on breaking change


class RouteResponse(BaseModel):
    routes: list[RouteAlternative]


class MetaResponse(BaseModel):
    """Pack coverage info. Additive — not part of the frozen /route contract."""
    region: str
    bbox: list[float] | None      # [west, south, east, north]; None for toy packs
    num_edges: int


class GeocodeResult(BaseModel):
    name: str
    lat: float
    lon: float


class GeocodeResponse(BaseModel):
    results: list[GeocodeResult]
