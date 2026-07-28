"""Turn "silently skipped" into "failed" when SR_CI_STRICT=1.

Two conditional skips can shrink this suite without anyone noticing, and both
hide exactly the coverage that matters most:

  * `tests/test_parity_cpp.py:16` — `pytest.importorskip("sr_core")`. If the C++
    extension is not built, the ENTIRE parity module produces zero test items.
    Bitwise Python<->C++ equality is the design premise of this codebase, so a
    green run that silently skipped all of it is worse than a red one.
  * `tests/test_parity_cpp.py:~97` — the real-pack parity tests skip unless
    `data/packs/berkeley_small` exists. Toy fixtures alone do not exercise the
    20k-edge graph.

Those skips are correct *locally*: a contributor without a compiler, or without
having built packs, should still be able to run the suite. So `test_parity_cpp.py`
is deliberately left alone. This file adds the opposite guarantee for CI, gated
on an environment variable, so the two can coexist.

`importorskip` skips at COLLECTION time — the module yields no items at all —
so there is nothing to mark or assert on after the fact. The check has to live
in a separate module, which is why this file exists rather than a marker.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pyref.graph import PACK_FORMAT_VERSION

REAL_PACK = Path("data/packs/berkeley_small")

strict = pytest.mark.skipif(
    os.environ.get("SR_CI_STRICT") != "1",
    reason="advisory locally; CI sets SR_CI_STRICT=1 to enforce",
)


@strict
def test_cpp_extension_is_built():
    """Guards the importorskip at tests/test_parity_cpp.py:16.

    A failure here means the whole parity suite vanished from this run.
    """
    import sr_core

    assert hasattr(sr_core, "Engine"), "sr_core imported but has no Engine"


@strict
def test_engine_impl_is_not_silently_downgraded():
    """`[engine] impl = "cpp"` falls back to pyref with only a warning
    (pyref/engine.py), which in production is a ~20x latency cliff nobody
    notices. If the config asks for cpp, CI must actually get cpp."""
    from pyref.config import DEFAULT_CONFIG_PATH, Config

    cfg = Config.load(os.environ.get("SR_CONFIG", DEFAULT_CONFIG_PATH))
    if cfg["engine"]["impl"] != "cpp":
        pytest.skip("config does not request the cpp engine")
    import sr_core  # noqa: F401


@strict
def test_real_pack_is_present():
    """Guards the skipif on the real-pack parity tests."""
    assert REAL_PACK.is_dir(), (
        f"{REAL_PACK} is missing, so real-pack parity silently skipped. "
        f"CI should fetch it via .github/actions/fetch-packs."
    )


@strict
def test_pack_format_matches_code():
    """A pack built for an older layout loads until it doesn't.

    This is not hypothetical: the packs sitting on disk during development were
    format_version 1 while the code required 2, which fails deep inside
    GraphPack.load() with no hint about the cause.
    """
    manifest = json.loads((REAL_PACK / "manifest.json").read_text())
    assert manifest["format_version"] == PACK_FORMAT_VERSION, (
        f"{REAL_PACK} is format_version {manifest['format_version']} but the code "
        f"requires {PACK_FORMAT_VERSION}. Rebuild and republish the packs, then "
        f"update packs.lock."
    )


@strict
def test_no_parity_tests_were_skipped(request):
    """Belt and braces: assert the parity module actually produced items.

    The checks above test the *causes* of an empty parity module; this tests the
    effect directly, so a new skip condition added later cannot slip past.
    """
    # Only meaningful when the whole suite was collected. Running a single file
    # (`pytest tests/test_ci_preconditions.py`) legitimately collects no parity
    # tests, and that must not read as a failure.
    args = [str(a) for a in request.config.invocation_params.args]
    if any(a.endswith(".py") or "::" in a for a in args):
        pytest.skip("subset run; this check applies to a full-suite run")

    items = request.session.items
    parity = [i for i in items if "test_parity_cpp" in str(i.fspath)]
    assert parity, (
        "no tests were collected from test_parity_cpp.py — the parity suite is "
        "not running at all in this session"
    )
