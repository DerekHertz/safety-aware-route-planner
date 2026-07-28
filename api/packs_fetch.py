"""Download prebuilt graph packs from object storage at startup.

`data/` is gitignored and building a pack hits Overpass, so a fresh container
has no packs and cannot serve. This module closes that gap: it reads
`packs.lock`, downloads whatever the configured regions need, verifies each
artifact against its recorded digest, and unpacks it into the pack directory.

Design notes:

* **No credentials.** Packs are derived from public OpenStreetMap data, so the
  bucket is public-read and this fetches over plain HTTPS with httpx (already a
  dependency). Nothing secret lives in the runtime environment. Only CI, which
  uploads, needs keys.

* **Gzip, not zstd.** stdlib `tarfile` reads .tar.gz; zstd needs the third-party
  `zstandard` package until Python 3.14. Measured on the berkeley_oakland pack,
  gzip gets 4.69 MB down to 2.69 MB (57%); zstd would save perhaps another
  0.2 MB on a download that happens once per machine lifetime. Not worth a
  runtime dependency in the API image.

* **Never overwrites.** A region already present on disk is left alone. Local
  development and the test suite therefore never touch the network, and a
  deploy cannot clobber a good pack with a bad download.

* **Atomic.** Each pack extracts into a temporary directory and is renamed into
  place only after its digest and layout check pass, so an interrupted download
  can never leave a half-written pack that `GraphPack.load()` would choke on.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

import httpx

from pyref.graph import PACK_FORMAT_VERSION

DEFAULT_LOCK_PATH = "packs.lock"
_CHUNK = 1 << 20  # 1 MiB


class PackFetchError(RuntimeError):
    """Raised when packs are required but cannot be obtained intact."""


@dataclass(frozen=True)
class PackSpec:
    region: str
    sha256: str
    size_bytes: int | None = None


@dataclass(frozen=True)
class PacksLock:
    base_url: str
    tag: str
    format_version: int
    regions: dict[str, PackSpec]

    @property
    def enabled(self) -> bool:
        """Fetching needs a bucket AND at least one published region.

        A base_url with no regions is the half-configured state you get between
        creating the bucket and landing the first publish, and it should read as
        "not set up yet" rather than "set up, but your region is missing".
        """
        return bool(self.base_url) and bool(self.regions)

    def url_for(self, region: str) -> str:
        base = self.base_url if self.base_url.endswith("/") else self.base_url + "/"
        prefix = f"{self.tag}/" if self.tag else ""
        return f"{base}{prefix}{region}.tar.gz"


def load_lock(path: str | Path | None = None) -> PacksLock | None:
    """Parse packs.lock. Returns None when the file is absent.

    SR_PACKS_URL overrides base_url, which is how a deployment points at its own
    bucket (or how a test points at a local HTTP server) without editing the
    committed file.
    """
    lock_path = Path(path or os.environ.get("SR_PACKS_LOCK") or DEFAULT_LOCK_PATH)
    if not lock_path.is_file():
        return None
    raw = tomllib.loads(lock_path.read_text(encoding="utf-8"))

    regions = {
        name: PackSpec(
            region=name,
            sha256=str(entry["sha256"]).lower(),
            size_bytes=entry.get("bytes"),
        )
        for name, entry in (raw.get("regions") or {}).items()
    }
    return PacksLock(
        base_url=os.environ.get("SR_PACKS_URL") or str(raw.get("base_url", "")),
        tag=str(raw.get("tag", "")),
        format_version=int(raw.get("format_version", PACK_FORMAT_VERSION)),
        regions=regions,
    )


def ensure_packs(
    regions: list[str],
    pack_root: str | Path,
    lock: PacksLock | None = None,
    *,
    timeout: float = 120.0,
) -> list[str]:
    """Make sure every named region exists under `pack_root`.

    Returns the regions actually downloaded (empty when everything was already
    present, or when fetching is disabled). Raises PackFetchError if a region is
    missing and cannot be fetched — a container that cannot serve its configured
    regions should fail loudly at boot, not answer requests with 500s.
    """
    root = Path(pack_root)
    missing = [r for r in regions if not _is_present(root / r)]
    if not missing:
        return []

    if lock is None:
        lock = load_lock()
    if lock is None or not lock.enabled:
        raise PackFetchError(
            f"missing pack(s) {missing} under {root} and pack fetching is not "
            f"configured. Either build them locally "
            f"(`python -m ingestion.build_pack --region <name>`) or set "
            f"base_url in packs.lock / the SR_PACKS_URL environment variable."
        )

    if lock.format_version != PACK_FORMAT_VERSION:
        raise PackFetchError(
            f"packs.lock publishes format_version {lock.format_version} but this "
            f"code requires {PACK_FORMAT_VERSION}. The bucket holds packs built "
            f"for a different layout; rebuild and republish them, then update "
            f"packs.lock."
        )

    unknown = [r for r in missing if r not in lock.regions]
    if unknown:
        raise PackFetchError(
            f"region(s) {unknown} are configured to be served but are not "
            f"published in packs.lock (has: {sorted(lock.regions)})."
        )

    root.mkdir(parents=True, exist_ok=True)
    fetched = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for region in missing:
            _fetch_one(client, lock, lock.regions[region], root)
            fetched.append(region)
    return fetched


def _is_present(pack_dir: Path) -> bool:
    """A pack counts as present only if its manifest is there — an empty or
    half-extracted directory must not be mistaken for a usable pack."""
    return (pack_dir / "manifest.json").is_file()


def _fetch_one(
    client: httpx.Client, lock: PacksLock, spec: PackSpec, root: Path
) -> None:
    url = lock.url_for(spec.region)
    # Staging dir sits inside `root` so the final rename stays on one
    # filesystem; os.replace across devices would fail.
    staging = Path(tempfile.mkdtemp(prefix=f".{spec.region}.", dir=root))
    try:
        archive = staging / "pack.tar.gz"
        digest = _download(client, url, archive)
        if digest != spec.sha256:
            raise PackFetchError(
                f"checksum mismatch for {spec.region}: {url} hashed to {digest}, "
                f"packs.lock expects {spec.sha256}. Refusing to install it."
            )

        extracted = staging / "x"
        extracted.mkdir()
        try:
            with tarfile.open(archive, mode="r:gz") as tf:
                # filter="data" blocks absolute paths, "..", symlinks and device
                # nodes. This archive comes off the network; without it a crafted
                # tarball could write anywhere the process can reach.
                tf.extractall(extracted, filter="data")
        except (tarfile.TarError, OSError) as exc:
            raise PackFetchError(
                f"could not unpack the archive for {spec.region} from {url}: {exc}"
            ) from exc

        payload = extracted / spec.region
        if not _is_present(payload):
            raise PackFetchError(
                f"archive for {spec.region} does not contain "
                f"{spec.region}/manifest.json (found: "
                f"{sorted(p.name for p in extracted.iterdir())})"
            )
        os.replace(payload, root / spec.region)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m api.packs_fetch --regions a,b`.

    Used by the fetch-packs CI action and for bootstrapping a checkout without
    building packs from Overpass. Shares the exact code path the API uses at
    startup, so there is no second implementation to drift.
    """
    import argparse

    ap = argparse.ArgumentParser(description="Download prebuilt graph packs.")
    ap.add_argument("--regions", required=True, help="comma-separated region names")
    ap.add_argument("--dest", default="data/packs", help="pack root (default: data/packs)")
    ap.add_argument("--lock", default=None, help="path to packs.lock")
    ap.add_argument(
        "--optional",
        action="store_true",
        help="warn instead of failing when fetching is unconfigured. Lets CI stay "
        "green before a bucket exists; tests that need a real pack skip instead.",
    )
    args = ap.parse_args(argv)

    regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    try:
        fetched = ensure_packs(regions, args.dest, load_lock(args.lock))
    except PackFetchError as exc:
        if args.optional:
            print(f"::warning::pack fetch skipped: {exc}")
            return 0
        print(f"error: {exc}")
        return 1
    print(f"fetched: {', '.join(fetched)}" if fetched else "all packs already present")
    return 0


def _download(client: httpx.Client, url: str, dest: Path) -> str:
    """Stream to disk, hashing as we go. Returns the hex sha256.

    Streamed rather than held in memory: packs are tens of megabytes and the
    API container is sized for the graph, not for buffering downloads.
    """
    h = hashlib.sha256()
    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes(_CHUNK):
                    h.update(chunk)
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        raise PackFetchError(f"downloading {url} failed: {exc}") from exc
    return h.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
