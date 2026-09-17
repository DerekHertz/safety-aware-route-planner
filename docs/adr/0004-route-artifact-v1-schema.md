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

**v1 is implemented.** All three tiers exist in `api/schemas.py`: render (`Segment.tier`,
`UnsafeCounts`, `detour_pct`), narrate (`maneuvers`), and re-plan (the `preference` object
plus `schema_version`, shipped in PR #32). The contract is enforced by
`tests/test_route_artifact_v1.py` and the `schema-sync` workflow.

**v2 is planned**, adding a `traffic_basis` field to `preference`: a source identifier plus
snapshot timestamp recording what traffic inputs the route was computed against. The
reproducer params are insufficient without it once traffic is not a pure function of the
clock, because a consumer diffing two artifacts cannot otherwise distinguish "traffic
changed" from "these were computed against different data" -- which is exactly what the
commute planner's disruption detection rests on (ADR-0011). It also makes explicit that an
artifact is **half perishable**: `eta_s` and segment timings go stale while unsafe counts
and tiers stay reproducible. See ADR-0010 for why the basis is currently `synthetic`.
