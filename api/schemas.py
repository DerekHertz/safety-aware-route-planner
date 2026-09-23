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
    # Naive = pack-local wall clock (used as-is); aware = an instant, converted
    # into the pack's timezone; omitted = "now" in the pack's timezone. The
    # artifact echoes the resolved naive local time (api/departure.py).
    departure_time: datetime.datetime | None = None
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


class TrafficBasis(BaseModel):
    """What traffic inputs a route was computed against (CONTEXT.md "traffic
    basis"; ADR-0004 schema v2). Minted by `sim.snapshot`, which is where the
    inputs actually come from — see `sim.snapshot.TrafficBasis` for the full
    argument, including why `as_of` currently equals `departure_time` and when
    it will stop doing so.

    Nested rather than three flat `traffic_*` keys on `Preference` so the
    eventual real-feed upgrade is a VALUE change (`source`, `as_of`) at one
    key rather than a reshuffle of the preference object: ADR-0010's "a data
    swap, not an architecture change", held at the contract.
    """
    source: str                      # "synthetic" today (ADR-0010)
    as_of: datetime.datetime         # when those inputs were observed
    profile_version: str             # content hash of the generating profiles


class _PreferenceParams(BaseModel):
    """The four resolved reproducer params, shared by the two preference shapes
    below so they cannot drift apart. Never referenced by a route, so it does
    not appear in the OpenAPI document and needs no `types.ts` mirror.

    `lambda` is a Python keyword, so the field is `lambda_` with a wire alias;
    FastAPI serializes response models by alias, so the JSON/TS key is `lambda`.
    """
    level: str                # "fast" | "balanced" | "safe" (== RouteAlternative.kind)
    lambda_: float = Field(alias="lambda")   # the safety weight that produced it
    detour_budget_pct: float  # RESOLVED (config default when the request omitted it)
    departure_time: datetime.datetime        # the departure basis the route used

    model_config = {"populate_by_name": True}


class Preference(_PreferenceParams):
    """The reproducible description of what a route was optimized for (ADR-0004):
    the human-meaningful safety-level label PLUS the resolved reproducer params.
    A nav consumer replays these to reroute at the SAME safety level (ADR-0002)
    instead of silently falling back to a time-only route.

    This is the OUTPUT shape: every field is required, so a consumer reading an
    artifact never has to null-check the basis. `CarriedPreference` below is
    what the wire accepts back.
    """
    traffic_basis: TrafficBasis              # what traffic it was computed against


class CarriedPreference(_PreferenceParams):
    """A preference arriving back OFF a client, on `RerouteRequest`.

    Identical to `Preference` except that `traffic_basis` is optional, because
    a client mid-drive is holding whatever artifact it was handed — possibly a
    v1 one with no basis at all. The repo's contract rule is that new wire
    shapes must be ADDITIVE, and a newly required field on a request model is
    not additive: it would 422 an in-flight nav session at the first reroute,
    which is precisely the session ADR-0008's reroute exists to keep alive.

    A SIBLING of `Preference`, not a subclass of it: a preference whose basis
    may be missing is not substitutable for one that guarantees it, and mypy
    rejects the widening outright. The shared private base is what keeps the
    four reproducer params single-sourced.

    The field is accepted and then IGNORED. A reroute builds a fresh snapshot
    and its artifact reports THAT snapshot's basis; echoing the carried one
    would label a new artifact with inputs it never used, and the difference
    between the two is the disruption signal a consumer is diffing for
    (ADR-0011).
    """
    traffic_basis: TrafficBasis | None = None


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


class RerouteRequest(BaseModel):
    """Reroute v1 (ADR-0008): replan from the current position to the original
    destination, carrying a prior artifact's `preference` so the replacement
    stays at the SAME safety level. The service recomputes only that one level.
    """
    origin: LatLon                  # the current position, mid-trip
    destination: LatLon             # the original, unchanged destination
    # Carried verbatim off the artifact being followed — which may be a v1
    # artifact with no traffic_basis, hence the relaxed input model.
    preference: CarriedPreference


class RerouteResponse(BaseModel):
    """A reroute yields ONE artifact at the carried level — not the fast/
    balanced/safe list a first plan returns."""
    route: RouteAlternative


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
