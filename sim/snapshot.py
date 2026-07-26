"""Departure-time traffic snapshot: per-edge speed multipliers and volumes.

The snapshot is computed ONCE per query in Python (numpy) and frozen — edge
costs are static within a single search run (spec). Phase 3 adds the
time-of-day profile lookup; `free_flow` is the profile-independent baseline
(multiplier 1.0 everywhere, base volumes) used by unit tests.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyref.config import Config
from pyref.graph import GraphPack, RoadClass


@dataclass(frozen=True)
class Snapshot:
    speed_mult: np.ndarray       # f64[E], fraction of free-flow speed
    volume_vph_lane: np.ndarray  # f64[E], vehicles/hour/lane
    edge_time_s: np.ndarray      # f64[E], length / (speed_limit * mult)


def _base_volume(pack: GraphPack, cfg: Config) -> np.ndarray:
    table = cfg["sim"]["base_vph_per_lane"]
    by_class = np.zeros(len(RoadClass), dtype=np.float64)
    for rc in RoadClass:
        by_class[rc.value] = float(table[rc.name])
    return by_class[pack.edge_road_class]


def at_time(pack: GraphPack, cfg: Config,
            departure: "datetime.datetime") -> Snapshot:
    """The real departure-time snapshot: profile multipliers by road class,
    applied per edge. Frozen for the whole query (spec)."""
    from sim.profiles import multipliers_at

    speed_by_class, vol_by_class = multipliers_at(cfg, departure)
    mult = speed_by_class[pack.edge_road_class]
    volume = _base_volume(pack, cfg) * vol_by_class[pack.edge_road_class]
    edge_time_s = pack.edge_length_m / (pack.edge_speed_mps * mult)
    return Snapshot(speed_mult=mult, volume_vph_lane=volume,
                    edge_time_s=edge_time_s)


def free_flow(pack: GraphPack, cfg: Config) -> Snapshot:
    """Baseline snapshot: no congestion, base (peak-scale) volumes."""
    mult = np.ones(pack.num_edges, dtype=np.float64)
    volume = _base_volume(pack, cfg)
    edge_time_s = pack.edge_length_m / (pack.edge_speed_mps * mult)
    return Snapshot(speed_mult=mult, volume_vph_lane=volume,
                    edge_time_s=edge_time_s)
