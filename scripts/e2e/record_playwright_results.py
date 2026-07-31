#!/usr/bin/env python3
"""Wrap a Playwright JSON report with immutable runner/source metadata."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e2e.product_e2e_receipt import (
    RAW_PLAYWRIGHT_PATH,
    SCHEMA_VERSION,
    canonical_json_bytes,
    parse_playwright_payload,
    seal_normalized,
    sha256_bytes,
)


def build_artifact(
    payload: object,
    *,
    source_sha: str,
    tree_sha: str,
    command: str,
    version: str,
    started_at: str,
    ended_at: str,
    exit_code: int,
    environment: dict[str, str],
) -> dict[str, object]:
    results, counts, integrity_errors = parse_playwright_payload(payload)
    artifact: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "runner": "playwright",
        "source": {
            "commit_sha": source_sha,
            "tree_sha": tree_sha,
        },
        "run": {
            "command": command,
            "version": version,
            "started_at": started_at,
            "ended_at": ended_at,
            "exit_code": exit_code,
            "environment": environment,
        },
        "payload": payload,
        "payload_sha256": sha256_bytes(canonical_json_bytes(payload)),
        "counts": counts,
        "results": results,
        "integrity_errors": integrity_errors,
    }
    return seal_normalized(artifact, "normalized_artifact_sha256")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / RAW_PLAYWRIGHT_PATH)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--tree-sha", required=True)
    parser.add_argument("--command", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--ended-at", required=True)
    parser.add_argument("--exit-code", required=True, type=int)
    parser.add_argument("--project", default="chromium")
    parser.add_argument("--workers", default="1")
    parser.add_argument("--retries", default="0")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = json.loads(args.payload.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Playwright JSON payload is missing or malformed: {exc}") from exc
    artifact = build_artifact(
        payload,
        source_sha=args.source_sha,
        tree_sha=args.tree_sha,
        command=args.command,
        version=args.version,
        started_at=args.started_at,
        ended_at=args.ended_at,
        exit_code=args.exit_code,
        environment={
            "project": args.project,
            "workers": args.workers,
            "retries": args.retries,
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    integrity_errors = artifact["integrity_errors"]
    if args.exit_code != 0 or integrity_errors:
        return args.exit_code or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
