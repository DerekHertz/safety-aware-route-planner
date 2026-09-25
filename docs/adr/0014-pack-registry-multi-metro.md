---
Status: accepted (amended 2026-09-24: step 7 measured, a city pack is ~28k edges; 2026-09-25: san_francisco published under a per-region tag and served)
Date: 2026-09-20
---

# Multi-metro: a registry of disjoint packs, all loaded at startup, picked by coordinates

Phase 4 (handoff.md) turns ADR-0007's "region-agnostic by design, single-metro by
data" into several metros served by one deployment. It is a **pack-per-metro**
design: every pack stays metro-sized and every route is computed inside exactly one
pack. It is not a larger search space, so nothing here touches ADR-0009, the parity
core, or `pyref/costs.py`.

The short version:

- The deployment names the packs it serves in config. Each pack's coverage is the
  `bbox` already stamped into its manifest. **Served bboxes must not overlap**; an
  overlap stops startup.
- The server maps a request to a pack **from the origin and destination
  coordinates**. No new field on `/route` or `/reroute`.
- Origin outside every served pack, destination outside every served pack, or the two
  in different packs: **422** with the existing `{"detail": str}` shape. No
  cross-pack routing.
- **All served packs are loaded eagerly at startup. No LRU.** An
  Oakland-sized pack costs about **20 MiB resident**. In the shipped container
  that is about 27 MiB, and a `san_francisco`-sized pack is about 43 MiB. See the
  2026-09-24 amendment.
- `/meta` grows an additive `packs` list. `/geocode` takes an optional `region`
  parameter. The client's hard-coded Berkeley map center goes away.

## What exists today

One pack per process. `AppState.load()` resolves one directory, from `SR_PACK_DIR` or
`<api.pack_dir>/<region.active>`, and builds one `Router(pack, cfg)`. That constructor
builds the `SnapIndex` (a `cKDTree` over segment midpoints), `topo_of`, and
`PackStatics`, which is the (pack, cfg)-only half of the cost model from ADR-0009 3a.
Four other places assume there is only one pack:

- `/meta` returns that pack's `region`/`bbox`/`num_edges`. The client uses it for the
  "GPS fix outside coverage" check and for the region label.
- `/geocode` bounds Nominatim to that pack's bbox (`viewbox` + `bounded=1`).
- `/health` hard-codes `"packs_loaded": 1`.
- `MapView.tsx` hard-codes `center: [-122.268, 37.845], zoom: 12.5`.

`ensure_packs()` already takes a list of regions, and `packs.lock` already publishes
two (`berkeley_small`, `berkeley_oakland`). The ingestion and fetch side is multi-region
already. Only the serving side is not.

## Measured: what one resident pack costs

The figures below are from `berkeley_oakland`, the pack production serves (N=7,686
nodes, E=20,683 directed edges, T=61,955 turns). The brief asked for `berkeley_small`,
and it is measured too, but at 1,827 edges its figures are dominated by fixed overhead
and say little about a real metro. Both packs were resolved through
`tests/helpers/packs.py::real_pack()`. Each run was a fresh process: `GraphPack.load`,
then `Router(pack, cfg)`, then six `Router.route()` calls on random node pairs. RSS/USS
came from `psutil`, and per-component allocations from `tracemalloc` (numpy reports
its buffers to it).

| | `berkeley_small` | `berkeley_oakland` |
|---|---|---|
| on-disk pack | 0.39 MiB | 4.50 MiB |
| `GraphPack` arrays (traced) | 0.43 MiB | 4.75 MiB |
| `Router` on top of that (traced) | 0.96 MiB | 11.28 MiB |
| — of which `SnapIndex` | 0.66 MiB | **7.79 MiB** |
| — of which `PackStatics` | 0.31 MiB | 3.49 MiB |
| **resident, pack + Router (RSS delta)** | **~1.2 MiB** | **~20.4 MiB** (5.4 + 15.0) |
| transient peak inside one request (traced) | 0.42 MiB | 4.39 MiB |
| cold load: `GraphPack.load` + `Router()` | 11 + 31 ms | 101 + 226 ms |
| interpreter + numpy + scipy baseline (RSS) | ~67-71 MiB | ~67 MiB |

