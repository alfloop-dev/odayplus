#!/usr/bin/env python3
"""Verification script for ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001.

Validates the fail-closed evidence produced against the current base:
1. Evidence files exist and the audit JSON carries the required structure.
2. The audit binds the same candidate SHA, manifest digest, component images and
   registry decision (go) as the canonical repository manifest and gate registry.
3. The six immutable hosted artifact IDs and preserved raw files match the audit hashes and sizes.
   The raw manifest-file hash is checked separately from its canonical logical manifest digest.
4. Authorization and gate clearance state are verified against the repository.
5. No deployment success is claimed anywhere.
6. The seven historical ODP-DEV-ROLLOUT-001 receipts recompute to the hashes the
   audit records (immutability is measured, not asserted).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = ROOT / "docs/evidence/runtime/ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001"
AUDIT_JSON = EVIDENCE_DIR / "live-runtime-reconciliation-audit.json"
README_MD = EVIDENCE_DIR / "README.md"
TRANSCRIPT_TXT = EVIDENCE_DIR / "live-readback-transcript.txt"
RELEASE_MANIFEST = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"
GATE_REGISTRY = ROOT / "docs/evidence/gates/RELEASE_GATE_REGISTRY.json"
HISTORICAL_DIR = ROOT / "docs/evidence/runtime/ODP-DEV-ROLLOUT-001"

SHA256_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_CURRENT_CANDIDATE = "a663d604831c4b9710dda9b7ca85ddbc193837e8"
EXPECTED_BUILD_RUN_ID = 36415981871
EXPECTED_RELEASE_ID = "odp-a663d604831c"
EXPECTED_MANIFEST_DIGEST = "sha256:6ed4f3a4c1506b5a99ac80d9e1e4eda544f90975f9553b6ef77581a3f21c5482"
EXPECTED_AUTHORIZATION_ID = "HUMANOPS-DEV-MIGRATION-20260928T120016Z"
EXPECTED_CI_RUN_ID = 36415981871
EXPECTED_HOSTED_ARTIFACT_IDS = {
    "initial-release-absence-readback-a663d604831c4b9710dda9b7ca85ddbc193837e8": 0,
    "release-environment-receipt-dev-build": 0,
    "release-npm-audit-receipt-dev": 0,
    "release-phase-receipt-dev-build": 0,
    "runtime-release-images-a663d604831c4b9710dda9b7ca85ddbc193837e8": 0,
    "runtime-release-manifest-a663d604831c4b9710dda9b7ca85ddbc193837e8": 0,
}
COMPONENTS = ("api", "web", "worker", "scheduler")
DEV_GATES = ("gate-0", "gate-1", "gate-4")
CLEARED_STATUSES = {"passed", "passed-with-deviation"}
HISTORICAL_FILES = (
    "README.md",
    "data-platform-dev-deployment.json",
    "dev-integration-readback.json",
    "dev-rollout-manifest-binding.json",
    "external-sources-provider-off-audit.json",
    "odayplus-dev-deployment.json",
    "release-receipts-index.json",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path, errors: list[str]) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - report any parse failure as evidence error
        errors.append(f"Failed to parse {path}: {exc}")
        return None


def _check_structure(audit: dict, errors: list[str]) -> None:
    for field in [
        "schema_version",
        "task_id",
        "audit_type",
        "release_status",
        "deployment_success_claimed",
        "historical_receipts_modified",
        "generated_at",
        "generated_by",
        "collection_baseline",
        "readback_window_utc",
        "candidate_reconciliation",
        "hosted_build_execution",
        "source_posture",
        "authorization_state",
        "live_gcp_runtime_state",
        "reconciliation_findings",
        "historical_receipts_sha256",
        "unblock_requirements",
        "superseded_unblock_requirements",
        "history",
    ]:
        if field not in audit:
            errors.append(f"audit JSON missing required field: {field}")


def _check_header(audit: dict, errors: list[str]) -> None:
    if audit.get("task_id") != "ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001":
        errors.append(f"Unexpected task_id: {audit.get('task_id')}")
    if audit.get("release_status") != "blocked":
        errors.append(f"Expected release_status='blocked', got {audit.get('release_status')}")
    if audit.get("deployment_success_claimed") is not False:
        errors.append("deployment_success_claimed must be false")
    if audit.get("historical_receipts_modified") is not False:
        errors.append("historical_receipts_modified must be false")
    window = audit.get("readback_window_utc", {})
    if (
        not (window.get("start_utc") and window.get("end_utc"))
        or window["start_utc"] > window["end_utc"]
    ):
        errors.append("readback_window_utc must record an ordered start/end pair")
    baseline = audit.get("collection_baseline", {})
    if not SHA_PATTERN.fullmatch(str(baseline.get("origin_dev_head_sha", ""))):
        errors.append("collection_baseline.origin_dev_head_sha is not a valid 40-char SHA")


def _check_candidate(audit: dict, manifest: dict, registry: dict, errors: list[str]) -> None:
    cand = audit.get("candidate_reconciliation", {})
    release = registry.get("release", {})
    if cand.get("authoritative_manifest_candidate_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("authoritative_manifest_candidate_sha does not match the canonical candidate")
    if cand.get("authoritative_manifest_digest") != EXPECTED_MANIFEST_DIGEST:
        errors.append("authoritative_manifest_digest does not match the canonical manifest digest")
    if cand.get("release_id") != EXPECTED_RELEASE_ID or manifest.get("release_id") != EXPECTED_RELEASE_ID:
        errors.append("release_id differs between audit, manifest and expectation")
    if manifest.get("candidate_sha") != cand.get("authoritative_manifest_candidate_sha"):
        errors.append("repository RELEASE_MANIFEST.json candidate_sha differs from the audit")
    if manifest.get("manifest_digest") != cand.get("authoritative_manifest_digest"):
        errors.append("repository RELEASE_MANIFEST.json manifest_digest differs from the audit")

    registry_manifest_digest = release.get("manifest_digest", "")
    if registry_manifest_digest and registry_manifest_digest != cand.get("authoritative_manifest_digest"):
        errors.append("repository registry release.manifest_digest differs from the audit")
    if release.get("candidate_sha") and release.get("candidate_sha") != cand.get("authoritative_manifest_candidate_sha"):
        errors.append("repository registry release.candidate_sha differs from the audit")
    if release.get("decision") != "go":
        errors.append("registry decision must be go")
    if cand.get("registry_decision") and cand.get("registry_decision") != "go":
        errors.append("this round must record registry decision go")

    # Cross-binding: component images
    cand_images = cand.get("component_images", {})
    manifest_components = manifest.get("components", {})
    for comp in COMPONENTS:
        expected_img = manifest_components.get(comp, {}).get("image")
        actual_img = cand_images.get(comp)
        if actual_img != expected_img:
            errors.append(f"candidate_reconciliation component_images[{comp}] ({actual_img}) != manifest component image ({expected_img})")

    # Initial release recovery bindings
    cand_recovery = cand.get("manifest_initial_release_recovery", {})
    manifest_recovery = manifest.get("initial_release_recovery", {})
    if cand_recovery.get("binding_digest") != manifest_recovery.get("binding_digest"):
        errors.append("candidate_reconciliation manifest_initial_release_recovery.binding_digest != manifest binding_digest")

    # Candidate rebind
    cand_rebind = cand.get("candidate_rebind", {})
    if cand_rebind.get("to_candidate_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("candidate_rebind.to_candidate_sha does not match expected candidate")
    if cand_rebind.get("to_manifest_digest") != EXPECTED_MANIFEST_DIGEST:
        errors.append("candidate_rebind.to_manifest_digest does not match expected manifest digest")
    if cand_rebind.get("build_run", {}).get("run_id") != EXPECTED_BUILD_RUN_ID:
        errors.append("candidate_rebind.build_run.run_id does not match expected build run")


def _check_build(audit: dict, manifest: dict, errors: list[str]) -> None:
    build_exec = audit.get("hosted_build_execution", {})
    if build_exec.get("run_id") != EXPECTED_BUILD_RUN_ID:
        errors.append(f"hosted build run must be {EXPECTED_BUILD_RUN_ID}")
    if build_exec.get("release_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("hosted build release_sha does not match current candidate")
    if build_exec.get("release_id") != EXPECTED_RELEASE_ID:
        errors.append(f"hosted build release_id must be {EXPECTED_RELEASE_ID}, got {build_exec.get('release_id')}")
    if build_exec.get("manifest_digest") != EXPECTED_MANIFEST_DIGEST:
        errors.append(f"hosted build manifest_digest must be {EXPECTED_MANIFEST_DIGEST}, got {build_exec.get('manifest_digest')}")
    conclusion = build_exec.get("conclusion") or build_exec.get("result")
    if conclusion != "success":
        errors.append("hosted build conclusion must be success")
    if (
        build_exec.get("run_url")
        != f"https://github.com/alfloop-dev/odayplus/actions/runs/{EXPECTED_BUILD_RUN_ID}"
    ):
        errors.append("hosted build run_url does not match the expected run")

    # Cross-binding: published images vs manifest components
    pub_images = build_exec.get("published_images", {})
    manifest_components = manifest.get("components", {})
    for comp in COMPONENTS:
        expected_img = manifest_components.get(comp, {}).get("image")
        actual_img = pub_images.get(comp)
        if actual_img != expected_img:
            errors.append(f"hosted_build_execution published_images[{comp}] ({actual_img}) != manifest component image ({expected_img})")

    # Cross-binding: signature refs
    pub_sigs = sorted(build_exec.get("signature_refs", []))
    manifest_sigs = sorted(manifest.get("signature_refs", []))
    if pub_sigs != manifest_sigs:
        errors.append("hosted_build_execution signature_refs do not match manifest signature_refs")

    # Cross-binding: sbom refs
    pub_sboms = sorted(build_exec.get("sbom_refs", []))
    manifest_sboms = sorted(manifest.get("sbom_refs", []))
    if pub_sboms != manifest_sboms:
        errors.append("hosted_build_execution sbom_refs do not match manifest sbom_refs")

    # Job execution validation
    jobs = build_exec.get("jobs", [])
    job_map = {j["name"]: j for j in jobs}
    build_job = job_map.get("Build once and publish the immutable artifact handoff")
    if not build_job or build_job.get("conclusion") != "success":
        errors.append("Build job must exist and have conclusion 'success'")

    lease_job = job_map.get("Verify the Supervisor lease authorises this deploy")
    if not lease_job or lease_job.get("conclusion") != "skipped":
        errors.append("Lease verification job must exist and be 'skipped' for build-only phase")

    deploy_job = job_map.get("Deploy the admitted artifact by immutable digest")
    if not deploy_job or deploy_job.get("conclusion") != "skipped":
        errors.append("Deploy job must exist and be 'skipped' for build-only phase")


def _check_hosted_artifacts(audit: dict, manifest: dict, errors: list[str]) -> None:
    build_exec = audit.get("hosted_build_execution", {})
    records = build_exec.get("uploaded_artifacts", [])
    by_name = {record.get("name"): record for record in records if isinstance(record, dict)}
    if len(records) != len(EXPECTED_HOSTED_ARTIFACT_IDS) or set(by_name) != set(EXPECTED_HOSTED_ARTIFACT_IDS):
        errors.append("hosted artifact inventory names do not match the six immutable run artifacts")

    raw_manifest_bytes = RELEASE_MANIFEST.read_bytes()
    repository_manifest = audit.get("candidate_reconciliation", {}).get("repository_manifest", {})
    actual_manifest_sha = hashlib.sha256(raw_manifest_bytes).hexdigest()
    if repository_manifest.get("raw_sha256") != actual_manifest_sha:
        errors.append("repository_manifest.raw_sha256 does not match the raw RELEASE_MANIFEST.json bytes")

    for name, expected_id in EXPECTED_HOSTED_ARTIFACT_IDS.items():
        record = by_name.get(name)
        if record is None:
            errors.append(f"hosted artifact record missing: {name}")
            continue
        if type(record.get("id")) is not int or record.get("id") != expected_id:
            errors.append(f"hosted artifact {name} has the wrong immutable artifact ID")
        if record.get("hash_scope") != "expanded artifact JSON file bytes":
            errors.append(f"hosted artifact {name} does not identify its raw hash scope")
        relative = Path(str(record.get("evidence_file") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"hosted artifact {name} has an unsafe evidence_file path")
            continue
        artifact_path = EVIDENCE_DIR / relative
        if not artifact_path.is_file():
            errors.append(f"hosted artifact raw file is missing: {name}")
            continue
        content = artifact_path.read_bytes()
        raw_sha = hashlib.sha256(content).hexdigest()
        if record.get("raw_sha256") != raw_sha:
            errors.append(f"hosted artifact {name} raw_sha256 does not match its preserved file")
        if record.get("raw_bytes") != len(content):
            errors.append(f"hosted artifact {name} raw_bytes does not match its preserved file")
        if not isinstance(record.get("archive_size_in_bytes"), int) or record["archive_size_in_bytes"] <= 0:
            errors.append(f"hosted artifact {name} has no valid GitHub archive size")

        if name.startswith("runtime-release-manifest-"):
            try:
                hosted_manifest = json.loads(content.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed artifact is evidence failure
                errors.append(f"hosted manifest artifact is not valid JSON: {exc}")
                continue
            if content != raw_manifest_bytes:
                errors.append("hosted manifest artifact is not byte-identical to repository RELEASE_MANIFEST.json")
            if repository_manifest.get("hosted_manifest_artifact_id") != expected_id:
                errors.append("repository_manifest hosted artifact ID does not match the hosted artifact record")
            if repository_manifest.get("byte_identical_to_hosted_artifact") is not True:
                errors.append("repository_manifest must assert verified byte identity with the hosted artifact")
            if hosted_manifest.get("manifest_digest") != EXPECTED_MANIFEST_DIGEST:
                errors.append("hosted manifest logical manifest_digest does not match the authorized release")
            if hosted_manifest.get("manifest_digest") != manifest.get("manifest_digest"):
                errors.append("hosted manifest logical digest differs from the repository manifest field")
            if repository_manifest.get("logical_manifest_digest") != hosted_manifest.get("manifest_digest"):
                errors.append("repository_manifest logical digest differs from the hosted manifest field")
            payload = dict(hosted_manifest)
            payload.pop("manifest_digest", None)
            canonical_payload = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            computed_logical_digest = "sha256:" + hashlib.sha256(canonical_payload).hexdigest()
            if computed_logical_digest != hosted_manifest.get("manifest_digest"):
                errors.append("hosted manifest logical digest does not recompute from canonical JSON")

    readback_source = audit.get("live_gcp_runtime_state", {}).get("readback_source", {})
    if readback_source.get("run_id") == build_exec.get("run_id"):
        absence_records = [
            record
            for record in records
            if str(record.get("name") or "").startswith("initial-release-absence-readback-")
        ]
        if len(absence_records) != 1:
            errors.append("the build run must contain exactly one initial-release absence artifact")
        elif readback_source.get("sha256") != absence_records[0].get("raw_sha256"):
            errors.append(
                "live_gcp_runtime_state.readback_source.sha256 does not match the raw "
                "initial-release absence artifact for the same build run"
            )

def _check_sources(audit: dict, manifest: dict, errors: list[str]) -> None:
    source_posture = audit.get("source_posture", {})
    attestation = manifest.get("sources_off_attestation", {})
    inventory = attestation.get("sources_inventory", [])
    if source_posture.get("all_sources_disabled") is not True or any(
        s.get("status") != "disabled" for s in inventory
    ):
        errors.append("source posture must record all sources disabled and the manifest must agree")
    if source_posture.get("zero_credentials_present") is not True or any(
        s.get("credentials_present") is not False for s in inventory
    ):
        errors.append("source posture must record zero provider credentials; manifest must agree")
    if source_posture.get("total_sources_audited") != 16 or len(inventory) != 16:
        errors.append("source posture must audit all 16 sources")
    if (
        source_posture.get("egress_posture") != "default-deny"
        or attestation.get("egress_posture") != "default-deny"
    ):
        errors.append("source posture must record default-deny egress")
    if source_posture.get("binding_digest") != attestation.get("binding_digest"):
        errors.append("source posture binding_digest does not match manifest sources_off_attestation.binding_digest")


def _check_authorization(audit: dict, registry: dict, errors: list[str]) -> None:
    authorization = audit.get("authorization_state", {})
    if authorization.get("supervisor_lease_issued") is not False:
        errors.append("authorization_state.supervisor_lease_issued must be false")
    if authorization.get("canonical_registry_decision") != "go":
        errors.append("canonical_registry_decision must be go")

    # Verify dev admission gates match registry
    dev_gates = authorization.get("dev_admission_gates", {})
    registry_gates = {g["id"]: g for g in registry.get("gates", []) if g.get("id") in DEV_GATES}
    for gate_id in DEV_GATES:
        audit_gate = dev_gates.get(gate_id, {})
        actual = registry_gates.get(gate_id, {})
        if actual.get("status") not in CLEARED_STATUSES:
            errors.append(f"{gate_id} is not cleared in the repository registry")
        if audit_gate.get("status") and audit_gate["status"] != actual.get("status"):
            errors.append(f"{gate_id} status in audit differs from registry")

    # Verify authorization binding
    auth_rec = authorization.get("human_authorization", {})
    if auth_rec.get("approval_id") != EXPECTED_AUTHORIZATION_ID:
        errors.append(f"human_authorization.approval_id must be {EXPECTED_AUTHORIZATION_ID}")
    if auth_rec.get("candidate_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("human_authorization.candidate_sha does not match current candidate")
    if auth_rec.get("manifest_digest") != EXPECTED_MANIFEST_DIGEST:
        errors.append("human_authorization.manifest_digest does not match current manifest digest")

    # Verify current release lease request
    req = authorization.get("current_release_lease_request", {})
    if req.get("approval_id") != EXPECTED_AUTHORIZATION_ID:
        errors.append(f"current_release_lease_request.approval_id must be {EXPECTED_AUTHORIZATION_ID}")
    if req.get("candidate_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("current_release_lease_request.candidate_sha does not match current candidate")
    if req.get("manifest_digest") != EXPECTED_MANIFEST_DIGEST:
        errors.append("current_release_lease_request.manifest_digest does not match current manifest digest")

    # Verify latest issuance decision
    issuance = authorization.get("latest_issuance_decision", {})
    if issuance.get("state") != "blocked":
        errors.append("latest_issuance_decision.state must be 'blocked'")
    if issuance.get("event") != "lease_issue_blocked":
        errors.append("latest_issuance_decision.event must be 'lease_issue_blocked'")


def _check_live_state(audit: dict, errors: list[str]) -> None:
    live_state = audit.get("live_gcp_runtime_state", {})
    if (
        live_state.get("deployment_commands_run") is not False
        or live_state.get("traffic_switch_run") is not False
    ):
        errors.append("deployment_commands_run and traffic_switch_run must be false")

    target_absence = live_state.get("target_absence_receipt", {})
    if target_absence.get("candidate_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("target_absence_receipt.candidate_sha does not match current candidate")
    if target_absence.get("migration_job", {}).get("name") != "oday-migration-r-a663d604831c":
        errors.append("target_absence_receipt.migration_job name must be 'oday-migration-r-a663d604831c'")
    if target_absence.get("worker_job", {}).get("name") != "oday-worker-r-a663d604831c":
        errors.append("target_absence_receipt.worker_job name must be 'oday-worker-r-a663d604831c'")
    if target_absence.get("scheduler_job", {}).get("name") != "oday-scheduler-r-a663d604831c":
        errors.append("target_absence_receipt.scheduler_job name must be 'oday-scheduler-r-a663d604831c'")


def _check_findings(audit: dict, errors: list[str]) -> None:
    findings = audit.get("reconciliation_findings", [])
    if len(findings) < 10:
        errors.append(f"Expected at least 10 reconciliation findings, got {len(findings)}")
    statuses = [f.get("status") for f in findings]
    if "fail_closed" not in statuses:
        errors.append("a fail_closed finding must be present")


def _check_history(audit: dict, errors: list[str]) -> None:
    recorded = audit.get("historical_receipts_sha256", {})
    if sorted(recorded) != sorted(HISTORICAL_FILES):
        errors.append("historical_receipts_sha256 must contain all seven historical receipts")
    for name in HISTORICAL_FILES:
        path = HISTORICAL_DIR / name
        if not path.is_file():
            errors.append(f"historical receipt missing: {path}")
            continue
        if recorded.get(name) != _sha256(path):
            errors.append(f"historical receipt {name} no longer matches the recorded sha256")
    history = audit.get("history", {})
    for key in ("round_2026_09_21", "hosted_build_execution_2026_09_04"):
        if key not in history:
            errors.append(f"history must retain {key}")


def _check_readme(errors: list[str]) -> None:
    readme = README_MD.read_text(encoding="utf-8")
    if EXPECTED_CURRENT_CANDIDATE not in readme:
        errors.append(f"README does not mention candidate {EXPECTED_CURRENT_CANDIDATE}")
    transcript = TRANSCRIPT_TXT.read_text(encoding="utf-8")
    if not transcript:
        errors.append("transcript text is empty")


def verify_evidence_bundle() -> list[str]:
    errors: list[str] = []
    for path, label in [
        (AUDIT_JSON, "audit JSON"),
        (README_MD, "README markdown"),
        (TRANSCRIPT_TXT, "transcript text"),
        (RELEASE_MANIFEST, "repository release manifest"),
        (GATE_REGISTRY, "repository gate registry"),
    ]:
        if not path.is_file():
            errors.append(f"Missing {label} file: {path}")
    if errors:
        return errors

    audit = _load_json(AUDIT_JSON, errors)
    manifest = _load_json(RELEASE_MANIFEST, errors)
    registry = _load_json(GATE_REGISTRY, errors)
    if errors or audit is None or manifest is None or registry is None:
        return errors
    _check_structure(audit, errors)
    if errors:
        return errors
    _check_header(audit, errors)
    _check_candidate(audit, manifest, registry, errors)
    _check_build(audit, manifest, errors)
    _check_hosted_artifacts(audit, manifest, errors)
    _check_sources(audit, manifest, errors)
    _check_authorization(audit, registry, errors)
    _check_live_state(audit, errors)
    _check_findings(audit, errors)
    _check_history(audit, errors)
    _check_readme(errors)
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
