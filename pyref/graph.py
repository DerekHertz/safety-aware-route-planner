"""GraphPack: the compact binary graph format shared by ingestion, pyref and
the C++ core.

On-disk layout: a directory of flat little-endian raw arrays (one file per
array, extension encodes dtype: .f64 / .i64 / .i32 / .u8) plus manifest.json
with counts, dtypes/shapes, enum tables and provenance. No .npz — the format
stays trivially readable from C++ (fread) and inspectable with np.fromfile.

Dtype policy (parity over size): floats are ALWAYS float64, indices int32
(with < 2**31 asserts at build time), enums/flags uint8. Every numpy array is
created with an explicit dtype — never rely on platform default ints
(int32 on Windows numpy<2, int64 elsewhere).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields
from enum import IntEnum
from pathlib import Path

import numpy as np

PACK_FORMAT_VERSION = 1


class Control(IntEnum):
    NONE = 0
    STOP_2WAY = 1
    STOP_4WAY = 2
    SIGNAL_PERMISSIVE = 3
    SIGNAL_PROTECTED = 4
    ROUNDABOUT = 5


class Maneuver(IntEnum):
    STRAIGHT = 0
    LEFT = 1
    RIGHT = 2
    UTURN = 3


class RoadClass(IntEnum):
    motorway = 0
    trunk = 1
    primary = 2
    secondary = 3
    tertiary = 4
    residential = 5
    unclassified = 6
    service = 7
    living_street = 8
    motorway_link = 9
    trunk_link = 10
    primary_link = 11
    secondary_link = 12
    tertiary_link = 13


# Rank for control inference & "major vs minor" comparisons (lower = more major).
# Links share their parent's rank.
ROAD_CLASS_RANK = {
    RoadClass.motorway: 0, RoadClass.motorway_link: 0,
    RoadClass.trunk: 1, RoadClass.trunk_link: 1,
    RoadClass.primary: 2, RoadClass.primary_link: 2,
    RoadClass.secondary: 3, RoadClass.secondary_link: 3,
    RoadClass.tertiary: 4, RoadClass.tertiary_link: 4,
    RoadClass.unclassified: 5,
    RoadClass.residential: 6,
    RoadClass.living_street: 7,
    RoadClass.service: 8,
}

_EXT_TO_DTYPE = {"f64": "<f8", "i64": "<i8", "i32": "<i4", "u8": "|u1"}
_DTYPE_TO_EXT = {v: k for k, v in _EXT_TO_DTYPE.items()}


@dataclass
class GraphPack:
    """All arrays of one region. Field names match on-disk file stems."""

    # nodes [N]
    node_lat: np.ndarray          # f64
    node_lon: np.ndarray          # f64
    node_osmid: np.ndarray        # i64
    node_control: np.ndarray      # u8  (Control enum, raw node-level)

    # directed edges [E] + node CSR
    node_out_ptr: np.ndarray      # i32 [N+1], tail-node CSR into edge arrays
    edge_tail: np.ndarray         # i32
    edge_head: np.ndarray         # i32
    edge_length_m: np.ndarray     # f64
    edge_speed_mps: np.ndarray    # f64 (defaults already applied)
    edge_lanes: np.ndarray        # f64 (per direction, defaults applied)
    edge_median: np.ndarray       # u8
    edge_road_class: np.ndarray   # u8  (RoadClass enum)
    edge_reverse: np.ndarray      # i32 (opposite-direction edge, -1 if one-way)
    edge_bearing_in: np.ndarray   # f64 (deg, first geometry segment)
    edge_bearing_out: np.ndarray  # f64 (deg, last geometry segment)
    edge_approach_control: np.ndarray  # u8 (control governing arrival at head)
    edge_must_stop: np.ndarray    # u8 (this approach must stop)
    edge_osm_way: np.ndarray      # i64 (debug)

    # geometry pool (rendering/snapping only — never in the hot loop)
    edge_geom_ptr: np.ndarray     # i32 [E+1]
    geom_lat: np.ndarray          # f64 [P]
    geom_lon: np.ndarray          # f64 [P]

    # turn table [T], CSR keyed by incoming edge
    turn_ptr: np.ndarray          # i32 [E+1]
    turn_out_edge: np.ndarray     # i32
    turn_maneuver: np.ndarray     # u8  (Maneuver enum)
    turn_angle_deg: np.ndarray    # f64 (signed bearing delta, debug/tuning)
    turn_allowed: np.ndarray      # u8

    # metadata (not arrays)
    meta: dict

    # ------------------------------------------------------------------ sizes
    @property
    def num_nodes(self) -> int:
        return len(self.node_lat)

    @property
    def num_edges(self) -> int:
        return len(self.edge_head)

    @property
    def num_turns(self) -> int:
        return len(self.turn_out_edge)

    # Derived at load: incoming edge id per turn row (CSR expansion).
    _turn_in_edge: np.ndarray | None = None

    @property
    def turn_in_edge(self) -> np.ndarray:
        if self._turn_in_edge is None:
            counts = np.diff(self.turn_ptr)
            self._turn_in_edge = np.repeat(
                np.arange(self.num_edges, dtype=np.int32), counts
            )
        return self._turn_in_edge

    # ------------------------------------------------------------------- I/O
    @staticmethod
    def array_fields() -> list[str]:
        return [f.name for f in fields(GraphPack)
                if f.name not in ("meta", "_turn_in_edge")]

    def validate(self) -> None:
        n, e, t = self.num_nodes, self.num_edges, self.num_turns
        assert len(self.node_out_ptr) == n + 1
        assert len(self.turn_ptr) == e + 1
        assert len(self.edge_geom_ptr) == e + 1
        assert self.node_out_ptr[-1] == e
        assert self.turn_ptr[-1] == t
        assert self.edge_geom_ptr[-1] == len(self.geom_lat) == len(self.geom_lon)
        assert e < 2**31 and t < 2**31
        for name in self.array_fields():
            arr = getattr(self, name)
            assert arr.dtype.str in _DTYPE_TO_EXT, \
                f"{name}: unexpected dtype {arr.dtype} — use explicit f64/i64/i32/u8"
        if t:
            assert self.turn_out_edge.min() >= 0 and self.turn_out_edge.max() < e
        # every turn leaves from the head of its in-edge
        ok = self.edge_tail[self.turn_out_edge] == self.edge_head[self.turn_in_edge]
        assert bool(np.all(ok)), "turn table topology mismatch"

    def write(self, pack_dir: str | Path) -> None:
        pack_dir = Path(pack_dir)
        pack_dir.mkdir(parents=True, exist_ok=True)
        self.validate()
        arrays_manifest = {}
        for name in self.array_fields():
            arr = np.ascontiguousarray(getattr(self, name))
            ext = _DTYPE_TO_EXT[arr.dtype.str]
            arr.tofile(pack_dir / f"{name}.{ext}")
            arrays_manifest[name] = {"file": f"{name}.{ext}",
                                     "dtype": _EXT_TO_DTYPE[ext],
                                     "shape": [int(arr.shape[0])]}
        manifest = {
            "format_version": PACK_FORMAT_VERSION,
            "endianness": "little",
            "counts": {"num_nodes": self.num_nodes, "num_edges": self.num_edges,
                       "num_turns": self.num_turns,
                       "num_geom_points": int(len(self.geom_lat))},
            "arrays": arrays_manifest,
            "enums": {
                "control": {c.name: int(c) for c in Control},
                "maneuver": {m.name: int(m) for m in Maneuver},
                "road_class": {r.name: int(r) for r in RoadClass},
            },
            **self.meta,
        }
        (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    @classmethod
    def load(cls, pack_dir: str | Path) -> "GraphPack":
        pack_dir = Path(pack_dir)
        manifest = json.loads((pack_dir / "manifest.json").read_text())
        if manifest["format_version"] != PACK_FORMAT_VERSION:
            raise ValueError(f"pack format {manifest['format_version']} != "
                             f"expected {PACK_FORMAT_VERSION}")
        kwargs = {}
        for name in cls.array_fields():
            spec = manifest["arrays"][name]
            arr = np.fromfile(pack_dir / spec["file"], dtype=np.dtype(spec["dtype"]))
            if len(arr) != spec["shape"][0]:
                raise ValueError(f"{name}: expected {spec['shape'][0]} items, "
                                 f"got {len(arr)}")
            kwargs[name] = arr
        meta = {k: v for k, v in manifest.items() if k not in ("arrays", "enums")}
        pack = cls(**kwargs, meta=meta)
        pack.validate()
        return pack
