"use client";

// Screen Wake Lock for mounted turn-by-turn (ADR-0012). On iOS a backgrounded
// PWA suspends, which kills watchPosition and speech together — so for live
// nav the screen must not sleep. This is a hard prerequisite for promoting
// live nav off NEXT_PUBLIC_ENABLE_LIVE_NAV (ADR-0008), not a nicety.

import { useEffect, useRef, useState } from "react";

export interface WakeLockState {
  /** Whether the browser is ACTUALLY holding the lock right now — not just
   *  whether one was requested. The request can be silently refused (page
   *  not visible, low battery, permissions policy) or revoked later, so this
   *  is what a "screen will stay on" indicator should trust, not `wanted`. */
  held: boolean;
  /** False when `navigator.wakeLock` doesn't exist at all (older Safari,
   *  some in-app browsers). Informational only — requesting is always safe
   *  to attempt regardless, see `useWakeLock` below. */
  supported: boolean;
}

/** The slice of `WakeLockSentinel` this needs, typed narrowly (rather than
 *  the ambient `WakeLockSentinel`) so a test's fake sentinel doesn't have to
 *  implement the full browser interface. */
interface WakeLockSentinelLike {
  release(): Promise<void>;
  addEventListener(type: "release", listener: () => void): void;
  removeEventListener(type: "release", listener: () => void): void;
}

/** Ditto for `WakeLock` itself. */
interface WakeLockLike {
  request(type: "screen"): Promise<WakeLockSentinelLike>;
}

/** The slice of `Navigator` this needs, typed narrowly (rather than the
 *  ambient `Navigator`) so tests can hand it a plain stub object instead of
 *  a real browser global. `wakeLock` is optional here even though lib.dom
 *  declares it as always-present: it genuinely isn't, on some browsers. */
interface WakeLockNavigator {
  wakeLock?: WakeLockLike;
}

/** Ditto for `Document` — just enough to listen for visibility changes. Any
 *  real `Document` (and Node's built-in `EventTarget`, which is all the
 *  tests use) satisfies this. */
interface WakeLockDocument {
  readonly visibilityState: string;
  addEventListener(type: "visibilitychange", listener: () => void): void;
  removeEventListener(type: "visibilitychange", listener: () => void): void;
}

export interface WakeLockController {
  /** True if this environment exposes the Wake Lock API at all. */
  readonly supported: boolean;
  /** Start or stop wanting the lock. Idempotent — calling it with the
   *  value it already has is a no-op. */
  setWanted(wanted: boolean): void;
  /** Stop wanting the lock and detach the visibilitychange listener.
   *  Call once, on unmount. */
  destroy(): void;
}

/**
 * The engine behind `useWakeLock`, factored out — and taking `nav`/`doc` as
 * plain injected parameters rather than reading the globals itself — so the
 * reacquisition logic can be driven directly in tests without rendering
 * anything (see useWakeLock.test.ts).
 *
 * **The reacquisition half is the whole point.** A wake lock is released
 * automatically by the browser the instant the document goes hidden (tab
 * switch, app switch, phone screen lock) and — this is the commonly missed
 * half — it does NOT come back on its own when the page is visible again.
 * Nothing re-requests it unless the app notices `visibilitychange` and does
 * so itself. This controller owns exactly that: once told the lock is
 * wanted, it keeps re-acquiring across any number of hide/show cycles until
 * told otherwise.
 *
 * `onChange` is only ever invoked from genuinely async contexts (a
 * `request()` promise settling, or the sentinel's own `release` event) —
 * never synchronously from `setWanted` — so wiring it straight to a React
 * state setter from an effect never trips `react-hooks/set-state-in-effect`.
 */
