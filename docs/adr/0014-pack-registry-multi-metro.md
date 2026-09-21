---
Status: proposed
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
  Oakland-sized pack costs about **20 MiB resident**.
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

**Guessed, not measured:** a real metro is bigger than `berkeley_oakland`, which is
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
