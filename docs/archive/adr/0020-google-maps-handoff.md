---
Status: accepted
Date: 2026-10-27
---

# Hand the chosen route to Google Maps for navigation, held on course by pinned waypoints

The owner set a two-day MVP: people actually drive with it, with voice turn-by-turn and
rerouting, starting with the owner and then a few testers in Berkeley, Oakland and SF.
Our own live navigation is still behind `NEXT_PUBLIC_ENABLE_LIVE_NAV` (ADR-0008), and a
native app or an OSRM rebuild would not fit in two days. The owner chose the
**handoff**: our route service plans and compares as today, and Google Maps drives.

This is ADR-0015's "Fallback" (a deep link with pinned waypoints), which that ADR's
2026-09-24 amendment left undecided and said needed its own ADR.

## Decisions

1. **Google Maps is the nav consumer for the MVP.** The reference client links the
   selected route into the Google Maps app with a Maps URLs Directions link,
   `dir_action=navigate`, and the route's waypoints. Google supplies the voice, the
   lane guidance and the rerouting. Our live nav stays parked (ADR-0008), and is not
   deleted.
2. **The route service pins the waypoints**, as an additive field on every `/route`
   alternative: `handoff: {waypoints: [LatLon], predicted_unsafe: int}`. It is `null` on
   `/reroute`, which serves our own nav. `schema_version` stays 2. The pins need the
   graph and the per-query costs, so a client cannot compute them.
3. **Our lambda-0 search stands in for Google.** Google's routing is unobservable here: the
   Routes API is excluded until ADR-0015's G0 clears. Time-optimal routing on the same
   per-query costs is the closest model we have of a time-optimal navigator.
4. **Pins protect safety, not geometry.** From each anchor (the origin, then each pin),
   the next pin goes on the **farthest** target edge that the stand-in reaches with no
   more flagged maneuvers than the target takes over the same stretch. That edge is
   found by binary search along the route, and the process repeats until the stand-in
   reaches the destination no less safely, or the cap is hit. A stand-in path that
   leaves our route but is no less safe gets no pin. Each pin is the midpoint of the
   part of its edge the route drives.
5. **The cap is 9 pins** (`[handoff] max_waypoints`), the Maps URLs limit outside
   mobile browsers. The Android and iOS apps take 9; mobile browsers take only 3, and
   the link is meant to open the app.
6. **What the pins cannot promise is reported, not hidden.** `predicted_unsafe` is the
   number of flagged maneuvers the stand-in takes through the final pins. When it is
   above the route's own `unsafe.total`, the client says so beside the button.

## Measured (2026-10-27, `berkeley_oakland`, pure-Python engine)

- **Sample:** 30 random origin/destination pairs, with departures alternating weekday
  08:00 and 03:00. 24 of them produced a `safe` alternative.
- **The first design placed fewer pins badly.** It put each pin where the stand-in first
  left our route. It needed a mean of 3.9 pins and a max of 9 per `safe` route. **5 of
  24** hit the cap with flagged maneuvers still predicted.
- **Farthest-reach placement (decision 4)** needed a **mean of 1.0 pins and a max of 4**.
  **0 of 24** were left with a predicted shortfall.
- **Latency:** pinning added roughly 0.1 to 0.7 s per `/route` call on the pure-Python
  engine. That engine is what `pytest` runs locally; the deployed service runs
  `sr_core`. Re-measure on the deployed service before calling it fine.

## Not verified until a real drive

- **Waypoints may be treated as stops.** The Maps URLs docs call them places "to route
  directions through", but the app may announce each one as a stop.
- **Waypoint support is not guaranteed.** The docs say waypoints "are not supported on
  all Google Maps products"; where they are not, they are ignored.
- **Turn-by-turn starts only near the origin.** `dir_action=navigate` starts navigation
  only when the origin is at or near the device's location. Otherwise Google shows a
  route preview.
- **Off-route rerouting is Google's.** After a wrong turn, Google reroutes to the next
  pin by its own logic, which may take a flagged maneuver.
- **Direction of approach is not modeled.** Google snaps a waypoint to a road without
  regard to direction, while our pins are directed edges. On a two-way street, Google
  could approach a pin from the far side.
- **The stand-in is not Google.** It uses synthetic traffic (ADR-0010); Google uses live
  traffic. `predicted_unsafe` is an estimate.

The first drives should record, per trip, whether Google followed the pins and what it
announced at each one.

## Consequences

- **ADR-0008:** live nav stays parked and is not the MVP path. Its un-park bar is
  unchanged.
- **ADR-0015:** its Fallback is now decided as above. G0 still reopens the
  Google-as-base-router direction.
- **ADR-0017 loses its data source.** Trip traces are recorded only while our own live
  nav runs (`web/lib/tripRecorder.ts`). Drives taken through Google Maps produce no trace,
  so ADR-0017's calibration needs another source, or a tester who drives with our nav.
  This is an open question for the owner.
- **The `/route` contract gains one additive field.** It is mirrored in
  `web/lib/types.ts` (`Handoff`) and mapped in `check-schema-sync.mjs`.
- **More searches per request.** Each alternative costs at least one extra lambda-0
  search, plus about log2(route edges) searches per pin. The rate limiter's
  one-token-per-call charge (`api/routes.py`) is unchanged; revisit it if pinning
  dominates request time once deployed.
- **Apple Maps and Waze are not done.** Apple Maps URLs take waypoints too, and Waze
  links take none. Either is a later client change; the server's pins are the same.
