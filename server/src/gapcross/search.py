"""Local search: candidate crossings of the same main street within 3 blocks of a flagged one.

For each flagged straight crossing on the default route, walk the main street both ways from it
and take the first 3 local cross streets in each direction. Each candidate is a list of waypoints
that follows the default route up to a detour start, crosses the main street on the cross street
in the same direction as the default, and rejoins the default route after the crossing. A
candidate counts only if its actual geometry makes that crossing (rule 6).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from .interpret import Interpretation, RouteCrossing, _Polyline
from .sources import OsmWay, RawRoute, RecordSnapshot
from .trips import TripPlan
from .types import Crossing, LatLon

BLOCKS = 3
MAX_ALONG_MAIN_M = 800.0  # how far along the main street the walk may go each way
CROSS_REACH_M = 40.0  # waypoints this far out on the cross street, either side of the main street
MIN_CROSS_REACH_M = 20.0
DETOUR_M = 300.0  # the detour leaves and rejoins the default route this far from the crossing
SAME_DIRECTION_DEG = 45.0
ARM_MATCH_DEG = 60.0
MAIN_HIGHWAYS = frozenset({"primary", "secondary", "tertiary"})


@dataclass(frozen=True)
class Target:
    avoid_key: str  # the default route's crossing this candidate replaces
    main_street: str
    junction: int  # a node of the candidate crossing on the main street
    cross_street: str
    approach_bearing: float  # the intended direction across the main street
    distance_m: float  # along the main street from the avoided crossing
    waypoints: tuple[LatLon, ...]


def targets(trip: TripPlan, default_raw: RawRoute, interpretation: Interpretation,
            crossings: Sequence[Crossing], snapshot: RecordSnapshot, limit: int) -> list[Target]:
    graph = _Graph(snapshot)
    line = _Polyline(default_raw.geometry)
    flagged = {c.key for c in crossings if c.assessment.status == "flagged"}
    per_crossing = []
    for rc in interpretation.crossings:
        if rc.key not in flagged or rc.kind != "straight":
            continue
        found = [t for t in _targets_for(rc, trip, line, graph) if t is not None]
        per_crossing.append(sorted(found, key=lambda t: t.distance_m))
    # round-robin so every flagged crossing gets its nearest candidates first
    ordered: list[Target] = []
    for rank in range(max((len(p) for p in per_crossing), default=0)):
        ordered += [p[rank] for p in per_crossing if rank < len(p)]
    return ordered[:limit]


def crosses_as_intended(target: Target, interpretation: Interpretation) -> bool:
    keys = {c.key for c in interpretation.crossings}
    if target.avoid_key in keys:
        return False
    return any(_hits(target, c) for c in interpretation.crossings)


def _hits(target: Target, c: RouteCrossing) -> bool:
    nodes = {m.node_id for m in c.movements}
    turn = abs((c.approach_bearing - target.approach_bearing + 540) % 360 - 180)
    return (c.main_street == target.main_street and target.junction in nodes and c.kind == "straight"
            and turn <= SAME_DIRECTION_DEG)


def _targets_for(rc: RouteCrossing, trip: TripPlan, line: _Polyline, graph: "_Graph"):
    start = rc.movements[0]
    own_cross = {m.from_street for m in rc.movements} | {m.to_street for m in rc.movements}
    bearing = rc.approach_bearing
    for node, cross, dist in graph.cross_streets(start.node_id, rc.main_street, own_cross):
        before = graph.walk_named(node, cross, (bearing + 180) % 360)
        after = graph.walk_named(node, cross, bearing)
        if before is None or after is None:
            continue
        yield Target(
            avoid_key=rc.key, main_street=rc.main_street, junction=node, cross_street=cross,
            approach_bearing=bearing, distance_m=dist,
            waypoints=_waypoints(trip, line, rc.along_m, before, after),
        )


def _waypoints(trip: TripPlan, line: _Polyline, along: float, before: LatLon, after: LatLon) -> tuple[LatLon, ...]:
    leave, rejoin = along - DETOUR_M, along + DETOUR_M
    vias = [(line.locate(v), v) for v in trip.example_via]
    points = [trip.origin]
    points += [v for a, v in vias if a < leave]
    if leave > 50:
        points.append(_round(line.point_at(leave)))
    points += [before, after]
    if rejoin < line.length - 50:
        points.append(_round(line.point_at(rejoin)))
    points += [v for a, v in vias if a > rejoin]
    points.append(trip.destination)
    return tuple(points)


def _round(p: LatLon) -> LatLon:
    return LatLon(lat=round(p.lat, 6), lon=round(p.lon, 6))


class _Graph:
    def __init__(self, snapshot: RecordSnapshot):
        self.nodes = snapshot.nodes
        self.adj: dict[int, list[tuple[OsmWay, int]]] = {}
        for way in snapshot.ways.values():
            ids = way.node_ids
            for a, b in zip(ids, ids[1:]):
                if a in self.nodes and b in self.nodes and a != b:
                    self.adj.setdefault(a, []).append((way, b))
                    self.adj.setdefault(b, []).append((way, a))

    def cross_streets(self, start: int, main: str, skip: set[str]):
        """(node, cross street, distance) for the first BLOCKS local cross streets each way."""
        for first in [nb for w, nb in self.adj.get(start, ()) if _name(w) == main]:
            seen: set[str] = set(skip)
            prev, cur, dist = start, first, self._d(start, first)
            while cur is not None and dist <= MAX_ALONG_MAIN_M and len(seen - skip) < BLOCKS:
                for way, _ in self.adj.get(cur, ()):
                    name = _name(way)
                    if name and name != main and name not in seen and way.tags.get("highway") not in MAIN_HIGHWAYS:
                        seen.add(name)
                        yield cur, name, dist
                nxt = self._onward(prev, cur, main)
                if nxt is None:
                    break
                prev, cur = cur, nxt
                dist += self._d(prev, cur)

    def walk_named(self, start: int, name: str, bearing: float) -> LatLon | None:
        """The point CROSS_REACH_M out from start along `name`, leaving in about this bearing."""
        arms = [(nb, self._bearing(start, nb)) for w, nb in self.adj.get(start, ()) if _name(w) == name]
        if not arms:
            return None
        first, b = min(arms, key=lambda a: _diff(a[1], bearing))
        if _diff(b, bearing) > ARM_MATCH_DEG:
            return None
        prev, cur, dist = start, first, self._d(start, first)
        while dist < CROSS_REACH_M:
            nxt = self._onward(prev, cur, name)
            if nxt is None:
                break
            prev, cur = cur, nxt
            dist += self._d(prev, cur)
        if dist < MIN_CROSS_REACH_M:
            return None
        return _round(self.nodes[cur].location)

    def _onward(self, prev: int, cur: int, name: str) -> int | None:
        heading = self._bearing(prev, cur)
        options = [nb for w, nb in self.adj.get(cur, ()) if _name(w) == name and nb != prev]
        if not options:
            return None
        best = min(options, key=lambda nb: _diff(self._bearing(cur, nb), heading))
        return best if _diff(self._bearing(cur, best), heading) <= ARM_MATCH_DEG else None

    def _d(self, a: int, b: int) -> float:
        p, q = self.nodes[a].location, self.nodes[b].location
        k = 111_320.0
        return math.hypot((p.lat - q.lat) * k, (p.lon - q.lon) * k * math.cos(math.radians(p.lat)))

    def _bearing(self, a: int, b: int) -> float:
        p, q = self.nodes[a].location, self.nodes[b].location
        k = 111_320.0
        x = (q.lon - p.lon) * k * math.cos(math.radians(p.lat))
        return math.degrees(math.atan2(x, (q.lat - p.lat) * k)) % 360


def _name(way: OsmWay) -> str:
    return way.tags.get("name", "")


def _diff(a: float, b: float) -> float:
    return abs((a - b + 540) % 360 - 180)
