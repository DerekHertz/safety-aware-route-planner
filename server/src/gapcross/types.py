"""Wire types shared by the service and the iOS app. Terms follow CONTEXT.md.

Distances are meters and times are seconds on the wire; the app converts to miles and feet.
Flag and unknown counts are never sent: the app derives them from `crossings`.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LatLon(Wire):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Trip(Wire):
    id: str
    name: str
    origin: LatLon
    destination: LatLon


class Maneuver(Wire):
    type: str
    modifier: str | None = None


class Step(Wire):
    instruction: str
    maneuver: Maneuver
    street: str
    distance_m: float = Field(ge=0)
    along_m: float = Field(ge=0, description="Distance from the route start to this step.")
    location: LatLon


class Evidence(Wire):
    source: Literal["osm", "jev", "city_record"]
    ref: str
    claim: str
    observed_on: date | None = None
    retrieved_at: datetime
    is_lead: bool = Field(description="A lead is shown but never changes a status.")


class Assessment(Wire):
    status: Literal["flagged", "unflagged", "unknown", "check_failed"]
    explanation: str
    evidence: list[Evidence]


class Crossing(Wire):
    key: str = Field(description="{sorted junction OSM node ids}/{kind}/{approach octant}")
    main_street: str
    kind: Literal["straight", "left_onto"]
    approach_bearing: float = Field(ge=0, lt=360)
    along_m: float = Field(ge=0)
    location: LatLon
    assessment: Assessment


class Route(Wire):
    label: Literal["provider", "example", "rerouted"]
    source: Literal["osrm", "recorded"] = Field(description="Where the route data came from, shown to the driver.")
    geometry: list[LatLon]
    distance_m: float = Field(ge=0)
    eta_s: float = Field(ge=0)
    steps: list[Step]
    crossings: list[Crossing] = Field(description="Empty on a rerouted route, which is not assessed.")


class Comparison(Wire):
    default: Route
    alternative: Route | None = None
    outcome: Literal["improvement", "no_improvement", "over_budget", "incomplete", "check_failed"]
    search_complete: bool
    budget_s: float = Field(ge=0)
    added_s: float | None = None
    summary: str


class ComparisonRequest(Wire):
    avoid: bool


class RerouteRequest(Wire):
    position: LatLon


class CrossingReport(Wire):
    key: str
    answer: Literal["as_described", "not_as_described", "not_sure"]


class DriveReport(Wire):
    trip_id: str
    route_label: Literal["provider", "example", "rerouted"]
    depart_at: datetime
    crossings: list[CrossingReport]


class Problem(Wire):
    detail: str
