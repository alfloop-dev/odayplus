#!/usr/bin/env python3
"""Verify product CI parallel lane results.

This script acts as the verification gate for the required 'product' aggregator job
in GitHub Actions CI. It inspects the `needs` payload from GitHub Actions to ensure:
1. `change-scope` succeeded.
2. If scope is 'development_tooling', product lanes may be skipped.
3. If scope is 'product_or_mixed', ALL required product lanes must exist and report
   'success'. Any failure, cancellation, missing lane, or unexpected skip fails closed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REQUIRED_PRODUCT_LANES = (
    "product-lint-unit",
    "product-db",
    "product-api-contract",
    "product-security",
    "product-node",
)


def parse_needs(raw_payload: str) -> dict[str, Any]:
    """Parse raw needs JSON into a dictionary."""
    if not raw_payload or not raw_payload.strip():
        raise ValueError("Needs payload is empty.")
    try:
        data = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse needs payload as JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Needs payload must be a JSON object, got {type(data).__name__}.")
    return data


def verify_product_lanes(
    needs_data: dict[str, Any],
    required_lanes: tuple[str, ...] = REQUIRED_PRODUCT_LANES,
) -> tuple[bool, list[str]]:
    """Verify that all required product lanes succeeded according to change scope.

    Returns (is_valid, error_messages).
    """
    errors: list[str] = []

    change_scope_data = needs_data.get("change-scope")
    if not isinstance(change_scope_data, dict):
        errors.append("Required job 'change-scope' is missing from CI needs context.")
        return False, errors

    scope_result = change_scope_data.get("result")
    if scope_result != "success":
        errors.append(
            f"Job 'change-scope' did not succeed (result: {scope_result!r})."
        )
        return False, errors

    outputs = change_scope_data.get("outputs") or {}
    scope = outputs.get("scope")
    if not scope:
        errors.append("Job 'change-scope' did not provide output 'scope'.")
        return False, errors

    if scope == "development_tooling":
        # Under development_tooling, product lanes are expected to be skipped.
        # However, if any lane was executed and failed/cancelled, fail closed.
        for lane in required_lanes:
            lane_data = needs_data.get(lane)
            if isinstance(lane_data, dict):
                lane_result = lane_data.get("result")
                if lane_result in ("failure", "cancelled"):
                    errors.append(
                        f"Lane '{lane}' reported '{lane_result}' during development_tooling scope."
                    )
        return len(errors) == 0, errors

    # Under product_or_mixed (or any non-tooling scope), ALL required lanes must succeed.
    for lane in required_lanes:
        if lane not in needs_data:
            errors.append(f"Required product lane '{lane}' is missing from needs context.")
            continue
        lane_data = needs_data[lane]
        if not isinstance(lane_data, dict):
            errors.append(f"Product lane '{lane}' data is invalid (got {type(lane_data).__name__}).")
            continue
        lane_result = lane_data.get("result")
        if lane_result == "success":
            continue
        elif lane_result == "failure":
            errors.append(f"Product lane '{lane}' failed.")
        elif lane_result == "cancelled":
            errors.append(f"Product lane '{lane}' was cancelled.")
        elif lane_result == "skipped":
            errors.append(
                f"Product lane '{lane}' was unexpectedly skipped for change scope '{scope}'."
            )
        else:
            errors.append(
                f"Product lane '{lane}' reported unexpected result: {lane_result!r}."
            )

    return len(errors) == 0, errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--needs",
        type=str,
        default=None,
        help="JSON string containing the GitHub Actions 'needs' context.",
    )
    parser.add_argument(
        "--needs-file",
        type=Path,
        default=None,
        help="Path to a JSON file containing the GitHub Actions 'needs' context.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    raw_payload: str | None = None

    if args.needs is not None:
        raw_payload = args.needs
    elif args.needs_file is not None:
        if not args.needs_file.exists():
            print(f"ERROR: Needs file does not exist: {args.needs_file}", file=sys.stderr)
            return 1
        raw_payload = args.needs_file.read_text(encoding="utf-8")
    elif "NEEDS_JSON" in os.environ:
        raw_payload = os.environ["NEEDS_JSON"]
    elif not sys.stdin.isatty():
        raw_payload = sys.stdin.read()

    if not raw_payload:
        print(
            "ERROR: No needs payload provided via --needs, --needs-file, NEEDS_JSON, or stdin.",
            file=sys.stderr,
        )
        return 1

    try:
        needs_data = parse_needs(raw_payload)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    scope = (
        (needs_data.get("change-scope") or {}).get("outputs") or {}
    ).get("scope", "unknown")

    ok, errors = verify_product_lanes(needs_data)

    print("=== Product CI Parallel Lane Verification ===")
    print(f"Detected Change Scope: {scope}")
    print(f"Required Product Lanes: {', '.join(REQUIRED_PRODUCT_LANES)}")

    if ok:
        if scope == "development_tooling":
            print(
                "[PASS] Development tooling change scope verified: product lanes "
                "safely bypassed."
            )
        else:
            print(
                f"[PASS] All {len(REQUIRED_PRODUCT_LANES)} product lanes succeeded "
                f"for scope '{scope}'."
            )
        return 0

    print("\n[FAIL] Product CI verification failed:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
