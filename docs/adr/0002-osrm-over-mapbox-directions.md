# Route from OSRM, not Mapbox Directions, despite Mapbox's built-in control flags

Mapbox Directions reports `traffic_signal` and `stop_sign` on intersections, which OSRM does not,
but its Product Terms (read 2026-11-05) bar caching or storing results and bulk or automated
queries. We need stored responses for test fixtures, the checked-label set and a recorded
fallback, so the route source is OSRM (OSM data under ODbL) with controls joined from Overpass.
Mapbox's flags appeared to come from the same OSM data, so little evidence is lost. Mapbox
Directions remains a possible second adapter behind the RouteSource seam once its terms are
clarified with Mapbox.

## Consequences

OSRM's absolute ETAs ran about 40% below Mapbox's on the same routes (754 s against 1276 s via
Oregon), while the detour's added time agreed (+21 s against +18 s). The budget is a share of
whichever source's ETA is in use, never a mix.
