# Route artifact v1: three tiers plus a carried preference, schema-versioned

The route artifact (ADR-0001) carries three tiers of information, so any consumer can
render, narrate, and re-plan a route without reaching back into the engine:

1. **Render** — geometry, per-segment safety tiers, distance/ETA, unsafe-action counts by
   type.
2. **Narrate** — per-turn maneuvers (already exposed as `RouteAlternative.maneuvers`).
3. **Re-plan** — a **`preference` object**: the safety-level label (`fast`/`balanced`/
   `safe`) *plus* the resolved reproducer params (λ, detour budget, departure-time basis).

The preference carries both a stable, human-meaningful label *and* the exact params
because each alone is insufficient: the label is coarse and could drift from the λ it maps
to, while raw params are meaningless to a UI. Carrying both lets the reference client show
the label and lets a nav consumer reproduce the exact route on reroute (ADR-0002).

The schema is **versioned and contract-tested** (extending the existing contract and
schema-sync suites), because it is the boundary every consumer depends on (ADR-0001).

## Status

Partially implemented. The render and narrate tiers exist in `api/schemas.py`
(`Segment.tier`, `UnsafeCounts`, `maneuvers`, `detour_pct`). The **`preference` object and
an explicit schema version do not yet exist** — building them is item (1) and (2) of the
robust-core milestone in ADR-0008.
