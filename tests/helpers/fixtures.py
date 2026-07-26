"""Named toy fixtures + routing helpers used across test modules.

All fixtures use explicit round lengths and 36 km/h (= 10 m/s) speeds so
optimal costs are hand-computable: a 1000 m edge takes exactly 100 s.
"""
from __future__ import annotations

import numpy as np

from pyref.config import Config
from pyref.costs import arc_cost, compute_costs, heuristic
from pyref.graph import Control, GraphPack, RoadClass
from pyref.search import PathResult, shortest_path, topo_of
from sim.snapshot import free_flow
from tests.helpers.toy_graphs import GraphBuilder, find_edge


def make_costs(pack: GraphPack, cfg: Config | None = None):
    cfg = cfg or Config.load()
    return compute_costs(pack, free_flow(pack, cfg), cfg)


def route_between_nodes(pack, qc, u: int, v: int, *, lam: float = 0.0,
                        algo: str = "dijkstra") -> PathResult | None:
    """Node-to-node routing for toys: seeds are all out-edges of u (cost =
    full edge time), destinations all in-edges of v (no tail refund)."""
    ac = arc_cost(pack, qc, lam)
    seeds = [(int(e), float(qc.edge_time_s[e]))
             for e in np.flatnonzero(pack.edge_tail == u)]
    dests = [(int(e), 0.0) for e in np.flatnonzero(pack.edge_head == v)]
    h = None
    if algo == "astar":
        h = heuristic(pack, qc, float(pack.node_lat[v]), float(pack.node_lon[v]))
    return shortest_path(topo_of(pack), ac, h, seeds, dests)


def find_turn(pack: GraphPack, e_in: int, e_out: int) -> int:
    lo, hi = pack.turn_ptr[e_in], pack.turn_ptr[e_in + 1]
    for t in range(lo, hi):
        if pack.turn_out_edge[t] == e_out:
            return t
    raise AssertionError(f"no turn {e_in}->{e_out}")


# ------------------------------------------------------------------ fixtures
# Node spacing for 1000 m edges: 0.008 deg (~890 m straight-line) keeps the
# geometric distance BELOW the declared edge length — required for heuristic
# consistency (h assumes edge length >= straight-line distance).
_SPACING_DEG = 0.008


def line3() -> tuple[GraphPack, int, int, int]:
    """A - B - C, two 1000 m residential segments. A->C costs exactly 200 s."""
    b = GraphBuilder()
    a = b.node(0.0, 0.0)
    m = b.node(0.0, _SPACING_DEG)
    c = b.node(0.0, 2 * _SPACING_DEG)
    b.edge(a, m, length_m=1000.0)
    b.edge(m, c, length_m=1000.0)
    return b.build(), a, m, c


def grid3x3() -> tuple[GraphPack, list[int]]:
    """3x3 residential grid, all edges 1000 m / 100 s. Corner-to-corner
    optimum = 4 edges = 400 s (many ties — cost is asserted, not the path)."""
    b = GraphBuilder()
    nodes = [[b.node(_SPACING_DEG * r, _SPACING_DEG * c) for c in range(3)]
             for r in range(3)]
    for r in range(3):
        for c in range(3):
            if c + 1 < 3:
                b.edge(nodes[r][c], nodes[r][c + 1], length_m=1000.0)
            if r + 1 < 3:
                b.edge(nodes[r][c], nodes[r + 1][c], length_m=1000.0)
    flat = [n for row in nodes for n in row]
    return b.build(), flat


def unprotected_left_city():
    """The named scenario: the FAST route takes an unprotected left onto a
    busy arterial; the SAFE route detours one block to a protected signal.

           A0 --- A1 --- A2 --- A3     <- busy primary arterial (E->W to A0)
                   |      |
                  SW ---- S            <- residential side streets

    Origin S, destination A0 (west on the arterial).
      fast: S->A2, LEFT at A2 (control NONE) onto the arterial  [flagged]
      safe: S->SW->A1, LEFT at A1 under a SIGNAL_PROTECTED      [clean]
    Arterial 56 km/h => 500 m in ~32.14 s; residential 10 m/s => 50 s.
    """
    b = GraphBuilder()
    a0 = b.node(0.0, -0.008)
    a1 = b.node(0.0, -0.004, control=Control.SIGNAL_PROTECTED)
    a2 = b.node(0.0, 0.0, control=Control.NONE)
    a3 = b.node(0.0, 0.004)
    s = b.node(-0.004, 0.0)
    sw = b.node(-0.004, -0.004)
    arterial = dict(road_class=RoadClass.primary, speed_kph=56, lanes=2,
                    length_m=500.0)
    b.edge(a0, a1, **arterial)
    b.edge(a1, a2, **arterial)
    b.edge(a2, a3, **arterial)
    b.edge(s, a2, length_m=500.0)
    b.edge(s, sw, length_m=500.0)
    b.edge(sw, a1, length_m=500.0)
    pack = b.build()
    ids = dict(a0=a0, a1=a1, a2=a2, a3=a3, s=s, sw=sw)
    return pack, ids


def cross_with_control(control: Control):
    """Busy E-W primary arterial crossing a quiet N-S residential street.

    Returns (pack, ids) where ids maps 'c','n','s','e','w' to node ids.
    The interesting maneuvers, approaching from the south (edge s->c):
      LEFT  onto c->w  (left onto the busy arterial)
      STRAIGHT onto c->n (crossing the busy arterial)
    """
    b = GraphBuilder()
    c = b.node(0.0, 0.0, control=control)
    n = b.node(0.004, 0.0)
    s = b.node(-0.004, 0.0)
    e = b.node(0.0, 0.004)
    w = b.node(0.0, -0.004)
    b.edge(s, c, length_m=500.0)   # residential 36 km/h
    b.edge(c, n, length_m=500.0)
    b.edge(w, c, road_class=RoadClass.primary, speed_kph=56, lanes=2, length_m=500.0)
    b.edge(c, e, road_class=RoadClass.primary, speed_kph=56, lanes=2, length_m=500.0)
    pack = b.build()
    ids = {"c": c, "n": n, "s": s, "e": e, "w": w}
    return pack, ids
