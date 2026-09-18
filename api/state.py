"""Application state: config, pack, router, limiter — loaded once at startup.

SR_PACK_DIR env var overrides the pack directory (tests point it at a toy
pack); otherwise <api.pack_dir>/<region.active> from config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from api.ratelimit import RateLimiter, build_limiter
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

    @classmethod
    def load(cls) -> AppState:
        cfg = Config.load(os.environ.get("SR_CONFIG", DEFAULT_CONFIG_PATH))
        pack_dir = os.environ.get("SR_PACK_DIR")
        if pack_dir is None:
            pack_dir = str(Path(cfg["api"]["pack_dir"]) / cfg.region_name)
        pack = GraphPack.load(pack_dir)
        return cls(cfg=cfg, pack=pack, router=Router(pack, cfg),
                   limiter=build_limiter(cfg))
