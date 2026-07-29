"""FastAPI app factory. Run with:

    uvicorn api.main:app --port 8000
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import geocode, meta, routes
from api.packs_fetch import ensure_packs
from api.state import AppState
from pyref.config import DEFAULT_CONFIG_PATH, Config


def _cors_origins(cfg: Config) -> list[str]:
    """Allowed origins, from SR_CORS_ORIGINS if set, else config.

    The deployed front-end lives on a different host from the API, and that
    host is not knowable at commit time, so it has to come from the
    environment.
    """
    env = os.environ.get("SR_CORS_ORIGINS")
    if env:
        return [o.strip() for o in env.split(",") if o.strip()]
    return list(cfg["api"]["cors_origins"])


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
    # CORS origins are configured, never wildcarded — the browser front-end
    # runs on a different origin in dev and in production.
    #
    # SR_CORS_ORIGIN_REGEX exists for hosts that mint a new domain per
    # deployment (preview builds get a generated hostname a static list can
    # never match).
    #
    # DANGER: anchor that regex to your own project and scope. A pattern like
    # `https://.*\.vercel\.app` matches every site anyone has ever deployed to
    # Vercel, i.e. it grants the whole platform access to this API.
    cfg = Config.load(os.environ.get("SR_CONFIG", DEFAULT_CONFIG_PATH))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(cfg),
        allow_origin_regex=os.environ.get("SR_CORS_ORIGIN_REGEX") or None,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(routes.router)
    app.include_router(geocode.router)
    app.include_router(meta.router)

    @app.get("/health")
    def health(request: Request):
        """Liveness AND readiness, because the platform check is the only thing
        watching.

        This used to return {"status": "ok"} unconditionally without touching
        app.state, which made it useless as a health check: a process whose
        pack never loaded would still report healthy and then 500 on every
        request. It now dereferences the state it needs and reports 503 until
        that exists — a load balancer should not send traffic here before then.

        `engine` is included deliberately. A missing sr_core downgrades to the
        pure-Python engine with only a warning, which in production shows up as
        nothing but latency; this makes it visible without reading logs.
        """
        state = getattr(request.app.state, "app_state", None)
        if state is None:
            return JSONResponse(
                {"status": "starting", "detail": "graph pack not loaded yet"},
                status_code=503,
            )
        return {
            "status": "ok",
            "packs_loaded": 1,
            "region": state.pack.meta.get("region"),
            "num_edges": state.pack.num_edges,
            "engine": state.router._impl,
        }

    return app


app = create_app()
