"""Control delay (ADR-0016): the expected wait at an intersection is travel
time, not penalty.

Two layers are pinned here. First the gap-wait formula itself -- Adams' delay
`E[w] = (exp(q*t_c) - q*t_c - 1) / q`, checked against `math.exp` and against
the ADR's worked numbers. Then the movement table, on the one-junction
`cross_with_control` toy: a quiet N-S residential street crossing a 4-lane
primary arterial, under every control type. Every expected value is
recomputed here from the config constants and the snapshot volumes, not read
back from `pyref.costs`, so the test pins the intended model rather than the
implementation's own arithmetic.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from pyref.config import Config
from pyref.costs import adams_delay_s, compute_costs
from pyref.graph import Control, ControlConfidence, RoadClass
from sim.snapshot import free_flow
from tests.helpers.fixtures import cross_with_control, find_turn
from tests.helpers.toy_graphs import find_edge

CFG = Config.load()
CD = CFG["sim"]["control_delay"]
CAP = float(CD["cap_s"])


def _adams(q_vph: float, t_c: float) -> float:
    """Hand formula, in veh/h for readability; capped like the model."""
    if q_vph == 0.0:
        return 0.0
    q = q_vph / 3600.0
    x = q * t_c
    return min((math.exp(x) - x - 1.0) / q, CAP)


# ------------------------------------------------------------ the formula
def test_adams_worked_numbers_from_the_adr():
    """ADR-0016: a crossing of ~2,000 veh/h comes to about a minute, a left
    into the same road to ~105 s."""
    q = np.array([2000.0 / 3600.0])
    crossing = float(adams_delay_s(q, np.array([6.5]), CAP)[0])
    left = float(adams_delay_s(q, np.array([7.5]), CAP)[0])
    assert crossing == pytest.approx(58.3, abs=0.1)
    assert left == pytest.approx(106.8, abs=0.1)


def test_adams_matches_the_closed_form():
    """The implementation evaluates exp with a fixed polynomial (so the golden
    digests stay machine-independent); it must agree with libm to ~1e-12."""
    q_vph = np.array([1.0, 30.0, 150.0, 600.0, 1200.0, 2000.0, 2400.0])
    for t_c in (4.1, 6.2, 6.5, 7.1, 7.5):
        got = adams_delay_s(q_vph / 3600.0, np.full(len(q_vph), t_c), 1e9)
        for g, qv in zip(got, q_vph, strict=True):
            q = qv / 3600.0
            want = (math.exp(q * t_c) - q * t_c - 1.0) / q
            assert g == pytest.approx(want, rel=1e-11)


def test_adams_is_capped():
    """The formula blows up toward saturation; a driver goes around instead,
    which is the search's job (ADR-0016 "Cap at ~120 s")."""
    q = np.array([3000.0, 10_000.0, 1e7]) / 3600.0
    got = adams_delay_s(q, np.full(3, 7.5), CAP)
    assert np.all(got == CAP)
    assert np.all(np.isfinite(got))


def test_adams_is_exactly_zero_with_no_conflicting_flow():
    got = adams_delay_s(np.array([0.0]), np.array([6.5]), CAP)
    assert got[0] == 0.0 and not np.signbit(got[0])


def test_adams_is_three_am_small():
    """ADR-0016: "At 3 am, at ~150 veh/h, it is about a second."""
    got = float(adams_delay_s(np.array([150.0 / 3600.0]), np.array([6.5]), CAP)[0])
    assert 0.3 < got < 1.5


# ------------------------------------------------------- the movement table
# cross_with_control under free_flow: the arterial carries base volume,
# 600 veh/h/lane x 2 lanes = 1200 veh/h per direction; the residential street
# 100 x 1 = 100 veh/h per direction.
ART = float(CFG["sim"]["base_vph_per_lane"]["primary"]) * 2
RES = float(CFG["sim"]["base_vph_per_lane"]["residential"]) * 1


def _delays(control):
    pack, ids = cross_with_control(control)
    qc = compute_costs(pack, free_flow(pack, CFG), CFG)

    def turn(a, b, c):
        return find_turn(pack, find_edge(pack, ids[a], ids[b]),
                         find_edge(pack, ids[b], ids[c]))

    return pack, qc, turn


def test_protected_controls_carry_no_delay():
    for ctrl in (Control.SIGNAL_PROTECTED, Control.ROUNDABOUT):
        _, qc, _ = _delays(ctrl)
        assert np.all(qc.turn_delay_s == 0.0), ctrl.name


def test_two_way_stop_minor_approach_waits_for_a_gap():
    _, qc, turn = _delays(Control.STOP_2WAY)
    # straight across both arterial directions
    assert qc.turn_delay_s[turn("s", "c", "n")] == pytest.approx(
        _adams(2 * ART, float(CD["t_c_crossing_s"])), rel=1e-12)
    # left onto a 4-lane road: both directions, the >= 4-lane critical gap
    assert qc.turn_delay_s[turn("s", "c", "w")] == pytest.approx(
        _adams(2 * ART, float(CD["t_c_left_wide_s"])), rel=1e-12)
    # right: only the near-side flow, i.e. the lanes being joined
    assert qc.turn_delay_s[turn("s", "c", "e")] == pytest.approx(
        _adams(ART, float(CD["t_c_right_s"])), rel=1e-12)


