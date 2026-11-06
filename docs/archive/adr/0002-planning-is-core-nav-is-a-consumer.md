# Stateless planning is the core; live navigation is a downstream, safety-aware consumer

Stateless route **planning** — one request in, up to three route artifacts out — is the
core experience the system is designed around. Live turn-by-turn **navigation** is a
future, separable layer built *on top of* a chosen route artifact, not a co-equal mode
baked into the engine or the planner.

The engine, API contract, and test suites are all shaped around stateless planning; live
nav is a fundamentally different stateful, real-time surface. Treating it as a consumer
(ADR-0001) keeps it from destabilizing the base.

The one hard constraint this places on navigation: a **reroute** must stay at the user's
chosen safety level. A nav consumer that reroutes mid-trip re-invokes the route service
with the artifact's carried `preference` (ADR-0004) — it must never fall back to a plain
time-only route. Without this, a reroute can silently swap the user from a `fast` route
onto a `safe` one (or vice versa), which is the concrete failure ADR-0008 records.
