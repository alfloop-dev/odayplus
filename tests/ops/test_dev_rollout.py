"""Contract and evidence tests for ODP-DEV-ROLLOUT-001 (Dev Rollout)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from delivery_toolchain.release.release_manifest import (
    compute_manifest_digest,
    load_manifest,
    validate_manifest,
)
from delivery_toolchain.release.release_receipts import (
    read_receipt,
    validate_receipt,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"
EVIDENCE_DIR = ROOT / "docs/evidence/runtime/ODP-DEV-ROLLOUT-001"
MANIFEST_BINDING_PATH = EVIDENCE_DIR / "dev-rollout-manifest-binding.json"
DATA_PLATFORM_DEPLOYMENT_PATH = EVIDENCE_DIR / "data-platform-dev-deployment.json"
ODAYPLUS_DEPLOYMENT_PATH = EVIDENCE_DIR / "odayplus-dev-deployment.json"
DEV_INTEGRATION_READBACK_PATH = EVIDENCE_DIR / "dev-integration-readback.json"
EXTERNAL_SOURCES_AUDIT_PATH = EVIDENCE_DIR / "external-sources-provider-off-audit.json"
RECEIPTS_INDEX_PATH = EVIDENCE_DIR / "release-receipts-index.json"


def test_evidence_files_exist() -> None:
    assert EVIDENCE_DIR.is_dir()
    for file_path in (
        MANIFEST_BINDING_PATH,
        DATA_PLATFORM_DEPLOYMENT_PATH,
        ODAYPLUS_DEPLOYMENT_PATH,
        DEV_INTEGRATION_READBACK_PATH,
        EXTERNAL_SOURCES_AUDIT_PATH,
        RECEIPTS_INDEX_PATH,
        EVIDENCE_DIR / "README.md",
    ):
        assert file_path.is_file(), f"Missing required evidence file: {file_path}"


def test_manifest_integrity_and_component_digests_match() -> None:
    manifest, errors = load_manifest(MANIFEST_PATH)
    assert errors == [], f"Manifest validation errors: {errors}"
    assert manifest is not None

    computed_digest = compute_manifest_digest(manifest)
    assert manifest["manifest_digest"] == computed_digest

    binding = json.loads(MANIFEST_BINDING_PATH.read_text(encoding="utf-8"))
    assert binding["release_id"] == manifest["release_id"]
    assert binding["candidate_sha"] == manifest["candidate_sha"]
    assert binding["manifest_digest"] == manifest["manifest_digest"]

    # All components defined in the manifest must match the deployed binding
    expected_components = ("api", "web", "data_platform", "migration", "worker", "scheduler")
    for component_name in expected_components:
        assert component_name in manifest["components"]
        assert component_name in binding["components_digest_validation"]
        manifest_image = manifest["components"][component_name]["image"]
        deployed_image = binding["components_digest_validation"][component_name]["deployed_image"]
        assert manifest_image == deployed_image
        assert binding["components_digest_validation"][component_name]["digest_match"] is True

    assert binding["digests_integrity"]["migration_digest"] == manifest["migration_digest"]
    assert binding["digests_integrity"]["data_contract_digest"] == manifest["data_contract_digest"]
    assert binding["digests_integrity"]["source_policy_digest"] == manifest["source_policy_digest"]
    assert binding["digests_integrity"]["all_digests_match"] is True


def test_data_platform_precedence_and_deployment_details() -> None:
    dp_data = json.loads(DATA_PLATFORM_DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    assert dp_data["subsystem"] == "data_platform"
    assert dp_data["environment"] == "dev"
    assert dp_data["namespace"] == "oday-dev"
    assert dp_data["deployment_sequence_precedence"]["precedence_verified"] is True
    assert dp_data["deployment_sequence_precedence"]["precedence_order"] == 1

    workloads = {w["name"]: w for w in dp_data["workloads"]}
    migrate_job = next(w for w in dp_data["workloads"] if w["kind"] == "Job" and "migrate" in w["name"])
    assert migrate_job["execution_order"] == "00-migration"
    assert migrate_job["status"] == "PASSED"
    assert migrate_job["receipt_recorded"] is True

    cron_job = workloads["oday-data-platform-bounded-daily"]
    assert cron_job["kind"] == "CronJob"
    assert cron_job["schedule"] == "0 1 * * *"
    assert cron_job["requires_migration_receipt"] is True
    assert cron_job["status"] == "ACTIVE_BOUNDED"

    # Manual jobs must be suspended
    for manual_name in (
        f"oday-data-platform-orders-history-{dp_data['candidate_sha'][:12]}",
        f"oday-data-platform-trade-manual-{dp_data['candidate_sha'][:12]}",
        f"oday-data-platform-device-log-manual-{dp_data['candidate_sha'][:12]}",
    ):
        job = workloads[manual_name]
        assert job["manual_only"] is True
        assert job["suspend"] is True

    assert dp_data["status_mapping_contract"]["version"] == "fongniao-prod-observed-v1"
    assert dp_data["secret_values_redacted"] is True


def test_odayplus_dev_deployment_configurations() -> None:
    op_data = json.loads(ODAYPLUS_DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    assert op_data["subsystem"] == "oday_plus"
    assert op_data["environment"] == "dev"

    services = op_data["deployed_services_and_jobs"]
    assert services["api"]["status"] == "READY"
    assert services["web"]["status"] == "READY"
    assert services["migration"]["status"] == "SUCCEEDED"
    assert services["worker"]["status"] == "READY"
    assert services["scheduler"]["status"] == "READY"
    assert op_data["secret_values_redacted"] is True


def test_dev_integration_readback_reports() -> None:
    readback = json.loads(DEV_INTEGRATION_READBACK_PATH.read_text(encoding="utf-8"))
    assert readback["readback_status"] == "PASSED"
    assert readback["environment"] == "dev"
    assert readback["stage"] == "dev-verified"

    reports = readback["reports"]
    assert reports["cloud_run_preflight"]["status"] == "passed"
    assert len(reports["cloud_run_preflight"]["checks_passed"]) >= 15
    assert reports["cloud_run_smoke"]["status"] == "passed"
    assert reports["cloud_run_smoke"]["rbac_smoke_authenticated"] is True
    assert reports["migration_compatibility"]["status"] == "passed"
    assert reports["migration_compatibility"]["expand_schema_compatible"] is True
    assert reports["jobs_validation"]["migration_job"]["status"] == "passed"
    assert reports["jobs_validation"]["worker_job"]["status"] == "passed"
    assert reports["jobs_validation"]["scheduler_job"]["status"] == "passed"
    assert reports["live_e2e_gate"]["status"] == "passed"


def test_sixteen_sources_disabled_and_zero_credentials() -> None:
    audit = json.loads(EXTERNAL_SOURCES_AUDIT_PATH.read_text(encoding="utf-8"))
    assert audit["total_sources_audited"] == 16
    assert audit["all_sources_disabled"] is True
    assert audit["zero_credentials_present"] is True
    assert audit["default_deny_egress_enforced"] is True
    assert audit["manifest_expected_enabled"] == []
    assert len(audit["sources_inventory"]) == 16

    for source in audit["sources_inventory"]:
        assert source["status"] == "disabled"
        assert source["credentials_present"] is False
        assert source["public_egress"] == "denied"


def test_release_receipts_conformance_and_redaction() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    receipts_data = json.loads(RECEIPTS_INDEX_PATH.read_text(encoding="utf-8"))
    assert receipts_data["environment"] == "dev"
    assert receipts_data["stage"] == "dev-verified"
    assert len(receipts_data["receipts"]) >= 2

    for receipt in receipts_data["receipts"]:
        errors = validate_receipt(
            receipt,
            expected_release_id=manifest["release_id"],
            expected_candidate_sha=manifest["candidate_sha"],
            expected_manifest_digest=manifest["manifest_digest"],
        )
        assert errors == [], f"Receipt {receipt.get('receipt_id')} validation errors: {errors}"
        assert receipt["secret_values_redacted"] is True
        assert receipt["result"] == "pass"