The `berkeley_oakland` RSS deltas repeated to within 0.5 MiB over three runs, and the
traced figures were identical each time. **The per-pack figure is about 20 MiB
resident, or roughly 1 KiB per directed edge.** Resident memory grows linearly with E
and with the geometry point count, because every structure listed is an array over
nodes, edges, turns or segments.

**How this was measured, and what it does not cover.** This agent could not reach
WSL, so the numbers come from Windows CPython 3.12.2, numpy 2.5.0 and scipy 1.14.0,
with the **pure-Python engine**. That scipy is outside its supported numpy range and
warned, but it ran. None of this changes the resident figure, because `sr_core`
adds nothing resident: `sr::Engine` holds non-owning pointers into the pack's own
turn arrays (`core/src/engine.hpp`). It allocates `dist`/`pred`/`dest_adjust` per
search, which is 24 B × E, about 0.5 MiB here, freed on return. That figure is
**computed from the source, not measured.** The baseline row also leaves out
FastAPI, uvicorn and pydantic, which the scratch script did not import. Re-measure it
inside the real container before sizing a replica.

Two observations the numbers force:

- **The `SnapIndex` is the largest thing a pack costs, at 38%**, more than the pack's
  own arrays. If memory ever becomes the constraint, cut it first: a float32 tree, or
  keeping only the projected endpoints it queries. The graph can stay as it is.
- **The per-request transient is per in-flight request, not per pack.** A burst of
  concurrent `/route` calls on an Oakland-sized pack costs about 4.4 MiB each, on top
  of the resident set. That was already true with one pack. It scales with the size of
  the pack being routed, not with how many packs are loaded.

### What this implies per replica

A replica needs the process baseline (roughly 70 MiB measured, and more once the web
stack is loaded), plus about 20 MiB for each Oakland-sized pack, plus about 4.4 MiB for
each request in flight. At 512 MiB with generous concurrency headroom, that leaves room
for **about 15-18 Oakland-sized packs**. Multi-metro does not bind on memory at the
sizes this repo builds today.

**Superseded 2026-09-24 by the amendment at the end of this ADR**, which builds
`san_francisco` and measures it in the real container. The guess is kept below as
written. **Guessed, not measured:** a real metro is bigger than `berkeley_oakland`, which is
roughly 9 × 14 km. At about 1 KiB per edge, a whole-metro pack of 200k-500k directed
edges would be 200-500 MiB, so one to three would fit per 1 GiB replica. No pack that
size has been built, and the edge counts are an order-of-magnitude guess, not a
number from ingestion. Build one real metro pack before sizing production. If the guess
holds, the scaling answer is to split metros across deployments (see "Consequences"),
not to evict.

## Decisions

### 1. Registry: config names the packs; the manifest supplies the bounds

- **Which packs to serve** is a new `[api] regions` list in `config/config.toml`,
  overridable by `SR_REGIONS` (comma-separated), like `SR_CORS_ORIGINS`. When absent
  it defaults to `[region.active]`, so today's single-pack deployment keeps working
  unchanged. `region.active` keeps its current meaning as the *ingestion* target.
- **A pack's name** is its directory name under `api.pack_dir`. It must equal the
  manifest's `region`, and a mismatch is fatal at load. The name is already the key in
  `packs.lock` and `[region.presets]`, and `ensure_packs(regions, ...)` already takes a
  list.
- **A pack's coverage** is its manifest `bbox` (`[west, south, east, north]`), stamped
  from the preset at build time by `ingestion/pack.py`. A served pack with a null bbox
  (a toy) is accepted only when it is the *only* served pack. There is nothing to route
  by otherwise.
- **`SR_PACK_DIR` still pins exactly one pack directory** and bypasses the registry,
  so the six API tests that monkeypatch it keep working as they are.
- **Overlapping bboxes are a startup error.** `berkeley_small` sits inside
  `berkeley_oakland`, so serving both would make the pack a route lands on depend on
  config order. The same coordinates would then produce different artifacts on two
  deployments, and neither artifact records why. Refusing the configuration is simpler
  than a precedence rule and keeps coordinate-to-pack a function.

