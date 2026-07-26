"""Turn-table construction: the ONLY place bearing geometry is computed.

Both engines consume the resulting uint8 maneuver codes — deriving turns at
engine load would need two independently-correct bearing implementations,
the single most likely source of pyref/C++ divergence.

Conventions:
  - Bearings are initial great-circle azimuths in degrees, clockwise from
    north, in [0, 360).
  - delta = wrap(bearing_in(out_edge) - bearing_out(in_edge)) to (-180, 180].
    Positive delta = clockwise = RIGHT; negative = LEFT.
  - |delta| < 30        -> STRAIGHT
  - |delta| > 150       -> UTURN   (also forced when out == reverse(in))
  - otherwise LEFT/RIGHT by sign.
  - U-turns are disallowed except where the in-edge's head has exactly one
    outgoing edge (dead end) — without that allowance a destination up a
    cul-de-sac would be unreachable from the "wrong" approach direction.
"""
from __future__ import annotations

import math

import numpy as np

from pyref.graph import Maneuver

STRAIGHT_MAX_DEG = 30.0
UTURN_MIN_DEG = 150.0


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees [0, 360)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360.0


def wrap_delta_deg(delta: float) -> float:
    """Wrap a bearing difference to (-180, 180]."""
    d = (delta + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def classify_maneuver(delta_deg: float, is_reverse_pair: bool) -> Maneuver:
    if is_reverse_pair or abs(delta_deg) > UTURN_MIN_DEG:
        return Maneuver.UTURN
    if abs(delta_deg) < STRAIGHT_MAX_DEG:
        return Maneuver.STRAIGHT
    return Maneuver.RIGHT if delta_deg > 0 else Maneuver.LEFT


def edge_bearings(geom_lat: np.ndarray, geom_lon: np.ndarray,
                  geom_ptr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-edge (bearing_in, bearing_out) from first/last distinct polyline
    points. Falls back to the overall endpoint bearing for degenerate
    (zero-length) segments."""
    E = len(geom_ptr) - 1
    b_in = np.zeros(E, dtype=np.float64)
    b_out = np.zeros(E, dtype=np.float64)
    for e in range(E):
        s, t = geom_ptr[e], geom_ptr[e + 1]
        lats, lons = geom_lat[s:t], geom_lon[s:t]
        n = len(lats)
        # first distinct point after the start
        j = 1
        while j < n - 1 and lats[j] == lats[0] and lons[j] == lons[0]:
            j += 1
        b_in[e] = initial_bearing_deg(lats[0], lons[0], lats[j], lons[j])
        # last distinct point before the end
        j = n - 2
        while j > 0 and lats[j] == lats[-1] and lons[j] == lons[-1]:
            j -= 1
        b_out[e] = initial_bearing_deg(lats[j], lons[j], lats[-1], lons[-1])
    return b_in, b_out


def build_turn_table(edge_tail: np.ndarray, edge_head: np.ndarray,
                     edge_reverse: np.ndarray,
                     node_out_ptr: np.ndarray,
                     bearing_in: np.ndarray, bearing_out: np.ndarray,
                     ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """CSR turn table keyed by incoming edge.

    Edges are already sorted by tail node, so `node_out_ptr[n]:node_out_ptr[n+1]`
    enumerates out-edges of node n in deterministic (ascending edge id) order.
    Returns (turn_ptr, turn_out_edge, turn_maneuver, turn_angle_deg, turn_allowed).
    """
    E = len(edge_tail)
    turn_ptr = np.zeros(E + 1, dtype=np.int32)
    out_edges: list[int] = []
    maneuvers: list[int] = []
    angles: list[float] = []
    allowed: list[int] = []

    for e in range(E):
        head = edge_head[e]
        lo, hi = node_out_ptr[head], node_out_ptr[head + 1]
        out_degree = hi - lo
        for e2 in range(lo, hi):
            delta = wrap_delta_deg(bearing_in[e2] - bearing_out[e])
            is_rev = edge_reverse[e] == e2
            man = classify_maneuver(delta, bool(is_rev))
            ok = 1
            if man == Maneuver.UTURN and out_degree > 1:
                ok = 0  # U-turns only allowed at dead ends
            out_edges.append(e2)
            maneuvers.append(int(man))
            angles.append(delta)
            allowed.append(ok)
        turn_ptr[e + 1] = len(out_edges)

    return (turn_ptr,
            np.asarray(out_edges, dtype=np.int32),
            np.asarray(maneuvers, dtype=np.uint8),
            np.asarray(angles, dtype=np.float64),
            np.asarray(allowed, dtype=np.uint8))
