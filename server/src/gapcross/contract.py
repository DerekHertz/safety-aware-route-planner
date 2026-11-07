"""Render the OpenAPI contract. Run `uv run python -m gapcross.contract` to rewrite api/openapi.json.

Optional fields are written without their `{"type": "null"}` branch: swift-openapi-generator skips
any property whose `anyOf` includes null, which would drop `Comparison.alternative` from the app.
Those fields are already left out of `required`, and a null on the wire decodes as absent.
"""

import json
from pathlib import Path
from typing import Any

from .app import app

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "api" / "openapi.json"


def render_contract() -> str:
    return json.dumps(_drop_null_branches(app.openapi()), indent=2, sort_keys=True) + "\n"


def _drop_null_branches(node: Any) -> Any:
    if isinstance(node, list):
        return [_drop_null_branches(v) for v in node]
    if not isinstance(node, dict):
        return node
    node = {k: _drop_null_branches(v) for k, v in node.items()}
    branches = node.get("anyOf")
    if isinstance(branches, list) and {"type": "null"} in branches:
        rest = [b for b in branches if b != {"type": "null"}]
        del node["anyOf"]
        if len(rest) == 1:
            node = {**rest[0], **node}
        else:
            node["anyOf"] = rest
    return node


if __name__ == "__main__":
    CONTRACT_PATH.write_text(render_contract())
    print(f"wrote {CONTRACT_PATH}")