def test_two_way_stop_priority_approach():
    """Right of way: through and right are free; a left yields to oncoming."""
    _, qc, turn = _delays(Control.STOP_2WAY)
    assert qc.turn_delay_s[turn("w", "c", "e")] == 0.0
    assert qc.turn_delay_s[turn("w", "c", "s")] == 0.0
    assert qc.turn_delay_s[turn("w", "c", "n")] == pytest.approx(
        _adams(ART, float(CD["t_c_priority_left_s"])), rel=1e-12)


def test_all_way_stop_is_a_small_fixed_wait():
    _, qc, turn = _delays(Control.STOP_4WAY)
    fixed = float(CD["all_way_stop_s"])
    for t in (turn("s", "c", "n"), turn("s", "c", "w"), turn("w", "c", "e")):
        assert qc.turn_delay_s[t] == fixed


def test_signal_major_vs_minor_approach_and_permissive_left():
    """The arterial outranks the side street, so its approaches get the major
    through wait; crossing it or turning left off the minor approach waits
    longer. A permissive left adds a gap wait against oncoming traffic."""
    _, qc, turn = _delays(Control.SIGNAL_PERMISSIVE)
    major = float(CD["signal_major_s"])
    minor = float(CD["signal_minor_s"])
    assert qc.turn_delay_s[turn("w", "c", "e")] == major
    assert qc.turn_delay_s[turn("s", "c", "n")] == minor
    # minor-approach permissive left: oncoming is the quiet north approach
    assert qc.turn_delay_s[turn("s", "c", "w")] == pytest.approx(
        minor + _adams(RES, float(CD["t_c_priority_left_s"])), rel=1e-12)
    # major-approach permissive left: oncoming is the other arterial direction
    assert qc.turn_delay_s[turn("w", "c", "n")] == pytest.approx(
        major + _adams(ART, float(CD["t_c_priority_left_s"])), rel=1e-12)


def test_uncontrolled_minor_approach_gap_waits_major_does_not():
    """No control at all: the road hierarchy decides who has priority."""
    _, qc, turn = _delays(Control.NONE)
    assert qc.turn_delay_s[turn("s", "c", "n")] == pytest.approx(
        _adams(2 * ART, float(CD["t_c_crossing_s"])), rel=1e-12)
    assert qc.turn_delay_s[turn("w", "c", "e")] == 0.0


def test_narrow_target_road_uses_the_narrow_left_gap():
    """7.1 s, not 7.5 s, when the road turned onto has < 4 lanes in total."""
    pack, ids = cross_with_control(Control.STOP_2WAY, lanes=1,
                                   road_class=RoadClass.secondary)
    qc = compute_costs(pack, free_flow(pack, CFG), CFG)
    t = find_turn(pack, find_edge(pack, ids["s"], ids["c"]),
                  find_edge(pack, ids["c"], ids["w"]))
    sec = float(CFG["sim"]["base_vph_per_lane"]["secondary"]) * 1
    assert qc.turn_delay_s[t] == pytest.approx(
        _adams(2 * sec, float(CD["t_c_left_s"])), rel=1e-12)


def test_inferred_control_gets_the_delay_of_its_inferred_control():
    """ADR-0016: the OBSERVED gate protects the unsafe COUNT, not the time. An
    untagged mixed-rank junction is inferred as a 2-way stop, and waits like
    one."""
    pack, _ = cross_with_control(None)
    assert (pack.edge_control_confidence == ControlConfidence.INFERRED).any()
    guessed = compute_costs(pack, free_flow(pack, CFG), CFG).turn_delay_s
    _, observed, _ = _delays(Control.STOP_2WAY)
    np.testing.assert_array_equal(guessed, observed.turn_delay_s)
    assert guessed.max() > 0.0


def test_turn_time_is_link_time_plus_delay():
    pack, qc, _ = _delays(Control.STOP_2WAY)
    np.testing.assert_array_equal(
        qc.turn_time_s, qc.edge_time_s[pack.turn_out_edge] + qc.turn_delay_s)
    assert np.all(qc.turn_delay_s >= 0.0)


def test_delay_is_independent_of_lambda():
    """It is time, so it sits inside every lambda's arc cost unchanged."""
    from pyref.costs import arc_cost
    pack, qc, _ = _delays(Control.STOP_2WAY)
    np.testing.assert_array_equal(arc_cost(pack, qc, 0.0), qc.turn_time_s)
    for lam in (0.5, 1.5):
        np.testing.assert_allclose(
            arc_cost(pack, qc, lam) - lam * qc.turn_penalty_s, qc.turn_time_s,
            rtol=1e-12)
