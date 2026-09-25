"""Print the OpenAPI schema that `web/lib/types.ts` is checked against.

Feeds web/scripts/check-schema-sync.mjs, which compares it against
web/lib/types.ts so the hand-mirrored contract cannot drift silently.

It is the route service's document with the commute planner's schemas
(`commute/`, ADR-0018) merged into `components.schemas`, so one mechanism
holds `types.ts` to both services. Only the schemas are merged; the checker
reads nothing else. A schema name the two services define differently is an
error rather than a silent overwrite.

Runs without a graph pack or a database: AppState.load() and the commute
store's schema creation both happen in FastAPI lifespans, not at import, so
building the app objects only needs config/config.toml. That keeps the
schema-sync CI job down to a plain `pip install -r requirements.txt` with no
pack fetch and no C++ build.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.main import create_app  # noqa: E402  (needs the path insert above)
from commute.app import create_app as create_commute_app  # noqa: E402


def merge_schemas(into: dict[str, Any], other: dict[str, Any], source: str) -> None:
    """Add `other`'s component schemas to `into`, in place."""
    schemas = into.setdefault("components", {}).setdefault("schemas", {})
    for name, schema in other.get("components", {}).get("schemas", {}).items():
        if name in schemas and schemas[name] != schema:
            raise ValueError(
                f"schema {name!r} is defined differently by the route service and "
                f"the {source} service; rename one of them")
        schemas[name] = schema


def merged_openapi() -> dict[str, Any]:
    doc = create_app().openapi()
    merge_schemas(doc, create_commute_app().openapi(), source="commute")
    return doc


def main() -> int:
    json.dump(merged_openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
