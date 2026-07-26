"""Intersection control classification.

Sources, in priority order:
  1. Explicit OSM node tags: highway=traffic_signals -> SIGNAL_PERMISSIVE
     (permissive-vs-protected is essentially never tagged in OSM, so per the
     spec every signalized approach defaults to SIGNAL_PERMISSIVE;
     SIGNAL_PROTECTED currently only arises in hand-built test graphs),
     highway=stop (+ stop=all -> STOP_4WAY else STOP_2WAY),
     highway=mini_roundabout -> ROUNDABOUT.
  2. Edge tag junction=roundabout/circular on any incident edge -> ROUNDABOUT.
  3. Heuristic inference by road-class rank + node degree (documented below).

Inference heuristic for untagged intersections (>= 3 legs):
  - Mixed road-class ranks meeting: assume STOP_2WAY — the standard case of a
    minor street meeting a major one; the minor (higher-rank-number)
    approaches must stop, the major flows free. This is exactly the
    configuration the "uncontrolled crossing" safety rule targets.
  - All approaches the same rank, 4+ legs, residential-or-smaller: assume
    STOP_4WAY (all-way stops are the norm on Berkeley/Oakland residential
    grids).
  - Everything else (same-rank tertiary+ crossings, 3-leg same-rank tees):
    NONE — uncontrolled. Conservative for safety flagging: crossing a busy
    road at such a node gets counted.
"""
from __future__ import annotations

from pyref.graph import ROAD_CLASS_RANK, Control, RoadClass

_RESIDENTIAL_RANK = ROAD_CLASS_RANK[RoadClass.residential]


def _tag_first(value):
    """OSMnx merges duplicate tags into lists; take the first."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def classify_node_control(node_data: dict, incident_edge_data: list[dict],
                          num_legs: int, approach_ranks: list[int]) -> Control:
    """Control type at one node.

    incident_edge_data: attr dicts of all incident (in+out) edges.
    num_legs: number of distinct neighbor nodes.
    approach_ranks: ROAD_CLASS_RANK of each incoming approach's road class.
    """
    # Explicit override hook: not produced by OSM today, but lets test
    # fixtures (and any future richer dataset) pin a control — notably
    # SIGNAL_PROTECTED, which real OSM tagging can't express.
    override = node_data.get("control_override")
    if override is not None:
        return Control[str(override)]

    hw = _tag_first(node_data.get("highway"))
    if hw == "traffic_signals":
        return Control.SIGNAL_PERMISSIVE
    if hw == "mini_roundabout":
        return Control.ROUNDABOUT
    for ed in incident_edge_data:
        if _tag_first(ed.get("junction")) in ("roundabout", "circular"):
            return Control.ROUNDABOUT
    if hw == "stop":
        if _tag_first(node_data.get("stop")) == "all":
            return Control.STOP_4WAY
        return Control.STOP_2WAY
    # --- untagged: heuristic inference ---
    if num_legs < 3 or not approach_ranks:
        return Control.NONE
    if len(set(approach_ranks)) > 1:
        return Control.STOP_2WAY
    if num_legs >= 4 and approach_ranks[0] >= _RESIDENTIAL_RANK:
        return Control.STOP_4WAY
    return Control.NONE


def resolve_approach(control: Control, approach_rank: int,
                     ranks_at_node: list[int]) -> tuple[Control, bool]:
    """(approach_control, must_stop) for one incoming edge at a node.

    STOP_2WAY: only the minor approaches (higher rank number than the most
    major road at the node) must stop. If every approach shares one rank —
    possible at explicitly tagged stop nodes — we can't tell which legs stop,
    so all are marked must_stop (conservative: flags more crossings, never
    fewer).
    """
    if control == Control.STOP_2WAY:
        min_rank = min(ranks_at_node)
        all_same = len(set(ranks_at_node)) == 1
        return control, (all_same or approach_rank > min_rank)
    if control == Control.STOP_4WAY:
        return control, True
    return control, False
