// The map's first framing of the coverage bbox, made padded once it can be.
//
// MapView builds the map already framed on the bbox, but WITHOUT padding:
// before the container has been measured MapLibre's canvas is a 400x300
// fallback, a mobile sheet inset does not fit inside that, and MapLibre then
// refuses the fit and leaves the camera at 0,0. So the map is re-fitted, once,
// with padding as soon as it has its real size — unless something else has
// already moved the camera, which then wins.

export interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface Size {
  width: number;
  height: number;
}

export interface Padding {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

/** Same inset the route fit uses on desktop (MapView's `fitPadding` default):
 *  clears the zoom buttons top-right and the attribution bottom-right. */
const OPEN_MARGIN_PX = 60;
/** Side and sheet-gap margin under a floating sheet — the page's mobile route
 *  fit uses the same, since a phone has no width to spare. */
const SHEET_MARGIN_PX = 24;
/** The least map, per axis, worth framing a region into. Below it the fit is
 *  pointless (and MapLibre refuses a padding that meets the canvas). */
const MIN_USABLE_PX = 100;

/** How far `overlay` covers the bottom of `map`, in CSS pixels: the visible
 *  height of a sheet floating over it. Zero for an element beside the map (the
 *  desktop sidebar column) or below it, or for no element at all. */
export function bottomOverlap(map: Rect, overlay: Rect | null): number {
  if (!overlay) return 0;
  const horizontal =
    Math.min(map.right, overlay.right) - Math.max(map.left, overlay.left);
  if (horizontal <= 0) return 0;
  const covered = map.bottom - Math.max(overlay.top, map.top);
  return Math.max(0, Math.min(covered, map.bottom - map.top));
}

/** Padding for the first framing of a map of `viewport` size whose bottom
 *  `sheetHeight` pixels are covered by a floating sheet (0 when none). Null
 *  when the map left over would be too small to frame anything into — the
 *  sheet is expanded, or the canvas is not sized yet. */
export function initialFitPadding(
  viewport: Size,
  sheetHeight: number,
): Padding | null {
  const side = sheetHeight > 0 ? SHEET_MARGIN_PX : OPEN_MARGIN_PX;
  const padding = {
    top: OPEN_MARGIN_PX,
    bottom: sheetHeight + side,
    left: side,
    right: side,
  };
  const usableW = viewport.width - padding.left - padding.right;
  const usableH = viewport.height - padding.top - padding.bottom;
  if (usableW < MIN_USABLE_PX || usableH < MIN_USABLE_PX) return null;
  return padding;
}

/** Whether to make the padded re-fit now. Only once; only with a bbox to
 *  frame; only once the canvas has its real size; and never over a camera
 *  that a gesture, a GPS fix or a route fit has already moved. */
export function shouldRefitInitial(s: {
  done: boolean;
  hasBounds: boolean;
  cameraMoved: boolean;
  sized: boolean;
}): boolean {
  return !s.done && s.hasBounds && !s.cameraMoved && s.sized;
}
