"""Shared toys for the commute planner's trip-trace ingest tests (ADR-0018).

Everything here is hand-computable: a fixed epoch, a hand-cranked clock, and
fixes that march north at 10 m/s from a point chosen so its digits are
distinctive enough to grep for in logs and database files.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient

# 2026-09-21T19:33:20Z. Any fixed instant inside the accepted range will do.
T0_MS = 1_790_000_000_000
DAY_MS = 86_400_000

# Distinctive digits, so "did a coordinate leak?" is a substring search.
LAT0 = 37.8712345
LON0 = -122.2687654
_DLAT_PER_S = 0.0000898   # ~10 m of latitude


class FakeClock:
    """Epoch-milliseconds clock that only moves when a test moves it."""

    def __init__(self, now_ms: int = T0_MS):
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance_ms(self, ms: int) -> None:
        self.now_ms += ms

    def advance_days(self, days: int) -> None:
        self.now_ms += days * DAY_MS


def fix(t: int, lat: float = LAT0, lon: float = LON0, **overrides) -> dict:
    out = {"t": t, "lat": lat, "lon": lon, "speed_mps": 10.0,
           "accuracy_m": 5.0, "heading_deg": 0.0}
    out.update(overrides)
    return out


def fixes(n: int, t0: int = T0_MS) -> list[dict]:
    """`n` fixes at 1 Hz, heading north from (LAT0, LON0)."""
    return [fix(t0 + 1000 * i, lat=round(LAT0 + _DLAT_PER_S * i, 7))
            for i in range(n)]


def artifact(eta_s: float = 600.0) -> dict:
    """A small route-artifact-shaped object. The server stores it opaquely, so
    only its JSON-ness matters; the shape just keeps the fixtures honest."""
    return {
        "kind": "safe",
        "geometry": {"type": "LineString",
                     "coordinates": [[LON0, LAT0], [LON0, LAT0 + 0.01]]},
        "distance_m": 1113.2,
        "eta_s": eta_s,
        "preference": {"level": "safe", "lambda": 2.0,
                       "detour_budget_pct": 0.25,
                       "departure_time": "2026-09-21T12:33:20",
                       "traffic_basis": {"source": "synthetic",
                                         "as_of": "2026-09-21T12:33:20",
                                         "profile_version": "abc123"}},
        "schema_version": 2,
    }


def chunk(n: int = 3, t0: int = T0_MS, with_artifact: bool = False) -> dict:
    body: dict = {"fixes": fixes(n, t0)}
    if with_artifact:
        body["artifacts"] = [{"effective_at": t0, "artifact": artifact()}]
    return body


def trip_end(ended_at: int = T0_MS + 600_000, arrived: bool = True,
             effective_at: int = T0_MS, eta_s: float = 540.0) -> dict:
    return {
        "ended_at": ended_at,
        "arrived": arrived,
        "prediction": {"effective_at": effective_at, "eta_s": eta_s,
                       "level": "safe", "profile_version": "abc123"},
    }


def new_trip_id() -> str:
    return str(uuid.uuid4())


def chunk_url(trip_id: str, seq: int) -> str:
    return f"/v1/trips/{trip_id}/chunks/{seq}"


def end_url(trip_id: str) -> str:
    return f"/v1/trips/{trip_id}/end"


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@contextmanager
def commute_client(db_path: Path, clock: FakeClock) -> Iterator[TestClient]:
    """The real commute app over a tmp_path database, lifespan and all."""
    from commute.app import create_app
    with TestClient(create_app(db_path=db_path, clock=clock)) as client:
        yield client
