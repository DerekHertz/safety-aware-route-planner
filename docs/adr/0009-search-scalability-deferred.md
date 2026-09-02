---
Status: proposed
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
