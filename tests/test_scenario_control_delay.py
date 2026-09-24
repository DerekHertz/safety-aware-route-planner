"""THE named scenario for ADR-0016: the grocery run.

A driver on a side street needs to get across a busy four-lane arterial. The
direct line crosses it at a two-way stop, where the arterial does not stop;
a signal sits one block over. At rush hour the gap wait at the stop sign is
well over a minute, and going to the light is faster even counting the
detour. At 3 am the arterial is empty, the gap wait is about a second, and
the direct crossing wins.

        N0                               residential (36 km/h)
        |
  AW == A0 ========== A1 == AE           4-lane primary arterial (56 km/h)
        |             |
        S0 ---------- S1                 residential

Origin S0, destination N0. A0 is a 2-way stop (the side street stops, the
arterial does not); A1 is signalized. Side-street blocks are 100 m, the
block over (S0-S1, A0-A1) 150 m.

  * direct: S0->A0->N0, one straight crossing from the stop line, a gap wait
    against both arterial directions;
  * signal: S0->S1->A1, left at the light from the minor approach (no
    oncoming leg at this T), along the arterial to A0, right onto N0 with
    the right of way (no wait).

The corner at S1 is a two-leg node: nothing conflicts, no delay. There is no
N1: with a parallel street north of the arterial, "cross at the light" and
"turn left at the light, then right at A0" tie to a tenth of a second, and a
scenario test should not hinge on a tie.
"""
from __future__ import annotations

import datetime
import math

import pytest

from pyref.config import Config
from pyref.costs import compute_costs
from pyref.graph import Control, RoadClass
from pyref.metrics import compute_metrics
from sim.profiles import multipliers_at
from sim.snapshot import at_time
from tests.helpers.fixtures import find_turn, route_between_nodes
from tests.helpers.toy_graphs import GraphBuilder, find_edge

CFG = Config.load()
CD = CFG["sim"]["control_delay"]

PEAK = datetime.datetime(2026, 7, 22, 17, 30)   # weekday evening rush
NIGHT = datetime.datetime(2026, 7, 22, 3, 0)

SIDE_M = 100.0     # side street blocks: S0-A0, A0-N0, S1-A1
BLOCK_M = 150.0    # one block over: S0-S1 and the arterial blocks


def grocery_run():
    b = GraphBuilder()
    dlat, dlon = 0.0008, 0.0012          # ~89 m / ~133 m: below edge lengths
    a0 = b.node(0.0, 0.0, control=Control.STOP_2WAY)
    a1 = b.node(0.0, dlon, control=Control.SIGNAL_PERMISSIVE)
    aw = b.node(0.0, -dlon)
    ae = b.node(0.0, 2 * dlon)
    s0 = b.node(-dlat, 0.0)
    s1 = b.node(-dlat, dlon)
    n0 = b.node(dlat, 0.0)
    arterial = dict(road_class=RoadClass.primary, speed_kph=56, lanes=2,
                    length_m=BLOCK_M)
    b.edge(aw, a0, **arterial)
    b.edge(a0, a1, **arterial)
    b.edge(a1, ae, **arterial)
    b.edge(s0, a0, length_m=SIDE_M)
    b.edge(a0, n0, length_m=SIDE_M)
    b.edge(s1, a1, length_m=SIDE_M)
    b.edge(s0, s1, length_m=BLOCK_M)
    ids = dict(a0=a0, a1=a1, aw=aw, ae=ae, s0=s0, s1=s1, n0=n0)
    return b.build(), ids


def _hand_times(when: datetime.datetime) -> tuple[float, float]:
    """(direct, signal) ETAs from first principles."""
    speed, vol = multipliers_at(CFG, when)
    res_v = 10.0 * speed[RoadClass.residential]                 # m/s
    art_v = (56.0 / 3.6) * speed[RoadClass.primary]
    art_q = (float(CFG["sim"]["base_vph_per_lane"]["primary"])
             * vol[RoadClass.primary] * 2)                       # veh/h per dir
    # crossing both arterial directions from the stop line
    q = 2 * art_q / 3600.0
    t_c = float(CD["t_c_crossing_s"])
    gap = min((math.exp(q * t_c) - q * t_c - 1.0) / q, float(CD["cap_s"]))
    direct = 2 * SIDE_M / res_v + gap
    # the side street is the minor approach at the light; its left there has
    # no oncoming leg, and the right at A0 is made with the right of way
    signal = ((BLOCK_M + 2 * SIDE_M) / res_v + BLOCK_M / art_v
              + float(CD["signal_minor_s"]))
    return direct, signal


def _fast(when):
    pack, ids = grocery_run()
    qc = compute_costs(pack, at_time(pack, CFG, when), CFG)
    r = route_between_nodes(pack, qc, ids["s0"], ids["n0"], lam=0.0)
    return pack, ids, qc, r


def test_rush_hour_the_fast_route_goes_to_the_light():
    direct, signal = _hand_times(PEAK)
    assert direct > 100.0          # more than a minute and a half of waiting
    assert signal < direct

    pack, ids, qc, r = _fast(PEAK)
    edges = r.edges(pack.turn_out_edge)
    assert edges == [
        find_edge(pack, ids["s0"], ids["s1"]),
        find_edge(pack, ids["s1"], ids["a1"]),
        find_edge(pack, ids["a1"], ids["a0"]),
        find_edge(pack, ids["a0"], ids["n0"]),
    ]
    m = compute_metrics(pack, qc, r)
    assert m.eta_s == pytest.approx(signal, rel=1e-9)
    assert m.control_delay_s == pytest.approx(float(CD["signal_minor_s"]),
                                              rel=1e-12)


def test_three_am_the_fast_route_crosses_directly():
    direct, signal = _hand_times(NIGHT)
    assert direct < signal

    pack, ids, qc, r = _fast(NIGHT)
    assert r.edges(pack.turn_out_edge) == [
        find_edge(pack, ids["s0"], ids["a0"]),
        find_edge(pack, ids["a0"], ids["n0"]),
    ]
    m = compute_metrics(pack, qc, r)
    assert m.eta_s == pytest.approx(direct, rel=1e-9)
    assert 0.0 < m.control_delay_s < 2.0      # "about a second"


def test_the_crossing_wait_itself_moves_with_the_hour():
    """The same turn, two departures: the only input that moved is volume."""
    pack, ids = grocery_run()
    t = find_turn(pack, find_edge(pack, ids["s0"], ids["a0"]),
                  find_edge(pack, ids["a0"], ids["n0"]))
    peak = compute_costs(pack, at_time(pack, CFG, PEAK), CFG).turn_delay_s[t]
    night = compute_costs(pack, at_time(pack, CFG, NIGHT), CFG).turn_delay_s[t]
    assert peak > 100.0 * night
