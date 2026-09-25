"""Two toy metros served side by side, for the ADR-0014 multi-pack tests.

The packs are toys built by the same `GraphBuilder` the API tests use, under
`tmp_path`, from a config that adds presets with disjoint bboxes. The config
reaches the app through the existing `SR_CONFIG` hook, with `[api] pack_dir`
rewritten to `tmp_path/packs`; `SR_PACK_DIR` must stay unset, since it pins
one pack and bypasses the served list entirely.
"""
from __future__ import annotations

import re
from pathlib import Path

from pyref.config import DEFAULT_CONFIG_PATH, Config
from tests.helpers.toy_graphs import GraphBuilder

# Disjoint toy metros, [west, south, east, north], nowhere near a real preset.
A_BBOX = [10.0, 10.0, 10.02, 10.01]
B_BBOX = [20.0, 20.0, 20.02, 20.01]
A_OVERLAPPING_B = [19.99, 19.99, 20.01, 20.005]

TWO = {"metro_a": (A_BBOX, "America/Los_Angeles"),
       "metro_b": (B_BBOX, "America/New_York")}


def _preset(name: str, bbox: list[float], tz: str | None) -> str:
    lines = [f"[region.presets.{name}]", f"bbox = {bbox!r}"]
    if tz is not None:
        lines.append(f'timezone = "{tz}"')
    return "\n".join(lines) + "\n"


def write_config(tmp_path: Path, *, presets: dict[str, tuple[list[float], str | None]],
                 regions: list[str] | None = None, active: str | None = None,
                 pack_root: Path | None = None) -> Path:
    """The shipped config, with `pack_dir` -> `pack_root` (default
    `tmp_path/packs`), extra presets appended, and optionally `[api] regions` /
    `region.active` set.

    The shipped `[api] regions` (the real deployment's served set) is always
    removed first: it names real metros these toys do not have, and leaving it
    would both shadow `active` and collide with a `regions` written here."""
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^regions = .*\n", "", text)
    root = (pack_root if pack_root is not None else tmp_path / "packs").as_posix()
    old_dir = 'pack_dir = "data/packs"'
    assert old_dir in text
    text = text.replace(old_dir, f"pack_dir = '{root}'")
    if regions is not None:
        assert "\n[api]\n" in text
        text = text.replace("\n[api]\n", f"\n[api]\nregions = {regions!r}\n", 1)
    if active is not None:
        old_active = 'active = "berkeley_oakland"'
        assert old_active in text
        text = text.replace(old_active, f'active = "{active}"')
    text += "\n" + "\n".join(_preset(n, b, tz) for n, (b, tz) in presets.items())
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def toy_endpoints(bbox: list[float]) -> tuple[tuple[float, float], tuple[float, float]]:
    """The two nodes `build_toy` places inside `bbox`, as `(lat, lon)` points:
    one a third of the way across, one two thirds."""
    west, south, east, north = bbox
    n0 = (south + (north - south) / 3, west + (east - west) / 3)
    n1 = (south + 2 * (north - south) / 3, west + 2 * (east - west) / 3)
    return n0, n1


def build_toy(cfg: Config, name: str, root: Path) -> None:
    """A two-node pack whose manifest `region` is `name` and whose bbox is
    stamped from `cfg`'s preset, written to `root/name`."""
    (lat0, lon0), (lat1, lon1) = toy_endpoints(list(cfg.bbox(name)))
    b = GraphBuilder(cfg)
    n0 = b.node(lat0, lon0)
    n1 = b.node(lat1, lon1)
    b.edge(n0, n1, length_m=200.0)
    b.build(region=name).write(root / name)