Rejected:
- *Scanning `pack_dir` for every directory*: a stale dev pack left on disk would
  start taking traffic, and the deployment would not say what it serves.
- *Reading the list from `packs.lock`*: that file says what has been published, not
  what a given deployment should serve.
- *Smallest-containing-bbox precedence for overlaps*: deterministic, but it chooses
  the graph with less context at exactly the points where the two packs disagree.

### 2. Request to pack: both endpoints, by bbox containment, server-side

For `/route` and `/reroute` alike:

1. Find the packs whose bbox contains the origin and the packs whose bbox contains the
   destination. With disjoint bboxes each set has at most one member.
2. If both land in the same pack P, route on P's `Router`. All the existing snapping
   and `RoutingError` handling applies unchanged, including *"origin is too far from
   any drivable road"* for a point inside the bbox but far from a road.
3. Otherwise, reject **before any search runs** (it is a cheap check), with **422**
   and a `{"detail": str}` body. That is the shape `/route` already uses for every
   `RoutingError`:
   - origin in no pack: `"origin is outside every served region"`
   - destination in no pack: `"destination is outside every served region"`
   - different packs: `"origin and destination are in different regions (A, B); routing
     across regions is not supported"`

   **The route quota token is still spent.** `enforce_route_quota` runs first, as a
   dependency, and moving it would give callers a free probe of coverage.

This is **additive by construction**. No request or response model changes, so the
frozen `/route` contract, `schema_version`, `types.ts` and `check-schema-sync.mjs` are
all untouched. Today every point outside the one pack already gets a 422 (a snap
failure); the only change is a more precise message.

`/reroute` needs no carried pack identity. It re-derives the pack from the current
position and the original destination. Both lie on a route computed inside P, so both
are inside P's bbox, apart from GPS drift at the very edge of coverage. That already
returns a 422 today.

Rejected:
- *An explicit `region`/`metro` request field*: the client would compute it from the
  same bboxes, and a field that can contradict the coordinates needs a fourth error
  case of its own. It can be added later as an optional hint if two packs are ever
  allowed to overlap.
- *Cross-pack routing (stitching at the boundary)*: this is ADR-0009's tiled-pack
  problem, and it is not needed to serve disjoint metros.
- *A structured error body (`{"detail": {"code": ...}}`)*: that changes the
  `HTTPException` detail shape on the frozen endpoint. The client pre-checks coverage
  from `/meta` (decision 4), so the 422 is a backstop and does not need parsing.

Deferred, and **the owner should review it**: recording *which pack* produced an
artifact. `preference.traffic_basis` records the traffic inputs but nothing records
the graph. Under disjoint bboxes that identity can be recovered from the coordinates,
and it cannot change between two calls in one deployment. It becomes necessary once
packs are republished while clients hold artifacts, or when the commute planner
(ADR-0011) diffs a stored baseline against a replan that ran on a rebuilt pack. At
that point it is an additive `preference.graph_basis` in a schema v3.

### 3. Load all served packs at startup; no LRU

`AppState` gains a `PackRegistry`: `name -> (GraphPack, Router, bbox)`. Every entry is
built in `lifespan` before `/health` reports ready. At about 20 MiB and about 0.33 s
per Oakland-sized pack (measured above), a handful of metros costs well under 100 MiB
and a couple of seconds of boot.

Rejected:
- *LRU / load-on-demand*: a miss puts a cold `GraphPack.load` plus `Router()`
  (0.33 s here, and linear in E) on the request path of an unlucky user. In a fresh
  container it can also put a pack download there. Eviction would also need
  reference-counting against in-flight threadpool requests still holding the
  evicted `Router`. All of that complexity saves memory the numbers say is not scarce.
- *`np.memmap` pack arrays shared across processes*: the on-disk arrays are only about
  25% of the resident cost. The `SnapIndex` and `PackStatics` are derived and
  per-process either way.

**Revisit trigger:** the sum of served packs passes about half of a replica's memory
limit. Measure one real metro-sized pack first (see "What this implies per replica").

### 4. Process model: unchanged, and `--workers` gets worse

