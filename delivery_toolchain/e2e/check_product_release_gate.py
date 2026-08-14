#!/usr/bin/env python3
"""Fail-closed product E2E and release-registry gate.

This gate validates the maintained, deterministic product E2E packet.  It is
deliberately independent of the retired PR82 external-proof campaign: live
provider and staging evidence are release decisions recorded in the Gate 0-6
registry, not a second fleet of one-off checker scripts.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FILES = {
    "product runner": "delivery_toolchain/e2e/run_product_e2e.sh",
    "python acceptance runner": "delivery_toolchain/e2e/run_python_e2e_tests.py",
    "playwright result recorder": "delivery_toolchain/e2e/record_playwright_results.py",
    "E2E receipt library": "delivery_toolchain/e2e/product_e2e_receipt.py",
    "E2E receipt generator": "delivery_toolchain/e2e/generate_product_e2e_receipt.py",
    "release gate registry": "docs/evidence/gates/RELEASE_GATE_REGISTRY.json",
    "release gate registry checker": "delivery_toolchain/e2e/check_release_gate_registry.py",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dev-merge", action="store_true", help="Validate deterministic CI inputs; a registry NO-GO is valid.")
    mode.add_argument("--require-go", action="store_true", help="Require a human-authorized Gate 0-6 GO decision.")
    parser.add_argument("--expected-sha", help="Require registry evidence to bind to this exact release SHA.")
    return parser.parse_args()


def run_registry_check(args: argparse.Namespace) -> tuple[int, str]:
    command = [sys.executable, "delivery_toolchain/e2e/check_release_gate_registry.py"]
    if args.require_go:
        command.append("--require-go")
    if args.expected_sha:
        command.extend(("--expected-sha", args.expected_sha))
    result = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def validate_product_packet(*, validate_receipt: bool) -> list[str]:
    errors = [f"missing {label}: {relative_path}" for label, relative_path in REQUIRED_FILES.items() if not (ROOT / relative_path).is_file()]
    if errors:
        return errors

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from delivery_toolchain.e2e.product_e2e_receipt import (
        validate_acceptance_scenarios_and_inventory,
        validate_receipt_packet,
    )

    errors.extend(validate_acceptance_scenarios_and_inventory(ROOT))
    if validate_receipt:
        errors.extend(validate_receipt_packet(ROOT))
    return errors


def main() -> int:
    args = parse_args()
    errors = validate_product_packet(validate_receipt=args.require_go)
    registry_status, registry_output = run_registry_check(args)
    if registry_status:
        errors.append(registry_output or "release gate registry check failed")

    if errors:
        print("product release gate failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    if args.dev_merge:
        print("dev merge gate static checks passed")
    else:
        print("product release gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
