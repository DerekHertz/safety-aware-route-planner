"""Render the OpenAPI contract. Run `uv run python -m gapcross.contract` to rewrite api/openapi.json."""

import json
from pathlib import Path

from .app import app

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "api" / "openapi.json"


def render_contract() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    CONTRACT_PATH.write_text(render_contract())
    print(f"wrote {CONTRACT_PATH}")
