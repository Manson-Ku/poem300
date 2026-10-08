#!/usr/bin/env python3
"""Sync the local Bpmf Huninn base font from a pinned upstream revision.

The font binary remains local/gitignored. This command verifies the
download using the Git blob SHA-1 recorded by the upstream repository.
"""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path

UPSTREAM_COMMIT = "fa20c2bb5e2986856974f00c93a662d0805c92a0"
UPSTREAM_PATH = "fonts/BpmfHuninn-Regular.ttf"
EXPECTED_GIT_BLOB_SHA1 = "38c3f0ea596bf4bbf6488cd1c7b5e77925a2199f"
URL = (
    "https://raw.githubusercontent.com/ButTaiwan/bpmfvs/"
    + UPSTREAM_COMMIT
    + "/"
    + UPSTREAM_PATH
)

def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="fonts/BpmfHuninn-Regular.ttf")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    if output.exists():
        current_sha = git_blob_sha1(output.read_bytes())
        if current_sha == EXPECTED_GIT_BLOB_SHA1:
            print(
                "PASS existing base font matches pinned upstream "
                + current_sha
            )
            return 0
        if not args.force:
            print(
                "BLOCKED local base font differs from pinned upstream "
                + current_sha
            )
            print("Re-run with --force to replace it.")
            return 1

    print(f"download={URL}")
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read()

    actual = git_blob_sha1(data)
    if actual != EXPECTED_GIT_BLOB_SHA1:
        print(
            "ERROR upstream Git blob SHA-1 mismatch "
            f"expected={EXPECTED_GIT_BLOB_SHA1} actual={actual}"
        )
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    print(f"PASS output={output.as_posix()} git_blob_sha1={actual}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
