"""Per-control-type safety rules: the spec's control-override table and the
two unsafe-action predicates, exercised through the REAL ingestion + cost
pipeline on a busy-arterial cross fixture.

Fixture (cross_with_control): E-W primary arterial (56 km/h, 2 lanes, busy
under free-flow base volumes), N-S residential. Approaching from the south:
LEFT turns onto the busy arterial, STRAIGHT crosses it.
"""
import pytest

from pyref.costs import (
    TIER_CAUTION,
    UNSAFE_CROSSING,
    UNSAFE_LEFT,
)
from pyref.graph import Control, RoadClass
from tests.helpers.fixtures import cross_with_control, find_turn, make_costs
from tests.helpers.toy_graphs import find_edge


def _turns(control: Control | None):
    pack, ids = cross_with_control(control)
    qc = make_costs(pack)
    e_in = find_edge(pack, ids["s"], ids["c"])
    t_left = find_turn(pack, e_in, find_edge(pack, ids["c"], ids["w"]))
    t_straight = find_turn(pack, e_in, find_edge(pack, ids["c"], ids["n"]))
    return pack, qc, t_left, t_straight


def test_arterial_is_busy_and_side_street_is_not():
    pack, ids = cross_with_control(Control.NONE)
    qc = make_costs(pack)
    assert qc.edge_busy[find_edge(pack, ids["c"], ids["e"])]
    assert qc.edge_busy[find_edge(pack, ids["w"], ids["c"])]
    assert not qc.edge_busy[find_edge(pack, ids["s"], ids["c"])]


def test_uncontrolled():
    pack, qc, t_left, t_straight = _turns(Control.NONE)
    assert qc.turn_unsafe_type[t_left] == UNSAFE_LEFT
    assert qc.turn_unsafe_type[t_straight] == UNSAFE_CROSSING
    assert qc.turn_penalty_s[t_left] > 0
    assert qc.turn_penalty_s[t_straight] > 0
    # left onto the busy road scores worse than crossing it
    assert qc.turn_penalty_s[t_left] > qc.turn_penalty_s[t_straight]


def test_stop_2way_minor_approach():
    """Holding the stop sign while cross traffic does not stop is no
    protection: both the left onto the arterial and the straight across it
    count. This is the motivating case — pulling up to a 4-lane arterial from
    a side street and having to find a gap."""
    pack, qc, t_left, t_straight = _turns(Control.STOP_2WAY)
    assert qc.turn_unsafe_type[t_left] == UNSAFE_LEFT
    assert qc.turn_unsafe_type[t_straight] == UNSAFE_CROSSING
    # no override applies: penalties match the uncontrolled case
    _, qc0, t_left0, t_straight0 = _turns(Control.NONE)
    assert qc.turn_penalty_s[t_left] == qc0.turn_penalty_s[t_left0]


def test_stop_2way_priority_approach_is_not_flagged():
    """The major road's own approaches hold priority at the same node — they
    must not be flagged just because the node is a 2-way stop."""
    pack, ids = cross_with_control(Control.STOP_2WAY)
    qc = make_costs(pack)
    e_in = find_edge(pack, ids["w"], ids["c"])          # arterial approach
    t = find_turn(pack, e_in, find_edge(pack, ids["c"], ids["n"]))
    assert qc.turn_unsafe_type[t] == 0


def test_stop_2way_needs_a_major_road_not_merely_a_busy_one():
    """A stop-sign maneuver counts only across a road that is busy AND big.
    The same fast-but-narrow tertiary street is flagged when there is no
    control at all, so it is the major gate doing the work here, not the
    busy threshold or tau."""
    small = dict(road_class=RoadClass.tertiary, speed_kph=65, lanes=1)

    pack, ids = cross_with_control(Control.NONE, **small)
    qc = make_costs(pack)
    e_in = find_edge(pack, ids["s"], ids["c"])
    t_left = find_turn(pack, e_in, find_edge(pack, ids["c"], ids["w"]))
    assert qc.edge_busy[find_edge(pack, ids["c"], ids["w"])]
    assert not qc.edge_major[find_edge(pack, ids["c"], ids["w"])]
    assert qc.turn_unsafe_type[t_left] == UNSAFE_LEFT

    pack, ids = cross_with_control(Control.STOP_2WAY, **small)
    qc = make_costs(pack)
    e_in = find_edge(pack, ids["s"], ids["c"])
    t_left = find_turn(pack, e_in, find_edge(pack, ids["c"], ids["w"]))
    assert qc.turn_unsafe_type[t_left] == 0


def test_inferred_control_is_penalized_but_never_counted():
    """An untagged junction falls to the road-class heuristic, which guesses
    a 2-way stop. The guess still steers routing away, but it must not appear
    in the user-facing count or paint the map red."""
    pack, qc, t_left, t_straight = _turns(None)
    _, qc_obs, t_left_obs, t_straight_obs = _turns(Control.STOP_2WAY)

    assert qc.turn_unsafe_type[t_left] == 0
    assert qc.turn_unsafe_type[t_straight] == 0
    assert qc.turn_tier[t_left] == TIER_CAUTION
    assert qc.turn_penalty_s[t_left] > 0
    # ...and the penalty is the observed one, damped by inferred_confidence_factor
    assert qc.turn_penalty_s[t_left] == pytest.approx(
        0.5 * qc_obs.turn_penalty_s[t_left_obs])


