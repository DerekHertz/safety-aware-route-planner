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
the unsafe-predicate masks and the crossing legs `_uncontrolled_crossing`
gathers through — is a function of (pack, cfg) alone, and used to be rebuilt on every request.
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

Control delay (ADR-0016)
------------------------
    turn_time_s = edge_time_s[out] + turn_delay_s        (time, never penalty)

The expected wait at the junction, charged to the turn that makes it. It is
travel time: it enters every lambda's arc cost unchanged, the ETA, the detour
budget and the fast route's choice. Constants are `[sim.control_delay]` in
config.toml (uncalibrated; ADR-0017 calibrates them). The governing control is
the approach's, as for the penalty, and an INFERRED control waits exactly like
an observed one: the OBSERVED gate above protects the unsafe COUNT, and the
best estimate of time uses the best guess of control.

Movement table, by the approach's control:
    SIGNAL_PROTECTED, ROUNDABOUT      -> 0
    STOP_4WAY                         -> all_way_stop_s
    SIGNAL_PERMISSIVE                 -> signal_major_s / signal_minor_s by
                                         approach; a LEFT adds a gap wait
                                         against oncoming (t_c_priority_left_s)
    priority approach                 -> STRAIGHT / RIGHT 0; LEFT gap wait
      (STOP_2WAY / YIELD not holding     against oncoming (t_c_priority_left_s)
       the line, or NONE and major)
    minor approach                    -> gap wait for every movement:
      (holding a stop / give-way line,   STRAIGHT t_c_crossing_s, LEFT
       or NONE and not major)            t_c_left_s (t_c_left_wide_s onto a
                                         road of >= left_wide_lanes_min lanes
                                         in total), RIGHT t_c_right_s
    UTURN                             -> 0 (only allowed at dead ends, which
                                         have nothing to wait for)

Gap wait: Adams' single-vehicle delay, `(e^(q*t_c) - q*t_c - 1) / q`, capped at
cap_s, exactly 0 at q == 0 (`adams_delay_s`). q is the conflicting flow in
veh/s, summed over the approaches ("legs") the movement must clear, each leg's
flow being `volume_vph_lane * lanes` -- the same [sim] volume the busy rule
reads, so the departure time is again the only thing that moves it.

**"Major" approach.** An approach is major iff it outranks every other road
at the junction (ROAD_CLASS_RANK, lower = more major, as in
ingestion/controls.py). The other roads are every incoming approach except the
approach itself and its oncoming leg, which are its own road. Outranking is a
strictly lower rank, or, at equal rank, going straight through against a road
that ends here: the T-junction rule, where the through road has priority over
the stem. Otherwise ties are minor -- with no dominant road the green, or the
priority, is split, and the longer signal wait is the closer estimate. A node
with fewer than three physical legs is a bend, not a junction, and is
vacuously major.

**Leg selection** -- documented approximations, all built from the existing
turn table rather than new geometry. A "straight feeder" of an edge o is an
incoming approach whose STRAIGHT turn leaves along o, i.e. the traffic that
travels o. Own in-edge is always excluded.
  * crossing (STRAIGHT): every other incoming approach at the node except the
    reverse of our out-edge -- `_crossing_legs`' set. At a 4-way this is both
    directions of the cross road. Overcounts at a T whose stem we pass, but
    only from a minor approach, which the T rule makes rare.
  * left onto a road: both of its directions -- the near side arriving on
    reverse(out), and the far side, out's straight feeders.
  * right onto a road: the near-side flow only, out's straight feeders -- the
    lanes being joined.
  * left across oncoming (priority approach, or any permissive signal): the
    straight feeders of reverse(in), i.e. whoever arrives dead ahead. None
    when our road is one-way.
Legs are whole-approach volumes, not per-movement ones (the sim has none), so
turning traffic on a conflicting leg counts as conflicting; a turn restriction
does not remove a feeder. `gap_legs` is padded with a sentinel edge id whose
flow is 0.0 -- NOT by repeating a real leg, because these legs are summed.

