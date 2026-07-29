# Safety-Aware Route Planner (prototype)

An in-car GPS route-planning prototype over real OpenStreetMap data (Berkeley /
North Oakland) that minimizes travel time while penalizing two unsafe
maneuvers:

1. **Unprotected left turns onto busy streets** (permissive signal or no
   control — not a protected arrow)
2. **Uncontrolled crossings of busy streets** (straight-through where the
   crossing has no signal and is not an all-way stop)

For an origin, destination, and departure time it returns up to three curated
alternatives — **fast / balanced / safe** — each with distance, ETA, and
unsafe-action counts split by type, plus per-segment safety-tier coloring.

## Architecture

```
ingestion/   OSMnx v2 download -> clean -> tag extraction -> compact binary pack
core/        C++17 routing engine (pybind11 module `sr_core`)
pyref/       pure-Python reference engine — the parity twin of core/
sim/         deterministic synthetic time-of-day traffic profiles
api/         FastAPI service (POST /route, GET /geocode) over the engine
web/         Next.js + React + MapLibre GL front-end (OpenFreeMap tiles)
config/      config.toml — every tunable number lives here
tests/       unit, safety-rule, scenario, contract and parity suites
```

Key design points:

- **Edge-based turn graph**: search state = directed edge; turn costs enter
  `g` at transitions. Dijkstra + A* (admissible time-only heuristic).
- **Shared numpy precompute**: all floating-point math (penalties, haversine,
  λ-scaling, congestion) happens once per query in `pyref/costs.py`; both
  engines only ADD prebuilt float64 arrays → Python↔C++ parity is **bitwise**
  (asserted by `tests/test_parity_cpp.py`).
- **Cost model** (all weights in `config/config.toml`):
  `raw = w_man·sev + w_speed·norm(speed) + w_lanes·norm(lanes) + w_vol·norm(vol) − w_med·median`,
  control override (protected/roundabout → 0, all-way stop → ×0.25, give-way
  straight/right → ×0.35 and give-way left → ×0.6, right-of-way
  straights/rights → ×0.2, signalized left → ×0.35, inferred-unprotected →
  ×0.5), `penalty_s = k·max(raw,0)`,
  `g = time + λ·penalty`. The same post-override raw drives the unsafe-action
  counters (`raw ≥ τ` + per-type predicate + OBSERVED control) and the
  safe/caution/unsafe tiers.
- **Alternatives**: λ sweep (0 / 0.5 / 1.5) → Jaccard dedup (0.8) →
  penalty-method rerun for genuine diversity, with a guard that drops a
  diversified route if it is safety-worse than the un-inflated optimum.
  Labels are assigned after dedup: lowest-λ survivor = fast, highest = safe.
- **Detour budget** (`detour_budget_pct`, default 0.25): how far out of the
  way the safe route may go for a safer crossing, as a fraction of the fastest
  route's time. λ alone cannot express this — it trades penalty against time
  at a fixed exchange rate with no notion of how far the user will actually
  go. The budget works both ways: when λ will not buy a detour the budget
  allows, a hard-avoid run forces it; when λ produces a route longer than the
  budget allows, that alternative is dropped. Each route reports `detour_pct`.
- **`safety_enabled=false`**: λ forced to 0, single fast route; unsafe
  counters are still computed and reported.
- **Traffic**: deterministic synthetic hourly profiles by road-class group;
  the departure-time snapshot is frozen per query and feeds both congestion
  (travel time) and the volume term of the safety score.

## Build & run

Prereqs: Python 3.12+, Node 20.9+, MSVC (for the optional C++ core).
The 3.12 floor comes from the pinned numpy and scipy, which both require it.

```powershell
powershell -File scripts\build_all.ps1   # venv, deps, packs, sr_core, tests
```

Then, in two terminals:

```powershell
.venv\Scripts\python.exe -m uvicorn api.main:app --port 8000
```

```powershell
npm --prefix web install
npm --prefix web run dev                  # http://localhost:3000
```

