"use client";

import {
  GeoJSONSource,
  Map as MLMap,
  Marker,
  NavigationControl,
  Popup,
  setWorkerUrl,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Feature, FeatureCollection } from "geojson";
import { useCallback, useEffect, useRef, useState } from "react";
import { unsafePointPopup } from "@/lib/controlDelay";
import { bboxToLngLatBounds } from "@/lib/coverage";
import {
  bottomOverlap,
  initialFitPadding,
  shouldRefitInitial,
} from "@/lib/initialFit";
import { LatLon, RouteAlternative, RouteKind, UnsafeType } from "@/lib/types";

// OpenFreeMap: genuinely free vector tiles, no API key. (MapLibre demotiles
// are demo-only; do not hotlink tile.openstreetmap.org rasters.)
const STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

// MapLibre locates its tile-parsing worker from `import.meta.url`, and
// returns an empty string when that is not an http(s) URL — which is what
// happens once Turbopack bundles it. It then does `new Worker("")`, which
// resolves to the page's own HTML: a Worker that never replies and never
// errors, leaving every tile stuck in state "loading" and the map showing
// nothing but its background colour. Pointing MapLibre at the real worker,
// served from /public (kept in sync by scripts/copy-maplibre-worker.mjs),
// avoids the bundler for this one asset.
// Must run before any Map is constructed.
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

export const KIND_COLORS: Record<RouteKind, string> = {
  fast: "#2563eb",
  balanced: "#9333ea",
  safe: "#16a34a",
};
const TIER_COLORS: Record<string, string> = {
  safe: "#16a34a",
  caution: "#f59e0b",
  unsafe: "#dc2626",
};
const ROUTE_KINDS: RouteKind[] = ["fast", "balanced", "safe"];

interface Props {
  routes: RouteAlternative[];
  selected: RouteKind | null;
  onSelect: (kind: RouteKind) => void;
  origin: LatLon | null;
  destination: LatLon | null;
  onSetPoint: (which: "origin" | "destination", p: LatLon) => void;
  /** Origin came from live GPS — render it as a location puck, not a pin. */
  originIsLive?: boolean;
  /** Recenter the map on this point when it changes identity. */
  flyTo?: LatLon | null;
  /**
   * Viewport inset, in CSS pixels, for fitBounds and flyTo. On mobile the
   * bottom sheet floats over the map, so without a bottom inset the route is
   * fitted into an area the sheet is covering.
   */
  fitPadding?:
    number | { top: number; bottom: number; left: number; right: number };
  /** Continuously recenter/rotate the camera on `followTarget` while true.
   *  Suppresses the route-fit-bounds behavior, which would otherwise fight
   *  it on every reroute. */
  cameraFollow?: boolean;
  /** Point to keep centered while `cameraFollow` is true — typically the
   *  live GPS fix. */
  followTarget?: LatLon | null;
  /** Camera bearing, in degrees, while following. `null`/`undefined` leaves
   *  the current bearing alone (e.g. while stationary). */
  heading?: number | null;
  /** Fired when the user drags, zooms, or rotates the map by hand — the
   *  caller should drop `cameraFollow` in response, matching the standard
   *  "pan away, then recenter" nav-app pattern. */
  onUserGesture?: () => void;
  /** Where to draw the origin marker — defaults to `origin`. Lets a caller
   *  show the puck at the raw live GPS fix even while `origin` itself (used
   *  for routing requests) is deliberately pinned, e.g. while on-route and
   *  navigating, to avoid rerouting on every fix. */
  originMarkerPosition?: LatLon | null;
  /**
   * The coverage bbox, `[west, south, east, north]`, to frame when the map is
   * created. Read ONCE, at construction; later changes are ignored (the
   * first-GPS-fix flyTo moves the camera from there).
   *
   * `undefined` means "not known yet" (/meta in flight): the map is not
   * constructed at all until it is, so it never renders MapLibre's default
   * 0,0 view and then jumps. `null` means "known, and there is nothing to
   * frame" (/meta failed, or a toy pack with no bbox): the whole world.
   */
  initialBounds?: number[] | null;
  /** Fired with the map's center after it loads and after every move, so the
   *  caller can tell which served pack the map is showing. */
  onViewChange?: (center: LatLon) => void;
  /** An element floating over the bottom of the map (the mobile bottom
   *  sheet). Its measured overlap pads the first framing of
   *  `initialBounds`, so the region is not opened underneath it. An element
   *  beside the map, like the desktop sidebar column, overlaps nothing. */
  bottomOverlayRef?: React.RefObject<HTMLElement | null>;
}

