"""Preset trips. The example route is forced through `example_via`; alternatives are never listed."""

import json
from dataclasses import dataclass
from pathlib import Path

from .sources import BBox
from .types import LatLon, Trip

TRIPS_PATH = Path(__file__).resolve().parents[2] / "trips.json"


@dataclass(frozen=True)
class TripPlan:
    id: str
    name: str
    origin: LatLon
    destination: LatLon
    example_via: tuple[LatLon, ...]
    bbox: BBox

    def wire(self) -> Trip:
        return Trip(id=self.id, name=self.name, origin=self.origin, destination=self.destination)


def load_trips(path: Path = TRIPS_PATH) -> dict[str, TripPlan]:
    plans = {}
    for t in json.loads(path.read_text())["trips"]:
        plans[t["id"]] = TripPlan(
            id=t["id"],
            name=t["name"],
            origin=LatLon(**t["origin"]),
            destination=LatLon(**t["destination"]),
            example_via=tuple(LatLon(**p) for p in t["example_via"]),
            bbox=BBox(**t["bbox"]),
        )
    return plans
