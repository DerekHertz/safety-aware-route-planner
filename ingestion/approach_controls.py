"""Observed intersection control, harvested from the UNSIMPLIFIED OSM graph.

Why this module exists
----------------------
OSMnx simplification (see ingestion/download.py) deletes degree-2 interstitial
nodes and discards their tags. In OSM, stop signs and traffic signals are
normally mapped on the *approach arm* at the stop line, several metres before
the junction node — exactly the nodes simplification removes. Measured against
the berkeley_oakland Overpass response, that discarded 87% of `highway=stop`
nodes and 25% of `highway=traffic_signals` nodes, so classify_node_control()
in ingestion/controls.py found nothing on the junction node and fell through to
its road-class heuristic, reporting fully signalized intersections as
uncontrolled crossings.

This module walks the unsimplified graph outward from every junction that
survives into the routing graph, collects the control nodes sitting on each
approach arm within `max_control_offset_m`, and resolves them into a
per-approach (control, must_stop) pair with OBSERVED confidence.

Resolution rules, in priority order, per junction:

  1. A traffic signal on ANY arm signalizes the WHOLE junction. Real signals
     control every leg, so every approach becomes SIGNAL_PERMISSIVE — this is
     the rule that stops a signalized 4-way from being reported as an
     uncontrolled crossing. Pedestrian-only signals are excluded; see
     _is_pedestrian_signal.
  2. mini_roundabout on the junction node -> ROUNDABOUT.
  3. Stop nodes -> STOP_4WAY when `stop=all` is tagged OR every arm carries a
     stop node. The second half is where most of the accuracy comes from:
     `stop=all` is nearly never tagged, but a stop node per arm is the normal
     mapping of an all-way stop. Otherwise STOP_2WAY, with must_stop set per
     arm FROM OBSERVATION rather than guessed from road class.
  4. give_way nodes -> YIELD, per arm, same shape as the STOP_2WAY case — but
     only where no arm has a stop sign (see _resolve_junction).
  5. Nothing found -> no entry is returned; the caller falls back to the
     heuristic in controls.py and marks the approach INFERRED.

Before those rules run, evidence is harmonized across the two arms of each
named street (_harmonize_opposite_arms): a device that governs a road governs
both directions of it, and believing otherwise makes routes leave a through
street mid-block.

Everything this module returns is ControlConfidence.OBSERVED — it came from a
tag, not a guess.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from ingestion.controls import tag_first
from pyref.geo import haversine_m
from pyref.graph import Control, ControlConfidence

# A walk down one arm gives up after this many nodes. Purely a guard against
# pathological geometry (rings, self-intersecting ways); real arms between two
# junctions are a handful of nodes long.
_MAX_WALK_STEPS = 400

# Node `highway` values that constitute intersection control.
SIGNAL = "signal"
STOP = "stop"
GIVE_WAY = "give_way"
MINI_ROUNDABOUT = "mini_roundabout"

_HIGHWAY_TO_KIND = {
    "traffic_signals": SIGNAL,
    "stop": STOP,
    "give_way": GIVE_WAY,
    "mini_roundabout": MINI_ROUNDABOUT,
}

# `traffic_signals=*` values that mark a signal as governing a pedestrian
# crossing rather than a vehicular junction.
_PEDESTRIAN_SIGNAL_VALUES = {"pedestrian_crossing", "crossing", "pedestrian"}


@dataclass(frozen=True)
class ApproachControl:
    """Resolved control for one incoming approach at one junction."""
    control: Control
    must_stop: bool
    confidence: ControlConfidence = ControlConfidence.OBSERVED


# ---------------------------------------------------------------- tag reading
def _is_pedestrian_signal(tags: dict) -> bool:
    """A signalized pedestrian crossing is not junction control.

    Two mappings in the wild: `highway=crossing` + `crossing=traffic_signals`
    (which never reaches _control_kind, since its `highway` is `crossing`), and
    `highway=traffic_signals` + `traffic_signals=pedestrian_crossing`. The
    `crossing` tag alone is enough to disqualify either.
    """
    if tag_first(tags.get("crossing")) is not None:
        return True
    return tag_first(tags.get("traffic_signals")) in _PEDESTRIAN_SIGNAL_VALUES


def _control_kind(tags: dict) -> str | None:
    """SIGNAL / STOP / GIVE_WAY / MINI_ROUNDABOUT for a node, else None."""
    kind = _HIGHWAY_TO_KIND.get(tag_first(tags.get("highway")))
    if kind == SIGNAL and _is_pedestrian_signal(tags):
        return None
    return kind


def _direction_tag(tags: dict, kind: str) -> str | None:
    """The `direction`-family tag governing this control node, if any."""
    specific = {SIGNAL: "traffic_signals:direction", STOP: "stop:direction"}.get(kind)
    if specific is not None:
        value = tag_first(tags.get(specific))
        if value is not None:
            return str(value)
    value = tag_first(tags.get("direction"))
    return None if value is None else str(value)


def _travels_way_forward(G_raw: nx.MultiDiGraph, at: int, toward: int) -> bool | None:
    """Does travelling `at` -> `toward` follow the OSM way's node order?

    OSMnx marks the synthetic opposite-direction edge of a two-way street with
    `reversed=True`. Returns None when the edge is missing or unmarked, in
    which case the caller ignores the direction tag rather than guessing.
    """
    edges = G_raw.get_edge_data(at, toward)
    if not edges:
        return None
    for data in edges.values():
        rev = tag_first(data.get("reversed"))
        if rev is None:
            continue
        if isinstance(rev, str):
            rev = rev.lower() == "true"
        return not bool(rev)
    return None


def _applies_to_approach(G_raw: nx.MultiDiGraph, tags: dict, kind: str,
                         at: int, toward: int) -> bool:
    """Whether a control node governs traffic moving `at` -> `toward`.

    `direction=forward|backward` is relative to the way's node order. When the
    tag is absent, unrecognised, or the way's orientation can't be determined,
    the control applies: a control node sits on the arm at the stop line facing
    inbound traffic, so placement alone already implies the approach it governs.
    """
    value = _direction_tag(tags, kind)
    if value not in ("forward", "backward"):
        return True
    forward = _travels_way_forward(G_raw, at, toward)
    if forward is None:
        return True
    return forward == (value == "forward")


# ---------------------------------------------------------------- arm walking
def _physical_neighbors(G_raw: nx.MultiDiGraph, node: int) -> set[int]:
    """Adjacency ignoring direction — a two-way street is two directed edges."""
    return (set(G_raw.successors(node)) | set(G_raw.predecessors(node))) - {node}


def _street_name(G_raw: nx.MultiDiGraph, a: int, b: int) -> str | None:
    """The street carrying the a-b segment, used to pair up the two arms of one
    road at a junction. None when unnamed — unnamed arms are never paired."""
    for data in (G_raw.get_edge_data(a, b) or {}).values():
        name = tag_first(data.get("name"))
        if name:
            return str(name)
    for data in (G_raw.get_edge_data(b, a) or {}).values():
        name = tag_first(data.get("name"))
        if name:
            return str(name)
    return None


def _walk_arm(G_raw: nx.MultiDiGraph, junction: int, first: int,
              junction_nodes: frozenset[int], max_offset_m: float
              ) -> tuple[int | None, list[tuple[int, str]]]:
    """Follow one arm outward from `junction`, collecting control nodes.

    Walks the chain of interstitial nodes until another junction is reached (or
    the chain forks or dead-ends). Returns the terminal junction node — which
    identifies the routing-graph edge this arm corresponds to — and the control
    nodes found within `max_offset_m` of the junction, as (osmid, kind) pairs.

    The walk deliberately runs to the end of the chain even after passing
    max_offset_m: the terminal node is what maps this arm back to a routing
    edge, so we need it even when the useful evidence stopped earlier. Tags on
    the terminal junction itself are NOT collected — they belong to that
    junction's own approaches, not to this one.
    """
    evidence: list[tuple[int, str]] = []
    prev, cur = junction, first
    dist = haversine_m(G_raw.nodes[prev]["y"], G_raw.nodes[prev]["x"],
                       G_raw.nodes[cur]["y"], G_raw.nodes[cur]["x"])

    for _ in range(_MAX_WALK_STEPS):
        if cur in junction_nodes:
            return cur, evidence
        tags = G_raw.nodes[cur]
        if dist <= max_offset_m:
            kind = _control_kind(tags)
            # Travel on this approach runs toward the junction, i.e. from `cur`
            # back to `prev` — the opposite of the direction we are walking.
            if kind is not None and _applies_to_approach(G_raw, tags, kind, cur, prev):
                evidence.append((cur, kind))
        nxt = _physical_neighbors(G_raw, cur) - {prev}
        if len(nxt) != 1:
            return None, evidence       # dead end, or a fork we can't resolve
        step = nxt.pop()
        dist += haversine_m(G_raw.nodes[cur]["y"], G_raw.nodes[cur]["x"],
                            G_raw.nodes[step]["y"], G_raw.nodes[step]["x"])
        prev, cur = cur, step
    return None, evidence


def _harmonize_opposite_arms(arms: dict[int, set[str]],
                             streets: dict[int, str | None]) -> None:
    """Give both arms of one street the same control evidence, in place.

    A stop sign or give-way tagged on only one of the two arms of a street is
    a mapping gap, not an asymmetric intersection: a device that governs a road
    governs both directions of it. Left alone, one direction of a through
    street ends up "must stop" while the opposite direction keeps priority,
    which is what sent routes fleeing a street mid-block.

    Only named streets are paired — an unnamed arm cannot be matched to its
    opposite with any confidence.
    """
    by_street: dict[str, list[int]] = {}
    for far, name in streets.items():
        if name is not None and far in arms:
            by_street.setdefault(name, []).append(far)
    for same in by_street.values():
        if len(same) < 2:
            continue
        shared = set().union(*(arms[far] for far in same))
        for far in same:
            arms[far] = set(shared)


# ------------------------------------------------------------------- resolving
def _resolve_junction(junction_kind: str | None, junction_tags: dict,
                      arms: dict[int, set[str]]) -> dict[int, ApproachControl]:
    """Per-approach control for one junction, keyed by the arm's far node.

    `arms` maps each resolvable arm's terminal node to the set of control kinds
    observed on it. Returns {} when there is no evidence at all, which tells
    the caller to fall back to inference.
    """
    if junction_kind == SIGNAL or any(SIGNAL in kinds for kinds in arms.values()):
        # Rule 1: a signal governs every leg of the junction, not just the arm
        # it happens to be mapped on.
        return {far: ApproachControl(Control.SIGNAL_PERMISSIVE, False)
                for far in arms}

    if junction_kind == MINI_ROUNDABOUT:
        return {far: ApproachControl(Control.ROUNDABOUT, False) for far in arms}

    stop_arms = {far for far, kinds in arms.items() if STOP in kinds}
    yield_arms = {far for far, kinds in arms.items() if GIVE_WAY in kinds}
    if stop_arms:
        # A junction is stop-controlled or give-way-controlled, not both. Where
        # stop signs are present, a give_way on some other arm is not the
        # junction's control — overwhelmingly it is a yield-to-pedestrians
        # marking at a crosswalk, or a leftover from a re-signed intersection.
        # Believing it made the through street's approaches "must yield" while
        # the opposite direction of the same street kept priority. 198 of the
        # 308 give-way junctions in berkeley_oakland are this mixed case.
        yield_arms = set()
    if not stop_arms and not yield_arms:
        return {}

    if stop_arms:
        # `stop=all` is nearly never tagged; a stop node on every arm is the
        # normal way an all-way stop appears in OSM.
        tagged_all_way = tag_first(junction_tags.get("stop")) == "all"
        every_arm_stops = len(arms) >= 3 and stop_arms == set(arms)
        if tagged_all_way or every_arm_stops:
            return {far: ApproachControl(Control.STOP_4WAY, True) for far in arms}

    out: dict[int, ApproachControl] = {}
    # Arms with no control node of their own hold priority. They take the
    # junction's dominant control type so costs.py can grant them the
    # right-of-way reduction (see pyref/costs.py).
    priority_control = Control.STOP_2WAY if stop_arms else Control.YIELD
    for far in arms:
        if far in stop_arms:
            out[far] = ApproachControl(Control.STOP_2WAY, True)
        elif far in yield_arms:
            out[far] = ApproachControl(Control.YIELD, True)
        else:
            out[far] = ApproachControl(priority_control, False)
    return out


def harvest(G_raw: nx.MultiDiGraph, junction_nodes, max_offset_m: float = 30.0
            ) -> dict[tuple[int, int], ApproachControl]:
    """Observed control per approach, keyed by (junction_osmid, from_osmid).

    `junction_nodes` is the node set of the SIMPLIFIED routing graph — the
    nodes that survive into the pack. `from_osmid` is the routing-graph
    neighbour the approach arrives from, so the key matches a directed edge
    (from_osmid -> junction_osmid) in the simplified graph.

    Junctions absent from `G_raw` (the two graphs can differ marginally at the
    bbox periphery) are skipped, as are junctions with no control evidence;
    both fall back to the inference heuristic in controls.py.
    """
    junction_nodes = frozenset(int(n) for n in junction_nodes)
    out: dict[tuple[int, int], ApproachControl] = {}

    for junction in junction_nodes:
        if junction not in G_raw:
            continue
        junction_tags = G_raw.nodes[junction]
        junction_kind = _control_kind(junction_tags)

        arms: dict[int, set[str]] = {}
        streets: dict[int, str | None] = {}
        for first in _physical_neighbors(G_raw, junction):
            far, evidence = _walk_arm(G_raw, junction, first,
                                      junction_nodes, max_offset_m)
            if far is None:
                continue
            # Two raw neighbours can lead to the same routing-graph node
            # (parallel ways, short divided blocks) — merge their evidence.
            arms.setdefault(far, set()).update(kind for _, kind in evidence)
            streets.setdefault(far, _street_name(G_raw, junction, first))

        if not arms:
            continue
        _harmonize_opposite_arms(arms, streets)
        resolved = _resolve_junction(junction_kind, junction_tags, arms)
        for far, approach in resolved.items():
            out[(junction, far)] = approach

    return out