Per request this is one gather of conflicting flow, one sum and one exp over
the gap-acceptance turns only (`control_delay_s`). The exp is `_exp_poly`, not
`np.exp`: see there -- the delay reaches pinned arrays, so it must be built
from IEEE-exact operations like everything else in this module.
"""
from __future__ import annotations

import math
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
    # f64[T] edge_time_s gathered to turn targets, PLUS turn_delay_s: the
    # time half of the arc cost, identical for every lambda
    turn_time_s: np.ndarray
    turn_delay_s: np.ndarray     # f64[T] expected control delay (ADR-0016), >= 0
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

    # --- the crossing predicate's only consumers, and what they cross ---
    # See `_crossing_legs`. `*_turns` are turn ids; `*_legs[j, i]` is the j-th
    # other incoming approach at turn `*_turns[i]`'s node, padded by repeating
    # a real one. One pair per mask: `busy` governs ctrl_none approaches,
    # `major` governs stop/yield-holding ones.
    cross_busy_turns: np.ndarray    # intp[n0]
    cross_busy_legs: np.ndarray     # intp[D0, n0]
    cross_major_turns: np.ndarray   # intp[n1]
    cross_major_legs: np.ndarray    # intp[D1, n1]

    # --- control delay (ADR-0016); see "Control delay" in the docstring ---
    delay_fixed_s: np.ndarray    # f64[T], signal / all-way-stop wait, else 0
    edge_lanes: np.ndarray       # f64[E], per direction: flow = volume * lanes
    # Gap-acceptance turns, their critical gaps, and the approaches whose flow
    # they must clear. `gap_legs[:, i]` is padded with the sentinel edge id E,
    # whose flow is pinned to 0.0 per request: the legs are SUMMED, so a
    # repeated real leg (the `_crossing_legs` trick for an any()) would count
    # that approach twice.
    gap_turns: np.ndarray        # intp[g]
    gap_tc: np.ndarray           # f64[g], critical gap t_c in seconds
    gap_legs: np.ndarray         # intp[Dg, g]
    delay_cap_s: float


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

    is_straight = pack.turn_maneuver == Maneuver.STRAIGHT
    unprotected_approach = ((ctrl == Control.STOP_2WAY) | yielding) & must_stop
    # The crossing predicate is read only through these two static gates
    # (straight, observed, and the approach's control class) — everywhere
    # else its answer is ANDed away. The two gates are disjoint by control.
    busy_turns, busy_legs = _crossing_legs(
        pack, is_straight & observed & ctrl_none)
    major_turns, major_legs = _crossing_legs(
        pack, is_straight & observed & unprotected_approach)

    delay_fixed_s, gap_turns, gap_tc, gap_legs = _control_delay_statics(
        pack, cfg["sim"]["control_delay"], ctrl, must_stop)

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
        is_straight=is_straight,
        observed=observed,
        ctrl_none=ctrl_none,
        # Holding the stop sign or yield line at a 2-way stop means cross
        # traffic does NOT stop — no protection at all. This is the "pull up to
        # an arterial from a side street and squeeze into a gap" case.
        unprotected_approach=unprotected_approach,
        cross_busy_turns=busy_turns,
        cross_busy_legs=busy_legs,
        cross_major_turns=major_turns,
        cross_major_legs=major_legs,
        delay_fixed_s=delay_fixed_s,
        edge_lanes=pack.edge_lanes.astype(np.float64),
        gap_turns=gap_turns,
        gap_tc=gap_tc,
        gap_legs=gap_legs,
        delay_cap_s=float(cfg["sim"]["control_delay"]["cap_s"]),
    )


def _crossing_legs(pack: GraphPack,
                   gate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each turn in `gate`, the incoming approaches a STRAIGHT through its
    node would cross.

    Documented approximation: a straight crosses a street iff some OTHER
    incoming approach at the node qualifies — excluding our own in-edge and the
    reverse of our out-edge, which are the two legs we travel along rather than
    across. This used to be a per-request node histogram over every edge
    (`np.add.at`) and every turn, run once per mask (ADR-0009, 3a-next); the
    legs are static, so they are listed here once instead.

    Returns `(turns, legs)`: `legs[:, i]` lists turn `turns[i]`'s crossed
    approaches, padded to a common depth by repeating its first one — a repeat
    cannot change an any(). Turns with nothing to cross can never answer True
    and are dropped. Column-major so the per-request reduce walks rows.
    """
    inn = pack.turn_in_edge
    rev = pack.edge_reverse[pack.turn_out_edge]
    # The old count subtracted both excluded legs, so it equalled "how many
    # others match" only when they are two distinct edges. Ingestion forces
    # out == reverse(in) to UTURN (ingestion/turns.py); guard the premise.
    straight_reversal = (pack.turn_maneuver == Maneuver.STRAIGHT) & (rev == inn)
    assert not straight_reversal.any(), (
        "a STRAIGHT turn leaves along the reverse of its in-edge; the crossing "
        "legs can no longer be listed statically without changing the count")

    turns = np.flatnonzero(gate)
    node_in = _group_padded(pack.edge_head, np.arange(pack.num_edges),
                            pack.num_nodes)
    cand = node_in[pack.edge_head[inn[turns]]]
    keep = ((cand >= 0) & (cand != inn[turns, None])
            & (cand != rev[turns, None]))
    has_any = keep.any(axis=1)
    turns, cand, keep = turns[has_any], cand[has_any], keep[has_any]
    # Kept legs first, then pad with the row's first kept leg.
    cand = np.take_along_axis(cand, np.argsort(~keep, axis=1, kind="stable"),
                              axis=1)
    depth = int(keep.sum(axis=1).max(initial=1))
    cand = cand[:, :depth]
    pad = np.arange(depth)[None, :] >= keep.sum(axis=1)[:, None]
    cand = np.where(pad, cand[:, :1], cand)
    return turns.astype(np.intp), np.ascontiguousarray(cand.T, dtype=np.intp)


