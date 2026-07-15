#!/usr/bin/env python3
"""SAST check using Bandit for Python security auditing."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    print("Starting Python SAST scan...")
    # Skip B301 (pickle), B310 (urllib urlopen), and B324 (md5 for mocks/non-security hashes)
    cmd = [
        "uv",
        "run",
        "--with",
        "bandit",
        "bandit",
        "-r",
        "modules",
        "apps",
        "shared",
        "solver",
        "-ll",
        "--skip",
        "B301,B310,B324",
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode == 0:
        print("SAST scan passed successfully.")
    else:
        print(f"SAST scan failed with exit code {result.returncode}")

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
