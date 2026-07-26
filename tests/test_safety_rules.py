"""Per-control-type safety rules: the spec's control-override table and the
two unsafe-action predicates, exercised through the REAL ingestion + cost
pipeline on a busy-arterial cross fixture.

Fixture (cross_with_control): E-W primary arterial (56 km/h, 2 lanes, busy
under free-flow base volumes), N-S residential. Approaching from the south:
LEFT turns onto the busy arterial, STRAIGHT crosses it.
"""
import numpy as np
import pytest

from pyref.costs import UNSAFE_CROSSING, UNSAFE_LEFT, compute_costs
from pyref.graph import Control
from tests.helpers.fixtures import cross_with_control, find_turn, make_costs
from tests.helpers.toy_graphs import find_edge


def _turns(control: Control):
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
    pack, qc, t_left, t_straight = _turns(Control.STOP_2WAY)
    # left predicate requires control in {NONE, SIGNAL_PERMISSIVE}
    assert qc.turn_unsafe_type[t_left] == 0
    # crossing predicate includes STOP_2WAY where this approach must stop
    assert qc.turn_unsafe_type[t_straight] == UNSAFE_CROSSING
    # no override applies: penalties match the uncontrolled case
    _, qc0, t_left0, t_straight0 = _turns(Control.NONE)
    assert qc.turn_penalty_s[t_left] == qc0.turn_penalty_s[t_left0]


def test_stop_4way_strongly_reduced_and_uncounted():
    pack, qc, t_left, t_straight = _turns(Control.STOP_4WAY)
    _, qc0, t_left0, _ = _turns(Control.NONE)
    assert qc.turn_unsafe_type[t_left] == 0
    assert qc.turn_unsafe_type[t_straight] == 0
    assert qc.turn_penalty_s[t_left] == pytest.approx(
        0.25 * qc0.turn_penalty_s[t_left0])


def test_signal_permissive():
    pack, qc, t_left, t_straight = _turns(Control.SIGNAL_PERMISSIVE)
    # permissive left onto busy street IS flagged...
    assert qc.turn_unsafe_type[t_left] == UNSAFE_LEFT
    # ...but a signalized crossing is not
    assert qc.turn_unsafe_type[t_straight] == 0
    assert qc.turn_penalty_s[t_left] > 0


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


def test_right_of_way_reduces_straight_but_not_left():
    """STRAIGHT through a permissive signal (cross traffic on red) is far
    cheaper than through an uncontrolled node; the permissive LEFT keeps its
    full raw score — it's the named hazard."""
    _, qc_sig, t_left_sig, t_straight_sig = _turns(Control.SIGNAL_PERMISSIVE)
    _, qc0, t_left0, t_straight0 = _turns(Control.NONE)
    assert qc_sig.turn_penalty_s[t_straight_sig] == pytest.approx(
        0.2 * qc0.turn_penalty_s[t_straight0])  # right_of_way_factor
    assert qc_sig.turn_penalty_s[t_left_sig] == qc0.turn_penalty_s[t_left0]


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
