"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import RouteCard from "@/components/RouteCard";
import SearchBox from "@/components/SearchBox";
import { fetchMeta, fetchRoutes } from "@/lib/api";
import {
  DEFAULT_DETOUR_BUDGET,
  DETOUR_BUDGET_OPTIONS,
  GeocodeResult,
  LatLon,
  PackMeta,
  RouteAlternative,
  RouteKind,
} from "@/lib/types";
import {
  DEFAULT_UNITS,
  UNITS_STORAGE_KEY,
  UnitSystem,
  formatDistance,
  formatDuration,
  isUnitSystem,
} from "@/lib/units";
import {
  distanceMeters,
  insideBbox,
  useGeolocation,
} from "@/lib/useGeolocation";
import {
  COMPACT_QUERY,
  SHEET_PEEK_PX,
  useMediaQuery,
} from "@/lib/useMediaQuery";

// MapLibre touches `window` at import time — client-only bundle
const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

/** Re-route only after the tracked position moves this far, so GPS jitter
 *  doesn't hammer the router. */
const REROUTE_MIN_MOVE_M = 25;
const REROUTE_DEBOUNCE_MS = 1500;

function nowLocalIso(): string {
  const d = new Date();
  d.setSeconds(0, 0);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function Home() {
  const [origin, setOrigin] = useState<LatLon | null>(null);
  const [destination, setDestination] = useState<LatLon | null>(null);
  const [originText, setOriginText] = useState("");
  const [destText, setDestText] = useState("");
  const [departure, setDeparture] = useState<string>(nowLocalIso());
  const [safety, setSafety] = useState(true);
  const [detourBudget, setDetourBudget] = useState(DEFAULT_DETOUR_BUDGET);
  const [units, setUnits] = useState<UnitSystem>(DEFAULT_UNITS);
  const [routes, setRoutes] = useState<RouteAlternative[]>([]);
  const [selected, setSelected] = useState<RouteKind | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<PackMeta | null>(null);
  const [metaSettled, setMetaSettled] = useState(false);
  const [coverageNote, setCoverageNote] = useState<string | null>(null);
  const [followMode, setFollowMode] = useState(true);
  const [flyTo, setFlyTo] = useState<LatLon | null>(null);
  // Mobile only (the sheet styling is behind a max-width query); harmless on
  // desktop, where .sidebar is a static column and the class does nothing.
  const [sheetOpen, setSheetOpen] = useState(false);
  const reqSeq = useRef(0);
  const seededRef = useRef(false);

  const geo = useGeolocation(true);
  const isCompact = useMediaQuery(COMPACT_QUERY);

  // Keep routes clear of the sheet when fitting the viewport. Held constant
  // rather than tracking the expanded height: MapLibre cannot honour padding
  // larger than the container, and once the sheet is open there is no map worth
  // fitting into anyway — the user collapses it to look, and finds the route
  // framed correctly.
  const fitPadding = useMemo(
    () =>
      isCompact
        ? { top: 60, left: 24, right: 24, bottom: SHEET_PEEK_PX + 24 }
        : 60,
    [isCompact],
  );

  // --- unit preference (read in an effect so SSR and client markup agree) ---
  useEffect(() => {
    const stored = window.localStorage.getItem(UNITS_STORAGE_KEY);
    if (isUnitSystem(stored)) setUnits(stored);
  }, []);
  const changeUnits = (u: UnitSystem) => {
    setUnits(u);
    window.localStorage.setItem(UNITS_STORAGE_KEY, u);
  };

  // --- region metadata (bbox drives the coverage check) ---
  useEffect(() => {
    fetchMeta()
      .then(setMeta)
      .catch(() => setMeta(null))
      .finally(() => setMetaSettled(true));
  }, []);

  // --- GPS -> origin ---------------------------------------------------
  // The pack only covers one metro area, so a fix outside it would make every
  // request 422. Fall back to the default view with an explanation instead.
  useEffect(() => {
    if (!geo.position || !followMode) return;
    // Wait for the coverage bbox before acting on a fix. Without this, a GPS
    // fix that arrives before /meta resolves skips the range check entirely
    // and we adopt (and fly to) an out-of-coverage location, only to show the
    // "outside the mapped area" notice a moment later.
    if (!metaSettled) return;
    if (meta && !insideBbox(geo.position, meta.bbox)) {
      setCoverageNote(
        "You're outside the mapped area (Berkeley / North Oakland) — showing the default region. Pick points on the map to route.",
      );
      setFollowMode(false);
      return;
    }
    setCoverageNote(null);
    const next = geo.position;
    setOrigin((prev) => {
      // Ignore sub-threshold jitter so we don't re-route constantly.
      if (prev && distanceMeters(prev, next) < REROUTE_MIN_MOVE_M) return prev;
      return next;
    });
    setOriginText("Current location");
    if (!seededRef.current) {
      seededRef.current = true;
      setFlyTo(next);
    }
  }, [geo.position, followMode, meta, metaSettled]);

  useEffect(() => {
    if (geo.error) setCoverageNote(geo.error);
  }, [geo.error]);

  // --- routing ---------------------------------------------------------
  const runRoute = useCallback(async () => {
    if (!origin || !destination) return;
    const seq = ++reqSeq.current;
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchRoutes(
        origin,
        destination,
        departure,
        safety,
        detourBudget,
      );
      if (seq !== reqSeq.current) return; // stale response
      setRoutes(resp.routes);
      // Results are the reason to look at the panel, so raise it. Done here in
      // the response handler rather than in an effect watching `routes`: this
      // is a reaction to an event, not derived state.
      if (resp.routes.length > 0) setSheetOpen(true);
      setSelected((prev) =>
        prev && resp.routes.some((r) => r.kind === prev)
          ? prev
          : (resp.routes.find((r) => r.kind === "safe")?.kind ??
            resp.routes[0]?.kind ??
            null),
      );
    } catch (e) {
      if (seq !== reqSeq.current) return;
      setRoutes([]);
      setSelected(null);
      setError(e instanceof Error ? e.message : "routing failed");
    } finally {
      if (seq === reqSeq.current) setLoading(false);
    }
  }, [origin, destination, departure, safety, detourBudget]);

  // Debounced so a moving origin (or rapid edits) coalesces into one request.
  useEffect(() => {
    const t = setTimeout(runRoute, REROUTE_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [runRoute]);

  // --- manual overrides always win over live GPS -----------------------
  const setPoint = useCallback((which: "origin" | "destination", p: LatLon) => {
    const label = `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`;
    setSheetOpen(false);
    if (which === "origin") {
      setFollowMode(false); // a deliberate choice must not be overwritten
      setOrigin(p);
      setOriginText(label);
    } else {
      setDestination(p);
      setDestText(label);
    }
  }, []);

  const pickGeocode =
    (which: "origin" | "destination") => (r: GeocodeResult) => {
      const p = { lat: r.lat, lon: r.lon };
      if (which === "origin") {
        setFollowMode(false);
        setOrigin(p);
        setOriginText(r.name);
      } else {
        setDestination(p);
        setDestText(r.name);
      }
    };

  const locateMe = () => {
    seededRef.current = false;
    setCoverageNote(null);
    setFollowMode(true);
    geo.refresh();
    if (geo.position) setFlyTo({ ...geo.position });
  };

  const reset = () => {
    reqSeq.current++;
    setOrigin(null);
    setDestination(null);
    setOriginText("");
    setDestText("");
    setRoutes([]);
    setSelected(null);
    setError(null);
    setLoading(false);
    setFollowMode(false);
  };

  const originIsLive = followMode && !!geo.position;
  const regionLabel = useMemo(
    () => (meta ? meta.region.replace(/_/g, " ") : "Berkeley / North Oakland"),
    [meta],
  );

  // Label on the collapsed sheet. It is the only thing visible when the panel
  // is down, so it should say what the app is currently doing rather than
  // "Show panel".
  const routeSummary = useMemo(() => {
    if (loading) return "Routing…";
    if (error) return "Routing failed — tap for details";
    const chosen = routes.find((r) => r.kind === selected) ?? routes[0];
    if (!chosen) {
      if (!origin) return "Set a starting point";
      if (!destination) return "Set a destination";
      return "Show panel";
    }
    return `${formatDuration(chosen.eta_s)} · ${formatDistance(chosen.distance_m, units)} · ${
      chosen.unsafe.total === 0
        ? "no unsafe maneuvers"
        : `${chosen.unsafe.total} unsafe`
    }`;
  }, [loading, error, routes, selected, origin, destination, units]);

  return (
    <main className="layout">
      <aside
        className={`sidebar${sheetOpen ? " expanded" : ""}`}
        // Focusing a field must expand the sheet: the geocoder's suggestion
        // list drops downward, and while collapsed that is off the bottom of
        // the screen. onFocusCapture rather than wiring onFocus through every
        // input.
        //
        // Restricted to INPUT deliberately. Focus fires BEFORE click, so
        // reacting to any focus made the handle unusable: focusing it set the
        // sheet open, then its own onClick toggled it straight back shut and
        // nothing appeared to happen.
        onFocusCapture={(e) => {
          if ((e.target as HTMLElement).tagName === "INPUT") setSheetOpen(true);
        }}
      >
        <button
          type="button"
          className="sheet-handle"
          aria-expanded={sheetOpen}
          aria-controls="route-panel"
          onClick={() => setSheetOpen((v) => !v)}
        >
          {sheetOpen ? "Hide panel" : routeSummary}
        </button>
        <h1>Safety-Aware Routes</h1>
        <p className="hint intro">
          Search, click the map, or use your current location. Covered area:{" "}
          {regionLabel}.
        </p>

        <div className="origin-row">
          <SearchBox
            placeholder="Origin — search or click map"
            value={originText}
            onTextChange={setOriginText}
            onPick={pickGeocode("origin")}
          />
          <button
            type="button"
            className={`locate${originIsLive ? " active" : ""}`}
            onClick={locateMe}
            title="Use my current location"
            aria-label="Use my current location"
          >
            ⌖
          </button>
        </div>

        <SearchBox
          placeholder="Destination"
          value={destText}
          onTextChange={setDestText}
          onPick={pickGeocode("destination")}
        />

        <div className="controls-row">
          <label>
            Departure
            <input
              type="datetime-local"
              value={departure}
              onChange={(e) => setDeparture(e.target.value)}
            />
          </label>
        </div>

        <div className="controls-row toggle-row">
          <label className="toggle">
            <input
              type="checkbox"
              checked={safety}
              onChange={(e) => setSafety(e.target.checked)}
            />
            <span>Safety optimization</span>
          </label>
          <div className="unit-toggle" role="group" aria-label="Distance units">
            <button
              type="button"
              className={units === "imperial" ? "on" : ""}
              onClick={() => changeUnits("imperial")}
            >
              mi
            </button>
            <button
              type="button"
              className={units === "metric" ? "on" : ""}
              onClick={() => changeUnits("metric")}
            >
              km
            </button>
          </div>
        </div>

        <div className="controls-row detour-row">
          <span className="detour-label">
            Detour for a safer crossing
            <span className="hint-inline">
              How far out of your way to reach a light or an all-way stop
            </span>
          </span>
          <div
            className="segmented"
            role="group"
            aria-label="Detour budget for a safer crossing"
          >
            {DETOUR_BUDGET_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={detourBudget === opt.value ? "on" : ""}
                aria-pressed={detourBudget === opt.value}
                onClick={() => setDetourBudget(opt.value)}
                disabled={!safety}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div className="controls-row">
          <button type="button" className="reset" onClick={reset}>
            Reset
          </button>
        </div>

        {coverageNote && <div className="status note">{coverageNote}</div>}
        {loading && <div className="status">Routing…</div>}
        {error && <div className="status error">{error}</div>}

        <div className="cards" id="route-panel">
          {routes.map((r) => (
            <RouteCard
              key={r.kind}
              route={r}
              units={units}
              selected={selected === r.kind}
              onSelect={() => setSelected(r.kind)}
            />
          ))}
        </div>
        {routes.length > 0 && selected && (
          <p className="hint">
            The selected route is colored by maneuver safety tier (green / amber
            / red). Red markers flag unsafe maneuvers: L = unprotected left, X =
            uncontrolled crossing. Only maneuvers where the map data actually
            records the traffic control are flagged — a signalized left, or an
            intersection OpenStreetMap says nothing about, shows amber instead.
          </p>
        )}
      </aside>
      <div className="map-wrap">
        <MapView
          routes={routes}
          selected={selected}
          onSelect={setSelected}
          origin={origin}
          destination={destination}
          onSetPoint={setPoint}
          originIsLive={originIsLive}
          flyTo={flyTo}
          fitPadding={fitPadding}
        />
      </div>
    </main>
  );
}
