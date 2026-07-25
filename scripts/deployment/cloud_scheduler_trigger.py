#!/usr/bin/env python3
"""Validate and read redacted Cloud Scheduler trigger snapshots."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = (
    "schedule",
    "timeZone",
    "httpTarget.uri",
    "httpTarget.oauthToken.serviceAccountEmail",
    "httpTarget.oauthToken.scope",
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("scheduler snapshot must be a JSON object")
    return payload


def _value(payload: Mapping[str, Any], field: str) -> str:
    current: object = payload
    for part in field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError(f"scheduler snapshot is missing {field}")
        current = current[part]
    if not isinstance(current, str) or not current.strip():
        raise ValueError(f"scheduler snapshot field {field} must be non-empty")
    return current.strip()


def _exists(payload: Mapping[str, Any]) -> bool:
    return payload.get("exists") is not False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("validate", "write-absent", "exists", "field"),
    )
    parser.add_argument("--description", type=Path, required=True)
    parser.add_argument("--field")
    args = parser.parse_args()

    if args.command == "write-absent":
        args.description.write_text('{"exists": false}\n', encoding="utf-8")
        return 0

    payload = _load(args.description)
    if args.command == "exists":
        print("true" if _exists(payload) else "false")
        return 0
    if not _exists(payload):
        raise ValueError("scheduler trigger did not exist in the snapshot")
    if args.command == "validate":
        for field in REQUIRED_FIELDS:
            _value(payload, field)
        return 0
    if not args.field:
        raise ValueError("--field is required for field")
    print(_value(payload, args.field))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
