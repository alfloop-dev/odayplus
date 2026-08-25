"""Contract and evidence tests for ODP-EPHEMERAL-STAGING-ROLLOUT-001 (Staging Rollout & Release Rehearsal)."""

from __future__ import annotations

import json
from pathlib import Path

from delivery_toolchain.release.release_manifest import (
    compute_manifest_digest,
    load_manifest,
)
from delivery_toolchain.release.release_receipts import (
    validate_receipt,
)
from product_ops.deployment.staging_lifecycle import (
    derive_release_tenant_id,
    get_ephemeral_resource_names,
    parse_timestamp,
    release_label_value,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"
EVIDENCE_DIR = ROOT / "docs/evidence/runtime/ODP-EPHEMERAL-STAGING-ROLLOUT-001"
MANIFEST_BINDING_PATH = EVIDENCE_DIR / "staging-rollout-manifest-binding.json"
DATA_PLATFORM_DEPLOYMENT_PATH = EVIDENCE_DIR / "data-platform-staging-deployment.json"
ODAYPLUS_DEPLOYMENT_PATH = EVIDENCE_DIR / "odayplus-staging-deployment.json"
STAGING_REHEARSAL_READBACK_PATH = EVIDENCE_DIR / "staging-rehearsal-readback.json"
EXTERNAL_SOURCES_AUDIT_PATH = EVIDENCE_DIR / "external-sources-provider-off-audit.json"
LIFECYCLE_TTL_AUDIT_PATH = EVIDENCE_DIR / "staging-lifecycle-ttl-audit.json"
RECEIPTS_INDEX_PATH = EVIDENCE_DIR / "release-receipts-index.json"
README_PATH = EVIDENCE_DIR / "README.md"


def test_evidence_files_exist() -> None:
    assert EVIDENCE_DIR.is_dir(), f"Evidence directory missing: {EVIDENCE_DIR}"
    for file_path in (
        MANIFEST_BINDING_PATH,
        DATA_PLATFORM_DEPLOYMENT_PATH,
        ODAYPLUS_DEPLOYMENT_PATH,
        STAGING_REHEARSAL_READBACK_PATH,
        EXTERNAL_SOURCES_AUDIT_PATH,
        LIFECYCLE_TTL_AUDIT_PATH,
        RECEIPTS_INDEX_PATH,
        README_PATH,
    ):
        assert file_path.is_file(), f"Missing required evidence file: {file_path}"


def test_manifest_integrity_and_staging_component_digests_match() -> None:
    manifest, errors = load_manifest(MANIFEST_PATH)
    assert errors == [], f"Manifest validation errors: {errors}"
    assert manifest is not None

    computed_digest = compute_manifest_digest(manifest)
    assert manifest["manifest_digest"] == computed_digest

    binding = json.loads(MANIFEST_BINDING_PATH.read_text(encoding="utf-8"))
    assert binding["release_id"] == manifest["release_id"]
    assert binding["candidate_sha"] == manifest["candidate_sha"]
    assert binding["manifest_digest"] == manifest["manifest_digest"]
    assert binding["environment"] == "staging"
    assert binding["stage"] == "staging-verified"

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
    assert binding["external_sources_expected_enabled"] == []


def test_data_platform_staging_precedence_and_workloads() -> None:
    dp_data = json.loads(DATA_PLATFORM_DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    assert dp_data["subsystem"] == "data_platform"
    assert dp_data["environment"] == "staging"
    assert dp_data["namespace"] == "oday-staging-odp-20260730-001"
    assert dp_data["deployment_sequence_precedence"]["precedence_verified"] is True
    assert dp_data["deployment_sequence_precedence"]["precedence_order"] == 1

    workloads = {w["name"]: w for w in dp_data["workloads"]}
    migrate_job = next(
        w for w in dp_data["workloads"] if w["kind"] == "Job" and "migrate" in w["name"]
    )
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


def test_odayplus_staging_isolated_resources_and_labels() -> None:
    op_data = json.loads(ODAYPLUS_DEPLOYMENT_PATH.read_text(encoding="utf-8"))
    assert op_data["subsystem"] == "oday_plus"
    assert op_data["environment"] == "staging"
    assert op_data["release_id"] == "odp-20260730-001"
    assert op_data["deployment_sequence_precedence"]["precedence_order"] == 2
    assert op_data["deployment_sequence_precedence"]["precedence_verified"] is True

    expected_names = get_ephemeral_resource_names(
        op_data["release_id"],
        op_data["project_id"],
        tenant_id=op_data["tenant_id"],
    )

    resources = op_data["isolated_resources"]
    assert resources["database"]["name"] == expected_names["database_name"]
    assert resources["database_user"]["name"] == expected_names["database_user"]
    assert resources["storage_bucket"]["name"] == expected_names["bucket_name"]
    assert resources["secrets"]["database_url"] == expected_names["secret_db_url"]
    assert resources["secrets"]["cursor_signing_key"] == expected_names["secret_cursor_key"]
    assert resources["secrets"]["web_session"] == expected_names["secret_web_session"]
    assert resources["pubsub"]["jobs_topic"] == expected_names["jobs_topic"]
    assert resources["pubsub"]["jobs_subscription"] == expected_names["jobs_sub"]
    assert resources["pubsub"]["jobs_dlq_topic"] == expected_names["jobs_dlq_topic"]
    assert resources["pubsub"]["jobs_dlq_subscription"] == expected_names["jobs_dlq_sub"]

    # Cloud Run Services
    assert resources["cloud_run_services"]["api"]["name"] == expected_names["cloud_run_api"]
    assert resources["cloud_run_services"]["api"]["status"] == "READY"
    assert resources["cloud_run_services"]["web"]["name"] == expected_names["cloud_run_web"]
    assert resources["cloud_run_services"]["web"]["status"] == "READY"

    # Cloud Run Jobs
    assert resources["cloud_run_jobs"]["migration"]["status"] == "SUCCEEDED"
    assert resources["cloud_run_jobs"]["worker"]["status"] == "READY"
    assert resources["cloud_run_jobs"]["scheduler"]["status"] == "READY"

    # Cloud Scheduler Trigger must start PAUSED
    assert resources["cloud_scheduler_trigger"]["name"] == expected_names["scheduler_job"]
    assert resources["cloud_scheduler_trigger"]["status"] == "PAUSED"
    assert resources["cloud_scheduler_trigger"]["paused"] is True

    # Tracking labels consistency
    labels = op_data["tracking_labels"]
    assert labels["environment"] == "staging"
    assert labels["ephemeral"] == "true"
    assert labels["managed_by"] == "terraform"
    assert labels["release_id"] == release_label_value("odp-20260730-001")
    assert labels["owner_task"] == "odp-ephemeral-staging-rollout-001"
    assert op_data["secret_values_redacted"] is True


def test_staging_full_rehearsal_suites_passed() -> None:
    rehearsal = json.loads(STAGING_REHEARSAL_READBACK_PATH.read_text(encoding="utf-8"))
    assert rehearsal["readback_status"] == "PASSED"
    assert rehearsal["environment"] == "staging"
    assert rehearsal["stage"] == "staging-verified"

    suites = rehearsal["rehearsal_suites"]

    # 1. Migration rehearsal (expand compatibility)
    mig = suites["migration_rehearsal"]
    assert mig["status"] == "passed"
    assert mig["expand_migration_executed"] is True
    assert mig["schema_compatibility_verified"] is True
    assert mig["contract_migration_deferred"] is True
    assert mig["dual_read_write_compatible"] is True

    # 2. Data platform contract
    dp = suites["data_platform_contract"]
    assert dp["status"] == "passed"
    assert dp["snapshot_materialization"] == "passed"
    assert dp["contract_readback"] == "passed"
    assert dp["masked_snapshot_restored"] is True
    assert dp["writable_prod_db_attached"] is False

    # 3. API/Web smoke & live E2E gate
    smoke = suites["api_web_smoke_and_e2e"]
    assert smoke["status"] == "passed"
    assert len(smoke["smoke_endpoints_verified"]) >= 5
    assert smoke["rbac_smoke_authenticated"] is True
    assert smoke["live_e2e_gate_status"] == "passed"
    assert smoke["assisted_listing_flow"] == "passed"
    assert smoke["canonical_model_contract"] == "passed"
    assert smoke["operator_console_rbac"] == "passed"

    # 4. Worker & Scheduler jobs one-shot
    jobs = suites["worker_scheduler_jobs"]
    assert jobs["status"] == "passed"
    assert jobs["migration_job"] == "SUCCEEDED"
    assert jobs["worker_job_oneshot"] == "passed"
    assert jobs["scheduler_job_oneshot"] == "passed"
    assert jobs["scheduler_trigger_starts_paused"] is True
    assert jobs["idempotency_verified"] is True
    assert jobs["retry_and_dlq_verified"] is True

    # 5. Backup & Restore drill
    bk = suites["backup_restore_drill"]
    assert bk["status"] == "passed"
    assert bk["backup_checkpoint_created"] is True
    assert bk["restore_drill_executed"] is True
    assert bk["data_integrity_and_checksum_parity"] == "verified"
    assert bk["row_counts_matched"] is True

    # 6. Rollback rehearsal
    rb = suites["rollback_rehearsal"]
    assert rb["status"] == "passed"
    assert rb["destructive_down_migration_prevented"] is True
    assert rb["service_pointer_reverted_to_prior"] is True
    assert rb["legacy_app_reads_expanded_schema"] is True
    assert rb["rollback_smoke_passed"] is True

    assert suites["secret_governance"]["secret_values_redacted"] is True
    assert suites["secret_governance"]["zero_plaintext_credentials"] is True


def test_sixteen_sources_disabled_and_zero_credentials_staging() -> None:
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


def test_staging_lifecycle_and_ttl_governance() -> None:
    lifecycle = json.loads(LIFECYCLE_TTL_AUDIT_PATH.read_text(encoding="utf-8"))
    assert lifecycle["release_id"] == "odp-20260730-001"
    assert lifecycle["release_label"] == release_label_value("odp-20260730-001")
    assert lifecycle["tenant_id"] == derive_release_tenant_id("odp-20260730-001")
    assert lifecycle["ttl_hours"] == 24
    assert lifecycle["owner_task"] == "ODP-EPHEMERAL-STAGING-ROLLOUT-001"

    created = parse_timestamp(lifecycle["created_at"])
    expires = parse_timestamp(lifecycle["expires_at"])
    assert (expires - created).total_seconds() == 24 * 3600

    assert "failure_policy" in lifecycle["lifecycle_governance"]
    assert "success_policy" in lifecycle["lifecycle_governance"]
    assert "cleanup_targeting_rule" in lifecycle["lifecycle_governance"]

    scan = lifecycle["orphan_scan_dry_run_result"]
    assert scan["total_scanned"] == 14
    assert scan["active_count"] == 14
    assert scan["expired_count"] == 0
    assert scan["orphan_count"] == 0
    assert scan["failed_cleanups"] == 0


def test_staging_release_receipts_conformance_and_redaction() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    receipts_data = json.loads(RECEIPTS_INDEX_PATH.read_text(encoding="utf-8"))
    assert receipts_data["environment"] == "staging"
    assert receipts_data["stage"] == "staging-verified"
    assert len(receipts_data["receipts"]) >= 2

    for receipt in receipts_data["receipts"]:
        errors = validate_receipt(
            receipt,
            expected_release_id=manifest["release_id"],
            expected_candidate_sha=manifest["candidate_sha"],
            expected_manifest_digest=manifest["manifest_digest"],
        )
        assert errors == [], f"Receipt {receipt.get('receipt_id')} validation errors: {errors}"
        assert receipt["environment"] == "staging"
        assert receipt["stage"] == "staging-verified"
        assert receipt["secret_values_redacted"] is True
        assert receipt["result"] == "pass"
