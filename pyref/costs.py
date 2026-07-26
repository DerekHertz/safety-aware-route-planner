"""Per-query cost precompute — the parity linchpin.

Everything floating-point-heavy happens HERE, once, in numpy: safety scores,
penalties, busy masks, unsafe predicates, tiers, arc costs and the A*
heuristic. The engines (pyref and C++) receive finished float64 arrays and do
nothing but add them, so both engines see bit-identical numbers.

Spec cost model:
    raw = w_man*severity + w_speed*norm(speed) + w_lanes*norm(lanes)
        + w_vol*norm(volume) - w_med*median          (attrs of the TARGET edge)
    control override (control governing this approach at the intersection):
        SIGNAL_PROTECTED / ROUNDABOUT -> raw = 0
        STOP_4WAY                     -> raw *= stop4way_factor
    penalty_seconds = k * max(raw, 0)

Dual output from the same post-override raw score:
    - penalty_seconds enters g via arc_cost = time(next_edge) + lambda*penalty
    - raw > tau_unsafe (plus per-type predicate) increments unsafe counters
    - tau bands give safe/caution/unsafe tier labels for map coloring
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyref.config import Config
from pyref.graph import Control, GraphPack, Maneuver
from pyref.geo import haversine_m
from sim.snapshot import Snapshot

UNSAFE_NONE = 0
UNSAFE_LEFT = 1        # unprotected left onto a busy street
UNSAFE_CROSSING = 2    # uncontrolled crossing of a busy street

TIER_SAFE = 0
TIER_CAUTION = 1
TIER_UNSAFE = 2


@dataclass(frozen=True)
class QueryCosts:
    """Frozen per-(pack, snapshot) arrays consumed by engines and metrics."""
    edge_time_s: np.ndarray      # f64[E]
    turn_penalty_s: np.ndarray   # f64[T]
    turn_raw: np.ndarray         # f64[T] post-override raw score (debug/tiers)
    edge_busy: np.ndarray        # bool[E]
    turn_unsafe_type: np.ndarray  # u8[T]: 0 none / 1 left / 2 crossing
    turn_tier: np.ndarray        # u8[T]: 0 safe / 1 caution / 2 unsafe
    v_max_mps: float             # max free-flow speed in pack (heuristic)


def _norm(x: np.ndarray, cap: float) -> np.ndarray:
    return np.clip(x / cap, 0.0, 1.0)


def compute_costs(pack: GraphPack, snap: Snapshot, cfg: Config) -> QueryCosts:
    cc = cfg["cost"]
    bc = cfg["busy"]
    tc = cfg["tiers"]

    out = pack.turn_out_edge
    inn = pack.turn_in_edge

    # --- target-edge attributes, gathered per turn ---
    spd_n = _norm(pack.edge_speed_mps, cc["speed_norm_max_mps"])
    lanes_n = _norm(pack.edge_lanes, cc["lanes_norm_max"])
    vol_n = _norm(snap.volume_vph_lane, cc["vol_norm_max_vph"])

    sev_table = np.zeros(len(Maneuver), dtype=np.float64)
    for m in Maneuver:
        sev_table[m.value] = float(cc["severity"][m.name])
    sev = sev_table[pack.turn_maneuver]

    raw = (cc["w_man"] * sev
           + cc["w_speed"] * spd_n[out]
           + cc["w_lanes"] * lanes_n[out]
           + cc["w_vol"] * vol_n[out]
           - cc["w_med"] * pack.edge_median[out].astype(np.float64))

    # --- control override: the control that governs this maneuver is the one
    # facing the incoming approach at the intersection (head of in-edge) ---
    ctrl = pack.edge_approach_control[inn]
    raw[(ctrl == Control.SIGNAL_PROTECTED) | (ctrl == Control.ROUNDABOUT)] = 0.0
    stop4 = ctrl == Control.STOP_4WAY
    raw[stop4] = raw[stop4] * float(cc["stop4way_factor"])

    # Right-of-way reduction (see config comment): STRAIGHT/RIGHT where cross
    # traffic is held — permissive signal, or 2-way stop with priority.
    # LEFT keeps full raw (permissive left = the named hazard). The unsafe
    # COUNTER predicates below already exclude these cases, so this factor
    # only shapes the continuous cost, keeping penalty and counters aligned.
    has_row = ((ctrl == Control.SIGNAL_PERMISSIVE)
               | ((ctrl == Control.STOP_2WAY) & (pack.edge_must_stop[inn] == 0)))
    row_reduce = has_row & ((pack.turn_maneuver == Maneuver.STRAIGHT)
                            | (pack.turn_maneuver == Maneuver.RIGHT))
    raw[row_reduce] = raw[row_reduce] * float(cc["right_of_way_factor"])

    penalty = float(cc["k_penalty_scale_s"]) * np.maximum(raw, 0.0)
    is_uturn = pack.turn_maneuver == Maneuver.UTURN
    penalty[is_uturn] += float(cc["uturn_fixed_penalty_s"])

    # --- busy roads (tunable combination of speed, lanes, volume) ---
    edge_busy = (bc["a_speed"] * spd_n + bc["a_lanes"] * lanes_n
                 + bc["a_vol"] * vol_n) > bc["busy_threshold"]

    # --- unsafe-action counters (spec predicates) ---
    tau = float(tc["tau_unsafe"])
    over_tau = raw > tau

    unprotected_left = (
        (pack.turn_maneuver == Maneuver.LEFT)
        & edge_busy[out]
        & ((ctrl == Control.NONE) | (ctrl == Control.SIGNAL_PERMISSIVE))
        & over_tau
    )

    # Crossing-street busyness approximation (documented): a STRAIGHT through
    # a node crosses a busy street iff any OTHER incoming approach at that
    # node (not our own in-edge, not the reverse of our out-edge) is busy.
    node_busy_in = np.zeros(pack.num_nodes, dtype=np.int64)
    np.add.at(node_busy_in, pack.edge_head, edge_busy.astype(np.int64))
    at_node = pack.edge_head[inn]
    cross_count = node_busy_in[at_node] - edge_busy[inn].astype(np.int64)
    rev = pack.edge_reverse[out]
    has_rev = rev >= 0
    cross_count = cross_count - np.where(has_rev, edge_busy[np.where(has_rev, rev, 0)].astype(np.int64), 0)
    cross_busy = cross_count > 0

    uncontrolled_crossing = (
        (pack.turn_maneuver == Maneuver.STRAIGHT)
        & cross_busy
        & ((ctrl == Control.NONE)
           | ((ctrl == Control.STOP_2WAY) & (pack.edge_must_stop[inn] == 1)))
        & over_tau
    )

    unsafe_type = np.zeros(pack.num_turns, dtype=np.uint8)
    unsafe_type[uncontrolled_crossing] = UNSAFE_CROSSING
    unsafe_type[unprotected_left] = UNSAFE_LEFT  # left wins if both somehow fire

    # --- tier bands for map coloring ---
    tier = np.zeros(pack.num_turns, dtype=np.uint8)
    tier[raw >= float(tc["tau_caution"])] = TIER_CAUTION
    tier[raw >= tau] = TIER_UNSAFE

    return QueryCosts(
        edge_time_s=snap.edge_time_s,
        turn_penalty_s=penalty,
        turn_raw=raw,
        edge_busy=edge_busy,
        turn_unsafe_type=unsafe_type,
        turn_tier=tier,
        v_max_mps=float(pack.edge_speed_mps.max()),
    )


def arc_cost(pack: GraphPack, qc: QueryCosts, lam: float) -> np.ndarray:
    """Per-turn generalized cost: travel time of the target edge plus the
    lambda-weighted safety penalty. The ONLY place lambda enters."""
    return qc.edge_time_s[pack.turn_out_edge] + lam * qc.turn_penalty_s


def heuristic(pack: GraphPack, qc: QueryCosts,
              dest_lat: float, dest_lon: float) -> np.ndarray:
    """Admissible A* heuristic: straight-line time to destination at the
    pack-wide max FREE-FLOW speed. Congestion only slows edges and penalties
    are >= 0, so this lower-bounds generalized cost for every lambda. Never
    includes any safety term (that would break admissibility)."""
    d = haversine_m(pack.node_lat[pack.edge_head], pack.node_lon[pack.edge_head],
                    dest_lat, dest_lon)
    return d / qc.v_max_mps
