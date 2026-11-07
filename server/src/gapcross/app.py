"""HTTP surface of the service.

Configuration (environment):
- GAPCROSS_SOURCES: `recorded` (default; fixtures, labeled recorded) or `live`.
- GAPCROSS_OSRM_URL, GAPCROSS_OVERPASS_URL: live endpoints (default: the public servers).
- GAPCROSS_REPORTS: the drive-report SQLite file (default server/var/drive_reports.sqlite).

The record snapshot is fetched once per trip corridor and kept for the life of the process, so
Overpass stays off the request path after the first request. A failed fetch is not kept.
"""

import os
import sqlite3
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response, status

from .assess import Advisor, FixedAdvisor, assess_route
from .compare import _route, compare, reroute
from .jev import API_KEY_ENV, CACHE_PATH, AnswerCache, JevAdvisor, live_jev
from .sources import (
    BBox,
    RecordSnapshot,
    RecordSource,
    Sources,
    SourceUnavailable,
    live_sources,
    offline_sources,
)
from .trips import TripPlan, load_trips
from .types import (
    Comparison,
    ComparisonRequest,
    DriveReport,
    Problem,
    RerouteRequest,
    Route,
    Trip,
)

REPORTS_PATH = Path(__file__).resolve().parents[2] / "var" / "drive_reports.sqlite"
ROUTE_UNAVAILABLE = "Route couldn't be obtained."
RESPONSES = {
    404: {"model": Problem, "description": "No such trip."},
    503: {"model": Problem, "description": ROUTE_UNAVAILABLE},
}


class CorridorRecords:
    """The record source behind one snapshot per bbox, kept once fetched."""

    def __init__(self, inner: RecordSource):
        self.inner = inner
        self._kept: dict[BBox, RecordSnapshot] = {}
        self._lock = threading.Lock()

    def snapshot(self, bbox: BBox) -> RecordSnapshot:
        with self._lock:
            if bbox not in self._kept:
                self._kept[bbox] = self.inner.snapshot(bbox)
            return self._kept[bbox]


@dataclass
class Service:
    sources: Sources
    advisor: Advisor
    source: Literal["osrm", "recorded"]
    reports_path: Path = REPORTS_PATH
    trips: dict[str, TripPlan] = field(default_factory=load_trips)

    def __post_init__(self) -> None:
        self.sources = Sources(self.sources.routes, CorridorRecords(self.sources.records))

    def prefetch(self) -> None:
        """Fetch every corridor's snapshot so Overpass is off the request path. Failures are
        left for the first request to meet (it then reports check_failed)."""
        for trip in self.trips.values():
            try:
                self.sources.records.snapshot(trip.bbox)
            except SourceUnavailable:
                pass

    def trip(self, trip_id: str) -> TripPlan:
        if trip_id not in self.trips:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such trip.")
        return self.trips[trip_id]

    def default(self, trip: TripPlan) -> Route:
        raw = self.sources.routes.route([trip.origin, *trip.example_via, trip.destination])
        assessed = assess_route(raw, self.sources.records, trip.bbox, self.advisor)
        checked = assessed.status != "check_failed"
        return _route(raw, assessed.crossings if checked else (), "example", self.source, checked=checked)

    def store_report(self, report: DriveReport) -> None:
        self.reports_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.reports_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS drive_reports (id INTEGER PRIMARY KEY, trip_id TEXT, "
                       "route_label TEXT, depart_at TEXT, received_json TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS drive_report_crossings (report_id INTEGER, trip_id TEXT, "
                       "route_label TEXT, crossing_key TEXT, answer TEXT)")
            cur = db.execute("INSERT INTO drive_reports (trip_id, route_label, depart_at, received_json) "
                             "VALUES (?, ?, ?, ?)",
                             (report.trip_id, report.route_label, report.depart_at.isoformat(),
                              report.model_dump_json()))
            db.executemany("INSERT INTO drive_report_crossings VALUES (?, ?, ?, ?, ?)",
                           [(cur.lastrowid, report.trip_id, report.route_label, c.key, c.answer)
                            for c in report.crossings])


def service_from_env() -> Service:
    advisor: Advisor = FixedAdvisor()  # recorded mode: no Jev answers, open questions stay unknown
    if os.environ.get("GAPCROSS_SOURCES", "recorded") == "live":
        sources = live_sources()  # GAPCROSS_OSRM_URL / GAPCROSS_OVERPASS_URL, else the public servers
        source: Literal["osrm", "recorded"] = "osrm"
        if os.environ.get(API_KEY_ENV):
            # the cache the corridor pre-run filled; misses go to Jev, one request per route
            advisor = JevAdvisor(live_jev(), AnswerCache(os.environ.get("GAPCROSS_JEV_CACHE", CACHE_PATH)))
    else:
        sources, source = offline_sources(), "recorded"
    reports = Path(os.environ.get("GAPCROSS_REPORTS", REPORTS_PATH))
    return Service(sources=sources, advisor=advisor, source=source, reports_path=reports)


def create_app(service: Service | None = None) -> FastAPI:
    state: dict[str, Service] = {}
    lock = threading.Lock()

    def svc() -> Service:
        with lock:
            if "s" not in state:
                state["s"] = service or service_from_env()
            return state["s"]

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        threading.Thread(target=lambda: svc().prefetch(), daemon=True).start()
        yield

    app = FastAPI(title="Gap-crossing route comparison", version="0.1.0", lifespan=lifespan)

    @app.get("/trips", response_model=list[Trip])
    def list_trips() -> list[Trip]:
        return [t.wire() for t in svc().trips.values()]

    @app.post("/trips/{trip_id}/default", response_model=Route, responses=RESPONSES)
    def default_route(trip_id: str) -> Route:
        s = svc()
        trip = s.trip(trip_id)
        try:
            return s.default(trip)
        except SourceUnavailable:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ROUTE_UNAVAILABLE) from None

    @app.post("/trips/{trip_id}/comparison", response_model=Comparison,
              responses={**RESPONSES, 422: {"model": Problem, "description": "Avoidance is off: call /default."}})
    def comparison(trip_id: str, request: ComparisonRequest) -> Comparison:
        s = svc()
        trip = s.trip(trip_id)
        if not request.avoid:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Avoidance is off: call /default.")
        try:
            return compare(trip, s.sources, s.advisor, source=s.source)
        except SourceUnavailable:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ROUTE_UNAVAILABLE) from None

    @app.post("/trips/{trip_id}/reroute", response_model=Route, responses=RESPONSES)
    def reroute_route(trip_id: str, request: RerouteRequest) -> Route:
        s = svc()
        trip = s.trip(trip_id)
        try:
            return reroute(request.position, trip, s.sources, s.source)
        except SourceUnavailable:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ROUTE_UNAVAILABLE) from None

    @app.post("/drive-reports", status_code=status.HTTP_204_NO_CONTENT,
              responses={404: {"model": Problem, "description": "No such trip."}})
    def drive_report(report: DriveReport) -> Response:
        s = svc()
        s.trip(report.trip_id)
        s.store_report(report)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app


app = create_app()
