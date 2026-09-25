"""Boot-time pack fetching.

The happy path runs against a real HTTP server serving a real gzip tarball
rather than a mocked client — the parts most likely to break (streaming,
hashing, tar layout, the atomic rename) are exactly the parts a mock would
paper over.
"""
from __future__ import annotations

import hashlib
import json
import tarfile
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from api.packs_fetch import (
    PackFetchError,
    PacksLock,
    PackSpec,
    ensure_packs,
    load_lock,
)


def _make_pack(root, region: str) -> None:
    """A directory that looks like a pack to the fetcher (manifest + a file)."""
    d = root / region
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"region": region, "format_version": 2}))
    (d / "node_lat.f64").write_bytes(b"\x00" * 64)


def _tar_gz(src_dir, dest_file, arcname: str) -> str:
    with tarfile.open(dest_file, "w:gz") as tf:
        tf.add(src_dir, arcname=arcname)
    return hashlib.sha256(dest_file.read_bytes()).hexdigest()


@contextmanager
def _serving(directory):
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def published(tmp_path):
    """A 'bucket' directory holding tag/region.tar.gz, plus its digest."""
    staged = tmp_path / "staged"
    _make_pack(staged, "toyland")
    bucket = tmp_path / "bucket" / "packs-v2-test"
    bucket.mkdir(parents=True)
    sha = _tar_gz(staged / "toyland", bucket / "toyland.tar.gz", "toyland")
    return tmp_path / "bucket", sha


def _lock(base_url: str, sha: str, **kw) -> PacksLock:
    return PacksLock(
        base_url=base_url,
        tag=kw.get("tag", "packs-v2-test"),
        format_version=kw.get("format_version", 2),
        regions={"toyland": PackSpec(region="toyland", sha256=sha)},
    )


def test_downloads_verifies_and_installs(tmp_path, published):
    bucket, sha = published
    root = tmp_path / "packs"
    with _serving(bucket) as base_url:
        fetched = ensure_packs(["toyland"], root, _lock(base_url, sha))
    assert fetched == ["toyland"]
    assert (root / "toyland" / "manifest.json").is_file()
    assert json.loads((root / "toyland" / "manifest.json").read_text())["region"] == "toyland"
    # staging directories must not survive a successful install
    assert [p.name for p in root.iterdir()] == ["toyland"]


def test_existing_pack_is_never_touched(tmp_path):
    """The no-network path: local dev and CI must not depend on a bucket."""
    root = tmp_path / "packs"
    _make_pack(root, "toyland")
    # base_url points nowhere; reaching the network at all would fail the test
    assert ensure_packs(["toyland"], root, _lock("http://127.0.0.1:1/", "0" * 64)) == []


def test_checksum_mismatch_installs_nothing(tmp_path, published):
    bucket, _sha = published
    root = tmp_path / "packs"
    wrong = "1" * 64
    with _serving(bucket) as base_url:  # noqa: SIM117 - server must outlive the call
        with pytest.raises(PackFetchError, match="checksum mismatch"):
            ensure_packs(["toyland"], root, _lock(base_url, wrong))
    # nothing installed, and no staging debris left behind
    assert not (root / "toyland").exists()
    assert list(root.iterdir()) == []


def test_missing_and_unconfigured_explains_how_to_fix(tmp_path):
    with pytest.raises(PackFetchError, match="ingestion.build_pack"):
        ensure_packs(["toyland"], tmp_path / "packs", _lock("", "0" * 64))


def test_format_version_mismatch_is_caught_before_download(tmp_path):
    """Guards the stale-artifact trap: a bucket built for an older pack layout
    would otherwise fail deep inside GraphPack.load()."""
    lock = _lock("http://127.0.0.1:1/", "0" * 64, format_version=1)
    with pytest.raises(PackFetchError, match="format_version 1"):
        ensure_packs(["toyland"], tmp_path / "packs", lock)


def test_unpublished_region_is_named_in_the_error(tmp_path):
    lock = _lock("http://127.0.0.1:1/", "0" * 64)
    with pytest.raises(PackFetchError, match="atlantis"):
        ensure_packs(["atlantis"], tmp_path / "packs", lock)


def test_archive_escaping_the_destination_is_rejected(tmp_path):
    """The artifact is downloaded over the network, so a crafted tarball must
    not be able to write outside the pack directory (tarfile filter='data')."""
    bucket = tmp_path / "bucket" / "packs-v2-test"
    bucket.mkdir(parents=True)
    evil = tmp_path / "evil.txt"
    evil.write_text("pwned")
    archive = bucket / "toyland.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(evil, arcname="../../escaped.txt")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()

    root = tmp_path / "packs"
    with _serving(tmp_path / "bucket") as base_url:  # noqa: SIM117
        with pytest.raises(PackFetchError):
            ensure_packs(["toyland"], root, _lock(base_url, sha))
    assert not (tmp_path / "escaped.txt").exists()
    assert not (root / "toyland").exists()


