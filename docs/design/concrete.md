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
  public demo server is for checks only.

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
