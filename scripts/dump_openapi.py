"""Print the API's OpenAPI schema to stdout.

Feeds web/scripts/check-schema-sync.mjs, which compares it against
web/lib/types.ts so the hand-mirrored contract cannot drift silently.

Runs without a graph pack: AppState.load() happens in the FastAPI lifespan, not
at import, so building the app object only needs config/config.toml. That keeps
the schema-sync CI job down to a plain `pip install -r requirements.txt` with no
pack fetch and no C++ build.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.main import create_app  # noqa: E402  (needs the path insert above)


def main() -> int:
    json.dump(create_app().openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
