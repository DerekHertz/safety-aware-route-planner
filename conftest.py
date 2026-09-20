import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Announce real-pack coverage that silently vanished from this run.

    Two conditional skips can shrink this suite without anyone noticing, and
    `tests/test_ci_preconditions.py` already turns both into failures when
    `SR_CI_STRICT=1`. That covers CI. Locally the skips are *correct* -- a
    contributor without a compiler or without built packs must still be able to
    run the suite -- but "correct" is not the same as "invisible", and pytest's
    one-letter `s` at the end of a green bar is invisible.

    So: no behaviour change, no extra failures, just a banner naming what was
    skipped and how to get it back. Nothing prints when nothing was missed.
    """
    from tests.helpers.packs import describe_resolution, missing_packs

    missing = missing_packs()
    try:
        import sr_core  # noqa: F401
        have_core = True
    except ImportError:
        have_core = False

    if not missing and have_core:
        return

    w = terminalreporter
    w.write_sep("=", "REDUCED COVERAGE (advisory)", yellow=True, bold=True)
    if missing:
        w.write_line(
            f"real packs not found, so real-pack tests skipped: "
            f"{', '.join(missing)}")
        w.write_line(f"  looked under: {describe_resolution()}")
        w.write_line(
            "  `data/` is gitignored, so a git worktree has no packs of its "
            "own and they are resolved from the main checkout instead. If that "
            "has none either, build or fetch them, or point SR_PACKS_ROOT at a "
            "packs directory.")
    if not have_core:
        w.write_line(
            "sr_core is not importable, so the ENTIRE Python<->C++ parity "
            "suite was skipped (tests/test_parity_cpp.py). See "
            "docs/agents/handoff.md for the build recipe.")
    w.write_line(
        "These are advisory locally; CI sets SR_CI_STRICT=1 and "
        "tests/test_ci_preconditions.py turns them into failures.")
