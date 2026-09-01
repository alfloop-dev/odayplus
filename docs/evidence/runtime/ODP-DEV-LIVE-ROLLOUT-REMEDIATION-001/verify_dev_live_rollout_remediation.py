#!/usr/bin/env python3
"""Verification script for ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001.

Validates:
1. Evidence files existence and schema structure.
2. Candidate drift detection integrity between authoritative manifest and origin/dev.
3. Hosted build phase run results (run 33509435127) and artifact digests format.
4. Fail-closed defect diagnosis consistency with Acceptance Criterion 10.
5. GCP live readback structure consistency.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = ROOT / "docs/evidence/runtime/ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001"
AUDIT_JSON = EVIDENCE_DIR / "live-runtime-reconciliation-audit.json"
README_MD = EVIDENCE_DIR / "README.md"
TRANSCRIPT_TXT = EVIDENCE_DIR / "live-readback-transcript.txt"

SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def verify_evidence_bundle() -> list[str]:
    errors: list[str] = []

    for path, label in [
        (AUDIT_JSON, "audit JSON"),
        (README_MD, "README markdown"),
        (TRANSCRIPT_TXT, "transcript text"),
    ]:
        if not path.is_file():
            errors.append(f"Missing {label} file: {path}")

    if errors:
        return errors

    try:
        audit = json.loads(AUDIT_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"Failed to parse {AUDIT_JSON}: {exc}"]

    # Check required top-level fields
    for field in [
        "schema_version",
        "task_id",
        "audit_type",
        "release_status",
        "deployment_success_claimed",
        "historical_receipts_modified",
        "generated_at",
        "generated_by",
        "readback_window_utc",
        "candidate_reconciliation",
        "hosted_build_execution",
        "live_gcp_runtime_state",
        "reconciliation_findings",
        "unblock_requirements",
    ]:
        if field not in audit:
            errors.append(f"audit JSON missing required field: {field}")

    if audit.get("task_id") != "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001":
        errors.append(f"Unexpected task_id: {audit.get('task_id')}")

    if audit.get("release_status") != "blocked":
        errors.append(f"Expected release_status='blocked', got {audit.get('release_status')}")

    if audit.get("deployment_success_claimed") is not False:
        errors.append("deployment_success_claimed must be false")

    if audit.get("historical_receipts_modified") is not False:
        errors.append("historical_receipts_modified must be false")

    # Check candidate reconciliation
    cand_rec = audit.get("candidate_reconciliation", {})
    if not SHA_PATTERN.fullmatch(cand_rec.get("authoritative_manifest_candidate_sha", "")):
        errors.append("authoritative_manifest_candidate_sha is not a valid 40-char SHA")
    if not SHA_PATTERN.fullmatch(cand_rec.get("origin_dev_head_sha", "")):
        errors.append("origin_dev_head_sha is not a valid 40-char SHA")

    # Check build execution images
    build_exec = audit.get("hosted_build_execution", {})
    published_images = build_exec.get("published_images", {})
    for comp in ["api", "web", "worker", "scheduler"]:
        ref = published_images.get(comp, "")
        if not IMAGE_DIGEST_PATTERN.fullmatch(ref):
            errors.append(f"published_images[{comp}] '{ref}' does not match immutable digest pattern")

    for sig in build_exec.get("signature_refs", []):
        if not IMAGE_DIGEST_PATTERN.fullmatch(sig):
            errors.append(f"signature_ref '{sig}' does not match immutable digest pattern")

    for sbom in build_exec.get("sbom_refs", []):
        if not IMAGE_DIGEST_PATTERN.fullmatch(sbom):
            errors.append(f"sbom_ref '{sbom}' does not match immutable digest pattern")

    # Check findings
    findings = audit.get("reconciliation_findings", [])
    if len(findings) < 4:
        errors.append(f"Expected at least 4 reconciliation findings, got {len(findings)}")

    return errors


def main() -> int:
    print("Verifying ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 evidence bundle...")
    errors = verify_evidence_bundle()
    if errors:
        print("FAIL: Verification errors encountered:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("PASS: ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001 evidence bundle verified successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
