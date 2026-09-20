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

**v2 is implemented, 2026-09-19.** `preference` carries a `traffic_basis`: a source
identifier plus snapshot timestamp recording what traffic inputs the route was computed
against, and `schema_version` is `2`. The reproducer params are insufficient without it
once traffic is not a pure function of the clock, because a consumer diffing two artifacts
cannot otherwise distinguish "traffic changed" from "these were computed against different
data" -- which is exactly what the commute planner's disruption detection rests on
(ADR-0011). It also makes explicit that an artifact is **half perishable**: `eta_s` and
segment timings go stale while unsafe counts and tiers stay reproducible. See ADR-0010 for
why the basis is currently `synthetic`.

## v2 as shipped, 2026-09-19

Three notes on the shape, because each resolves something the v2 sketch above left open.

**It is a nested object, `{source, as_of, profile_version}`.** The basis is minted in
`sim/snapshot.py` -- where the traffic inputs actually are -- and forwarded unchanged by
the engine, so ADR-0010's "a data swap, not an architecture change" holds literally: a
real feed changes the *values* at one key and touches nothing else on the wire. `source`
is a constant in the snapshot module rather than a config knob, because a knob could be
set to `inrix` while the hand-authored profiles were still running, and provenance that
can lie is worse than none.

**`as_of` currently duplicates `departure_time`, on purpose.**
`sim.profiles.multipliers_at` reads nothing but the departure clock, so the instant these
inputs were "observed" *is* the instant they describe -- ADR-0010's "two replans for the
same departure time are byte-identical". Shipping the field anyway is what makes the
future divergence a value change rather than a schema change; the equality is asserted by
a test whose docstring says it is expected to stop holding. `profile_version` -- a content
hash of the `[sim]` table, scoped to `[sim]` so an unrelated config edit does not move it
-- is the half that carries real information today, and is what makes "somebody retuned
the synthetic profiles" detectable from two artifacts alone.

**Required on output, optional on input.** `Preference` is also the request body on
`/reroute`, carried verbatim off whatever artifact a client is following -- possibly a v1
one. A newly required request field is not an additive change and would 422 an in-flight
nav session at its first reroute, the exact session ADR-0008's reroute exists to keep
alive. So `RerouteRequest` takes a `CarriedPreference`: a sibling of `Preference` -- both
extend a private base holding the four reproducer params -- with `traffic_basis` relaxed
to optional. A sibling rather than a subclass because a preference whose basis may be
missing is not substitutable for one that guarantees it, which mypy rejects outright. The
server ignores the carried value and reports the basis of the snapshot it actually
computed, because echoing it would label a new artifact with inputs it never used.
