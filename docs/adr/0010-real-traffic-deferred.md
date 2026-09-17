---
Status: accepted
Date: 2026-09-17
---

# Real traffic is deferred; the commute planner ships on deterministic synthetic traffic

We evaluated integrating a live traffic feed so the commute planner (ADR-0011) could
detect disruptions, and **decided to defer it**. The planner ships on the existing
deterministic `[sim]` model, and a **disruption-source interface** marks the seam where a
real feed will eventually plug in.

This ADR exists mostly to record the *research*, because the findings are expensive to
rediscover and two of them are counter-intuitive enough to be re-proposed by a future
reader who has not seen them.

## What the market actually sells (surveyed 2026-09-17)

**Live, network-wide traffic *volume* does not exist as a commercial product.** TomTom
Flow Segment Data, HERE Traffic API v7 `/flow` (including lane-level `advancedFeatures`),
Mapbox Traffic Data, and every real-time INRIX API return **speed, travel time, and jam
factor only**. This was verified field-by-field against official documentation.

Volume exists in exactly two forms, neither of them live:

- **Typical profiles** derived from probe data — INRIX Volume Profiles (directional,
  15-minute bins, ~2.65M US road miles) and TomTom Traffic Volume (batch, minimum
  3-day-old data, undocumented directionality).
- **Real loop-detector counts** from public agencies — Caltrans PeMS (free, true per-lane
  30-second flow, California freeways only), MnDOT (Twin Cities), FHWA TMAS (national but
  point-based and ~3 months lagged).

## Two substitutions that do not work

**Jam factor is not a volume proxy; it inverts the signal.** Flow is density times speed,
so a jammed link has *low* flow and *high* jam factor. Substituting congestion for volume
would make gridlocked arterials read as safe to turn across — an error at exactly the
conditions the safety model exists to catch.

**Live speed cannot substitute for volume in the busy rule either.** Real speed-flow
curves are flat through the entire uncongested regime: speed holds at free-flow until
volume reaches roughly 70-80% of capacity, then falls off sharply. So:

```
3am arterial:   20 vph/lane, cars at 45 mph  ->  free flow
2pm arterial:  400 vph/lane, cars at 40 mph  ->  free flow
```

Twenty times the exposure, near-identical speed reading. That is precisely the
discrimination ADR-0005 requires, and speed data cannot make it. Speed measures
*severity* (how bad a misjudged gap is); volume measures *exposure* (how often there is a
car to conflict with). They are different halves of the risk and neither substitutes for
the other.

## Why defer rather than buy

**No free tier can feed a router.** TomTom's free tier is 20,000 requests/month against a
point-query API; this project's `berkeley_oakland` pack has **20,678 directed edges**, so
one full network refresh exceeds the entire monthly quota. TomTom's 200K/month flow
*tiles* are rendered for display, not parseable edge speeds. Free tiers are sized for
display and spot checks, not for a routing cost model.

The paid options all carry a procurement rather than a signup, and the project is at a
stage where that investment is not yet justified. Meanwhile the **departure-time sweep**
(ADR-0011) delivers real value with no feed at all.

**Terms vary sharply and should be re-checked before any adoption.** Mapbox's Product
Terms (Feb 2026) explicitly permit feeding a non-Mapbox routing engine and storing the
data server-side. HERE caps caching at 30 days and prohibits "scaling one Request to serve
multiple End Users", which is what a server-side routing cache does. TomTom's API terms
could not be retrieved at all — the documented URL serves a JS shell with no terms text.

## Consequences

- **ADR-0006 stands intact, both halves.** An earlier draft of this decision would have
  superseded its "no live traffic" half; deferring supersedes nothing.
- The synthetic `[sim]` profiles remain the volume input to the busy rule. They are a
  hand-authored version of exactly what INRIX sells — same units (vph/lane), same shape
  (class base rate times a 24-hour multiplier) — so the eventual upgrade is a **data
  swap, not an architecture change**.
- The route artifact carries `traffic_basis` in its `preference` (ADR-0004, schema v2) so
  the basis of any artifact is legible and a future swap is visible at the contract.
- **Replan-and-diff is inert under a deterministic model.** Two replans for the same
  departure time are byte-identical, so the diff can never fire. The departure-time sweep
  is therefore the commute planner's real deliverable, and the diff scheduler waits for
  the first real adapter (ADR-0011).
- Determinism makes the departure sweep **precomputable and cacheable** per
  origin/destination pair, so the planner has near-zero marginal cost per user per day.
  That property disappears when a live feed arrives.
- **Live speed is explicitly barred from the safety severity term** for now, even when a
  feed exists. That would make unsafe counts non-reproducible and break the scenario suite
  that ADR-0006 names as the validation contract. Revisit only once the suite can be
  pinned to a recorded speed fixture.

## Revisit triggers

Any one of these reopens this decision:

1. **Measured corridor ETA error** (logged per ADR-0011) is large enough to matter to a
   user deciding when to leave.
2. Users report missed disruptions that were **not** incidents — i.e. congestion-only
   events an incident feed would never have caught.
3. **Coverage expands** to metros with no free event feed.

When triggered, the order is **free event feeds first**: 511.org (Bay Area incidents,
free, token), WZDx (national work zones, free, public domain), and PeMS (California
freeway flow, free, but its automated-access terms could not be confirmed and must be
checked first). A paid speed layer only after those prove insufficient, and only with the
measured error to justify it.

Note that free event feeds give **events, not a speed layer** — they populate the
disruption source, they do not improve ETAs.
