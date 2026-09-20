"""Departure-time traffic snapshot: per-edge speed multipliers and volumes.

The snapshot is computed ONCE per query in Python (numpy) and frozen — edge
costs are static within a single search run (spec). Phase 3 adds the
time-of-day profile lookup; `free_flow` is the profile-independent baseline
(multiplier 1.0 everywhere, base volumes) used by unit tests.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

import numpy as np

from pyref.config import Config
from pyref.graph import GraphPack, RoadClass

# The traffic source identifier this module stamps on its snapshots. A
# CONSTANT, deliberately not a config knob: it names which code path actually
# produced the numbers, and a knob could be set to "inrix" while these
# hand-authored profiles were still running. Provenance that can lie is worse
# than none. A real feed (ADR-0010's triggers) arrives as a new generator
# beside `at_time` with its own identifier, which is the "data swap, not an
# architecture change" that ADR-0010 promises: a VALUE change at this key, not
# a shape change.
SYNTHETIC_SOURCE = "synthetic"


@dataclass(frozen=True)
class TrafficBasis:
    """The recorded provenance of the traffic inputs a snapshot was computed
    against (CONTEXT.md "traffic basis"; ADR-0004 schema v2). Rides in the
    route artifact's `preference` so a consumer diffing two artifacts can tell
    "traffic changed" from "these were computed against different data".

    `as_of` is when the traffic inputs were OBSERVED. **Under the synthetic
    model that is exactly `departure`**, because `sim.profiles.multipliers_at`
    reads nothing but the departure clock — so today this field duplicates the
    preference's `departure_time` and carries no independent information. That
    is ADR-0010's "two replans for the same departure time are byte-identical,
    so the diff can never fire", stated at the contract instead of buried. The
    field is shaped for the real-feed case, where `as_of` is when the feed
    observed the network and is unrelated to when the user plans to leave; the
    duplication ends there, with no wire change.

    `profile_version` is the half that IS information today: a content hash of
    the `[sim]` table (`Config.sim_profile_version`), so "somebody retuned the
    synthetic profiles" is detectable from two artifacts alone. Without it the
    basis would be legible in name only while the only thing that can actually
    move under a deterministic model stayed invisible.
    """
    source: str                      # e.g. "synthetic"
    as_of: datetime.datetime         # when the inputs were observed
    profile_version: str             # content hash of the generating profiles


@dataclass(frozen=True)
class Snapshot:
    speed_mult: np.ndarray       # f64[E], fraction of free-flow speed
    volume_vph_lane: np.ndarray  # f64[E], vehicles/hour/lane
    edge_time_s: np.ndarray      # f64[E], length / (speed_limit * mult)
    # None only for `free_flow`, which is a clock-less baseline and never
    # reaches an artifact; every departure-time snapshot carries one.
    basis: TrafficBasis | None = None


def _base_volume(pack: GraphPack, cfg: Config) -> np.ndarray:
    table = cfg["sim"]["base_vph_per_lane"]
    by_class = np.zeros(len(RoadClass), dtype=np.float64)
    for rc in RoadClass:
        by_class[rc.value] = float(table[rc.name])
    return by_class[pack.edge_road_class]


def at_time(pack: GraphPack, cfg: Config, departure: datetime.datetime) -> Snapshot:
    """The real departure-time snapshot: profile multipliers by road class,
    applied per edge. Frozen for the whole query (spec)."""
    from sim.profiles import multipliers_at

    speed_by_class, vol_by_class = multipliers_at(cfg, departure)
    mult = speed_by_class[pack.edge_road_class]
    volume = _base_volume(pack, cfg) * vol_by_class[pack.edge_road_class]
    edge_time_s = pack.edge_length_m / (pack.edge_speed_mps * mult)
    # The basis is minted HERE, next to the inputs it describes, not re-derived
    # downstream from (cfg, departure): that is what keeps the eventual feed
    # swap confined to this module (ADR-0010).
    return Snapshot(speed_mult=mult, volume_vph_lane=volume,
                    edge_time_s=edge_time_s,
                    basis=TrafficBasis(source=SYNTHETIC_SOURCE,
                                       as_of=departure,
                                       profile_version=cfg.sim_profile_version))


def free_flow(pack: GraphPack, cfg: Config) -> Snapshot:
    """Baseline snapshot: no congestion, base (peak-scale) volumes.

    No `basis`: this is not a departure-time snapshot — there is no clock to
    record — and it is used only by unit tests, benchmarks and scripts, never
    on the path that emits a route artifact.
    """
    mult = np.ones(pack.num_edges, dtype=np.float64)
    volume = _base_volume(pack, cfg)
    edge_time_s = pack.edge_length_m / (pack.edge_speed_mps * mult)
    return Snapshot(speed_mult=mult, volume_vph_lane=volume,
                    edge_time_s=edge_time_s)