def test_mixed_tags_fetch_each_region_from_its_own_release(tmp_path):
    """Per-region tags, end to end: parse a lock whose entries live under two
    release directories, then fetch both from one bucket. A region published
    later goes under its own immutable tag without moving the others."""
    staged = tmp_path / "staged"
    bucket = tmp_path / "bucket"
    shas = {}
    for region, tag in (("oldtown", "packs-v2-old"), ("newtown", "packs-v2-new")):
        _make_pack(staged, region)
        (bucket / tag).mkdir(parents=True)
        shas[region] = _tar_gz(staged / region, bucket / tag / f"{region}.tar.gz", region)

    root = tmp_path / "packs"
    with _serving(bucket) as base_url:
        p = tmp_path / "packs.lock"
        p.write_text(
            f'base_url = "{base_url}"\ntag = "packs-v2-old"\nformat_version = 2\n'
            "[regions]\n"
            f'oldtown = {{ sha256 = "{shas["oldtown"]}", bytes = 1 }}\n'
            f'newtown = {{ sha256 = "{shas["newtown"]}", bytes = 1, tag = "packs-v2-new" }}\n'
        )
        fetched = ensure_packs(["oldtown", "newtown"], root, load_lock(p))
    assert fetched == ["oldtown", "newtown"]
    for region in fetched:
        manifest = json.loads((root / region / "manifest.json").read_text())
        assert manifest["region"] == region


def test_printed_stanza_pastes_into_a_lock_without_moving_other_regions(
        tmp_path, capsys):
    """The writer/reader contract: `scripts/package_packs.py` (what the
    build-packs workflow runs) prints lines that, pasted under an existing
    lock's `[regions]`, resolve to the tag that run uploaded to, while an
    entry already there keeps resolving under the top-level tag."""
    from scripts.package_packs import main as package_main

    _make_pack(tmp_path / "built", "newtown")
    assert package_main(["--regions", "newtown", "--packs-dir", str(tmp_path / "built"),
                         "--out", str(tmp_path / "dist"), "--tag", "packs-v2-new"]) == 0
    printed = capsys.readouterr().out.split("--- paste into packs.lock ---")[1]
    lines = [ln for ln in printed.splitlines() if ln.startswith("newtown = ")]
    assert len(lines) == 1
    # Pasting must not be able to move the default tag everyone else uses.
    assert not any(ln.startswith("tag =") for ln in printed.splitlines())

    p = tmp_path / "packs.lock"
    p.write_text('base_url = "https://b.test/"\ntag = "packs-v2-old"\nformat_version = 2\n'
                 '[regions]\noldtown = { sha256 = "ab", bytes = 1 }\n' + lines[0] + "\n")
    lock = load_lock(p)
    assert lock is not None
    assert lock.url_for("newtown") == "https://b.test/packs-v2-new/newtown.tar.gz"
    assert lock.url_for("oldtown") == "https://b.test/packs-v2-old/oldtown.tar.gz"
    sha = hashlib.sha256((tmp_path / "dist" / "newtown.tar.gz").read_bytes()).hexdigest()
    assert lock.regions["newtown"].sha256 == sha


def test_http_error_is_wrapped(tmp_path, published):
    bucket, sha = published
    lock = _lock("", sha, tag="wrong-tag")
    with _serving(bucket) as base_url:  # noqa: SIM117
        lock = _lock(base_url, sha, tag="no-such-tag")
        with pytest.raises(PackFetchError, match="failed"):
            ensure_packs(["toyland"], tmp_path / "packs", lock)


class TestLockFile:
    def test_absent_lock_returns_none(self, tmp_path):
        assert load_lock(tmp_path / "nope.lock") is None

    def test_parses_regions_and_disables_on_empty_base_url(self, tmp_path):
        p = tmp_path / "packs.lock"
        p.write_text(
            'base_url = ""\ntag = "t"\nformat_version = 2\n'
            '[regions.a]\nsha256 = "AB"\nbytes = 12\n'
        )
        lock = load_lock(p)
        assert lock is not None and not lock.enabled
        assert lock.regions["a"].sha256 == "ab"  # normalized to lowercase
        assert lock.regions["a"].size_bytes == 12

    def test_base_url_without_regions_reads_as_unconfigured(self, tmp_path):
        """The half-configured state between creating a bucket and landing the
        first publish must say "not set up", not "your region is missing"."""
        p = tmp_path / "packs.lock"
        p.write_text('base_url = "https://x.test/"\ntag = ""\nformat_version = 2\n[regions]\n')
        lock = load_lock(p)
        assert lock is not None and not lock.enabled
        with pytest.raises(PackFetchError, match="not configured"):
            ensure_packs(["toyland"], tmp_path / "packs", lock)

    def test_env_var_overrides_base_url(self, tmp_path, monkeypatch):
        p = tmp_path / "packs.lock"
        p.write_text(
            'base_url = ""\ntag = "t"\nformat_version = 2\n'
            '[regions.r]\nsha256 = "ab"\n'
        )
        monkeypatch.setenv("SR_PACKS_URL", "https://example.test/")
        lock = load_lock(p)
        assert lock is not None and lock.enabled
        assert lock.url_for("r") == "https://example.test/t/r.tar.gz"

    def test_region_tag_overrides_and_absent_tag_falls_back(self, tmp_path):
        p = tmp_path / "packs.lock"
        p.write_text(
            'base_url = "https://b.test"\ntag = "packs-v2-top"\nformat_version = 2\n'
            "[regions]\n"
            'plain = { sha256 = "ab", bytes = 1 }\n'
            'pinned = { sha256 = "cd", bytes = 2, tag = "packs-v2-own" }\n'
        )
        lock = load_lock(p)
        assert lock is not None
        assert lock.url_for("plain") == "https://b.test/packs-v2-top/plain.tar.gz"
        assert lock.url_for("pinned") == "https://b.test/packs-v2-own/pinned.tar.gz"

    def test_committed_lock_file_is_parseable_and_matches_code(self):
        """packs.lock ships with fetching disabled, but it must always parse and
        must never drift from PACK_FORMAT_VERSION."""
        from pyref.graph import PACK_FORMAT_VERSION

        lock = load_lock("packs.lock")
        assert lock is not None, "packs.lock is missing from the repo root"
        assert lock.format_version == PACK_FORMAT_VERSION
