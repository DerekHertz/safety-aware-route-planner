"""Application state: config, pack, router, limiter — loaded once at startup.

SR_PACK_DIR env var overrides the pack directory (tests point it at a toy
pack); otherwise <api.pack_dir>/<region.active> from config.
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
from pyref.config import DEFAULT_CONFIG_PATH, Config
from pyref.engine import Router
from pyref.graph import GraphPack


@dataclass
class AppState:
    cfg: Config
    pack: GraphPack
    router: Router
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

    @classmethod
    def load(cls) -> AppState:
        cfg = Config.load(os.environ.get("SR_CONFIG", DEFAULT_CONFIG_PATH))
        pinned = os.environ.get("SR_PACK_DIR")
        pack_dir = pinned
        if pack_dir is None:
            pack_dir = str(Path(cfg["api"]["pack_dir"]) / cfg.region_name)
        pack = GraphPack.load(pack_dir)
        tz = pack_timezone(cfg, pack.meta.get("region"),
                           allow_unconfigured=pinned is not None)
        return cls(cfg=cfg, pack=pack, router=Router(pack, cfg),
                   limiter=build_limiter(cfg),
                   route_limiter=build_route_limiter(cfg),
                   trusted_proxies=trusted_proxies(cfg),
                   pack_tz=tz)
