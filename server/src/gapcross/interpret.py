"""Movement interpretation: a route becomes ordered movements and the crossings they make.

Knows OSM structure and route geometry, nothing about traffic controls. Rules (owner, 2026-11-06):

- Each route intersection is matched to the nearest OSM junction node within 3 m. A junction is a
  node with three or more arms counted from OSM ways, not from OSRM bearings. An intersection
  OSRM sees as a junction with no OSM junction node within 3 m is reported as uncovered.
- Headings come from the route geometry around the junction, so a via point on a junction (where
  OSRM gives no approach bearing) is still a movement. Turn = departure minus approach heading:
  straight within ±45°, left -45° to -135°, right +45° to +135°, beyond is a U-turn.
- Only `highway` primary, secondary or tertiary is a main street; everything else is local.
- Movements onto or across the same-named main street within 50 m merge into one crossing; any
  left makes it `left_onto`. A group whose route arrived on that same street is a left off it, not
  a crossing. Right turns are movements but never crossings.
"""

import math
from dataclasses import dataclass
from typing import Literal

from .sources import OsmWay, RawRoute, RecordSnapshot
from .types import LatLon

MATCH_RADIUS_M = 3.0
MERGE_WINDOW_M = 50.0
HEADING_REACH_M = 20.0
MAIN_HIGHWAYS = frozenset({"primary", "secondary", "tertiary"})
OCTANTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

Turn = Literal["straight", "left", "right", "u_turn"]


@dataclass(frozen=True)
class Movement:
    node_id: int
    location: LatLon
    along_m: float
    approach_bearing: float
    departure_bearing: float
    turn: Turn
    from_street: str
    to_street: str
    main_street: str | None  # the main street this movement goes straight across or left onto


@dataclass(frozen=True)
class RouteCrossing:
    """A crossing before assessment: the movements it is made of, keyed for records and caches."""

    key: str
    main_street: str
    kind: Literal["straight", "left_onto"]
    approach_bearing: float
    along_m: float
    location: LatLon
    movements: tuple[Movement, ...]


@dataclass(frozen=True)
class Interpretation:
    movements: tuple[Movement, ...]
    crossings: tuple[RouteCrossing, ...]
    uncovered: tuple[LatLon, ...]  # junction intersections with no OSM junction node within 3 m


def interpret(raw: RawRoute, snapshot: RecordSnapshot) -> Interpretation:
    junctions = _Junctions(snapshot)
    line = _Polyline(raw.geometry)
    matched: list[tuple[int, float]] = []
    uncovered: list[LatLon] = []
    seen: set[int] = set()
    for step in raw.steps:
        for x in step.intersections:
            node = junctions.nearest(x.location)
            if node is None:
                if len(x.bearings) >= 3:
                    uncovered.append(x.location)
                continue
            if node in seen:
                continue
            seen.add(node)
            matched.append((node, line.locate(snapshot.nodes[node].location)))
    matched.sort(key=lambda m: m[1])

    movements = []
    for i, (node, along) in enumerate(matched):
        before = along - matched[i - 1][1] if i > 0 else math.inf
        after = matched[i + 1][1] - along if i + 1 < len(matched) else math.inf
        movement = _movement(node, along, line, junctions, snapshot, before, after)
        if movement is not None:
            movements.append(movement)
    return Interpretation(tuple(movements), _crossings(movements), tuple(uncovered))


def _movement(node, along, line, junctions, snapshot, gap_before, gap_after) -> Movement | None:
    if along <= 0 or along >= line.length:
        return None
    back = min(HEADING_REACH_M, gap_before / 2, along)
    ahead = min(HEADING_REACH_M, gap_after / 2, line.length - along)
    here = line.point_at(along)
    approach = _bearing(line.point_at(along - back), here)
    departure = _bearing(here, line.point_at(along + ahead))
    turn = _turn(approach, departure)
    arms = junctions.arms(node)
    arrival = _closest_arm(arms, (approach + 180) % 360)
    leaving = _closest_arm(arms, departure)
    from_street, to_street = _name(arrival.way), _name(leaving.way)
    main = None
    if turn == "left" and _is_main(leaving.way) and to_street != from_street:
        main = to_street
    elif turn == "straight":
        crossed = [a.way for a in arms if a is not arrival and a is not leaving and _is_main(a.way)
                   and _name(a.way) not in (from_street, to_street)]
        if crossed:
            main = _name(min(crossed, key=_rank))
    return Movement(
        node_id=node, location=snapshot.nodes[node].location, along_m=along,
        approach_bearing=approach, departure_bearing=departure, turn=turn,
        from_street=from_street, to_street=to_street, main_street=main,
    )


def _crossings(movements: list[Movement]) -> tuple[RouteCrossing, ...]:
    crossings = []
    used: set[int] = set()
    for i, first in enumerate(movements):
        if first.main_street is None or i in used:
            continue
        street = first.main_street
        group = [first]
        for j in range(i + 1, len(movements)):
            m = movements[j]
            if m.along_m - first.along_m > MERGE_WINDOW_M:
                break
            if m.main_street == street:
                group.append(m)
                used.add(j)
        arrived_on_it = any(
            m.from_street == street for m in movements
            if first.along_m - MERGE_WINDOW_M <= m.along_m <= group[-1].along_m
        )
        if arrived_on_it:
            continue
        kind = "left_onto" if any(m.turn == "left" for m in group) else "straight"
        nodes = "+".join(str(n) for n in sorted(m.node_id for m in group))
        crossings.append(RouteCrossing(
            key=f"{nodes}/{kind}/{octant(first.approach_bearing)}",
            main_street=street, kind=kind, approach_bearing=first.approach_bearing,
            along_m=first.along_m, location=first.location, movements=tuple(group),
        ))
    return tuple(crossings)


