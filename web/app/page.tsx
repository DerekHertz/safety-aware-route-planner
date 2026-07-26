"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import RouteCard from "@/components/RouteCard";
import SearchBox from "@/components/SearchBox";
import { fetchRoutes } from "@/lib/api";
import { GeocodeResult, LatLon, RouteAlternative, RouteKind } from "@/lib/types";

// MapLibre touches `window` at import time — client-only bundle
const MapView = dynamic(() => import("@/components/MapView"), { ssr: false });

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
  const [routes, setRoutes] = useState<RouteAlternative[]>([]);
  const [selected, setSelected] = useState<RouteKind | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reqSeq = useRef(0);

  const runRoute = useCallback(async () => {
    if (!origin || !destination) return;
    const seq = ++reqSeq.current;
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchRoutes(origin, destination, departure, safety);
      if (seq !== reqSeq.current) return; // stale response
      setRoutes(resp.routes);
      setSelected((prev) =>
        prev && resp.routes.some((r) => r.kind === prev)
          ? prev
          : resp.routes.find((r) => r.kind === "safe")?.kind ?? resp.routes[0]?.kind ?? null);
    } catch (e) {
      if (seq !== reqSeq.current) return;
      setRoutes([]);
      setSelected(null);
      setError(e instanceof Error ? e.message : "routing failed");
    } finally {
      if (seq === reqSeq.current) setLoading(false);
    }
  }, [origin, destination, departure, safety]);

  // auto-route whenever the inputs are complete / change
  useEffect(() => {
    runRoute();
  }, [runRoute]);

  const setPoint = useCallback((which: "origin" | "destination", p: LatLon) => {
    const label = `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`;
    if (which === "origin") {
      setOrigin(p);
      setOriginText(label);
    } else {
      setDestination(p);
      setDestText(label);
    }
  }, []);

  const pickGeocode = (which: "origin" | "destination") => (r: GeocodeResult) => {
    const p = { lat: r.lat, lon: r.lon };
    if (which === "origin") {
      setOrigin(p);
      setOriginText(r.name);
    } else {
      setDestination(p);
      setDestText(r.name);
    }
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
  };

  return (
    <main className="layout">
      <aside className="sidebar">
        <h1>Safety-Aware Routes</h1>
        <p className="hint">
          Search or click the map to set origin and destination
          (Berkeley / North Oakland).
        </p>
        <SearchBox
          placeholder="Origin — search or click map"
          value={originText}
          onTextChange={setOriginText}
          onPick={pickGeocode("origin")}
        />
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
          <button type="button" className="reset" onClick={reset}>
            Reset
          </button>
        </div>

        {loading && <div className="status">Routing…</div>}
        {error && <div className="status error">{error}</div>}

        <div className="cards">
          {routes.map((r) => (
            <RouteCard
              key={r.kind}
              route={r}
              selected={selected === r.kind}
              onSelect={() => setSelected(r.kind)}
            />
          ))}
        </div>
        {routes.length > 0 && selected && (
          <p className="hint">
            The selected route is colored by maneuver safety tier
            (green / amber / red). Red markers flag unsafe maneuvers:
            L = unprotected left, X = uncontrolled crossing.
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
        />
      </div>
    </main>
  );
}