ADR-0013 made multiple replicas the way to add capacity. Nothing in this ADR changes
that. **Every replica serves every configured pack**, so the load balancer stays
region-blind and needs no sticky routing. The argument against `uvicorn --workers N`
gets stronger: each worker would now duplicate the resident set of *all* packs, not
just one. Keep the Dockerfile's single-process `CMD`.

The per-client routing quota (ADR-0013 amendment) stays **per client, not per
pack**. It rations this deployment's CPU, and one request costs about the same on
any metro-sized pack. As ADR-0013 already notes, this undercharges if a pack is ever
much larger than a metro.

### 5. Geocoding: per-region bounding, chosen by the client

`/geocode` accepts an optional `region` query parameter. When it names a served pack,
Nominatim is bounded to that pack's bbox, which is today's behaviour, per pack. When
it is absent and exactly one pack is served, the behaviour is unchanged. When it is
absent and several packs are served, the query goes out **unbounded** and the results
are **post-filtered** to points inside any served bbox. An unknown `region` returns
422. The per-process response cache keys on `(q, region)`.

The Nominatim token bucket stays **one global bucket**. It enforces a third party's
per-deployment policy, and ADR-0013's reasoning is unchanged. The client sends the
region its map is showing or its GPS fix is in, which is the region the user is almost
always searching.

Rejected:
- *A union bbox across all packs*: two metros 500 km apart produce a box covering
  half a state, and `bounded=1` then accepts matches from everywhere in between.
- *Querying Nominatim once per served pack*: this multiplies the one budget that fails
  closed.

ADR-0013's Photon trigger (*"coverage expands to enough regions that a per-region
index is being built by the ingestion pipeline anyway"*) is **approached but not
met** by a few metros. Re-read it when the served list passes about five.

### 6. Web client

- `GET /meta` gains an additive `packs: [{region, bbox, num_edges}]`. It lists every
  served pack in `[api] regions` order, and the first is the default. The existing
  top-level `region`/`bbox`/`num_edges` keep their meaning *for the default pack*, so
  an old client keeps working. `MetaResponse` → `PackMeta` is already mapped in
  `check-schema-sync.mjs`, so the new field is a hand-mirrored `types.ts` edit in the
  same PR. A separate `GET /packs` endpoint was rejected: it would be a second
  description of coverage that could drift from `/meta`.
- **Initial map view:** remove the hard-coded center. Fit to the bbox of the pack that
  contains the first GPS fix, or else fit to the default pack's bbox.
- **Coverage check:** `insideBbox(p, meta.bbox)` becomes "inside any of
  `meta.packs`". The region label comes from the pack containing the origin.
- **Pre-flight:** if the origin and destination fall in different packs, or in none,
  the client says so and never calls `/route`. This mirrors the server's error
  contract (decision 2), which stays the authority.
- The geocode `SearchBox` passes the current region (decision 5).
- `/health` reports the real `packs_loaded` and lists `regions`. `/health` is not part
  of the frozen contract.

### 7. Departure time needs a per-pack timezone

This is a finding from the survey of the code, not something the brief asked about.
**No code path handles time zones.** `sim/profiles.multipliers_at` reads only the
departure time's `.hour`/`.minute`. The web client sends a naive browser-local
timestamp. `/route` falls back to `datetime.datetime.now()`, which is naive server
time, and `python:3.12-slim` runs in UTC. So a caller that omits `departure_time`
*already* gets Bay Area traffic for a clock 7-8 hours off. That bug is latent today,
because the reference client always sends a time. Multi-metro makes it structural: one
server clock cannot be local time for two metros in different time zones.

