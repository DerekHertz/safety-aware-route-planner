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
from pyref.alternatives import Alternative, compute_alternatives, compute_single
from pyref.config import Config
from pyref.costs import compute_costs, heuristic
from pyref.graph import GraphPack
from pyref.metrics import compute_metrics
from pyref.search import PathResult, shortest_path, topo_of
from pyref.snap import SnapCandidate, SnapIndex
from sim.snapshot import at_time


class RoutingError(Exception):
    """User-facing routing problem (bad snap, no path...)."""


# The route-artifact contract version (ADR-0004). Lives with the engine because
# the engine emits the artifact; api/schemas.py just mirrors it onto the wire.
# Bump this only on a deliberate breaking change to the artifact shape.
ROUTE_SCHEMA_VERSION = 1


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
    preference: dict          # {level, lambda, detour_budget_pct, departure_time}
    detour_pct: float = 0.0   # extra time vs the fastest route in this response
    schema_version: int = ROUTE_SCHEMA_VERSION


@dataclass
class _Plan:
    """The snapped, time-resolved search inputs for one query — the shared
    output of Router._resolve, consumed by both route() and reroute()."""
    qc: object
    o_by_edge: dict
    d_by_edge: dict
    seeds: list[tuple[int, float]]
    dests: list[tuple[int, float]]
    h: np.ndarray | None
    same_edge: PathResult | None   # set when origin/dest share one directed edge


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

    def _resolve(self, origin_lat: float, origin_lon: float,
                 dest_lat: float, dest_lon: float,
                 departure: datetime.datetime) -> _Plan:
        """Snap both endpoints, build the time-resolved costs, and prepare the
        search inputs (seeds/dests/heuristic). Shared by route() and reroute()
        so a reroute snaps and costs a query identically to a first plan.

        Detects the same-edge short-circuit (origin and destination on one
        directed edge, destination downstream) and carries its ready PathResult
        so the caller can label it at whatever safety level it needs.
        """
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

        same_edge = None
        for e, oc in o_by_edge.items():
            dc = d_by_edge.get(e)
            if dc is not None and oc.frac <= dc.frac:
                same_edge = PathResult(
                    turn_ids=np.array([], dtype=np.int32),
                    first_edge=e, dest_edge=e,
                    total_cost=(dc.frac - oc.frac) * float(qc.edge_time_s[e]))
                break

        seeds = [(int(c.edge), (1.0 - c.frac) * float(qc.edge_time_s[c.edge]))
                 for c in o_cands]
        dests = [(int(c.edge), (c.frac - 1.0) * float(qc.edge_time_s[c.edge]))
                 for c in d_cands]
        h = None
        if cfg["search"]["algo"] == "astar":
            h = heuristic(pack, qc, d_cands[0].lat, d_cands[0].lon)
        return _Plan(qc=qc, o_by_edge=o_by_edge, d_by_edge=d_by_edge,
                     seeds=seeds, dests=dests, h=h, same_edge=same_edge)

    def route(self, origin_lat: float, origin_lon: float,
              dest_lat: float, dest_lon: float,
              departure: datetime.datetime,
              safety_enabled: bool = True,
              detour_budget_pct: float | None = None) -> list[RouteOut]:
        cfg = self.cfg
        # Resolve the detour budget ONCE, here, so the value carried in every
        # route's preference is the concrete one the search used — never null,
        # even when the request omitted it (ADR-0004: the artifact is
        # self-describing). compute_alternatives applies the same fallback.
        budget = (float(cfg["alternatives"]["detour_budget_pct"])
                  if detour_budget_pct is None else detour_budget_pct)
        # The lambda a route's preference carries is the one its safety LEVEL
        # maps to (ADR-0004: "the lambda it maps to"), not the sweep lambda that
        # happened to survive dedup. After relabelling, a route labelled "safe"
        # may have been produced by the balanced sweep run; the contract still
        # reports lambda_safe, so label and lambda cannot drift apart.
        alt = cfg["alternatives"]
        lam_by_kind = {"fast": float(alt["lambda_fast"]),
                       "balanced": float(alt["lambda_balanced"]),
                       "safe": float(alt["lambda_safe"])}

        plan = self._resolve(origin_lat, origin_lon, dest_lat, dest_lon, departure)

        if plan.same_edge is not None:
            res = plan.same_edge
            return [self._describe("fast", res, plan.qc,
                                   plan.o_by_edge[res.first_edge],
                                   plan.d_by_edge[res.dest_edge],
                                   lam=lam_by_kind["fast"], budget=budget,
                                   departure=departure)]

        alts = self._alternatives(plan.qc, plan.seeds, plan.dests, plan.h,
                                  safety_enabled, detour_budget_pct)
        if not alts:
            raise RoutingError("no route found between these points")
        routes = [self._describe(a.kind, a.result, plan.qc,
                                 plan.o_by_edge[a.result.first_edge],
                                 plan.d_by_edge[a.result.dest_edge],
                                 lam=lam_by_kind[a.kind], budget=budget,
                                 departure=departure)
                  for a in alts]
        return _with_detour_pct(routes)

    def reroute(self, origin_lat: float, origin_lon: float,
                dest_lat: float, dest_lon: float,
                *, level: str, lam: float, detour_budget_pct: float,
                departure: datetime.datetime) -> RouteOut:
        """Reroute v1 (ADR-0008): replan from the current position to the
        original destination, recomputing ONLY the carried safety level. Returns
        a single artifact so a nav consumer stays at its chosen level instead of
        silently swapping onto whichever level happens to be fastest from the
        new position (the failure ADR-0002's carried-preference rule prevents).

        `level`, `lam` and `detour_budget_pct` come straight off the artifact's
        carried preference; `departure` is the preference's departure basis, so
        the reroute reproduces the same time-of-day conditions.
        """
        plan = self._resolve(origin_lat, origin_lon, dest_lat, dest_lon, departure)

        if plan.same_edge is not None:
            res = plan.same_edge
            return self._describe(level, res, plan.qc,
                                  plan.o_by_edge[res.first_edge],
                                  plan.d_by_edge[res.dest_edge],
                                  lam=lam, budget=detour_budget_pct,
                                  departure=departure)

        result = compute_single(
            self.pack, plan.qc, self.topo, plan.seeds, plan.dests, plan.h,
            self.cfg, level=level, lam=lam,
            detour_budget_pct=detour_budget_pct,
            run=lambda ac, hh, s, d: self._shortest_path(ac, hh, s, d))
        if result is None:
            raise RoutingError("no route found between these points")
        # detour_pct is defined relative to the fastest route in a RESPONSE; a
        # reroute returns one route, so it is the fastest by definition (0.0).
        return self._describe(level, result, plan.qc,
                              plan.o_by_edge[result.first_edge],
                              plan.d_by_edge[result.dest_edge],
                              lam=lam, budget=detour_budget_pct,
                              departure=departure)

    def _alternatives(self, qc, seeds, dests, h, safety_enabled,
                      detour_budget_pct) -> list[Alternative]:
        return compute_alternatives(
            self.pack, qc, self.topo, seeds, dests, h, self.cfg, safety_enabled,
            run=lambda ac, hh, s, d: self._shortest_path(ac, hh, s, d),
            detour_budget_pct=detour_budget_pct)

    def _describe(self, kind: str, result: PathResult, qc,
                  oc: SnapCandidate, dc: SnapCandidate, *,
                  lam: float, budget: float,
                  departure: datetime.datetime) -> RouteOut:
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
            # the label mirrors kind so the artifact is self-describing when
            # a consumer holds one route out of the response array (ADR-0004).
            preference={"level": kind, "lambda": lam,
                        "detour_budget_pct": budget,
                        "departure_time": departure},
        )
