"""Contract lock for the trip-trace ingest (ADR-0018).

The client recorder is built against these shapes by someone who reads the
contract, not this code, so a key change here must be a conscious one: update
`web/lib/types.ts` and ADR-0018 in the same commit.

Also pins that `scripts/dump_openapi.py` carries these schemas into the one
document the `schema-sync` CI job checks, so `types.ts` is held to this service
by the same mechanism as the route service.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from commute.app import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent

# schema -> {field: required?}
EXPECTED = {
    "TraceFix": {"t": True, "lat": True, "lon": True, "speed_mps": True,
                 "accuracy_m": True, "heading_deg": True},
    "FollowedArtifact": {"effective_at": True, "artifact": True},
    "TraceChunk": {"fixes": True, "artifacts": False},
    "ChunkReceipt": {"trip_id": True, "seq": True, "created": True},
    "EtaPrediction": {"effective_at": True, "eta_s": True, "level": True,
                      "profile_version": True},
    "TripEnd": {"ended_at": True, "arrived": True, "prediction": True},
    "TripEndReceipt": {"trip_id": True, "created": True},
    "TesterInfo": {"label": True},
}


@pytest.fixture(scope="module")
def openapi(tmp_path_factory):
    db = tmp_path_factory.mktemp("contract") / "c.sqlite3"
    return create_app(db_path=db).openapi()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_wire_shapes(openapi, name):
    schema = openapi["components"]["schemas"][name]
    required = set(schema.get("required", []))
    assert {p: p in required for p in schema["properties"]} == EXPECTED[name]


def test_no_unmirrored_models(openapi):
    ours = set(openapi["components"]["schemas"]) - {"HTTPValidationError",
                                                    "ValidationError"}
    assert ours == set(EXPECTED)


def test_the_endpoints(openapi):
    ops = {(method.upper(), path)
           for path, item in openapi["paths"].items() for method in item}
    assert ops == {
        ("GET", "/health"),
        ("GET", "/v1/me"),
        ("PUT", "/v1/trips/{trip_id}/chunks/{seq}"),
        ("POST", "/v1/trips/{trip_id}/end"),
    }


def _dump_module():
    spec = importlib.util.spec_from_file_location(
        "dump_openapi", REPO_ROOT / "scripts" / "dump_openapi.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_schema_sync_document_carries_both_services():
    doc = _dump_module().merged_openapi()
    schemas = doc["components"]["schemas"]
    assert "RouteRequest" in schemas        # the route service
    assert set(EXPECTED) <= set(schemas)    # the commute planner


def test_a_name_defined_differently_by_the_two_services_is_an_error():
    dump = _dump_module()
    base = {"components": {"schemas": {"Shared": {"type": "object", "properties": {"a": {}}}}}}
    same = {"components": {"schemas": {"Shared": {"type": "object", "properties": {"a": {}}}}}}
    dump.merge_schemas(base, same, source="commute")   # identical is fine
    other = {"components": {"schemas": {"Shared": {"type": "object", "properties": {"b": {}}}}}}
    with pytest.raises(ValueError, match="Shared"):
        dump.merge_schemas(base, other, source="commute")
