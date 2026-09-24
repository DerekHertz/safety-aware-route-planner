"""Application state: config, served packs, limiters — loaded once at startup.

The served packs live in a `PackRegistry` (api/registry.py, ADR-0014). Today
it always holds exactly one:

* `SR_PACK_DIR` set: that one directory, **named by its manifest `region`**
  rather than by the directory (tests write toy packs to arbitrary dirs such
  as `tmp/"bk"`), so no directory-name check applies.
* otherwise: `<api.pack_dir>/<region.active>`, whose manifest `region` must
  equal `region.active` — a mismatch is fatal at startup.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from api.departure import pack_timezone
from api.ratelimit import (
    PerClientLimiter,
    RateLimiter,
    build_limiter,
    build_route_limiter,
    trusted_proxies,
)
from api.registry import PackRegistry, load_named_pack, load_pinned_pack
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
    # The served pack's IANA zone, from its config preset (api/departure.py).
    # Departure times are resolved into it before the traffic-profile lookup.
    pack_tz: ZoneInfo

    # Shorthands for the sole served pack. Kept because tests reach through
    # them (e.g. spying on `app_state.router.route`); they return the very
    # objects the registry holds, so a patch on one is seen by the handlers.
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
            entry = load_pinned_pack(pinned, cfg)
        else:
            entry = load_named_pack(Path(cfg["api"]["pack_dir"]), cfg.region_name, cfg)
        tz = pack_timezone(cfg, entry.pack.meta.get("region"),
                           allow_unconfigured=pinned is not None)
        return cls(cfg=cfg, registry=PackRegistry([entry]),
                   limiter=build_limiter(cfg),
                   route_limiter=build_route_limiter(cfg),
                   trusted_proxies=trusted_proxies(cfg),
                   pack_tz=tz)
