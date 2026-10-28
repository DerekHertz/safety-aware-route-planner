"""The Trip planner reaches the engine only through `/route` (ADR-0019 decision 1).

Same rule and same mechanism as `tests/test_commute_boundary.py`: no import of
the engine's halves (`pyref`, `sim`, `sr_core`, `core`), the pack builder
(`ingestion`) or the route service's package (`api`). Importing `commute` is
allowed, and is how tester tokens are shared.
"""
from __future__ import annotations

import subprocess
import sys

from tests.test_commute_boundary import FORBIDDEN, REPO_ROOT, _imported_roots

PLANNER = REPO_ROOT / "planner"


def test_the_planner_package_exists_and_has_modules():
    assert (PLANNER / "__init__.py").is_file()
    assert len(list(PLANNER.rglob("*.py"))) > 1


def test_no_planner_module_imports_the_engine_or_the_route_service():
    offenders = {
        str(path.relative_to(REPO_ROOT)): sorted(_imported_roots(path) & FORBIDDEN)
        for path in sorted(PLANNER.rglob("*.py"))
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert offenders == {}, f"planner/ must not import {sorted(FORBIDDEN)}: {offenders}"


def test_importing_the_app_loads_none_of_them_transitively():
    probe = (
        "import sys, planner.app, planner.eval\n"
        f"bad = sorted(m for m in sys.modules if m.split('.')[0] in {sorted(FORBIDDEN)!r})\n"
        "print(','.join(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == ""
