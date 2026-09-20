"""Unit tests for `tests.helpers.packs`, the real-pack locator.

`data/` is gitignored, so it exists only in the main checkout. A `git worktree`
gets a fresh checkout with no `data/` at all, and every real-pack test resolves
its pack through a CWD-relative literal like `"data/packs/berkeley_small"` --
so in a worktree they all skip and the bar stays green. That is a second,
independent way to lose real-pack coverage on top of the `importorskip`
described in `tests/test_ci_preconditions.py`, and the two stack: an agent can
build `sr_core`, believe it has closed the known gap, and still not run a single
real-pack assertion.

The resolution order is exercised here as a pure function over explicit inputs
rather than by manufacturing real worktrees, so these tests say the same thing
on every machine and on Windows.
"""
from __future__ import annotations

from pathlib import Path

from tests.helpers import packs as P


def _mkpacks(root: Path) -> Path:
    d = root / "data" / "packs"
    d.mkdir(parents=True)
    return d


def test_env_override_wins_over_everything(tmp_path):
    """`SR_PACKS_ROOT` is the escape hatch: an explicit answer beats both the
    CWD and git, and is honoured even if it does not exist yet, so a typo
    surfaces as "this path has no packs" rather than silently falling back."""
    cwd = tmp_path / "cwd"
    _mkpacks(cwd)
    elsewhere = tmp_path / "elsewhere"
    got = P.resolve_packs_root(env=str(elsewhere), cwd=cwd, git_common_dir=None)
    assert got == elsewhere


def test_cwd_wins_when_it_has_packs(tmp_path):
    """The main checkout must keep behaving exactly as it does today -- no
    subprocess, no surprises."""
    cwd = tmp_path / "main"
    want = _mkpacks(cwd)
    common = cwd / ".git"
    common.mkdir()
    assert P.resolve_packs_root(env=None, cwd=cwd, git_common_dir=common) == want


def test_worktree_falls_back_to_the_main_checkout(tmp_path):
    """The case this module exists for: CWD is a worktree with no `data/`,
    and `--git-common-dir` points at the main checkout's `.git`, whose parent
    does have the packs."""
    main = tmp_path / "main"
    want = _mkpacks(main)
    common = main / ".git"
    common.mkdir()
    wt = tmp_path / "main" / ".claude" / "worktrees" / "wt"
    wt.mkdir(parents=True)
    assert P.resolve_packs_root(env=None, cwd=wt, git_common_dir=common) == want


def test_ancestor_walk_finds_the_main_checkout_without_git(tmp_path):
    """The fallback that actually carries this repo. Worktrees live at
    `<main>/.claude/worktrees/<name>`, and the worktree's `.git` file records a
    Windows path, so `git rev-parse` run inside WSL -- how pytest runs here --
    resolves nothing. Passing `git_common_dir=None` is that situation."""
    main = tmp_path / "main"
    want = _mkpacks(main)
    wt = main / ".claude" / "worktrees" / "wt"
    wt.mkdir(parents=True)
    assert P.resolve_packs_root(env=None, cwd=wt, git_common_dir=None) == want


def test_git_common_dir_beats_the_ancestor_walk(tmp_path):
    """Authority order: when git can answer, its answer wins over whatever
    happens to sit in a parent directory."""
    main = tmp_path / "main"
    authoritative = _mkpacks(main)
    common = main / ".git"
    common.mkdir()
    outer = tmp_path / "outer"
    _mkpacks(outer)
    wt = outer / "wt"
    wt.mkdir()
    got = P.resolve_packs_root(env=None, cwd=wt, git_common_dir=common)
    assert got == authoritative


def test_falls_back_to_the_cwd_path_when_nothing_has_packs(tmp_path):
    """With no packs anywhere the answer is still the CWD-relative path, so the
    skip reason names the location a reader expects to populate."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    got = P.resolve_packs_root(env=None, cwd=cwd, git_common_dir=None)
    assert got == cwd / "data" / "packs"


def test_a_worktree_without_packs_anywhere_does_not_win_on_a_bare_git_dir(tmp_path):
    """`--git-common-dir` resolving somewhere real is not evidence the packs are
    there; only an actual `data/packs` directory is."""
    main = tmp_path / "main"
    common = main / ".git"
    common.mkdir(parents=True)
    wt = tmp_path / "wt"
    wt.mkdir()
    assert P.resolve_packs_root(env=None, cwd=wt, git_common_dir=common) == (
        wt / "data" / "packs")


def test_real_pack_returns_none_and_records_the_miss(tmp_path, monkeypatch):
    """A miss has to be recorded, because the whole point is that the skip
    stops being silent -- `conftest.py` reads this back in the summary."""
    monkeypatch.setenv("SR_PACKS_ROOT", str(tmp_path))
    P.reset_missing()
    assert P.real_pack("nope_small") is None
    assert "nope_small" in P.missing_packs()
    P.reset_missing()


def test_real_pack_returns_the_directory_when_present(tmp_path, monkeypatch):
    (tmp_path / "toy_pack").mkdir()
    monkeypatch.setenv("SR_PACKS_ROOT", str(tmp_path))
    P.reset_missing()
    assert P.real_pack("toy_pack") == tmp_path / "toy_pack"
    assert P.missing_packs() == []


def test_skip_reason_names_the_pack_and_the_root(tmp_path, monkeypatch):
    """The reason string is what a reader sees next to `s` in `-rs` output; it
    has to say which pack and where it was looked for."""
    monkeypatch.setenv("SR_PACKS_ROOT", str(tmp_path))
    reason = P.skip_reason("berkeley_small")
    assert "berkeley_small" in reason
    assert str(tmp_path) in reason


def test_packs_root_is_not_cached_across_env_changes(tmp_path, monkeypatch):
    """Guards against a future `lru_cache`: `test_api_contract.py` monkeypatches
    pack env vars per test, so a cached root would leak between tests."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    monkeypatch.setenv("SR_PACKS_ROOT", str(a))
    assert P.packs_root() == a
    monkeypatch.setenv("SR_PACKS_ROOT", str(b))
    assert P.packs_root() == b
