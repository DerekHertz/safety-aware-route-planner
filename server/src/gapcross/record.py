"""Record fixtures from live sources: `uv run python -m gapcross.record`.

Records, for every trip: the provider's own route, the example route, and the record snapshot of
its bbox; plus every waypoint list in fixtures/extra_routes.json. Waits between requests to stay
within public servers' light-use limits.
"""

import json
import time

from .sources import live_sources
from .transport import FIXTURE_DIR
from .trips import load_trips
from .types import LatLon

PAUSE_S = 1.5


def main() -> None:
    sources = live_sources(record=True)
    extra = json.loads((FIXTURE_DIR / "extra_routes.json").read_text())["routes"]
    for trip in load_trips().values():
        for waypoints in ([trip.origin, trip.destination], [trip.origin, *trip.example_via, trip.destination]):
            route = sources.routes.route(waypoints)
            print(f"{trip.id}: {len(waypoints)} waypoints, {route.duration_s:.0f} s")
            time.sleep(PAUSE_S)
        snap = sources.records.snapshot(trip.bbox)
        print(f"{trip.id}: snapshot {len(snap.nodes)} nodes, {len(snap.ways)} ways, taken {snap.taken_at}")
    for item in extra:
        time.sleep(PAUSE_S)
        route = sources.routes.route([LatLon(**p) for p in item["waypoints"]])
        print(f"{item['name']}: {route.duration_s:.0f} s")


def record_search(osrm_url: str) -> None:
    """Record every candidate route the search asks for, against the recorded snapshot.

    `uv run python -m gapcross.record --search http://localhost:5000` (the self-hosted OSRM from
    S6; fixtures are keyed by request meaning, so they answer for any OSRM). No deadline applies.
    """
    import math

    from .assess import FixedAdvisor
    from .compare import compare
    from .sources import OsrmRouteSource, OverpassRecordSource, Sources
    from .transport import FixtureTransport, RecordingTransport

    sources = Sources(OsrmRouteSource(RecordingTransport(), osrm_url), OverpassRecordSource(FixtureTransport()))
    trips = load_trips()
    for trip in trips.values():
        result = compare(trip, sources, FixedAdvisor(), deadline_s=math.inf)
        print(f"{trip.id}: {result.outcome}, alternative eta {result.alternative.eta_s if result.alternative else '-'}")
    extra = json.loads((FIXTURE_DIR / "extra_routes.json").read_text())
    for item in extra.get("reroutes", ()):
        trip = trips[item["trip"]]
        route = sources.routes.route([LatLon(**item["position"]), trip.destination])
        print(f"{item['name']}: {route.duration_s:.0f} s")


if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "--search":
        record_search(sys.argv[2])
    else:
        main()