const emptyFC = (): FeatureCollection => ({
  type: "FeatureCollection",
  features: [],
});

export default function MapView({
  routes,
  selected,
  onSelect,
  origin,
  destination,
  onSetPoint,
  originIsLive = false,
  flyTo = null,
  fitPadding = 60,
  cameraFollow = false,
  followTarget = null,
  heading = null,
  onUserGesture,
  originMarkerPosition = origin,
  initialBounds,
  onViewChange,
  bottomOverlayRef,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const originMarker = useRef<Marker | null>(null);
  const destMarker = useRef<Marker | null>(null);
  const layersReady = useRef(false);
  // Set by anything that moves the camera after construction — a gesture, the
  // first-GPS-fix flyTo, camera follow, a route fit — so the one-off padded
  // re-fit of initialBounds never overrides it.
  const cameraMoved = useRef(false);
  const [mapError, setMapError] = useState<string | null>(null);

  // Refs so the map's event handlers see current props without being torn down
  // and re-registered on every render.
  //
  // Written in an effect rather than during render: a render can be discarded
  // or replayed under concurrent rendering, and mutating a ref from one would
  // publish state that was never committed. Handlers here all fire from user
  // interaction, i.e. long after commit, so post-commit assignment is soon
  // enough. This effect is declared FIRST so it runs before the effects below
  // that call syncRoutes().
  const stateRef = useRef({
    origin,
    destination,
    onSetPoint,
    onSelect,
    onUserGesture,
    onViewChange,
    initialBounds,
    bottomOverlayRef,
  });
  const routesRef = useRef({ routes, selected });
  useEffect(() => {
    stateRef.current = {
      origin,
      destination,
      onSetPoint,
      onSelect,
      onUserGesture,
      onViewChange,
      initialBounds,
      bottomOverlayRef,
    };
    routesRef.current = { routes, selected };
  });

  /** Push current route data into the map sources. Safe to call any time —
   *  no-ops until the layers exist. */
  const syncRoutes = useCallback(() => {
    const map = mapRef.current;
    if (!map || !layersReady.current) return;
    const { routes, selected } = routesRef.current;
    const byKind = new Map(routes.map((r) => [r.kind, r]));

    for (const kind of ROUTE_KINDS) {
      const r = byKind.get(kind);
      const src = map.getSource(`route-${kind}`) as GeoJSONSource | undefined;
      const feature: Feature = {
        type: "Feature",
        properties: {},
        geometry: r ? r.geometry : { type: "LineString", coordinates: [] },
      };
      src?.setData(r ? feature : emptyFC());
      if (map.getLayer(`route-${kind}`)) {
        map.setPaintProperty(
          `route-${kind}`,
          "line-opacity",
          selected && selected !== kind ? 0.3 : 0.55,
        );
      }
    }

    const sel = selected ? byKind.get(selected) : undefined;

    const tiers: FeatureCollection = sel
      ? {
          type: "FeatureCollection",
          features: sel.segments.map((s): Feature => ({
            type: "Feature",
            properties: { color: TIER_COLORS[s.tier] },
            geometry: s.geometry,
          })),
        }
      : emptyFC();
    (map.getSource("route-tiers") as GeoJSONSource | undefined)?.setData(tiers);

    const points: FeatureCollection = sel
      ? {
          type: "FeatureCollection",
          features: sel.unsafe_points.map((p): Feature => ({
            type: "Feature",
            properties: { type: p.type, expected_wait_s: p.expected_wait_s },
            geometry: { type: "Point", coordinates: [p.lon, p.lat] },
          })),
        }
      : emptyFC();
    (map.getSource("unsafe-points") as GeoJSONSource | undefined)?.setData(
      points,
    );
  }, []);

  /** Create sources/layers. Idempotent: guarded by per-id existence checks,
   *  so it is safe to call repeatedly and after a style reload.
   *
   *  Deliberately gated on the style SPEC being parsed rather than on
   *  map.isStyleLoaded(). The latter additionally waits for sources to
   *  finish loading, which needs render frames — so on a page that isn't
   *  compositing it never becomes true and the overlays would never be
   *  created. addSource/addLayer only need the spec, and anything still
   *  not ready throws and is retried by a later trigger. */
  const initLayers = useCallback(
    (map: MLMap) => {
      if (!map.style) return;
      try {
        for (const kind of ROUTE_KINDS) {
          const id = `route-${kind}`;
          if (map.getSource(id)) continue;
          map.addSource(id, { type: "geojson", data: emptyFC() });
          map.addLayer({
            id,
            type: "line",
            source: id,
            layout: { "line-cap": "round", "line-join": "round" },
            paint: {
              "line-color": KIND_COLORS[kind],
              "line-width": 5,
              "line-opacity": 0.45,
            },
          });
          map.on("click", id, () => stateRef.current.onSelect(kind));
          map.on(
            "mouseenter",
            id,
            () => (map.getCanvas().style.cursor = "pointer"),
          );
          map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
        }

        // selected route drawn on top, colored per-segment by safety tier
        if (!map.getSource("route-tiers")) {
          map.addSource("route-tiers", { type: "geojson", data: emptyFC() });
          map.addLayer({
            id: "route-tiers",
            type: "line",
            source: "route-tiers",
            layout: { "line-cap": "round", "line-join": "round" },
            paint: { "line-color": ["get", "color"], "line-width": 6 },
          });
        }

        // flagged unsafe maneuvers on the selected route
        if (!map.getSource("unsafe-points")) {
          map.addSource("unsafe-points", { type: "geojson", data: emptyFC() });
          map.addLayer({
            id: "unsafe-points",
            type: "circle",
            source: "unsafe-points",
            paint: {
              "circle-radius": 8,
              "circle-color": "#dc2626",
              "circle-stroke-color": "#ffffff",
              "circle-stroke-width": 2,
            },
          });
          map.addLayer({
            id: "unsafe-points-label",
            type: "symbol",
            source: "unsafe-points",
            layout: {
              "text-field": [
                "case",
                ["==", ["get", "type"], "unprotected_left"],
                "L",
                "X",
              ],
              "text-size": 11,
              "text-font": ["Noto Sans Bold"],
              "text-allow-overlap": true,
            },
            paint: { "text-color": "#ffffff" },
          });
          map.on("click", "unsafe-points", (ev) => {
            const f = ev.features?.[0];
            if (!f) return;
            // Feature properties come back untyped. A missing wait (a server
            // predating ADR-0016) goes in as NaN, never as a coerced 0, so
            // the popup leaves the line out rather than claiming no wait.
            const waitS = f.properties?.expected_wait_s;
            const { title, wait } = unsafePointPopup(
              f.properties?.type as UnsafeType,
              typeof waitS === "number" ? waitS : NaN,
            );
            // Built as DOM text, not setHTML: the wait can read "<5 s".
            const el = document.createElement("div");
            const heading = el.appendChild(document.createElement("strong"));
            heading.textContent = title;
            if (wait) {
              const line = el.appendChild(document.createElement("div"));
              line.textContent = wait;
            }
            new Popup().setLngLat(ev.lngLat).setDOMContent(el).addTo(map);
          });
        }

        layersReady.current = true;
        syncRoutes();
      } catch {
        // Style spec not ready yet; load/style.load/visibilitychange/interval
        // all retry, and every step above is idempotent.
      }
    },
    [syncRoutes],
  );

  // Flips false -> true once, when the caller knows what to frame. A boolean
  // rather than the bbox itself, so a later bbox change can never tear the map
  // down and rebuild it.
  const viewKnown = initialBounds !== undefined;

  useEffect(() => {
    if (!containerRef.current || mapRef.current || !viewKnown) return;
    const bounds = stateRef.current.initialBounds;
    const map = new MLMap({
      container: containerRef.current,
      style: STYLE_URL,
      // Framed without padding: MapLibre refuses a fit whose padding exceeds
      // the canvas (the mobile sheet inset on a not-yet-sized 400x300
      // canvas) and then leaves the camera at 0,0 — the very flash this
      // avoids.
      ...(bounds
        ? { bounds: bboxToLngLatBounds(bounds) }
        : { center: [0, 0] as [number, number], zoom: 1 }),
      // Compact on small screens: the attribution is an ODbL obligation, so it
      // must stay reachable, but the expanded form eats a phone's width.
      attributionControl: { compact: true },
    });
    // Zoom buttons only where there is a precise pointer. On touch they are
    // 29px targets duplicating a pinch gesture, and they crowd a small screen.
    if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
      map.addControl(new NavigationControl(), "top-right");
    }
    mapRef.current = map;

    // ...then, once the canvas has its real size, re-frame the same bbox
    // padded clear of the bottom sheet (and the controls). Instant, so it is
    // not a visible jump; once; and never over a camera something else has
    // already moved. On an ordinary page the container is laid out before the
    // map is built, so this lands on the very first call below, before the
    // first frame; the resize paths retry it when the canvas started at the
    // 400x300 fallback.
    let refitDone = false;
    const tryInitialRefit = () => {
      const el = containerRef.current;
      if (!el) return;
      const w = el.clientWidth;
      const h = el.clientHeight;
      const canvas = map.getCanvas();
      const sized =
        w > 0 && h > 0 && canvas.clientWidth === w && canvas.clientHeight === h;
      if (
        !bounds ||
        !shouldRefitInitial({
          done: refitDone,
          hasBounds: true,
          cameraMoved: cameraMoved.current,
          sized,
        })
      ) {
        return;
      }
      refitDone = true;
      const overlay = stateRef.current.bottomOverlayRef?.current ?? null;
      const sheet = bottomOverlap(
        el.getBoundingClientRect(),
        overlay?.getBoundingClientRect() ?? null,
      );
      const padding = initialFitPadding({ width: w, height: h }, sheet);
      if (!padding) return; // sheet expanded: nothing worth framing into
      map.fitBounds(bboxToLngLatBounds(bounds), { padding, animate: false });
    };
    tryInitialRefit();

    // Surface failures instead of swallowing them. Without this, a broken
    // style or tile source leaves a silently blank map with no diagnostic.
    map.on("error", (e) => {
      const msg = e.error?.message ?? "unknown map error";
      console.error("[MapView]", msg);
      setMapError(msg);
    });

    // The style may already be loaded by the time we attach (e.g. a cached
    // style on remount), in which case "load" never fires again — so try
    // immediately, and also on both style events.
    // "styledata" fires as soon as the style spec is parsed, well before the
    // "load" event (which also waits on sources and therefore on rendering).
    const tryInit = () => initLayers(map);
    map.on("load", tryInit);
    map.on("load", tryInitialRefit);
    map.on("style.load", tryInit);
    map.on("styledata", tryInit);
    tryInit();

    // MapLibre only auto-resizes on WINDOW resize. When the container starts
    // at zero size (created before layout, or while the tab/pane is hidden)
    // the canvas sticks at MapLibre's 400x300 fallback forever and the map
    // renders into a corner of a blank area. Watch the container itself.
    const ro = new ResizeObserver(() => {
      map.resize();
      tryInitialRefit();
    });
    ro.observe(containerRef.current);

    // Belt and braces: ResizeObserver callbacks are delivered during the
    // page's rendering steps, so a page that never composites (hidden tab,
    // some embedded panes) never gets them — the very situation that leaves
    // the canvas stranded at 400x300. A timer is not tied to rendering, so
    // this reconciles the two sizes even then. Costs two reads per tick and
    // only calls resize() on a genuine mismatch.
    const reconcile = window.setInterval(() => {
      const el = containerRef.current;
      if (!el) return;
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (w === 0 || h === 0) return;
      const canvas = map.getCanvas();
      if (canvas.clientWidth !== w || canvas.clientHeight !== h) map.resize();
      tryInitialRefit();
      if (!layersReady.current) tryInit();
    }, 500);

    // A hidden page does not composite, so requestAnimationFrame never runs
    // and the map cannot render or finish loading. Kick it when we become
    // visible again.
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      map.resize();
      map.triggerRepaint();
      tryInitialRefit();
      tryInit();
    };
    document.addEventListener("visibilitychange", onVisible);

    // A user-initiated pan/zoom/rotate should drop camera-follow, same as any
    // other nav app — "originalEvent" is what distinguishes a real gesture
    // from our own programmatic easeTo/flyTo/fitBounds calls, which don't set it.
    const onGesture = (e: { originalEvent?: unknown }) => {
      if (!e.originalEvent) return;
      cameraMoved.current = true;
      stateRef.current.onUserGesture?.();
    };
    const reportView = () => {
      const c = map.getCenter();
      stateRef.current.onViewChange?.({ lat: c.lat, lon: c.lng });
    };
    map.on("load", reportView);
    map.on("moveend", reportView);

    map.on("dragstart", onGesture);
    map.on("zoomstart", onGesture);
    map.on("rotatestart", onGesture);

    map.on("click", (ev) => {
      // clicks on route/marker layers are handled above; only set endpoints
      // for plain map clicks
      const hits = map.queryRenderedFeatures(ev.point, {
        layers: [
          "route-fast",
          "route-balanced",
          "route-safe",
          "unsafe-points",
        ].filter((l) => map.getLayer(l)),
      });
      if (hits.length > 0) return;
      const p = { lat: ev.lngLat.lat, lon: ev.lngLat.lng };
      const s = stateRef.current;
      if (!s.origin) s.onSetPoint("origin", p);
      else s.onSetPoint("destination", p);
    });

    return () => {
      ro.disconnect();
      window.clearInterval(reconcile);
      document.removeEventListener("visibilitychange", onVisible);
      map.remove();
      mapRef.current = null;
      layersReady.current = false;
    };
  }, [initLayers, viewKnown]);

  // routes/selection -> layers, and fit the viewport to the new routes.
  // Skipped while cameraFollow is on: otherwise every reroute yanks the
  // camera back out to a route-fit, fighting the follow behavior below.
  useEffect(() => {
    syncRoutes();
    const map = mapRef.current;
    if (map && !cameraFollow && routes.length > 0) {
      const coords = routes.flatMap((r) => r.geometry.coordinates);
      if (coords.length) {
        const lons = coords.map((c) => c[0]);
        const lats = coords.map((c) => c[1]);
        cameraMoved.current = true;
        map.fitBounds(
          [
            [Math.min(...lons), Math.min(...lats)],
            [Math.max(...lons), Math.max(...lats)],
          ],
          { padding: fitPadding, duration: 400 },
        );
      }
    }
    // viewKnown: the map is built late (see initialBounds), so anything that
    // arrived before it must be applied once it exists.
  }, [routes, selected, syncRoutes, fitPadding, cameraFollow, viewKnown]);

  // explicit recenter request (e.g. first GPS fix, or the locate button)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !flyTo) return;
    cameraMoved.current = true;
    map.flyTo({
      center: [flyTo.lon, flyTo.lat],
      zoom: Math.max(map.getZoom(), 14),
      duration: 800,
      // Same reason as fitBounds: without the inset (mobile bottom sheet) the
      // puck can land under the sheet, so "locate me" appears to do nothing.
      // Only pass padding when there's an actual inset object — MapLibre reads
      // `.top` off the value whenever the key is PRESENT, so an explicit
      // `padding: undefined` (the desktop case) throws. Omit the key instead.
      ...(typeof fitPadding === "number" ? {} : { padding: fitPadding }),
    });
  }, [flyTo, fitPadding]);

  // continuous camera follow: recenter + rotate to heading on every live fix.
  // Flat pitch, no 3D tilt (see the navigation plan) — `heading ?? current
  // bearing` so a stationary fix (heading null) doesn't snap back to north.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !cameraFollow || !followTarget) return;
    cameraMoved.current = true;
    map.easeTo({
      center: [followTarget.lon, followTarget.lat],
      bearing: heading ?? map.getBearing(),
      pitch: 0,
      duration: 900,
      easing: (t) => t,
    });
  }, [cameraFollow, followTarget, heading]);

  // endpoint markers
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const sync = (
      ref: React.RefObject<Marker | null>,
      point: LatLon | null,
      color: string,
      which: "origin" | "destination",
      live: boolean,
    ) => {
      if (!point) {
        ref.current?.remove();
        ref.current = null;
        return;
      }
      // A live-GPS origin renders as a puck rather than a draggable pin, so
      // it reads differently from a point the user placed deliberately.
      const wantsPuck = which === "origin" && live;
      const isPuck = ref.current?.getElement().classList.contains("gps-puck");
      if (ref.current && isPuck !== wantsPuck) {
        ref.current.remove();
        ref.current = null;
      }
      if (!ref.current) {
        let marker: Marker;
        if (wantsPuck) {
          const el = document.createElement("div");
          el.className = "gps-puck";
          // "map" alignment: the puck's rotation is relative to true north,
          // matching `heading`, rather than to the screen (which would spin
          // it back upright every time the camera itself rotates).
          marker = new Marker({ element: el, rotationAlignment: "map" });
        } else {
          marker = new Marker({ color, draggable: true });
          marker.on("dragend", () => {
            const p = marker.getLngLat();
            stateRef.current.onSetPoint(which, { lat: p.lat, lon: p.lng });
          });
        }
        marker.setLngLat([point.lon, point.lat]).addTo(map);
        ref.current = marker;
      } else {
        ref.current.setLngLat([point.lon, point.lat]);
      }
      if (wantsPuck && ref.current) {
        ref.current
          .getElement()
          .classList.toggle("gps-puck--heading", heading != null);
        ref.current.setRotation(heading ?? 0);
      }
    };
    sync(originMarker, originMarkerPosition, "#0f766e", "origin", originIsLive);
    sync(destMarker, destination, "#b91c1c", "destination", false);
    // viewKnown: re-run once the late-built map exists (see initialBounds).
  }, [originMarkerPosition, destination, originIsLive, heading, viewKnown]);

  return (
    <div className="map-root">
      <div ref={containerRef} className="map-canvas" />
      {mapError && (
        <div className="map-error">
          <span>Map error: {mapError}</span>
          <button
            type="button"
            onClick={() => setMapError(null)}
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}
