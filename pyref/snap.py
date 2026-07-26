"""Point -> directed-edge snapping, shared by both engines (all in Python).

A cKDTree over geometry-segment midpoints yields candidate segments; exact
point-to-segment projection (in a local equirectangular meter frame — fine at
city scale) picks the best edge. Both directions of a two-way street become
candidates: same snapped point, mirrored fraction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from pyref.geo import EARTH_RADIUS_M, haversine_m
from pyref.graph import GraphPack


@dataclass(frozen=True)
class SnapCandidate:
    edge: int
    frac: float          # position along the DIRECTED edge, by length in [0,1]
    dist_m: float        # distance from query point to snapped point
    lat: float           # snapped point
    lon: float


class SnapIndex:
    def __init__(self, pack: GraphPack):
        self.pack = pack
        self._lat0 = math.radians(float(np.mean(pack.node_lat)))
        self._kx = EARTH_RADIUS_M * math.cos(self._lat0)
        self._ky = EARTH_RADIUS_M

        # explode geometry pool into segments
        E = pack.num_edges
        seg_edge: list[int] = []
        seg_a: list[int] = []          # pool index of segment start
        for e in range(E):
            s, t = pack.edge_geom_ptr[e], pack.edge_geom_ptr[e + 1]
            for i in range(s, t - 1):
                seg_edge.append(e)
                seg_a.append(i)
        self.seg_edge = np.asarray(seg_edge, dtype=np.int32)
        self.seg_a = np.asarray(seg_a, dtype=np.int32)

        ax, ay = self._project(pack.geom_lat[self.seg_a], pack.geom_lon[self.seg_a])
        bx, by = self._project(pack.geom_lat[self.seg_a + 1], pack.geom_lon[self.seg_a + 1])
        self._ax, self._ay, self._bx, self._by = ax, ay, bx, by
        self._tree = cKDTree(np.column_stack([(ax + bx) / 2, (ay + by) / 2]))

        # per-edge cumulative polyline length at each pool point (meters),
        # for converting a segment hit into a fraction along the edge
        seg_len = haversine_m(pack.geom_lat[:-1], pack.geom_lon[:-1],
                              pack.geom_lat[1:], pack.geom_lon[1:])
        # zero out lengths that span edge boundaries in the pool
        boundary = np.zeros(len(pack.geom_lat) - 1, dtype=bool)
        boundary[pack.edge_geom_ptr[1:-1] - 1] = True
        seg_len = np.where(boundary, 0.0, seg_len)
        self._cum = np.concatenate([[0.0], np.cumsum(seg_len)])

    def _project(self, lat, lon):
        return (np.radians(np.asarray(lon, dtype=np.float64)) * self._kx,
                np.radians(np.asarray(lat, dtype=np.float64)) * self._ky)

    def _edge_polyline_len(self, e: int) -> float:
        s, t = self.pack.edge_geom_ptr[e], self.pack.edge_geom_ptr[e + 1]
        return float(self._cum[t - 1] - self._cum[s])

    def snap(self, lat: float, lon: float, k: int = 20,
             max_m: float = 300.0) -> list[SnapCandidate]:
        """Best snap plus the reverse-direction twin (if two-way).
        Empty list if nothing within max_m."""
        px, py = self._project(lat, lon)
        px, py = float(px), float(py)
        k = min(k, len(self.seg_edge))
        _, idx = self._tree.query([px, py], k=k)
        idx = np.atleast_1d(idx)

        best: SnapCandidate | None = None
        for si in idx:
            si = int(si)
            ax, ay = self._ax[si], self._ay[si]
            bx, by = self._bx[si], self._by[si]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            u = 0.0 if L2 == 0.0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
            qx, qy = ax + u * dx, ay + u * dy
            d = math.hypot(px - qx, py - qy)
            if d > max_m or (best is not None and d >= best.dist_m):
                continue
            e = int(self.seg_edge[si])
            s = int(self.pack.edge_geom_ptr[e])
            a = int(self.seg_a[si])
            seg_len = self._cum[a + 1] - self._cum[a]
            along = (self._cum[a] - self._cum[s]) + u * seg_len
            total = self._edge_polyline_len(e)
            frac = 0.0 if total <= 0 else min(1.0, along / total)
            lat_q = math.degrees(qy / self._ky)
            lon_q = math.degrees(qx / self._kx)
            best = SnapCandidate(edge=e, frac=frac, dist_m=d, lat=lat_q, lon=lon_q)

        if best is None:
            return []
        out = [best]
        rev = int(self.pack.edge_reverse[best.edge])
        if rev >= 0:
            out.append(SnapCandidate(edge=rev, frac=1.0 - best.frac,
                                     dist_m=best.dist_m,
                                     lat=best.lat, lon=best.lon))
        return out
