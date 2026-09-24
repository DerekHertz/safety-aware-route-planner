"""Application state: config, served packs, limiters — loaded once at startup.

The served packs live in a `PackRegistry` (api/registry.py, ADR-0014), every
one loaded eagerly here, before `/health` reports ready:

* `SR_PACK_DIR` set: exactly that one directory, **named by its manifest
  `region`** rather than by the directory (tests write toy packs to arbitrary
  dirs such as `tmp/"bk"`), so no directory-name check applies. It bypasses
  the served list below entirely.
* otherwise: `<api.pack_dir>/<name>` for each name in `served_regions(cfg)` —
  `SR_REGIONS`, else `[api] regions`, else `[region.active]`. Each manifest
  `region` must equal its directory name, each preset must carry a timezone,
  and the bboxes must be disjoint; any violation is fatal at startup.

Each entry carries its own timezone (`PackEntry.tz`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from api.ratelimit import (
    PerClientLimiter,
    RateLimiter,
    build_limiter,
    build_route_limiter,
    trusted_proxies,
)
from api.registry import (
    PackRegistry,
    load_pinned_pack,
    load_served_packs,
    served_regions,
)
from pyref.config import DEFAULT_CONFIG_PATH, Config
from pyref.engine import Router
from pyref.graph import GraphPack


@dataclass
class AppState:
    cfg: Config
    registry: PackRegistry
    # Built here rather than lazily inside the endpoint so a misconfigured
    # shared limiter (a Redis URL with no `redis` package) fails at startup,
    # where the platform sees a crash-looping container, instead of on the
    # first person to type into the search box.
    limiter: RateLimiter
    # The routing quota: one bucket per client, not one per deployment. Built
    # here for the same reason as `limiter`, and separately from it because the
    # two ration different things — see the PerClientLimiter docstring.
    route_limiter: PerClientLimiter
    # How many proxies to believe in `X-Forwarded-For`. Resolved once at
    # startup, like the CORS origins: it describes where this container is
    # deployed, and re-reading the environment per request would only invite
    # the answer to change under a live limiter.
    trusted_proxies: int

    # Shorthands for the sole served pack; they raise with two or more. The
    # handlers select per request (`registry.pack_for`), but these return the
    # very objects the registry holds, so on a one-pack deployment a patch
    # through one (a spy on `app_state.router.route`) is seen by the handlers.
    @property
    def pack(self) -> GraphPack:
        return self.registry.only().pack

    @property
    def router(self) -> Router:
        return self.registry.only().router

    @classmethod
    def load(cls) -> AppState:
        cfg = Config.load(os.environ.get("SR_CONFIG", DEFAULT_CONFIG_PATH))
        pinned = os.environ.get("SR_PACK_DIR")
        if pinned is not None:
            registry = PackRegistry([load_pinned_pack(pinned, cfg)])
        else:
            registry = load_served_packs(Path(cfg["api"]["pack_dir"]),
                                         served_regions(cfg), cfg)
        return cls(cfg=cfg, registry=registry,
                   limiter=build_limiter(cfg),
                   route_limiter=build_route_limiter(cfg),
                   trusted_proxies=trusted_proxies(cfg))
