---
Status: proposed (amended 2026-09-17)
---

# Search scalability for larger regions (deferred)

**Open question, not yet decided.** The current search is Dijkstra / A\* over the
edge-based turn graph, which is sub-millisecond on a single metro pack but would not scale
to a much larger search space (a whole region or country). Region-agnostic *correctness*
is settled (ADR-0007); region-agnostic *scale* is not.

This is recorded so the limitation is explicit rather than discovered later. Directions to
theorize about when it becomes relevant: contraction hierarchies, boundary/overlay graphs,
tiled packs with cross-tile stitching. Each interacts non-trivially with the edge-based
turn model and the Python↔C++ parity guarantee, so none is a drop-in. No option is chosen
yet.

## Amendment, 2026-09-17

Two findings that change this ADR's framing without resolving it.

**Contraction hierarchies are almost certainly *not available* to this project**, so
listing them as a neutral option was misleading. CH assumes a **static scalar metric**.
This engine's metric is time-dependent (`sim.snapshot.at_time`), *lambda*-parameterized
(three safety levels per request), and re-weighted mid-request by the penalty-method rerun
in `pyref/alternatives.py`. Time-dependent CH exists but is research-grade, and the lambda
sweep would need a separate hierarchy per level. Boundary/overlay graphs and tiled packs
with cross-tile stitching remain open; CH should not be picked up without confronting this
first.

**The first scalability blocker is not the search algorithm.** `pyref/costs.py`
`compute_costs` builds full-graph numpy arrays over **every turn in the pack on every
request**, `heuristic` haversines every edge head, and `arc_cost` allocates another
full-graph array **per lambda and per rerun** (3-8 times per request). At 61,946 turns
that is trivial; it scales linearly with pack size and becomes the binding constraint at
roughly one large metro, well before the search algorithm does. Evaluating turn cost
lazily over the explored frontier instead of materializing the whole graph is both cheaper
than any option listed above and a prerequisite for most of them.

**Related decision:** ADR-0006's identity question was settled in favour of keeping this
project's own engine rather than adopting Valhalla or GraphHopper, on the grounds that the
safety cost model (approach-control confidence gating, per-maneuver control overrides, the
lambda sweep, the detour budget, the penalty-method reruns) is non-standard enough that
porting it into a foreign costing abstraction would mostly deliver parity with what
already exists, at the cost of the Python/C++ parity suite that proves the model correct.
Revisit if coverage commits to nationwide, or if production map-matching is needed (which
the learned-habitual-route idea in ADR-0011 would require, and Valhalla ships Meili).
