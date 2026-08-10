"""RouterFacade: one call = origin/destination/departure/toggle -> up to three
fully-described routes. Owns the pack, snap index and engine dispatch
(pyref | cpp feature flag); everything numeric flows through the shared
numpy precompute so both engines see identical inputs.
"""
from __future__ import annotations

import datetime
import warnings
from dataclasses import dataclass

import numpy as np

from pyref import geometry as geo_out
from pyref.alternatives import Alternative, compute_alternatives
from pyref.config import Config
from pyref.costs import compute_costs, heuristic
from pyref.graph import GraphPack
from pyref.metrics import compute_metrics
from pyref.search import PathResult, shortest_path, topo_of
from pyref.snap import SnapCandidate, SnapIndex
from sim.snapshot import at_time


class RoutingError(Exception):
    """User-facing routing problem (bad snap, no path...)."""


def _with_detour_pct(routes: list[RouteOut]) -> list[RouteOut]:
    """Fill in each route's extra time relative to the fastest one returned,
    so the UI can show what a safer route actually costs."""
    fastest = min((r.eta_s for r in routes), default=0.0)
    if fastest > 0:
        for r in routes:
            r.detour_pct = (r.eta_s - fastest) / fastest
    return routes


@dataclass
class RouteOut:
    """One route in (almost) API-contract shape."""
    kind: str
    geometry: dict
    distance_m: float
    eta_s: float
    unsafe: dict
    segments: list[dict]
    unsafe_points: list[dict]
    maneuvers: list[dict]
    detour_pct: float = 0.0   # extra time vs the fastest route in this response


class Router:
    def __init__(self, pack: GraphPack, cfg: Config):
        self.pack = pack
        self.cfg = cfg
        self.snap_index = SnapIndex(pack)
        self.topo = topo_of(pack)
        self._impl = cfg["engine"]["impl"]
        self._cpp_engine = None
        if self._impl == "cpp":
            try:
                import sr_core
                self._cpp_engine = sr_core.Engine(
                    pack.turn_ptr, pack.turn_out_edge,
                    pack.turn_in_edge, pack.turn_allowed)
            except ImportError:
                # stacklevel=2 points the warning at whoever constructed the
                # Router, not at this line — this is a ~20x latency cliff and
                # the caller is who needs to see it.
                warnings.warn("sr_core extension not built - falling back to "
                              "the pure-Python engine ([engine] impl='cpp')",
                              stacklevel=2)
                self._impl = "pyref"

    # engine dispatch: identical signature both ways
    def _shortest_path(self, ac, h, seeds, dests) -> PathResult | None:
        if self._cpp_engine is not None:
            hit = self._cpp_engine.shortest_path(ac, h, seeds, dests)
            if hit is None:
                return None
            turn_ids, first_edge, dest_edge, total = hit
            return PathResult(turn_ids=turn_ids, first_edge=int(first_edge),
                              dest_edge=int(dest_edge), total_cost=float(total))
        return shortest_path(self.topo, ac, h, seeds, dests)

    def route(self, origin_lat: float, origin_lon: float,
              dest_lat: float, dest_lon: float,
              departure: datetime.datetime,
              safety_enabled: bool = True,
              detour_budget_pct: float | None = None) -> list[RouteOut]:
        cfg = self.cfg
        pack = self.pack
        sc = cfg["search"]

        o_cands = self.snap_index.snap(origin_lat, origin_lon,
                                       k=int(sc["snap_k"]), max_m=float(sc["snap_max_m"]))
        d_cands = self.snap_index.snap(dest_lat, dest_lon,
                                       k=int(sc["snap_k"]), max_m=float(sc["snap_max_m"]))
        if not o_cands:
            raise RoutingError("origin is too far from any drivable road")
        if not d_cands:
            raise RoutingError("destination is too far from any drivable road")

        snap = at_time(pack, cfg, departure)
        qc = compute_costs(pack, snap, cfg)

        o_by_edge = {c.edge: c for c in o_cands}
        d_by_edge = {c.edge: c for c in d_cands}

        # same-edge short-circuit: origin and destination on one directed
        # edge with the destination downstream — no search needed
        for e, oc in o_by_edge.items():
            dc = d_by_edge.get(e)
            if dc is not None and oc.frac <= dc.frac:
                res = PathResult(turn_ids=np.array([], dtype=np.int32),
                                 first_edge=e, dest_edge=e,
                                 total_cost=(dc.frac - oc.frac) * float(qc.edge_time_s[e]))
                return [self._describe("fast", res, qc, oc, dc)]

        seeds = [(int(c.edge), (1.0 - c.frac) * float(qc.edge_time_s[c.edge]))
                 for c in o_cands]
        dests = [(int(c.edge), (c.frac - 1.0) * float(qc.edge_time_s[c.edge]))
                 for c in d_cands]
        h = None
        if cfg["search"]["algo"] == "astar":
            h = heuristic(pack, qc, d_cands[0].lat, d_cands[0].lon)

        alts = self._alternatives(qc, seeds, dests, h, safety_enabled,
                                  detour_budget_pct)
        if not alts:
            raise RoutingError("no route found between these points")
        routes = [self._describe(a.kind, a.result, qc,
                                 o_by_edge[a.result.first_edge],
                                 d_by_edge[a.result.dest_edge]) for a in alts]
        return _with_detour_pct(routes)

    def _alternatives(self, qc, seeds, dests, h, safety_enabled,
                      detour_budget_pct) -> list[Alternative]:
        return compute_alternatives(
            self.pack, qc, self.topo, seeds, dests, h, self.cfg, safety_enabled,
            run=lambda ac, hh, s, d: self._shortest_path(ac, hh, s, d),
            detour_budget_pct=detour_budget_pct)

    def _describe(self, kind: str, result: PathResult, qc,
                  oc: SnapCandidate, dc: SnapCandidate) -> RouteOut:
        pack = self.pack
        m = compute_metrics(pack, qc, result,
                            frac_origin=oc.frac, frac_dest=dc.frac)
        return RouteOut(
            kind=kind,
            geometry=geo_out.route_geometry(pack, result, oc.frac, dc.frac),
            distance_m=m.distance_m,
            eta_s=m.eta_s,
            unsafe={"unprotected_left": m.unprotected_left,
                    "uncontrolled_crossing": m.uncontrolled_crossing,
                    "total": m.unsafe_total},
            segments=geo_out.route_segments(pack, qc, result, oc.frac, dc.frac),
            unsafe_points=geo_out.unsafe_points(pack, qc, result),
            maneuvers=geo_out.route_maneuvers(pack, result, oc.frac, dc.frac),
        )
