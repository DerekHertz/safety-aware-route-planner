"""Google Maps handoff (ADR-0020): waypoints that make a time-optimal
navigator follow a route's safety.

The navigator's own routing is unobservable, so our lambda-0 search stands in
for it. Between consecutive anchors (origin, pins, destination) the stand-in
runs on the same per-query costs. Each pin goes on the farthest target edge
the stand-in reaches, from the previous anchor, with no more flagged maneuvers
than the target takes over the same stretch. Pins protect safety,
not geometry: a stand-in path that differs from ours but is no less safe is
left alone, which keeps the count inside the navigator's waypoint limit.

What the stand-in still takes through the final pins is reported as
`predicted_unsafe`, so a client can warn rather than promise.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyref.costs import UNSAFE_NONE, QueryCosts, arc_cost, heuristic
from pyref.geometry import edge_point
from pyref.graph import GraphPack
from pyref.search import PathResult


@dataclass(frozen=True)
class HandoffPlan:
    edges: list[int]                       # pinned target edges, in route order
    waypoints: list[tuple[float, float]]   # (lat, lon), one per pinned edge
    # Flagged maneuvers the stand-in takes when driving through the pins.
    # Equal to the target's own count when the pins fully steer it.
    predicted_unsafe: int


def pin_waypoints(pack: GraphPack, qc: QueryCosts, target: PathResult, *,
                  seeds: list[tuple[int, float]],
                  dests: list[tuple[int, float]],
                  h_dest: np.ndarray | None,
                  run,
                  max_waypoints: int,
                  frac_origin: float = 0.0,
                  frac_dest: float = 1.0) -> HandoffPlan:
    """`seeds`/`dests`/`h_dest` are the query the target was searched from;
    `run(ac, h, seeds, dests)` performs one search (pyref or sr_core)."""
    edges = target.edges(pack.turn_out_edge)
    n = len(edges)
    origin, dest = -1, n                   # anchor sentinels around edge indices
    flagged = qc.turn_unsafe_type != UNSAFE_NONE
    ac = arc_cost(pack, qc, 0.0)

    def frac_of(i: int) -> float:
        """Midpoint of the part of edge i the route actually drives."""
        lo = frac_origin if i == 0 else 0.0
        hi = frac_dest if i == n - 1 else 1.0
        return (lo + hi) / 2.0

    def point_of(i: int) -> tuple[float, float]:
        return edge_point(pack, edges[i], frac_of(i))

    cache: dict[tuple[int, int], PathResult | None] = {}

    def stand_in(a: int, b: int) -> PathResult | None:
        if (a, b) not in cache:
            if a == origin:
                s = seeds
            else:
                t = float(qc.edge_time_s[edges[a]])
                s = [(edges[a], (1.0 - frac_of(a)) * t)]
            if b == dest:
                d, h = dests, h_dest
            else:
                t = float(qc.edge_time_s[edges[b]])
                d = [(edges[b], (frac_of(b) - 1.0) * t)]
                lat, lon = point_of(b)
                h = heuristic(pack, qc, lat, lon) if h_dest is not None else None
            cache[(a, b)] = run(ac, h, s, d)
        return cache[(a, b)]

    def span(a: int, b: int) -> tuple[int, int]:
        """Target edge indices [ia, ib] the stretch a..b covers."""
        return max(a, 0), min(b, n - 1)

    def target_unsafe(a: int, b: int) -> int:
        ia, ib = span(a, b)
        return int(np.count_nonzero(flagged[target.turn_ids[ia:ib]]))

    def stand_in_unsafe(a: int, b: int) -> int:
        res = stand_in(a, b)
        if res is None:                    # no path: nothing to learn here
            return target_unsafe(a, b)
        return int(np.count_nonzero(flagged[res.turn_ids]))

    def steered(a: int, b: int) -> bool:
        return stand_in_unsafe(a, b) <= target_unsafe(a, b)

    # Greedy farthest reach: from each anchor, pin the farthest target edge
    # the stand-in still reaches no less safely, then continue from there.
    # Binary search assumes reach is monotone along the route; where it is
    # not, the result is still correct (every stretch is checked), only
    # possibly not the fewest pins.
    pins: list[int] = []
    a = origin
    while len(pins) < max_waypoints and not steered(a, dest):
        lo, hi, best = a + 1, n - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if steered(a, mid):
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        if best is None:                   # cannot be steered from here
            break
        pins.append(best)
        a = best

    anchors = [origin, *pins, dest]
    predicted = sum(stand_in_unsafe(a, b)
                    for a, b in zip(anchors, anchors[1:], strict=False))
    return HandoffPlan(edges=[edges[i] for i in pins],
                       waypoints=[point_of(i) for i in pins],
                       predicted_unsafe=predicted)
