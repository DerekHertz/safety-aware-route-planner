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
  control override (protected/roundabout → 0, all-way stop → ×0.25,
  right-of-way straights/rights → ×0.2), `penalty_s = k·max(raw,0)`,
  `g = time + λ·penalty`. The same post-override raw drives the unsafe-action
  counters (`raw > τ` + per-type predicate) and the safe/caution/unsafe tiers.
- **Alternatives**: λ sweep (0 / 0.5 / 1.5) → Jaccard dedup (0.8) →
  penalty-method rerun for genuine diversity, with a guard that drops a
  diversified route if it is safety-worse than the un-inflated optimum.
  Labels are assigned after dedup: lowest-λ survivor = fast, highest = safe.
- **`safety_enabled=false`**: λ forced to 0, single fast route; unsafe
  counters are still computed and reported.
- **Traffic**: deterministic synthetic hourly profiles by road-class group;
  the departure-time snapshot is frozen per query and feeds both congestion
  (travel time) and the volume term of the safety score.

## Build & run

Prereqs: Python 3.11+, Node 18+, MSVC (for the optional C++ core).

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
cached in `data/cache/` so Overpass is hit once per region.

## API contract (frozen — a future mobile client reuses it)

`POST /route` `{origin:{lat,lon}, destination:{lat,lon}, departure_time, safety_enabled}` →

```json
{ "routes": [ {
    "kind": "fast|balanced|safe",
    "geometry": { "type": "LineString", "coordinates": [...] },
    "distance_m": 0, "eta_s": 0,
    "unsafe": { "unprotected_left": 0, "uncontrolled_crossing": 0, "total": 0 },
    "segments": [ { "geometry": {...}, "tier": "safe|caution|unsafe" } ],
    "unsafe_points": [ { "lon": 0, "lat": 0, "type": "unprotected_left|uncontrolled_crossing" } ]
} ] }
```

`GET /geocode?q=...` proxies Nominatim (rate-limited, identified UA, cached)
bounded to the pack bbox.

## Modeling notes / heuristics (documented approximations)

- Intersection controls come from OSM tags where present
  (`highway=traffic_signals|stop`, `junction=roundabout`); untagged
  intersections are inferred from road-class rank + node degree
  (see `ingestion/controls.py`). Permissive-vs-protected signals are not
  tagged in OSM — signals default to `SIGNAL_PERMISSIVE`;
  `SIGNAL_PROTECTED` arises via the `control_override` hook (tests/future data).
- "Busy" is a tunable weighted score of speed/lanes/volume (`[busy]`),
  so busyness is time-of-day dependent.
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
