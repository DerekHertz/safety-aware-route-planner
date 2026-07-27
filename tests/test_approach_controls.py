"""Harvesting intersection control from the unsimplified graph.

These tests build raw OSM-shaped graphs — junction node, interstitial control
nodes on the approach arms, neighbouring junctions — and check what
ingestion.approach_controls.harvest() makes of them. That interstitial layer is
exactly what OSMnx simplification deletes, and its loss is what made a fully
signalized intersection report as an uncontrolled crossing.
"""
import math

import networkx as nx

from ingestion.approach_controls import ApproachControl, harvest
from pyref.geo import EARTH_RADIUS_M
from pyref.graph import Control, ControlConfidence

# Degrees of latitude per metre. Arms are laid out around the equator so the
# same factor works for longitude.
_DEG_PER_M = 360.0 / (2.0 * math.pi * EARTH_RADIUS_M)

_ARM_BEARINGS = {"north": (1, 0), "south": (-1, 0), "east": (0, 1), "west": (0, -1)}
# North/south are the two arms of one street, east/west of the other — the
# pairing _harmonize_opposite_arms relies on.
_ARM_STREETS = {"north": "Main Street", "south": "Main Street",
                "east": "Cross Street", "west": "Cross Street"}
_FAR_M = 200.0


class _Raw:
    """Minimal stand-in for an unsimplified OSMnx drive graph."""

    def __init__(self):
        self.G = nx.MultiDiGraph()
        self._next = 0
        self._way = 1000

    def node(self, north_m: float = 0.0, east_m: float = 0.0, **tags) -> int:
        nid = self._next
        self._next += 1
        self.G.add_node(nid, y=north_m * _DEG_PER_M, x=east_m * _DEG_PER_M, **tags)
        return nid

    def way(self, *nodes: int, oneway: bool = False, name: str | None = None) -> None:
        """Chain `nodes` in OSM way order. Two-way ways get the synthetic
        opposite-direction edges OSMnx marks with reversed=True."""
        way = self._way
        self._way += 1
        attrs = {"osmid": way}
        if name is not None:
            attrs["name"] = name
        for u, v in zip(nodes, nodes[1:]):
            self.G.add_edge(u, v, reversed=False, **attrs)
            if not oneway:
                self.G.add_edge(v, u, reversed=True, **attrs)


def _cross(arms: dict[str, list[tuple[float, dict]]], *, named: bool = True,
           **junction_tags):
    """A 4-arm cross with the given control nodes on its approach arms.

    `arms` maps an arm name to (distance_from_junction_m, tags) pairs. Each arm
    runs out to a neighbouring junction at _FAR_M, so every arm terminates at a
    node the routing graph would keep.

    `named=False` leaves the ways unnamed, which suppresses opposite-arm
    harmonization.

    Returns (raw_graph, junction_nodes, junction_id, {arm_name: far_node_id}).
    """
    r = _Raw()
    junction = r.node(**junction_tags)
    far: dict[str, int] = {}
    for name, (dn, de) in _ARM_BEARINGS.items():
        chain = [junction]
        for dist, tags in sorted(arms.get(name, [])):
            chain.append(r.node(dn * dist, de * dist, **tags))
        far[name] = r.node(dn * _FAR_M, de * _FAR_M)
        chain.append(far[name])
        r.way(*chain, name=_ARM_STREETS[name] if named else None)
    return r.G, {junction, *far.values()}, junction, far


def _controls(arms, *, max_offset_m: float = 30.0, named: bool = True,
              **junction_tags):
    """(resolved, junction, far) for a cross, keyed by arm name."""
    G, junctions, junction, far = _cross(arms, named=named, **junction_tags)
    found = harvest(G, junctions, max_offset_m)
    by_arm = {name: found.get((junction, node)) for name, node in far.items()}
    return by_arm, junction, far


# --------------------------------------------------------------------- signals
def test_signal_on_one_arm_controls_the_whole_junction():
    """The reported bug, as a regression test.

    A signalized 4-way is normally mapped with a traffic_signals node on each
    approach arm rather than on the junction node — and sometimes on only one
    arm. Every leg is controlled either way, so no approach may come out
    uncontrolled.
    """
    by_arm, _, _ = _controls({"south": [(12.0, {"highway": "traffic_signals"})]})
    assert set(by_arm) == set(_ARM_BEARINGS)
    for name, approach in by_arm.items():
        assert approach is not None, name
        assert approach.control == Control.SIGNAL_PERMISSIVE
        assert approach.must_stop is False
        assert approach.confidence == ControlConfidence.OBSERVED


def test_signal_on_the_junction_node_itself():
    by_arm, _, _ = _controls({}, highway="traffic_signals")
    assert all(a.control == Control.SIGNAL_PERMISSIVE for a in by_arm.values())


