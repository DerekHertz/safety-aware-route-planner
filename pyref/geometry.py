"""Route -> GeoJSON: full LineString plus per-segment tier pieces for map
coloring, with partial first/last edge clipping at snap fractions."""
from __future__ import annotations

import numpy as np

from pyref.costs import TIER_SAFE, QueryCosts
from pyref.geo import haversine_m
from pyref.graph import GraphPack
from pyref.search import PathResult


def _edge_coords(pack: GraphPack, e: int,
                 frac_start: float = 0.0, frac_end: float = 1.0) -> list[list[float]]:
    """[lon, lat] coords of edge e clipped to [frac_start, frac_end] by
    polyline length."""
    s, t = int(pack.edge_geom_ptr[e]), int(pack.edge_geom_ptr[e + 1])
    lats = pack.geom_lat[s:t]
    lons = pack.geom_lon[s:t]
    if frac_start <= 0.0 and frac_end >= 1.0:
        return [[float(lo), float(la)] for lo, la in zip(lons, lats, strict=True)]

    seg = haversine_m(lats[:-1], lons[:-1], lats[1:], lons[1:])
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = cum[-1]
    if total <= 0.0:
        return [[float(lons[0]), float(lats[0])], [float(lons[-1]), float(lats[-1])]]

    def point_at(dist: float) -> list[float]:
        i = int(np.searchsorted(cum, dist, side="right") - 1)
        i = max(0, min(i, len(seg) - 1))
        span = seg[i]
        u = 0.0 if span <= 0 else (dist - cum[i]) / span
        return [float(lons[i] + u * (lons[i + 1] - lons[i])),
                float(lats[i] + u * (lats[i + 1] - lats[i]))]

    d0, d1 = frac_start * total, frac_end * total
    inner = [[float(lo), float(la)]
             for lo, la, c in zip(lons, lats, cum, strict=True) if d0 < c < d1]
    return [point_at(d0)] + inner + [point_at(d1)]


def route_geometry(pack: GraphPack, result: PathResult,
                   frac_origin: float = 0.0, frac_dest: float = 1.0) -> dict:
    """GeoJSON LineString for the whole route."""
    edges = result.edges(pack.turn_out_edge)
    coords: list[list[float]] = []
    for i, e in enumerate(edges):
        fs = frac_origin if i == 0 else 0.0
        fe = frac_dest if i == len(edges) - 1 else 1.0
        if i == 0 and len(edges) == 1:
            fs, fe = frac_origin, frac_dest
        pts = _edge_coords(pack, e, fs, fe)
        if coords and pts and coords[-1] == pts[0]:
            pts = pts[1:]
        coords.extend(pts)
    return {"type": "LineString", "coordinates": coords}


def route_segments(pack: GraphPack, qc: QueryCosts, result: PathResult,
                   frac_origin: float = 0.0, frac_dest: float = 1.0) -> list[dict]:
    """Per-edge pieces labeled with the tier of the maneuver that ENTERS the
    edge (the first edge, entered without a turn, is 'safe')."""
    edges = result.edges(pack.turn_out_edge)
    tiers = [TIER_SAFE] + [int(qc.turn_tier[t]) for t in result.turn_ids]
    names = {0: "safe", 1: "caution", 2: "unsafe"}
    out = []
    for i, (e, tier) in enumerate(zip(edges, tiers, strict=True)):
        fs = frac_origin if i == 0 else 0.0
        fe = frac_dest if i == len(edges) - 1 else 1.0
        out.append({
            "geometry": {"type": "LineString",
                         "coordinates": _edge_coords(pack, e, fs, fe)},
            "tier": names[tier],
        })
    return out


def unsafe_points(pack: GraphPack, qc: QueryCosts, result: PathResult) -> list[dict]:
    """Locations of flagged unsafe maneuvers (for map markers)."""
    out = []
    for t in result.turn_ids:
        kind = int(qc.turn_unsafe_type[t])
        if kind == 0:
            continue
        node = int(pack.edge_head[pack.turn_in_edge[t]])
        out.append({
            "lon": float(pack.node_lon[node]),
            "lat": float(pack.node_lat[node]),
            "type": "unprotected_left" if kind == 1 else "uncontrolled_crossing",
        })
    return out
