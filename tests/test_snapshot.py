"""Traffic-profile interpolation and snapshot wiring."""
import datetime

import numpy as np
import pytest

from pyref.config import Config
from pyref.graph import RoadClass
from sim.profiles import class_group, interpolate_hourly, multipliers_at
from sim.snapshot import at_time, free_flow
from tests.helpers.fixtures import cross_with_control
from pyref.graph import Control


CFG = Config.load()


def test_hour_center_exact():
    table = list(np.linspace(0.1, 1.0, 24))
    for h in range(24):
        assert interpolate_hourly(table, h + 0.5) == pytest.approx(table[h])


def test_interpolation_midpoint():
    table = [0.0] * 24
    table[8] = 1.0
    # halfway between centers 7.5 and 8.5 -> mean of entries 7 and 8
    assert interpolate_hourly(table, 8.0) == pytest.approx(0.5)


def test_midnight_wrap():
    table = [0.0] * 24
    table[23] = 1.0
    table[0] = 0.5
    # 0.0 hour sits between centers 23.5 (weight .5) and 0.5 (weight .5)
    assert interpolate_hourly(table, 0.0) == pytest.approx(0.75)


def test_class_groups_cover_all_classes():
    for rc in RoadClass:
        assert class_group(rc, CFG) in ("arterial", "collector", "local")


def test_rush_hour_slower_than_night():
    am_peak = datetime.datetime(2026, 7, 22, 8, 0)
    night = datetime.datetime(2026, 7, 22, 3, 0)
    s_peak, v_peak = multipliers_at(CFG, am_peak)
    s_night, v_night = multipliers_at(CFG, night)
    prim = RoadClass.primary.value
    assert s_peak[prim] < s_night[prim]
    assert v_peak[prim] > v_night[prim]


def test_snapshot_scales_edge_time():
    pack, _ = cross_with_control(Control.NONE)
    am = at_time(pack, CFG, datetime.datetime(2026, 7, 22, 8, 0))
    ff = free_flow(pack, CFG)
    # congestion can only slow edges down (speed_mult <= 1)
    assert np.all(am.speed_mult <= 1.0 + 1e-12)
    assert np.all(am.edge_time_s >= ff.edge_time_s - 1e-12)
    # deterministic: same departure -> identical snapshot
    am2 = at_time(pack, CFG, datetime.datetime(2026, 7, 22, 8, 0))
    assert np.array_equal(am.edge_time_s, am2.edge_time_s)
    assert np.array_equal(am.volume_vph_lane, am2.volume_vph_lane)


def test_busyness_is_time_of_day_dependent():
    """A secondary street should be busy at rush hour but not at 3am."""
    from pyref.costs import compute_costs
    from tests.helpers.toy_graphs import GraphBuilder, find_edge

    b = GraphBuilder()
    a = b.node(0.0, 0.0)
    c = b.node(0.0, 0.004)
    b.edge(a, c, road_class=RoadClass.secondary, speed_kph=48, lanes=2,
           length_m=500.0)
    pack = b.build()
    e = find_edge(pack, a, c)

    peak = compute_costs(pack, at_time(pack, CFG, datetime.datetime(2026, 7, 22, 17, 30)), CFG)
    night = compute_costs(pack, at_time(pack, CFG, datetime.datetime(2026, 7, 22, 3, 0)), CFG)
    assert bool(peak.edge_busy[e])
    assert not bool(night.edge_busy[e])
