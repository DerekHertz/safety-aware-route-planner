# Concrete design: types and interfaces

Stage 2 of the method in `AGENTS.md`, building on [abstract.md](abstract.md). Terms are in
[CONTEXT.md](../../CONTEXT.md). Settled 2026-11-05 unless marked open.

## Stack and contract

- **Server:** Python 3.12, FastAPI, Pydantic v2, `uv`, in `server/` ([ADR-0001](../adr/0001-service-and-native-client.md)).
- **Contract:** the Pydantic models generate `api/openapi.json`, which is committed. A test fails
  when the committed file drifts from the models. The iOS app in `ios/` generates its client from
  that file with `swift-openapi-generator` and reads nothing else from `server/`.

## The service module

One deep module with two operations:

- `compare(trip, avoid, deadline) -> Comparison`
- `reroute(position, trip) -> Route`: unassessed, labeled `rerouted`.

Three seams to the outside world, each with a live adapter and a recorded-fixture adapter.
Each raises one `SourceUnavailable` error, which becomes `check_failed` or an incomplete search,
never `unknown`.

```
RouteSource.route(waypoints: [LatLon], depart: datetime) -> RawRoute
    # one merged route even when waypoints split it into legs;
    # steps with intersections (location, bearings, in/out)
RecordSource.snapshot(bbox) -> RecordSnapshot
    # junction nodes with tags and coordinates, ways with tags and node refs,
    # turn restrictions, snapshot timestamp
Jev.ask(state: dict, questions: {name: Question}) -> ({name: Answer}, model_version)
```


| Seam | Live adapter | Fixture adapter |
|---|---|---|
| RouteSource | OSRM ([ADR-0002](../adr/0002-osrm-over-mapbox-directions.md)) | Recorded OSRM responses |
| RecordSource | Overpass | Recorded Overpass responses |
| Jev | TypeSafe API ([ADR-0003](../adr/0003-jev-text-only-cached.md)) | Recorded answers |

Movement interpretation, assessment, local search and ranking are internal modules with internal
tests. They are not part of the interface. Tests cross `compare()` with fixture adapters.

## HTTP endpoints

| Endpoint | Returns |
|---|---|
| `GET /trips` | The preset trips. A route that can't be obtained is HTTP 503 on any endpoint below. |
| `POST /trips/{id}/default` | The assessed default route, so the app can draw it at once. With avoidance off, this is the only call. |
| `POST /trips/{id}/comparison {avoid}` | A `Comparison`, within the 10-second deadline. |
| `POST /trips/{id}/reroute {position}` | An unassessed route labeled `rerouted`. |

## Core types

```
Route       { label: provider|example|rerouted, geometry: [LatLon], distance_m, eta_s,
              steps: [Step], crossings: [Crossing] }
Step        { instruction, maneuver {type, modifier}, street, distance_m, along_m, location }
Crossing    { key, main_street, kind: straight|left_onto, approach_bearing, along_m,
              location, assessment: Assessment }
Assessment  { status: flagged|unflagged|unknown|check_failed, explanation, evidence: [Evidence] }
Evidence    { source: osm|jev|city_record, ref, claim, observed_on?,
              retrieved_at, is_lead }
Comparison  { default: Route, alternative?: Route,
              outcome: improvement|no_improvement|over_budget|incomplete|check_failed,
              search_complete, budget_s, added_s? }
```

- Flag and unknown counts are never sent. The app derives them from `crossings`, so marker
  counts equal assessment counts by construction.
- Lead evidence is shown but never changes a status.
- **Units:** the API carries meters (`_m` fields). The app converts at the display edge: miles,
  and feet under about 0.1 mi for turn prompts, on screen and in voice.

## Crossing key

`{sorted junction OSM node ids}/{kind}/{approach octant}`, for example
`53050628+240469698/straight/E`. Node ids come from matching each route intersection to the
nearest OSM junction node within 3 m, never from OSRM's `annotation.nodes`, which rounds ids
of 10^10 and above.

## Data and hosting

- **Fixtures:** each live adapter has a `record` mode that writes responses to `server/fixtures/`,
  keyed by a hash of the request. Tests use only fixture adapters and never touch the network.
- **Trips:** `server/trips.json`, each with id, name, origin, destination and the `example_via`
  waypoints that force the example route. The search is never told about the alternative; a test
  asserts it finds Dohr and Ward.
- **Drive reports:** the only labels. Written by drivers after a real drive, plus the owner's
  description of the two demo crossings. Used by tests and evaluation, never as evidence.
- **Jev gate:** automatic. Jev must agree with the rules on rule-settled Berkeley crossings, given
  the same records, before its answers on other crossings stop being leads.
- **Jev answer cache:** SQLite at `server/var/jev_cache.sqlite`, gitignored, filled for the corridor
  by a pre-run command.
