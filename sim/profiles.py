"""Deterministic synthetic time-of-day traffic profiles.

Config ([sim]) defines road-class groups (arterial / collector / local), each
with 24 hourly multipliers for speed and volume. The value for hour h is
anchored at the half-hour center h+0.5; lookups interpolate piecewise-
linearly between adjacent centers, wrapping 23.5 -> 0.5 across midnight.
No RNG anywhere — the same departure time always yields the same snapshot.
"""
from __future__ import annotations

import datetime

import numpy as np

from pyref.config import Config
from pyref.graph import RoadClass


def interpolate_hourly(table: list[float], hour_of_day: float) -> float:
    """Piecewise-linear lookup in a 24-entry hourly table (values anchored at
    half-hour centers), wrapping around midnight."""
    assert len(table) == 24
    x = (hour_of_day - 0.5) % 24.0     # distance past the most recent center
    i = int(x)                          # center index
    frac = x - i
    a = table[i % 24]
    b = table[(i + 1) % 24]
    return a + frac * (b - a)


def class_group(rc: RoadClass, cfg: Config) -> str:
    for group, members in cfg["sim"]["class_groups"].items():
        if rc.name in members:
            return group
    return "local"


def multipliers_at(cfg: Config, departure: datetime.datetime
                   ) -> tuple[np.ndarray, np.ndarray]:
    """(speed_mult_by_class, volume_mult_by_class) — f64 arrays indexed by
    RoadClass value, for the given departure time."""
    hour = departure.hour + departure.minute / 60.0 + departure.second / 3600.0
    speed_by_class = np.ones(len(RoadClass), dtype=np.float64)
    vol_by_class = np.ones(len(RoadClass), dtype=np.float64)
    for rc in RoadClass:
        profile = cfg["sim"]["profiles"][class_group(rc, cfg)]
        speed_by_class[rc.value] = interpolate_hourly(profile["speed_mult"], hour)
        vol_by_class[rc.value] = interpolate_hourly(profile["volume_mult"], hour)
    return speed_by_class, vol_by_class
