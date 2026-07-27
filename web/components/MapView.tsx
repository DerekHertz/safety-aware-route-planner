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
import { LatLon, RouteAlternative, RouteKind } from "@/lib/types";

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
}

const emptyFC = (): FeatureCollection => ({ type: "FeatureCollection", features: [] });

export default function MapView({
  routes, selected, onSelect, origin, destination, onSetPoint,
  originIsLive = false, flyTo = null,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const originMarker = useRef<Marker | null>(null);
  const destMarker = useRef<Marker | null>(null);
  const layersReady = useRef(false);
  const [mapError, setMapError] = useState<string | null>(null);
  // refs so map event handlers see current state without re-registering
  const stateRef = useRef({ origin, destination, onSetPoint, onSelect });
  stateRef.current = { origin, destination, onSetPoint, onSelect };
  const routesRef = useRef({ routes, selected });
  routesRef.current = { routes, selected };

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
        type: "Feature", properties: {}, geometry: r ? r.geometry : { type: "LineString", coordinates: [] },
      };
      src?.setData(r ? feature : emptyFC());
      if (map.getLayer(`route-${kind}`)) {
        map.setPaintProperty(`route-${kind}`, "line-opacity",
          selected && selected !== kind ? 0.3 : 0.55);
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
            properties: { type: p.type },
            geometry: { type: "Point", coordinates: [p.lon, p.lat] },
          })),
        }
      : emptyFC();
    (map.getSource("unsafe-points") as GeoJSONSource | undefined)?.setData(points);
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
  const initLayers = useCallback((map: MLMap) => {
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
      map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
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
          "text-field": ["case", ["==", ["get", "type"], "unprotected_left"], "L", "X"],
          "text-size": 11,
          "text-font": ["Noto Sans Bold"],
          "text-allow-overlap": true,
        },
        paint: { "text-color": "#ffffff" },
      });
      map.on("click", "unsafe-points", (ev) => {
        const f = ev.features?.[0];
        if (!f) return;
        const label = f.properties?.type === "unprotected_left"
          ? "Unprotected left turn onto a busy street"
          : "Uncontrolled crossing of a busy street";
        new Popup().setLngLat(ev.lngLat).setHTML(`<strong>${label}</strong>`).addTo(map);
      });
    }

    layersReady.current = true;
    syncRoutes();
    } catch {
      // Style spec not ready yet; load/style.load/visibilitychange/interval
      // all retry, and every step above is idempotent.
    }
  }, [syncRoutes]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new MLMap({
      container: containerRef.current,
      style: STYLE_URL,
      center: [-122.268, 37.845], // Berkeley/Oakland
      zoom: 12.5,
    });
    map.addControl(new NavigationControl(), "top-right");
    mapRef.current = map;

    // Surface failures instead of swallowing them. Without this, a broken
    // style or tile source leaves a silently blank map with no diagnostic.
    map.on("error", (e) => {
      const msg = (e as { error?: Error })?.error?.message ?? "unknown map error";
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
    map.on("style.load", tryInit);
    map.on("styledata", tryInit);
    tryInit();

    // MapLibre only auto-resizes on WINDOW resize. When the container starts
    // at zero size (created before layout, or while the tab/pane is hidden)
    // the canvas sticks at MapLibre's 400x300 fallback forever and the map
    // renders into a corner of a blank area. Watch the container itself.
    const ro = new ResizeObserver(() => map.resize());
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
      if (!layersReady.current) tryInit();
    }, 500);

    // A hidden page does not composite, so requestAnimationFrame never runs
    // and the map cannot render or finish loading. Kick it when we become
    // visible again.
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      map.resize();
      map.triggerRepaint();
      tryInit();
    };
    document.addEventListener("visibilitychange", onVisible);

    map.on("click", (ev) => {
      // clicks on route/marker layers are handled above; only set endpoints
      // for plain map clicks
      const hits = map.queryRenderedFeatures(ev.point, {
        layers: ["route-fast", "route-balanced", "route-safe", "unsafe-points"]
          .filter((l) => map.getLayer(l)),
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
  }, [initLayers]);

  // routes/selection -> layers, and fit the viewport to the new routes
  useEffect(() => {
    syncRoutes();
    const map = mapRef.current;
    if (map && routes.length > 0) {
      const coords = routes.flatMap((r) => r.geometry.coordinates);
      if (coords.length) {
        const lons = coords.map((c) => c[0]);
        const lats = coords.map((c) => c[1]);
        map.fitBounds(
          [[Math.min(...lons), Math.min(...lats)],
           [Math.max(...lons), Math.max(...lats)]],
          { padding: 60, duration: 400 },
        );
      }
    }
  }, [routes, selected, syncRoutes]);

  // explicit recenter request (e.g. first GPS fix, or the locate button)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !flyTo) return;
    map.flyTo({ center: [flyTo.lon, flyTo.lat], zoom: Math.max(map.getZoom(), 14), duration: 800 });
  }, [flyTo]);

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
          marker = new Marker({ element: el });
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
    };
    sync(originMarker, origin, "#0f766e", "origin", originIsLive);
    sync(destMarker, destination, "#b91c1c", "destination", false);
  }, [origin, destination, originIsLive]);

  return (
    <div className="map-root">
      <div ref={containerRef} className="map-canvas" />
      {mapError && (
        <div className="map-error">
          <span>Map error: {mapError}</span>
          <button type="button" onClick={() => setMapError(null)} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}
    </div>
  );
}