def test_yield_sits_between_a_stop_and_nothing():
    pack, qc, t_left, t_straight = _turns(Control.YIELD)
    _, qc0, t_left0, _ = _turns(Control.NONE)
    assert qc.turn_penalty_s[t_left] == pytest.approx(
        0.6 * qc0.turn_penalty_s[t_left0])          # yield_factor
    # a give_way approach onto a major road is still unprotected
    assert qc.turn_unsafe_type[t_left] == UNSAFE_LEFT


def test_yielding_straight_is_ordered_between_a_green_light_and_a_stop():
    """What a yield sign constrains is the movement that crosses conflicting
    traffic. Going straight past one must not cost more than crossing at a
    permissive signal (it did — 3x — which drove routes off through streets
    mid-block), nor less (which would make yields cheaper than a green)."""
    _, qc_yield, _, t_yield = _turns(Control.YIELD)
    _, qc_sig, _, t_sig = _turns(Control.SIGNAL_PERMISSIVE)
    _, qc_stop, _, t_stop = _turns(Control.STOP_2WAY)

    signal = qc_sig.turn_penalty_s[t_sig]
    yielding = qc_yield.turn_penalty_s[t_yield]
    stop = qc_stop.turn_penalty_s[t_stop]
    assert signal < yielding < stop, (signal, yielding, stop)


def test_yielding_straight_is_not_double_discounted():
    """yield_row_factor replaces right_of_way_factor for these movements
    rather than composing with it; stacking put a yield below a green light."""
    _, qc_yield, _, t_yield = _turns(Control.YIELD)
    _, qc0, _, t0 = _turns(Control.NONE)
    assert qc_yield.turn_penalty_s[t_yield] == pytest.approx(
        0.35 * qc0.turn_penalty_s[t0])              # yield_row_factor alone


def test_stop_4way_strongly_reduced_and_uncounted():
    pack, qc, t_left, t_straight = _turns(Control.STOP_4WAY)
    _, qc0, t_left0, _ = _turns(Control.NONE)
    assert qc.turn_unsafe_type[t_left] == 0
    assert qc.turn_unsafe_type[t_straight] == 0
    assert qc.turn_penalty_s[t_left] == pytest.approx(
        0.25 * qc0.turn_penalty_s[t_left0])


def test_signal_permissive():
    """A signal is a form of traffic control, so neither maneuver is counted.
    The left is still visibly worse than free — it lands in the caution band,
    amber on the map — because OSM cannot say whether it has a protected
    arrow."""
    pack, qc, t_left, t_straight = _turns(Control.SIGNAL_PERMISSIVE)
    assert qc.turn_unsafe_type[t_left] == 0
    assert qc.turn_unsafe_type[t_straight] == 0
    assert qc.turn_penalty_s[t_left] > 0
    assert qc.turn_tier[t_left] == TIER_CAUTION


def test_signalized_left_is_far_cheaper_than_a_stop_sign_left():
    """The routing preference the planner exists to express: given the choice,
    take the left at the light rather than the one from the stop sign."""
    _, qc_sig, t_left_sig, _ = _turns(Control.SIGNAL_PERMISSIVE)
    _, qc_stop, t_left_stop, _ = _turns(Control.STOP_2WAY)
    assert qc_sig.turn_penalty_s[t_left_sig] < qc_stop.turn_penalty_s[t_left_stop]


def test_signal_protected_zeroes_everything():
    pack, qc, t_left, t_straight = _turns(Control.SIGNAL_PROTECTED)
    assert qc.turn_penalty_s[t_left] == 0.0
    assert qc.turn_penalty_s[t_straight] == 0.0
    assert qc.turn_unsafe_type[t_left] == 0
    assert qc.turn_unsafe_type[t_straight] == 0


def test_roundabout_zeroes_everything():
    pack, qc, t_left, t_straight = _turns(Control.ROUNDABOUT)
    assert qc.turn_penalty_s[t_left] == 0.0
    assert qc.turn_unsafe_type[t_left] == 0
    assert qc.turn_unsafe_type[t_straight] == 0


def test_right_of_way_reduces_straight_more_than_left():
    """STRAIGHT through a permissive signal (cross traffic on red) is far
    cheaper than through an uncontrolled node. The permissive LEFT is
    discounted too, but by much less — it still crosses oncoming traffic."""
    _, qc_sig, t_left_sig, t_straight_sig = _turns(Control.SIGNAL_PERMISSIVE)
    _, qc0, t_left0, t_straight0 = _turns(Control.NONE)
    assert qc_sig.turn_penalty_s[t_straight_sig] == pytest.approx(
        0.2 * qc0.turn_penalty_s[t_straight0])   # right_of_way_factor
    assert qc_sig.turn_penalty_s[t_left_sig] == pytest.approx(
        0.35 * qc0.turn_penalty_s[t_left0])      # signal_left_factor


def test_median_reduces_raw_score():
    from tests.helpers.fixtures import cross_with_control
    pack, ids = cross_with_control(Control.NONE)
    qc0 = make_costs(pack)
    e_in = find_edge(pack, ids["s"], ids["c"])
    t_left = find_turn(pack, e_in, find_edge(pack, ids["c"], ids["w"]))
    raw0 = qc0.turn_raw[t_left]
    # flip the median flag on the target edge and recompute
    pack.edge_median[find_edge(pack, ids["c"], ids["w"])] = 1
    qc1 = make_costs(pack)
    assert qc1.turn_raw[t_left] == pytest.approx(raw0 - 0.4)  # w_med from config
