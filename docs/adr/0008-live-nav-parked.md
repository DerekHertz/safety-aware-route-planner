# Live navigation is parked until the core is robust; rebuild it as a clean consumer

The "Add real-time GPS turn-by-turn navigation" work (commit 4ab2679) is **parked behind
an experimental flag** rather than fixed in place. It is green (all tests/lint/schema
pass), but it was bolted *into* the planner: live nav was layered onto the plan-a-route UI
by overloading `origin`/`selected`/`followMode` state in `web/app/page.tsx`. The
observable consequences:

- **Silent safety-level swap on reroute** — a reroute reselects a route tier from the new
  position and can drop the user from `fast` onto `safe` (or vice versa) with no notice.
  This is the failure ADR-0002's carried-`preference` reroute rule exists to prevent.
- **No arrival lifecycle** — reroute always targets the original destination; there is no
  "you have arrived" state.
- **Fragile reroute dance** — a ~15s post-reroute deadband suppresses off-route detection,
  and the "recalculating…" HUD flickers.

Rather than patch a surface we've already decided should be a downstream *consumer*
(ADR-0002), we keep the sound primitives — the backend per-turn maneuvers and the
unit-tested pure helpers in `web/lib/routeProgress.ts` — and stop investing in the
`page.tsx` nav-state overloading. Nav gets rebuilt as a clean artifact consumer once the
core is robust, at which point the silent-swap bug disappears by construction.

## The "robust core" milestone (the bar to un-park nav)

1. `web/` consumes the route-artifact contract v1, including the `preference` (ADR-0004).
2. The contract is schema-versioned and contract-tested.
3. The `page.tsx` nav-state overloading is removed; live nav is quarantined behind the flag.
4. The safety scenario suite is green.

## Reroute v1 semantics (for the eventual rebuild)

Replan from the current position to the original destination, using the carried
`preference`, recomputing **only that one safety level** — not the full fast/balanced/safe
set. Trying to rejoin the original route is a future nav nicety, deliberately out of v1.
