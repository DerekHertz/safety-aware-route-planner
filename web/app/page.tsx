"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import NavHud from "@/components/NavHud";
import RouteCard from "@/components/RouteCard";
import SearchBox from "@/components/SearchBox";
import TraceSettings from "@/components/TraceSettings";
import { fetchMeta, fetchRoutes } from "@/lib/api";
import {
  coverageLabel,
  initialViewBbox,
  insideCoverage,
  packForPoint,
  preflight,
  regionLabel as labelOf,
} from "@/lib/coverage";
import { googleMapsDirectionsUrl } from "@/lib/googleMapsLink";
import { compareRoutes } from "@/lib/routeComparison";
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
import { distanceMeters, useGeolocation } from "@/lib/useGeolocation";
import { useHeading } from "@/lib/useHeading";
import {
  COMPACT_QUERY,
  SHEET_PEEK_PX,
  useMediaQuery,
} from "@/lib/useMediaQuery";
import { useNavigation } from "@/lib/useNavigation";
import { useTripRecorder } from "@/lib/useTripRecorder";
import { useWakeLock } from "@/lib/useWakeLock";

// MapLibre touches `window` at import time — client-only bundle
const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

// Live turn-by-turn is quarantined behind an opt-in flag (ADR-0008): the whole
// navigation mode is unreachable unless NEXT_PUBLIC_ENABLE_LIVE_NAV is set. Read
// at module scope — NEXT_PUBLIC_* is inlined at build time.
const LIVE_NAV_ENABLED = process.env.NEXT_PUBLIC_ENABLE_LIVE_NAV === "1";

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
  // "Compare in Google Maps" (ADR-0015 amendment): built from the endpoints
  // THIS set of routes was requested for, not the live origin/destination,
  // which may already have moved while the debounced re-route is pending.
  const [googleMapsHref, setGoogleMapsHref] = useState<string | null>(null);
  const [selected, setSelected] = useState<RouteKind | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [meta, setMeta] = useState<PackMeta | null>(null);
  const [metaSettled, setMetaSettled] = useState(false);
  const [coverageNote, setCoverageNote] = useState<string | null>(null);
  const [followMode, setFollowMode] = useState(true);
  const [flyTo, setFlyTo] = useState<LatLon | null>(null);
  // Where the map is looking, reported by MapView after every move. Decides
  // which served pack a geocoder search is bounded to.
  const [mapCenter, setMapCenter] = useState<LatLon | null>(null);
  // Continuously recenters/rotates the camera on the live fix. Independent of
  // followMode (which only controls whether GPS feeds `origin`) so a manual
  // map pan can drop the camera without abandoning live tracking.
  const [cameraFollow, setCameraFollow] = useState(true);
  // Active turn-by-turn mode: swaps the route-selection panel for NavHud and
  // switches the reroute trigger from raw movement to actually leaving the
  // route (see useRouteProgress).
  const [navigating, setNavigating] = useState(false);
  // Mobile only (the sheet styling is behind a max-width query); harmless on
  // desktop, where .sidebar is a static column and the class does nothing.
  const [sheetOpen, setSheetOpen] = useState(false);
  const reqSeq = useRef(0);
  const seededRef = useRef(false);
  // The panel: a bottom sheet over the map on mobile, a column beside it on
  // desktop. MapView measures it to frame the region clear of the sheet.
  const sheetRef = useRef<HTMLElement>(null);

  const geo = useGeolocation(true);
  const heading = useHeading(geo.position);
  const isCompact = useMediaQuery(COMPACT_QUERY);

  const activeRoute = useMemo(
    () => routes.find((r) => r.kind === selected) ?? null,
    [routes, selected],
  );
  // Fast/balanced/safe, ordered and with each one's time cost against the
  // fastest of the set — the comparison the cards below render (ADR handoff
  // Phase 2: "is the safer route worth the extra minutes?").
  const comparisonRows = useMemo(() => compareRoutes(routes), [routes]);
  // The nav session owns the followed route, its progress, reroute + arrival
  // lifecycle. Seeded with the chosen alternative when navigation starts; the
  // planner's own state (origin/routes/selected) stays frozen for the session.
  const nav = useNavigation(
    navigating ? activeRoute : null,
    destination,
    geo.position,
    geo.accuracy,
  );
  const progress = nav.progress;
  // Trip-trace recording (ADR-0017): automatic for the nav session once this
  // device has opted in, inert otherwise.
  useTripRecorder(
    navigating ? nav.route : null,
    nav.phase,
    destination,
    geo.reading,
  );
  // Held for exactly the mounted-nav session (ADR-0012) — same gate NavHud
  // renders on below, NOT "a route is planned", so a phone sitting on a desk
  // between trips doesn't drain its battery over nothing.
  const wakeLock = useWakeLock(navigating && !!nav.route);

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

  // Dev-only debug handle: lets the console (or an automated driver) read
  // the current routes/state without a real UI selector for everything.
  // Assigns a plain window property — no React setState involved, so this
  // is not the effect-syncing pattern the hooks lint rule cares about.
  useEffect(() => {
    if (process.env.NODE_ENV === "production") return;
    (window as unknown as { __srDebug?: unknown }).__srDebug = {
      routes,
      selected,
      origin,
      destination,
      navigating,
      // The route actually being FOLLOWED and drawn while navigating — a
      // reroute replaces it in place, so `routes` (the frozen planner set) goes
      // stale mid-trip. Read this, not `routes`, to see the live geometry.
      navRoute: nav.route,
      navPhase: nav.phase,
      rerouting: nav.rerouting,
      wakeLockHeld: wakeLock.held,
      wakeLockSupported: wakeLock.supported,
      progress,
      geoPosition: geo.position,
      geoAccuracy: geo.accuracy,
      cameraFollow,
      heading,
    };
  });

  // --- unit preference (read in an effect so SSR and client markup agree) ---
  useEffect(() => {
    const stored = window.localStorage.getItem(UNITS_STORAGE_KEY);
    if (isUnitSystem(stored)) setUnits(stored);
  }, []);
  const changeUnits = (u: UnitSystem) => {
    setUnits(u);
    window.localStorage.setItem(UNITS_STORAGE_KEY, u);
  };

  // --- region metadata (the served packs drive coverage, view, pre-flight) ---
  useEffect(() => {
    fetchMeta()
      .then(setMeta)
      .catch(() => setMeta(null))
      .finally(() => setMetaSettled(true));
  }, []);

  // --- GPS -> origin ---------------------------------------------------
  // The served packs cover a few metro areas at most, so a fix outside all of
  // them would make every request 422. Stay on the default view with an
  // explanation instead.
  useEffect(() => {
    if (!geo.position || !followMode) return;
    // While navigating, the planner is frozen: the nav session owns the
    // followed route and reroutes via useNavigation, so `origin` must not move
    // (moving it here is exactly the overloading that caused the silent
    // safety-level swap — ADR-0008). The live puck still tracks GPS via
    // `followTarget`/`originMarkerPosition`, which read geo.position directly.
    if (navigating) return;
    // Wait for the coverage bbox before acting on a fix. Without this, a GPS
    // fix that arrives before /meta resolves skips the range check entirely
    // and we adopt (and fly to) an out-of-coverage location, only to show the
    // "outside the mapped area" notice a moment later.
    if (!metaSettled) return;
    if (meta && !insideCoverage(meta.packs, geo.position)) {
      setCoverageNote(
        `You're outside the mapped area (${coverageLabel(meta.packs)}) — showing the default region. Pick points on the map to route.`,
      );
      setFollowMode(false);
      return;
    }
    setCoverageNote(null);
    const next = geo.position;
    setOrigin((prev) => {
      // Ignore sub-threshold jitter so we don't re-route constantly while
      // just planning.
      if (prev && distanceMeters(prev, next) < REROUTE_MIN_MOVE_M) return prev;
      return next;
    });
    setOriginText("Current location");
    if (!seededRef.current) {
      seededRef.current = true;
      setFlyTo(next);
    }
  }, [geo.position, followMode, meta, metaSettled, navigating]);

  useEffect(() => {
    if (geo.error) setCoverageNote(geo.error);
  }, [geo.error]);

  // --- routing ---------------------------------------------------------
  const runRoute = useCallback(async () => {
    if (!origin || !destination) return;
    const seq = ++reqSeq.current;
    // Pre-flight (ADR-0014 decision 6): a pair the server can only refuse —
    // different regions, or outside all of them — is said here and never
    // sent. Skipped while /meta is unknown; the server stays the authority.
    const refusal = meta ? preflight(meta.packs, origin, destination) : null;
    if (refusal) {
      setRoutes([]);
      setSelected(null);
      setError(refusal);
      setLoading(false);
      return;
    }
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
      setGoogleMapsHref(googleMapsDirectionsUrl(origin, destination));
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
  }, [origin, destination, departure, safety, detourBudget, meta]);

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
    setCameraFollow(true);
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
    setNavigating(false);
  };

  const originIsLive = followMode && !!geo.position;
  // Only follow the camera while the origin actually IS the live fix — no
  // point chasing GPS around if the user placed the origin elsewhere.
  const followTarget = originIsLive ? geo.position : null;
  const onUserGesture = useCallback(() => setCameraFollow(false), []);
  const recenter = () => setCameraFollow(true);

  const startNavigating = () => {
    if (!LIVE_NAV_ENABLED) return;
    setCameraFollow(true);
    setNavigating(true);
  };
  const exitNavigating = () => setNavigating(false);
  // The pack containing the origin; with no origin (or one outside every
  // pack), everything that is covered. Null until /meta answers.
  const regionLabel = useMemo(() => {
    if (!meta) return null;
    const own = origin ? packForPoint(meta.packs, origin) : null;
    return own ? labelOf(own.region) : coverageLabel(meta.packs);
  }, [meta, origin]);
  // The pack a search is bounded to: the one the map shows, else the one the
  // GPS fix is in. Undefined (no `region` sent) when neither is covered.
  const searchRegion = useMemo(() => {
    if (!meta) return undefined;
    const pack =
      (mapCenter && packForPoint(meta.packs, mapCenter)) ||
      (geo.position && packForPoint(meta.packs, geo.position));
    return pack ? pack.region : undefined;
  }, [meta, mapCenter, geo.position]);
  // What the map frames when it is built: undefined until /meta settles, so
  // the map is not built on a guess (see MapView's initialBounds).
  const initialBounds = metaSettled
    ? initialViewBbox(meta?.packs ?? [], geo.position)
    : undefined;

  // Label on the collapsed sheet. It is the only thing visible when the panel
  // is down, so it should say what the app is currently doing rather than
  // "Show panel".
  const routeSummary = useMemo(() => {
    if (navigating && nav.route) {
      if (nav.phase === "arrived") return "You have arrived";
      const remainingS = progress?.remainingS ?? nav.route.eta_s;
      const remainingM = progress?.remainingM ?? nav.route.distance_m;
      return `${formatDuration(remainingS)} · ${formatDistance(remainingM, units)} remaining`;
    }
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
  }, [
    navigating,
    nav.route,
    nav.phase,
    progress,
    loading,
    error,
    routes,
    selected,
    origin,
    destination,
    units,
  ]);

  // While navigating, the map draws the FOLLOWED route (which a reroute
  // replaces in place) rather than the planner's alternatives, so the line on
  // screen always matches what the HUD is guiding along.
  const mapRoutes = navigating && nav.route ? [nav.route] : routes;
  const mapSelected = navigating && nav.route ? nav.route.kind : selected;

  return (
    <main className="layout">
      <aside
        ref={sheetRef}
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
        {navigating && nav.route ? (
          <NavHud
            route={nav.route}
            progress={progress}
            units={units}
            phase={nav.phase}
            rerouting={nav.rerouting}
            onExit={exitNavigating}
          />
        ) : (
          <>
            <p className="hint intro">
              Search, click the map, or use your current location.
              {regionLabel && <> Covered area: {regionLabel}.</>}
            </p>

            <div className="origin-row">
              <SearchBox
                placeholder="Origin — search or click map"
                value={originText}
                onTextChange={setOriginText}
                onPick={pickGeocode("origin")}
                region={searchRegion}
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
              region={searchRegion}
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
              <div
                className="unit-toggle"
                role="group"
                aria-label="Distance units"
              >
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
              {LIVE_NAV_ENABLED && activeRoute && originIsLive && (
                <button
                  type="button"
                  className="start-nav"
                  onClick={startNavigating}
                >
                  Start navigating
                </button>
              )}
            </div>

            {coverageNote && <div className="status note">{coverageNote}</div>}
            {loading && <div className="status">Routing…</div>}
            {error && <div className="status error">{error}</div>}

            <div className="cards" id="route-panel">
              {comparisonRows.map(({ route: r, deltaS }) => (
                <RouteCard
                  key={r.kind}
                  route={r}
                  // A time delta only means something with something to
                  // compare against — omit it when safety is off and the
                  // response is a lone "fast" route.
                  deltaS={comparisonRows.length > 1 ? deltaS : undefined}
                  units={units}
                  selected={selected === r.kind}
                  onSelect={() => setSelected(r.kind)}
                />
              ))}
            </div>
            {routes.length > 0 && googleMapsHref && (
              // A plain link: no key, no Google script, no prefetch. Nothing
              // is sent to Google until it is followed.
              <a
                className="gmaps-link"
                href={googleMapsHref}
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Compare in Google Maps (opens in a new tab)"
                title="Google's own route for the same start and destination"
              >
                Compare in Google Maps
                <span aria-hidden="true">↗</span>
              </a>
            )}
            {routes.length > 0 && selected && (
              <p className="hint">
                The selected route is colored by maneuver safety tier (green /
                amber / red). Red markers flag unsafe maneuvers: L = unprotected
                left, X = uncontrolled crossing; tap one for its expected wait.
                Only maneuvers where the map data actually records the traffic
                control are flagged — a signalized left, or an intersection
                OpenStreetMap says nothing about, shows amber instead.
              </p>
            )}
            {/* Recording only ever runs inside live nav (ADR-0017). */}
            {LIVE_NAV_ENABLED && <TraceSettings />}
          </>
        )}
      </aside>
      <div className="map-wrap">
        <MapView
          routes={mapRoutes}
          selected={mapSelected}
          onSelect={setSelected}
          origin={origin}
          destination={destination}
          onSetPoint={setPoint}
          originIsLive={originIsLive}
          flyTo={flyTo}
          fitPadding={fitPadding}
          cameraFollow={cameraFollow}
          followTarget={followTarget}
          heading={heading}
          onUserGesture={onUserGesture}
          originMarkerPosition={followTarget ?? origin}
          initialBounds={initialBounds}
          onViewChange={setMapCenter}
          bottomOverlayRef={sheetRef}
        />
        {originIsLive && !cameraFollow && (
          <button
            type="button"
            className="recenter-btn"
            onClick={recenter}
            title="Recenter on my location"
            aria-label="Recenter on my location"
          >
            ⌖
          </button>
        )}
      </div>
    </main>
  );
}