The engine implementation is chosen by `[engine] impl` in
`config/config.toml` (`"cpp"` falls back to pyref with a warning if
`sr_core` isn't built). `scripts/bench.py` compares the two (~20× speedup,
<1 ms per query on the Berkeley/Oakland pack).

Regions are presets in `[region.presets]`; build a pack with
`python -m ingestion.build_pack --region <name>`. Raw OSM downloads are
cached in `data/cache/` so Overpass is hit once per region. Cache filenames
carry `INGEST_SCHEMA_VERSION` (`ingestion/download.py`) — bump it whenever tag
retention or simplification changes, or a stale cache will keep feeding the
old data into new code.

### Pack distribution

Packs are build outputs, not source: `data/` is gitignored, and the API cannot
start without one. `packs.lock` pins which prebuilt artifacts a checkout, a CI
run, and a deployed container should all use.

At startup the API downloads any missing pack (`api/packs_fetch.py`), verifies
it against the digest in `packs.lock`, and unpacks it atomically. This is a
no-op when the pack is already on disk, so **local development never touches
the network** — and neither does the test suite, which pins `SR_PACK_DIR` at a
generated toy pack.

While `base_url` in `packs.lock` is empty, fetching is disabled entirely and
the API reads local disk only. To enable it:

1. Create a **public-read** R2 (or S3) bucket. Packs derive from public
   OpenStreetMap data, so there is nothing secret in them: the runtime fetches
   over plain HTTPS with no credentials, and only CI — which uploads — needs
   keys. Set the repo variable `SR_PACKS_BASE_URL` and the secrets
   `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` plus the
   variable `SR_PACKS_BUCKET`.
2. Run the **build-packs** workflow (manual dispatch — it hits Overpass, so it
   never runs per-push). It builds each region, publishes reproducible
   `<region>.tar.gz` artifacts under an immutable tag, and prints the
   `packs.lock` stanza.
3. Paste that stanza into `packs.lock` and open a PR. Rolling forward is
   merging it; rolling back is reverting it.

Archiving is deterministic — sorted entries, normalized mtime/uid/mode, fixed
gzip header — so packaging the same pack directory twice gives byte-identical
output. The *pipeline* is not reproducible end to end, though:
`ingestion/pack.py` stamps `created_utc` into every manifest, so two builds of
identical OSM data yield different pack bytes and different digests. The
digests in `packs.lock` therefore have to come from the run that published the
artifacts; they cannot be re-derived by rebuilding.
`scripts/package_packs.py` does the packaging and can be run locally.

To pull published packs into a fresh checkout without building from Overpass:

```bash
python -m api.packs_fetch --regions berkeley_small,berkeley_oakland
```

## Running it (including on a phone)

### The container

```bash
docker build -t sr-api .
docker run -p 8000:8080 sr-api
```

`Dockerfile` is multi-stage: the builder installs g++ and produces an `sr_core`
wheel, and the runtime stage carries no compiler, no `ingestion/`, and no osmnx
— that package and its geo stack are build-time only, and excluding them keeps
the image at ~520 MB instead of well over a gigabyte. The build **fails** if
`sr_core` cannot be imported, because `pyref/engine.py` otherwise falls back to
the pure-Python engine with only a warning, and that is a ~20× latency
regression whose only symptom is slow responses.

Packs are not baked in — the container downloads them at boot from the bucket
pinned in `packs.lock` (see *Pack distribution*), so adding a region is an
upload and a restart rather than an image rebuild.

`/health` reports readiness, not just liveness: 503 until a pack is actually
loaded, and it includes `engine` so a silent downgrade to the pure-Python path
is visible from outside without reading logs.

```json
{"status":"ok","packs_loaded":1,"region":"berkeley_oakland","num_edges":20678,"engine":"cpp"}
```

### On a phone, via a tunnel

Geolocation needs a **secure context**, so GPS origin tracking will not work
over plain http from a phone — you need HTTPS even for local testing. A tunnel
is the cheapest way to get it, and needs no hosting account.

