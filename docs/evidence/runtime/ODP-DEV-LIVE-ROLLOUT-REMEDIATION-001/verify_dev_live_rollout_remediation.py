#!/usr/bin/env python3
"""Verification script for ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001.

Validates the fail-closed evidence produced by the latest owner run:
1. Evidence files existence and schema structure.
2. Candidate drift detection integrity between the repository manifest and origin/dev.
3. Hosted build phase run 33844319992 and exact artifact digest syntax.
4. Rollback, lease-authority, and GCP-readback blockers are recorded without success claims.
5. Historical receipt immutability is represented by seven hashes.
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
EXPECTED_CURRENT_CANDIDATE = "1edb2f834cbf38ccd489cd999802098076e891b7"
EXPECTED_BUILD_RUN_ID = 33844319992
EXPECTED_RELEASE_ID = "odp-1edb2f834cbf"
EXPECTED_MANIFEST_DIGEST = "sha256:c9ff71c7557c4009487cf6e093280f47369db2fdd49a17ac8b9e1aa472e8c2c7"


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

    candidate_reconciliation = audit.get("candidate_reconciliation", {})
    if candidate_reconciliation.get("origin_dev_head_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("origin_dev_head_sha does not match the current owner-run candidate")
    if candidate_reconciliation.get("drift_status") != "diverged":
        errors.append("candidate drift must be recorded as diverged")

    build_exec = audit.get("hosted_build_execution", {})
    if build_exec.get("run_id") != EXPECTED_BUILD_RUN_ID:
        errors.append(f"hosted build run must be {EXPECTED_BUILD_RUN_ID}")
    if build_exec.get("release_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("hosted build release_sha does not match current candidate")
    if build_exec.get("result") != "success":
        errors.append("hosted build result must be success")
    if build_exec.get("release_id") != EXPECTED_RELEASE_ID:
        errors.append("hosted build release_id does not match current candidate")
    if build_exec.get("manifest_digest") != EXPECTED_MANIFEST_DIGEST:
        errors.append("hosted build manifest_digest does not match the current manifest")
    if build_exec.get("run_url") != f"https://github.com/alfloop-dev/odayplus/actions/runs/{EXPECTED_BUILD_RUN_ID}":
        errors.append("hosted build run_url does not match the expected run")
    if build_exec.get("handoff_manifest_published") is not True:
        errors.append("handoff_manifest_published must be true")
    if build_exec.get("image_handoff_published") is not True:
        errors.append("image_handoff_published must be true")

    # Check candidate reconciliation
    cand_rec = audit.get("candidate_reconciliation", {})
    if not SHA_PATTERN.fullmatch(cand_rec.get("authoritative_manifest_candidate_sha", "")):
        errors.append("authoritative_manifest_candidate_sha is not a valid 40-char SHA")
    if not SHA_PATTERN.fullmatch(cand_rec.get("origin_dev_head_sha", "")):
        errors.append("origin_dev_head_sha is not a valid 40-char SHA")

    # Check build execution images
    published_images = build_exec.get("published_images", {})
    for comp in ["api", "web", "worker", "scheduler"]:
        ref = published_images.get(comp, "")
        if not IMAGE_DIGEST_PATTERN.fullmatch(ref):
            errors.append(f"published_images[{comp}] '{ref}' does not match immutable digest pattern")

    if len(published_images) != 4:
        errors.append("published_images must contain exactly api, web, worker, and scheduler")
    if len(build_exec.get("signature_refs", [])) != 4:
        errors.append("hosted build must record four Cosign signature refs")
    if len(build_exec.get("sbom_refs", [])) != 4:
        errors.append("hosted build must record four SBOM refs")

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

    authorization = audit.get("authorization_state", {})
    for field in (
        "supervisor_lease_issued",
        "private_signing_key_available_to_worker",
        "admission_possible",
    ):
        if authorization.get(field) is not False:
            errors.append(f"authorization_state.{field} must be false")
    if not isinstance(audit.get("historical_receipts_sha256"), dict) or len(
        audit["historical_receipts_sha256"]
    ) != 7:
        errors.append("historical_receipts_sha256 must contain all seven historical receipts")

    live_state = audit.get("live_gcp_runtime_state", {})
    if live_state.get("current_readback_result") != "predeploy_target_absence_verified":
        errors.append("current GCP readback must record hosted pre-deploy target absence")
    if live_state.get("deployment_commands_run") is not False:
        errors.append("deployment_commands_run must be false")

    absence = live_state.get("target_absence_receipt", {})
    if absence.get("candidate_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("target absence readback must bind to the current candidate")
    targets = absence.get("api_service"), absence.get("web_service"), absence.get("migration_job"), absence.get("worker_job"), absence.get("scheduler_job")
    if len(targets) != 5 or any(not isinstance(target, dict) or target.get("exists") is not False for target in targets):
        errors.append("all five release targets must be explicitly absent in pre-deploy readback")

    source_posture = audit.get("source_posture", {})
    if source_posture.get("all_sources_disabled") is not True:
        errors.append("source posture must record all sources disabled")
    if source_posture.get("zero_credentials_present") is not True:
        errors.append("source posture must record zero provider credentials")
    if source_posture.get("total_sources_audited") != 16:
        errors.append("source posture must audit all 16 sources")
    if source_posture.get("egress_posture") != "default-deny":
        errors.append("source posture must record default-deny egress")

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
