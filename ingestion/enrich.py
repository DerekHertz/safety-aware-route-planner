"""OSM tag parsing and defaulting for edge attributes.

All defaults come from config ([defaults] tables). Every function is pure so
it can be unit-tested without OSMnx.
"""
from __future__ import annotations

import re
from typing import Any

from pyref.graph import ROAD_CLASS_RANK, RoadClass

_MPH_TO_KPH = 1.609344
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)")

# OSM `highway` values seen on drivable ways that we fold into our enum.
_HIGHWAY_ALIASES = {
    "road": "unclassified",
    "busway": "unclassified",
    "living_street": "living_street",
}


def road_class_from_highway(value: Any) -> RoadClass:
    """Map an OSM `highway` tag (possibly a list) to our RoadClass enum.

    Unknown values fall back to `unclassified` — the drive filter has already
    excluded footways etc., so anything unknown is a drivable oddity.
    """
    if isinstance(value, (list, tuple)):
        # Simplified edges can merge ways of different classes; keep the most
        # major one (lowest rank) so safety scoring errs on the busy side.
        classes = [road_class_from_highway(v) for v in value]
        return min(classes, key=lambda c: ROAD_CLASS_RANK[c])
    name = str(value)
    name = _HIGHWAY_ALIASES.get(name, name)
    try:
        return RoadClass[name]
    except KeyError:
        return RoadClass.unclassified


def parse_maxspeed_kph(value: Any) -> float | None:
    """Parse OSM `maxspeed` to km/h. Returns None when unusable (-> default).

    Handles "25 mph", "40", "walk"/"none"/"signals" junk, and lists from
    simplified edges (takes the max — err on the busy side).
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        parsed = [parse_maxspeed_kph(v) for v in value]
        vals = [v for v in parsed if v is not None]
        return max(vals) if vals else None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    m = _NUM_RE.search(s)
    if not m:
        return None
    speed = float(m.group(1))
    if "mph" in s:
        speed *= _MPH_TO_KPH
    return speed


def parse_lanes_per_direction(value: Any, oneway: bool) -> float | None:
    """Parse OSM `lanes` (total for two-way ways) to per-direction lanes."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        parsed = [parse_lanes_per_direction(v, oneway) for v in value]
        vals = [v for v in parsed if v is not None]
        return max(vals) if vals else None
    m = _NUM_RE.search(str(value))
    if not m:
        return None
    lanes = float(m.group(1))
    if not oneway:
        lanes = max(lanes / 2.0, 1.0)
    return lanes


def is_oneway(data: dict) -> bool:
    ow = data.get("oneway", False)
    if isinstance(ow, (list, tuple)):
        return any(is_oneway({"oneway": v}) for v in ow)
    return ow in (True, "yes", "true", "1", "-1", "reverse")


def infer_median(road_class: RoadClass, oneway: bool) -> bool:
    """Median heuristic: OSM has no reliable divided-road tag, but dual
    carriageways are mapped as parallel one-way pairs. A one-way edge of a
    major class is therefore almost always one side of a divided road.
    (Documented approximation — one-way couplets also match.)
    """
    return oneway and ROAD_CLASS_RANK[road_class] <= ROAD_CLASS_RANK[RoadClass.secondary]


def effective_speed_kph(data: dict, road_class: RoadClass, cfg) -> float:
    parsed = parse_maxspeed_kph(data.get("maxspeed"))
    if parsed is not None and parsed > 0:
        return parsed
    return float(cfg["defaults"]["speed_kph_by_class"][road_class.name])


def effective_lanes(data: dict, road_class: RoadClass, oneway: bool, cfg) -> float:
    parsed = parse_lanes_per_direction(data.get("lanes"), oneway)
    if parsed is not None and parsed > 0:
        return parsed
    return float(cfg["defaults"]["lanes_by_class"][road_class.name])
