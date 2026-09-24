"""The served packs, and which one a request belongs to (ADR-0014).

A deployment serves one or more packs. Each pack covers its manifest `bbox`,
and a request is routed on the pack whose bbox contains **both** endpoints.
Keeping that a function of the coordinates is the whole design, so the
invariants that make it one are checked at startup and are fatal:

* A pack's **name** is its directory name under `api.pack_dir`, and it must
  equal the manifest's `region`.
* Served bboxes are **pairwise disjoint**.
* A null bbox (a toy pack) is allowed only when it is the **sole** served pack.
* Every served pack has a timezone (ADR-0014 decision 7), resolved once at
  load and carried on its `PackEntry`.

Which packs are served is `served_regions`: `[api] regions`, or `SR_REGIONS`,
or else `[region.active]` (step 2).

Conventions, which differ on purpose and are easy to swap:

* A **point** is `(lat, lon)` — the order `/route` and `/reroute` take their
  `origin`/`destination` in (`api.schemas.LatLon`).
* A **bbox** is the manifest's `[west, south, east, north]`, i.e.
  `(lon_min, lat_min, lon_max, lat_max)`.

Containment is **closed on every side**: a point exactly on an edge or corner
is inside. It follows that two boxes which merely *touch* share those boundary
points, so touching counts as overlap and is refused — otherwise `pack_for`
would have two answers on the shared edge. Boxes crossing the antimeridian are
not supported (west > east is rejected as malformed).
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from api.departure import pack_timezone
from pyref.config import Config
from pyref.engine import Router
from pyref.graph import GraphPack

BBox = tuple[float, float, float, float]   # west, south, east, north
Point = tuple[float, float]                # lat, lon


class PackConfigError(ValueError):
    """The served-pack configuration is invalid. Raised at startup only."""


# ------------------------------------------------------------ selection errors
@dataclass(frozen=True)
class OriginOutside:
    @property
    def detail(self) -> str:
        return "origin is outside every served region"


@dataclass(frozen=True)
class DestinationOutside:
    @property
    def detail(self) -> str:
        return "destination is outside every served region"


@dataclass(frozen=True)
class DifferentPacks:
    origin_pack: str
    destination_pack: str

    @property
    def detail(self) -> str:
        return ("origin and destination are in different regions "
                f"({self.origin_pack}, {self.destination_pack}); "
                "routing across regions is not supported")


PackSelectionError = OriginOutside | DestinationOutside | DifferentPacks


# ------------------------------------------------------------------ pure core
def bbox_contains(bbox: Sequence[float] | None, point: Point) -> bool:
    """Closed-interval containment of a `(lat, lon)` point.

    A null bbox contains every point: it is only legal for a sole served pack
    (`validate_coverage`), where there is nothing to choose between and
    snapping decides, exactly as before the registry existed.
    """
    if bbox is None:
        return True
    west, south, east, north = bbox
    lat, lon = point
    return south <= lat <= north and west <= lon <= east


def _owner(packs: Iterable[tuple[str, Sequence[float] | None]], point: Point) -> str | None:
    # With validated (disjoint) coverage at most one pack matches.
    for name, bbox in packs:
        if bbox_contains(bbox, point):
            return name
    return None


def pack_for(packs: Sequence[tuple[str, Sequence[float] | None]],
             origin: Point, destination: Point) -> str | PackSelectionError:
    """The name of the pack containing both endpoints, or why there is none.

    Pure: takes `(name, bbox)` pairs, assumed already validated. When both
    endpoints are outside every pack the origin is reported, since it is
    checked first.
    """
    o = _owner(packs, origin)
    if o is None:
        return OriginOutside()
    d = _owner(packs, destination)
    if d is None:
        return DestinationOutside()
    if o != d:
        return DifferentPacks(origin_pack=o, destination_pack=d)
    return o


def _check_bbox(name: str, bbox: Sequence[float]) -> BBox:
    if len(bbox) != 4:
        raise PackConfigError(
            f"pack {name!r} has a malformed bbox {list(bbox)!r}: "
            "expected [west, south, east, north]")
    west, south, east, north = (float(v) for v in bbox)
    if west > east or south > north:
        raise PackConfigError(
            f"pack {name!r} has a malformed bbox {list(bbox)!r}: expected "
            "[west, south, east, north] with west <= east and south <= north")
    return west, south, east, north


def _intersects(a: BBox, b: BBox) -> bool:
    # Closed boxes: a shared edge or corner is an intersection.
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def validate_coverage(packs: Sequence[tuple[str, Sequence[float] | None]]) -> None:
    """Refuse a served set on which `pack_for` would not be a function."""
    names = [n for n, _ in packs]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise PackConfigError(f"pack(s) served more than once: {', '.join(dupes)}")
    if len(packs) > 1:
        nulls = [n for n, b in packs if b is None]
        if nulls:
            raise PackConfigError(
                f"pack {nulls[0]!r} has no bbox; a pack without one can only be "
                "served on its own, since there is nothing to route by")
    boxes = [(n, _check_bbox(n, b)) for n, b in packs if b is not None]
    for i, (na, a) in enumerate(boxes):
        for nb, b in boxes[i + 1:]:
            if _intersects(a, b):
                raise PackConfigError(
                    f"served packs {na!r} and {nb!r} have bboxes that overlap "
                    f"(touching counts): {list(a)} vs {list(b)}. The same "
                    "coordinates would depend on config order; serve one or the other")


def validate_pack_name(name: str, meta: dict) -> None:
    """A pack's directory name must equal its manifest `region`."""
    region = meta.get("region")
    if region != name:
        raise PackConfigError(
            f"pack directory {name!r} holds a manifest for region {region!r}; "
            "a served pack's directory name must equal its manifest region")


