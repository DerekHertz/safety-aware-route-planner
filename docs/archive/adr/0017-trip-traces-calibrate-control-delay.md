---
Status: accepted
Date: 2026-09-24
---

# Beta trip traces are collected by the commute planner and calibrate the control-delay model

ADR-0016's control delay ships with textbook constants. The owner and friends testing
the beta drive real routes with the app open. Their GPS traces are the one source of
*measured* intersection waits this project can get for free. They also carry the
predicted-versus-actual arrival log that ADR-0011 requires from day one, and that
ADR-0010's first revisit trigger is written against. One pipeline serves both.

## Where: a first slice of the commute planner, not the route service

Trip traces go to the **commute planner service** (ADR-0011), started early as one
append-only ingest endpoint and a small datastore. The route service stays stateless
(ADR-0002); ADR-0011 already places identity and storage outside it.

- **No accounts yet.** Each beta device gets a **tester token**. Google Sign-In arrives
  with Phase 5 as planned.
- Rejected: an ingest endpoint on the route service. The owner's reason for wanting it
  was "don't depend on a tester to upload". That is met by automatic upload from the
  client (below), not by which service receives it.

## What: the raw trace, trimmed on the device

- **Raw trace, not on-device extraction.** About 1 Hz fixes (position, timestamp, speed,
  accuracy), plus the route artifact being followed and any reroute artifacts. Waits are
  extracted **server-side**, so the extractor can be fixed and re-run over old traces.
  Extracting on the phone would throw that evidence away.
- **Automatic, with a one-time opt-in per device.** Recording runs whenever live
  navigation runs. There is no per-trip upload action.
- **Resilient upload.** Fixes go to local storage (IndexedDB) as they arrive and are
  flushed in chunks about every 2 minutes and at trip end. Anything unsent is retried
  on next launch. A PWA can be killed mid-trip, and a trip must survive that.
- **Privacy trim on the device.** Nothing is uploaded until the vehicle is 300 m from
  the origin. The trailing 300 m is always held back and discarded at trip end. The
  endpoints of a trip, meaning homes and workplaces, never leave the phone.
- **Retention.** Raw traces are kept 90 days. Extracted waits are kept indefinitely.

**Platform limit, accepted:** a PWA gets GPS only in the foreground. iOS gives web apps
no background location, so collection needs the app open with navigation running. Screen
Wake Lock (ADR-0012) already keeps it there. Testing needs `NEXT_PUBLIC_ENABLE_LIVE_NAV`
on in the beta build (ADR-0008).

## How a wait is measured

Traces are matched to **the route the tester was following**, not map-matched from
scratch. `web/lib/routeProgress.ts` already projects fixes onto the route, and every
turn on the route is a known `(approach, movement, control)` in the pack.

- **Wait** = time spent below ~2 m/s within ~60 m upstream of the intersection. It is
  recorded against that turn with time of day and day type.
- **Off-route stretches are discarded.** After a reroute, matching continues against the
  new artifact.
- Rejected: general HMM map matching. It is a research problem at intersections (ADR-0015,
  B2), and the only data this approach loses is data we don't want: driving off the
  route.
- **Tests:** synthetic traces through the dev-only `__srMockGeo` hook in
  `web/lib/useGeolocation.ts`, with a known stop, a known wait and a known intersection.
  The server-side extractor gets the same fixtures.

## How measurements reach routing: calibration, not lookup

A few testers yield a few observations per intersection per month, spread across 96
fifteen-minute bins a day. A per-intersection, per-bin lookup would be empty almost
everywhere.

- **Now:** fit ADR-0016's handful of constants (critical gaps, per-class signal waits,
  the all-way-stop constant, the volume-profile scale) against *all* observations
  pooled. The fit writes **config**, so `traffic_basis.profile_version` changes, and
  routes stay deterministic and reproducible under the scenario suite.
- **Later:** per-intersection overrides where observations are dense enough, such as a
  tester's own daily commute, shrunk toward the model elsewhere.
- **Never:** measured waits read directly into a live request. That would make artifacts
  non-reproducible, the same objection ADR-0010 raises against live speed in the
  severity term.

## Consequences

- **Amends ADR-0011.** The commute planner service starts before Phase 5, as an ingest
  slice with tester tokens instead of accounts. Its predicted-versus-actual log starts
  with the beta rather than with the first saved commute.
- The trace store holds location history of real people. It stays private, is never
  exported, and is covered by the retention rule above. Accounts (Phase 5) inherit it.
- Calibration is only as good as its sample. Report per-constant sample counts with every
  fit, and do not ship a fitted constant with fewer than a stated minimum of
  observations. The number is set before the first fit.
