"""Typed access to config/config.toml — the single tunables file.

Loaded once and passed explicitly (no module-level globals). Ingestion records
a hash of the loaded config into the pack manifest so a stale pack built under
different tunables is detectable.
"""
from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
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
