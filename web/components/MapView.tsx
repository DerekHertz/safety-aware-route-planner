"use client";

import {
  GeoJSONSource,
  Map as MLMap,
  Marker,
  NavigationControl,
  Popup,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import { LatLon, RouteAlternative, RouteKind } from "@/lib/types";

// OpenFreeMap: genuinely free vector tiles, no API key. (MapLibre demotiles
// are demo-only; do not hotlink tile.openstreetmap.org rasters.)
const STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";

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

interface Props {
  routes: RouteAlternative[];
  selected: RouteKind | null;
  onSelect: (kind: RouteKind) => void;
  origin: LatLon | null;
  destination: LatLon | null;
  onSetPoint: (which: "origin" | "destination", p: LatLon) => void;
}

const EMPTY_FC = { type: "FeatureCollection", features: [] } as const;

export default function MapView({
  routes, selected, onSelect, origin, destination, onSetPoint,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const originMarker = useRef<Marker | null>(null);
  const destMarker = useRef<Marker | null>(null);
  const loadedRef = useRef(false);
  // refs so map event handlers see current state without re-registering
  const stateRef = useRef({ origin, destination, onSetPoint, onSelect });
  stateRef.current = { origin, destination, onSetPoint, onSelect };

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

    map.on("load", () => {
      const kinds: RouteKind[] = ["fast", "balanced", "safe"];
      for (const kind of kinds) {
        map.addSource(`route-${kind}`, { type: "geojson", data: EMPTY_FC });
        map.addLayer({
          id: `route-${kind}`,
          type: "line",
          source: `route-${kind}`,
          layout: { "line-cap": "round", "line-join": "round" },
          paint: {
            "line-color": KIND_COLORS[kind],
            "line-width": 5,
            "line-opacity": 0.45,
          },
        });
        map.on("click", `route-${kind}`, () => stateRef.current.onSelect(kind));
        map.on("mouseenter", `route-${kind}`,
          () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", `route-${kind}`,
          () => (map.getCanvas().style.cursor = ""));
      }
      // selected route drawn on top, colored per-segment by safety tier
      map.addSource("route-tiers", { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: "route-tiers",
        type: "line",
        source: "route-tiers",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["get", "color"],
          "line-width": 6,
        },
      });
      // flagged unsafe maneuvers on the selected route
      map.addSource("unsafe-points", { type: "geojson", data: EMPTY_FC });
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
        new Popup()
          .setLngLat(ev.lngLat)
          .setHTML(`<strong>${label}</strong>`)
          .addTo(map);
      });
      loadedRef.current = true;
      syncRoutes();
    });

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
      else if (!s.destination) s.onSetPoint("destination", p);
      else s.onSetPoint("destination", p);
    });

    return () => {
      map.remove();
      mapRef.current = null;
      loadedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function syncRoutes() {
    const map = mapRef.current;
    if (!map || !loadedRef.current) return;
    const byKind = new Map(routes.map((r) => [r.kind, r]));
    for (const kind of ["fast", "balanced", "safe"] as RouteKind[]) {
      const r = byKind.get(kind);
      const src = map.getSource(`route-${kind}`) as GeoJSONSource;
      src?.setData(r
        ? { type: "Feature", properties: {}, geometry: r.geometry }
        : (EMPTY_FC as never));
      if (map.getLayer(`route-${kind}`)) {
        map.setPaintProperty(`route-${kind}`, "line-opacity",
          selected && selected !== kind ? 0.3 : 0.55);
      }
    }
    const sel = selected ? byKind.get(selected) : undefined;
    const tierSrc = map.getSource("route-tiers") as GeoJSONSource;
    tierSrc?.setData(sel
      ? {
          type: "FeatureCollection",
          features: sel.segments.map((s) => ({
            type: "Feature",
            properties: { color: TIER_COLORS[s.tier] },
            geometry: s.geometry,
          })),
        }
      : (EMPTY_FC as never));
    const ptSrc = map.getSource("unsafe-points") as GeoJSONSource;
    ptSrc?.setData(sel
      ? {
          type: "FeatureCollection",
          features: sel.unsafe_points.map((p) => ({
            type: "Feature",
            properties: { type: p.type },
            geometry: { type: "Point", coordinates: [p.lon, p.lat] },
          })),
        }
      : (EMPTY_FC as never));
  }

  // routes/selection -> layers
  useEffect(() => {
    syncRoutes();
    // fit map to the routes once per new route set
    const map = mapRef.current;
    if (map && routes.length > 0) {
      const coords = routes.flatMap((r) => r.geometry.coordinates);
      const lons = coords.map((c) => c[0]);
      const lats = coords.map((c) => c[1]);
      map.fitBounds(
        [[Math.min(...lons), Math.min(...lats)],
         [Math.max(...lons), Math.max(...lats)]],
        { padding: 60, duration: 400 },
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routes, selected]);

  // endpoint markers
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const sync = (
      ref: React.MutableRefObject<Marker | null>,
      point: LatLon | null,
      color: string,
      which: "origin" | "destination",
    ) => {
      if (!point) {
        ref.current?.remove();
        ref.current = null;
        return;
      }
      if (!ref.current) {
        ref.current = new Marker({ color, draggable: true })
          .setLngLat([point.lon, point.lat])
          .addTo(map);
        ref.current.on("dragend", () => {
          const p = ref.current!.getLngLat();
          stateRef.current.onSetPoint(which, { lat: p.lat, lon: p.lng });
        });
      } else {
        ref.current.setLngLat([point.lon, point.lat]);
      }
    };
    sync(originMarker, origin, "#0f766e", "origin");
    sync(destMarker, destination, "#b91c1c", "destination");
  }, [origin, destination]);

  return <div ref={containerRef} style={{ width: "100%", height: "100%" }} />;
}
