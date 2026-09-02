# Safety-Aware Route Planner

An in-car route-planning system over real OpenStreetMap data that minimizes travel
time while penalizing two specific *unprotected* maneuvers. This glossary fixes the
project's vocabulary; the "why" behind these terms lives in `docs/adr/`.

## Language

### The thesis

**Safety layer**:
The penalty the engine adds on top of time-optimal routing to discourage unsafe
maneuvers. It never replaces the time objective — it trades against it.

**Unprotected left**:
A left turn onto a busy street under a permissive signal or no control — i.e. not a
protected arrow. One of the two committed unsafe maneuvers.
_Avoid_: unguarded left, unsignalized left.

**Uncontrolled crossing**:
A straight-through crossing of a busy street where the crossing has no signal and is
not an all-way stop. The second of the two committed unsafe maneuvers.
_Avoid_: unprotected crossing (reserve "unprotected" for the pair collectively).

**Busy road**:
A street that is dangerous to turn onto or cross unprotected. Determined by a
**hybrid** rule: a static floor from road character (class, lanes, tags) plus a
time-dynamic component from simulated volume. A busy road never becomes safe at
midnight — the floor holds.
_Avoid_: major road, arterial (those are OSM classes, not the safety concept).

**Unsafe action**:
An instance of one of the committed maneuvers that clears the counting threshold on a
route. Reported per route as an **unsafe-action count**, split by type. Distinct from a
caution — a caution is visible on the map but not counted.
_Avoid_: violation, hazard.

### Routing outputs — two "tiers" that must not blur

**Safety level**:
The label of a whole route alternative: `fast`, `balanced`, or `safe`. Set by which
λ produced it (see `preference`). This is the *route-level* choice a user makes.
In code: `RouteAlternative.kind`.
_Avoid_: "tier" (that word is reserved for segments — see below), route type.

**Safety tier**:
The per-**segment** safety coloring shown on the map: `safe`, `caution`, or `unsafe`.
A property of one piece of one route, not of the route as a whole.
In code: `Segment.tier`.
_Avoid_: "level" (reserved for routes), segment class.

> The `safe` overlap is deliberate and dangerous: a route's **safety level** can be
> `safe` while still containing `caution` or even `unsafe` **safety tiers** on
> individual segments. Never use "tier" and "level" interchangeably.

**Route artifact**:
The self-contained output of a routing query for one alternative: geometry, per-segment
safety tiers, distance/ETA, unsafe-action counts, per-turn maneuvers, and its
`preference`. It is the versioned contract boundary — every consumer reads this and
nothing deeper.
_Avoid_: route response, route object (those name the transport, not the concept).

**Preference**:
The reproducible description of *what a route was optimized for*, carried inside the
route artifact so any consumer can reproduce or reroute it: the safety-level **label**
plus the resolved **reproducer params** (λ, detour budget, departure-time basis).
_Avoid_: settings, options.

**λ (lambda)**:
The safety weight in the generalized cost `g = time + λ·penalty`. λ=0 is pure time;
higher λ buys safety at a fixed exchange rate against time. An internal knob — users
see the safety level, not λ.
_Avoid_: safety factor, weight (too generic).

**Detour budget**:
How far out of the way the safe route may go to avoid a counted unsafe maneuver,
expressed as a fraction of the fastest route's time. Expresses "go two blocks to the
light" — something λ alone cannot, since λ has no notion of how far the user will go.
In code: `detour_budget_pct` / reported as `detour_pct`.
_Avoid_: detour limit, slack.

### System boundary

**Route service**:
The deliverable: the routing engine plus the API in front of it. It emits route
artifacts and knows nothing about GPS, screens, or clients.
_Avoid_: backend, server (too generic).

**Reference client**:
The `web/` planner UI — the canonical, first-party consumer of route artifacts. A
demonstration of the contract, not part of the core.
_Avoid_: frontend, the app.

**Nav consumer** (a.k.a. **routing handler**):
Any consumer that takes a chosen route artifact and drives live turn-by-turn
navigation (GPS, HUD, voice, rerouting). A downstream consumer of the artifact, never
part of the engine. Currently parked.
_Avoid_: navigator, GPS module.

**Reroute**:
A nav consumer re-invoking the route service mid-trip with the artifact's carried
`preference`, so the replacement stays at the same safety level. Never a fallback to a
plain time-only route.
_Avoid_: recalculate, refresh.

**Parity core**:
The pairing of the pure-Python reference engine (`pyref/`) and the C++ engine
(`sr_core`, built from `core/`). They are held bitwise-identical by the parity test
suite; `sr_core` is an optional speed twin, not a divergent implementation.
_Avoid_: the C++ engine (names only half the pair).

**Pack**:
A compact binary graph artifact built by the ingestion pipeline from an OSM region.
Packs are build outputs, not source; the route service cannot start without one.
_Avoid_: graph file, dataset.