def _group_padded(keys: np.ndarray, values: np.ndarray,
                  num_keys: int) -> np.ndarray:
    """`values` grouped by `keys` into a [num_keys, max group size] matrix,
    padded with -1, each row in the original (stable) order. With keys =
    edge_head and values = edge ids this is "incoming edges per node"."""
    order = np.argsort(keys, kind="stable")
    count = np.bincount(keys, minlength=num_keys)
    width = max(int(count.max(initial=0)), 1)
    start = np.concatenate(([0], np.cumsum(count)[:-1]))
    slot = np.arange(len(order)) - np.repeat(start, count)
    out = np.full((num_keys, width), -1, dtype=np.intp)
    out[keys[order], slot] = values[order]
    return out


def adams_delay_s(q: np.ndarray, t_c: np.ndarray, cap_s: float) -> np.ndarray:
    """Adams' single-vehicle gap wait for Poisson traffic, capped:
    `E[w] = (exp(q*t_c) - q*t_c - 1) / q`, with q in veh/s and t_c in s.

    Exactly +0.0 where q == 0 (the 0/0 limit is 0). The exponent is clamped
    at `_EXP_X_MAX`, far past where the cap binds, so it cannot overflow.
    Uses `_exp_poly`, not `np.exp` -- see there for why."""
    x = np.minimum(q * t_c, _EXP_X_MAX)
    # e^x >= 1 + x, but a rounding ulp at tiny x must not make a wait negative
    # (delay >= 0 is what keeps the A* heuristic admissible).
    num = np.maximum(_exp_poly(x) - x - 1.0, 0.0)
    w = np.zeros(len(q), dtype=np.float64)
    np.divide(num, q, out=w, where=q > 0.0)
    return np.minimum(w, cap_s)


# exp via Cody-Waite range reduction and a fixed Taylor polynomial.
#
# np.exp is a transcendental that numpy dispatches to different SIMD kernels
# by CPU (an AVX-512 build differs from libm in the last ulp), which is the
# reason tests/test_costs_golden.py refuses to pin the heuristic. The delay
# sits inside turn_time_s and every arc_cost, which ARE pinned, so it is
# built from IEEE-754 exact operations only (+, -, *, rint, ldexp): the same
# bits on every machine. ~1e-15 relative error on [0, 50]; the delay needs
# nowhere near that, but the determinism is the point.
_LN2_HI = 6.93147180369123816490e-01   # fdlibm split: k*_LN2_HI is exact
_LN2_LO = 1.90821492927058770002e-10
_INV_LN2 = 1.44269504088896338700e+00
_EXP_COEF = tuple(1.0 / math.factorial(n) for n in range(12))   # |r| <= ln2/2
_EXP_X_MAX = 50.0


def _exp_poly(x: np.ndarray) -> np.ndarray:
    k = np.rint(x * _INV_LN2)
    r = (x - k * _LN2_HI) - k * _LN2_LO
    p = np.full(len(x), _EXP_COEF[-1], dtype=np.float64)
    for c in reversed(_EXP_COEF[:-1]):
        p *= r
        p += c
    return np.ldexp(p, k.astype(np.int32))


