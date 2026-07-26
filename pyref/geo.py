"""Geodesic helpers (numpy-vectorized). Used by ingestion, snapping and the
A* heuristic precompute — never inside the engine hot loops."""
from __future__ import annotations

import numpy as np

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in meters. Accepts scalars or numpy arrays."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=np.float64))
                              for x in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    return EARTH_RADIUS_M * 2.0 * np.arcsin(np.sqrt(a))
