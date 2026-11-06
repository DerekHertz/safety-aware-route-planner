# Implementation slices

Stage 4 of the method in `AGENTS.md`, built on [concrete.md](concrete.md) and
[properties.md](properties.md). Draft 2026-11-06, awaiting approval. Each slice starts with a
failing test (`tdd`) and ends on its check. At most 5 workers run at once.

## Order

```
S0 scaffold + contract ──┬── S1 fixtures ──┬── S2 interpretation ── S3 assessment ── S4 search + comparison ── S5 HTTP
                         │                 ├── S6 live adapters + OSRM in Docker
                         │                 └── S7 Jev, cache, gate
                         └── S8 iOS planning + simulated drive (Mac, against the contract)
S5 + S6 ── S9 deploy        S8 + S9 ── S10 in-car drive
```

**Tonight's demo** is S0–S5 plus S8: the service on recorded fixtures, labeled as recorded, and the
app in the iOS simulator. S6, S7, S9 and S10 make it live, Jev-backed and drivable.

## Slices

| Slice | Scope | Done when | Needs from the owner |
|---|---|---|---|
| **S0** Scaffold and contract | `server/` with `uv`, FastAPI, pytest. Every Pydantic type from concrete.md. `api/openapi.json` generated and committed, with a drift test. `docker compose` for the service. | `uv run pytest` is green, including the drift test; the OpenAPI file lists every endpoint and type. | Nothing |
| **S1** Fixtures and trips | `server/trips.json` with the Berkeley Bowl West trip and its `example_via`. Record OSRM routes (example, Dohr/Ward candidates, nearby crossings) and the corridor's Overpass snapshot into `server/fixtures/`. Fixture adapters for RouteSource and RecordSource. | Fixture adapters return recorded data offline; the earlier access-check values (754 s, 775 s, the Sacramento node ids) reappear from fixtures. | Nothing |
| **S2** Movement interpretation | Route to ordered movements and crossings; carriageway merge; nearest-junction match within 3 m; crossing keys. | The example route yields exactly one Sacramento crossing, keyed `53050628+240469698/straight/E`; the Dohr/Ward route yields the Ward crossing and no crossing at Dohr. | Nothing |
| **S3** Assessment rules | Rules over records; statuses; `check_failed` on `SourceUnavailable`; leads; Jev seam stubbed with fixture answers. | Oregon is flagged, Ward unflagged, a junction with no control record unknown. Tests for rules 1, 2, 8 and 9, each checking against values computed outside the code under test. | Nothing |
| **S4** Search and comparison | Candidate crossings within 3 blocks; geometry check of each candidate; budget; ranking; outcomes; `search_complete`; injected clock for the deadline; templated explanations. | The search finds Dohr/Ward without being told. Tests for rules 3–7 with an independent oracle; the deadline test uses the fake clock. Rule 14 string test over every template. | Nothing |
| **S5** HTTP service | The four endpoints, 503 on route failure, `reroute`, `drive-reports` (stored in SQLite). | Endpoint tests pass on fixtures; rule 10: the same request twice gives byte-identical responses. | Nothing |
| **S6** Live sources | OSRM in Docker on a Berkeley-only extract; live Overpass adapter; `record` mode; latency measured; candidate limit set from it. | `compare()` on live adapters finishes inside 10 s for the demo trip; measured times and the chosen candidate limit are written to concrete.md. | Nothing (downloads an OSM extract) |
| **S7** Jev | Jev adapter batched per route; SQLite answer cache; Flow B pre-run and gate. | The gate runs on the corridor's rule-settled crossings and logs its pass or fail with counts; cache hits skip Jev; latency per batch measured. | Nothing (uses `TYPESAFE_API_KEY`) |
| **S8** iOS planning and simulated drive | Xcode project in `ios/`; client generated from `api/openapi.json`; MapKit routes and markers; avoidance toggle; `SimulatedFeed`, `Follower`, guidance UI in miles and feet. | In the simulator: the trip loads, both routes and markers show, counts match crossings, the simulated drive reaches the destination with turns in order. Tests for rules 11–13. | A Claude Code session on the Mac with Xcode; the service reachable from the Mac (run it there with `docker compose`) |
| **S9** Deploy | The compose stack on a GCP e2-micro; memory measured; Hetzner if it does not fit. | `GET /trips` answers from the VM over HTTPS; memory use recorded. | A Google Cloud account and project; `gcloud` installed and logged in |
| **S10** In-car drive | `CoreLocationFeed`, voice prompts, off-route banner and recompute, drive-report screen; a build signed onto the iPhone. | One real drive of the demo trip with a passenger operating the phone; the drive report arrives on the server. | The iPhone, the Apple ID for signing, and the drive |

## Parallel plan

After S0: S1 and S8 start together (S8 needs only the contract). After S1: S2, S6 and S7 run in
parallel, then S3, S4, S5 in sequence. That is at most 4 workers at once.
