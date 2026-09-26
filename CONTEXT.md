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

**Busy floor**:
The per-road-class **minimum** busy contribution that the time-dynamic volume term can
raise but never lower. It is what stops a four-lane arterial from reading as benign at
3am. Set low enough that a genuinely quiet street does de-rate overnight. A named config
parameter on purpose (`busy_floor_by_class`), never an emergent property of weight tuning.
_Avoid_: busy minimum, static threshold.

**Control delay**:
The expected time spent waiting at an intersection because of how it is controlled: for
a signal, an all-way stop, or a usable gap in conflicting traffic. It is **time**, not
safety: it belongs in a route's ETA and in the fast route's choice, independent of λ. An
unprotected maneuver usually carries both a control delay and a safety penalty, and the
two must not be conflated.
_Avoid_: intersection penalty (that is the safety term), turn cost, wait time (too loose).

**Control confidence**:
How the engine knows an intersection approach's control. OBSERVED means an OSM tag said
so. INFERRED means it was guessed from road class, which is how every "no control" case
arises, because OSM never tags an absence. VERIFIED means an imagery-derived label
confirmed it, and it counts as OBSERVED only for label classes that cleared the precision
bar.
_Avoid_: certainty, accuracy (those name measurements, not this label).

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

**Traffic basis**:
The recorded provenance of the traffic inputs a route was computed against: the source
identifier plus a snapshot timestamp. Carried inside the `preference` alongside the other
resolved reproducer params. It exists because an artifact is **half perishable** -- its
`eta_s` and segment timings go stale while its unsafe counts and tiers stay reproducible
-- and a consumer diffing two artifacts must be able to tell "traffic changed" from "these
were computed against different data".
Shipped 2026-09-19 as `preference.traffic_basis` = `{source, as_of, profile_version}`
(ADR-0004 schema v2). `profile_version` is the third part the two-part definition above
did not name: under the deterministic `[sim]` model `as_of` is just the departure clock,
so a hash of the generating profiles is the only part of the basis that can actually
differ between two artifacts today.
_Avoid_: traffic source, snapshot (each names only half of it).

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

**Commute planner**:
The stateful consumer that persistently knows a user's saved **commutes** and tells them
how to leave before they leave. It owns identity, storage and scheduling -- none of which
the route service has -- and reaches the engine only through route artifacts.
_Avoid_: commute service (that names the deployment, not the concept), traffic watcher.

**Commute**:
A saved, recurring Trip plus a habitual departure time, owned by the commute planner.
The unit its scheduling and measurement are keyed on.
_Avoid_: route (a commute is the standing intent; a route is one answer to it).

**Departure-time sweep**:
The commute planner's headline output: the same origin/destination planned across a range
of departure times, so a user can see what leaving earlier or later actually costs.
Answers "when should I leave", which the deterministic traffic model can answer honestly.
Distinct from a **disruption**, which answers "what just went wrong".
_Avoid_: schedule, time sweep.

**Disruption**:
An event that materially changes a commute's route or arrival time -- the thing worth
interrupting someone over. Detected by replanning and diffing against the baseline
artifact, never by a separate traffic-monitoring system. Currently a **stub**: under
deterministic traffic two replans of the same commute are byte-identical, so nothing can
fire (ADR-0010).
_Avoid_: incident (that is one possible source of a disruption, not the concept), delay.

**Trip trace**:
The recorded GPS track of one driven Trip, together with the route artifact being
followed, collected from consenting testers. It is evidence for calibrating the model
and measuring ETA error, and never a direct input to a live route.
_Avoid_: probe data (that names a commercial product), GPS log, telemetry.

**Tester token**:
The credential one beta device presents to the commute planner in place of an account,
issued by the owner and revocable. It identifies a device, not a person, and a trip trace
belongs to the token that uploaded it. A stand-in until the commute planner has accounts.
_Avoid_: API key, user id, account.

**Trip planner**:
The consumer that turns a Request into route-service inputs, then selects and explains
one of the route alternatives the service returns. It never creates or edits a route.
_Avoid_: assistant, agent, chatbot.

### Asking and answering (Trip planner)

**Trip**:
An ordered journey from an origin to a destination. A Trip is what the user wants; a
route is how they get there.
_Avoid_: journey, ride, drive, route.

**Request**:
What the user says they want for a Trip, in natural language.
_Avoid_: query, prompt, intent.

**Constraint**:
A hard rule derived from a Request. A route alternative that violates it is discarded.
_Avoid_: rule, filter, avoid (as a noun).

**Tradeoff**:
A weighted cost derived from a Request, traded against the others. Ambiguous clauses
default to a Tradeoff. Tradeoffs resolve into the reproducer params recorded in a
route's **Preference**.
_Avoid_: preference (reserved for the artifact's reproducer record), weight, soft
constraint, priority.

**Unsupported clause**:
A part of a Request that the Trip planner recognizes but cannot express as a Constraint
or Tradeoff. Always named in the Explanation; never silently dropped.
_Avoid_: ignored clause, unknown intent.

**Relaxation**:
Downgrading a Constraint to a Tradeoff so an infeasible Trip gets Candidate Routes.
Only with the user's confirmation; never automatic.
_Avoid_: fallback, loosening, override.

**Candidate Route**:
A route alternative that satisfies every Constraint of its Trip. Only the route service
creates routes, so every Candidate Route is one of its alternatives.
_Avoid_: option, suggestion.

**Choice**:
The single Candidate Route selected for the user.
_Avoid_: recommendation, pick, best route.

**Explanation**:
A short statement of why the Choice was made, naming the tradeoff, any clause treated
as a Tradeoff rather than a Constraint, and any Unsupported clause.
_Avoid_: rationale, reasoning, summary.

### Engine internals

**Parity core**:
The pairing of the pure-Python reference engine (`pyref/`) and the C++ engine
(`sr_core`, built from `core/`). They are held bitwise-identical by the parity test
suite; `sr_core` is an optional speed twin, not a divergent implementation.
_Avoid_: the C++ engine (names only half the pair).

**Pack**:
A compact binary graph artifact built by the ingestion pipeline from an OSM region.
Packs are build outputs, not source; the route service cannot start without one.
_Avoid_: graph file, dataset.
