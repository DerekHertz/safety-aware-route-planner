"""RouterFacade: one call = origin/destination/departure/toggle -> up to three
fully-described routes. Owns the pack, snap index and engine dispatch
(pyref | cpp feature flag); everything numeric flows through the shared
numpy precompute so both engines see identical inputs.
"""
from __future__ import annotations

import datetime
import warnings
from dataclasses import dataclass, field

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
                warnings.warn("sr_core extension not built - falling back to "
                              "the pure-Python engine ([engine] impl='cpp')")
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
              safety_enabled: bool = True) -> list[RouteOut]:
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

        alts = self._alternatives(qc, seeds, dests, h, safety_enabled)
        if not alts:
            raise RoutingError("no route found between these points")
        return [self._describe(a.kind, a.result, qc,
                               o_by_edge[a.result.first_edge],
                               d_by_edge[a.result.dest_edge]) for a in alts]

    def _alternatives(self, qc, seeds, dests, h, safety_enabled) -> list[Alternative]:
        return compute_alternatives(
            self.pack, qc, self.topo, seeds, dests, h, self.cfg, safety_enabled,
            run=lambda ac, hh, s, d: self._shortest_path(ac, hh, s, d))

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
        )
