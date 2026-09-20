"""Typed access to config/config.toml — the single tunables file.

Loaded once and passed explicitly (no module-level globals). Ingestion records
a hash of the loaded config into the pack manifest so a stale pack built under
different tunables is detectable.
"""
from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.toml"


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    source_path: Path

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
        path = Path(path)
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        return cls(raw=raw, source_path=path)

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    # --- region -------------------------------------------------------------
    @property
    def region_name(self) -> str:
        return self.raw["region"]["active"]

    def bbox(self, region: str | None = None) -> tuple[float, float, float, float]:
        """(west, south, east, north) for the given (or active) preset."""
        name = region or self.region_name
        return tuple(self.raw["region"]["presets"][name]["bbox"])

    @property
    def network_type(self) -> str:
        return self.raw["region"]["network_type"]

    # --- provenance ---------------------------------------------------------
    def content_hash(self) -> str:
        """Stable hash of the config file contents (for the pack manifest)."""
        return hashlib.sha256(self.source_path.read_bytes()).hexdigest()[:16]

    @cached_property
    def sim_profile_version(self) -> str:
        """Content hash of the `[sim]` table — the synthetic traffic profiles,
        base volumes and class groups that `sim.profiles.multipliers_at` reads.
        Shipped inside every artifact's `traffic_basis` (ADR-0004 v2).

        Scoped to `[sim]`, NOT `content_hash()`: a rate-limit or CORS edit does
        not change the traffic inputs, and a version that moved on every config
        commit would be noise in exactly the artifact diff it exists to serve
        (ADR-0011's disruption detection).

        Hashed off the PARSED table rather than the file text so reformatting
        and comment edits are invisible while a changed number is not.
        `sort_keys` makes it independent of table order; floats round-trip
        through `repr`, which is exact for f64.

        `cached_property` because this is on the per-request path (every
        snapshot mints a basis) while the config is immutable for the life of
        the process. It writes straight into `__dict__`, which a frozen
        dataclass permits — `frozen` only intercepts `__setattr__`.
        """
        blob = json.dumps(self.raw["sim"], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