Decision: each `[region.presets.<name>]` gains an IANA `timezone`, taken from config
rather than the manifest so the published immutable packs need no republish. A
missing timezone is fatal only if the pack is served. Before the profile lookup,
departure is resolved into the pack's local wall clock: an aware datetime is converted,
a naive one is taken as already pack-local (today's behaviour, unchanged), and "now"
becomes `now(pack_tz)`. The cost model still sees a naive local clock, so
`pyref/costs.py`, the golden digests and parity are untouched.

## Implementation sequence

Each step is one PR with a failing test written first. Steps 1-5 are backend-only; step
6 is the only one that touches the web client.

1. **`PackRegistry` with one pack, no behaviour change.** Add a pure
   `pack_for(o, d) -> name | error` over `(name, bbox)` pairs, and startup validation
   (name matches the manifest; bboxes disjoint; a null bbox only when alone). Tests are
   toy bboxes: containment, edges on the boundary (closed intervals, pinned in a test),
   overlap refused, name mismatch refused. `AppState` holds a registry of one and
   `routes.py` reads `registry.only()`. The whole existing suite stays green.
2. **`[api] regions` / `SR_REGIONS`, eager load of N packs, `/health` counts them.**
   Build two tiny toy packs with disjoint bboxes under `tmp_path` using the existing
   builder in `tests/test_api_contract.py`. Assert both load and `packs_loaded == 2`.
   An overlapping pair fails startup. `ensure_packs` receives the whole list.
3. **Route by coordinates, plus the 422 contract.** Using the two toy packs: O and D
   in A routes on A; one of each is 422 "different regions"; one outside everything is
   422 "outside every served region". Assert that no `Router.route` call happens (spy)
   and that the quota token is spent. Do the same for `/reroute`. Also add a
   real-pack test that a known Berkeley O/D still produces byte-identical artifacts
   through the registry, via `real_pack("berkeley_oakland")`.
4. **Per-pack timezone (decision 7).** Pure-function tests: an aware input is
   converted, a naive input passes through, `now` uses the pack's zone. A served pack
   without a timezone fails startup. The existing golden digests must not move.
5. **`/geocode?region=`.** Mock the Nominatim transport. Assert the `viewbox` is the
   named pack's, that the unbounded multi-pack case post-filters, that an unknown
   region is 422, and that the cache keys on region.
6. **`/meta.packs` plus the client.** The backend test covers the additive field and
   keeps the legacy fields equal to the default pack. Mirror it in `types.ts` and keep
   schema-sync green. Vitest covers `packForPoint`, the multi-pack `insideBbox`, the
   initial-view choice, and the cross-region pre-flight message. Remove the
   hard-coded center in `MapView.tsx`.
7. **Ops, and a second real metro.** Add a preset with `timezone`, run `build-packs`,
   paste the `packs.lock` stanza, and set `[api] regions`. **Re-measure resident
   memory on the real metro pack in the real container** and amend this ADR with the
   figure that replaces the guess above.

Steps 1-3 are the minimum for a deployment to serve two metros. Step 4 must land before
two metros **in different time zones** are served. Steps 5 and 6 are what make the
multi-metro service usable from the reference client.

## Consequences

- There is no engine, cost-model or parity change anywhere in this plan. The golden
  digests in `tests/test_costs_golden.py` are the check for that: they must not move
  in any step.
- If metro packs turn out to be several hundred MiB (step 7), the scale-out answer is
  **several deployments, each serving a subset of regions**. The client would choose
  an API base per region from a small static map. That is deferred until step 7's
  measurement says it is needed. It is the same region-blind replica model repeated,
  not a new architecture.
- `region.active` and `[api] regions` both exist and mean different things: the
  ingestion target and the served set. The config comments must say so where each
  appears.

## Amendment, 2026-09-24: step 7 measured — a city pack is ~28k edges, ~43 MiB in the container

Step 7 is done: measured on 2026-09-24, then published and served on 2026-09-25 (last
subsection). A second real pack,
`san_francisco`, was built locally and measured two ways: with the methodology of the
table above, and inside the shipped Docker image. **It replaces the "whole-metro pack of
200k-500k directed edges" guess.** That guess described a whole metropolitan region, and
pack-per-metro never builds one of those.

### What was built

`[region.presets.san_francisco]`, `bbox = [-122.515, 37.705, -122.355, 37.833]`, is the
City and County of San Francisco, Treasure Island included. It is the dense urban metro
that ADR-0015's amendment asked for. Its east edge (-122.355) is 0.035° (about 3 km)
clear of `berkeley_oakland`'s west edge (-122.32), and a test in
`tests/test_pack_registry.py` pins the two shipped presets as disjoint.

- **N = 10,229 nodes, E = 28,163 directed edges, T = 86,679 turns**, with 167,951
  geometry points and 139,788 snap segments. That is **1.36x `berkeley_oakland`'s
  edges**. The densest street grid on the West Coast is not an order of magnitude
  larger than Berkeley plus North Oakland.
- On disk the pack is 5.97 MiB, and it packages to a 3.53 MB `.tar.gz`
  (`berkeley_oakland`: 4.50 MiB and 2.69 MB).
- `python -m ingestion.build_pack --region san_francisco` took **57 s wall**, including
  one Overpass pull (a single 16 MB response, which the simplified and the raw graph
  share through OSMnx's cache). It used 36 s of CPU and peaked at **1.17 GiB RSS**,
  most of that the unsimplified graph (80,388 nodes, 140,177 edges).
- **Control harvesting works on dense data.** 52.1% of approaches are OBSERVED
  (`berkeley_oakland`: 55.8%). The observed approaches are 5,555 signal, 4,860 all-way
  stop, 3,860 two-way stop, 314 roundabout and 77 yield. San Francisco's all-way stops
  show up as they should: 4,860 observed, against 812 in `berkeley_oakland`.
- Speed is defaulted from road class on **74.1%** of edges (`berkeley_oakland`:
  57.4%), because San Francisco's `maxspeed` tagging is sparser.
- **Served next to `berkeley_oakland`,** `/health` reports `packs_loaded: 2`. Nine
  San Francisco O/D pairs each returned 2-3 alternatives with 0-2 counted unsafe
  maneuvers, Treasure Island to the Ferry Building included. A Berkeley pair still
  routes, and cross-bay pairs get the "different regions" 422. At 08:00, 0.51% of
  allowed turns count as unsafe (`berkeley_oakland`: 2.35%), because signals and
  all-way stops are everywhere. The bbox cuts the Golden Gate Bridge and the Bay
  Bridge's eastbound span, which leaves one-way dead ends where they cross it. The
  parts cut off are outside the city, so no in-city route needs them. The largest
  strongly connected component holds 99.4% of nodes (`berkeley_oakland`: 99.2%).

### In-process, the table's methodology, three fresh processes each

The table above was measured on Windows. Both packs are re-measured here on **Linux**:
WSL2 Ubuntu, CPython 3.12.3, numpy 2.5.1, scipy 1.18.0, the pure-Python engine, and the
same six random-pair `Router.route()` calls. RSS/USS come from `/proc/self`, not
`psutil`, and "traced" is `tracemalloc`. The Router is split into its parts with
traceback filters on `pyref/snap.py` and `pyref/costs.py`.

| | `berkeley_oakland` (table above, Windows) | `berkeley_oakland` (Linux) | `san_francisco` (Linux) |
|---|---|---|---|
| on-disk pack | 4.50 MiB | 4.50 MiB | 5.97 MiB |
| `GraphPack` arrays (traced) | 4.75 MiB | 4.74 MiB | 6.31 MiB |
| `Router` on top of that (traced) | 11.28 MiB | 12.19 MiB | 15.64 MiB |
| — of which `SnapIndex` | 7.79 MiB | 7.79 MiB | 9.82 MiB |
| — of which `PackStatics` | 3.49 MiB | 4.40 MiB | 5.82 MiB |
| **resident, pack + Router (RSS delta)** | **~20.4 MiB** (5.4 + 15.0) | **~31.9 MiB** (5.5 + 26.3) | **~46.4 MiB** (7.3 + 39.1) |
| — still resident after `malloc_trim(0)` | — | ~21.1 MiB | ~26.8 MiB |
| transient peak inside one request (traced) | 4.39 MiB | 4.21 MiB | 5.95 MiB |
| cold load: `GraphPack.load` + `Router()` | 101 + 226 ms | 119-128 + 117-136 ms | 132-395 + 182-210 ms |
| interpreter + numpy + scipy baseline (RSS) | ~67 MiB | ~62 MiB | ~62 MiB |

Across runs, RSS deltas agreed to within 0.6 MiB and traced figures to within 0.01 MiB.
USS matched RSS to within 0.5 MiB. Two things in the table need explaining:

- **`PackStatics` grew from 3.49 to 4.40 MiB on the same pack.** The `_crossing_legs`
  index arrays (ADR-0009, 2026-09-20) and ADR-0016's control-delay statics landed
  after the original table. That growth is real code, not measurement noise.
- **Linux holds about 11-20 MiB more than the live set.** Router construction makes
  large temporaries: the per-segment Python lists in `SnapIndex`, and numpy scratch in
  `build_pack_statics`. glibc keeps those pages after they are freed. Once it frees one
  large block, its dynamic mmap threshold rises, and later blocks go on the heap and stay
  resident. Calling `malloc_trim(0)` right after `Router()` gives back 10.8 MiB on
  `berkeley_oakland` and 19.6 MiB on `san_francisco`. What is left, ~21 and ~27 MiB, is
  the live set. The table's **~1 KiB per directed edge** still holds for the live set
  (1.05 and 0.97 KiB). The Windows heap had returned those pages, so the 20.4 MiB above
  was always the live set, not what a Linux replica holds.

### In the real container

These runs use the repo `Dockerfile`, built from this branch: `python:3.12-slim`,
`sr_core` built, and `/health` reporting `"engine":"cpp"`. The host was Docker Desktop
29.5.3 on WSL2 (kernel 6.6.87, cgroup v2). Packs were bind-mounted read-only at
`/app/data/packs`. `SR_PACKS_URL` pointed at a closed port, so any download attempt would
have failed startup. `SR_TRUSTED_PROXIES=1` and a distinct `X-Forwarded-For` per request
kept the per-client quota out of the way, and every request returned 200. Each run
was a fresh container. It idled 15 s after `/health` went 200 and was read. It then got
50 sequential `/route` calls at 08:15 (AM peak) over 10 fixed O/D pairs in the served
pack(s), settled 5 s, and was read again. The table reports the cgroup's
**`memory.current`**, which is what a container memory limit enforces, with PID 1's
(uvicorn's) `VmRSS` in brackets. Each cell is the mean of 3 runs, and runs agreed to
within 1.2 MiB. `berkeley_small` (1,827 edges) stands in for the no-pack baseline,
since the app cannot start without a pack.

| served | idle after startup | after 50 `/route` |
|---|---|---|
| `berkeley_small` (≈ baseline) | 63.7 MiB (96.0) | 64.6 MiB (96.7) |
| `berkeley_oakland` | 90.9 MiB (123.6) | 96.5 MiB (129.3) |
| `san_francisco` | 106.3 MiB (138.3) | 113.8 MiB (146.3) |
| both | 130.4 MiB (162.8) | 138.1 MiB (170.7) |

- **Per pack, over the baseline, at idle:** `berkeley_oakland` +27.2 MiB (1.35 KiB per
  edge), `san_francisco` +42.6 MiB (1.55 KiB per edge), and both together +66.7 MiB.
  That is 3 MiB under the sum, because the second pack's build reuses heap that the
  first one freed. For sizing, use **1.5 KiB per directed edge**.
- **Serving traffic adds a one-time high-water mark** of about 6-8 MiB: the freed
  request temporaries, kept by glibc as above. The figure does not grow with the number
  of packs (+7.7 MiB with both). Concurrent requests each add their own transient on top
  (the traced 4.2 / 6.0 MiB per request above).
- **`VmRSS` runs about 32 MiB above `memory.current`.** Those are shared file-backed
  pages (the interpreter and the numpy/scipy shared objects) that were not charged to
  this cgroup. `memory.stat` `file` read 0 in every run. On a cold host those pages can
  be charged to the first container that touches them, as reclaimable page cache.
  **Size limits on `memory.current`, and treat `VmRSS` as the conservative ceiling.**
- **One allocator knob, measured but not adopted.** `MALLOC_MMAP_THRESHOLD_=65536`
  pins the threshold, so large temporaries are mmapped and returned on free. On "both"
  it measured 105.1-105.9 MiB idle and 105.9-106.6 MiB after the 50 requests (3 runs).
  That is **about 25 MiB less at idle and 32 MiB less after load**. But the same 50
  sequential requests took 3.8-4.0 s instead of 2.8-3.2 s. That is plausibly the
  mmap/munmap and page faults on every per-request array, and it was not benchmarked.
  Adopting it needs a latency measurement first. Calling `malloc_trim(0)` once after
  `lifespan` loads the packs is the other candidate: it recovers the load-time half
  with no per-request cost.

### What this implies

- **The guess is replaced.** A dense city pack is **~28k directed edges and ~43 MiB in
  the container**, not 200k-500k edges and 200-500 MiB. `berkeley_oakland` is ~27 MiB
  there, against the 20 MiB this ADR has been quoting.
- **N resident packs per replica:** about 64 MiB baseline, plus 1.5 KiB × E per pack,
  plus about 8 MiB of warm high-water, plus 4-6 MiB per request in flight. At **512
  MiB** with 20 requests in flight (~120 MiB), that leaves room for about **7
  `san_francisco`-sized or 10-11 `berkeley_oakland`-sized packs**. The count above
  ("about 15-18 Oakland-sized packs") used the Windows live set and an import-only
  baseline, and is superseded. Decision 3's revisit trigger (served packs pass half the limit) fires
  at about 6 San Francisco-sized packs on a 512 MiB replica, or about 12 on 1 GiB.
  **Eager loading with no LRU stands.**
- **"Several deployments, each serving a subset of regions" (Consequences) is not
  needed** at city scale. Ten city-sized metros fit in one 1 GiB replica. It would
  become the answer again only for a region-scale pack: a nine-county Bay Area at a
  guessed 200-500k edges would be 300-750 MiB at 1.5 KiB per edge. Pack-per-metro does
  not call for that pack, so the note stays deferred, and the trigger above is what
  would revive it.
- **The `SnapIndex` is still the largest single structure,** 37% of the live set on
  `san_francisco` (9.82 of ~26.8 MiB), the same share as before. If memory ever
  binds, the order of levers is now: the allocator first (above, no code), then the
  `SnapIndex`.

### Publishing: per-region tags in `packs.lock` (2026-09-25)

`san_francisco` is published and served. **The owner chose a third option instead of
the two first drafted here.** Both drafts dodged the same problem. `packs.lock` had
one `tag` for every region, so publishing one region under a new tag would have
re-pointed the others at a directory that does not hold them. The drafts were to
reuse the old tag directory, or to rebuild every preset.

- **A `[regions]` entry may carry its own `tag`,** and the top-level `tag` is now only
  the default for entries without one. `api/packs_fetch.py` resolves each region's URL
  through it (`PacksLock.tag_for`). The `fetch-packs` action calls that code, so it
  follows the same rule. `scripts/package_packs.py`, which prints `build-packs`'s
  stanza, now puts `tag` on every line and prints no top-level `tag`. A one-region
  publish is therefore a one-line paste that cannot move another region.
- **Published** by one dispatch of `build-packs` (run 36157065575), with
  `regions=san_francisco` and `tag=packs-v2-20260925`. Publish and public-read
  verification both passed. The entry is `san_francisco = { sha256 =
  "8bd6c7e1…129e", bytes = 3531939, tag = "packs-v2-20260925" }`. The Berkeley
  entries and their tag are byte-for-byte unchanged. The CI build pulled fresh OSM data
  and has 43 more geometry points than the local build measured above, with identical
  node, edge and turn counts.
- **Served:** `[api] regions = ["berkeley_oakland", "san_francisco"]`, with
  `region.active` first as the default pack. `tests/test_multi_pack_load.py` now checks
  that every region the shipped config serves is in `packs.lock` and has a timezone,
  and that the served bboxes are disjoint. Those are the three things that would stop a
  fresh container at boot. The toy multi-pack tests strip the shipped `regions` key.
  The CI `docker image` job asserts `packs_loaded: 2` and routes one pair in each pack,
  fetching both from R2 as production does.
- **Checked on the real path:** an API with the shipped config and an empty pack
  directory fetched both packs from R2, each under its own tag, and verified their
  digests. `/health` reported `packs_loaded: 2` (48,841 edges: the published
  `berkeley_oakland` is 20,678), and one San Francisco pair and one Berkeley pair both
  routed.
