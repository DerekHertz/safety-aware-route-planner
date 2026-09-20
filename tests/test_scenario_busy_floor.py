"""THE named scenario test (ADR-0005 amendment): a primary arterial must stay
"busy" at 3am even though the time-varying volume term alone would let it
drop below busy_threshold. Read literally, without a floor, this is exactly
the accident ADR-0005 called out: a default primary arterial clears "not
busy" by 0.026 at 3am, and a 0.03 nudge to a_speed would silently flip every
arterial's busy-ness with no code change. busy_floor_by_class makes "which
roads are always busy" an explicit, named, testable decision instead.
"""
import datetime

from pyref.config import Config
from pyref.costs import compute_costs
from pyref.graph import RoadClass
from sim.snapshot import at_time
from tests.helpers.toy_graphs import GraphBuilder, find_edge

CFG = Config.load()


def _unfloored_busy_score(speed_kph: float, lanes: float, vol_vph_lane: float) -> float:
    """The busy formula as it stood BEFORE ADR-0005's floor -- recomputed by
    hand here (not imported from pyref.costs) so the test pins the intended
    behaviour independently of the implementation it is checking."""
    cc, bc = CFG["cost"], CFG["busy"]
    spd_n = min(1.0, (speed_kph / 3.6) / cc["speed_norm_max_mps"])
    lanes_n = min(1.0, lanes / cc["lanes_norm_max"])
    vol_n = min(1.0, vol_vph_lane / cc["vol_norm_max_vph"])
    return bc["a_speed"] * spd_n + bc["a_lanes"] * lanes_n + bc["a_vol"] * vol_n


def test_3am_primary_arterial_stays_busy_via_the_floor():
    """Default primary arterial (56 km/h, 2 lanes/direction -- ADR-0005's own
    example), queried at 3am. The floor -- not the volume term -- is what
    makes it "busy": deliberately, and pinned here, instead of by accident."""
    b = GraphBuilder()
    a = b.node(0.0, 0.0)
    c = b.node(0.0, 0.004)
    b.edge(a, c, road_class=RoadClass.primary, speed_kph=56, lanes=2, length_m=500.0)
    pack = b.build()
    e = find_edge(pack, a, c)

    night = datetime.datetime(2026, 7, 22, 3, 0)
    snap = at_time(pack, CFG, night)
    qc = compute_costs(pack, snap, CFG)

    # Without a floor, this is the ADR-0005 accident: the raw score sits
    # under busy_threshold at 3am, so "not busy" would depend on how close
    # a_speed/a_lanes/a_vol happen to sit to the line that day.
    raw_score = _unfloored_busy_score(56.0, 2.0, float(snap.volume_vph_lane[e]))
    assert raw_score < CFG["busy"]["busy_threshold"], (
        "fixture or weights drifted off the ADR-0005 case -- the unfloored "
        "score is no longer under threshold at 3am, so this test would stop "
        "pinning anything meaningful")

    # The floor holds regardless: comfortably above threshold, so a small
    # future nudge to a_speed/a_lanes/a_vol cannot silently flip it back.
    floor = float(CFG["busy"]["busy_floor_by_class"]["primary"])
    assert floor > CFG["busy"]["busy_threshold"]

    assert bool(qc.edge_busy[e]), (
        "a primary arterial must never read as 'not busy' just because it "
        "is 3am -- ADR-0005: 'a four-lane arterial does not become benign "
        "because it is empty'")


def test_3am_primary_arterial_busy_at_every_hour_not_just_midnight():
    """The floor is a road-character floor, not a midnight special case: the
    same primary arterial reads busy at peak too, for the ordinary
    (non-floor) reason that peak volume alone already clears threshold."""
    b = GraphBuilder()
    a = b.node(0.0, 0.0)
    c = b.node(0.0, 0.004)
    b.edge(a, c, road_class=RoadClass.primary, speed_kph=56, lanes=2, length_m=500.0)
    pack = b.build()
    e = find_edge(pack, a, c)

    for hour in (0, 3, 8, 12, 17, 23):
        snap = at_time(pack, CFG, datetime.datetime(2026, 7, 22, hour, 0))
        qc = compute_costs(pack, snap, CFG)
        assert bool(qc.edge_busy[e]), f"primary arterial read not-busy at hour {hour}"


def test_collector_with_no_floor_configured_still_de_rates_at_night():
    """Requirement (c): a class nobody named in busy_floor_by_class floors at
    0.0 -- i.e. no floor at all, identical to pre-ADR-0005 behaviour. Uses a
    secondary (collector-group) road, which ADR-0005 never asked to pin."""
    assert "secondary" not in CFG["busy"]["busy_floor_by_class"]

    b = GraphBuilder()
    a = b.node(0.0, 0.0)
    c = b.node(0.0, 0.004)
    b.edge(a, c, road_class=RoadClass.secondary, speed_kph=48, lanes=2, length_m=500.0)
    pack = b.build()
    e = find_edge(pack, a, c)

    peak = compute_costs(
        pack, at_time(pack, CFG, datetime.datetime(2026, 7, 22, 17, 30)), CFG)
    night = compute_costs(
        pack, at_time(pack, CFG, datetime.datetime(2026, 7, 22, 3, 0)), CFG)
    assert bool(peak.edge_busy[e])
    assert not bool(night.edge_busy[e])