def test_signal_beats_stop_signs_on_other_arms():
    """A signal upgrade sometimes leaves stale stop nodes on the side streets.
    The signal governs."""
    by_arm, _, _ = _controls({
        "south": [(12.0, {"highway": "stop"})],
        "north": [(12.0, {"highway": "stop"})],
        "east": [(10.0, {"highway": "traffic_signals"})],
    })
    assert all(a.control == Control.SIGNAL_PERMISSIVE for a in by_arm.values())


def test_pedestrian_signal_is_not_junction_control():
    """A signalized crosswalk near the corner controls pedestrians, not the
    intersection. Treating it as junction control would hide a real hazard."""
    by_arm, _, _ = _controls({
        "south": [(12.0, {"highway": "traffic_signals",
                          "traffic_signals": "pedestrian_crossing"})],
        "north": [(12.0, {"highway": "traffic_signals", "crossing": "traffic_signals"})],
    })
    assert all(a is None for a in by_arm.values())


# ----------------------------------------------------------------- stop signs
def test_stop_on_every_arm_is_an_all_way_stop():
    """`stop=all` is nearly never tagged; a stop node per arm is how an
    all-way stop actually appears in OSM."""
    by_arm, _, _ = _controls({
        name: [(12.0, {"highway": "stop"})] for name in _ARM_BEARINGS
    })
    for approach in by_arm.values():
        assert approach.control == Control.STOP_4WAY
        assert approach.must_stop is True


def test_stop_on_two_arms_gives_observed_must_stop():
    """The minor street stops, the major street does not — read off which arm
    the stop node sits on, rather than guessed from road class."""
    by_arm, _, _ = _controls({
        "south": [(12.0, {"highway": "stop"})],
        "north": [(12.0, {"highway": "stop"})],
    })
    assert by_arm["south"].control == Control.STOP_2WAY
    assert by_arm["south"].must_stop is True
    assert by_arm["north"].must_stop is True
    # The uncontrolled arms hold priority through the same node.
    assert by_arm["east"].control == Control.STOP_2WAY
    assert by_arm["east"].must_stop is False
    assert by_arm["west"].must_stop is False


def test_stop_all_tag_on_the_junction_wins_without_per_arm_nodes():
    by_arm, _, _ = _controls({"south": [(12.0, {"highway": "stop"})]},
                             highway="stop", stop="all")
    assert all(a.control == Control.STOP_4WAY for a in by_arm.values())


def test_give_way_becomes_yield():
    by_arm, _, _ = _controls({"south": [(12.0, {"highway": "give_way"})]})
    assert by_arm["south"].control == Control.YIELD
    assert by_arm["south"].must_stop is True
    assert by_arm["east"].must_stop is False       # the other street has priority


def test_a_stop_sign_beats_a_stray_give_way_on_another_arm():
    """A junction is stop-controlled or give-way-controlled, not both. A
    give_way alongside stop signs is a yield-to-pedestrians marking or a
    leftover — believing it made the through street "must yield" in one
    direction while the opposite direction kept priority."""
    by_arm, _, _ = _controls({
        "south": [(12.0, {"highway": "stop"})],
        "north": [(12.0, {"highway": "stop"})],
        "east": [(12.0, {"highway": "give_way"})],
    })
    assert by_arm["south"].control == Control.STOP_2WAY
    assert by_arm["south"].must_stop is True
    for arm in ("east", "west"):
        assert by_arm[arm].control == Control.STOP_2WAY
        assert by_arm[arm].must_stop is False, arm


# ------------------------------------------------- opposite-arm harmonization
def test_a_stop_tagged_on_one_arm_governs_both_directions():
    """A device that governs a road governs both directions of it. Tagging
    only one arm is a mapping gap, not an asymmetric intersection."""
    by_arm, _, _ = _controls({"south": [(12.0, {"highway": "stop"})]})
    assert by_arm["south"].must_stop is True
    assert by_arm["north"].must_stop is True       # same street, harmonized
    assert by_arm["east"].must_stop is False       # the cross street keeps priority
    assert by_arm["west"].must_stop is False


def test_harmonization_can_complete_an_all_way_stop():
    by_arm, _, _ = _controls({
        "south": [(12.0, {"highway": "stop"})],
        "east": [(12.0, {"highway": "stop"})],
    })
    assert all(a.control == Control.STOP_4WAY for a in by_arm.values())


def test_unnamed_arms_are_not_harmonized():
    """Pairing is by street name; without one there is no way to know which
    arm is the far side of the same road."""
    by_arm, _, _ = _controls({"south": [(12.0, {"highway": "stop"})]}, named=False)
    assert by_arm["south"].must_stop is True
    assert by_arm["north"].must_stop is False


