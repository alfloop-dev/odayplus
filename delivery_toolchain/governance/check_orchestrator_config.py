#!/usr/bin/env python3
"""Validate repository orchestrator configs against the authoritative schema."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

from common import ConfigError, load_config, load_config_document  # noqa: E402


def config_paths(extra_paths: list[str]) -> list[Path]:
    candidates = [
        ORCHESTRATOR_DIR / "config.example.json",
        ORCHESTRATOR_DIR / "config.local.example.json",
        ORCHESTRATOR_DIR / "config.json",
        ORCHESTRATOR_DIR / "config.local.json",
        *(Path(raw).expanduser() for raw in extra_paths),
    ]
    result: list[Path] = []
    for path in candidates:
        resolved = path if path.is_absolute() else ROOT / path
        if resolved.exists() and resolved not in result:
            result.append(resolved)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        help="also validate an explicit runtime config (repeatable)",
    )
    args = parser.parse_args()

    paths = config_paths(args.config)
    try:
        for path in paths:
            load_config_document(path)
        default_config = ORCHESTRATOR_DIR / "config.json"
        if default_config.exists():
            load_config(default_config)
        for raw_path in args.config:
            load_config(Path(raw_path).expanduser())
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Validated {len(paths)} config documents and their merged runtime views.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