def _control_delay_statics(pack: GraphPack, cd, ctrl: np.ndarray,
                           must_stop: np.ndarray
                           ) -> tuple[np.ndarray, np.ndarray, np.ndarray,
                                      np.ndarray]:
    """The (pack, cfg)-only half of the control delay: fixed waits per turn,
    and for every gap-acceptance turn its critical gap and conflicting legs.
    The movement table and the leg approximations are documented in the
    module docstring ("Control delay"); this is their one implementation.

    Returns `(delay_fixed_s[T], gap_turns[g], gap_tc[g], gap_legs[Dg, g])`,
    `gap_legs` padded with the sentinel edge id E (zero flow)."""
    E, T = pack.num_edges, pack.num_turns
    inn = pack.turn_in_edge.astype(np.intp)
    out = pack.turn_out_edge.astype(np.intp)
    rev = pack.edge_reverse.astype(np.intp)
    man = pack.turn_maneuver
    is_straight = man == Maneuver.STRAIGHT
    is_left = man == Maneuver.LEFT
    is_right = man == Maneuver.RIGHT

    # STRAIGHT feeders of each edge o: the incoming approaches whose straight
    # continuation leaves the node along o, i.e. the traffic that travels o.
    feeders = _group_padded(out[is_straight], inn[is_straight], E)

    def feeders_of(edges: np.ndarray) -> np.ndarray:
        rows = feeders[np.maximum(edges, 0)]
        return np.where((edges >= 0)[:, None], rows, -1)

    # Oncoming approach of each edge i: whatever feeds reverse(i). Empty when
    # our road is one-way, or when nothing arrives dead ahead.
    oncoming = feeders_of(rev)                                    # [E, F]

    # --- which approach is "major" -------------------------------------
    # See "Control delay" in the module docstring for the rule in prose.
    rank = _road_class_ranks(pack.edge_road_class)
    continues = np.zeros(E, dtype=bool)
    continues[inn[is_straight]] = True
    node_in = _group_padded(pack.edge_head, np.arange(E), pack.num_nodes)
    others = node_in[pack.edge_head]                              # [E, W]
    own = ((others == np.arange(E)[:, None])
           | (others[:, :, None] == oncoming[:, None, :]).any(axis=2))
    other_road = (others >= 0) & ~own
    o_rank = rank[np.maximum(others, 0)]
    outranks = ((rank[:, None] < o_rank)
                | ((rank[:, None] == o_rank) & continues[:, None]
                   & ~continues[np.maximum(others, 0)]))
    major = np.where(other_road, outranks, True).all(axis=1)       # [E]
    # Physical legs per node (distinct neighbours, either direction). Under
    # three it is a bend or a change of way, not a junction: no other road.
    ends = np.unique(np.stack([np.concatenate([pack.edge_head, pack.edge_tail]),
                               np.concatenate([pack.edge_tail, pack.edge_head])]),
                     axis=1)
    num_legs = np.bincount(ends[0], minlength=pack.num_nodes)
    major |= num_legs[pack.edge_head] < 3

    # --- the movement table ----------------------------------------------
    c = ctrl
    two_way = (c == Control.STOP_2WAY) | (c == Control.YIELD)
    none = c == Control.NONE
    maj = major[inn]
    uturn = man == Maneuver.UTURN
    # Minor approach: holds a stop or give-way line, or has no control and
    # no priority either. Waits for a gap for every movement.
    minor = ((two_way & must_stop) | (none & ~maj)) & ~uturn
    # Priority approach at a 2-way stop / yield / uncontrolled junction:
    # through and right are free; a left yields to oncoming.
    priority = (two_way & ~must_stop) | (none & maj)
    signal = c == Control.SIGNAL_PERMISSIVE

    delay_fixed_s = np.zeros(T, dtype=np.float64)
    delay_fixed_s[(c == Control.STOP_4WAY) & ~uturn] = float(cd["all_way_stop_s"])
    delay_fixed_s[signal & ~uturn & maj] = float(cd["signal_major_s"])
    delay_fixed_s[signal & ~uturn & ~maj] = float(cd["signal_minor_s"])

    lanes = pack.edge_lanes.astype(np.float64)
    road_lanes = lanes[out] + np.where(rev[out] >= 0, lanes[np.maximum(rev[out], 0)], 0.0)
    wide = road_lanes >= float(cd["left_wide_lanes_min"])

    pairs_t: list[np.ndarray] = []
    pairs_leg: list[np.ndarray] = []
    tc = np.zeros(T, dtype=np.float64)

    def add(gate: np.ndarray, cand: np.ndarray, t_c) -> None:
        """Gap-accept at the turns in `gate`, clearing the flow on the legs
        `cand[t]` (a [T, k] candidate matrix, -1 = no leg), with gap `t_c`."""
        ts = np.flatnonzero(gate)
        cand = cand[ts]
        keep = (cand >= 0) & (cand != inn[ts, None])
        pairs_t.append(np.broadcast_to(ts[:, None], cand.shape)[keep])
        pairs_leg.append(cand[keep])
        tc[ts] = t_c[ts] if isinstance(t_c, np.ndarray) else float(t_c)

    # Crossing: every other incoming approach but our own in-edge and the
    # reverse of our out-edge (== `_crossing_legs`).
    cross = node_in[pack.edge_head[inn]]
    add(minor & is_straight, np.where(cross == rev[out][:, None], -1, cross),
        cd["t_c_crossing_s"])
    # Left onto a road: both of its directions -- the near side, arriving on
    # reverse(out), and the far side, the straight feeders of out.
    both = np.concatenate([rev[out][:, None], feeders_of(out)], axis=1)
    add(minor & is_left, both, np.where(wide, float(cd["t_c_left_wide_s"]),
                          float(cd["t_c_left_s"])))
    # Right onto a road: the near-side flow only, the lanes being joined.
    add(minor & is_right, feeders_of(out), cd["t_c_right_s"])
    # Left across oncoming traffic, from a priority approach or at a signal
    # (permissive: no arrow is ever mapped) -- on top of the signal wait.
    add((priority | signal) & is_left, oncoming[inn], cd["t_c_priority_left_s"])

    t_all = np.concatenate(pairs_t)
    leg_all = np.concatenate(pairs_leg)
    # One row per turn; a leg listed twice would be counted twice.
    key = np.unique(t_all.astype(np.int64) * (E + 1) + leg_all)
    t_all, leg_all = key // (E + 1), key % (E + 1)
    gap_turns, start, count = np.unique(t_all, return_index=True,
                                        return_counts=True)
    depth = max(int(count.max(initial=0)), 1)
    slot = np.arange(len(t_all)) - np.repeat(start, count)
    legs = np.full((depth, len(gap_turns)), E, dtype=np.intp)
    legs[slot, np.repeat(np.arange(len(gap_turns)), count)] = leg_all
    return (delay_fixed_s, gap_turns.astype(np.intp), tc[gap_turns],
            np.ascontiguousarray(legs))


