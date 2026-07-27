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
        YIELD + STRAIGHT/RIGHT        -> raw *= yield_row_factor
        YIELD + LEFT/UTURN            -> raw *= yield_factor
        SIGNAL_PERMISSIVE + LEFT      -> raw *= signal_left_factor
        right-of-way STRAIGHT/RIGHT   -> raw *= right_of_way_factor
        INFERRED + unprotected        -> raw *= inferred_confidence_factor
    penalty_seconds = k * max(raw, 0)

Dual output from the same post-override raw score:
    - penalty_seconds enters g via arc_cost = time(next_edge) + lambda*penalty
    - raw >= tau_unsafe (plus per-type predicate) increments unsafe counters
    - tau bands give safe/caution/unsafe tier labels for map coloring

Counting only what OSM actually told us
---------------------------------------
Both unsafe counters require ControlConfidence.OBSERVED on the governing
approach. An approach whose control was guessed by the road-class heuristic in
ingestion/controls.py still carries a (damped) routing penalty, so routes keep
preferring known-controlled intersections, but it never increments the
user-facing count and never renders as an `unsafe` tier — a guess is not
evidence of a hazard. See ingestion/approach_controls.py for where OBSERVED
control comes from.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyref.config import Config
from pyref.graph import (
    ROAD_CLASS_RANK,
    Control,
    ControlConfidence,
    GraphPack,
    Maneuver,
    RoadClass,
)
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
    edge_major: np.ndarray       # bool[E] busy AND physically big
    turn_unsafe_type: np.ndarray  # u8[T]: 0 none / 1 left / 2 crossing
    turn_tier: np.ndarray        # u8[T]: 0 safe / 1 caution / 2 unsafe
    v_max_mps: float             # max free-flow speed in pack (heuristic)


def _norm(x: np.ndarray, cap: float) -> np.ndarray:
    return np.clip(x / cap, 0.0, 1.0)


def _road_class_ranks(edge_road_class: np.ndarray) -> np.ndarray:
    """ROAD_CLASS_RANK per edge (lower = more major). RoadClass enum VALUES are
    not ranks — links share their parent's rank — so this table lookup is not
    the identity."""
    table = np.zeros(len(RoadClass), dtype=np.int64)
    for rc in RoadClass:
        table[rc.value] = ROAD_CLASS_RANK[rc]
    return table[edge_road_class]


