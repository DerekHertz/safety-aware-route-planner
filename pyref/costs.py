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

What is per-pack and what is per-query (ADR-0009, 2026-09-18)
------------------------------------------------------------
The departure time reaches this model through exactly one term: normalized
volume. Everything else — severity, the speed and lanes norms, the median
term, every control-override mask, the busy floor, the "physically big" test,
the unsafe-predicate masks and the index arrays `_cross_count` gathers through
— is a function of (pack, cfg) alone, and used to be rebuilt on every request.
`PackStatics` holds that half, built once per pack (see `Router.__init__`);
`compute_costs` computes it on demand when a caller does not supply it, so
every existing call site keeps working, just without the saving.

This split is written to be BITWISE neutral, not merely equivalent, because
the Python/C++ parity guarantee rests on both engines consuming the same
finished arrays (ADR-0009 explains why laziness, the alternative, cannot be).
Two rules make that hold, and tests/test_costs_golden.py enforces it:

  * **Summation order is preserved.** Only a left-to-right *prefix* of each sum
    is hoisted, so the remaining terms are added in the original order. Float
    addition is not associative; re-grouping a sum is a real change.
  * **Multiplications are folded only across disjoint masks.** Five of the six
    override stages are mutually exclusive by control type, so collapsing them
    into one multiplier array applies at most one factor per turn — identical
    to applying them in sequence. `inferred_confidence_factor` can stack on top
    of a yield factor, so it stays a second, separate multiply rather than
    being pre-multiplied into the first (a*b applied at once is not bitwise
    equal to a then b). `_assert_disjoint` checks the premise at build time.

The protected-control override stays an assignment of exactly `+0.0`. Folding
it in as a `* 0.0` would give `-0.0` wherever the pre-override score was
negative, which is a different bit pattern; see `_median_cross` in the golden
test for the case that reaches it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pyref.config import Config
from pyref.geo import haversine_m
from pyref.graph import (
    ROAD_CLASS_RANK,
    Control,
    ControlConfidence,
    GraphPack,
    Maneuver,
    RoadClass,
)
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
    turn_time_s: np.ndarray      # f64[T] edge_time_s gathered to turn targets
    turn_penalty_s: np.ndarray   # f64[T]
    turn_raw: np.ndarray         # f64[T] post-override raw score (debug/tiers)
    edge_busy: np.ndarray        # bool[E]
    edge_major: np.ndarray       # bool[E] busy AND physically big
    turn_unsafe_type: np.ndarray  # u8[T]: 0 none / 1 left / 2 crossing
    turn_tier: np.ndarray        # u8[T]: 0 safe / 1 caution / 2 unsafe
    v_max_mps: float             # max free-flow speed in pack (heuristic)


@dataclass(frozen=True)
class PackStatics:
    """The (pack, cfg)-only half of the cost model, built once per pack.

    Nothing here depends on the departure time. Build it with
    `build_pack_statics` and hand it to `compute_costs`; the module docstring
    explains which algebraic liberties were and were not taken.
    """
    # --- per edge [E] ---
    spd_n: np.ndarray            # f64, norm(speed)
    lanes_n: np.ndarray          # f64, norm(lanes)
    busy_base: np.ndarray        # f64, the volume-free prefix of busy score
    busy_floor: np.ndarray       # f64, per-class lower bound (ADR-0005)
    edge_big: np.ndarray         # bool, "physically big" half of edge_major
    v_max_mps: float

    # --- per turn [T] ---
    raw_base: np.ndarray         # f64, the volume-free prefix of raw
    med_term: np.ndarray         # f64, w_med * median(target), subtracted last
    protected: np.ndarray        # bool, raw is forced to +0.0
    control_mult: np.ndarray     # f64, the five disjoint override factors
    guessed_mult: np.ndarray     # f64, inferred_confidence_factor, applied after
    is_uturn: np.ndarray         # bool
    is_left: np.ndarray          # bool
    is_straight: np.ndarray      # bool
    observed: np.ndarray         # bool, governing approach was OSM-tagged
    ctrl_none: np.ndarray        # bool, no control at all on the approach
    unprotected_approach: np.ndarray  # bool, holds a stop/yield line

    # --- index arrays _cross_count would otherwise re-gather per request ---
    in_head: np.ndarray          # i32[T], node this maneuver happens at
    out_rev: np.ndarray          # i32[T], reverse of the out-edge (0 where none)
    out_has_rev: np.ndarray      # bool[T]


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


