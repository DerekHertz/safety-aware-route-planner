"""Generate the PWA icon set.

    python scripts/make_icons.py

Committed as a generator rather than as opaque PNGs so the icons can be
restyled by editing colours here and re-running, and so nobody has to wonder
how a binary in the repo was produced. Pure stdlib — no Pillow, no build step.

The artwork is a route making a protected left: the whole point of the app.
Rendered from signed distance fields with analytic anti-aliasing, which is
both far faster than supersampling and sharper at 192 px.

Outputs (web/public/):
  icon-192.png, icon-512.png   — standard PWA icons
  icon-512-maskable.png        — artwork inside the 80% safe zone, so Android
                                 can crop it to any mask without clipping
  ../app/apple-icon.png        — 180px; Next emits the apple-touch-icon link
  ../app/icon.png              — 512px; Next emits the favicon link
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

BG = (15, 23, 42)  # slate-900, matches the PWA theme_color
ROUTE = (34, 197, 94)  # green-500 — the "safe" tier colour
PIN = (248, 250, 252)  # slate-50


def _write_png(path: Path, w: int, h: int, pixels: bytearray) -> None:
    """Minimal RGBA PNG writer (filter type 0 on every scanline)."""
    raw = b"".join(
        b"\x00" + bytes(pixels[y * w * 4 : (y + 1) * w * 4]) for y in range(h)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    denom = vx * vx + vy * vy
    t = 0.0 if denom == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
    dx, dy = wx - t * vx, wy - t * vy
    return math.hypot(dx, dy)


def _mix(dst: tuple[int, int, int], src: tuple[int, int, int], a: float):
    return tuple(round(d + (s - d) * a) for d, s in zip(dst, src, strict=True))


def render(size: int, *, maskable: bool) -> bytearray:
    px = bytearray(size * size * 4)
    # Maskable icons must keep their content inside the middle 80%, because
    # the platform may crop to a circle, squircle, or anything else.
    scale = 0.62 if maskable else 0.78
    cx = cy = size / 2
    aa = 1.0  # anti-alias width, in pixels

    # Route: up the left, right-angle turn, out to the right — an L.
    ax, ay = cx - 0.34 * size * scale, cy + 0.40 * size * scale
    bx, by = cx - 0.34 * size * scale, cy - 0.16 * size * scale
    ex, ey = cx + 0.38 * size * scale, cy - 0.16 * size * scale
    stroke = 0.115 * size * scale
    dot_r = 0.135 * size * scale

    corner = 0.22 * size  # background rounding
    half = size / 2

    for y in range(size):
        for x in range(size):
            fx, fy = x + 0.5, y + 0.5

            # Rounded-square background.
            qx = abs(fx - half) - (half - corner)
            qy = abs(fy - half) - (half - corner)
            d_bg = (
                math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - corner
            )
            bg_a = min(1.0, max(0.0, 0.5 - d_bg / aa))
            if bg_a <= 0.0:
                continue

            colour = BG

            d_route = min(
                _seg_dist(fx, fy, ax, ay, bx, by),
                _seg_dist(fx, fy, bx, by, ex, ey),
            )
            route_a = min(1.0, max(0.0, 0.5 - (d_route - stroke) / aa))
            if route_a > 0.0:
                colour = _mix(colour, ROUTE, route_a)

            # Destination dot at the end of the turn.
            d_dot = math.hypot(fx - ex, fy - ey) - dot_r
            dot_a = min(1.0, max(0.0, 0.5 - d_dot / aa))
            if dot_a > 0.0:
                colour = _mix(colour, PIN, dot_a)

            i = (y * size + x) * 4
            px[i : i + 3] = bytes(colour)
            px[i + 3] = round(255 * bg_a)
    return px


def main() -> int:
    web = Path(__file__).resolve().parent.parent / "web"
    targets = [
        (web / "public" / "icon-192.png", 192, False),
        (web / "public" / "icon-512.png", 512, False),
        (web / "public" / "icon-512-maskable.png", 512, True),
        (web / "app" / "apple-icon.png", 180, False),
        (web / "app" / "icon.png", 512, False),
    ]
    for path, size, maskable in targets:
        _write_png(path, size, size, render(size, maskable=maskable))
        print(f"  {path.relative_to(web.parent)}  {size}x{size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
