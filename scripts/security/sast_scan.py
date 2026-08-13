#!/usr/bin/env python3
"""Compatibility entrypoint for the canonical delivery-toolchain command."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from delivery_toolchain.security.sast_scan import main  # noqa: E402

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
