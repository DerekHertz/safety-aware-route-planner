"""The commute planner reaches the engine only through route artifacts.

ADR-0011 puts the commute planner in this repo as its own top-level directory
and says the ADR-0001 boundary is enforced "by a test asserting the commute
service imports nothing from `pyref/`, not by a repo wall". This is that test.

It forbids more than `pyref`: the engine's other halves (`sim`, the C++
`sr_core`, `core`), the pack builder (`ingestion`), and the route service's own
package (`api`). The two services share a wire contract, not code; an import of
`api` would make the commute planner deploy the route service's dependencies
and inherit its config loading, which is `pyref.config`.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMUTE = REPO_ROOT / "commute"
FORBIDDEN = {"pyref", "sim", "sr_core", "core", "ingestion", "api"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_commute_package_exists_and_has_modules():
    assert (COMMUTE / "__init__.py").is_file()
    assert len(list(COMMUTE.rglob("*.py"))) > 1


def test_no_commute_module_imports_the_engine_or_the_route_service():
    offenders = {
        str(path.relative_to(REPO_ROOT)): sorted(_imported_roots(path) & FORBIDDEN)
        for path in sorted(COMMUTE.rglob("*.py"))
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert offenders == {}, f"commute/ must not import {sorted(FORBIDDEN)}: {offenders}"


def test_importing_the_app_loads_none_of_them_transitively():
    """The static check sees only direct imports; this catches one smuggled in
    through a third module."""
    probe = (
        "import sys, commute.app, commute.tokens, commute.retention\n"
        f"bad = sorted(m for m in sys.modules if m.split('.')[0] in {sorted(FORBIDDEN)!r})\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == ""