def _cross_count(pack: GraphPack, inn: np.ndarray, out: np.ndarray,
                 mask: np.ndarray) -> np.ndarray:
    """Does a STRAIGHT through this node cross a street matching `mask`?

    Documented approximation: it does iff any OTHER incoming approach at the
    node qualifies — excluding our own in-edge and the reverse of our out-edge,
    which are the two legs we are travelling along rather than across.
    """
    node_in = np.zeros(pack.num_nodes, dtype=np.int64)
    np.add.at(node_in, pack.edge_head, mask.astype(np.int64))
    count = node_in[pack.edge_head[inn]] - mask[inn].astype(np.int64)
    rev = pack.edge_reverse[out]
    has_rev = rev >= 0
    count = count - np.where(has_rev, mask[np.where(has_rev, rev, 0)].astype(np.int64), 0)
    return count > 0


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
    must_stop = pack.edge_must_stop[inn] == 1
    observed = pack.edge_control_confidence[inn] == ControlConfidence.OBSERVED
    is_left = pack.turn_maneuver == Maneuver.LEFT

    raw[(ctrl == Control.SIGNAL_PROTECTED) | (ctrl == Control.ROUNDABOUT)] = 0.0
    stop4 = ctrl == Control.STOP_4WAY
    raw[stop4] = raw[stop4] * float(cc["stop4way_factor"])

    straight_or_right = ((pack.turn_maneuver == Maneuver.STRAIGHT)
                         | (pack.turn_maneuver == Maneuver.RIGHT))

    # A give-way approach is split by maneuver rather than by must_stop. What a
    # yield sign actually constrains is the movement that crosses conflicting
    # traffic — the left. Going straight or right past one is much closer to a
    # right-of-way movement than to a stop-and-wait, so it takes its own factor
    # instead of stacking yield_factor with right_of_way_factor (which would
    # price it below a green light) or neither (which priced it 3x above one).
    yielding = ctrl == Control.YIELD
    yield_row = yielding & straight_or_right
    raw[yield_row] = raw[yield_row] * float(cc["yield_row_factor"])
    yield_conflict = yielding & ~straight_or_right
    raw[yield_conflict] = raw[yield_conflict] * float(cc["yield_factor"])

    # Right-of-way reduction (see config comment): STRAIGHT/RIGHT where cross
    # traffic is held — permissive signal, or a 2-way stop where THIS approach
    # has priority. LEFT is excluded; it gets its own treatment below. The
    # unsafe COUNTER predicates exclude these cases too, so the factor only
    # shapes the continuous cost, keeping penalty and counters aligned.
    has_row = ((ctrl == Control.SIGNAL_PERMISSIVE)
               | ((ctrl == Control.STOP_2WAY) & ~must_stop))
    row_reduce = has_row & straight_or_right
    raw[row_reduce] = raw[row_reduce] * float(cc["right_of_way_factor"])

    # A left at a signal is discounted, not exonerated: OSM cannot say whether
    # the left has a protected arrow, and steering routes toward signalized
    # intersections is the point of this planner. The factor is tuned to land
    # this in the caution band, so the turn stays visible on the map while
    # dropping out of the unsafe count (the counter below excludes it too).
    signal_left = (ctrl == Control.SIGNAL_PERMISSIVE) & is_left
    raw[signal_left] = raw[signal_left] * float(cc["signal_left_factor"])

    # An approach whose control was guessed rather than tagged should not be
    # asserted dangerous. Damp the penalty exactly where the guess is what makes
    # the maneuver expensive — an approach with no protection. Guesses that
    # claim protection (an inferred STOP_4WAY, say) keep their score unchanged;
    # letting a guess buy a discount would be the opposite error.
    unprotected = (ctrl == Control.NONE) | (
        ((ctrl == Control.STOP_2WAY) | yielding) & must_stop)
    guessed = unprotected & ~observed
    raw[guessed] = raw[guessed] * float(cc["inferred_confidence_factor"])

    penalty = float(cc["k_penalty_scale_s"]) * np.maximum(raw, 0.0)
    is_uturn = pack.turn_maneuver == Maneuver.UTURN
    penalty[is_uturn] += float(cc["uturn_fixed_penalty_s"])

    # --- busy roads (tunable combination of speed, lanes, volume) ---
    edge_busy = (bc["a_speed"] * spd_n + bc["a_lanes"] * lanes_n
                 + bc["a_vol"] * vol_n) > bc["busy_threshold"]
    # "Major" narrows busy to roads that are also physically big — the ones a
    # driver holding a stop sign has to cross several lanes of. A stop-sign
    # left onto a busy but ordinary 2-lane street is not counted.
    edge_major = edge_busy & (
        (pack.edge_lanes >= float(bc["major_lanes_min"]))
        | (_road_class_ranks(pack.edge_road_class)
           <= ROAD_CLASS_RANK[RoadClass[str(bc["major_class_max"])]])
    )

    # --- unsafe-action counters (spec predicates) ---
    tau = float(tc["tau_unsafe"])
    over_tau = raw >= tau

    # Holding the stop sign or yield line at a 2-way stop means cross traffic
    # does NOT stop — no protection at all. This is the "pull up to an arterial
    # from a side street and squeeze into a gap" case.
    unprotected_approach = ((ctrl == Control.STOP_2WAY) | yielding) & must_stop

    unprotected_left = (
        is_left
        & observed
        & over_tau
        & (((ctrl == Control.NONE) & edge_busy[out])
           | (unprotected_approach & edge_major[out]))
    )

    uncontrolled_crossing = (
        (pack.turn_maneuver == Maneuver.STRAIGHT)
        & observed
        & over_tau
        & (((ctrl == Control.NONE) & _cross_count(pack, inn, out, edge_busy))
           | (unprotected_approach & _cross_count(pack, inn, out, edge_major)))
    )

    unsafe_type = np.zeros(pack.num_turns, dtype=np.uint8)
    unsafe_type[uncontrolled_crossing] = UNSAFE_CROSSING
    unsafe_type[unprotected_left] = UNSAFE_LEFT  # left wins if both somehow fire

    # --- tier bands for map coloring ---
    tier = np.zeros(pack.num_turns, dtype=np.uint8)
    tier[raw >= float(tc["tau_caution"])] = TIER_CAUTION
    tier[over_tau] = TIER_UNSAFE
    # Never paint a guess red. An inferred approach can still reach the unsafe
    # band on target-edge attributes alone; cap it at caution so the map and the
    # (observed-only) count tell the same story.
    tier[(tier == TIER_UNSAFE) & ~observed] = TIER_CAUTION

    return QueryCosts(
        edge_time_s=snap.edge_time_s,
        turn_penalty_s=penalty,
        turn_raw=raw,
        edge_busy=edge_busy,
        edge_major=edge_major,
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
