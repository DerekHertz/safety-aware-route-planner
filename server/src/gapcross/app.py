"""HTTP surface of the service. Handlers are stubs until S5."""

from fastapi import FastAPI, HTTPException, status

from .types import (
    Comparison,
    ComparisonRequest,
    DriveReport,
    Problem,
    RerouteRequest,
    Route,
    Trip,
)

app = FastAPI(title="Gap-crossing route comparison", version="0.1.0")

ROUTE_UNAVAILABLE = {503: {"model": Problem, "description": "Route couldn't be obtained."}}


def _not_built() -> HTTPException:
    return HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Not built yet.")


@app.get("/trips", response_model=list[Trip])
def list_trips() -> list[Trip]:
    raise _not_built()


@app.post("/trips/{trip_id}/default", response_model=Route, responses=ROUTE_UNAVAILABLE)
def default_route(trip_id: str) -> Route:
    raise _not_built()


@app.post("/trips/{trip_id}/comparison", response_model=Comparison, responses=ROUTE_UNAVAILABLE)
def comparison(trip_id: str, request: ComparisonRequest) -> Comparison:
    raise _not_built()


@app.post("/trips/{trip_id}/reroute", response_model=Route, responses=ROUTE_UNAVAILABLE)
def reroute(trip_id: str, request: RerouteRequest) -> Route:
    raise _not_built()


@app.post("/drive-reports", status_code=status.HTTP_204_NO_CONTENT)
def drive_report(report: DriveReport) -> None:
    raise _not_built()
