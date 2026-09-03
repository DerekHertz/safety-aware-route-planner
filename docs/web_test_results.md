# Live turn-by-turn navigation — retest report (round 2)

**App:** Safety-Aware Route Planner (`web`, Next.js 16.2.11 / Turbopack) at `http://localhost:3000`
**Build:** `NEXT_PUBLIC_ENABLE_LIVE_NAV=1 npm --prefix web run dev`, after the round-1 fixes
**Date:** 2026-09-03
**Scope:** re-verification of the three round-1 failures only. Previously-passing steps
(2, 3, 6) were not re-run except where they fell out of the reroute testing.
**Testing conditions:** unpatched. No browser-side workarounds were installed this round.

## Verdict

| Round-1 finding | Status |
|---|---|
| B1 — `⌖` hard-crashes the app (`padding: undefined` → `flyTo`) | **FIXED** |
| B2 — `__srMockGeo` throws on a single-point track | **FIXED** |
| Step 4 — off-route never detected, no reroute | **FIXED** |
| Step 5 — tier/color preserved across a reroute | **PASS** (was untestable in round 1) |

Three new observations follow. None block the feature; N1 is worth fixing because it
misleads automated tests, N3 is unresolved and flagged rather than asserted.

---

## Re-verified fixes

### B1 — `⌖` button: FIXED

Clicked with no patches installed. No crash, no console exceptions, no Next error overlay.
Origin reads "Current location", the button takes its highlighted state, and the map flies to
the puck at 37.876 / -122.268. Ran unpatched for the remainder of the session (multiple
`⌖` presses, camera follows, reroute camera moves) with no recurrence.

### B2 — single-point mock track: FIXED

`__srMockGeo.start([{lat: 37.8760, lon: -122.2680}])` ran for 6 s with **zero** exceptions;
`__srDebug.geoPosition` holds the last fix as expected. Single-point tracks were used freely
for the rest of the session, including the Step 6 arrival snippet, with no errors.

### Step 4 — reroute in place: FIXED

Captured with a `MutationObserver` on `.nav-hud` plus a 100 ms poll of the MapLibre
`route-fast` source:

```
7210ms [rgb(37,99,235)] srcN=55 :: <1 min 0.1 mi remaining Exit
8414ms [rgb(37,99,235)] srcN=55 :: <1 min 410 ft remaining Exit  Recalculating…
8466ms [rgb(37,99,235)] srcN=8  :: Turn left in 180 ft <1 min 0.1 mi remaining Exit
```

- `progress.offRoute` now transitions to `true` (round 1: never, at up to 1586 m off-route).
- "Recalculating…" renders in the HUD.
- The `route-fast` GeoJSON source is rebuilt with its first vertex at the current GPS
  position, terminating at the destination.
- `navigating` stayed `true` in every sample across all runs — it never fell back to the
  planner mid-trip.

Confirmed across three independent departures (150 m and 300 m perpendicular at 45 %, 50 %
and 75 % along the route). Representative reroute, showing `remainingM` recomputed upward
from the new position and `offsetM` reset:

```
12100ms remM=162 offM=89 offsetM=532 :: <1 min 0.1 mi remaining
13300ms remM=268 offM=32 offsetM=0   :: Turn left in 380 ft <1 min 0.2 mi remaining
```

### Step 5 — no tier/level swap: PASS

This check was vacuous in round 1 (no reroute occurred to preserve a tier across). It now
passes for real. Across every reroute:

- `__srDebug.selected` stayed `"fast"`.
- `.nav-hud` `border-left-color` stayed `rgb(37, 99, 235)` — byte-identical to the baseline
  captured immediately after Start navigating.
- The redrawn geometry landed on the `route-fast` source only; `route-balanced` and
  `route-safe` remained empty arrays throughout.

---

## New observations

### N1 — `__srDebug.routes` is stale after a reroute (test-surface bug)

After a reroute, the map and `__srDebug.progress` use the new geometry, but
`__srDebug.routes` still exposes the pre-reroute polyline:

```json
{
  "debugRoute": { "n": 40, "first": [-122.26773, 37.87602] },
  "mapRoute":   { "n": 12, "first": [-122.26409, 37.87560] },
  "inSync": false
}
```

The inconsistency is externally visible: at one point `progress.offRouteM` read 16 m while
the position was 287 m from the polyline `__srDebug.routes` reported — because progress was
computed against the *new* line and the snapshot held the *old* one.