export function createWakeLockController(
  nav: WakeLockNavigator,
  doc: WakeLockDocument,
  onChange: (held: boolean) => void,
): WakeLockController {
  let wanted = false;
  let sentinel: WakeLockSentinelLike | null = null;
  // Bumped on every acquire attempt so a request that resolves after we've
  // since stopped wanting the lock (deliberately disabled, or superseded by
  // a newer request from a fast disable/re-enable) can recognize itself as
  // stale and let go, instead of clobbering state a newer request already
  // owns.
  let requestToken = 0;

  function acquire() {
    const api = nav.wakeLock;
    if (!api) return; // unsupported — degrade silently, never throw
    const token = ++requestToken;
    api.request("screen").then(
      (s) => {
        if (token !== requestToken || !wanted) {
          // Stale by the time it resolved: let it go rather than hold a
          // lock nobody wants anymore.
          s.release().catch(() => {});
          return;
        }
        sentinel = s;
        // The browser can release the lock out from under us for reasons
        // that go beyond an explicit release() call — going hidden, low
        // battery, or anything else a UA decides warrants it. Its `release`
        // event is the one reliable signal for "we no longer hold it",
        // whoever/whatever initiated the release, so it's the single place
        // `held` gets cleared.
        s.addEventListener("release", () => {
          if (sentinel === s) {
            sentinel = null;
            onChange(false);
          }
        });
        onChange(true);
      },
      () => {
        // Rejections are routine, not exceptional: page not visible, low
        // battery, permissions policy. This is a screen-sleep nicety
        // failing, not a navigation failure — never throw into the caller.
        if (token === requestToken) onChange(false);
      },
    );
  }

  function release() {
    // Deliberately NOT nulling `sentinel` here: its own `release` listener
    // (registered in acquire()) is the single place that happens, so a
    // release we trigger and one the browser triggers on its own go through
    // the same path and the same stale-sentinel guard (see acquire()).
    if (sentinel) sentinel.release().catch(() => {});
  }

  function onVisibilityChange() {
    // The reacquisition half (see the doc comment above). Only act if the
    // lock is still wanted AND we don't already hold one — a
    // visibilitychange firing for an unrelated reason, or one that arrives
    // after the session ended, must not re-request.
    if (doc.visibilityState === "visible" && wanted && !sentinel) {
      acquire();
    }
  }

  doc.addEventListener("visibilitychange", onVisibilityChange);

  return {
    supported: !!nav.wakeLock,
    setWanted(next: boolean) {
      if (wanted === next) return;
      wanted = next;
      if (next) acquire();
      else release();
    },
    destroy() {
      doc.removeEventListener("visibilitychange", onVisibilityChange);
      wanted = false;
      release();
    },
  };
}

/**
 * Keeps the screen awake for exactly as long as `wanted` is true.
 *
 * Wire `wanted` to the live-nav session's lifecycle (e.g.
 * `navigating && !!nav.route` in app/page.tsx) — NOT to "a route is planned"
 * or "the map is open". Holding a wake lock while the phone is sitting on a
 * desk between trips would drain it for nothing; ADR-0012 scopes this to
 * mounted navigation specifically.
 *
 * Degrades honestly: an unsupported browser or a rejected request never
 * throws into render and never blocks navigation — it just leaves `held`
 * false, which a caller can surface if it wants to tell the truth about
 * screen-sleep risk.
 */
export function useWakeLock(wanted: boolean): WakeLockState {
  const [acquired, setAcquired] = useState(false);
  const controllerRef = useRef<WakeLockController | null>(null);

  // Created once per mount, independent of `wanted` — the controller itself
  // is long-lived and just gets told what to want, below.
  useEffect(() => {
    if (typeof navigator === "undefined" || typeof document === "undefined") {
      return;
    }
    const controller = createWakeLockController(
      navigator,
      document,
      setAcquired,
    );
    controllerRef.current = controller;
    return () => {
      controller.destroy();
      controllerRef.current = null;
    };
  }, []);

  useEffect(() => {
    controllerRef.current?.setWanted(wanted);
  }, [wanted]);

  const supported = typeof navigator !== "undefined" && !!navigator.wakeLock;

  // Derived rather than trusting `acquired` alone: the instant the caller
  // stops wanting the lock, `held` must read false immediately, without
  // waiting on the browser's async release to round-trip back through
  // `onChange`. (That release IS still awaited before the NEXT acquire can
  // happen — this only affects what this render reports.)
  return { held: wanted && acquired, supported };
}
