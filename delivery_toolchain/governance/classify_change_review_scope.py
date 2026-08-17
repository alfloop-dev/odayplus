#!/usr/bin/env python3
"""Classify a proposed change as development tooling or product/mixed.

The result is intentionally fail-closed: only paths explicitly listed in the
checked-in manifest are development tooling.  Product and unknown paths must
retain the product reviewer gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "config/change-review-scopes.json"


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: manifest must be a JSON object")
    tooling = payload.get("development_tooling")
    if not isinstance(tooling, dict):
        raise ValueError(f"{path}: development_tooling must be an object")
    return payload


def is_development_tooling_path(path: str, manifest: dict[str, Any]) -> bool:
    tooling = manifest["development_tooling"]
    exact_paths = {str(value) for value in tooling.get("include_paths", [])}
    prefixes = tuple(
        str(value)
        for key in ("include_prefixes", "include_path_prefixes")
        for value in tooling.get(key, [])
    )
    return path in exact_paths or path.startswith(prefixes)


def classify_paths(paths: list[str], manifest: dict[str, Any]) -> dict[str, Any]:
    normalized = sorted({path.strip() for path in paths if path.strip()})
    non_tooling = [
        path for path in normalized if not is_development_tooling_path(path, manifest)
    ]
    return {
        "scope": "development_tooling" if normalized and not non_tooling else "product_or_mixed",
        "paths": normalized,
        "non_tooling_paths": non_tooling,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths-file", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    paths = args.paths_file.read_text(encoding="utf-8").splitlines()
    print(json.dumps(classify_paths(paths, manifest), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
