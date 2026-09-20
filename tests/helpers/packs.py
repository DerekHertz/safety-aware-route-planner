"""Find the real graph packs from anywhere in the repo, worktrees included.

`data/` is gitignored (`.gitignore` line 4), so the packs live only in the main
checkout. Every `git worktree` gets a fresh checkout **without** `data/`, and
the real-pack tests used to name their pack as a CWD-relative literal
(`"data/packs/berkeley_small"`). In a worktree that resolves to nothing, every
real-pack test skips, and the run still reports a green bar. Measured: a
worktree with `sr_core` genuinely built produced `15 passed, 2 skipped` for the
parity module, where the 2 skipped were the real-pack tests.

That is a *second* way to lose real-pack coverage, independent of the
`pytest.importorskip("sr_core")` documented in `tests/test_ci_preconditions.py`,
and the two stack badly: build the extension, close the gap you know about, and
still assert nothing against a 20k-edge graph. With seven active worktrees in
this repo, that is the normal working mode for an agent session, not an edge
case.

Resolution order
----------------
1. ``SR_PACKS_ROOT``, if set. Explicit beats inferred, and it is honoured even
   when it does not exist, so a typo reads as "no packs there" rather than
   silently falling back to a different answer.
2. ``<cwd>/data/packs``, if it exists -- the main checkout, unchanged.
3. ``<parent of git --git-common-dir>/data/packs``, if it exists. From inside a
   worktree `git rev-parse --git-common-dir` prints the *main* checkout's
   `.git`, which is exactly the hop needed; from the main checkout it prints
   `.git` and step 2 already won.
4. ``<ancestor>/data/packs`` for the nearest ancestor that has one. This is not
   redundant with step 3. Worktrees here live at
   `<main>/.claude/worktrees/<name>`, i.e. *underneath* the main checkout, and
   step 3 does not survive this project's actual toolchain: the worktree's
   `.git` file records a **Windows** path (`gitdir: D:/.../.git/worktrees/...`),
   so `git rev-parse` run inside WSL -- which is how pytest runs here, the repo
   `.venv` being a Linux venv -- reports "not a git repository" and yields
   nothing. The walk needs no subprocess and does not care which OS wrote the
   pointer file. Step 3 is kept ahead of it because it is the authoritative
   answer when git can answer, and it also covers a worktree created *outside*
   the main checkout, which the walk cannot reach.
5. Otherwise ``<cwd>/data/packs`` anyway, so the skip reason names the path a
   reader would expect to populate.

Why not ``SR_PACK_DIR``
-----------------------
`SR_PACK_DIR` (api/state.py) names **one pack directory**, not the packs root,
and `tests/test_api_contract.py`, `test_app_config.py` and four others
monkeypatch it to a toy pack under `tmp_path`. Reusing it here would point the
real-pack tests at a two-node toy mid-run. Different meaning, different name.

Why not a per-worktree symlink
------------------------------
It works, but it is a step every worktree needs and nothing enforces -- the same
shape of failure as the one being fixed, just moved into a setup doc. Resolving
through git needs no ceremony and is self-healing for worktrees that already
exist.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

# Packs a test asked for and did not get, in ask order. `conftest.py` reads this
# back in `pytest_terminal_summary` so the skip is announced rather than silent.
_MISSING: list[str] = []


def _git_common_dir(cwd: Path) -> Path | None:
    """The main checkout's `.git`, or None if git is unavailable/not a repo.

    Never raises: a test suite must not depend on git being installed, and the
    caller already has a sane fallback.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    # Relative in the main checkout (`.git`), absolute from a worktree.
    return (cwd / out).resolve()


def resolve_packs_root(*, env: str | None, cwd: Path,
                       git_common_dir: Path | None) -> Path:
    """Pure core of `packs_root`, kept separate so the order above is testable
    without manufacturing real worktrees on three platforms."""
    if env:
        return Path(env)
    local = cwd / "data" / "packs"
    if local.is_dir():
        return local
    if git_common_dir is not None:
        shared = git_common_dir.parent / "data" / "packs"
        if shared.is_dir():
            return shared
    for parent in cwd.resolve().parents:
        candidate = parent / "data" / "packs"
        if candidate.is_dir():
            return candidate
    return local


def packs_root() -> Path:
    """Where built packs live for this checkout.

    Deliberately uncached: several modules monkeypatch pack env vars per test,
    and a cached root would leak the first test's answer into the rest.
    Resolution is two `is_dir()` calls plus, only in a worktree, one `git
    rev-parse` -- cheap enough to redo.
    """
    cwd = Path.cwd()
    env = os.environ.get("SR_PACKS_ROOT")
    git_dir = None if (env or (cwd / "data" / "packs").is_dir()) else _git_common_dir(cwd)
    return resolve_packs_root(env=env, cwd=cwd, git_common_dir=git_dir)


def describe_resolution() -> str:
    """One line for the terminal summary: where we looked and how we got there."""
    root = packs_root()
    if os.environ.get("SR_PACKS_ROOT"):
        return f"{root} (from SR_PACKS_ROOT)"
    if root == Path.cwd() / "data" / "packs":
        return f"{root} (this checkout; no packs found anywhere above it either)"
    return f"{root} (resolved out of this worktree, from the main checkout)"


def real_pack(name: str) -> Path | None:
    """The built pack `name`, or None -- recording the miss for the summary."""
    p = packs_root() / name
    if p.is_dir():
        return p
    if name not in _MISSING:
        _MISSING.append(name)
    return None


def has_real_pack(name: str) -> bool:
    """`real_pack` as a predicate, for `pytest.mark.skipif` conditions."""
    return real_pack(name) is not None


def skip_reason(name: str) -> str:
    return f"real pack {name!r} not built under {packs_root()}"


def missing_packs() -> list[str]:
    return list(_MISSING)


def reset_missing() -> None:
    _MISSING.clear()
