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


class RouteAlternative(BaseModel):
    kind: str                 # "fast" | "balanced" | "safe"
    geometry: dict            # GeoJSON LineString
    distance_m: float
    eta_s: float
    unsafe: UnsafeCounts
    segments: list[Segment]
    unsafe_points: list[UnsafePoint]


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