The front-end proxies `/api/*` to the backend (`rewrites` in
`web/next.config.ts`), so **one** tunnel to port 3000 serves the whole app. That
matters more than it looks: `NEXT_PUBLIC_API_URL` is inlined at *build* time, so
pointing the browser straight at the API would mean rebuilding the front-end
every time a tunnel handed out a new hostname. Going through the proxy also
means no CORS configuration at all.

```bash
# terminal 1 — API on :8000
docker run -p 8000:8080 sr-api

# terminal 2 — web on :3000, proxying /api to the container
npm --prefix web run dev

# terminal 3 — public HTTPS URL for :3000
cloudflared tunnel --url http://localhost:3000
```

Open the printed `https://….trycloudflare.com` on the phone. `API_PROXY_TARGET`
overrides the proxy destination if the API is somewhere other than
`http://localhost:8000`.

### If you deploy it later

Nothing here is host-specific — it is a container that reads its configuration
from the environment (`SR_CORS_ORIGINS`, `SR_CORS_ORIGIN_REGEX`,
`SR_PACKS_URL`, `SR_NOMINATIM_CONTACT`). Two constraints carry over to any host:

- **HTTPS is mandatory**, for the geolocation reason above.
- **Run exactly one process, and do not use `--workers`.** The Nominatim rate
  limiter in `api/geocode.py` is a module-level lock plus a timestamp, so it
  only serialises within a single process. Each extra process or replica
  multiplies the request rate against a service whose policy allows roughly one
  per second. Platforms that autoscale by default need an explicit max of 1.
  Scaling out means moving that limiter somewhere shared first.

## API contract (frozen — a future mobile client reuses it)

`POST /route` `{origin:{lat,lon}, destination:{lat,lon}, departure_time,
safety_enabled, detour_budget_pct}` →

```json
{ "routes": [ {
    "kind": "fast|balanced|safe",
    "geometry": { "type": "LineString", "coordinates": [...] },
    "distance_m": 0, "eta_s": 0, "detour_pct": 0,
    "unsafe": { "unprotected_left": 0, "uncontrolled_crossing": 0, "total": 0 },
    "segments": [ { "geometry": {...}, "tier": "safe|caution|unsafe" } ],
    "unsafe_points": [ { "lon": 0, "lat": 0, "type": "unprotected_left|uncontrolled_crossing" } ]
} ] }
```

`detour_budget_pct` (request, optional — config default when omitted) and
`detour_pct` (response) are the additive fields; everything else is unchanged.

`GET /geocode?q=...` proxies Nominatim (rate-limited, identified UA, cached)
bounded to the pack bbox. Set `SR_NOMINATIM_CONTACT` to override the
User-Agent contact string per deployment.

`GET /meta` → `{region, bbox, num_edges}` — additive endpoint so the client
can tell whether a GPS fix falls inside the routable region. `bbox` is
`[west, south, east, north]`, or `null` for packs with no declared coverage.

## Front-end behavior

- **Current location.** The origin tracks live GPS (`watchPosition`). A
  deliberate origin — map click, marker drag, or geocode pick — disables
  follow mode so it is never overwritten; the ⌖ button re-enables it.
  Re-routing is throttled to movements over 25 m and debounced, so GPS
  jitter doesn't hammer the router. A fix outside the pack bbox falls back
  to the default view with an explanation rather than a 422.
- **Units.** Miles by default, toggleable to km, persisted in
  `localStorage`. The API always speaks SI (`distance_m`, `eta_s`);
  conversion is presentation-only so the response contract stays
  framework-agnostic for a future mobile client.
- **Map robustness.** MapLibre only auto-resizes on *window* resize, so a
  map created before layout (or while the tab/pane is hidden) would strand
  its canvas at the 400×300 fallback inside a full-size container — a blank
  map. A ResizeObserver plus a timer-based reconciliation (ResizeObserver
  and `requestAnimationFrame` callbacks are both tied to the page's
  rendering steps, which a non-compositing page never runs) keep the canvas
  matched to its container, and map errors surface in a banner instead of
  failing silently.

See [docs/map-provider-tradeoffs.md](docs/map-provider-tradeoffs.md) for the
MapLibre vs Google Maps Platform evaluation.

