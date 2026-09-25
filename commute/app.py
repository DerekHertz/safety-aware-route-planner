"""The commute planner's HTTP surface: trip-trace ingest (ADR-0018).

    uvicorn commute.app:app --port 8100

    GET  /health                           liveness; no token
    GET  /v1/me                            which tester token this is
    PUT  /v1/trips/{trip_id}/chunks/{seq}  store one chunk of a trip trace
    POST /v1/trips/{trip_id}/end           end a trip; one ETA log row

Everything under `/v1` needs `Authorization: Bearer <tester token>`. Both
writes are idempotent: an identical resend is a 200 no-op and a different body
under the same key is a 409, so a client may retry anything it is unsure about.
Shapes and limits are in `commute/schemas.py`; the client's obligations
(the 300 m trim above all) are in ADR-0018.

**No coordinate is ever logged.** Nothing here logs a request body, and a 422
names the offending field without echoing its value. Keep it that way: the
store holds location history of real people.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Sequence
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from commute import tokens
from commute.schemas import (
    MAX_BODY_BYTES,
    MAX_SEQ,
    ChunkReceipt,
    TesterInfo,
    TraceChunk,
    TripEnd,
    TripEndReceipt,
)
from commute.store import Clock, Outcome, TraceStore, resolve_db_path, wall_clock_ms

router = APIRouter()

_bearer = HTTPBearer(
    auto_error=False,
    description="A tester token, minted by `python -m commute.tokens issue`.")


def _store(request: Request) -> TraceStore:
    store: TraceStore = request.app.state.store
    return store


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(401, detail, headers={"WWW-Authenticate": "Bearer"})


def current_tester(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> tokens.Tester:
    if credentials is None:
        raise _unauthorized("missing bearer tester token")
    tester = tokens.authenticate(_store(request), credentials.credentials)
    if tester is None:
        raise _unauthorized("unknown or revoked tester token")
    return tester


Tester = Annotated[tokens.Tester, Depends(current_tester)]
TripId = Annotated[uuid.UUID, Path(description="A client-generated UUID, one per trip.")]
Seq = Annotated[int, Path(ge=0, le=MAX_SEQ,
                          description="The chunk's position in its trip, from 0.")]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing, unknown or revoked tester token."},
    404: {"description": "The trip belongs to another tester token."},
    409: {"description": "Different content is already stored under this key."},
    413: {"description": f"Body over {MAX_BODY_BYTES} bytes."},
}


@router.get("/health", response_model=None)
def health(request: Request) -> JSONResponse | dict[str, str]:
    try:
        _store(request).ping()
    except sqlite3.Error:
        return JSONResponse({"status": "unavailable", "detail": "datastore unreachable"},
                            status_code=503)
    return {"status": "ok"}


@router.get("/v1/me", response_model=TesterInfo, responses={401: _ERRORS[401]})
def me(tester: Tester) -> TesterInfo:
    return TesterInfo(label=tester.label)


@router.put(
    "/v1/trips/{trip_id}/chunks/{seq}", response_model=ChunkReceipt, status_code=201,
    responses={200: {"model": ChunkReceipt,
                     "description": "An identical replay; nothing was stored."},
               **_ERRORS})
def put_chunk(trip_id: TripId, seq: Seq, body: TraceChunk, request: Request,
              response: Response, tester: Tester) -> ChunkReceipt:
    # sync on purpose: sqlite3 blocks, so FastAPI runs this in its threadpool
    trip = str(trip_id)
    outcome = _store(request).put_chunk(tester.id, trip, seq, body)
    if outcome is Outcome.NOT_YOURS:
        raise HTTPException(404, "no such trip for this tester token")
    if outcome is Outcome.CONFLICT:
        raise HTTPException(409, f"chunk {seq} of this trip is already stored "
                                 "with different content")
    if outcome is Outcome.REPLAYED:
        response.status_code = 200
    return ChunkReceipt(trip_id=trip, seq=seq, created=outcome is Outcome.CREATED)


@router.post(
    "/v1/trips/{trip_id}/end", response_model=TripEndReceipt, status_code=201,
    responses={200: {"model": TripEndReceipt,
                     "description": "An identical replay; nothing was stored."},
               **_ERRORS})
def end_trip(trip_id: TripId, body: TripEnd, request: Request, response: Response,
             tester: Tester) -> TripEndReceipt:
    trip = str(trip_id)
    outcome = _store(request).end_trip(tester.id, trip, body)
    if outcome is Outcome.NOT_YOURS:
        raise HTTPException(404, "no such trip for this tester token")
    if outcome is Outcome.CONFLICT:
        raise HTTPException(409, "this trip already ended with different content")
    if outcome is Outcome.REPLAYED:
        response.status_code = 200
    return TripEndReceipt(trip_id=trip, created=outcome is Outcome.CREATED)


# --- errors -----------------------------------------------------------------


def describe_errors(errors: Sequence[Any], limit: int = 3) -> str:
    """FastAPI's 422 body lists each error with the offending `input`; for a
    fix, that input is a coordinate. This keeps the field path and message
    only, as one string, so every error from this service is `{"detail": str}`
    (the reference client reads `detail` as a string: `web/lib/api.ts`)."""
    parts = []
    for err in errors[:limit]:
        path = ""
        for part in err.get("loc", ()):
            if part == "body":
                continue
            path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
        parts.append(f"{path or 'body'}: {err.get('msg', 'invalid')}")
    if len(errors) > limit:
        parts.append(f"and {len(errors) - limit} more")
    return "; ".join(parts)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    return JSONResponse({"detail": describe_errors(errors) or "invalid request"},
                        status_code=422)


class BodySizeLimit:
    """Refuse a body over `max_bytes` with 413 before anything parses it.

    Checks `Content-Length` when there is one. Without it (chunked transfer),
    it reads the body itself, counting, and hands the app a replay of it; the
    limit keeps that buffer small.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None:
            if declared > self.max_bytes:
                await self._too_large(scope, receive, send)
            else:
                await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                return                      # the client went away
            body += message.get("body", b"")
            if len(body) > self.max_bytes:
                await self._too_large(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            {"detail": f"request body exceeds {self.max_bytes} bytes"}, status_code=413)
        await response(scope, receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


# --- the app ------------------------------------------------------------------


def _cors_origins() -> list[str]:
    """`SR_COMMUTE_CORS_ORIGINS`, comma-separated; the local web dev server by
    default. Never a wildcard: requests carry a credential."""
    env = os.environ.get("SR_COMMUTE_CORS_ORIGINS")
    if env:
        return [o.strip() for o in env.split(",") if o.strip()]
    return ["http://localhost:3000"]


def create_app(db_path: str | os.PathLike[str] | None = None,
               clock: Clock | None = None) -> FastAPI:
    """Builds the app without touching disk; the database is created at
    startup. `db_path` defaults to `SR_COMMUTE_DB`, then
    `data/commute/commute.sqlite3`."""
    store = TraceStore(resolve_db_path(db_path), clock or wall_clock_ms)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.init_schema()
        yield

    app = FastAPI(title="Commute planner: trip-trace ingest", version="1",
                  lifespan=lifespan)
    app.state.store = store
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.include_router(router)
    # Added last = outermost, so a 413 still carries CORS headers the
    # browser needs to read it.
    app.add_middleware(BodySizeLimit, max_bytes=MAX_BODY_BYTES)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_origin_regex=os.environ.get("SR_COMMUTE_CORS_ORIGIN_REGEX") or None,
        allow_methods=["GET", "PUT", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    return app


app = create_app()