**Why it matters.** `__srDebug` is the documented hook the test plan drives. Any automated
check that asserts "the route redrew" by reading `__srDebug.routes[...].geometry` will
conclude the reroute never happened. It cost real time this round — the intermediate finding
"route never changed" was wrong, and only inspecting `map.getStyle().sources['route-fast']`
directly disproved it.

**Fix.** Point `__srDebug.routes` at the same state the map layer renders from, so the debug
snapshot and the drawn route cannot diverge.

### N2 — "Recalculating…" is visible for ~52 ms

Present at 8414 ms, gone by 8466 ms. Functionally correct, but below the threshold at which a
person can perceive it — the round-1 report's 400–600 ms polling missed it entirely, and a
user watching the HUD will too. If it is meant as user-facing feedback, give it a minimum
display duration (~800–1000 ms) independent of how fast the reroute resolves.

### N3 — one false arrival at 228 m, NOT REPRODUCED

Observed once. The arrival panel fired while the traveler was 228 m from the destination:

```json
{ "offsetM": 201, "offRouteM": 226.6, "offRoute": false,
  "remainingM": 0, "nextManeuver": null,
  "geo":  { "lat": 37.875369, "lon": -122.259803 },
  "dest": { "lat": 37.874695, "lon": -122.262255 } }
```

`remainingM` reached 0 because `offsetM` clamped to the route's end vertex while the position
was 227 m lateral of the line — the same projection-clamping shape as the original round-1
`nearEndpoint` defect, surfacing in the arrival check instead.

**Three targeted repro attempts all produced correct behavior:**

1. 300 m overshoot straight past the destination → arrival fired at 31 m (legitimate), and
   correctly stayed arrived while continuing 300 m past.
2. Lateral 300 m departure perpendicular to the final segment at 75 % along the route →
   clean reroute, no arrival, `sawArrived: false`.
3. Mid-route 150 m perpendicular departure at 50 % → clean reroute, no arrival.

The one occurrence happened in a compound state: a second departure was launched off a
*freshly rerouted* line while the previous mock track was still settling. That suggests a
narrow race between the reroute completing and the arrival check running against a
half-updated projection, rather than a reliably reachable defect.

**Recommendation.** Not a release blocker without a repro. Worth a defensive guard regardless:
gate arrival on actual great-circle distance to the destination (`ARRIVAL_RADIUS_M = 25`)
rather than on `remainingM === 0`, so a clamped projection cannot by itself declare arrival.
That guard is cheap and would close the whole class.

---

## Note on the test plan's Step 4 script

The script in the original plan still does not exercise reroute on this route, and its passing
without a reroute is **correct behavior**, not a regression:

```
maxOffRouteM = 159, settles at 58 m
```

The route is L-shaped, so the `+0.0018 / +0.0018` offset from the 55 % point lands near the
*other* leg rather than leaving the route. Replace it with a perpendicular departure computed
from the local segment bearing:

```js
const src = map.getStyle().sources['route-fast'].data;
const c = (src.features ? src.features[0] : src).geometry.coordinates;
const i = Math.floor(c.length * 0.5), a = c[i], b = c[i + 1];
const latR = a[1] * Math.PI / 180;
let dx = (b[0] - a[0]) * Math.cos(latR), dy = b[1] - a[1];
const L = Math.hypot(dx, dy); dx /= L; dy /= L;
const px = -dy, py = dx, off = 150 / 111320;   // 150 m perpendicular
const track = [];
for (let k = 0; k < 5; k++) { const j = Math.floor(i * k / 5); track.push({lat: c[j][1], lon: c[j][0]}); }
for (let k = 1; k <= 10; k++) {
  const f = off * k / 3;
  track.push({lat: a[1] + py * f, lon: a[0] + px * f / Math.cos(latR)});
}
__srMockGeo.start(track, {intervalMs: 1200});
```

And because of N1, assert the redraw against the map source, not `__srDebug.routes`:

```js
const ft = (s => s.features ? s.features[0] : s)(map.getStyle().sources['route-fast'].data);
ft.geometry.coordinates[0];        // should be at the current GPS position after a reroute
map.getStyle().sources['route-balanced'].data.features;  // should stay empty (tier preserved)
map.getStyle().sources['route-safe'].data.features;      // should stay empty
```

"Recalculating…" needs an observer or ≤50 ms polling to catch (see N2).