## Intersection control: observed vs inferred

Whether a maneuver is unsafe depends almost entirely on what holds back the
traffic you cross, so control classification is the accuracy-critical step.

OSM maps stop signs and traffic signals on the **approach arm** at the stop
line, not on the junction node — and OSMnx simplification deletes those
interstitial nodes along with their tags. On `berkeley_oakland` that discarded
87% of `highway=stop` nodes, 25% of `highway=traffic_signals`, and 100% of
`highway=give_way`, so most intersections fell through to a road-class guess
and fully signalized crossings were reported as uncontrolled.

`ingestion/approach_controls.py` therefore harvests control from the
**unsimplified** graph, walking outward from each junction along every arm
(up to `[ingest] max_control_offset_m`) and resolving per approach:

- a signal on **any** arm signalizes **every** leg (pedestrian crossings
  excluded — see `_is_pedestrian_signal`);
- a stop node on **every** arm is an all-way stop, even without `stop=all`,
  which is nearly never tagged;
- otherwise `must_stop` is read off **which arm the stop node sits on**,
  rather than guessed from road class;
- a `give_way` is ignored at a junction that has stop signs — a junction is
  stop-controlled or give-way-controlled, not both, and the stray tag is
  usually a yield-to-pedestrians marking (198 of 308 give-way junctions in
  `berkeley_oakland` are this mixed case);
- evidence is harmonized across the **two arms of one named street**, because a
  device that governs a road governs both directions of it. Tagging one arm
  only is a mapping gap; left alone it makes one direction of a through street
  "must stop" while the opposite direction keeps priority, which sends routes
  off that street mid-block;
- `direction` / `traffic_signals:direction` / `stop:direction` are honored.

Anything the tags do not settle still falls back to the road-class heuristic
in `ingestion/controls.py`. The two cases are distinguished by
`edge_control_confidence` (`ControlConfidence.OBSERVED` / `INFERRED`), and
**only OBSERVED approaches can be counted as unsafe maneuvers or painted red**
— a guess is not evidence of a hazard. Inferred unprotected approaches still
carry a damped penalty (`inferred_confidence_factor`), so routes keep
preferring known-controlled intersections without the UI claiming the unknown
one is dangerous. `approaches with OBSERVED control` in the sanity report is
the headline number: ~56% on `berkeley_oakland`.

Use `scripts/inspect_junction.py --lat --lon` to see how any single
intersection was classified and why.

## Modeling notes / heuristics (documented approximations)

- Permissive-vs-protected signals are not tagged in OSM, so every signal is
  `SIGNAL_PERMISSIVE`; `SIGNAL_PROTECTED` arises only via the
  `control_override` hook (tests/future data). A left at a signal is therefore
  discounted (`signal_left_factor`) into the caution band rather than counted:
  a signal is a form of traffic control, and routing toward signalized
  intersections is the point.
- "Busy" is a tunable weighted score of speed/lanes/volume (`[busy]`),
  so busyness is time-of-day dependent. "Major" additionally requires the road
  to be physically big (`major_lanes_min` / `major_class_max`) and gates the
  stop-controlled predicates. In practice `busy` almost implies `major` — only
  14 of 3,698 busy edges in `berkeley_oakland` are busy-but-not-major — so the
  gate is a safety net rather than a load-bearing filter.
- A 2-way stop offers the approach that must stop no protection at all: both
  the left onto and the straight across a major road are counted. The
  approaches with priority through the same node are not.
- Median presence is inferred from one-way major-class edges (dual
  carriageways map as one-way pairs).
- The crossing-street busyness of a STRAIGHT maneuver is approximated by
  "any other incoming approach at the node is busy".
- U-turns are disallowed except at dead ends (fixed penalty, config).

## Non-goals (scope guard)

No live traffic, no voice nav, no accounts, no ML scoring; OSM turn-restriction
relations are an optional future hard-constraint module. Mobile is future
scope — the API contract is framework-agnostic for a React Native/Expo or PWA
client.
