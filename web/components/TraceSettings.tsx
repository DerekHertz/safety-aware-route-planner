"use client";

import { FormEvent, useState } from "react";
import { OptInResult } from "@/lib/tripRecorder";
import { getTripRecorder, useTripRecorderStatus } from "@/lib/useTripRecorder";

type Refusal = Extract<OptInResult, { ok: false }>["reason"];

const REFUSAL: Record<Refusal, string> = {
  rejected: "The trip service doesn't recognise that token, or it was revoked.",
  unreachable:
    "Couldn't reach the trip service. Check the connection and retry.",
  error: "The trip service answered with an error. Try again later.",
  unconfigured: "This build has no trip service configured.",
};

/**
 * The one-time, per-device opt-in to trip-trace recording (ADR-0017): paste
 * the tester token the owner issued, checked with `GET /v1/me`. After that,
 * every live-navigation session is recorded and uploaded automatically;
 * there is no per-trip button. Renders nothing unless the build names a
 * commute service (NEXT_PUBLIC_COMMUTE_URL).
 */
export default function TraceSettings() {
  const status = useTripRecorderStatus();
  const [token, setToken] = useState("");
  const [checking, setChecking] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);

  if (!status.configured) return null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const rec = getTripRecorder();
    if (!rec) return;
    setChecking(true);
    setRefusal(null);
    const result = await rec.optIn(token);
    setChecking(false);
    if (result.ok) setToken("");
    else setRefusal(REFUSAL[result.reason]);
  };

  const forget = () => {
    if (
      window.confirm(
        "Remove the tester token and any trips not yet uploaded from this device?",
      )
    ) {
      void getTripRecorder()?.forget();
    }
  };

  const state = !status.optedIn
    ? "off"
    : status.rejected
      ? "token rejected"
      : status.enabled
        ? "on"
        : "paused";

  return (
    <details className="trace-settings">
      <summary>Trip recording (beta): {state}</summary>
      <p className="hint">
        While you navigate, this device records its GPS trace and uploads it to
        calibrate intersection waits. The first and last 300 m of every trip
        never leave the phone.
      </p>
      {status.rejected && (
        <div className="status error">
          The trip service rejected this device&apos;s token. Paste a new one,
          or forget this device.
        </div>
      )}
      {(!status.optedIn || status.rejected) && (
        <form className="trace-token" onSubmit={submit}>
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            aria-label="Tester token"
            placeholder="Paste your tester token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
          />
          <button
            type="submit"
            className="reset"
            disabled={checking || !token.trim()}
          >
            {checking ? "Checking…" : "Turn on"}
          </button>
        </form>
      )}
      {status.optedIn && (
        <div className="controls-row toggle-row">
          <label className="toggle">
            <input
              type="checkbox"
              checked={status.enabled}
              onChange={(e) =>
                void getTripRecorder()?.setEnabled(e.target.checked)
              }
            />
            <span>Record trips as {status.label}</span>
          </label>
          <button type="button" className="reset" onClick={forget}>
            Forget
          </button>
        </div>
      )}
      {status.queued > 0 && (
        <p className="hint">
          {status.queued} upload{status.queued === 1 ? "" : "s"} waiting.
        </p>
      )}
      {refusal && <div className="status error">{refusal}</div>}
    </details>
  );
}
