"""Measure live-source latency: `GAPCROSS_OSRM_URL=http://localhost:5000 uv run python -m gapcross.latency`.

Times OSRM calls (provider route, example route, candidate-like via routes) and one Overpass
snapshot of the trip bbox. Run against the self-hosted OSRM; one public Overpass query only.
Nothing is recorded.
"""

import statistics
import sys
import time

from .sources import live_sources
from .trips import load_trips
from .types import LatLon

REPEATS = 5


def _via_points(trip) -> list[LatLon]:
    """A dozen points spread around the corridor, as candidate crossings would be."""
    b = trip.bbox
    pts = []
    for i in range(4):
        for j in range(3):
            lat = b.south + (b.north - b.south) * (0.2 + 0.2 * i)
            lon = b.west + (b.east - b.west) * (0.25 + 0.25 * j)
            pts.append(LatLon(lat=lat, lon=lon))
    return pts


def _stats(name: str, times: list[float]) -> None:
    times = sorted(times)
    p95 = times[min(len(times) - 1, int(round(0.95 * (len(times) - 1))))]
    print(f"{name:28s} n={len(times):3d} p50={statistics.median(times)*1000:7.1f} ms  p95={p95*1000:7.1f} ms  max={times[-1]*1000:7.1f} ms")


def _time(fn) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


def main() -> None:
    sources = live_sources()
    print("osrm:", sources.routes.base_url, " overpass:", sources.records.url)
    trip = next(iter(load_trips().values()))
    sources.routes.route([trip.origin, trip.destination])  # warm up
    ends = [trip.origin, trip.destination]
    _stats("provider route", [_time(lambda: sources.routes.route(ends)) for _ in range(REPEATS * 2)])
    via_example = [trip.origin, *trip.example_via, trip.destination]
    _stats("example route", [_time(lambda: sources.routes.route(via_example)) for _ in range(REPEATS * 2)])
    cand = []
    for p in _via_points(trip):
        try:
            cand.append(_time(lambda: sources.routes.route([trip.origin, p, trip.destination])))
        except Exception as error:  # an unroutable via point is not a latency sample
            print("skip", p, error, file=sys.stderr)
    _stats("candidate-like via routes", cand)
    if "--no-overpass" not in sys.argv:
        t = _time(lambda: sources.records.snapshot(trip.bbox))
        print(f"overpass snapshot (trip bbox, 1 query): {t*1000:.0f} ms")
    if "--compare" in sys.argv:
        _compare(trip, sources)


def _compare(trip, live) -> None:
    """compare() as the service runs it: live OSRM, the corridor snapshot already held (prefetched
    at startup), no Jev answers. Uses the recorded snapshot when --no-overpass is given."""
    from .app import CorridorRecords
    from .assess import FixedAdvisor
    from .compare import compare
    from .sources import Sources, offline_sources

    records = CorridorRecords(offline_sources().records if "--no-overpass" in sys.argv else live.records)
    records.snapshot(trip.bbox)
    sources = Sources(live.routes, records)
    outcomes = []
    times = []
    for _ in range(REPEATS):
        t = time.perf_counter()
        outcomes.append(compare(trip, sources, FixedAdvisor(), source="osrm").outcome)
        times.append(time.perf_counter() - t)
    _stats("compare() end to end", times)
    print("outcomes:", sorted(set(outcomes)))


if __name__ == "__main__":
    main()