# ------------------------------------------------------------ served set
def served_regions(cfg: Config, env: Mapping[str, str] | None = None) -> list[str]:
    """The names of the packs this deployment serves, in order.

    `SR_REGIONS` (comma-separated, parsed like `SR_CORS_ORIGINS`: blanks
    dropped, and an empty value counts as unset) overrides `[api] regions`;
    with neither, the served set is `[region.active]`, so a single-pack
    deployment needs no new config. `region.active` stays the *ingestion*
    target; this list is the *served* set (ADR-0014 decision 1).
    """
    env = os.environ if env is None else env
    raw = env.get("SR_REGIONS")
    if raw:
        names = [r.strip() for r in raw.split(",") if r.strip()]
        if names:
            return names
    configured = cfg["api"].get("regions")
    if configured is None:
        return [cfg.region_name]
    names = [str(r) for r in configured]
    if not names:
        raise PackConfigError(
            "[api] regions is empty; list the packs to serve, or remove the key "
            "to serve [region] active")
    return names


# ------------------------------------------------------------------- entries
@dataclass(frozen=True)
class PackEntry:
    name: str
    pack: GraphPack
    router: Router
    bbox: BBox | None
    # The pack's IANA zone (api/departure.py): departure times are resolved
    # into it before the traffic-profile lookup.
    tz: ZoneInfo


def _entry(name: str, pack: GraphPack, cfg: Config, tz: ZoneInfo) -> PackEntry:
    raw = pack.meta.get("bbox")
    bbox = None if raw is None else _check_bbox(name, raw)
    return PackEntry(name=name, pack=pack, router=Router(pack, cfg), bbox=bbox, tz=tz)


def load_named_pack(pack_root: str | Path, name: str, cfg: Config) -> PackEntry:
    """Load `<pack_root>/<name>`, refusing it if its manifest names another
    region or its preset has no timezone."""
    pack = GraphPack.load(Path(pack_root) / name)
    validate_pack_name(name, pack.meta)
    tz = pack_timezone(cfg, name, allow_unconfigured=False)
    return _entry(name, pack, cfg, tz)


def load_served_packs(pack_root: str | Path, names: Sequence[str],
                      cfg: Config) -> PackRegistry:
    """Eagerly load every served pack and validate the set (ADR-0014 decision 3)."""
    return PackRegistry([load_named_pack(pack_root, n, cfg) for n in names])


def load_pinned_pack(pack_dir: str | Path, cfg: Config) -> PackEntry:
    """Load the one directory `SR_PACK_DIR` pins, **named by its manifest**.

    `SR_PACK_DIR` bypasses naming-by-directory (ADR-0014 decision 1): the test
    suite writes packs to arbitrary directories (`tmp/"toy"`, `tmp/"bk"` for a
    pack whose manifest says `berkeley_small`), so the directory name carries
    no meaning here and there is nothing to check it against. A manifest with
    no `region` falls back to the directory name.

    A pinned pack whose region is not a config preset (the API tests' toy) is
    served in UTC; one whose region *is* a preset must carry its timezone.
    """
    p = Path(pack_dir)
    pack = GraphPack.load(p)
    region = pack.meta.get("region")
    tz = pack_timezone(cfg, region, allow_unconfigured=True)
    return _entry(str(region or p.name), pack, cfg, tz)


# ------------------------------------------------------------------ registry
class PackRegistry:
    """The validated set of served packs, in configured order."""

    def __init__(self, entries: Sequence[PackEntry]):
        if not entries:
            raise PackConfigError("a deployment must serve at least one pack")
        validate_coverage([(e.name, e.bbox) for e in entries])
        self._entries = list(entries)
        self._by_name = {e.name: e for e in self._entries}

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, name: str) -> PackEntry:
        return self._by_name[name]

    def names(self) -> list[str]:
        return [e.name for e in self._entries]

    def only(self) -> PackEntry:
        """The sole served pack; raises with two or more. `/route` and
        `/reroute` select by coordinates (`pack_for`); `/meta` and `/geocode`
        still use this until ADR-0014 steps 6 and 5."""
        if len(self._entries) != 1:
            raise RuntimeError(
                f"only() on a registry of {len(self._entries)} packs; "
                "select one with pack_for()")
        return self._entries[0]

    def pack_for(self, origin: Point, destination: Point) -> str | PackSelectionError:
        return pack_for([(e.name, e.bbox) for e in self._entries], origin, destination)
