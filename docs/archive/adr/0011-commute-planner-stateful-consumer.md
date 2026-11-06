---
Status: accepted (amended 2026-09-24)
Date: 2026-09-17
---

# The commute planner is a stateful consumer; its first deliverable is a departure-time sweep

A **commute planner** — persistently knowing a user's home/work pair and telling them how
to leave before they leave — is the next feature line. It is built as a **separate,
stateful service that consumes route artifacts**, never as an engine feature.

## Why a separate service

The route service is stateless by design (ADR-0002) and the route artifact is the contract
boundary (ADR-0001). A commute planner needs the three things the route service
deliberately does not have: user identity, persistent storage, and a scheduler. Adding
them to the route service would collapse exactly the boundary ADR-0001 exists to protect,
and would make the stateless routing core hostage to a session store — the same coupling
that the Nominatim rate limiter created between geocoding and the process model.

It lives in **this repo**, as a new top-level directory. The `schema-sync` mechanism
(`web/scripts/check-schema-sync.mjs`) is repo-local and diffs by path; splitting repos
would mean duplicating it or publishing the contract as a versioned package, which is real
ops overhead for a one-person project. A second in-repo consumer instead *strengthens* the
contract test, because the artifact gains a second independent reader. The ADR-0001
boundary is enforced by a test asserting the commute service imports nothing from
`pyref/`, not by a repo wall.

## The deliverable is a departure-time sweep, not a traffic watcher

The feature was originally conceived as watching for traffic slowdowns. ADR-0010 defers
real traffic, and under a deterministic model **replan-and-diff can never fire**: two
replans for the same departure time are byte-identical. So the headline feature is the
thing the existing time-dependent model *can* answer honestly:

> Leaving at 07:45 costs +1 min over the fastest option; leaving at 08:05 costs +4 min.
> Your safe route at 08:05 arrives 08:29.

Because synthetic traffic is deterministic, this sweep is **precomputable and cacheable**
per origin/destination pair, so the planner costs almost nothing per user per day.

**Disruption detection is built as an interface with a stub.** When a real feed arrives
(ADR-0010's triggers), the detector is scheduled replanning plus a diff against the
baseline artifact — deliberately *not* a separate traffic-monitoring system, because a
disruption that does not change your route or arrival time is not worth an interruption.

## Decisions

- **Departure times are user-set**, not learned. Inference needs weeks of data and fails
  on the irregular day, which is the day it would matter. Calendar-derived departure
  (reading the first event's time and location) is a natural follow-up, but it expands the
  Google OAuth scope from identity to calendar reading — a materially larger consent ask
  that should be earned after people use the explicit version.
- **One notification per morning.** Today it is a commute brief. When a feed exists, it
  becomes a disruption alert firing on an **ETA delta past an absolute-minutes threshold,
  or a material route-geometry change**. Absolute minutes rather than a percentage,
  because 20% of a 9-minute commute is not worth a push and 20% of a 45-minute one
  obviously is. Starting value ~8 minutes, as a named parameter.
- A change in **unsafe-action count alone does not notify**. It is not actionable before
  departure — the router already handled it — and firing on it trains people to ignore the
  channel.
- **ETA accuracy is claimed for the commute corridor only.** ADR-0006's "plausible, not
  competitive" stands generally: blanket ETA benchmarking against vendors with a decade of
  probe data is unwinnable and unwanted. But a planner that tells someone when to leave
  and is routinely wrong is worse than no feature, and the corridor is the one scope where
  accuracy is *measurable*, because the same user runs the same route daily.

## Consequences

- **Predicted-versus-actual arrival is logged from day one**, even though ETAs come from
  synthetic profiles and will be mediocre. This is what converts "should we buy traffic
  data?" from a guess into a number, and it is the evidence ADR-0010's triggers are
  written against. Instrumenting first is cheap; deciding without it is not.
- The service needs accounts, a datastore, a scheduler, and notification delivery — a
  genuine second ops surface, and the largest single cost in this line.
- Route artifacts consumed here are half-perishable: `eta_s` and segment timings go stale
  while `unsafe` counts and tiers stay reproducible. `traffic_basis` (ADR-0004, schema v2)
  is what makes that legible.

## Amendment, 2026-09-24: the service starts early, as a trip-trace ingest (ADR-0017)

Before Phase 5, the commute planner service begins as one append-only endpoint that
receives **trip traces** from beta testers, identified by a per-device tester token rather
than an account. The predicted-versus-actual log therefore starts with the beta, not with
the first saved commute. It covers every tested trip, not just commute corridors, which
widens the evidence ADR-0010's first trigger is measured against. Accounts, saved
commutes, the scheduler and the sweep are unchanged and still Phase 5. See ADR-0017 for
what is recorded, the on-device privacy trim, and retention.
