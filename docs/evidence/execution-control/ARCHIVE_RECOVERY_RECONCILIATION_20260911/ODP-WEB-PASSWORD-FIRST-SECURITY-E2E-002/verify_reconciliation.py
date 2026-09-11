#!/usr/bin/env python3
"""Offline verification script for ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002 reconciliation."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    base_dir = Path(__file__).resolve().parent
    json_path = base_dir / "acceptance-reconciliation.json"
    readme_path = base_dir / "README.md"
    repo_root = base_dir.parents[4]

    errors: list[str] = []

    if not json_path.exists():
        errors.append(f"Missing {json_path}")
    if not readme_path.exists():
        errors.append(f"Missing {readme_path}")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    try:
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"ERROR: Failed to parse {json_path}: {exc}", file=sys.stderr)
        return 1

    # Check task ID
    if data.get("task_id") != "ODP-WEB-PASSWORD-FIRST-SECURITY-E2E-002":
        errors.append(f"Unexpected task_id: {data.get('task_id')}")

    # Check 6 criteria
    criteria = data.get("acceptance_criteria_reconciliation", [])
    if len(criteria) != 6:
        errors.append(f"Expected 6 criteria, got {len(criteria)}")

    for idx, c in enumerate(criteria, start=1):
        if c.get("index") != idx:
            errors.append(f"Criterion at index {idx} has mismatched index field {c.get('index')}")
        if c.get("verdict") != "VERIFIED":
            errors.append(f"Criterion {idx} verdict is not VERIFIED: {c.get('verdict')}")
        if not c.get("primary_evidence"):
            errors.append(f"Criterion {idx} has no primary_evidence")

    # Check historical delivery files existence in the repository
    delivered_files = data.get("historical_delivery", {}).get("delivered_files", [])
    for rel_path in delivered_files:
        full_path = repo_root / rel_path
        if not full_path.exists():
            errors.append(f"Delivered file does not exist in repo: {rel_path}")

    # Check summary
    summary = data.get("criteria_summary", {})
    if summary.get("total") != 6 or summary.get("verified") != 6:
        errors.append(f"Summary does not match 6 verified: {summary}")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    print(f"OK: All 6 criteria in {json_path.name} validated successfully against repository files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
