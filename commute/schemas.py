"""Wire models for the trip-trace ingest (ADR-0018).

Mirrored by hand in `web/lib/types.ts`. The `schema-sync` CI job holds the two
together: `scripts/dump_openapi.py` merges this service's schemas into the route
service's OpenAPI document, and `web/scripts/check-schema-sync.mjs` maps them in
`PAIRS`.

Conventions a client must follow, all enforced here:

* **Every instant is `EpochMs`**: an integer count of milliseconds since the
  Unix epoch, UTC. That is the unit of `GeolocationPosition.timestamp`; round
  it, and never send seconds or an ISO string.
* Coordinates are WGS84 degrees.
* Unknown fields are ignored and not stored, so a newer client can talk to an
  older server.
* Every error body is `{"detail": "<string>"}`, 422 included (see `app.py`).
"""
from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EPOCH_MS_MIN = 946_684_800_000     # 2000-01-01T00:00:00Z
EPOCH_MS_MAX = 4_102_444_800_000   # 2100-01-01T00:00:00Z

# A chunk is flushed about every 2 minutes (ADR-0017), ~120 fixes at 1 Hz. The
# ceiling leaves room for a faster GPS and a backlog, and a client with more
# than this splits it across seqs.
MAX_FIXES_PER_CHUNK = 600
# A reroute is self-limited to about one per 18 s, so ~7 in a 2-minute chunk.
MAX_ARTIFACTS_PER_CHUNK = 16
MAX_SEQ = 99_999
# Checked before the body is parsed. A full chunk of fixes is ~70 KB and a
# metro-scale artifact ~100 KB, so this is generous, not tight.
MAX_BODY_BYTES = 1_048_576
MAX_ETA_S = 172_800.0              # 48 h

EpochMs = Annotated[int, Field(
    strict=True, ge=EPOCH_MS_MIN, le=EPOCH_MS_MAX,
    description="Milliseconds since the Unix epoch, UTC, as an integer.")]


class _Wire(BaseModel):
    """Request models. Python's JSON parser accepts `NaN` and `Infinity`
    literals; nothing non-finite is stored."""
    model_config = ConfigDict(allow_inf_nan=False)


def _all_finite(value: Any) -> Any:
    """Reject non-finite numbers anywhere inside an opaque JSON value.
    Iterative, so a deeply nested value cannot exhaust the stack."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("contains a non-finite number")
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return value


class TraceFix(_Wire):
    """One GPS fix, as `navigator.geolocation` reported it.

    `speed_mps` and `heading_deg` are null when the device does not report
    them (the browser gives null, or NaN for a stationary heading).
    """
    t: EpochMs
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    speed_mps: Annotated[float, Field(ge=0)] | None
    accuracy_m: float = Field(ge=0)
    heading_deg: Annotated[float, Field(ge=0, le=360)] | None


class FollowedArtifact(_Wire):
    """A route artifact the client began following at `effective_at`: the one
    navigation started with, or a reroute's replacement.

    Stored opaquely. The client must clip it before upload: its geometry runs
    from the trip's origin to its destination, and ADR-0017's 300 m trim covers
    those endpoints (see ADR-0018).
    """
    effective_at: EpochMs
    artifact: dict[str, Any]

    @field_validator("artifact")
    @classmethod
    def _finite(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _all_finite(value)


class TraceChunk(_Wire):
    """Body of `PUT /v1/trips/{trip_id}/chunks/{seq}`.

    `fixes` are strictly increasing in `t` and `artifacts` in `effective_at`,
    within the chunk. A chunk carries at least one of either. Order ACROSS
    chunks is not checked: they may arrive in any order.
    """
    fixes: list[TraceFix] = Field(max_length=MAX_FIXES_PER_CHUNK)
    artifacts: list[FollowedArtifact] = Field(
        default_factory=list, max_length=MAX_ARTIFACTS_PER_CHUNK)

    @model_validator(mode="after")
    def _ordered_and_not_empty(self) -> TraceChunk:
        if not self.fixes and not self.artifacts:
            raise ValueError("a chunk must carry at least one fix or artifact")
        for i in range(1, len(self.fixes)):
            if self.fixes[i].t <= self.fixes[i - 1].t:
                raise ValueError(f"fixes[{i}].t must be later than fixes[{i - 1}].t")
        for i in range(1, len(self.artifacts)):
            if self.artifacts[i].effective_at <= self.artifacts[i - 1].effective_at:
                raise ValueError(f"artifacts[{i}].effective_at must be later than "
                                 f"artifacts[{i - 1}].effective_at")
        return self


class ChunkReceipt(BaseModel):
    """`created` is false for a replay of a chunk already held."""
    trip_id: str
    seq: int
    created: bool


class EtaPrediction(_Wire):
    """What the route artifact being followed when the trip ended predicted.

    `effective_at` is when that artifact took effect (navigation start, or the
    reroute that produced it), so its predicted arrival is
    `effective_at + eta_s * 1000`.
    """
    effective_at: EpochMs
    eta_s: float = Field(ge=0, le=MAX_ETA_S)               # the artifact's `eta_s`
    level: str = Field(min_length=1, max_length=32)         # its `preference.level`
    # its `preference.traffic_basis.profile_version`; null for a v1 artifact
    profile_version: Annotated[str, Field(max_length=128)] | None


class TripEnd(_Wire):
    """Body of `POST /v1/trips/{trip_id}/end`: one row of the
    predicted-versus-actual ETA log (ADR-0011). Times only, never a place.

    `arrived` is true when navigation detected arrival, false when it was
    stopped or abandoned first. `prediction` is null only if no artifact was
    being followed.
    """
    ended_at: EpochMs
    arrived: bool = Field(strict=True)
    prediction: EtaPrediction | None

    @model_validator(mode="after")
    def _prediction_precedes_end(self) -> TripEnd:
        if self.prediction is not None and self.prediction.effective_at > self.ended_at:
            raise ValueError("prediction.effective_at must not be later than ended_at")
        return self


class TripEndReceipt(BaseModel):
    """`created` is false for a replay of an end already held."""
    trip_id: str
    created: bool


class TesterInfo(BaseModel):
    """`GET /v1/me`: lets a client check a tester token at opt-in."""
    label: str
