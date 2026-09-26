#!/usr/bin/env python3
"""Verification script for ODP-DEV-LIVE-ROLLOUT-REMEDIATION-001.

Validates the fail-closed evidence produced against the current base:
1. Evidence files exist and the audit JSON carries the required structure.
2. The audit binds the same candidate SHA, manifest digest, component images and
   registry decision (go) as the canonical repository manifest and gate registry.
3. The hosted build run and artifact digests are syntactically immutable.
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
EXPECTED_CURRENT_CANDIDATE = "419e6bf4958269c5b9e94efcb80770e28cd54dda"
EXPECTED_BUILD_RUN_ID = 36080312679
EXPECTED_RELEASE_ID = "odp-419e6bf49582"
EXPECTED_MANIFEST_DIGEST = "sha256:134cc712132155b0003d68063298d3044d5400d91244b8024b4448268c4fc678"
EXPECTED_MANIFEST_ARTIFACT_ID = 10840909143
EXPECTED_CI_RUN_ID = 36034118291
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
    if audit.get("generated_at") != window.get("end_utc"):
        errors.append("generated_at must equal the readback window end")
    baseline = audit.get("collection_baseline", {})
    for key in ("origin_dev_head_sha", "task_base_merge_sha", "task_base_merge_tree"):
        if not SHA_PATTERN.fullmatch(str(baseline.get(key, ""))):
            errors.append(f"collection_baseline.{key} is not a valid 40-char SHA")
    parents = baseline.get("task_base_merge_parents", [])
    if len(parents) != 2 or parents[1] != baseline.get("origin_dev_head_sha"):
        errors.append("task_base_merge_parents must name origin/dev as the second parent")
    if baseline.get("merge_tree_precheck_equal") is not True:
        errors.append("merge tree must be recorded as equal to the merge-tree precheck")
    if baseline.get("forbidden_path_diff_vs_origin_dev_empty") is not True:
        errors.append("forbidden path diff must be recorded as empty")


def _check_candidate(audit: dict, manifest: dict, registry: dict, errors: list[str]) -> None:
    cand = audit.get("candidate_reconciliation", {})
    release = registry.get("release", {})
    if cand.get("authoritative_manifest_candidate_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("authoritative_manifest_candidate_sha does not match the canonical candidate")
    if cand.get("authoritative_manifest_digest") != EXPECTED_MANIFEST_DIGEST:
        errors.append("authoritative_manifest_digest does not match the canonical manifest digest")
    if cand.get("release_id") != EXPECTED_RELEASE_ID or manifest.get("release_id") != (
        EXPECTED_RELEASE_ID
    ):
        errors.append("release_id differs between audit, manifest and expectation")
    if manifest.get("candidate_sha") != cand.get("authoritative_manifest_candidate_sha"):
        errors.append("repository RELEASE_MANIFEST.json candidate_sha differs from the audit")
    if manifest.get("manifest_digest") != cand.get("authoritative_manifest_digest"):
        errors.append("repository RELEASE_MANIFEST.json manifest_digest differs from the audit")
    if release.get("candidate_sha") != cand.get("authoritative_manifest_candidate_sha"):
        errors.append("repository registry release.candidate_sha differs from the audit")
    if release.get("manifest_digest") != cand.get("authoritative_manifest_digest"):
        errors.append("repository registry release.manifest_digest differs from the audit")
    if release.get("decision") != cand.get("registry_decision"):
        errors.append("registry decision recorded in the audit differs from the repository")
    if cand.get("registry_decision") != "go":
        errors.append("this round must record registry decision go (it was measured as go)")
    signoff = release.get("human_signoff", {})
    if not signoff.get("approver") or not signoff.get("date"):
        errors.append("registry release.human_signoff must name an approver and a date")
    if cand.get("registry_human_signoff") != signoff:
        errors.append("registry_human_signoff recorded in the audit differs from the repository")
    if release.get("admission_target") != "dev" or cand.get("registry_admission_target") != "dev":
        errors.append("registry admission_target must be dev")
    if not SHA_PATTERN.fullmatch(str(cand.get("origin_dev_head_sha", ""))):
        errors.append("origin_dev_head_sha is not a valid 40-char SHA")
    if cand.get("candidate_is_ancestor_of_origin_dev") is not True:
        errors.append("candidate must be recorded as an ancestor of origin/dev")
    if cand.get("drift_status") != "evidence_only_descendant":
        errors.append("drift_status must be evidence_only_descendant for a current candidate")
    if cand.get("non_evidence_paths_changed") != []:
        errors.append("non_evidence_paths_changed must be empty when no rebuild is claimed")
    if any(not p.startswith("docs/evidence/") for p in cand.get("paths_changed_between", [])):
        errors.append("every path between candidate and origin/dev must be under docs/evidence/")
    if (
        cand.get("candidate_must_rebuild") is not False
        or cand.get("old_artifacts_reused") is not False
    ):
        errors.append("candidate_must_rebuild and old_artifacts_reused must both be false")
    repo_manifest = cand.get("repository_manifest", {})
    if repo_manifest.get("raw_sha256") != _sha256(RELEASE_MANIFEST):
        errors.append("repository_manifest.raw_sha256 does not match the repository manifest bytes")
    if repo_manifest.get("hosted_manifest_artifact_id") != EXPECTED_MANIFEST_ARTIFACT_ID:
        errors.append("hosted_manifest_artifact_id must be the run's manifest artifact")
    if repo_manifest.get("byte_identical_to_hosted_artifact") is not True:
        errors.append("repository manifest must be recorded as byte-identical to the artifact")
    rebind = registry.get("candidate_rebind", {})
    if rebind.get("to_candidate_sha") != EXPECTED_CURRENT_CANDIDATE or (
        rebind.get("build_run", {}).get("run_id") != EXPECTED_BUILD_RUN_ID
    ):
        errors.append("registry candidate_rebind must bind the candidate to the expected build run")
    if cand.get("candidate_rebind", {}).get("build_run") != rebind.get("build_run"):
        errors.append("candidate_rebind.build_run in the audit differs from the repository")


def _check_build(audit: dict, manifest: dict, errors: list[str]) -> None:
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
    if (
        build_exec.get("run_url")
        != f"https://github.com/alfloop-dev/odayplus/actions/runs/{EXPECTED_BUILD_RUN_ID}"
    ):
        errors.append("hosted build run_url does not match the expected run")
    if build_exec.get("dispatched_by_this_task") is not False:
        errors.append("this round must not claim to have dispatched the canonical build")
    if build_exec.get("single_run_build_and_handoff") is not True:
        errors.append("this candidate was built and handed off in a single run")
    for flag in (
        "handoff_manifest_published",
        "image_handoff_published",
        "initial_release_absence_readback_published",
    ):
        if build_exec.get(flag) is not True:
            errors.append(f"{flag} must be true")
    gated_job_names = {
        "Verify the Supervisor lease authorises this deploy",
        "Deploy the admitted artifact by immutable digest",
    }
    gated_jobs = [job for job in build_exec.get("jobs", []) if job.get("name") in gated_job_names]
    if len(gated_jobs) != 2 or any(job.get("conclusion") != "skipped" for job in gated_jobs):
        errors.append("lease-verification and deploy jobs must be recorded as skipped")

    published_images = build_exec.get("published_images", {})
    if sorted(published_images) != sorted(COMPONENTS):
        errors.append("published_images must contain exactly api, web, worker, and scheduler")
    for comp in COMPONENTS:
        ref = published_images.get(comp, "")
        if not IMAGE_DIGEST_PATTERN.fullmatch(ref):
            errors.append(f"published_images[{comp}] '{ref}' is not an immutable digest ref")
        if manifest.get("components", {}).get(comp, {}).get("image") != ref:
            errors.append(f"published_images[{comp}] differs from the repository manifest")
    migration = manifest.get("components", {}).get("migration", {}).get("image")
    if build_exec.get("migration_component_image") != migration:
        errors.append("migration component image differs from the repository manifest")
    for key in ("signature_refs", "sbom_refs"):
        refs = build_exec.get(key, [])
        if len(refs) != 4 or refs != manifest.get(key):
            errors.append(f"{key} must be the four refs recorded in the repository manifest")
        for ref in refs:
            if not IMAGE_DIGEST_PATTERN.fullmatch(ref):
                errors.append(f"{key} entry '{ref}' is not an immutable digest ref")
    artifacts = build_exec.get("uploaded_artifacts", [])
    if len(artifacts) != 6 or any(
        not re.fullmatch(r"[0-9a-f]{64}", str(a.get("sha256", ""))) for a in artifacts
    ):
        errors.append("six uploaded artifacts with sha256 values must be recorded")
    manifest_artifacts = [a for a in artifacts if a.get("id") == EXPECTED_MANIFEST_ARTIFACT_ID]
    if len(manifest_artifacts) != 1 or manifest_artifacts[0].get("sha256") != _sha256(
        RELEASE_MANIFEST
    ):
        errors.append("the manifest artifact sha256 must equal the repository manifest bytes")
    log_readback = build_exec.get("build_job_log_readback", {})
    counts = log_readback.get("digest_occurrences", {})
    if any(counts.get(comp, 0) < 1 for comp in COMPONENTS) or counts.get("manifest_digest", 0) < 1:
        errors.append("build job log readback must show every component digest and the manifest")
    if log_readback.get("cosign_verify_invocations", 0) < 4:
        errors.append("build job log readback must show at least four cosign verify invocations")
    if len(build_exec.get("rekor_tlog_indexes_in_log", [])) != 8:
        errors.append("eight Rekor tlog entries (4 sign + 4 attest) must be recorded")


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


def _check_authorization(audit: dict, registry: dict, errors: list[str]) -> None:
    authorization = audit.get("authorization_state", {})
    release = registry.get("release", {})
    for field in (
        "supervisor_lease_issued",
        "private_signing_key_available_to_worker",
    ):
        if authorization.get(field) is not False:
            errors.append(f"authorization_state.{field} must be false")
    if authorization.get("canonical_registry_decision") != release.get("decision"):
        errors.append("canonical_registry_decision differs from the repository registry")

    dev_gates = authorization.get("dev_admission_gates", {})
    registry_dev_gates = {
        g["id"]: g for g in registry.get("gates", []) if g.get("admission_target") == "dev"
    }
    if set(dev_gates) != set(registry_dev_gates) or set(dev_gates) != set(DEV_GATES):
        errors.append("dev_admission_gates must list exactly gate-0, gate-1 and gate-4")
    for gate_id, gate in dev_gates.items():
        actual = registry_dev_gates.get(gate_id, {})
        receipts = actual.get("receipts", [])
        if gate.get("status") != actual.get("status") or gate.get("receipts") != len(receipts):
            errors.append(f"{gate_id} status/receipt count differs from the repository registry")
        if actual.get("status") not in CLEARED_STATUSES:
            errors.append(f"{gate_id} is not cleared in the repository registry")
        if len(receipts) != 1 or receipts[0].get("release_sha") != EXPECTED_CURRENT_CANDIDATE:
            errors.append(f"{gate_id} must carry one receipt bound to the candidate")
        if receipts and receipts[0].get("result") != "pass":
            errors.append(f"{gate_id} receipt result must be pass")
        if actual.get("blockers"):
            errors.append(f"{gate_id} must have no blockers")
        if gate.get("receipt_release_sha") != (receipts[0].get("release_sha") if receipts else None):
            errors.append(f"{gate_id} receipt_release_sha differs from the repository registry")
    dry = authorization.get("registry_admission_errors_dry_run", {})
    for key in ("release_sha_origin_dev_tip", "release_sha_candidate"):
        entry = dry.get(key, {})
        if not SHA_PATTERN.fullmatch(str(entry.get("sha", ""))) or entry.get("errors") != []:
            errors.append(
                f"registry_admission_errors_dry_run.{key} must record an empty error list"
            )


def _check_live_state(audit: dict, errors: list[str]) -> None:
    live_state = audit.get("live_gcp_runtime_state", {})
    if live_state.get("current_readback_result") != "predeploy_target_absence_verified":
        errors.append("current GCP readback must record hosted pre-deploy target absence")
    if (
        live_state.get("deployment_commands_run") is not False
        or live_state.get("traffic_switch_run") is not False
    ):
        errors.append("deployment_commands_run and traffic_switch_run must be false")
    for key in (
        "cloud_run_generated_urls",
        "cloud_run_revisions",
        "job_executions",
        "scheduler_triggers",
    ):
        if live_state.get(key) != []:
            errors.append(f"live_gcp_runtime_state.{key} must be empty without a deployment")
    source = live_state.get("readback_source", {})
    if source.get("run_id") != EXPECTED_BUILD_RUN_ID or not re.fullmatch(
        r"[0-9a-f]{64}", str(source.get("sha256", ""))
    ):
        errors.append("readback_source must be the hosted absence artifact of the build run")
    absence = live_state.get("target_absence_receipt", {})
    if absence.get("candidate_sha") != EXPECTED_CURRENT_CANDIDATE:
        errors.append("target absence readback must bind to the current candidate")
    targets = [
        absence.get(k)
        for k in ("api_service", "web_service", "migration_job", "worker_job", "scheduler_job")
    ]
    if any(not isinstance(t, dict) or t.get("exists") is not False for t in targets):
        errors.append("all five release targets must be explicitly absent in pre-deploy readback")
    direct = live_state.get("direct_gcloud_readback_this_round", {})
    if direct.get("attempted") is not True or direct.get("succeeded") is not False:
        errors.append("direct gcloud readback must be recorded as attempted and failed")
    if any(c.get("exit_code") == 0 for c in direct.get("commands", [])):
        errors.append("no direct gcloud readback command succeeded this round")


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
    for token in (
        EXPECTED_CURRENT_CANDIDATE,
        EXPECTED_MANIFEST_DIGEST,
        "decision=go",
        "ODP-DEV-RELEASE-GATE-RECONCILIATION-006",
    ):
        if token not in readme:
            errors.append(f"README does not mention {token}")
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
