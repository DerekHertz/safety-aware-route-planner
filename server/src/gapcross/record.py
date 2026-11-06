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


if __name__ == "__main__":
    main()
