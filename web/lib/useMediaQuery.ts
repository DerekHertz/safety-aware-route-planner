"use client";

import { useCallback, useSyncExternalStore } from "react";

/**
 * Subscribe to a CSS media query.
 *
 * useSyncExternalStore rather than useState + useEffect: matchMedia IS an
 * external store, so this is what the hook is for. It also avoids a
 * synchronous setState inside an effect, which causes a second render pass on
 * every mount and is flagged by the React Compiler lint rules.
 *
 * The server snapshot is `false`, so the desktop layout renders on the server
 * and the query resolves on hydration. That matches the CSS, which is
 * mobile-conditional via a max-width query.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );

  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** Matches the `max-width: 767px` breakpoint the bottom sheet uses in CSS. */
export const COMPACT_QUERY = "(max-width: 767px)";

/** Keep in sync with --sheet-peek in globals.css. */
export const SHEET_PEEK_PX = 158;