def _busy_floor_by_class(bc, edge_road_class: np.ndarray) -> np.ndarray:
    """busy_floor_by_class[class] per edge (ADR-0005). A class with no entry
    in config.toml floors at 0.0 — i.e. no floor, today's behaviour."""
    floors = bc.get("busy_floor_by_class", {})
    table = np.zeros(len(RoadClass), dtype=np.float64)
    for rc in RoadClass:
        table[rc.value] = float(floors.get(rc.name, 0.0))
    return table[edge_road_class]


def _assert_disjoint(masks: list[np.ndarray]) -> None:
    """Collapsing several `raw[mask] *= factor` stages into one multiplier
    array is bitwise-safe only while no turn is hit twice: `(x*a)*b` and
    `x*(a*b)` differ in the last ulp. The override ladder is disjoint by
    construction (each stage keys off a different Control), so this is a
    guard on the config and the enum staying that way, paid once per pack."""
    total = np.zeros(len(masks[0]), dtype=np.int64)
    for m in masks:
        total += m
    assert int(total.max(initial=0)) <= 1, (
        "control-override masks overlap; they can no longer be folded into a "
        "single multiplier without changing results bitwise")


def build_pack_statics(pack: GraphPack, cfg: Config) -> PackStatics:
    """Everything in the cost model that a departure time cannot change."""
    cc = cfg["cost"]
    bc = cfg["busy"]

    out = pack.turn_out_edge
    inn = pack.turn_in_edge

    spd_n = _norm(pack.edge_speed_mps, cc["speed_norm_max_mps"])
    lanes_n = _norm(pack.edge_lanes, cc["lanes_norm_max"])

    sev_table = np.zeros(len(Maneuver), dtype=np.float64)
    for m in Maneuver:
        sev_table[m.value] = float(cc["severity"][m.name])
    sev = sev_table[pack.turn_maneuver]

    # The leading three terms of `raw`, in their original order. The volume
    # term is added to this at query time and the median term subtracted after,
    # exactly as the single expression used to evaluate.
    raw_base = (cc["w_man"] * sev
                + cc["w_speed"] * spd_n[out]
                + cc["w_lanes"] * lanes_n[out])
    med_term = cc["w_med"] * pack.edge_median[out].astype(np.float64)

    # --- control override: the control that governs this maneuver is the one
    # facing the incoming approach at the intersection (head of in-edge) ---
    ctrl = pack.edge_approach_control[inn]
    must_stop = pack.edge_must_stop[inn] == 1
    observed = pack.edge_control_confidence[inn] == ControlConfidence.OBSERVED
    is_left = pack.turn_maneuver == Maneuver.LEFT

    protected = (ctrl == Control.SIGNAL_PROTECTED) | (ctrl == Control.ROUNDABOUT)
    stop4 = ctrl == Control.STOP_4WAY

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
    yield_conflict = yielding & ~straight_or_right

    # Right-of-way reduction (see config comment): STRAIGHT/RIGHT where cross
    # traffic is held — permissive signal, or a 2-way stop where THIS approach
    # has priority. LEFT is excluded; it gets its own treatment below. The
    # unsafe COUNTER predicates exclude these cases too, so the factor only
    # shapes the continuous cost, keeping penalty and counters aligned.
    has_row = ((ctrl == Control.SIGNAL_PERMISSIVE)
               | ((ctrl == Control.STOP_2WAY) & ~must_stop))
    row_reduce = has_row & straight_or_right

    # A left at a signal is discounted, not exonerated: OSM cannot say whether
    # the left has a protected arrow, and steering routes toward signalized
    # intersections is the point of this planner. The factor is tuned to land
    # this in the caution band, so the turn stays visible on the map while
    # dropping out of the unsafe count (the counter below excludes it too).
    signal_left = (ctrl == Control.SIGNAL_PERMISSIVE) & is_left

    _assert_disjoint([stop4, yield_row, yield_conflict, row_reduce, signal_left])
    control_mult = np.ones(pack.num_turns, dtype=np.float64)
    control_mult[stop4] = float(cc["stop4way_factor"])
    control_mult[yield_row] = float(cc["yield_row_factor"])
    control_mult[yield_conflict] = float(cc["yield_factor"])
    control_mult[row_reduce] = float(cc["right_of_way_factor"])
    control_mult[signal_left] = float(cc["signal_left_factor"])

    # An approach whose control was guessed rather than tagged should not be
    # asserted dangerous. Damp the penalty exactly where the guess is what makes
    # the maneuver expensive — an approach with no protection. Guesses that
    # claim protection (an inferred STOP_4WAY, say) keep their score unchanged;
    # letting a guess buy a discount would be the opposite error.
    ctrl_none = ctrl == Control.NONE
    unprotected = ctrl_none | (
        ((ctrl == Control.STOP_2WAY) | yielding) & must_stop)
    guessed = unprotected & ~observed
    # Kept separate from control_mult: `guessed` legitimately overlaps the
    # yield stages, so the two factors must land as two multiplies.
    guessed_mult = np.ones(pack.num_turns, dtype=np.float64)
    guessed_mult[guessed] = float(cc["inferred_confidence_factor"])

    # "Major" narrows busy to roads that are also physically big — the ones a
    # driver holding a stop sign has to cross several lanes of. A stop-sign
    # left onto a busy but ordinary 2-lane street is not counted.
    edge_big = (
        (pack.edge_lanes >= float(bc["major_lanes_min"]))
        | (_road_class_ranks(pack.edge_road_class)
           <= ROAD_CLASS_RANK[RoadClass[str(bc["major_class_max"])]])
    )

    rev = pack.edge_reverse[out]
    has_rev = rev >= 0

    return PackStatics(
        spd_n=spd_n,
        lanes_n=lanes_n,
        busy_base=bc["a_speed"] * spd_n + bc["a_lanes"] * lanes_n,
        busy_floor=_busy_floor_by_class(bc, pack.edge_road_class),
        edge_big=edge_big,
        v_max_mps=float(pack.edge_speed_mps.max()),
        raw_base=raw_base,
        med_term=med_term,
        protected=protected,
        control_mult=control_mult,
        guessed_mult=guessed_mult,
        is_uturn=pack.turn_maneuver == Maneuver.UTURN,
        is_left=is_left,
        is_straight=pack.turn_maneuver == Maneuver.STRAIGHT,
        observed=observed,
        ctrl_none=ctrl_none,
        # Holding the stop sign or yield line at a 2-way stop means cross
        # traffic does NOT stop — no protection at all. This is the "pull up to
        # an arterial from a side street and squeeze into a gap" case.
        unprotected_approach=(((ctrl == Control.STOP_2WAY) | yielding)
                              & must_stop),
        in_head=pack.edge_head[inn],
        out_rev=np.where(has_rev, rev, 0),
        out_has_rev=has_rev,
    )