def _uncontrolled_crossing(st: PackStatics, over_tau: np.ndarray,
                           edge_busy: np.ndarray,
                           edge_major: np.ndarray) -> np.ndarray:
    """UNSAFE_CROSSING's predicate: an observed STRAIGHT over tau that crosses
    a busy street from an uncontrolled approach, or a major one while holding a
    stop/yield line. Computed only at the turns `_crossing_legs` listed — every
    other turn is False by construction. The two turn sets are disjoint (one
    keys off Control.NONE, the other off a held STOP_2WAY/YIELD), so the second
    scatter cannot overwrite an answer from the first."""
    out = np.zeros(len(over_tau), dtype=bool)
    t = st.cross_busy_turns
    out[t] = over_tau[t] & edge_busy[st.cross_busy_legs].any(axis=0)
    t = st.cross_major_turns
    out[t] = over_tau[t] & edge_major[st.cross_major_legs].any(axis=0)
    return out


def control_delay_s(st: PackStatics, snap: Snapshot) -> np.ndarray:
    """Per-turn expected control delay for this snapshot: the static fixed
    waits, plus the gap waits -- one gather of conflicting flow, one sum, one
    exp. Flow on an approach is volume per lane times lanes; the sentinel
    edge E pads `gap_legs` and carries exactly zero."""
    flow = np.empty(len(st.edge_lanes) + 1, dtype=np.float64)
    np.multiply(snap.volume_vph_lane, st.edge_lanes, out=flow[:-1])
    flow[-1] = 0.0
    # axis-0 reduction over a C-contiguous [D, g] array: rows are added in
    # order, element by element -- no pairwise regrouping, same bits anywhere.
    q = flow[st.gap_legs].sum(axis=0) / 3600.0
    delay = st.delay_fixed_s.copy()
    delay[st.gap_turns] += adams_delay_s(q, st.gap_tc, st.delay_cap_s)
    return delay


def compute_costs(pack: GraphPack, snap: Snapshot, cfg: Config,
                  statics: PackStatics | None = None) -> QueryCosts:
    """The per-query half. `statics` is built on demand when omitted, which
    keeps every caller working; `Router` builds it once and passes it in."""
    cc = cfg["cost"]
    bc = cfg["busy"]
    tc = cfg["tiers"]
    st = statics if statics is not None else build_pack_statics(pack, cfg)

    out = pack.turn_out_edge

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

    uncontrolled_crossing = _uncontrolled_crossing(st, over_tau, edge_busy,
                                                   edge_major)

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

    delay = control_delay_s(st, snap)

    return QueryCosts(
        edge_time_s=snap.edge_time_s,
        # Gathered once here rather than on every arc_cost call: it is the same
        # array for all lambdas and all penalty-method reruns within a request.
        # The control delay is time (ADR-0016), so it lives here, not in the
        # penalty; adding an exact +0.0 leaves undelayed turns bit-identical.
        turn_time_s=snap.edge_time_s[out] + delay,
        turn_delay_s=delay,
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
