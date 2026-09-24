---
Status: proposed
Date: 2026-09-24
---

# Google Routes as the base router, our model as the safety scorer: gated on terms, proven by benchmark

The owner wants this project to take Google's traffic-aware routes as the baseline.
Instead of producing its own fast, balanced and safe routes, the service would **score
Google's routes** for the two unprotected maneuvers of ADR-0003. The target is dense
urban areas, where those maneuvers are most frequent. The reasoning: Google's ETAs and
traffic are far better than the synthetic model of ADR-0010, while this project's
maneuver-level safety judgement is something Google does not offer. The owner accepts
the licensing consequence already documented in `docs/map-provider-tradeoffs.md`:
Routes content must go on a Google basemap, so the basemap moves to Google.

This ADR records that direction and the owner's four choices, which came from a
grilling session on 2026-09-24. It also records one finding that changes the
sequencing: **the "no non-Google map" clause is not the binding constraint. The
"no creating content" clause might be.** So this ADR orders a benchmark behind a
terms-clearance gate. It does not order a product migration.

## Owner decisions (2026-09-24)

1. **Architecture: score Google's alternatives.** Ask the Routes API for its
   traffic-aware routes (`computeAlternativeRoutes`, which returns up to three). Place
   each one on our OSM graph, count unsafe maneuvers with the existing model, and rank
   them. **Only if every alternative is unsafe** do we add intermediate waypoints to
   force Google onto a safer path. Google owns ETA and traffic; this project owns the
   safety judgement.
   Rejected: *take Google's default route, then iteratively add vias.* Each pass can
   route into a new unsafe maneuver, every pass is a billed call, and choosing a via
   that works needs our own safe route anyway.
   Rejected: *our router, with Google only for the basemap.* It leaves the ETA and
   traffic weakness exactly where it is.
2. **Control data: benchmark before trusting a source.** The owner proposed sourcing
   signals and stop signs from Google. Checked 2026-09-24: **the Routes API v2 exposes
   no intersection-control data.** Its `Maneuver` enum is purely geometric
   (`TURN_LEFT`, `STRAIGHT`, `UTURN_LEFT`, ...). Steps carry distance, duration,
   polyline, locations and a navigation instruction. Nothing describes signals, stop
   signs, yields or turn protection. The traffic-light and stop-sign display is a
   **Google Maps app** feature (extended to Android Auto on 2026-09-23), not a
   Platform API. Control therefore stays OSM-derived, including the
   OBSERVED/INFERRED confidence gate that the safety counts depend on. That makes
   **map-matching Google geometry onto the OSM graph a required component**, and its
   accuracy at intersections has to be measured.
3. **First deliverable: an offline benchmark, not a product switch.** See "Plan".
4. **Commute planner (ADR-0011): re-query, do not store routes.** A saved commute
   stores origin, destination and departure time. The route is fetched fresh on every
   run. Only our own derived figures are kept, and only if gate G0 allows even that.

## The finding: which clause actually binds

From the Google Maps Platform terms, read on 2026-09-24:

- **Service Specific Terms §19.1:** Routes API content *may* be used **without** any
  Google Map. **§19.2:** it must not be used **with a non-Google map**. **§19.3:**
  lat/lng values from the Routes API may be cached for **at most 30 consecutive
  days**, then deleted.
- **Terms of Service §3.2.3(a), No Scraping:** do not "pre-fetch, index, store,
  reshare, or rehost Google Maps Content outside the services", beyond the caching
  that §19.3 allows.
- **Terms of Service §3.2.3(c), No Creating Content From Google Maps Content:** its
  examples include using Places lat/lng values **"as an input for point-in-polygon
  analysis"**.

The map clause is answered by moving the basemap. The open question is §3.2.3(c).
Map-matching Google's route geometry onto an OSM graph and deriving maneuver counts
from it is structurally close to the spatial-analysis example the clause names. It
is not the same thing: that example concerns Places data, and our output is shown to
the same user alongside Google's own route, on Google's own map. Whether that counts
as "creating content" is not something this repo can settle by reading. Until it is
settled, a stored benchmark dataset of scored Google routes carries the same
question, plus the §19.3 30-day limit on what may be kept.

`docs/map-provider-tradeoffs.md` also warns about the ODbL side: Google content must
not flow *into* the OSM-derived dataset. Scoring does not write Google data into the
pack. The pack stays pure OSM, and the Google geometry is a transient per-request
input. This is a smaller risk than §3.2.3(c), but the pack builder should keep it
structurally impossible: ingestion never sees Routes content.

