"""Resolve the commit SHA baked into the Docker image (/app/GIT_SHA).

Prefers the explicit GIT_SHA build arg (validated as a hex SHA), then
resolves .git/HEAD without needing the git binary (slim images don't ship
it). Prints "unknown" when nothing resolves, so a malformed value can never
be advertised as the running commit.
"""

from __future__ import annotations

import os
import re

# Accepts full SHAs and the usual git short forms (>= 4 chars).
_HEX_SHA = re.compile(r"^[0-9a-f]{4,40}$")


def is_hex_sha(value: str) -> bool:
    return bool(_HEX_SHA.match(value))


def from_git(git_dir: str = ".git") -> str | None:
    try:
        with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head or None
    ref = head[4:].strip()
    try:
        with open(os.path.join(git_dir, ref), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        pass
    try:
        with open(os.path.join(git_dir, "packed-refs"), encoding="utf-8") as fh:
            packed = fh.read()
    except OSError:
        return None
    for line in packed.splitlines():
        if line.endswith(" " + ref):
            return line.split()[0]
    return None


def main() -> int:
    sha = os.environ.get("GIT_SHA", "unknown").strip()
    if not is_hex_sha(sha):
        sha = from_git(os.environ.get("GIT_DIR", ".git")) or "unknown"
        if not is_hex_sha(sha):
            sha = "unknown"
    print(sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
