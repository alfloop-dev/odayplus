#!/usr/bin/env python3
"""Collect this checkout's generated E2E evidence without archiving old receipts.

The three canonical evidence files are tracked historical inputs as well as
runner outputs. A bootstrap failure must not upload those historical inputs as
this run's evidence. Preserve exact-source outputs byte for byte, including
failed results for diagnosis, and fail a successful runner's collection if its
packet is missing or invalid. Collection never grants live/release admission.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.e2e.product_e2e_receipt import (
    RAW_PLAYWRIGHT_PATH,
    RAW_PYTEST_PATH,
    RECEIPT_PATH,
    SCHEMA_VERSION,
    iso_now,
    normalized_hash,
    sha256_bytes,
    source_identity,
    validate_raw_artifact,
    validate_receipt_packet,
)


def collect_evidence(root: Path, output: Path, *, expect_complete: bool = False) -> dict[str, Any]:
    if output.exists() and any(output.iterdir()):
        raise ValueError("Evidence output directory must be empty; refusing stale files")
    output.mkdir(parents=True, exist_ok=True)
    source = source_identity(root)
    files: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []
    errors: list[str] = []
    for relative, runner in (
        (RAW_PLAYWRIGHT_PATH, "playwright"),
        (RAW_PYTEST_PATH, "pytest"),
        (RECEIPT_PATH, "receipt"),
    ):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            omitted.append({"path": relative, "reason": "missing or non-regular output"})
            continue
        raw = path.read_bytes()
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            omitted.append({"path": relative, "reason": "malformed JSON"})
            continue
        source_field = "tested_source" if runner == "receipt" else "source"
        if not isinstance(payload, dict) or payload.get(source_field) != source:
            omitted.append({"path": relative, "reason": "not this checkout's source/tree"})
            continue
        if runner == "receipt":
            validation_errors = []
            if payload.get("schema_version") != SCHEMA_VERSION:
                validation_errors.append("receipt schema mismatch")
            if payload.get("normalized_receipt_sha256") != normalized_hash(
                payload, "normalized_receipt_sha256"
            ):
                validation_errors.append("receipt normalized hash mismatch")
            if expect_complete and (
                payload.get("status") != "passed"
                or payload.get("exit_code") != 0
                or payload.get("validation_errors") != []
            ):
                validation_errors.append("receipt does not record a passing execution")
        else:
            validation_errors = validate_raw_artifact(
                payload, runner, require_success=expect_complete
            )
        destination = output / path.name
        destination.write_bytes(raw)
        files.append(
            {
                "path": relative,
                "archive_path": path.name,
                "sha256": sha256_bytes(raw),
                "bytes": len(raw),
                "validation_errors": validation_errors,
            }
        )
        errors.extend(f"{path.name}: {error}" for error in validation_errors)
    if expect_complete:
        errors.extend(f"{item['path']}: {item['reason']}" for item in omitted)
        if not omitted:
            errors.extend(validate_receipt_packet(root, allow_worktree_evidence=True))
    manifest = {
        "kind": "odayplus.ci-product-e2e-evidence",
        "collected_at": iso_now(),
        "checkout_source": source,
        "expected_complete_passing_packet": expect_complete,
        "files": files,
        "omitted": omitted,
        "validation_errors": errors,
        "full_product_acceptance_claimed": False,
        "production_admission_claimed": False,
    }
    (output / "collection.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect-complete", action="store_true")
    args = parser.parse_args()
    manifest = collect_evidence(ROOT, args.output, expect_complete=args.expect_complete)
    print(
        f"E2E evidence: {len(manifest['files'])} current-source files, "
        f"{len(manifest['omitted'])} omitted, "
        f"{len(manifest['validation_errors'])} validation errors"
    )
    return int(args.expect_complete and bool(manifest["validation_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
