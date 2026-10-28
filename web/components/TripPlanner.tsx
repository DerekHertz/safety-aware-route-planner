"use client";

import { FormEvent, useState } from "react";
import {
  PlannerAuthError,
  fetchPlan,
  readToken,
  relaxationLabel,
  saveToken,
} from "@/lib/planner";
import { ConstraintName, LatLon, PlanResponse } from "@/lib/types";

interface Props {
  origin: LatLon | null;
  destination: LatLon | null;
  /** The device's wall clock, ISO without an offset. */
  nowLocal: () => string;
  /** A plan arrived: its routes replace the comparison, its Choice is shown. */
  onPlan: (resp: PlanResponse) => void;
}

/** The Trip planner (ADR-0019): a Request box and the Explanation panel. The
 *  Choice itself is highlighted on its route card by the page. */
export default function TripPlanner({
  origin,
  destination,
  nowLocal,
  onPlan,
}: Props) {
  const [text, setText] = useState("");
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needToken, setNeedToken] = useState(false);
  const [token, setToken] = useState("");

  const ready = !!origin && !!destination && text.trim().length > 0 && !busy;

  async function run(relax: ConstraintName[]) {
    if (!origin || !destination) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await fetchPlan(
        origin,
        destination,
        text.trim(),
        nowLocal(),
        relax,
        readToken(),
      );
      setPlan(resp);
      onPlan(resp);
    } catch (e) {
      if (e instanceof PlannerAuthError) setNeedToken(true);
      setError(e instanceof Error ? e.message : "planning failed");
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    if (ready) run([]);
  }

  function submitToken(e: FormEvent) {
    e.preventDefault();
    if (!token.trim()) return;
    saveToken(token.trim());
    setToken("");
    setNeedToken(false);
    run([]);
  }

  return (
    <section className="trip-planner" aria-label="Trip planner">
      <form onSubmit={submit}>
        <label htmlFor="trip-request" className="detour-label">
          Ask for a route
          <span className="hint-inline">
            e.g. “safest way, but no more than 10 minutes longer”
          </span>
        </label>
        <div className="trip-request-row">
          <input
            id="trip-request"
            type="text"
            maxLength={500}
            value={text}
            placeholder="How do you want to get there?"
            onChange={(e) => setText(e.target.value)}
          />
          <button type="submit" disabled={!ready}>
            {busy ? "Planning…" : "Plan"}
          </button>
        </div>
        {!origin || !destination ? (
          <p className="hint-inline">Pick an origin and a destination first.</p>
        ) : null}
      </form>

      {needToken && (
        <form className="trace-token" onSubmit={submitToken}>
          <input
            type="password"
            aria-label="Tester token"
            placeholder="Tester token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <button type="submit" disabled={!token.trim()}>
            Save
          </button>
        </form>
      )}
      {error && <div className="status error">{error}</div>}

      {plan && (
        <div
          className={`explanation ${plan.status}`}
          role="status"
          aria-live="polite"
        >
          {plan.explanation.map((line, i) => (
            <p key={i}>{line}</p>
          ))}
          {plan.status === "infeasible" && plan.relaxation && (
            <button
              type="button"
              disabled={busy}
              onClick={() => run(plan.relaxation!.constraints)}
            >
              {relaxationLabel(plan.relaxation.constraints)}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
