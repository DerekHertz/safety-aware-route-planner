"use client";

import { useEffect, useState } from "react";
import { KIND_COLORS } from "./MapView";
import { NavPhase } from "@/lib/navigation";
import { ManeuverType, RouteAlternative, UnsafeType } from "@/lib/types";
import { UnitSystem, formatDistance, formatDuration } from "@/lib/units";
import { useSpeech } from "@/lib/useSpeech";
import { RouteProgressState } from "@/lib/useRouteProgress";

interface Props {
  route: RouteAlternative;
  progress: RouteProgressState | null;
  units: UnitSystem;
  /** `arrived` swaps the live readout for a "you have arrived" panel. */
  phase: NavPhase;
  /** A reroute is in flight — show a "recalculating" status. */
  rerouting: boolean;
  onExit: () => void;
}

// No street names: the graph pack doesn't retain OSM `name` tags (see the
// navigation plan), so instructions are generic direction + distance.
const MANEUVER_ARROW: Record<ManeuverType, string> = {
  left: "↰",
  right: "↱",
  uturn: "↩",
};
const MANEUVER_TEXT: Record<ManeuverType, string> = {
  left: "Turn left",
  right: "Turn right",
  uturn: "Make a U-turn",
};

const ALERT_TEXT: Record<UnsafeType, string> = {
  unprotected_left: "Unprotected left ahead",
  uncontrolled_crossing: "Uncontrolled crossing ahead",
};

/** How long a safety-alert banner stays visible before auto-dismissing. */
const ALERT_BANNER_MS = 6000;

/** Replaces the route-selection panel while actively navigating: a
 *  persistent readout of remaining distance/time, rather than the static
 *  per-alternative summary `RouteCard` shows before a route is chosen. */
export default function NavHud({
  route,
  progress,
  units,
  phase,
  rerouting,
  onExit,
}: Props) {
  const color = KIND_COLORS[route.kind];
  const remainingM = progress?.remainingM ?? route.distance_m;
  const remainingS = progress?.remainingS ?? route.eta_s;
  const upcoming = progress?.nextManeuver ?? null;
  const untilManeuverM = upcoming
    ? upcoming.offset_m - (progress?.offsetM ?? 0)
    : null;

  const speech = useSpeech();

  // The most recently fired alert, held locally so the banner can persist
  // past the single render on which `progress.alerts` reports it — that
  // array only ever contains newly-entered-lookahead points, already
  // deduped upstream. Adopting a new alert is done directly during render
  // (comparing against the last-seen key, both held in state) rather than
  // in an effect: this is the "adjusting state when a prop changes" pattern
  // from the React docs, and it sidesteps react-hooks/set-state-in-effect
  // entirely since no setState call is inside a useEffect body. The actual
  // side effects (speaking, and scheduling the auto-dismiss) live in a
  // separate effect keyed off the banner below, where the state update for
  // dismissal happens inside the setTimeout callback rather than
  // synchronously in the effect body.
  const [lastAlertKey, setLastAlertKey] = useState<string | null>(null);
  const [alertBanner, setAlertBanner] = useState<{
    key: string;
    text: string;
  } | null>(null);

  const currentAlert = progress?.alerts?.[0] ?? null;
  const currentAlertKey = currentAlert
    ? `${currentAlert.point.type}:${currentAlert.offsetM}`
    : null;
  if (currentAlertKey && currentAlertKey !== lastAlertKey) {
    setLastAlertKey(currentAlertKey);
    setAlertBanner({
      key: currentAlertKey,
      text: ALERT_TEXT[currentAlert!.point.type as UnsafeType],
    });
  }

  useEffect(() => {
    if (!alertBanner) return;
    speech.speak(alertBanner.text);
    const t = setTimeout(() => setAlertBanner(null), ALERT_BANNER_MS);
    return () => clearTimeout(t);
  }, [alertBanner, speech]);

  // Announce a maneuver once, when it first becomes the upcoming one —
  // same render-time-adjustment pattern as the alert banner above, so the
  // "have we already announced this offset" check never needs a ref (which
  // this config's react-hooks/refs rule forbids reading/writing during
  // render anyway) or a setState call inside an effect.
  const [announcedOffset, setAnnouncedOffset] = useState<number | null>(null);
  const upcomingOffset = upcoming?.offset_m ?? null;
  if (upcomingOffset != null && upcomingOffset !== announcedOffset) {
    setAnnouncedOffset(upcomingOffset);
  }

  useEffect(() => {
    if (
      announcedOffset == null ||
      !upcoming ||
      upcoming.offset_m !== announcedOffset
    ) {
      return;
    }
    const distM = untilManeuverM != null ? Math.max(0, untilManeuverM) : 0;
    speech.speak(
      `${MANEUVER_TEXT[upcoming.type]} in ${formatDistance(distM, units)}`,
    );
    // Only re-announce when the maneuver identity changes, not on every
    // distance/units tick while it remains the upcoming one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [announcedOffset]);

  // Terminal arrival state — all hooks above have run, so an early return here
  // is safe. Replaces the live readout with a done panel (ADR-0008: nav gains an
  // arrival lifecycle it previously lacked).
  if (phase === "arrived") {
    return (
      <div className="nav-hud" style={{ borderLeftColor: color }}>
        <div className="nav-hud-top">
          <div className="nav-hud-readout">
            <span className="nav-hud-eta">You have arrived</span>
          </div>
          <button
            type="button"
            className="nav-hud-exit"
            onClick={onExit}
            aria-label="Finish navigation"
          >
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="nav-hud" style={{ borderLeftColor: color }}>
      <div
        className="voice-toggle unit-toggle"
        role="group"
        aria-label="Spoken instructions"
      >
        <button
          type="button"
          className={!speech.enabled ? "on" : ""}
          onClick={() => speech.setEnabled(false)}
        >
          🔇 Voice off
        </button>
        <button
          type="button"
          className={speech.enabled ? "on" : ""}
          onClick={() => speech.setEnabled(true)}
        >
          🔊 Voice on
        </button>
      </div>
      {alertBanner && (
        <div className="nav-hud-alert" key={alertBanner.key}>
          ⚠ {alertBanner.text}
        </div>
      )}
      {upcoming && untilManeuverM != null && (
        <div className="nav-hud-maneuver">
          <span className="nav-hud-arrow" aria-hidden="true">
            {MANEUVER_ARROW[upcoming.type]}
          </span>
          <span>
            {MANEUVER_TEXT[upcoming.type]} in{" "}
            {formatDistance(Math.max(0, untilManeuverM), units)}
          </span>
        </div>
      )}
      <div className="nav-hud-top">
        <div className="nav-hud-readout">
          <span className="nav-hud-eta">{formatDuration(remainingS)}</span>
          <span className="nav-hud-dist">
            {formatDistance(remainingM, units)} remaining
          </span>
        </div>
        <button
          type="button"
          className="nav-hud-exit"
          onClick={onExit}
          aria-label="Exit navigation"
        >
          Exit
        </button>
      </div>
      {rerouting ? (
        <div className="nav-hud-status">Recalculating…</div>
      ) : (
        progress?.offRoute && (
          <div className="nav-hud-status">Off route — recalculating…</div>
        )
      )}
    </div>
  );
}
