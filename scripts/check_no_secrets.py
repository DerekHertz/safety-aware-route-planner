"""Backstop against committing credentials.

Deliberately a small, dependency-free check rather than gitleaks or GitHub's
push protection: the former adds a third-party action with licensing quirks,
and the latter needs Advanced Security on private repositories. This catches
the realistic accidents — a stray .env, a pasted key, a private key file — and
makes no claim to be a real secret scanner.

Scans files tracked by git, so anything gitignored (data/, .env, node_modules)
is out of scope by construction.

    python scripts/check_no_secrets.py
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Filenames that should never be tracked, whatever their contents.
FORBIDDEN_NAMES = re.compile(
    r"(^|/)("
    r"\.env(\.[\w.-]+)?"          # .env, .env.local, .env.production
    r"|credentials(\.json)?"
    r"|id_rsa|id_ed25519"
    r"|.*\.(pem|pfx|p12|keystore)"
    r")$",
    re.IGNORECASE,
)
# .env.example is the documented template and must stay tracked.
ALLOWED_NAMES = re.compile(r"(^|/)\.env\.example$")

CONTENT_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key block"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub token"),
    (re.compile(r"\bxox[abpsr]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    # An assignment whose *name* says secret and whose value is long enough to
    # be one. Placeholders (anything with example/changeme/your, or all zeros)
    # are excluded below.
    #
    # No \b around the keyword group: underscore is a word character, so \b
    # never matches between "aws_" and "secret", and every snake_case name —
    # which is to say most of them — would slip straight through. Requiring the
    # keyword to be immediately followed by an assignment is what keeps this
    # anchored instead.
    (
        re.compile(
            r"""(?i)(secret|password|passwd|api[_-]?key|access[_-]?key|
                 auth[_-]?token|private[_-]?key)\s*[:=]\s*["'][^"'\s]{16,}["']""",
            re.VERBOSE,
        ),
        "credential-shaped assignment",
    ),
]

PLACEHOLDER = re.compile(
    r"(?i)(example|changeme|your[_-]?|placeholder|redacted|xxxx|<[^>]+>|\bfake\b|^0+$)"
)

# Text files only; a binary hit would be a false positive.
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".ico", ".gif", ".pdf", ".zst", ".gz", ".pyd", ".so"}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, text=True, check=True
    ).stdout
    return [f for f in out.split("\0") if f]


def main() -> int:
    problems: list[str] = []

    for name in tracked_files():
        if FORBIDDEN_NAMES.search(name) and not ALLOWED_NAMES.search(name):
            problems.append(f"{name}: this file should never be committed")

        path = Path(name)
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern, label in CONTENT_PATTERNS:
                m = pattern.search(line)
                if not m or PLACEHOLDER.search(m.group(0)):
                    continue
                problems.append(f"{name}:{lineno}: possible {label}")

    if problems:
        print("Possible secrets in tracked files:\n", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(
            "\nIf a match is a false positive, narrow the pattern in "
            "scripts/check_no_secrets.py rather than deleting the check.",
            file=sys.stderr,
        )
        return 1

    print(f"no credential-shaped content in {len(tracked_files())} tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