def octant(bearing: float) -> str:
    return OCTANTS[int(((bearing % 360) + 22.5) // 45) % 8]


def _turn(approach: float, departure: float) -> Turn:
    delta = (departure - approach + 540) % 360 - 180
    if abs(delta) <= 45:
        return "straight"
    if -135 <= delta < -45:
        return "left"
    if 45 < delta <= 135:
        return "right"
    return "u_turn"


def _is_main(way: OsmWay) -> bool:
    return way.tags.get("highway") in MAIN_HIGHWAYS


def _rank(way: OsmWay) -> int:
    return ("primary", "secondary", "tertiary").index(way.tags["highway"])


def _name(way: OsmWay) -> str:
    return way.tags.get("name", "")


@dataclass(frozen=True)
class _Arm:
    way: OsmWay
    bearing: float


def _closest_arm(arms: list[_Arm], bearing: float) -> _Arm:
    return min(arms, key=lambda a: abs((a.bearing - bearing + 540) % 360 - 180))


class _Junctions:
    """OSM junction nodes (three or more arms from drivable ways) and their arms."""

    def __init__(self, snapshot: RecordSnapshot):
        self.snapshot = snapshot
        self._arms: dict[int, list[tuple[OsmWay, int]]] = {}
        for way in snapshot.ways.values():
            ids = way.node_ids
            for k, n in enumerate(ids):
                for neighbour in (k - 1, k + 1):
                    if 0 <= neighbour < len(ids) and ids[neighbour] != n:
                        self._arms.setdefault(n, []).append((way, ids[neighbour]))
        nodes = snapshot.nodes
        self.ids = [n for n, arms in self._arms.items() if len(arms) >= 3 and n in nodes]
        self._cells: dict[tuple[int, int], list[int]] = {}
        for n in self.ids:
            self._cells.setdefault(_cell(nodes[n].location), []).append(n)

    def nearest(self, at: LatLon) -> int | None:
        cx, cy = _cell(at)
        best, best_d = None, MATCH_RADIUS_M
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for n in self._cells.get((cx + dx, cy + dy), ()):
                    d = _distance(self.snapshot.nodes[n].location, at)
                    if d <= best_d:
                        best, best_d = n, d
        return best

    def arms(self, node: int) -> list[_Arm]:
        here = self.snapshot.nodes[node].location
        return [
            _Arm(way, _bearing(here, self.snapshot.nodes[other].location))
            for way, other in self._arms[node] if other in self.snapshot.nodes
        ]


def _cell(p: LatLon) -> tuple[int, int]:
    return int(math.floor(p.lat * 10_000)), int(math.floor(p.lon * 10_000))


class _Polyline:
    def __init__(self, points: tuple[LatLon, ...]):
        self.points = [p for k, p in enumerate(points) if k == 0 or p != points[k - 1]]
        self.cumulative = [0.0]
        for a, b in zip(self.points, self.points[1:]):
            self.cumulative.append(self.cumulative[-1] + _distance(a, b))
        self.length = self.cumulative[-1]

    def locate(self, p: LatLon) -> float:
        """Distance along the line to the point on it closest to p."""
        best_d, best_along = math.inf, 0.0
        for k, (a, b) in enumerate(zip(self.points, self.points[1:])):
            t, d = _project(p, a, b)
            if d < best_d:
                best_d, best_along = d, self.cumulative[k] + t * (self.cumulative[k + 1] - self.cumulative[k])
        return best_along

    def point_at(self, along: float) -> LatLon:
        along = min(max(along, 0.0), self.length)
        for k in range(len(self.points) - 1):
            start, end = self.cumulative[k], self.cumulative[k + 1]
            if along <= end and end > start:
                t = (along - start) / (end - start)
                a, b = self.points[k], self.points[k + 1]
                return LatLon(lat=a.lat + t * (b.lat - a.lat), lon=a.lon + t * (b.lon - a.lon))
        return self.points[-1]


def _xy(p: LatLon, origin: LatLon) -> tuple[float, float]:
    k = 111_320.0
    return (p.lon - origin.lon) * k * math.cos(math.radians(origin.lat)), (p.lat - origin.lat) * k


def _distance(a: LatLon, b: LatLon) -> float:
    return math.hypot(*_xy(b, a))


def _project(p: LatLon, a: LatLon, b: LatLon) -> tuple[float, float]:
    bx, by = _xy(b, a)
    px, py = _xy(p, a)
    length2 = bx * bx + by * by
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, (px * bx + py * by) / length2))
    return t, math.hypot(px - t * bx, py - t * by)


def _bearing(a: LatLon, b: LatLon) -> float:
    x, y = _xy(b, a)
    return math.degrees(math.atan2(x, y)) % 360