- **OSRM:** self-hosted in Docker on a Berkeley-only extract, preprocessed in WSL and uploaded. The
  public demo server is for checks only. Built by `server/osrm/prepare.sh` (Geofabrik NorCal
  extract, clipped to W-122.34 S37.80 E-122.20 N37.93, MLD car profile, `osrm-backend` v5.27.1);
  served by the `osrm` service in `compose.yaml`; data lands in gitignored `server/osrm/data/`.
- **Live source URLs:** `live_sources()` reads `GAPCROSS_OSRM_URL` and `GAPCROSS_OVERPASS_URL`
  (argument beats environment beats the public servers); compose sets the first to `http://osrm:5000`.
  Live tests carry the `live` marker and are skipped unless run with `-m live`.

## Drive reports

`POST /drive-reports`, sent once after a real drive, never after a simulated one:

```
{ trip_id, route_label, depart_at,
  crossings: [{ key, answer: as_described | not_as_described | not_sure }] }
```

One tap per marked crossing on the driven route; unanswered crossings are dropped. Stored in a
server-side SQLite file separate from the Jev cache, treated as durable data and backed up.

## Jev questions

Each crossing adds only the questions the rules left open to its route's single request:

```
{key}.approach_control  choice: stops_for_us | does_not_stop | cannot_tell
{key}.road_function     choice: through_street | local | ambiguous
{key}.records_conflict  choice: agree | conflict | insufficient
```

An answer counts only at probability >= 0.9 (a starting value, checked against the gate);
anything lower maps to unknown.

## iOS follower

```swift
protocol PositionFeed { var fixes: AsyncStream<Fix> { get } }  // coordinate, course, speed, time
  SimulatedFeed(route, speedMultiplier)   // simulator; owns start, pause, restart
  CoreLocationFeed()                      // the car
Follower(route, feed) -> AsyncStream<GuidanceState>
GuidanceState = following(next: Step, distanceToIt_m) | offRoute | arrived
```

Off-route: more than 40 m from the route for more than 5 s (starting guesses, tuned on the first
drive). The follower cannot tell the two feeds apart.

## Hosting

One always-on cloud VM runs the service, the OSRM container and both SQLite files. Start on a free
Google Cloud e2-micro (1 GB); if measured memory does not fit, move to a Hetzner 4 GB plan.

## Measured latency and candidate limit (S6, 2026-11-06)

Self-hosted OSRM (v5.27.1, MLD) in Docker on WSL2, Berkeley extract (prepared graph about 40 MB,
container memory about 30 MiB idle). Measured with `uv run python -m gapcross.latency`; calls are
sequential HTTP from the host. Local routes reproduce the recorded public-server fixtures exactly
(provider 708 s / 5874 m, example 754 s / 6005 m).

| Call | n | p50 | p95 |
|---|---|---|---|
| OSRM provider route (origin to destination) | 10 | 17.8 ms | 19.9 ms |
| OSRM example route (via Oregon) | 10 | 18.0 ms | 19.6 ms |
| OSRM candidate-like via route (12 spread points) | 12 | 17.6 ms | 28.4 ms |
| Overpass snapshot of the trip bbox (public overpass-api.de) | 1 | 35 s | - |
| `compare()` end to end: live OSRM, snapshot held, no Jev | 5 | 0.52 s | 0.60 s |

Overpass: the public server took 35 s for the corridor snapshot (three earlier attempts timed out
or answered 502). It is therefore never on the request path: the service fetches each corridor's
snapshot once at startup (`Service.prefetch`) and keeps it for the life of the process. The
adapter's HTTP timeout is 120 s (the query's own limit is 90 s). A request that arrives before a
successful fetch tries once, and reports `check_failed` if that fails.

**S6 check: met.** `compare()` for the demo trip on the self-hosted OSRM, with the corridor
snapshot held, finishes in about 0.5 s (p50; max 0.6 s) and finds the Ward improvement.

**Jev (S7):** model pinned to `jev-1.14.0`. One batch per route: 0.21–0.27 s for 2–15 questions
on a cache miss; a full hit makes no call. The live service reads the cache the corridor pre-run
filled (`uv run python -m gapcross.prerun`), so `compare()` with Jev still takes about 0.5 s.
`approach_control = stops_for_us` means the main street's traffic stops for the driver
(unflagged); `does_not_stop` means a gap crossing (flagged).

**Jev gate (S7, first corridor run 2026-11-06):** 27 rule-settled crossings, Jev confident on 2
(both agree), abstained on 25; 2 open crossings asked. The gate needs at least 10 confident
agreements and no confident disagreement (`MIN_AGREE`), so it fails and Jev's answers stay leads
until better evidence or wording raises its confidence.

**Candidate limit: 8.** The demo search generates 4 candidates; each costs about 20 ms of OSRM and
about 0.1 s of interpretation and assessment. Jev adds about 0.3 s per route batch on a cache miss
(S7), so 8 candidates plus the default stay under 4 s even with no Jev answer cached.
