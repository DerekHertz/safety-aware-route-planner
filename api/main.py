"""FastAPI app factory. Run with:

    uvicorn api.main:app --port 8000
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import geocode, meta, routes
from api.packs_fetch import ensure_packs
from api.state import AppState
from pyref.config import DEFAULT_CONFIG_PATH, Config


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A fresh container has no packs (data/ is gitignored and building one hits
    # Overpass), so pull anything missing before the router needs it. No-ops
    # when the pack is already on disk, which is the local-dev and CI case.
    #
    # SR_PACK_DIR pins one explicit pack directory — the test suite points it at
    # a generated toy pack — so when it is set we must not touch the network at
    # all, or every API test would depend on a bucket.
    if os.environ.get("SR_PACK_DIR") is None:
        cfg = Config.load(os.environ.get("SR_CONFIG", DEFAULT_CONFIG_PATH))
        fetched = ensure_packs([cfg.region_name], cfg["api"]["pack_dir"])
        if fetched:
            print(f"[api] fetched pack(s) from object storage: {', '.join(fetched)}")

    app.state.app_state = AppState.load()
    m = app.state.app_state.pack.meta
    print(f"[api] serving pack '{m.get('region')}' "
          f"({app.state.app_state.pack.num_edges:,} directed edges)")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Safety-Aware Route Planner", lifespan=lifespan)
    # CORS origins are configured, not wildcarded — the browser front-end
    # runs on a different port in dev
    cfg = Config.load(os.environ.get("SR_CONFIG", DEFAULT_CONFIG_PATH))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg["api"]["cors_origins"]),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(routes.router)
    app.include_router(geocode.router)
    app.include_router(meta.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
