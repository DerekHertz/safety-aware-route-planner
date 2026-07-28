"""Package built graph packs into per-region .tar.gz artifacts and print the
`packs.lock` stanza that pins them.

Run by .github/workflows/build-packs.yml after ingestion, but it works locally
too:

    python scripts/package_packs.py --regions berkeley_small --out dist/packs

One archive per region, rather than one for everything, so a deployment
downloads only the regions it serves and adding a region does not invalidate
the others' digests.

Archives are byte-reproducible: entries are sorted, and mtime/uid/gid/mode are
normalized in both the tar entries and the gzip header. Rebuilding the same
pack therefore yields the same sha256, so the digests in packs.lock can be
re-derived and checked rather than merely trusted.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import tarfile
from pathlib import Path

# Fixed stamp for reproducibility. The value is arbitrary; only its constancy
# matters. Zero is avoided because some tools treat it as "unset".
_EPOCH = 315532800  # 1980-01-01T00:00:00Z


def _normalize(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    ti.mtime = _EPOCH
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mode = 0o755 if ti.isdir() else 0o644
    return ti


def package(pack_dir: Path, dest: Path) -> tuple[str, int]:
    """Write dest (a .tar.gz of pack_dir under arcname=pack_dir.name).

    Returns (sha256_hex, size_bytes).
    """
    region = pack_dir.name
    members = sorted(
        (p for p in pack_dir.rglob("*") if p.is_file()),
        key=lambda p: p.relative_to(pack_dir).as_posix(),
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as raw:
        # mtime=0 keeps the gzip header constant; tarfile's own "w:gz" mode
        # stamps the current time and would break reproducibility.
        with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf:
                for p in members:
                    arc = f"{region}/{p.relative_to(pack_dir).as_posix()}"
                    tf.add(p, arcname=arc, filter=_normalize)

    data = dest.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--regions", required=True, help="comma-separated region names")
    ap.add_argument("--packs-dir", default="data/packs", help="where built packs live")
    ap.add_argument("--out", default="dist/packs", help="where to write .tar.gz files")
    ap.add_argument("--tag", default="", help="release tag, for the printed stanza")
    ap.add_argument("--base-url", default="", help="bucket URL, for the printed stanza")
    args = ap.parse_args(argv)

    packs_dir, out = Path(args.packs_dir), Path(args.out)
    regions = [r.strip() for r in args.regions.split(",") if r.strip()]

    entries: list[tuple[str, str, int]] = []
    for region in regions:
        src = packs_dir / region
        if not (src / "manifest.json").is_file():
            print(f"error: {src} has no manifest.json — was the pack built?")
            return 1
        sha, size = package(src, out / f"{region}.tar.gz")
        manifest = json.loads((src / "manifest.json").read_text())
        print(
            f"{region}: {size / 1e6:.2f} MB  sha256={sha[:16]}...  "
            f"format_version={manifest.get('format_version')}  "
            f"edges={manifest.get('num_edges', '?')}"
        )
        entries.append((region, sha, size))

    print("\n--- paste into packs.lock ---")
    if args.base_url:
        print(f'base_url = "{args.base_url}"')
    if args.tag:
        print(f'tag = "{args.tag}"')
    print("\n[regions]")
    for region, sha, size in entries:
        print(f'{region} = {{ sha256 = "{sha}", bytes = {size} }}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