## Plan

**G0: terms clearance (blocking; owner action).** Get a written answer, from Google
Maps Platform sales/support or from counsel, to one question: *may an application
display Routes API alternatives on a Google map, annotated with safety flags computed
by matching those routes against the application's own OpenStreetMap-derived
intersection data; and may it retain the derived per-route counts (no Google
geometry) beyond 30 days?* No code that calls the Routes API is merged before this
answer. If the answer is no, go to "Fallback".

**B1: benchmark harness (after G0).** Build 200 urban origin/destination pairs,
weighted toward dense grids and arterial crossings, inside the served packs. For each
pair, record:
- Google's up to three alternatives: ETA, distance, and polyline, with the geometry
  deleted within 30 days per §19.3;
- our `fast`/`balanced`/`safe` routes, with their existing `UnsafeCounts`;
- the matched Google routes, scored by our model.

Metrics:
- **Unsafe maneuvers per km** for each Google alternative, and for the best-scoring
  one, against our three routes.
- **ETA cost:** Google's ETA for its safest alternative against its fastest.
- **Coverage:** how often *some* Google alternative is already at least as safe as
  our `safe` route. That is the case where no vias are needed.

**B2: map-matching accuracy (runs with B1).** Our own routes have known OSM edge
sequences, so render them to polylines, degrade them to Google-like density, and
re-match. Score the match *at turns*, not per edge: every intersection maneuver must
come back with the same turn classification. The acceptance bar is set before
running, e.g. at least 99% of gated turns recovered. Accuracy at intersections is
where ADR-0010 deferred OpenLR conflation, and it is where this design either works
or fails.

**B3: control-data benchmark (owner's request, re-aimed).** Google exposes no control
data, so this cannot be "Google versus OSM". It becomes: **OSM control coverage in
the target urban areas**, i.e. the OBSERVED fraction at arterial junctions, sampled
against a ground truth such as municipal signal inventories where they are published.
If OBSERVED coverage is poor, scoring anyone's route is unreliable, Google's included.

**Decision point, after B1-B3:**
- If Google's alternatives already contain a route that is safe enough most of the
  time, and matching clears its bar, then build the product: a Google basemap, the
  Routes API for alternatives, our scorer, and a via step for the rest.
- If they rarely do, our own router's `safe` route remains the product. Google then
  only offers an ETA sanity check, with no geometry retained.

**Via step (only if built).** When no alternative is acceptable, compute our `safe`
route, find where it departs from the best Google alternative, and add waypoints
**just past each departure**. Re-request once. The via count stays within the
Routes API limit, and the call budget is capped at 2 per user request. If the second
response is still unsafe, show our `safe` route with Google's ETA for comparison.

## Fallback if G0 says no

We route; the user's navigation app drives. Compute the `safe` route with our own
router, as today. Hand it to Google Maps, Apple Maps or Waze via a **deep link** with
waypoints pinned at each departure from our `fast` route. A deep link is not a Maps
Platform API call, so §§19 and 3.2.3 do not reach it. Its waypoint limits and its
own terms still need confirming. This also offers an alternative to un-parking our
own live navigation (ADR-0008). It is worth its own ADR if it is chosen.

## Consequences

- The three-route product stays untouched until the decision point. Nothing in this
  ADR changes the engine, the cost model, parity or the golden digests. The scorer
  reuses the existing turn classification.
- Map-matching is a new core component with its own accuracy test. It is the first
  place Google geometry and OSM topology meet, and it needs its own module boundary.
  Nothing downstream of it may persist Google content beyond §19.3.
- Adopting the Google basemap (at the decision point) drags geocoding along with it.
  Places Autocomplete replaces Nominatim, which retires ADR-0013's Nominatim bucket
  and the Photon question. It also brings a billing account and a restricted browser
  key, per `docs/map-provider-tradeoffs.md`.
- The safety thesis, "a safety layer on top of time-optimal routing" (ADR-0003), is
  arguably served *better*: Google provides the time-optimal layer, and this project
  is only the safety layer.
- **Quadtrees / spatial indexing** (raised in the same session): not a lever. Snapping
  already uses a k-d tree and takes microseconds. Request time is graph search plus
  cost precompute (ADR-0009). Under this ADR, most of the search would move to Google
  anyway.
