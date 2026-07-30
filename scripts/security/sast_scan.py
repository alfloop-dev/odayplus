#!/usr/bin/env python3
"""SAST check using Bandit for Python security auditing."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    print("Starting Python SAST scan...")
    venv_bin = str(ROOT / ".venv/bin")
    search_path = f"{venv_bin}:/home/lupin/.local/bin:/home/lupin/.cargo/bin:/usr/local/bin"
    uv_path = shutil.which("uv") or shutil.which("uv", path=search_path)
    bandit_path = shutil.which("bandit") or shutil.which("bandit", path=search_path)

    if uv_path:
        cmd = [
            uv_path,
            "run",
            "--with",
            "bandit",
            "bandit",
            "-r",
            "modules",
            "apps",
            "shared",
            "solver",
            "-x",
            ".venv,apps/data_platform/.venv",
            "-ll",
            "--skip",
            "B301,B310,B324,B104",
        ]
    elif bandit_path:
        cmd = [
            bandit_path,
            "-r",
            "modules",
            "apps",
            "shared",
            "solver",
            "-x",
            ".venv,apps/data_platform/.venv",
            "-ll",
            "--skip",
            "B301,B310,B324,B104",
        ]
    else:
        print("Error: Neither 'uv' nor 'bandit' executable found in PATH. SAST scan failed.", file=sys.stderr)
        return 1

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