# ------------------------------------------------------------------- distance
def test_control_node_beyond_the_offset_is_ignored():
    """A stop node most of the way down the block belongs to the next
    intersection, not this one."""
    by_arm, _, _ = _controls({"south": [(80.0, {"highway": "stop"})]},
                             max_offset_m=30.0)
    assert all(a is None for a in by_arm.values())


def test_offset_is_measured_along_the_arm():
    by_arm, _, _ = _controls({"south": [(25.0, {"highway": "stop"})]},
                             max_offset_m=30.0)
    assert by_arm["south"].control == Control.STOP_2WAY


def test_no_evidence_returns_nothing_so_the_caller_can_infer():
    by_arm, _, _ = _controls({})
    assert all(a is None for a in by_arm.values())


# ------------------------------------------------------------------ direction
def test_direction_tag_restricts_which_approach_a_stop_governs():
    """`direction` is relative to the way's node order. Each arm here is
    digitized outward from the junction, so traffic approaching the junction
    travels *backward* along the way — a `direction=backward` stop applies to
    it and a `direction=forward` one does not.
    """
    inbound, _, _ = _controls({
        "south": [(12.0, {"highway": "stop", "direction": "backward"})]})
    assert inbound["south"].control == Control.STOP_2WAY
    assert inbound["south"].must_stop is True

    outbound, _, _ = _controls({
        "south": [(12.0, {"highway": "stop", "direction": "forward"})]})
    assert all(a is None for a in outbound.values())


def test_unresolvable_direction_still_applies():
    """Placement alone already implies the approach: a control node sits at the
    stop line facing inbound traffic. When the way's orientation can't be read,
    keep the control rather than discard real evidence."""
    G, junctions, junction, far = _cross(
        {"south": [(12.0, {"highway": "stop", "direction": "forward"})]})
    for _, _, data in G.edges(data=True):
        data.pop("reversed", None)
    found = harvest(G, junctions, 30.0)
    assert found[(junction, far["south"])].control == Control.STOP_2WAY


# ----------------------------------------------------------------- boundaries
def test_tags_on_the_neighbouring_junction_do_not_leak_back():
    """A control node AT the far end of an arm belongs to that junction's own
    approaches. Only interstitial nodes count for this one."""
    r = _Raw()
    junction = r.node()
    near = r.node(0.0, -20.0)                     # 20 m west, no tags
    neighbour = r.node(0.0, -40.0, highway="stop")
    r.way(junction, near, neighbour)
    found = harvest(r.G, {junction, neighbour}, 60.0)
    assert (junction, neighbour) not in found


def test_junction_missing_from_the_raw_graph_is_skipped():
    """The simplified and unsimplified graphs can differ at the bbox
    periphery; a junction absent from the raw graph must not raise."""
    G, junctions, junction, far = _cross(
        {"south": [(12.0, {"highway": "stop"})]})
    found = harvest(G, junctions | {99999}, 30.0)
    assert found[(junction, far["south"])].control == Control.STOP_2WAY


# ------------------------------------------------------- into the built pack
def test_build_pack_prefers_harvested_control_over_the_heuristic():
    """The other half of the fix: what harvest() finds has to actually reach
    the pack, overriding the road-class guess and marking the approach
    OBSERVED so pyref/costs.py will count it."""
    from pyref.config import Config
    from pyref.graph import ControlConfidence, RoadClass
    from tests.helpers.toy_graphs import GraphBuilder, find_edge

    b = GraphBuilder(Config.load())
    c = b.node(0.0, 0.0)                    # untagged: heuristic territory
    n, s = b.node(0.004, 0.0), b.node(-0.004, 0.0)
    e, w = b.node(0.0, 0.004), b.node(0.0, -0.004)
    b.edge(s, c, length_m=500.0)
    b.edge(c, n, length_m=500.0)
    b.edge(w, c, road_class=RoadClass.primary, speed_kph=56, lanes=2, length_m=500.0)
    b.edge(c, e, road_class=RoadClass.primary, speed_kph=56, lanes=2, length_m=500.0)

    guessed = b.build()
    e_in = find_edge(guessed, s, c)
    assert guessed.edge_approach_control[e_in] == Control.STOP_2WAY   # a guess
    assert guessed.edge_control_confidence[e_in] == ControlConfidence.INFERRED

    # ...now say a traffic_signals node was found on one of the arms.
    observed = {(c, far): ApproachControl(Control.SIGNAL_PERMISSIVE, False)
                for far in (n, s, e, w)}
    harvested = b.build(observed_controls=observed)
    assert harvested.edge_approach_control[e_in] == Control.SIGNAL_PERMISSIVE
    assert harvested.edge_control_confidence[e_in] == ControlConfidence.OBSERVED
    assert harvested.edge_must_stop[e_in] == 0