def _cross_count(pack: GraphPack, st: PackStatics, inn: np.ndarray,
                 mask: np.ndarray) -> np.ndarray:
    """Does a STRAIGHT through this node cross a street matching `mask`?

    Documented approximation: it does iff any OTHER incoming approach at the
    node qualifies — excluding our own in-edge and the reverse of our out-edge,
    which are the two legs we are travelling along rather than across.
    """
    # `np.add.at` rather than a bincount over the masked heads: the latter is
    # the same integer histogram and avoids the unbuffered-scatter slow path,
    # but it was measured SLOWER in situ here (0.052 s -> 0.066 s over 40
    # requests), because at E ~= 20k the boolean-mask gather it needs costs
    # more than the scatter it saves. Left as it was.
    node_in = np.zeros(pack.num_nodes, dtype=np.int64)
    np.add.at(node_in, pack.edge_head, mask.astype(np.int64))
    count = node_in[st.in_head] - mask[inn].astype(np.int64)
    count = count - np.where(st.out_has_rev,
                             mask[st.out_rev].astype(np.int64), 0)
    return count > 0


def compute_costs(pack: GraphPack, snap: Snapshot, cfg: Config,
                  statics: PackStatics | None = None) -> QueryCosts:
    """The per-query half. `statics` is built on demand when omitted, which
    keeps every caller working; `Router` builds it once and passes it in."""
    cc = cfg["cost"]
    bc = cfg["busy"]
    tc = cfg["tiers"]
    st = statics if statics is not None else build_pack_statics(pack, cfg)

    out = pack.turn_out_edge
    inn = pack.turn_in_edge

    # --- the one term a departure time can move ---
    vol_n = _norm(snap.volume_vph_lane, cc["vol_norm_max_vph"])

    # Same order as the original single expression: the hoisted prefix, then
    # the volume term added, then the median term subtracted.
    raw = (st.raw_base + cc["w_vol"] * vol_n[out]) - st.med_term

    # Protected controls are an assignment, never a multiply — `* 0.0` would
    # produce -0.0 where raw was negative.
    raw = np.where(st.protected, 0.0, raw)
    raw = raw * st.control_mult
    raw = raw * st.guessed_mult

    penalty = float(cc["k_penalty_scale_s"]) * np.maximum(raw, 0.0)
    penalty[st.is_uturn] += float(cc["uturn_fixed_penalty_s"])

    # --- busy roads (tunable combination of speed, lanes, volume) ---
    # ADR-0005: a per-class floor the volume term can raise but never lower —
    # see config.toml [busy.busy_floor_by_class] for the "why" and the
    # 3am-arterial arithmetic this replaces.
    busy_contribution = st.busy_base + bc["a_vol"] * vol_n
    edge_busy = np.maximum(busy_contribution, st.busy_floor) > bc["busy_threshold"]
    edge_major = edge_busy & st.edge_big

    # --- unsafe-action counters (spec predicates) ---
    tau = float(tc["tau_unsafe"])
    over_tau = raw >= tau

    unprotected_left = (
        st.is_left
        & st.observed
        & over_tau
        & ((st.ctrl_none & edge_busy[out])
           | (st.unprotected_approach & edge_major[out]))
    )

    uncontrolled_crossing = (
        st.is_straight
        & st.observed
        & over_tau
        & ((st.ctrl_none & _cross_count(pack, st, inn, edge_busy))
           | (st.unprotected_approach & _cross_count(pack, st, inn, edge_major)))
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
    tier[(tier == TIER_UNSAFE) & ~st.observed] = TIER_CAUTION

    return QueryCosts(
        edge_time_s=snap.edge_time_s,
        # Gathered once here rather than on every arc_cost call: it is the same
        # array for all lambdas and all penalty-method reruns within a request.
        turn_time_s=snap.edge_time_s[out],
        turn_penalty_s=penalty,
        turn_raw=raw,
        edge_busy=edge_busy,
        edge_major=edge_major,
        turn_unsafe_type=unsafe_type,
        turn_tier=tier,
        v_max_mps=st.v_max_mps,
    )


def arc_cost(pack: GraphPack, qc: QueryCosts, lam: float) -> np.ndarray:
    """Per-turn generalized cost: travel time of the target edge plus the
    lambda-weighted safety penalty. The ONLY place lambda enters."""
    return qc.turn_time_s + lam * qc.turn_penalty_s


def heuristic(pack: GraphPack, qc: QueryCosts,
              dest_lat: float, dest_lon: float) -> np.ndarray:
    """Admissible A* heuristic: straight-line time to destination at the
    pack-wide max FREE-FLOW speed. Congestion only slows edges and penalties
    are >= 0, so this lower-bounds generalized cost for every lambda. Never
    includes any safety term (that would break admissibility).

    Evaluated over NODES and then gathered to edge heads. Many edges share a
    head node, so this is strictly fewer haversines for the same answer — and
    it is the same answer bitwise, which is not free to assume when the ufuncs
    involved are sin/cos/arcsin over a different-length array. Pinned by
    tests/test_costs_golden.py::test_heuristic_equals_a_per_node_gather.
    """
    d = haversine_m(pack.node_lat, pack.node_lon, dest_lat, dest_lon)
    return (d / qc.v_max_mps)[pack.edge_head]
