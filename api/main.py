"""FastAPI app factory. Run with:

    uvicorn api.main:app --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import geocode, routes
from api.state import AppState


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.app_state = AppState.load()
    m = app.state.app_state.pack.meta
    print(f"[api] serving pack '{m.get('region')}' "
          f"({app.state.app_state.pack.num_edges:,} directed edges)")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Safety-Aware Route Planner", lifespan=lifespan)
    # CORS origins are configured, not wildcarded — the browser front-end
    # runs on a different port in dev
    import os
    from pyref.config import Config, DEFAULT_CONFIG_PATH
    cfg = Config.load(os.environ.get("SR_CONFIG", DEFAULT_CONFIG_PATH))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg["api"]["cors_origins"]),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(routes.router)
    app.include_router(geocode.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
