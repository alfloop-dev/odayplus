#!/usr/bin/env python3
"""Tests for ephemeral staging IaC, lifecycle management, cleanup, and orphan scanning."""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from product_ops.deployment.staging_lifecycle import (
    MAX_TENANT_ID_LENGTH,
    TENANT_ID_PATTERN,
    UNVERIFIABLE_STATE_PREFIX,
    ReleaseIdentityConflict,
    ReleaseStateUnverifiable,
    StagingConfig,
    cleanup_ephemeral_staging,
    compute_release_hash,
    create_ephemeral_staging,
    derive_release_tenant_id,
    extend_staging_ttl,
    generate_staging_labels,
    generate_tfvars,
    get_ephemeral_resource_names,
    is_staging_ephemeral_resource,
    main,
    make_terraform_creation_executor,
    make_terraform_deletion_executor,
    parse_module_variables,
    plan_staging_resources,
    release_label_value,
    resolve_tenant_id,
    scan_orphans,
    validate_immutable_release_identity,
    validate_module_contract,
    validate_staging_config,
    validate_tfvars_against_module,
)

MODULE_DIR = ROOT / "infra/terraform/modules/ephemeral_staging"


class EphemeralStagingLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.valid_config = StagingConfig(
            release_id="odp-20260824-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            project_id="oday-staging-proj",
            region="asia-east1",
            cloud_sql_instance_name="oday-staging-pg",
            cloud_sql_connection_name="oday-staging-proj:asia-east1:oday-staging-pg",
            network_name="oday-staging-vpc",
            subnetwork_name="oday-staging-subnet",
            kms_key_id="projects/oday-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
            deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
            api_image="asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "c" * 64,
            web_image="asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "d" * 64,
            worker_image="asia-east1-docker.pkg.dev/proj/repo/worker@sha256:" + "e" * 64,
            scheduler_image="asia-east1-docker.pkg.dev/proj/repo/scheduler@sha256:" + "f" * 64,
            ttl_hours=24,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )

    def test_staging_config_validation_success(self) -> None:
        errors = validate_staging_config(self.valid_config)
        self.assertEqual(errors, [])

    def test_staging_config_validation_invalid_fields(self) -> None:
        # Invalid release_id
        bad_cfg = dataclasses_replace(self.valid_config, release_id="invalid id with spaces")
        self.assertTrue(any("release_id" in err for err in validate_staging_config(bad_cfg)))

        # Invalid owner_task_id
        bad_cfg = dataclasses_replace(self.valid_config, owner_task_id="")
        self.assertTrue(any("owner_task_id" in err for err in validate_staging_config(bad_cfg)))

        bad_cfg = dataclasses_replace(self.valid_config, owner_task_id="invalid task id with spaces")
        self.assertTrue(any("owner_task_id" in err for err in validate_staging_config(bad_cfg)))

        # Invalid candidate_sha
        bad_cfg = dataclasses_replace(self.valid_config, candidate_sha="not-a-40-char-sha")
        self.assertTrue(any("candidate_sha" in err for err in validate_staging_config(bad_cfg)))

        # Invalid manifest_digest
        bad_cfg = dataclasses_replace(self.valid_config, manifest_digest="bad-digest")
        self.assertTrue(any("manifest_digest" in err for err in validate_staging_config(bad_cfg)))

        # Invalid image (missing digest)
        bad_cfg = dataclasses_replace(self.valid_config, api_image="gcr.io/repo/api:latest")
        self.assertTrue(any("api_image" in err for err in validate_staging_config(bad_cfg)))

        bad_cfg = dataclasses_replace(self.valid_config, web_image="gcr.io/repo/web:latest")
        self.assertTrue(any("web_image" in err for err in validate_staging_config(bad_cfg)))

        bad_cfg = dataclasses_replace(self.valid_config, worker_image="gcr.io/repo/worker:latest")
        self.assertTrue(any("worker_image" in err for err in validate_staging_config(bad_cfg)))

        bad_cfg = dataclasses_replace(self.valid_config, scheduler_image="gcr.io/repo/scheduler:latest")
        self.assertTrue(any("scheduler_image" in err for err in validate_staging_config(bad_cfg)))

        # Invalid TTL
        bad_cfg = dataclasses_replace(self.valid_config, ttl_hours=0)
        self.assertTrue(any("ttl_hours" in err for err in validate_staging_config(bad_cfg)))

        bad_cfg = dataclasses_replace(self.valid_config, ttl_hours=200)
        self.assertTrue(any("ttl_hours" in err for err in validate_staging_config(bad_cfg)))

    def test_naming_collision_avoidance_and_length_limits(self) -> None:
        # Two very long release IDs differing only in the last character
        rel1 = "odp-20260824-feature-very-long-branch-name-segment-001"
        rel2 = "odp-20260824-feature-very-long-branch-name-segment-002"

        names1 = get_ephemeral_resource_names(rel1, "oday-staging-proj-very-long-30")
        names2 = get_ephemeral_resource_names(rel2, "oday-staging-proj-very-long-30")

        # Names must be strictly different (no collision)
        self.assertNotEqual(names1["sa_runtime"], names2["sa_runtime"])
        self.assertNotEqual(names1["database_name"], names2["database_name"])
        self.assertNotEqual(names1["bucket_name"], names2["bucket_name"])
        self.assertNotEqual(names1["cloud_run_api"], names2["cloud_run_api"])

        # GCP Limits verification
        # Service account account_id: max 30 chars
        self.assertLessEqual(len(names1["sa_runtime"]), 30)
        self.assertLessEqual(len(names1["sa_web"]), 30)
        self.assertLessEqual(len(names1["sa_worker"]), 30)

        # Cloud SQL DB & User: max 63 chars
        self.assertLessEqual(len(names1["database_name"]), 63)
        self.assertLessEqual(len(names1["database_user"]), 63)

        # GCS Bucket: max 63 chars
        self.assertLessEqual(len(names1["bucket_name"]), 63)

        # Cloud Run Service: max 63 chars
        self.assertLessEqual(len(names1["cloud_run_api"]), 63)
        self.assertLessEqual(len(names1["cloud_run_web"]), 63)

    def test_generate_staging_labels(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        rel_hash = compute_release_hash("odp-20260824-001")
        labels = generate_staging_labels(
            release_id="odp-20260824-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            ttl_hours=24,
            created_at=now,
        )

        self.assertEqual(labels["app"], "oday-plus")
        self.assertEqual(labels["environment"], "staging")
        self.assertEqual(labels["managed_by"], "terraform")
        self.assertEqual(labels["ephemeral"], "true")
        self.assertEqual(labels["release_id"], f"odp-20260824-001-{rel_hash}")
        self.assertEqual(labels["tenant"], f"tenant-odp-20260824-001-{rel_hash}")
        self.assertEqual(labels["candidate_sha"], "a" * 40)
        self.assertEqual(labels["manifest_digest_prefix"], "b" * 16)
        self.assertEqual(labels["owner_task"], "odp-ephemeral-staging-iac-001")
        self.assertEqual(labels["created_at"], "2026-08-24-12-00-00")
        self.assertEqual(labels["expires_at"], "2026-08-25-12-00-00")

    def test_tenant_isolation_explicit_and_default(self) -> None:
        rel_hash = compute_release_hash("odp-20260824-001")
        # Default tenant derived from release_id
        labels_default = generate_staging_labels(
            release_id="odp-20260824-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )
        self.assertEqual(labels_default["tenant"], f"tenant-odp-20260824-001-{rel_hash}")

        # Explicit tenant passed
        labels_explicit = generate_staging_labels(
            release_id="odp-20260824-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            tenant_id="custom-tenant-42",
        )
        self.assertEqual(labels_explicit["tenant"], "custom-tenant-42")

        cfg_tenant = dataclasses_replace(self.valid_config, tenant_id="custom-tenant-42")
        tfvars = generate_tfvars(cfg_tenant)
        self.assertEqual(tfvars["tenant_id"], "custom-tenant-42")

        names = get_ephemeral_resource_names("odp-20260824-001", "oday-staging-proj", tenant_id="custom-tenant-42")
        self.assertEqual(names["tenant_id"], "custom-tenant-42")

    def test_mandatory_labels_cannot_be_overridden(self) -> None:
        rel_hash = compute_release_hash("odp-20260824-001")
        labels = generate_staging_labels(
            release_id="odp-20260824-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            additional_labels={
                "managed_by": "unsafe-script",
                "environment": "prod",
                "ephemeral": "false",
                "release_id": "other-release",
                "custom_label": "retained",
            },
        )

        self.assertEqual(labels["managed_by"], "terraform")
        self.assertEqual(labels["environment"], "staging")
        self.assertEqual(labels["ephemeral"], "true")
        self.assertEqual(labels["release_id"], f"odp-20260824-001-{rel_hash}")
        self.assertEqual(labels["custom_label"], "retained")

    def test_generate_tfvars(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        tfvars = generate_tfvars(self.valid_config, created_at=now)
        self.assertEqual(tfvars["project_id"], "oday-staging-proj")
        self.assertEqual(tfvars["release_id"], "odp-20260824-001")
        # An omitted tenant resolves to the deterministic release-derived tenant.
        # Emitting "" here would be rejected by the module's tenant_id validation.
        self.assertEqual(tfvars["tenant_id"], derive_release_tenant_id("odp-20260824-001"))
        self.assertEqual(tfvars["candidate_sha"], "a" * 40)
        self.assertEqual(tfvars["ttl_hours"], 24)
        self.assertEqual(tfvars["created_at"], "2026-08-24T12:00:00Z")

    def test_plan_staging_resources(self) -> None:
        resources = plan_staging_resources(self.valid_config)
        types = [r.resource_type for r in resources]
        names = [r.resource_name for r in resources]

        self.assertIn("google_sql_database", types)
        self.assertIn("google_sql_user", types)
        self.assertIn("google_secret_manager_secret", types)
        self.assertIn("google_storage_bucket", types)
        self.assertIn("google_service_account", types)
        self.assertIn("google_pubsub_topic", types)
        self.assertIn("google_pubsub_subscription", types)
        self.assertIn("google_cloud_run_v2_service", types)
        self.assertEqual(types.count("google_cloud_run_v2_job"), 3)
        self.assertTrue(any("migration" in n for n in names))
        self.assertTrue(any("worker" in n for n in names))
        self.assertTrue(any("scheduler" in n for n in names))
        self.assertIn("google_cloud_scheduler_job", types)

        rel_hash = compute_release_hash("odp-20260824-001")
        self.assertIn(f"stg_odp_20260824_001_{rel_hash}", names)
        self.assertIn(f"stg_odp_20260824_001_{rel_hash}_app", names)
        self.assertTrue(any("worker-trigger" in n for n in names))

        for r in resources:
            self.assertEqual(r.labels["managed_by"], "terraform")
            self.assertEqual(r.labels["ephemeral"], "true")
            self.assertEqual(r.labels["app"], "oday-plus")

    def test_create_ephemeral_staging_receipt_dry_run(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        receipt = create_ephemeral_staging(self.valid_config, dry_run=True, now=now)

        self.assertTrue(receipt.success)
        self.assertEqual(receipt.action, "create")
        self.assertEqual(receipt.release_id, "odp-20260824-001")
        self.assertEqual(receipt.candidate_sha, "a" * 40)
        self.assertEqual(receipt.metadata["scheduler_paused"], True)
        self.assertTrue(len(receipt.resources) > 0)
        for r in receipt.resources:
            self.assertEqual(r["status"], "planned")
            self.assertIn("labels", r)
            self.assertEqual(r["labels"]["managed_by"], "terraform")

    def test_create_ephemeral_staging_non_dry_run_requires_executor(self) -> None:
        # Non dry run without executor fails closed
        receipt = create_ephemeral_staging(self.valid_config, dry_run=False)
        self.assertFalse(receipt.success)
        self.assertTrue(receipt.remediation_required)
        self.assertTrue(any("creation_executor" in err for err in receipt.errors))

        # With executor succeeds
        def mock_executor(cfg: StagingConfig, res: list) -> bool:
            return True

        receipt_exec = create_ephemeral_staging(self.valid_config, dry_run=False, creation_executor=mock_executor)
        self.assertTrue(receipt_exec.success)
        self.assertFalse(receipt_exec.remediation_required)
        for r in receipt_exec.resources:
            self.assertEqual(r["status"], "provisioned")

        empty_receipt = create_ephemeral_staging(
            self.valid_config,
            dry_run=False,
            creation_executor=lambda _cfg, _resources: [],
        )
        self.assertFalse(empty_receipt.success)
        self.assertTrue(empty_receipt.remediation_required)

    def test_cleanup_exact_label_matching_and_safety(self) -> None:
        target_release = "odp-20260824-001"
        other_release = "odp-20260823-999"

        target_labels = generate_staging_labels(
            release_id=target_release,
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )
        other_labels = generate_staging_labels(
            release_id=other_release,
            candidate_sha="1" * 40,
            manifest_digest="sha256:" + "2" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )

        inventory = [
            # Target staging resources (should be deleted)
            {"id": "db-1", "type": "google_sql_database", "labels": target_labels},
            {"id": "bucket-1", "type": "google_storage_bucket", "labels": target_labels},
            {"id": "svc-1", "type": "google_cloud_run_v2_service", "labels": target_labels},
            # Different staging release (must NOT be deleted)
            {"id": "db-2", "type": "google_sql_database", "labels": other_labels},
            # Missing managed_by=terraform (must NOT be deleted by standard cleanup)
            {
                "id": "unmanaged-db",
                "type": "google_sql_database",
                "labels": {"app": "oday-plus", "environment": "staging", "ephemeral": "true", "release_id": target_release},
            },
            # Production resource (must NEVER be deleted)
            {
                "id": "prod-db",
                "type": "google_sql_database",
                "labels": {"app": "oday-plus", "environment": "prod", "managed_by": "terraform"},
            },
            # Long-lived shared infrastructure (must NEVER be deleted)
            {
                "id": "shared-vpc",
                "type": "google_compute_network",
                "labels": {"app": "oday-plus", "environment": "staging", "ephemeral": "false"},
            },
        ]

        def mock_deleter(res: dict) -> bool:
            return True

        receipt = cleanup_ephemeral_staging(
            release_id=target_release,
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            dry_run=False,
            deletion_executor=mock_deleter,
        )

        self.assertTrue(receipt.success)
        deleted_ids = [r["id"] for r in receipt.resources]
        self.assertEqual(sorted(deleted_ids), ["bucket-1", "db-1", "svc-1"])
        self.assertNotIn("db-2", deleted_ids)
        self.assertNotIn("unmanaged-db", deleted_ids)
        self.assertNotIn("prod-db", deleted_ids)
        self.assertNotIn("shared-vpc", deleted_ids)

    def test_cleanup_empty_inventory_fails_without_allow_empty(self) -> None:
        receipt = cleanup_ephemeral_staging(
            release_id="odp-20260824-001",
            project_id="oday-staging-proj",
            resource_inventory=[],
            dry_run=True,
            allow_empty=False,
        )
        self.assertFalse(receipt.success)
        self.assertTrue(any("No matching ephemeral staging resources found" in err for err in receipt.errors))

        receipt_allowed = cleanup_ephemeral_staging(
            release_id="odp-20260824-001",
            project_id="oday-staging-proj",
            resource_inventory=[],
            dry_run=True,
            allow_empty=True,
        )
        self.assertFalse(receipt_allowed.success)
        self.assertIn("allow_empty option cannot authorize", receipt_allowed.errors[0])

    def test_cleanup_rejects_broad_wildcard_targets(self) -> None:
        for bad_target in ("*", "all", "prod", "production", "dev", ""):
            receipt = cleanup_ephemeral_staging(
                release_id=bad_target,
                project_id="oday-staging-proj",
                resource_inventory=[],
                dry_run=False,
            )
            self.assertFalse(receipt.success)
            self.assertTrue(receipt.remediation_required)
            self.assertTrue(len(receipt.errors) > 0)

    def test_cleanup_failure_flags_remediation(self) -> None:
        target_labels = generate_staging_labels(
            release_id="odp-20260824-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )
        inventory = [
            {"id": "db-fail", "type": "google_sql_database", "labels": target_labels},
        ]

        def failing_executor(res: dict) -> bool:
            raise RuntimeError("Cloud SQL API error: database locked")

        receipt = cleanup_ephemeral_staging(
            release_id="odp-20260824-001",
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            dry_run=False,
            deletion_executor=failing_executor,
        )

        self.assertFalse(receipt.success)
        self.assertTrue(receipt.remediation_required)
        self.assertTrue(any("database locked" in err for err in receipt.errors))

    def test_cleanup_rejects_missing_ttl_labels(self) -> None:
        labels = generate_staging_labels(
            release_id="odp-20260824-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )
        labels.pop("expires_at")
        receipt = cleanup_ephemeral_staging(
            release_id="odp-20260824-001",
            project_id="oday-staging-proj",
            resource_inventory=[{"id": "unsafe", "type": "bucket", "labels": labels}],
            dry_run=False,
            allow_empty=False,
        )
        self.assertFalse(receipt.success)
        # The resource shares the release label but fails full ownership
        # validation (missing expires_at), so the new sibling-verification
        # gate refuses the entire release-scoped cleanup.
        self.assertTrue(
            any("incomplete or invalid ownership labels" in e for e in receipt.errors)
            or any("No matching ephemeral staging resources" in e for e in receipt.errors),
            f"Expected sibling-verification or no-match error, got: {receipt.errors}",
        )
        self.assertTrue(receipt.remediation_required)

    def test_scan_orphans_detects_expired_and_unmanaged(self) -> None:
        now = datetime(2026, 8, 25, 14, 0, 0, tzinfo=UTC)
        fresh_time = now - timedelta(hours=2)
        expired_time = now - timedelta(hours=26)

        fresh_labels = generate_staging_labels(
            release_id="odp-fresh-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            created_at=fresh_time,
            ttl_hours=24,
        )
        expired_labels = generate_staging_labels(
            release_id="odp-expired-001",
            candidate_sha="c" * 40,
            manifest_digest="sha256:" + "d" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            created_at=expired_time,
            ttl_hours=24,
        )
        unmanaged_labels = {
            "app": "oday-plus",
            "environment": "staging",
            "ephemeral": "true",
            "managed_by": "custom_script",
            "release_id": "odp-orphan-001",
        }

        inventory = [
            {
                "id": "fresh-res",
                "type": "google_sql_database",
                "raw_release_id": "odp-fresh-001",
                "labels": fresh_labels,
            },
            {
                "id": "expired-res",
                "type": "google_sql_database",
                "raw_release_id": "odp-expired-001",
                "labels": expired_labels,
            },
            {"id": "unmanaged-res", "type": "google_storage_bucket", "labels": unmanaged_labels},
            {"id": "prod-res", "type": "google_sql_database", "labels": {"environment": "prod"}},
        ]

        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            now=now,
            auto_cleanup=False,
        )

        self.assertEqual(result.total_scanned, 4)
        self.assertEqual(result.active_count, 1)
        self.assertEqual(result.expired_count, 1)
        self.assertEqual(result.orphan_count, 2)  # expired-res and unmanaged-res
        self.assertIn(release_label_value("odp-expired-001"), result.expired_releases)
        self.assertTrue(len(result.alerts) >= 2)

    def test_scan_orphans_reports_staging_candidate_missing_app_label(self) -> None:
        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=[
                {
                    "id": "missing-app",
                    "type": "google_sql_database",
                    "labels": {
                        "environment": "staging",
                        "ephemeral": "true",
                        "release_id": "odp-orphan-001",
                    },
                }
            ],
            now=datetime(2026, 8, 25, 14, 0, 0, tzinfo=UTC),
            auto_cleanup=True,
        )
        self.assertEqual(result.orphan_count, 1)
        self.assertEqual(result.failed_cleanups, 1)
        self.assertEqual(len(result.remediation_tasks), 1)
        self.assertIn("incomplete staging identity", result.orphan_resources[0]["reason"])

    def test_extend_staging_ttl_enforces_limits_owner_and_reason(self) -> None:
        curr_expires = datetime(2026, 8, 24, 18, 0, 0, tzinfo=UTC)
        created_at = datetime(2026, 8, 23, 18, 0, 0, tzinfo=UTC)

        # Successful extension (12h)
        ext = extend_staging_ttl(
            release_id="odp-20260824-001",
            extend_hours=12,
            reason="Investigating intermittent E2E timeout in worker drill",
            owner="Antigravity3",
            current_expires_at=curr_expires,
            created_at=created_at,
        )
        self.assertEqual(ext["release_id"], "odp-20260824-001")
        self.assertEqual(ext["extended_by_hours"], 12)
        self.assertEqual(ext["owner"], "Antigravity3")
        self.assertEqual(ext["new_expires_at"], "2026-08-25T06:00:00Z")

        # Missing created_at must be rejected (cannot omit or infer)
        with self.assertRaises(ValueError):
            extend_staging_ttl(
                release_id="odp-20260824-001",
                extend_hours=12,
                reason="debugging",
                owner="Antigravity3",
                current_expires_at=curr_expires,
                created_at=None,
            )

        # Inverted created_at > current_expires_at must be rejected
        with self.assertRaises(ValueError):
            extend_staging_ttl(
                release_id="odp-20260824-001",
                extend_hours=12,
                reason="debugging",
                owner="Antigravity3",
                current_expires_at=curr_expires,
                created_at=curr_expires + timedelta(hours=1),
            )

        # An actually 168h-old release cannot be extended by even 1h (exceeds max 168h TTL)
        old_created = datetime(2026, 8, 24, 0, 0, 0, tzinfo=UTC)
        old_expires = datetime(2026, 8, 31, 0, 0, 0, tzinfo=UTC)  # 168h from old_created
        with self.assertRaises(ValueError):
            extend_staging_ttl(
                release_id="odp-20260824-001",
                extend_hours=1,
                reason="debugging",
                owner="Antigravity3",
                current_expires_at=old_expires,
                created_at=old_created,
            )

        # 999h extension must be rejected
        with self.assertRaises(ValueError):
            extend_staging_ttl(
                release_id="odp-20260824-001",
                extend_hours=999,
                reason="debugging",
                owner="Antigravity3",
                current_expires_at=curr_expires,
                created_at=created_at,
            )

        # A caller cannot weaken the hard product cap by passing a larger
        # max_total_ttl_hours policy.
        with self.assertRaises(ValueError):
            extend_staging_ttl(
                release_id="odp-20260824-001",
                extend_hours=1,
                reason="debugging",
                owner="Antigravity3",
                current_expires_at=curr_expires,
                created_at=created_at,
                max_total_ttl_hours=999,
            )

        # Total TTL > 168h must be rejected
        with self.assertRaises(ValueError):
            extend_staging_ttl(
                release_id="odp-20260824-001",
                extend_hours=150,
                reason="debugging",
                owner="Antigravity3",
                current_expires_at=curr_expires,
                created_at=created_at,
            )

        # Missing reason raises ValueError
        with self.assertRaises(ValueError):
            extend_staging_ttl(
                release_id="odp-20260824-001",
                extend_hours=12,
                reason="",
                owner="Antigravity3",
                current_expires_at=curr_expires,
                created_at=created_at,
            )

        # Missing owner raises ValueError
        with self.assertRaises(ValueError):
            extend_staging_ttl(
                release_id="odp-20260824-001",
                extend_hours=12,
                reason="debugging",
                owner="",
                current_expires_at=curr_expires,
                created_at=created_at,
            )

    def test_release_label_collision_avoidance_for_ambiguous_ids(self) -> None:
        """Verify release IDs differing only by punctuation or case never collide in label or cleanup."""
        ambiguous_ids = ["rel_1.0", "rel.1.0", "rel-1-0", "REL_1.0", "rel_1_0"]
        labels_by_id = {}
        for rid in ambiguous_ids:
            lbl = release_label_value(rid)
            self.assertLessEqual(len(lbl), 63)
            self.assertNotIn(lbl, labels_by_id.values(), f"Collision detected for release_id {rid}")
            labels_by_id[rid] = lbl

        # Verify that generate_staging_labels and is_staging_ephemeral_resource enforce exact release isolation
        staging_labels_by_id = {
            rid: generate_staging_labels(
                release_id=rid,
                candidate_sha="a" * 40,
                manifest_digest="sha256:" + "b" * 64,
                owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            )
            for rid in ambiguous_ids
        }

        # Each release must match ONLY its own target_release_id and reject all other ambiguous variants
        for target_rid in ambiguous_ids:
            for candidate_rid, candidate_lbl in staging_labels_by_id.items():
                is_match = is_staging_ephemeral_resource(candidate_lbl, target_rid)
                if candidate_rid == target_rid:
                    self.assertTrue(is_match, f"Expected exact match for {target_rid}")
                else:
                    self.assertFalse(is_match, f"Expected non-match between {target_rid} and {candidate_rid}")

        # Inventory cleanup test: cleanup for rel-1-0 cleans ONLY rel-1-0 and leaves others untouched
        inventory = [
            {"id": f"res-{rid}", "type": "google_sql_database", "labels": staging_labels_by_id[rid]}
            for rid in ambiguous_ids
        ]
        deleted_ids: list[str] = []

        def track_deletion(r: Any) -> bool:
            deleted_ids.append(r["id"])
            return True

        receipt = cleanup_ephemeral_staging(
            release_id="rel-1-0",
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            dry_run=False,
            deletion_executor=track_deletion,
        )
        self.assertTrue(receipt.success)
        self.assertEqual(deleted_ids, ["res-rel-1-0"])
        self.assertEqual(len(receipt.resources), 1)

    def test_auto_cleanup_and_state_mapping_for_expired_labeled_resources(self) -> None:
        """Verify scan_orphans auto-cleanup matches labeled resources and correctly resolves state paths."""
        now = datetime(2026, 8, 25, 14, 0, 0, tzinfo=UTC)
        expired_time = now - timedelta(hours=26)
        raw_release_id = "odp-20260824-001"
        rel_label = release_label_value(raw_release_id)

        expired_labels = generate_staging_labels(
            release_id=raw_release_id,
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            created_at=expired_time,
            ttl_hours=24,
        )
        self.assertEqual(expired_labels["release_id"], rel_label)

        # Inventory item carrying only the GCP label value for release_id
        inventory = [
            {
                "id": "projects/p/instances/i/databases/stg_db",
                "type": "google_sql_database",
                "raw_release_id": raw_release_id,
                "labels": dict(expired_labels),
            }
        ]

        deleted_items: list[str] = []

        def mock_cleaner(res: Mapping[str, Any]) -> bool:
            deleted_items.append(str(res.get("id")))
            return True

        # Scan with auto_cleanup=True must successfully delete the expired resource
        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            now=now,
            auto_cleanup=True,
            deletion_executor=mock_cleaner,
        )

        self.assertEqual(result.expired_count, 1)
        self.assertEqual(result.orphan_count, 1)
        self.assertEqual(result.cleaned_count, 1)
        self.assertEqual(result.failed_cleanups, 0)
        self.assertEqual(deleted_items, ["projects/p/instances/i/databases/stg_db"])

        # Test state path resolution for both raw ID and label value
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            from product_ops.deployment.staging_lifecycle import _terraform_state_paths
            # First write a tfvars using raw release ID
            state_p, vars_p, inv_p = _terraform_state_paths(raw_release_id, tmp_path)
            vars_p.write_text(json.dumps({"release_id": raw_release_id}), encoding="utf-8")
            state_p.touch()

            # Looking up by label value must resolve to the EXACT SAME state and vars file
            lookup_state_p, lookup_vars_p, lookup_inv_p = _terraform_state_paths(rel_label, tmp_path)
            self.assertEqual(state_p.resolve(), lookup_state_p.resolve())
            self.assertEqual(vars_p.resolve(), lookup_vars_p.resolve())

    def test_label_only_inventory_is_reported_but_never_auto_deleted(self) -> None:
        """A hashed release label is not reversible and cannot authorize deletion."""
        now = datetime(2026, 8, 25, 14, 0, 0, tzinfo=UTC)
        raw_release_id = "odp-label-only-001"
        labels = generate_staging_labels(
            release_id=raw_release_id,
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            created_at=now - timedelta(hours=26),
            ttl_hours=24,
        )
        deleted: list[str] = []

        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=[
                {"id": "label-only", "type": "google_sql_database", "labels": labels}
            ],
            now=now,
            auto_cleanup=True,
            deletion_executor=lambda resource: deleted.append(str(resource["id"])) or True,
        )

        self.assertEqual(result.expired_count, 1)
        self.assertEqual(result.cleaned_count, 0)
        self.assertEqual(result.failed_cleanups, 1)
        self.assertEqual(deleted, [])
        self.assertIn("authoritative raw_release_id", result.orphan_resources[0]["reason"])

    def test_rerun_create_preserves_authoritative_created_at(self) -> None:
        """Verify that rerunning create for the same release preserves existing created_at and does not refresh TTL."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            first_time = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
            second_time = datetime(2026, 8, 24, 14, 0, 0, tzinfo=UTC)  # 2 hours later

            cfg = dataclasses_replace(self.valid_config, created_at="", release_id="odp-rerun-test-001")
            planned_first = plan_staging_resources(cfg, created_at=first_time)

            from product_ops.deployment.staging_lifecycle import (
                _terraform_state_paths,
                make_terraform_creation_executor,
            )
            state_p, vars_p, inv_p = _terraform_state_paths(cfg.release_id, tmp_path)

            # Mock executor to simulate Terraform apply by writing files
            executor = make_terraform_creation_executor(
                module_dir=MODULE_DIR,
                state_dir=tmp_path,
                initialize=False,
            )
            # Patch _run_terraform inside executor
            import unittest.mock as mock
            with mock.patch("product_ops.deployment.staging_lifecycle._run_terraform"):
                # First run at 12:00:00Z
                executor(cfg, planned_first)
                self.assertTrue(vars_p.is_file())
                first_vars = json.loads(vars_p.read_text(encoding="utf-8"))
                self.assertEqual(first_vars["created_at"], "2026-08-24T12:00:00Z")

                # Second run at 14:00:00Z without created_at (simulate CLI rerun)
                planned_second = plan_staging_resources(cfg, created_at=second_time)
                executor(cfg, planned_second)
                second_vars = json.loads(vars_p.read_text(encoding="utf-8"))

                # Authoritative timestamp MUST NOT have changed to 14:00:00Z
                self.assertEqual(second_vars["created_at"], "2026-08-24T12:00:00Z")
                inv_data = json.loads(inv_p.read_text(encoding="utf-8"))
                self.assertEqual(inv_data[0]["created_at"], "2026-08-24T12:00:00Z")
                self.assertEqual(inv_data[0]["expires_at"], "2026-08-25T12:00:00Z")

    def test_create_failure_triggers_exact_cleanup(self) -> None:
        """Verify that create failure automatically executes failure-path cleanup to delete partial resources."""
        cleaned_resources: list[str] = []

        def failing_creator(cfg: StagingConfig, res: Any) -> bool:
            raise RuntimeError("Cloud Run API quota exceeded during service deployment")

        def mock_cleaner(res: Mapping[str, Any]) -> bool:
            cleaned_resources.append(str(res.get("type")))
            return True

        receipt = create_ephemeral_staging(
            self.valid_config,
            dry_run=False,
            creation_executor=failing_creator,
            cleanup_executor=mock_cleaner,
        )

        self.assertFalse(receipt.success)
        self.assertTrue(receipt.remediation_required)
        self.assertTrue(any("quota exceeded" in err for err in receipt.errors))
        # Failure cleanup must have been called for planned resources
        self.assertTrue(len(cleaned_resources) > 0)
        self.assertIn("failure_cleanup_receipt", receipt.metadata)
        self.assertTrue(receipt.metadata["failure_cleanup_receipt"]["success"])
        self.assertIn("failure-path cleanup succeeded", receipt.remediation_notes)

    def test_future_creation_timestamp_is_rejected(self) -> None:
        """Verify that created_at in the future is rejected to prevent bypassing TTL policy."""
        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)

        # Future timestamp in 2099
        future_cfg = dataclasses_replace(self.valid_config, created_at="2099-01-01T00:00:00Z")
        errors = validate_staging_config(future_cfg, now=now)
        self.assertTrue(any("cannot be in the future" in err for err in errors))

        # Future timestamp +1 hour
        near_future_cfg = dataclasses_replace(self.valid_config, created_at="2026-08-24T13:00:00Z")
        errors = validate_staging_config(near_future_cfg, now=now)
        self.assertTrue(any("cannot be in the future" in err for err in errors))

        # Timestamp within 5-min clock skew is allowed
        skew_cfg = dataclasses_replace(self.valid_config, created_at="2026-08-24T12:03:00Z")
        self.assertEqual(validate_staging_config(skew_cfg, now=now), [])

        # create_ephemeral_staging with future timestamp fails
        receipt = create_ephemeral_staging(future_cfg, dry_run=True, now=now)
        self.assertFalse(receipt.success)
        self.assertTrue(any("cannot be in the future" in err for err in receipt.errors))

        # extend_staging_ttl with future created_at raises ValueError
        with self.assertRaises(ValueError):
            extend_staging_ttl(
                release_id="odp-20260824-001",
                extend_hours=12,
                reason="debugging",
                owner="Antigravity",
                current_expires_at=datetime(2099, 1, 2, 0, 0, 0, tzinfo=UTC),
                created_at=datetime(2099, 1, 1, 0, 0, 0, tzinfo=UTC),
                now=now,
            )

    def test_validate_module_contract(self) -> None:
        errors = validate_module_contract(MODULE_DIR)
        self.assertEqual(errors, [])

    def test_workflow_entrypoints_are_unmodified(self) -> None:
        # Check that runtime workflow files are preserved and no secondary workflow created
        workflows_dir = ROOT / ".github/workflows"
        if workflows_dir.is_dir():
            files = [f.name for f in workflows_dir.iterdir() if f.is_file()]
            # Ensure deploy-dev.yml is present and untouched
            self.assertIn("deploy-dev.yml", files)

    def test_scan_orphans_active_over_policy_resource_is_remediation_only_and_not_auto_deleted(self) -> None:
        """Verify that an active resource whose TTL exceeds scanner max_ttl_hours is remediation-only and never auto-deleted."""
        now = datetime(2026, 8, 25, 14, 0, 0, tzinfo=UTC)
        created_time = datetime(2026, 8, 25, 14, 0, 0, tzinfo=UTC)

        labels = generate_staging_labels(
            release_id="odp-legal-168h-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            created_at=created_time,
            ttl_hours=168,
        )

        inventory = [
            {
                "id": "active-168h-res",
                "type": "google_sql_database",
                "raw_release_id": "odp-legal-168h-001",
                "labels": labels,
            },
        ]

        deleted_items: list[str] = []

        def mock_cleaner(res: Mapping[str, Any]) -> bool:
            deleted_items.append(str(res.get("id")))
            return True

        # Under default 24h scanner with auto_cleanup=True:
        # Must be active=1, expired=0, orphan=1, cleaned=0 (auto-deletion refused, remediation task generated)
        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            max_ttl_hours=24,
            now=now,
            auto_cleanup=True,
            deletion_executor=mock_cleaner,
        )

        self.assertEqual(result.active_count, 1)
        self.assertEqual(result.expired_count, 0)
        self.assertEqual(result.orphan_count, 1)
        self.assertEqual(result.cleaned_count, 0)
        self.assertEqual(deleted_items, [])
        self.assertEqual(result.failed_cleanups, 1)
        self.assertEqual(len(result.remediation_tasks), 1)
        self.assertEqual(result.remediation_tasks[0]["task_type"], "ephemeral_staging_orphan_remediation")
        self.assertTrue(any("active" in err.lower() and "automatic deletion refused" in err.lower() for err in result.remediation_tasks[0]["errors"]))
        self.assertTrue(any("exceeds maximum TTL" in alert for alert in result.alerts))

        # Under 168h scanner:
        # Resource is within policy and active -> orphan_count=0, remediation_tasks=0
        result_168 = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            max_ttl_hours=168,
            now=now,
            auto_cleanup=True,
            deletion_executor=mock_cleaner,
        )
        self.assertEqual(result_168.active_count, 1)
        self.assertEqual(result_168.expired_count, 0)
        self.assertEqual(result_168.orphan_count, 0)
        self.assertEqual(result_168.cleaned_count, 0)
        self.assertEqual(result_168.failed_cleanups, 0)
        self.assertEqual(len(result_168.remediation_tasks), 0)

    def test_scan_orphans_expired_over_policy_resource_is_auto_deleted_when_expired(self) -> None:
        """Verify that an over-policy resource is safely auto-deleted once it actually expires."""
        created_time = datetime(2026, 8, 25, 14, 0, 0, tzinfo=UTC)
        now_after_expiry = datetime(2026, 9, 2, 14, 0, 0, tzinfo=UTC)  # Past expiry

        labels = generate_staging_labels(
            release_id="odp-expired-168h-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            created_at=created_time,
            ttl_hours=168,
        )

        inventory = [
            {
                "id": "expired-168h-res",
                "type": "google_sql_database",
                "raw_release_id": "odp-expired-168h-001",
                "labels": labels,
            },
        ]

        deleted_items: list[str] = []

        def mock_cleaner(res: Mapping[str, Any]) -> bool:
            deleted_items.append(str(res.get("id")))
            return True

        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            max_ttl_hours=24,
            now=now_after_expiry,
            auto_cleanup=True,
            deletion_executor=mock_cleaner,
        )

        self.assertEqual(result.active_count, 0)
        self.assertEqual(result.expired_count, 1)
        self.assertEqual(result.orphan_count, 1)
        self.assertEqual(result.cleaned_count, 1)
        self.assertEqual(result.failed_cleanups, 0)
        self.assertEqual(deleted_items, ["expired-168h-res"])
        self.assertEqual(len(result.remediation_tasks), 0)

    def test_scan_orphans_validates_max_ttl_hours_bounds(self) -> None:
        """Verify that scan_orphans rejects max_ttl_hours outside 1..168."""
        inventory: list[dict[str, Any]] = []

        for bad_ttl in (0, -1, -24, 169, 500):
            with self.assertRaises(ValueError):
                scan_orphans(
                    project_id="oday-staging-proj",
                    resource_inventory=inventory,
                    max_ttl_hours=bad_ttl,
                )

        # Valid bounds work without error
        res_min = scan_orphans(project_id="oday-staging-proj", resource_inventory=inventory, max_ttl_hours=1)
        self.assertEqual(res_min.total_scanned, 0)
        res_max = scan_orphans(project_id="oday-staging-proj", resource_inventory=inventory, max_ttl_hours=168)
        self.assertEqual(res_max.total_scanned, 0)

    def test_cli_scan_orphans_validates_max_ttl_hours(self) -> None:
        """Verify that scan-orphans CLI command rejects invalid max_ttl_hours bounds."""
        import tempfile

        from product_ops.deployment.staging_lifecycle import main

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            tf.write("[]")
            tf_path = tf.name

        try:
            # 0 must exit 1
            code_0 = main(["scan-orphans", "--project-id", "oday-staging-proj", "--inventory-file", tf_path, "--max-ttl-hours", "0"])
            self.assertEqual(code_0, 1)

            # 200 must exit 1
            code_200 = main(["scan-orphans", "--project-id", "oday-staging-proj", "--inventory-file", tf_path, "--max-ttl-hours", "200"])
            self.assertEqual(code_200, 1)

            # 24 must exit 0
            code_24 = main(["scan-orphans", "--project-id", "oday-staging-proj", "--inventory-file", tf_path, "--max-ttl-hours", "24"])
            self.assertEqual(code_24, 0)
        finally:
            Path(tf_path).unlink(missing_ok=True)

    def test_rerun_create_rejects_mismatched_candidate_sha_and_preserves_existing_state(self) -> None:
        """Regression test for Rollout Plan §5.2:

        Rerunning create for an existing release with a different candidate_sha must be
        rejected without modifying existing tfvars, inventory, or live state.
        """
        import tempfile
        import unittest.mock as mock

        from product_ops.deployment.staging_lifecycle import (
            _terraform_state_paths,
            make_terraform_creation_executor,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            first_time = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)

            # First run: candidate 'a'*40, manifest 'b'*64
            cfg1 = dataclasses_replace(
                self.valid_config,
                release_id="odp-immutable-test-001",
                candidate_sha="a" * 40,
                manifest_digest="sha256:" + "b" * 64,
                created_at="2026-08-24T12:00:00Z",
            )
            planned_first = plan_staging_resources(cfg1, created_at=first_time)
            _, vars_p, inv_p = _terraform_state_paths(cfg1.release_id, tmp_path)

            executor = make_terraform_creation_executor(
                module_dir=MODULE_DIR,
                state_dir=tmp_path,
                initialize=False,
            )

            with mock.patch("product_ops.deployment.staging_lifecycle._run_terraform"):
                # First run succeeds and writes initial tfvars/inventory
                executor(cfg1, planned_first)
                self.assertTrue(vars_p.is_file())
                first_vars = json.loads(vars_p.read_text(encoding="utf-8"))
                self.assertEqual(first_vars["candidate_sha"], "a" * 40)
                self.assertEqual(first_vars["manifest_digest"], "sha256:" + "b" * 64)

                # Second run: same release, but candidate_sha changed to 'e'*40
                cfg2 = dataclasses_replace(
                    cfg1,
                    candidate_sha="e" * 40,
                    manifest_digest="sha256:" + "f" * 64,
                )
                planned_second = plan_staging_resources(cfg2, created_at=first_time)

                with self.assertRaises(RuntimeError) as ctx:
                    executor(cfg2, planned_second)

                self.assertIn("immutable candidate_sha", str(ctx.exception).lower())
                self.assertIn("5.2", str(ctx.exception))

                # Verify persisted tfvars was NOT modified and still has original 'a'*40 and 'b'*64
                persisted_vars = json.loads(vars_p.read_text(encoding="utf-8"))
                self.assertEqual(persisted_vars["candidate_sha"], "a" * 40)
                self.assertEqual(persisted_vars["manifest_digest"], "sha256:" + "b" * 64)

                # Verify persisted inventory was NOT modified
                persisted_inv = json.loads(inv_p.read_text(encoding="utf-8"))
                self.assertEqual(persisted_inv[0]["labels"]["candidate_sha"], "a" * 40)

    def test_rerun_create_rejects_mismatched_manifest_digest(self) -> None:
        """Verify that rerunning with a different manifest_digest on the same release is rejected."""
        import tempfile
        import unittest.mock as mock

        from product_ops.deployment.staging_lifecycle import (
            _terraform_state_paths,
            make_terraform_creation_executor,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            first_time = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)

            cfg1 = dataclasses_replace(
                self.valid_config,
                release_id="odp-manifest-test-001",
                candidate_sha="a" * 40,
                manifest_digest="sha256:" + "b" * 64,
                created_at="2026-08-24T12:00:00Z",
            )
            planned = plan_staging_resources(cfg1, created_at=first_time)
            _, vars_p, _ = _terraform_state_paths(cfg1.release_id, tmp_path)

            executor = make_terraform_creation_executor(
                module_dir=MODULE_DIR,
                state_dir=tmp_path,
                initialize=False,
            )

            with mock.patch("product_ops.deployment.staging_lifecycle._run_terraform"):
                executor(cfg1, planned)

                # Same candidate, but different manifest digest
                cfg2 = dataclasses_replace(
                    cfg1,
                    manifest_digest="sha256:" + "f" * 64,
                )
                with self.assertRaises(RuntimeError) as ctx:
                    executor(cfg2, planned)

                self.assertIn("immutable manifest_digest", str(ctx.exception).lower())
                persisted_vars = json.loads(vars_p.read_text(encoding="utf-8"))
                self.assertEqual(persisted_vars["manifest_digest"], "sha256:" + "b" * 64)

    def test_rerun_create_rejects_mismatched_images_or_project(self) -> None:
        """Verify that rerunning with changed container images or project_id is rejected."""
        import tempfile
        import unittest.mock as mock

        from product_ops.deployment.staging_lifecycle import make_terraform_creation_executor

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            first_time = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)

            cfg1 = dataclasses_replace(
                self.valid_config,
                release_id="odp-img-test-001",
                created_at="2026-08-24T12:00:00Z",
            )
            planned = plan_staging_resources(cfg1, created_at=first_time)

            executor = make_terraform_creation_executor(
                module_dir=MODULE_DIR,
                state_dir=tmp_path,
                initialize=False,
            )

            with mock.patch("product_ops.deployment.staging_lifecycle._run_terraform"):
                executor(cfg1, planned)

                # Changed api_image
                cfg_api = dataclasses_replace(
                    cfg1,
                    api_image="asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "9" * 64,
                )
                with self.assertRaises(RuntimeError) as ctx:
                    executor(cfg_api, planned)
                self.assertIn("immutable api_image", str(ctx.exception).lower())

                # Changed web_image
                cfg_web = dataclasses_replace(
                    cfg1,
                    web_image="asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "8" * 64,
                )
                with self.assertRaises(RuntimeError) as ctx:
                    executor(cfg_web, planned)
                self.assertIn("immutable web_image", str(ctx.exception).lower())

                # Changed project_id
                cfg_proj = dataclasses_replace(
                    cfg1,
                    project_id="other-staging-proj",
                )
                with self.assertRaises(RuntimeError) as ctx:
                    executor(cfg_proj, planned)
                self.assertIn("project_id", str(ctx.exception).lower())

    def test_rerun_create_allows_idempotent_reapply_with_matching_identity(self) -> None:
        """Verify that rerunning create with the exact same immutable identity succeeds idempotently."""
        import tempfile
        import unittest.mock as mock

        from product_ops.deployment.staging_lifecycle import (
            _terraform_state_paths,
            make_terraform_creation_executor,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            first_time = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)

            cfg = dataclasses_replace(
                self.valid_config,
                release_id="odp-idempotent-001",
                created_at="2026-08-24T12:00:00Z",
            )
            planned = plan_staging_resources(cfg, created_at=first_time)
            _, vars_p, _ = _terraform_state_paths(cfg.release_id, tmp_path)

            executor = make_terraform_creation_executor(
                module_dir=MODULE_DIR,
                state_dir=tmp_path,
                initialize=False,
            )

            with mock.patch("product_ops.deployment.staging_lifecycle._run_terraform") as mock_tf:
                # First run
                res1 = executor(cfg, planned)
                self.assertTrue(res1)
                self.assertEqual(mock_tf.call_count, 1)  # apply only since initialize=False

                # Second run with same config and empty created_at
                cfg_rerun = dataclasses_replace(cfg, created_at="")
                res2 = executor(cfg_rerun, planned)
                self.assertTrue(res2)
                self.assertEqual(mock_tf.call_count, 2)  # 2 applies total

                persisted = json.loads(vars_p.read_text(encoding="utf-8"))
                self.assertEqual(persisted["created_at"], "2026-08-24T12:00:00Z")
                self.assertEqual(persisted["candidate_sha"], cfg.candidate_sha)
                self.assertEqual(persisted["manifest_digest"], cfg.manifest_digest)

    def test_create_conflict_does_not_trigger_failure_cleanup_of_existing_release(self) -> None:
        """Verify that a rejected create rerun due to immutable identity conflict does NOT destroy existing release."""
        cleaned: list[str] = []

        def failing_creator(cfg: StagingConfig, res: Any) -> bool:
            raise RuntimeError(
                "Existing release state conflict for 'odp-safe-001': "
                "Existing release state has immutable candidate_sha 'a'*40; rerun with candidate_sha 'e'*40 is rejected."
            )

        def mock_cleaner(res: Mapping[str, Any]) -> bool:
            cleaned.append(str(res.get("id")))
            return True

        receipt = create_ephemeral_staging(
            self.valid_config,
            dry_run=False,
            creation_executor=failing_creator,
            cleanup_executor=mock_cleaner,
        )

        self.assertFalse(receipt.success)
        self.assertFalse(receipt.remediation_required)
        self.assertEqual(cleaned, [])  # Cleanup executor MUST NOT be invoked!
        self.assertIn("conflict", receipt.remediation_notes.lower())

    def test_cli_create_dry_run_rejects_immutable_identity_conflict(self) -> None:
        """Verify that CLI create --dry-run rejects mismatched immutable identity if state exists."""
        import tempfile

        from product_ops.deployment.staging_lifecycle import (
            _terraform_state_paths,
            generate_tfvars,
            main,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            rel_id = "odp-cli-conflict-001"

            # Create existing tfvars with candidate_sha 'a'*40
            _, vars_p, _ = _terraform_state_paths(rel_id, tmp_path)
            cfg1 = dataclasses_replace(
                self.valid_config,
                release_id=rel_id,
                candidate_sha="a" * 40,
                manifest_digest="sha256:" + "b" * 64,
                created_at="2026-08-24T12:00:00Z",
            )
            vars_p.write_text(json.dumps(generate_tfvars(cfg1), indent=2), encoding="utf-8")

            # Run CLI create in dry-run with candidate_sha 'e'*40
            argv = [
                "create",
                "--release-id", rel_id,
                "--candidate-sha", "e" * 40,
                "--manifest-digest", "sha256:" + "b" * 64,
                "--project-id", "oday-staging-proj",
                "--api-image", self.valid_config.api_image,
                "--web-image", self.valid_config.web_image,
                "--worker-image", self.valid_config.worker_image,
                "--scheduler-image", self.valid_config.scheduler_image,
                "--owner-task-id", "ODP-EPHEMERAL-STAGING-IAC-001",
                "--release-profile", "full",
                "--state-dir", str(tmp_path),
                "--dry-run",
            ]
            exit_code = main(argv)
            self.assertEqual(exit_code, 1)

    def test_validate_immutable_release_identity_direct_cases(self) -> None:
        """Direct unit tests for validate_immutable_release_identity."""
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths, generate_tfvars

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            rel_id = "odp-direct-val-001"

            # Non-existent state dir returns no errors
            self.assertEqual(validate_immutable_release_identity(self.valid_config, tmp_path / "nonexistent"), [])

            # Write tfvars
            _, vars_p, inv_p = _terraform_state_paths(rel_id, tmp_path)
            cfg_base = dataclasses_replace(
                self.valid_config,
                release_id=rel_id,
                candidate_sha="a" * 40,
                manifest_digest="sha256:" + "b" * 64,
                created_at="2026-08-24T12:00:00Z",
            )
            vars_p.write_text(json.dumps(generate_tfvars(cfg_base), indent=2), encoding="utf-8")

            # Matching config -> empty errors
            self.assertEqual(validate_immutable_release_identity(cfg_base, tmp_path), [])

            # Mismatched candidate_sha
            cfg_bad_sha = dataclasses_replace(cfg_base, candidate_sha="f" * 40)
            errs = validate_immutable_release_identity(cfg_bad_sha, tmp_path)
            self.assertTrue(any("candidate_sha" in e for e in errs))

            # Mismatched manifest_digest
            cfg_bad_md = dataclasses_replace(cfg_base, manifest_digest="sha256:" + "9" * 64)
            errs = validate_immutable_release_identity(cfg_bad_md, tmp_path)
            self.assertTrue(any("manifest_digest" in e for e in errs))

            # Mismatched created_at
            cfg_bad_created = dataclasses_replace(cfg_base, created_at="2026-08-24T15:00:00Z")
            errs = validate_immutable_release_identity(cfg_bad_created, tmp_path)
            self.assertTrue(any("authoritative created_at" in e for e in errs))

    def test_tenant_and_owner_punctuation_normalization_and_labels(self) -> None:
        from product_ops.deployment.staging_lifecycle import (
            bounded_label_value,
            tenant_label_value,
        )

        cfg = dataclasses_replace(
            self.valid_config,
            tenant_id="custom_tenant",
            owner_task_id="ODP_TASK_001",
        )

        # 1. Config validation passes
        self.assertEqual(validate_staging_config(cfg), [])

        # 2. Label generation preserves underscores
        labels = generate_staging_labels(
            release_id=cfg.release_id,
            candidate_sha=cfg.candidate_sha,
            manifest_digest=cfg.manifest_digest,
            owner_task_id=cfg.owner_task_id,
            tenant_id=cfg.tenant_id,
        )
        self.assertEqual(labels["tenant"], "custom_tenant")
        self.assertEqual(labels["owner_task"], "odp_task_001")
        self.assertEqual(bounded_label_value("custom_tenant"), "custom_tenant")
        self.assertEqual(bounded_label_value("ODP_TASK_001"), "odp_task_001")
        self.assertEqual(tenant_label_value("custom_tenant"), "custom_tenant")

        # 3. Planned resources have consistent tenant and owner labels
        planned = plan_staging_resources(cfg)
        for r in planned:
            self.assertEqual(r.labels["tenant"], "custom_tenant")
            self.assertEqual(r.labels["owner_task"], "odp_task_001")

        # 4. TFVars generation retains exact tenant_id and owner_task_id
        tfvars = generate_tfvars(cfg, created_at=datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC))
        self.assertEqual(tfvars["tenant_id"], "custom_tenant")
        self.assertEqual(tfvars["owner_task_id"], "ODP_TASK_001")

    def test_created_at_future_timestamp_guard(self) -> None:
        # Future timestamp in config validation
        future_cfg = dataclasses_replace(self.valid_config, created_at="2099-01-01T00:00:00Z")
        now_dt = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        errs = validate_staging_config(future_cfg, now=now_dt)
        self.assertTrue(any("cannot be in the future" in e for e in errs))

        # generate_tfvars rejects future created_at
        with self.assertRaises(ValueError) as ctx:
            generate_tfvars(future_cfg, now=now_dt)
        self.assertIn("cannot be in the future", str(ctx.exception))


class DefaultTenantTfvarsContractTests(unittest.TestCase):
    """Regression cover for the default (no explicit tenant) create path.

    ``generate_tfvars`` used to emit ``tenant_id: ""`` whenever ``--tenant-id``
    was omitted, which is the CLI default. The module's ``tenant_id`` validation
    only accepted null or a valid identifier, so every normal live create failed
    closed with "Invalid value for variable" before provisioning anything.
    """

    def setUp(self) -> None:
        self.release_id = "odp-20260824-001"
        self.config = StagingConfig(
            release_id=self.release_id,
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            project_id="oday-staging-proj",
            kms_key_id="projects/oday-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
            deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )

    def test_default_tfvars_tenant_is_never_empty(self) -> None:
        self.assertEqual(self.config.tenant_id, "")

        tfvars = generate_tfvars(self.config)

        self.assertNotEqual(tfvars["tenant_id"], "")
        self.assertEqual(tfvars["tenant_id"], derive_release_tenant_id(self.release_id))
        self.assertIsNotNone(TENANT_ID_PATTERN.fullmatch(tfvars["tenant_id"]))

    def test_default_tfvars_satisfy_module_variable_validations(self) -> None:
        tfvars = generate_tfvars(self.config)

        self.assertEqual(validate_tfvars_against_module(tfvars, MODULE_DIR), [])

    def test_empty_tenant_tfvars_would_be_caught_by_module_contract(self) -> None:
        """The module tolerates an empty tenant, but tfvars must still pin one."""
        declarations = parse_module_variables((MODULE_DIR / "variables.tf").read_text(encoding="utf-8"))

        tenant_declaration = declarations["tenant_id"]
        self.assertTrue(tenant_declaration["allows_null"])
        self.assertTrue(tenant_declaration["allows_empty"])
        self.assertTrue(tenant_declaration["patterns"])

        # An empty string does not satisfy the declared identifier pattern, which
        # is exactly why the producer must resolve it before writing tfvars.
        for pattern in tenant_declaration["patterns"]:
            self.assertIsNone(re.search(pattern, ""))

    def test_module_contract_rejects_a_producer_that_drops_the_tenant(self) -> None:
        """validate_module_contract must fail if generate_tfvars regresses."""
        import product_ops.deployment.staging_lifecycle as lifecycle

        original = lifecycle.generate_tfvars

        def empty_tenant_tfvars(*args: Any, **kwargs: Any) -> dict[str, Any]:
            tfvars = original(*args, **kwargs)
            tfvars["tenant_id"] = ""
            return tfvars

        lifecycle.generate_tfvars = empty_tenant_tfvars
        try:
            errors = lifecycle.validate_module_contract(MODULE_DIR)
        finally:
            lifecycle.generate_tfvars = original

        self.assertTrue(
            any("release-derived tenant_id" in err for err in errors),
            f"module contract did not catch an empty generated tenant_id: {errors}",
        )

    def test_variable_parser_ignores_comments_and_quoted_braces(self) -> None:
        sample = """
variable "with_comment" {
  type = string
  # a comment with a brace { and a lone quote "
  // another } comment
  default = "x"

  validation {
    condition     = can(regex("^[a-z]+$", var.with_comment))
    error_message = "bad"
  }
}

variable "after_comment" {
  type = string
}
"""
        declarations = parse_module_variables(sample)

        self.assertEqual(set(declarations), {"with_comment", "after_comment"})
        self.assertEqual(declarations["with_comment"]["patterns"], ["^[a-z]+$"])
        self.assertTrue(declarations["with_comment"]["has_default"])
        self.assertFalse(declarations["after_comment"]["has_default"])

    def test_missing_required_variable_is_reported(self) -> None:
        tfvars = generate_tfvars(self.config)
        tfvars.pop("kms_key_id")

        errors = validate_tfvars_against_module(tfvars, MODULE_DIR)

        self.assertTrue(any("kms_key_id" in err for err in errors), errors)

    def test_undeclared_tfvars_key_is_reported(self) -> None:
        tfvars = generate_tfvars(self.config)
        tfvars["not_a_module_variable"] = "x"

        errors = validate_tfvars_against_module(tfvars, MODULE_DIR)

        self.assertTrue(any("not_a_module_variable" in err for err in errors), errors)

    def test_derived_tenant_is_deterministic_and_release_scoped(self) -> None:
        first = derive_release_tenant_id(self.release_id)
        second = derive_release_tenant_id(self.release_id)
        other = derive_release_tenant_id("odp-20260824-002")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(first, f"tenant-{self.release_id}-{compute_release_hash(self.release_id)}")

    def test_derived_tenant_stays_within_module_length_limit(self) -> None:
        long_release = "odp-" + "x" * 120
        derived = derive_release_tenant_id(long_release)

        self.assertLessEqual(len(derived), MAX_TENANT_ID_LENGTH)
        self.assertIsNotNone(TENANT_ID_PATTERN.fullmatch(derived))
        # Uniqueness survives truncation because the hash covers the raw release id.
        self.assertNotEqual(derived, derive_release_tenant_id(long_release + "y"))

        long_config = dataclasses_replace(self.config, release_id=long_release)
        self.assertEqual(validate_tfvars_against_module(generate_tfvars(long_config), MODULE_DIR), [])

    def test_tenant_resolution_is_shared_by_every_layer(self) -> None:
        derived = derive_release_tenant_id(self.release_id)

        tfvars = generate_tfvars(self.config)
        names = get_ephemeral_resource_names(self.release_id, self.config.project_id)
        labels = generate_staging_labels(
            release_id=self.release_id,
            candidate_sha=self.config.candidate_sha,
            manifest_digest=self.config.manifest_digest,
            owner_task_id=self.config.owner_task_id,
        )

        self.assertEqual(tfvars["tenant_id"], derived)
        self.assertEqual(names["tenant_id"], derived)
        self.assertEqual(labels["tenant"], derived)
        self.assertEqual(resolve_tenant_id(self.release_id), derived)
        self.assertEqual(resolve_tenant_id(self.release_id, "explicit-tenant"), "explicit-tenant")

    def test_default_tenant_rerun_is_idempotent_and_tenant_change_is_rejected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            tfvars_path = state_root / "odp-20260824-001.tfvars.json"
            tfvars_path.write_text(json.dumps(generate_tfvars(self.config)), encoding="utf-8")

            # Same release, tenant still omitted: resolves to the recorded tenant.
            self.assertEqual(validate_immutable_release_identity(self.config, state_root), [])

            # Re-supplying the derived tenant explicitly is still the same tenant.
            same = dataclasses_replace(self.config, tenant_id=derive_release_tenant_id(self.release_id))
            self.assertEqual(validate_immutable_release_identity(same, state_root), [])

            # Switching to a different tenant on an existing release is rejected.
            switched = dataclasses_replace(self.config, tenant_id="other-tenant")
            errors = validate_immutable_release_identity(switched, state_root)
            self.assertTrue(any("immutable tenant_id" in err for err in errors), errors)


class UnverifiableReleaseStateTests(unittest.TestCase):
    """Existing release state that cannot be parsed must fail closed.

    A swallowed parse error made ``validate_immutable_release_identity`` return
    ``[]``, which reads as "nothing provisioned yet". The live apply then failed
    on the same unreadable file and ``create_ephemeral_staging`` ran failure-path
    cleanup against the exact labels of the release that was already there.
    """

    def setUp(self) -> None:
        self.release_id = "odp-unreadable-001"
        self.config = StagingConfig(
            release_id=self.release_id,
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            project_id="oday-staging-proj",
            region="asia-east1",
            cloud_sql_instance_name="oday-staging-pg",
            cloud_sql_connection_name="oday-staging-proj:asia-east1:oday-staging-pg",
            network_name="oday-staging-vpc",
            subnetwork_name="oday-staging-subnet",
            kms_key_id="projects/oday-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
            deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
            api_image="asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "c" * 64,
            web_image="asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "d" * 64,
            ttl_hours=24,
            created_at="2026-08-24T12:00:00Z",
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )
        self.now = datetime(2026, 8, 24, 13, 0, 0, tzinfo=UTC)

    def test_unparsable_tfvars_is_an_identity_error_not_an_empty_result(self) -> None:
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths

        for payload in ("{ this is not json", '"a string"', "[]", "null"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmpdir:
                state_root = Path(tmpdir)
                _, vars_p, _ = _terraform_state_paths(self.release_id, state_root)
                vars_p.write_text(payload, encoding="utf-8")

                errors = validate_immutable_release_identity(self.config, state_root)
                self.assertTrue(errors, f"{payload!r} must not validate as 'no existing release'")
                self.assertTrue(
                    all(err.startswith(UNVERIFIABLE_STATE_PREFIX) for err in errors),
                    errors,
                )

    def test_unparsable_inventory_is_an_identity_error(self) -> None:
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            _, vars_p, inv_p = _terraform_state_paths(self.release_id, state_root)
            vars_p.write_text(json.dumps(generate_tfvars(self.config)), encoding="utf-8")
            inv_p.write_text("{not json at all", encoding="utf-8")

            errors = validate_immutable_release_identity(self.config, state_root)
            self.assertTrue(errors)
            self.assertTrue(any(err.startswith(UNVERIFIABLE_STATE_PREFIX) for err in errors), errors)
            self.assertTrue(any("inventory file" in err for err in errors), errors)

    def test_readable_matching_state_still_validates_clean(self) -> None:
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            _, vars_p, _ = _terraform_state_paths(self.release_id, state_root)
            vars_p.write_text(json.dumps(generate_tfvars(self.config)), encoding="utf-8")
            self.assertEqual(validate_immutable_release_identity(self.config, state_root), [])

    def test_creation_executor_raises_a_typed_unverifiable_conflict(self) -> None:
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            _, vars_p, _ = _terraform_state_paths(self.release_id, state_root)
            vars_p.write_text("{ corrupt", encoding="utf-8")

            executor = make_terraform_creation_executor(
                module_dir=MODULE_DIR,
                state_dir=state_root,
                terraform_bin="/bin/true",
                initialize=False,
            )
            planned = plan_staging_resources(
                self.config,
                created_at=datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC),
                now=self.now,
            )
            with self.assertRaises(ReleaseStateUnverifiable) as ctx:
                executor(self.config, planned)
            self.assertIn("Existing release state conflict", str(ctx.exception))
            self.assertTrue(issubclass(ReleaseStateUnverifiable, ReleaseIdentityConflict))
            # The corrupt file is evidence for the operator and must survive.
            self.assertEqual(vars_p.read_text(encoding="utf-8"), "{ corrupt")

    def test_unreadable_state_create_preserves_release_and_never_cleans_up(self) -> None:
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            state_p, vars_p, inv_p = _terraform_state_paths(self.release_id, state_root)
            state_p.write_text('{"live": "terraform state"}', encoding="utf-8")
            vars_p.write_text("{ corrupt", encoding="utf-8")

            cleaned: list[str] = []

            receipt = create_ephemeral_staging(
                self.config,
                dry_run=False,
                now=self.now,
                creation_executor=make_terraform_creation_executor(
                    module_dir=MODULE_DIR,
                    state_dir=state_root,
                    terraform_bin="/bin/true",
                    initialize=False,
                ),
                cleanup_executor=lambda res: cleaned.append(str(res.get("id"))) or True,
            )

            self.assertFalse(receipt.success)
            # The whole point: no exact-label cleanup of the existing release.
            self.assertEqual(cleaned, [])
            self.assertNotIn("failure_cleanup_receipt", receipt.metadata)
            self.assertTrue(receipt.metadata.get("existing_release_state_preserved"))
            self.assertTrue(receipt.metadata.get("release_state_unverifiable"))
            # Unreadable state is not a clean refusal; a human has to look at it.
            self.assertTrue(receipt.remediation_required)
            self.assertIn("preserved", receipt.remediation_notes.lower())
            # Existing state files are untouched and no new tfvars was written.
            self.assertEqual(state_p.read_text(encoding="utf-8"), '{"live": "terraform state"}')
            self.assertEqual(vars_p.read_text(encoding="utf-8"), "{ corrupt")
            self.assertFalse(inv_p.exists())

    def test_plain_identity_conflict_stays_remediation_free(self) -> None:
        cleaned: list[str] = []

        def conflicting_creator(cfg: StagingConfig, res: Any) -> bool:
            raise ReleaseIdentityConflict(
                f"Existing release state conflict for {cfg.release_id!r}: immutable candidate_sha"
            )

        receipt = create_ephemeral_staging(
            self.config,
            dry_run=False,
            now=self.now,
            creation_executor=conflicting_creator,
            cleanup_executor=lambda res: cleaned.append(str(res.get("id"))) or True,
        )

        self.assertFalse(receipt.success)
        self.assertEqual(cleaned, [])
        self.assertFalse(receipt.remediation_required)
        self.assertFalse(receipt.metadata.get("release_state_unverifiable", False))

    def test_orphan_tfstate_without_sidecars_fails_closed(self) -> None:
        """Regression: tfstate exists but no tfvars/inventory → unverifiable, not empty.

        Before the fix, validate_immutable_release_identity returned [] when
        only .tfstate existed (missing sidecars), which let
        make_terraform_creation_executor write new tfvars/inventory and apply
        against pre-existing state whose identity was unknown.
        """
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            state_p, vars_p, inv_p = _terraform_state_paths(self.release_id, state_root)

            # Create ONLY the .tfstate file — no tfvars, no inventory
            state_p.write_text('{"version": 4, "resources": []}', encoding="utf-8")
            self.assertTrue(state_p.is_file())
            self.assertFalse(vars_p.is_file())
            self.assertFalse(inv_p.is_file())

            errors = validate_immutable_release_identity(self.config, state_root)

            # Must NOT be empty — that would allow overwriting unknown state
            self.assertTrue(errors, "Orphan tfstate with no sidecars must not validate as 'no existing release'")
            self.assertTrue(
                all(err.startswith(UNVERIFIABLE_STATE_PREFIX) for err in errors),
                f"All errors must be unverifiable-prefixed, got: {errors}",
            )
            self.assertTrue(any("neither tfvars nor inventory" in err for err in errors), errors)

    def test_orphan_tfstate_creation_executor_refuses_apply_and_no_sidecar_overwrite(self) -> None:
        """Regression: orphan .tfstate must block apply, preserve state, and never write sidecars.

        Proves three things:
        1. make_terraform_creation_executor raises ReleaseStateUnverifiable (no apply).
        2. create_ephemeral_staging does NOT run failure-path cleanup (no destroy of unknown resources).
        3. No new tfvars or inventory file is written (no sidecar overwrite of unknown identity).
        """
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            state_p, vars_p, inv_p = _terraform_state_paths(self.release_id, state_root)

            # Pre-existing state file with no identity sidecars
            original_state_content = '{"version": 4, "serial": 1, "resources": [{"type": "google_sql_database"}]}'
            state_p.write_text(original_state_content, encoding="utf-8")

            # 1. Creation executor must raise ReleaseStateUnverifiable
            executor = make_terraform_creation_executor(
                module_dir=MODULE_DIR,
                state_dir=state_root,
                terraform_bin="/bin/true",
                initialize=False,
            )
            planned = plan_staging_resources(
                self.config,
                created_at=datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC),
                now=self.now,
            )
            with self.assertRaises(ReleaseStateUnverifiable) as ctx:
                executor(self.config, planned)
            self.assertIn("Existing release state conflict", str(ctx.exception))
            self.assertIn(UNVERIFIABLE_STATE_PREFIX, str(ctx.exception))

            # 2. No sidecar files were created or overwritten
            self.assertFalse(vars_p.is_file(), "tfvars must NOT be written for orphan state")
            self.assertFalse(inv_p.is_file(), "inventory must NOT be written for orphan state")

            # 3. Original state file is preserved untouched
            self.assertEqual(state_p.read_text(encoding="utf-8"), original_state_content)

            # 4. Full create_ephemeral_staging flow: no cleanup runs
            cleaned: list[str] = []
            receipt = create_ephemeral_staging(
                self.config,
                dry_run=False,
                now=self.now,
                creation_executor=make_terraform_creation_executor(
                    module_dir=MODULE_DIR,
                    state_dir=state_root,
                    terraform_bin="/bin/true",
                    initialize=False,
                ),
                cleanup_executor=lambda res: cleaned.append(str(res.get("id"))) or True,
            )
            self.assertFalse(receipt.success)
            self.assertEqual(cleaned, [], "Failure-path cleanup must NOT run against unverifiable state")
            self.assertTrue(receipt.metadata.get("existing_release_state_preserved"))
            self.assertTrue(receipt.metadata.get("release_state_unverifiable"))
            self.assertTrue(receipt.remediation_required)
            # State file still untouched after the full flow
            self.assertEqual(state_p.read_text(encoding="utf-8"), original_state_content)
            self.assertFalse(vars_p.is_file())
            self.assertFalse(inv_p.is_file())

    def test_cli_dry_run_rejects_unreadable_existing_state(self) -> None:
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths, main

        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            _, vars_p, _ = _terraform_state_paths(self.release_id, state_root)
            vars_p.write_text("{ corrupt", encoding="utf-8")

            exit_code = main([
                "create",
                "--release-id", self.release_id,
                "--candidate-sha", "a" * 40,
                "--manifest-digest", "sha256:" + "b" * 64,
                "--project-id", "oday-staging-proj",
                "--api-image", self.config.api_image,
                "--web-image", self.config.web_image,
                "--worker-image", self.config.worker_image,
                "--scheduler-image", self.config.scheduler_image,
                "--owner-task-id", "ODP-EPHEMERAL-STAGING-IAC-001",
                "--release-profile", "full",
                "--state-dir", str(state_root),
                "--dry-run",
            ])
            self.assertEqual(exit_code, 1)


class ReleaseScopedAutoCleanupTests(unittest.TestCase):
    """Auto-cleanup must decide per release, because deletion is per release.

    ``make_terraform_deletion_executor`` runs one ``terraform destroy`` for the
    whole release state. Deciding expiry per inventory row therefore let a single
    expired resource take its still-active siblings, and the release state file,
    down with it.
    """

    def setUp(self) -> None:
        self.now = datetime(2026, 8, 25, 14, 0, 0, tzinfo=UTC)
        self.release_id = "odp-mixed-ttl-001"

    def _labels(self, *, created_at: datetime, ttl_hours: int) -> dict[str, str]:
        return generate_staging_labels(
            release_id=self.release_id,
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            created_at=created_at,
            ttl_hours=ttl_hours,
        )

    def _mixed_inventory(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "active-168h-res",
                "type": "google_sql_database",
                "raw_release_id": self.release_id,
                "labels": self._labels(created_at=self.now - timedelta(hours=1), ttl_hours=168),
            },
            {
                "id": "expired-24h-res",
                "type": "google_storage_bucket",
                "raw_release_id": self.release_id,
                "labels": self._labels(created_at=self.now - timedelta(hours=26), ttl_hours=24),
            },
        ]

    def test_expired_sibling_does_not_authorize_deleting_an_active_resource(self) -> None:
        deleted: list[str] = []

        # max_ttl_hours=168 keeps the active resource entirely off the orphan
        # list, so only the release grouping can protect it.
        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=self._mixed_inventory(),
            max_ttl_hours=168,
            now=self.now,
            auto_cleanup=True,
            deletion_executor=lambda res: deleted.append(str(res.get("id"))) or True,
        )

        self.assertEqual(deleted, [])
        self.assertEqual(result.active_count, 1)
        self.assertEqual(result.expired_count, 1)
        self.assertEqual(result.orphan_count, 1)
        self.assertEqual(result.cleaned_count, 0)
        self.assertEqual(result.failed_cleanups, 1)
        self.assertEqual(len(result.remediation_tasks), 1)
        self.assertTrue(
            any("not expired" in err for err in result.remediation_tasks[0]["errors"]),
            result.remediation_tasks[0],
        )

    def test_mixed_release_under_strict_policy_refuses_every_member(self) -> None:
        deleted: list[str] = []

        # max_ttl_hours=24 puts the over-policy active resource on the orphan
        # list too; it still must not be deleted, and neither may its sibling.
        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=self._mixed_inventory(),
            max_ttl_hours=24,
            now=self.now,
            auto_cleanup=True,
            deletion_executor=lambda res: deleted.append(str(res.get("id"))) or True,
        )

        self.assertEqual(deleted, [])
        self.assertEqual(result.orphan_count, 2)
        self.assertEqual(result.cleaned_count, 0)
        self.assertEqual(result.failed_cleanups, 2)
        self.assertEqual(len(result.remediation_tasks), 2)

    def test_label_only_sibling_blocks_the_whole_release(self) -> None:
        """A row whose raw id cannot be proven still shares the release state."""
        deleted: list[str] = []
        inventory = [
            {
                "id": "label-only-res",
                "type": "google_sql_database",
                "labels": self._labels(created_at=self.now - timedelta(hours=26), ttl_hours=24),
            },
            {
                "id": "expired-24h-res",
                "type": "google_storage_bucket",
                "raw_release_id": self.release_id,
                "labels": self._labels(created_at=self.now - timedelta(hours=26), ttl_hours=24),
            },
        ]

        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            now=self.now,
            auto_cleanup=True,
            deletion_executor=lambda res: deleted.append(str(res.get("id"))) or True,
        )

        self.assertEqual(deleted, [])
        self.assertEqual(result.cleaned_count, 0)
        self.assertEqual(result.failed_cleanups, 2)

    def test_terraform_destroy_and_release_state_survive_a_mixed_release(self) -> None:
        """End-to-end proof against the real release-scoped deletion executor."""
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths

        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            state_p, vars_p, inv_p = _terraform_state_paths(self.release_id, state_root)
            state_p.write_text('{"live": "terraform state"}', encoding="utf-8")
            vars_p.write_text(json.dumps({"release_id": self.release_id}), encoding="utf-8")
            inv_p.write_text("[]", encoding="utf-8")

            # /bin/true makes every terraform call "succeed", so a destroy that
            # runs would unlink all three release files.
            result = scan_orphans(
                project_id="oday-staging-proj",
                resource_inventory=self._mixed_inventory(),
                max_ttl_hours=168,
                now=self.now,
                auto_cleanup=True,
                deletion_executor=make_terraform_deletion_executor(
                    self.release_id,
                    module_dir=MODULE_DIR,
                    state_dir=state_root,
                    terraform_bin="/bin/true",
                    initialize=False,
                ),
            )

            self.assertEqual(result.cleaned_count, 0)
            self.assertEqual(result.failed_cleanups, 1)
            self.assertTrue(state_p.is_file(), "release-scoped destroy removed live state")
            self.assertTrue(vars_p.is_file())
            self.assertTrue(inv_p.is_file())

    def test_fully_expired_release_is_still_cleaned_as_one_unit(self) -> None:
        """The grouping gate must not block a release that really is finished."""
        import tempfile

        from product_ops.deployment.staging_lifecycle import _terraform_state_paths

        inventory = [
            {
                "id": "expired-db",
                "type": "google_sql_database",
                "raw_release_id": self.release_id,
                "labels": self._labels(created_at=self.now - timedelta(hours=30), ttl_hours=24),
            },
            {
                "id": "expired-bucket",
                "type": "google_storage_bucket",
                "raw_release_id": self.release_id,
                "labels": self._labels(created_at=self.now - timedelta(hours=26), ttl_hours=24),
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            state_p, vars_p, inv_p = _terraform_state_paths(self.release_id, state_root)
            state_p.write_text('{"live": "terraform state"}', encoding="utf-8")
            vars_p.write_text(json.dumps({"release_id": self.release_id}), encoding="utf-8")
            inv_p.write_text("[]", encoding="utf-8")

            result = scan_orphans(
                project_id="oday-staging-proj",
                resource_inventory=inventory,
                now=self.now,
                auto_cleanup=True,
                deletion_executor=make_terraform_deletion_executor(
                    self.release_id,
                    module_dir=MODULE_DIR,
                    state_dir=state_root,
                    terraform_bin="/bin/true",
                    initialize=False,
                ),
            )

            self.assertEqual(result.cleaned_count, 2)
            self.assertEqual(result.failed_cleanups, 0)
            self.assertEqual(result.remediation_tasks, [])
            self.assertFalse(state_p.exists())
            self.assertFalse(vars_p.exists())
            self.assertFalse(inv_p.exists())

    def test_separate_releases_do_not_block_each_other(self) -> None:
        deleted: list[str] = []
        other_release = "odp-other-release-001"
        inventory = [
            {
                "id": "active-other",
                "type": "google_sql_database",
                "raw_release_id": other_release,
                "labels": generate_staging_labels(
                    release_id=other_release,
                    candidate_sha="c" * 40,
                    manifest_digest="sha256:" + "d" * 64,
                    owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
                    created_at=self.now - timedelta(hours=1),
                    ttl_hours=24,
                ),
            },
            {
                "id": "expired-mine",
                "type": "google_storage_bucket",
                "raw_release_id": self.release_id,
                "labels": self._labels(created_at=self.now - timedelta(hours=26), ttl_hours=24),
            },
        ]

        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            now=self.now,
            auto_cleanup=True,
            deletion_executor=lambda res: deleted.append(str(res.get("id"))) or True,
        )

        self.assertEqual(deleted, ["expired-mine"])
        self.assertEqual(result.cleaned_count, 1)
        self.assertEqual(result.failed_cleanups, 0)


class FailClosedReviewBlockerRegressionTests(unittest.TestCase):
    """Regression tests for the three fail-closed review blockers.

    These directly correspond to the Codex2 REOPEN findings:
    (1) validate_immutable_release_identity must fail closed when tfstate+inventory
        but no tfvars, and must check ALL inventory rows for project/tenant/created_at.
    (2) cleanup_ephemeral_staging must refuse when same-release siblings have
        invalid or incomplete labels, and must refuse when labels are non-mapping.
    (3) scan_orphans must report non-mapping labels as orphans, never skip silently.
    """

    def setUp(self) -> None:
        import tempfile
        self.state_dir = Path(tempfile.mkdtemp(prefix="review-blockers-"))
        self.config = StagingConfig(
            release_id="odp-blocker-regression-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            project_id="oday-staging-proj",
            kms_key_id="projects/oday-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
            deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            created_at="2026-08-24T10:00:00Z",
        )
        self.now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.state_dir, ignore_errors=True)

    # --- Blocker 1: validate_immutable_release_identity ---

    def test_tfstate_plus_inventory_no_tfvars_fails_closed(self) -> None:
        """Regression: tfstate + inventory without tfvars is unverifiable."""
        from product_ops.deployment.staging_lifecycle import (
            _terraform_state_paths,
        )
        state_path, _tfvars, inventory_path = _terraform_state_paths(
            self.config.release_id, self.state_dir,
        )
        state_path.write_text("{}", encoding="utf-8")
        labels = generate_staging_labels(
            release_id=self.config.release_id,
            candidate_sha=self.config.candidate_sha,
            manifest_digest=self.config.manifest_digest,
            owner_task_id=self.config.owner_task_id,
            created_at=self.now,
        )
        inventory_path.write_text(json.dumps([{
            "type": "google_sql_database",
            "name": "db",
            "id": "db-id",
            "labels": labels,
        }]), encoding="utf-8")

        errors = validate_immutable_release_identity(self.config, self.state_dir)
        self.assertTrue(len(errors) > 0, "Expected unverifiable error, got empty")
        self.assertTrue(
            any(UNVERIFIABLE_STATE_PREFIX in e for e in errors),
            f"Expected UNVERIFIABLE_STATE_PREFIX in errors, got: {errors}",
        )
        self.assertTrue(
            any("without the authoritative tfvars sidecar" in e for e in errors),
            f"Expected 'without the authoritative tfvars sidecar' in errors, got: {errors}",
        )

    def test_inventory_all_rows_checked_not_just_first(self) -> None:
        """Regression: a conflicting 2nd-row candidate_sha must be caught."""
        from product_ops.deployment.staging_lifecycle import (
            _terraform_state_paths,
        )
        _state, tfvars_path, inventory_path = _terraform_state_paths(
            self.config.release_id, self.state_dir,
        )
        tfvars_path.write_text(json.dumps({
            "release_id": self.config.release_id,
            "candidate_sha": self.config.candidate_sha,
            "manifest_digest": self.config.manifest_digest,
            "project_id": self.config.project_id,
            "tenant_id": resolve_tenant_id(self.config.release_id, ""),
            "created_at": self.config.created_at,
        }), encoding="utf-8")

        good_labels = generate_staging_labels(
            release_id=self.config.release_id,
            candidate_sha=self.config.candidate_sha,
            manifest_digest=self.config.manifest_digest,
            owner_task_id=self.config.owner_task_id,
            created_at=self.now,
        )
        bad_labels = dict(good_labels)
        bad_labels["candidate_sha"] = "f" * 40

        inventory_path.write_text(json.dumps([
            {"type": "bucket", "name": "row0", "id": "id0", "labels": good_labels},
            {"type": "db", "name": "row1", "id": "id1", "labels": bad_labels},
        ]), encoding="utf-8")

        errors = validate_immutable_release_identity(self.config, self.state_dir)
        self.assertTrue(len(errors) > 0, "Expected error for mismatched row 1")
        self.assertTrue(
            any("at index 1" in e for e in errors),
            f"Expected index 1 reference in errors, got: {errors}",
        )

    def test_inventory_non_dict_row_fails_closed(self) -> None:
        """Regression: a non-dict inventory row is unverifiable."""
        from product_ops.deployment.staging_lifecycle import (
            _terraform_state_paths,
        )
        _state, tfvars_path, inventory_path = _terraform_state_paths(
            self.config.release_id, self.state_dir,
        )
        tfvars_path.write_text(json.dumps({
            "release_id": self.config.release_id,
            "candidate_sha": self.config.candidate_sha,
            "manifest_digest": self.config.manifest_digest,
        }), encoding="utf-8")
        inventory_path.write_text(json.dumps([
            "not-a-dict-row",
        ]), encoding="utf-8")

        errors = validate_immutable_release_identity(self.config, self.state_dir)
        self.assertTrue(len(errors) > 0)
        self.assertTrue(
            any("non-dict entry at index 0" in e for e in errors),
            f"Expected non-dict error, got: {errors}",
        )

    def test_inventory_tenant_mismatch_caught(self) -> None:
        """Regression: mismatched tenant label across inventory must be caught."""
        from product_ops.deployment.staging_lifecycle import (
            _terraform_state_paths,
            bounded_label_value,
        )
        _state, tfvars_path, inventory_path = _terraform_state_paths(
            self.config.release_id, self.state_dir,
        )
        tfvars_path.write_text(json.dumps({
            "release_id": self.config.release_id,
            "candidate_sha": self.config.candidate_sha,
            "manifest_digest": self.config.manifest_digest,
            "project_id": self.config.project_id,
            "tenant_id": resolve_tenant_id(self.config.release_id, ""),
            "created_at": self.config.created_at,
        }), encoding="utf-8")

        labels = generate_staging_labels(
            release_id=self.config.release_id,
            candidate_sha=self.config.candidate_sha,
            manifest_digest=self.config.manifest_digest,
            owner_task_id=self.config.owner_task_id,
            created_at=self.now,
        )
        tampered_labels = dict(labels)
        tampered_labels["tenant"] = bounded_label_value("wrong-tenant-id")

        inventory_path.write_text(json.dumps([
            {"type": "bucket", "name": "row0", "id": "id0", "labels": tampered_labels},
        ]), encoding="utf-8")

        errors = validate_immutable_release_identity(self.config, self.state_dir)
        self.assertTrue(
            any("immutable tenant label" in e for e in errors),
            f"Expected tenant label mismatch error, got: {errors}",
        )

    def test_inventory_created_at_mismatch_caught(self) -> None:
        """Regression: mismatched created_at across inventory must be caught."""
        from product_ops.deployment.staging_lifecycle import (
            _terraform_state_paths,
        )
        _state, tfvars_path, inventory_path = _terraform_state_paths(
            self.config.release_id, self.state_dir,
        )
        tfvars_path.write_text(json.dumps({
            "release_id": self.config.release_id,
            "candidate_sha": self.config.candidate_sha,
            "manifest_digest": self.config.manifest_digest,
            "project_id": self.config.project_id,
            "tenant_id": resolve_tenant_id(self.config.release_id, ""),
            "created_at": self.config.created_at,
        }), encoding="utf-8")

        labels = generate_staging_labels(
            release_id=self.config.release_id,
            candidate_sha=self.config.candidate_sha,
            manifest_digest=self.config.manifest_digest,
            owner_task_id=self.config.owner_task_id,
            created_at=self.now,
        )
        tampered_labels = dict(labels)
        tampered_labels["created_at"] = "2099-01-01-00-00-00"

        config_with_time = StagingConfig(
            release_id=self.config.release_id,
            candidate_sha=self.config.candidate_sha,
            manifest_digest=self.config.manifest_digest,
            project_id=self.config.project_id,
            owner_task_id=self.config.owner_task_id,
            created_at="2026-08-24T12:00:00Z",
        )
        inventory_path.write_text(json.dumps([
            {"type": "bucket", "name": "row0", "id": "id0", "labels": tampered_labels},
        ]), encoding="utf-8")

        errors = validate_immutable_release_identity(config_with_time, self.state_dir)
        self.assertTrue(
            any("authoritative created_at" in e for e in errors),
            f"Expected created_at mismatch error, got: {errors}",
        )

    # --- Blocker 2: cleanup_ephemeral_staging ---

    def test_cleanup_refuses_same_release_sibling_invalid_labels(self) -> None:
        """Regression: cleanup refuses when any sibling has incomplete labels."""
        labels_good = generate_staging_labels(
            release_id="odp-sibling-test-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )
        labels_bad = dict(labels_good)
        labels_bad["candidate_sha"] = ""

        inventory = [
            {"id": "good-res", "type": "bucket", "labels": labels_good},
            {"id": "bad-res", "type": "db", "labels": labels_bad},
        ]
        receipt = cleanup_ephemeral_staging(
            release_id="odp-sibling-test-001",
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            dry_run=True,
        )
        self.assertFalse(receipt.success)
        self.assertTrue(receipt.remediation_required)
        self.assertTrue(
            any("incomplete or invalid ownership labels" in e for e in receipt.errors),
            f"Expected sibling validation error, got: {receipt.errors}",
        )

    def test_cleanup_refuses_non_mapping_labels_in_inventory(self) -> None:
        """Regression: cleanup refuses when inventory has non-mapping labels."""
        labels_good = generate_staging_labels(
            release_id="odp-nonmap-test-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )
        inventory = [
            {"id": "good-res", "type": "bucket", "labels": labels_good},
            {"id": "bad-res", "type": "db", "labels": "not-a-mapping"},
        ]
        receipt = cleanup_ephemeral_staging(
            release_id="odp-nonmap-test-001",
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            dry_run=True,
        )
        self.assertFalse(receipt.success)
        self.assertTrue(receipt.remediation_required)
        self.assertTrue(
            any("unreadable labels" in e for e in receipt.errors),
            f"Expected non-mapping label error, got: {receipt.errors}",
        )

    def test_cleanup_succeeds_when_all_siblings_valid(self) -> None:
        """Positive: cleanup succeeds when all same-release resources are valid."""
        labels = generate_staging_labels(
            release_id="odp-allgood-test-001",
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
        )
        inventory = [
            {"id": "res1", "type": "bucket", "labels": labels},
            {"id": "res2", "type": "db", "labels": labels},
        ]
        receipt = cleanup_ephemeral_staging(
            release_id="odp-allgood-test-001",
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            dry_run=True,
        )
        self.assertTrue(receipt.success)
        self.assertEqual(len(receipt.resources), 2)

    # --- Blocker 3: scan_orphans ---

    def test_scan_orphans_reports_non_mapping_labels_as_orphan(self) -> None:
        """Regression: non-mapping labels must be reported, not silently skipped."""
        inventory = [
            {"id": "malformed-res", "type": "bucket", "labels": "not-a-dict"},
            {"id": "also-malformed", "type": "db", "labels": 42},
            {"id": "list-labels", "type": "svc", "labels": ["a", "b"]},
        ]
        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            now=self.now,
        )
        self.assertEqual(result.orphan_count, 3, f"Expected 3 orphans, got {result.orphan_count}")
        for orphan in result.orphan_resources:
            self.assertIn("non-mapping labels", orphan["reason"])
        self.assertEqual(len(result.alerts), 3)
        for alert in result.alerts:
            self.assertIn("non-mapping labels", alert)

    def test_scan_orphans_non_mapping_labels_not_auto_deleted(self) -> None:
        """Regression: non-mapping labels resources must not be auto-deleted."""
        deleted: list[str] = []
        inventory = [
            {"id": "malformed-res", "type": "bucket", "labels": "not-a-dict"},
        ]
        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            now=self.now,
            auto_cleanup=True,
            deletion_executor=lambda res: deleted.append(str(res.get("id"))) or True,
        )
        self.assertEqual(len(deleted), 0, "Non-mapping labels resources must not be auto-deleted")
        self.assertEqual(result.orphan_count, 1)
        self.assertEqual(result.cleaned_count, 0)

    def test_scan_orphans_non_mapping_counted_in_total(self) -> None:
        """Regression: non-mapping labels resources must count in total_scanned."""
        inventory = [
            {"id": "normal", "type": "bucket", "labels": {}},
            {"id": "malformed", "type": "db", "labels": None},
        ]
        result = scan_orphans(
            project_id="oday-staging-proj",
            resource_inventory=inventory,
            now=self.now,
        )
        self.assertEqual(result.total_scanned, 2)
        self.assertEqual(result.orphan_count, 1)


class IncompleteReleaseIdentityBundleTests(unittest.TestCase):
    """A partial authoritative bundle must never read as "no existing release".

    Every immutable comparison in ``validate_immutable_release_identity`` is a
    no-op when the stored side is absent, so a truncated tfvars sidecar (in the
    limit, ``{}``) used to return ``[]``. The creation executor then overwrote
    the sidecars and applied against a ``.tfstate`` whose candidate, manifest,
    images, project, tenant, and created_at nobody could prove.
    """

    def setUp(self) -> None:
        import tempfile

        self.state_dir = Path(tempfile.mkdtemp(prefix="incomplete-identity-"))
        self.release_id = "odp-incomplete-bundle-001"
        self.config = StagingConfig(
            release_id=self.release_id,
            candidate_sha="a" * 40,
            manifest_digest="sha256:" + "b" * 64,
            project_id="oday-staging-proj",
            kms_key_id="projects/oday-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
            deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
            region="asia-east1",
            api_image="asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "c" * 64,
            web_image="asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "d" * 64,
            worker_image="asia-east1-docker.pkg.dev/proj/repo/worker@sha256:" + "e" * 64,
            scheduler_image="asia-east1-docker.pkg.dev/proj/repo/scheduler@sha256:" + "f" * 64,
            owner_task_id="ODP-EPHEMERAL-STAGING-IAC-001",
            created_at="2026-08-24T12:00:00Z",
        )
        self.created_dt = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        self.now = datetime(2026, 8, 24, 13, 0, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.state_dir, ignore_errors=True)

    def _paths(self) -> tuple[Path, Path, Path]:
        from product_ops.deployment.staging_lifecycle import _terraform_state_paths

        return _terraform_state_paths(self.release_id, self.state_dir)

    def _inventory_rows(self) -> list[dict[str, Any]]:
        labels = generate_staging_labels(
            release_id=self.release_id,
            candidate_sha=self.config.candidate_sha,
            manifest_digest=self.config.manifest_digest,
            owner_task_id=self.config.owner_task_id,
            created_at=self.created_dt,
        )
        return [
            {
                "type": "google_storage_bucket",
                "name": "bucket",
                "id": "bucket-id",
                "release_id": self.release_id,
                "raw_release_id": self.release_id,
                "labels": labels,
            }
        ]

    def _seed(self, tfvars: Any, *, inventory: bool = True) -> None:
        state_path, tfvars_path, inventory_path = self._paths()
        state_path.write_text("{}", encoding="utf-8")
        tfvars_path.write_text(json.dumps(tfvars), encoding="utf-8")
        if inventory:
            inventory_path.write_text(json.dumps(self._inventory_rows()), encoding="utf-8")

    def test_empty_tfvars_object_is_unverifiable(self) -> None:
        """tfstate plus a parseable but empty tfvars proves nothing."""
        self._seed({})

        errors = validate_immutable_release_identity(self.config, self.state_dir)

        self.assertTrue(errors, "tfvars={} must not validate as 'no existing release'")
        self.assertTrue(
            any(err.startswith(UNVERIFIABLE_STATE_PREFIX) for err in errors), errors
        )
        self.assertTrue(
            any("missing the immutable identity field" in err for err in errors), errors
        )

    def test_tfvars_missing_any_single_identity_field_is_unverifiable(self) -> None:
        """Dropping one field at a time must always fail closed on that field."""
        from product_ops.deployment.staging_lifecycle import (
            IMMUTABLE_RELEASE_IDENTITY_FIELDS,
        )

        complete = generate_tfvars(self.config)
        self.assertEqual(validate_immutable_release_identity(self.config, self.state_dir), [])

        for field in IMMUTABLE_RELEASE_IDENTITY_FIELDS:
            with self.subTest(field=field):
                partial = dict(complete)
                partial.pop(field)
                self._seed(partial)

                errors = validate_immutable_release_identity(self.config, self.state_dir)

                self.assertTrue(errors, f"missing {field} must fail closed")
                self.assertTrue(
                    any(
                        err.startswith(UNVERIFIABLE_STATE_PREFIX)
                        and field in err
                        for err in errors
                    ),
                    f"{field}: {errors}",
                )

    def test_reviewer_probe_bundle_is_unverifiable(self) -> None:
        """The exact probe from the review: identity minus images and created_at."""
        self._seed(
            {
                "release_id": self.release_id,
                "candidate_sha": self.config.candidate_sha,
                "manifest_digest": self.config.manifest_digest,
                "project_id": self.config.project_id,
                "tenant_id": resolve_tenant_id(self.release_id, ""),
            }
        )

        errors = validate_immutable_release_identity(self.config, self.state_dir)

        self.assertTrue(errors)
        joined = " ".join(errors)
        for field in ("region", "api_image", "web_image", "created_at", "owner_task_id"):
            self.assertIn(field, joined)

    def test_stored_release_id_must_match_this_release(self) -> None:
        """A sidecar recorded for another release may not be applied against."""
        foreign = dict(generate_tfvars(self.config))
        foreign["release_id"] = "odp-some-other-release-999"
        self._seed(foreign)

        errors = validate_immutable_release_identity(self.config, self.state_dir)

        self.assertTrue(
            any("was provisioned for a different release_id" in err for err in errors),
            errors,
        )

    def test_region_and_owner_task_are_immutable(self) -> None:
        self._seed(generate_tfvars(self.config))

        moved = dataclasses_replace(self.config, region="us-central1")
        self.assertTrue(
            any("immutable region" in err for err in validate_immutable_release_identity(moved, self.state_dir)),
        )

        reassigned = dataclasses_replace(self.config, owner_task_id="ODP-SOME-OTHER-TASK-001")
        self.assertTrue(
            any(
                "immutable owner_task_id" in err
                for err in validate_immutable_release_identity(reassigned, self.state_dir)
            ),
        )

    def test_tfstate_and_tfvars_without_inventory_is_unverifiable(self) -> None:
        """Without the ownership manifest the resource set cannot be enumerated."""
        self._seed(generate_tfvars(self.config), inventory=False)

        errors = validate_immutable_release_identity(self.config, self.state_dir)

        self.assertTrue(errors)
        self.assertTrue(
            any(err.startswith(UNVERIFIABLE_STATE_PREFIX) for err in errors), errors
        )
        self.assertTrue(
            any("without the release-scoped inventory manifest" in err for err in errors),
            errors,
        )

    def test_inventory_row_without_full_ownership_labels_is_unverifiable(self) -> None:
        """A row that cannot prove ownership makes the manifest unusable."""
        rows = self._inventory_rows()
        rows[0]["labels"].pop("owner_task")
        state_path, tfvars_path, inventory_path = self._paths()
        state_path.write_text("{}", encoding="utf-8")
        tfvars_path.write_text(json.dumps(generate_tfvars(self.config)), encoding="utf-8")
        inventory_path.write_text(json.dumps(rows), encoding="utf-8")

        errors = validate_immutable_release_identity(self.config, self.state_dir)

        self.assertTrue(
            any("do not prove" in err and "full release-scoped ownership" in err for err in errors),
            errors,
        )

    def test_complete_matching_bundle_still_validates_clean(self) -> None:
        """The guard must not block a legitimate idempotent rerun."""
        self._seed(generate_tfvars(self.config))

        self.assertEqual(validate_immutable_release_identity(self.config, self.state_dir), [])

        # A rerun that omits created_at adopts the stored authoritative value.
        without_created = dataclasses_replace(self.config, created_at="")
        self.assertEqual(
            validate_immutable_release_identity(without_created, self.state_dir), []
        )

    def test_creation_executor_refuses_partial_bundle_without_write_or_apply(self) -> None:
        """No sidecar overwrite, no terraform apply against unverifiable state."""
        import unittest.mock as mock

        partial = {
            "release_id": self.release_id,
            "candidate_sha": self.config.candidate_sha,
            "manifest_digest": self.config.manifest_digest,
            "project_id": self.config.project_id,
            "tenant_id": resolve_tenant_id(self.release_id, ""),
        }
        self._seed(partial)
        state_path, tfvars_path, inventory_path = self._paths()
        inventory_before = inventory_path.read_text(encoding="utf-8")

        executor = make_terraform_creation_executor(
            module_dir=MODULE_DIR,
            state_dir=self.state_dir,
            initialize=False,
        )
        planned = plan_staging_resources(self.config, created_at=self.created_dt, now=self.now)

        with mock.patch("product_ops.deployment.staging_lifecycle._run_terraform") as run_tf:
            with self.assertRaises(ReleaseStateUnverifiable) as ctx:
                executor(self.config, planned)

        self.assertIn(UNVERIFIABLE_STATE_PREFIX, str(ctx.exception))
        run_tf.assert_not_called()
        # The operator's evidence must survive untouched.
        self.assertEqual(json.loads(tfvars_path.read_text(encoding="utf-8")), partial)
        self.assertEqual(inventory_path.read_text(encoding="utf-8"), inventory_before)
        self.assertEqual(state_path.read_text(encoding="utf-8"), "{}")

    def test_create_never_cleans_up_after_a_partial_bundle_refusal(self) -> None:
        """Failure-path cleanup would destroy exactly what the guard protected."""
        self._seed({})
        deleted: list[str] = []

        executor = make_terraform_creation_executor(
            module_dir=MODULE_DIR,
            state_dir=self.state_dir,
            initialize=False,
        )
        receipt = create_ephemeral_staging(
            self.config,
            dry_run=False,
            now=self.now,
            creation_executor=executor,
            cleanup_executor=lambda res: deleted.append(str(res.get("id"))) or True,
        )

        self.assertFalse(receipt.success)
        self.assertEqual(deleted, [], "unverifiable state must never trigger cleanup")
        self.assertTrue(receipt.metadata.get("existing_release_state_preserved"))
        self.assertTrue(receipt.metadata.get("release_state_unverifiable"))
        self.assertNotIn("failure_cleanup_receipt", receipt.metadata)
        self.assertTrue(receipt.remediation_required)
        self.assertTrue(any(UNVERIFIABLE_STATE_PREFIX in err for err in receipt.errors), receipt.errors)


class EphemeralStagingVerificationAndHoldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release_id = "odp-20260824-001"
        self.candidate_sha = "a" * 40
        self.manifest_digest = "sha256:" + "b" * 64
        self.project_id = "oday-staging-proj"
        self.now = datetime(2026, 8, 27, 6, 0, 0, tzinfo=UTC)

    def _seed_live_state(self, state_dir: Path) -> None:
        from product_ops.deployment.staging_lifecycle import (
            _lifecycle_state_path,
            _terraform_state_paths,
            generate_staging_labels,
            generate_tfvars,
        )

        cfg = StagingConfig(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            project_id=self.project_id,
            region="asia-east1",
            cloud_sql_instance_name="oday-staging-pg",
            cloud_sql_connection_name="oday-staging-proj:asia-east1:oday-staging-pg",
            network_name="oday-staging-vpc",
            subnetwork_name="oday-staging-subnet",
            kms_key_id="projects/oday-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
            deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
            api_image="asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "c" * 64,
            web_image="asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "d" * 64,
            worker_image="asia-east1-docker.pkg.dev/proj/repo/worker@sha256:" + "e" * 64,
            scheduler_image="asia-east1-docker.pkg.dev/proj/repo/scheduler@sha256:" + "f" * 64,
            created_at=self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            owner_task_id="ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001",
        )
        state_path, tfvars_path, inventory_path = _terraform_state_paths(self.release_id, state_dir)
        state_path.write_text("{}", encoding="utf-8")
        tfvars_path.write_text(json.dumps(generate_tfvars(cfg)), encoding="utf-8")
        labels = generate_staging_labels(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            owner_task_id=cfg.owner_task_id,
            created_at=self.now,
        )
        inventory_path.write_text(
            json.dumps([{
                "type": "google_cloud_run_v2_service",
                "name": "staging-service",
                "id": "staging-service-id",
                "raw_release_id": self.release_id,
                "labels": labels,
                "created_at": cfg.created_at,
                "expires_at": "2026-08-28T06:00:00Z",
            }]),
            encoding="utf-8",
        )
        marker_path = _lifecycle_state_path(self.release_id, state_dir)
        marker_path.write_text(
            json.dumps({
                "schema_version": 1,
                "release_id": self.release_id,
                "status": "created",
                "secret_values_redacted": True,
            }),
            encoding="utf-8",
        )

    def _valid_outputs(self) -> dict[str, Any]:
        from product_ops.deployment.staging_lifecycle import (
            generate_staging_labels,
            get_ephemeral_resource_names,
        )

        names = get_ephemeral_resource_names(self.release_id, self.project_id)
        labels = generate_staging_labels(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            owner_task_id="ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001",
            created_at=self.now,
        )
        return {
            "release_id": self.release_id,
            "staging_project_id": self.project_id,
            "created_at": "2026-08-27T06:00:00Z",
            "expires_at": "2026-08-28T06:00:00Z",
            "staging_api_uri": "https://staging-api.example.test",
            "staging_web_uri": "https://staging-web.example.test",
            "staging_api_service_name": names["cloud_run_api"],
            "staging_web_service_name": names["cloud_run_web"],
            "staging_database_name": names["database_name"],
            "staging_data_bucket": names["bucket_name"],
            "staging_tenant_id": names["tenant_id"],
            "staging_runtime_service_account": f"{names['sa_runtime']}@{self.project_id}.iam.gserviceaccount.com",
            "staging_web_service_account": f"{names['sa_web']}@{self.project_id}.iam.gserviceaccount.com",
            "staging_worker_service_account": f"{names['sa_worker']}@{self.project_id}.iam.gserviceaccount.com",
            "staging_migration_job_name": names["cloud_run_migration_job"],
            "staging_worker_job_name": names["cloud_run_worker_job"],
            "staging_scheduler_job_name": names["cloud_run_scheduler_job"],
            "staging_scheduler_trigger_name": names["scheduler_job"],
            "staging_cloud_sql_instance": "oday-staging-pg",
            "staging_api_image": "asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "c" * 64,
            "staging_web_image": "asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "d" * 64,
            "staging_worker_image": "asia-east1-docker.pkg.dev/proj/repo/worker@sha256:" + "e" * 64,
            "staging_scheduler_image": "asia-east1-docker.pkg.dev/proj/repo/scheduler@sha256:" + "f" * 64,
            "resource_labels": labels,
            "ownership_manifest": {"labels": labels, "resources": {"api": names["cloud_run_api"]}},
        }

    def test_non_dry_run_requires_outputs_executor_and_live_state(self) -> None:
        from product_ops.deployment.staging_lifecycle import verify_ephemeral_staging

        outputs = self._valid_outputs()
        receipt = verify_ephemeral_staging(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            project_id=self.project_id,
            operator_identity=outputs["staging_runtime_service_account"],
            lifecycle_outputs=outputs,
            now=self.now,
        )

        self.assertFalse(receipt.success)
        self.assertTrue(any("authoritative live stage executor" in error for error in receipt.errors))
        self.assertTrue(any("Terraform state" in error for error in receipt.errors))

    def test_mapping_stage_result_without_explicit_success_fails_closed(self) -> None:
        from product_ops.deployment.staging_lifecycle import verify_ephemeral_staging

        receipt = verify_ephemeral_staging(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            project_id=self.project_id,
            dry_run=True,
            stage_executor=lambda _stage, _context: {},
            now=self.now,
        )

        self.assertFalse(receipt.success)
        self.assertTrue(any("incomplete result" in error for error in receipt.errors))

    def test_sql_rehearsal_uses_bare_verified_foundation_name(self) -> None:
        from unittest.mock import patch

        from product_ops.deployment.staging_lifecycle import make_live_rehearsal_executor

        outputs = self._valid_outputs()
        with patch(
            "product_ops.deployment.staging_lifecycle._live_gcloud",
            side_effect=lambda argv, **kwargs: argv[3] if argv[0] == "iam" else "",
        ) as cloud:
            executor = make_live_rehearsal_executor(
                outputs,
                project_id=self.project_id,
                region="asia-east1",
                operator_identity=outputs["staging_runtime_service_account"],
                cloud_sql_instance=f"{self.project_id}:asia-east1:oday-staging-pg",
            )
            result = executor("backup_restore_drill", {})
        self.assertTrue(result["success"])
        sql_calls = [call.args[0] for call in cloud.call_args_list if call.args[0][0] == "sql"]
        self.assertEqual([argv[1:4] for argv in sql_calls], [
            ["export", "sql", "oday-staging-pg"], ["import", "sql", "oday-staging-pg"],
        ])

    def test_sql_rehearsal_rejects_a_different_instance_before_any_api_call(self) -> None:
        from unittest.mock import patch

        from product_ops.deployment.staging_lifecycle import make_live_rehearsal_executor

        outputs = self._valid_outputs()
        with patch("product_ops.deployment.staging_lifecycle._live_gcloud") as cloud:
            with self.assertRaisesRegex(ValueError, "Terraform output handoff"):
                make_live_rehearsal_executor(
                    outputs,
                    project_id=self.project_id,
                    region="asia-east1",
                    operator_identity=outputs["staging_runtime_service_account"],
                    cloud_sql_instance=f"{self.project_id}:asia-east1:other-instance",
                )
            cloud.assert_not_called()

    def test_live_executor_rejects_arbitrary_operator_identity(self) -> None:
        from product_ops.deployment.staging_lifecycle import make_live_rehearsal_executor

        outputs = self._valid_outputs()
        with self.assertRaises(ValueError) as ctx:
            make_live_rehearsal_executor(
                outputs,
                project_id=self.project_id,
                region="asia-east1",
                operator_identity="arbitrary@oday-staging-proj.iam.gserviceaccount.com",
            )
        self.assertIn("exactly equal", str(ctx.exception))

    def test_verify_ephemeral_staging_success(self) -> None:
        from product_ops.deployment.staging_lifecycle import (
            REHEARSAL_STAGE_NAMES,
            verify_ephemeral_staging,
        )

        receipt = verify_ephemeral_staging(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            project_id=self.project_id,
            dry_run=True,
            now=self.now,
        )

        self.assertTrue(receipt.success)
        self.assertEqual(receipt.action, "verify")
        self.assertEqual(receipt.release_id, self.release_id)
        self.assertEqual(receipt.candidate_sha, self.candidate_sha)
        self.assertTrue(receipt.metadata.get("secret_values_redacted"))
        self.assertEqual(receipt.metadata.get("external_sources_expected_enabled"), [])
        self.assertEqual(receipt.metadata.get("public_egress"), "default_deny")
        self.assertEqual(receipt.metadata.get("identity_scope"), "release_scoped_least_privilege")
        self.assertEqual(len(receipt.resources), len(REHEARSAL_STAGE_NAMES))
        self.assertTrue(all(r["success"] for r in receipt.resources))

    def test_verify_ephemeral_staging_stage_failure(self) -> None:
        from product_ops.deployment.staging_lifecycle import verify_ephemeral_staging

        def failing_executor(stage: str, context: Mapping[str, Any]) -> bool:
            if stage == "worker_idempotency":
                return False
            return True

        receipt = verify_ephemeral_staging(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            project_id=self.project_id,
            dry_run=True,
            stage_executor=failing_executor,
            now=self.now,
        )

        self.assertFalse(receipt.success)
        self.assertTrue(receipt.remediation_required)
        self.assertTrue(any("worker_idempotency failed" in err for err in receipt.errors))

    # ODP-CORE-OPS-RELEASE-PROFILE-001: staging must serve and verify the
    # admitted profile; a model-less runtime defaulting to full fails closed.

    MODEL_ERROR = (
        "forecastops: MLFLOW_TRACKING_URI_REQUIRED: production model runtime is not configured"
    )
    REGISTRY_REFUSED: tuple[int, dict[str, Any]] = (
        503,
        {
            "error": {
                "code": "LEARNINGHUB_RUNTIME_CONFIGURATION_ERROR",
                "message": "Learning Hub production requires an injected remote MLflow registry",
            }
        },
    )

    @staticmethod
    def _gate_fixtures() -> Any:
        """The live E2E gate's own passing fixtures (real capability evidence)."""

        import importlib.util

        name = "staging_live_e2e_gate_base_fixtures"
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                name, ROOT / "tests/e2e/test_live_e2e_gate.py"
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        return sys.modules[name]

    @classmethod
    def _readiness(cls, profile: str = "core-ops", *, ready: bool = False) -> dict[str, Any]:
        from copy import deepcopy

        payload = deepcopy(cls._gate_fixtures().readiness_payload())
        details = payload["details"]
        details["deploymentMode"] = "staging"
        details["releaseProfile"] = {
            "name": profile,
            "valid": True,
            "modelReadinessClaimed": profile == "full",
            "error": None,
        }
        if not ready:
            models = details["models"]
            models.update(
                {
                    "mode": "mlflow-production-unverified",
                    "productionBindingsReady": False,
                    "autoSeeded": False,
                    "error": cls.MODEL_ERROR,
                    "blockingReasons": ["PRODUCTION_MODEL_BINDINGS_UNVERIFIED"],
                }
            )
            models["capabilities"]["forecastops"].update(
                {
                    "available": False,
                    "reasonCode": "MLFLOW_TRACKING_URI_REQUIRED",
                    "error": "production model runtime is not configured",
                }
            )
        return payload

    @classmethod
    def _registry(cls) -> tuple[int, dict[str, Any]]:
        return 200, cls._gate_fixtures().models_payload()

    @staticmethod
    def _model_failures(
        readiness: dict[str, Any],
        registry: tuple[int, dict[str, Any]],
        expected: str = "core-ops",
        *,
        error: str = "",
    ) -> dict[str, str]:
        from product_ops.deployment.staging_lifecycle import staging_model_gate_checks

        checks = staging_model_gate_checks(
            readiness,
            registry_status=registry[0],
            registry_payload=registry[1],
            registry_error=error,
            expected_profile=expected,
        )
        return {check.name: check.detail for check in checks if not check.ok}

    def test_staging_release_profile_check_accepts_truthful_runtimes(self) -> None:
        from product_ops.deployment.staging_lifecycle import staging_release_profile_errors

        self.assertEqual(
            staging_release_profile_errors(self._readiness(), expected_profile="core-ops"), []
        )
        # core-ops with genuinely verified bindings is not forced to claim absence.
        self.assertEqual(
            staging_release_profile_errors(
                self._readiness(ready=True), expected_profile="core-ops"
            ),
            [],
        )
        self.assertEqual(
            staging_release_profile_errors(
                self._readiness("full", ready=True), expected_profile="full"
            ),
            [],
        )

    def test_staging_release_profile_check_refuses_the_default_full_lie(self) -> None:
        from product_ops.deployment.staging_lifecycle import staging_release_profile_errors

        # The pre-fix staging runtime: no ODP_RELEASE_PROFILE, so full/claimed
        # while the admitted manifest is core-ops and no model exists.
        errors = staging_release_profile_errors(
            self._readiness("full"), expected_profile="core-ops"
        )
        self.assertTrue(any(e.startswith("runtime:release_profile") for e in errors), errors)

    def test_staging_release_profile_check_refuses_mismatches(self) -> None:
        from product_ops.deployment.staging_lifecycle import staging_release_profile_errors

        cases: list[tuple[str, Any, str]] = [
            ("claim", lambda d: d["releaseProfile"].update(modelReadinessClaimed=True),
             "runtime:release_profile"),
            ("invalid", lambda d: d["releaseProfile"].update(valid=False), "runtime:release_profile"),
            ("missing", lambda d: d.pop("releaseProfile"), "runtime:release_profile"),
            ("deployment", lambda d: d.update(deploymentMode="production"), "deploymentMode"),
        ]
        for name, mutate, fragment in cases:
            with self.subTest(name):
                payload = self._readiness()
                mutate(payload["details"])
                errors = staging_release_profile_errors(payload, expected_profile="core-ops")
                self.assertTrue(any(fragment in e for e in errors), errors)

        payload = self._readiness()
        payload["status"] = "degraded"
        self.assertTrue(staging_release_profile_errors(payload, expected_profile="core-ops"))
        for expected in ("dev-admin", "", "bogus"):
            with self.subTest(expected=expected):
                self.assertTrue(
                    staging_release_profile_errors(self._readiness(), expected_profile=expected)
                )

    def test_staging_model_gate_accepts_truthful_runtimes(self) -> None:
        # core-ops without models: limitation reported, registry really refuses.
        self.assertEqual(self._model_failures(self._readiness(), self.REGISTRY_REFUSED), {})
        # The gate's bound-registry exception: an empty listing is honest.
        bound = self._readiness()
        bound["details"]["models"]["learninghubRegistryBound"] = True
        self.assertEqual(self._model_failures(bound, (200, {"items": [], "count": 0})), {})
        # Claimed bindings (core-ops or full) pass only with the full assertions.
        self.assertEqual(self._model_failures(self._readiness(ready=True), self._registry()), {})
        self.assertEqual(
            self._model_failures(self._readiness("full", ready=True), self._registry(), "full"),
            {},
        )

    def test_staging_model_gate_names_every_assertion_it_ran(self) -> None:
        from product_ops.deployment.staging_lifecycle import staging_model_gate_checks

        def names(readiness: dict[str, Any], registry: tuple[int, dict[str, Any]]) -> set[str]:
            return {
                check.name
                for check in staging_model_gate_checks(
                    readiness,
                    registry_status=registry[0],
                    registry_payload=registry[1],
                    expected_profile="core-ops",
                )
            }

        refused = names(self._readiness(), self.REGISTRY_REFUSED)
        self.assertTrue(
            {
                "runtime:model_limitation_reported",
                "runtime:model_capability:forecastops",
                "runtime:model_capability:avm",
                "runtime:model_no_surrogate_markers",
                "models:registry_refused",
                "models:no_surrogate_markers",
            }
            <= refused,
            refused,
        )
        self.assertNotIn("runtime:model_bindings", refused)
        claimed = names(self._readiness(ready=True), self._registry())
        self.assertTrue(
            {
                "runtime:model_bindings",
                "runtime:model_capability:forecastops",
                "models:registry",
                "models:forecastops:production_alias",
                "models:forecastops:lineage",
                "models:forecastops:artifact_store",
                "models:avm:no_fabricated_alias",
            }
            <= claimed,
            claimed,
        )
        self.assertNotIn("models:registry_refused", claimed)

    def test_staging_model_gate_refuses_missing_models_that_are_not_refused(self) -> None:
        """Branch (a): no readiness claimed, so absence must be truthful end to end."""

        def caps(mutate: Any) -> Any:
            return lambda d: mutate(d["models"]["capabilities"])

        readiness_cases: list[tuple[str, Any, str]] = [
            ("seeded", lambda d: d["models"].update(autoSeeded=True),
             "runtime:model_limitation_reported"),
            ("no-reason", lambda d: d["models"].update(blockingReasons=[]),
             "runtime:model_limitation_reported"),
            ("no-error", lambda d: d["models"].update(error=None),
             "runtime:model_limitation_reported"),
            ("prod-mode", lambda d: d["models"].update(mode="mlflow-production"),
             "runtime:model_limitation_reported"),
            ("no-models", lambda d: d.pop("models"), "runtime:model_limitation_reported"),
            ("unavailable-without-reason",
             caps(lambda c: c["forecastops"].update(reasonCode=None)),
             "runtime:model_capability:forecastops"),
            ("available-with-reason",
             caps(lambda c: c["forecastops"].update(available=True)),
             "runtime:model_capability:forecastops"),
            ("no-capabilities", lambda d: d["models"].pop("capabilities"),
             "runtime:model_capability:forecastops"),
            ("governed-disabled-seeded",
             caps(lambda c: c["avm"]["governedDisabledEvidence"].update(autoSeeded=True)),
             "runtime:model_capability:avm"),
            ("governed-disabled-available", caps(lambda c: c["avm"].update(available=True)),
             "runtime:model_capability:avm"),
            ("surrogate", lambda d: d["models"].update(source="fixture"),
             "runtime:model_no_surrogate_markers"),
        ]
        for name, mutate, check in readiness_cases:
            with self.subTest(name):
                payload = self._readiness()
                mutate(payload["details"])
                self.assertIn(check, self._model_failures(payload, self.REGISTRY_REFUSED))

        bound = self._readiness()
        bound["details"]["models"]["learninghubRegistryBound"] = True
        registry_cases: list[tuple[str, dict[str, Any], tuple[int, dict[str, Any]], str]] = [
            # Reports missing models while the registry serves a non-empty listing.
            ("served-versions", self._readiness(), self._registry(), "models:registry_refused"),
            ("served-empty-unbound", self._readiness(), (200, {"items": [], "count": 0}),
             "models:registry_refused"),
            ("bound-but-served", bound, self._registry(), "models:registry_refused"),
            ("forbidden", self._readiness(), (403, {}), "models:registry_refused"),
            ("surrogate-refusal", self._readiness(), (503, {"source": "fixture"}),
             "models:no_surrogate_markers"),
        ]
        for name, payload, registry, check in registry_cases:
            with self.subTest(name):
                self.assertIn(check, self._model_failures(payload, registry))
        self.assertIn(
            "models:registry_refused",
            self._model_failures(self._readiness(), (0, {}), error="URLError"),
        )

    def test_staging_model_gate_holds_claimed_readiness_to_full_assertions(self) -> None:
        """Branch (b): claimed bindings get the full model assertions, no shortcuts."""

        def registry_with(mutate: Any) -> tuple[int, dict[str, Any]]:
            status, payload = self._registry()
            mutate(payload)
            return status, payload

        def fabricated_alias(payload: dict[str, Any]) -> None:
            payload["items"].append(
                {**payload["items"][0], "model_name": "dealroom_avm", "version": "1"}
            )

        for profile in ("core-ops", "full"):
            readiness_cases: list[tuple[str, Any, str]] = [
                ("seeded", lambda d: d["models"].update(autoSeeded=True), "runtime:model_bindings"),
                ("mode", lambda d: d["models"].update(mode="mlflow-production-unverified"),
                 "runtime:model_bindings"),
                ("error", lambda d: d["models"].update(error="degraded"), "runtime:model_bindings"),
                ("no-capabilities", lambda d: d["models"].pop("capabilities"),
                 "runtime:model_capability:forecastops"),
                ("forecastops-unavailable",
                 lambda d: d["models"]["capabilities"]["forecastops"].update(available=False),
                 "runtime:model_capability:forecastops"),
            ]
            for name, mutate, check in readiness_cases:
                with self.subTest(profile=profile, case=name):
                    payload = self._readiness(profile, ready=True)
                    mutate(payload["details"])
                    self.assertIn(check, self._model_failures(payload, self._registry(), profile))

            registry_cases: list[tuple[str, tuple[int, dict[str, Any]], str]] = [
                ("refused", self.REGISTRY_REFUSED, "models:registry"),
                ("empty", (200, {"items": [], "count": 0}), "models:registry"),
                ("no-alias", registry_with(lambda p: p["items"][0].update(aliases=[])),
                 "models:forecastops:production_alias"),
                ("no-lineage", registry_with(lambda p: p["items"][0].pop("approved_by")),
                 "models:forecastops:lineage"),
                ("no-snapshot", registry_with(lambda p: p["items"][0].pop("dataset_snapshot_id")),
                 "models:forecastops:lineage"),
                ("local-artifact",
                 registry_with(lambda p: p["items"][0].update(artifact_uri="file:///tmp/m")),
                 "models:forecastops:artifact_store"),
                ("fabricated-alias", registry_with(fabricated_alias),
                 "models:avm:no_fabricated_alias"),
            ]
            for name, registry, check in registry_cases:
                with self.subTest(profile=profile, case=name):
                    self.assertIn(
                        check,
                        self._model_failures(
                            self._readiness(profile, ready=True), registry, profile
                        ),
                    )

        # full never takes the refusal branch: missing models simply fail.
        failures = self._model_failures(self._readiness("full"), self.REGISTRY_REFUSED, "full")
        self.assertIn("runtime:model_bindings", failures)
        self.assertIn("models:registry", failures)

    def test_live_http_get_returns_refusals_instead_of_raising(self) -> None:
        import io
        import urllib.error
        from unittest.mock import patch

        from product_ops.deployment.staging_lifecycle import _live_http_get

        refusal = urllib.error.HTTPError(
            "https://api.example/api/v1/learninghub/models", 503, "Service Unavailable", {},
            io.BytesIO(json.dumps(self.REGISTRY_REFUSED[1]).encode()),
        )
        with patch("urllib.request.urlopen", side_effect=refusal):
            self.assertEqual(
                _live_http_get("https://api.example/x", "token"),
                (503, self.REGISTRY_REFUSED[1], ""),
            )
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            status, payload, error = _live_http_get("https://api.example/x", "token")
        self.assertEqual((status, payload), (0, {}))
        self.assertIn("URLError", error)

    def _smoke_with_readiness(
        self,
        readiness: dict[str, Any],
        expected: str,
        registry: tuple[int, dict[str, Any]] | None = None,
        *,
        registry_error: str = "",
    ) -> Any:
        from unittest.mock import patch

        from product_ops.deployment.staging_lifecycle import make_live_rehearsal_executor

        outputs = self._valid_outputs()
        registry = self.REGISTRY_REFUSED if registry is None else registry
        registry_reads: list[str] = []

        def describe(kind: str, name: str, **_kwargs: Any) -> dict[str, Any]:
            image = outputs["staging_api_image" if "api" in name else "staging_web_image"]
            return {"spec": {"template": {"containers": [{"image": image}],
                                          "vpcAccess": {"egress": "ALL_TRAFFIC"}}}}

        def json_get(url: str, _token: str, **_kwargs: Any) -> dict[str, Any]:
            if url.endswith("/readiness"):
                return readiness
            if url.endswith("/platform/version"):
                return {"status": "ok", "release_sha": outputs["resource_labels"]["candidate_sha"]}
            return {"status": "ok"}

        def http_get(url: str, _token: str, **_kwargs: Any) -> tuple[int, Any, str]:
            registry_reads.append(url)
            return registry[0], registry[1], registry_error

        with patch(
            "product_ops.deployment.staging_lifecycle._live_gcloud",
            side_effect=lambda argv, **kwargs: argv[3] if argv[0] == "iam" else "",
        ), patch(
            "product_ops.deployment.staging_lifecycle._live_cloud_run_description",
            side_effect=describe,
        ), patch(
            "product_ops.deployment.staging_lifecycle._live_identity_token",
            return_value="token",
        ), patch(
            "product_ops.deployment.staging_lifecycle._live_json_get", side_effect=json_get
        ), patch(
            "product_ops.deployment.staging_lifecycle._live_http_get", side_effect=http_get
        ):
            executor = make_live_rehearsal_executor(
                outputs,
                project_id=self.project_id,
                region="asia-east1",
                operator_identity=outputs["staging_runtime_service_account"],
            )
            try:
                return executor("api_web_authenticated_smoke", {"release_profile": expected})
            finally:
                self.assertLessEqual(len(registry_reads), 1)
                for url in registry_reads:
                    self.assertEqual(
                        url, outputs["staging_api_uri"].rstrip("/") + "/api/v1/learninghub/models"
                    )

    def test_live_smoke_stage_verifies_the_admitted_profile(self) -> None:
        result = self._smoke_with_readiness(self._readiness(), "core-ops")

        self.assertTrue(result["success"])
        proof = result["release_profile"]
        self.assertEqual((proof["name"], proof["verified"]), ("core-ops", True))
        self.assertIn("runtime:model_limitation_reported", proof["model_checks"])
        self.assertIn("models:registry_refused", proof["model_checks"])

        full = self._smoke_with_readiness(
            self._readiness("full", ready=True), "full", self._registry()
        )
        self.assertIn("models:forecastops:lineage", full["release_profile"]["model_checks"])

    def test_live_smoke_stage_fails_closed_on_a_runtime_defaulting_to_full(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "runtime:release_profile"):
            self._smoke_with_readiness(self._readiness("full"), "core-ops")
        with self.assertRaisesRegex(RuntimeError, "may not run in staging"):
            self._smoke_with_readiness(self._readiness(), "")

    def test_live_smoke_stage_runs_the_model_gate_against_the_live_registry(self) -> None:
        # (a) missing models reported, but the registry still serves versions.
        with self.assertRaisesRegex(RuntimeError, "staging model gate failed: .*models:registry_refused"):
            self._smoke_with_readiness(self._readiness(), "core-ops", self._registry())
        # (a) capability state that is neither bound nor truthfully refused.
        broken = self._readiness()
        broken["details"]["models"]["capabilities"]["forecastops"]["reasonCode"] = None
        with self.assertRaisesRegex(RuntimeError, "runtime:model_capability:forecastops"):
            self._smoke_with_readiness(broken, "core-ops")
        # (b) claimed bindings with seeded models, no capabilities, or no lineage.
        seeded = self._readiness(ready=True)
        seeded["details"]["models"]["autoSeeded"] = True
        with self.assertRaisesRegex(RuntimeError, "runtime:model_bindings"):
            self._smoke_with_readiness(seeded, "core-ops", self._registry())
        bare = self._readiness(ready=True)
        bare["details"]["models"].pop("capabilities")
        with self.assertRaisesRegex(RuntimeError, "runtime:model_capability"):
            self._smoke_with_readiness(bare, "core-ops", self._registry())
        status, models = self._registry()
        models["items"][0].pop("approved_by")
        with self.assertRaisesRegex(RuntimeError, "models:forecastops:lineage"):
            self._smoke_with_readiness(self._readiness(ready=True), "core-ops", (status, models))
        # An unreadable registry is never evidence.
        with self.assertRaisesRegex(RuntimeError, "models:registry_refused"):
            self._smoke_with_readiness(
                self._readiness(), "core-ops", (0, {}), registry_error="URLError"
            )

    def test_live_verify_requires_the_admitted_release_profile(self) -> None:
        from product_ops.deployment.staging_lifecycle import verify_ephemeral_staging

        outputs = self._valid_outputs()
        for profile in ("", "dev-admin"):
            with self.subTest(profile=profile):
                receipt = verify_ephemeral_staging(
                    release_id=self.release_id,
                    candidate_sha=self.candidate_sha,
                    manifest_digest=self.manifest_digest,
                    project_id=self.project_id,
                    operator_identity=outputs["staging_runtime_service_account"],
                    lifecycle_outputs=outputs,
                    stage_executor=lambda _stage, _context: {"success": True},
                    now=self.now,
                    release_profile=profile,
                )
                self.assertFalse(receipt.success)
                self.assertTrue(
                    any("admitted manifest's release profile" in e for e in receipt.errors)
                )

    def test_generated_tfvars_carry_the_admitted_profile_into_the_module(self) -> None:
        from product_ops.deployment.staging_lifecycle import (
            StagingConfig,
            generate_tfvars,
            validate_staging_config,
            validate_tfvars_against_module,
        )

        base = dict(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            project_id=self.project_id,
            owner_task_id="ODP-CORE-OPS-RELEASE-PROFILE-001",
            kms_key_id="projects/p/locations/asia-east1/keyRings/staging/cryptoKeys/release",
            deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
            created_at="2026-08-27T06:00:00Z",
        )
        module = Path(__file__).resolve().parents[2] / "infra/terraform/modules/ephemeral_staging"
        tfvars = generate_tfvars(StagingConfig(**base, release_profile="core-ops"), now=self.now)
        self.assertEqual(tfvars["release_profile"], "core-ops")
        self.assertEqual(validate_tfvars_against_module(tfvars, module), [])
        missing = {key: value for key, value in tfvars.items() if key != "release_profile"}
        self.assertTrue(
            any("release_profile" in e for e in validate_tfvars_against_module(missing, module))
        )
        for profile in ("dev-admin", "", "Core-Ops"):
            with self.subTest(profile=profile):
                errors = validate_staging_config(
                    StagingConfig(**base, release_profile=profile), now=self.now
                )
                self.assertTrue(any("release_profile" in e for e in errors), errors)
        main_tf = (module / "main.tf").read_text(encoding="utf-8")
        self.assertEqual(
            main_tf.count('name  = "ODP_RELEASE_PROFILE"'),
            main_tf.count('name  = "ODP_DEPLOY_ENV"'),
        )

    def test_verify_ephemeral_staging_dev_identity_rejection(self) -> None:
        from product_ops.deployment.staging_lifecycle import verify_ephemeral_staging

        receipt = verify_ephemeral_staging(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            project_id=self.project_id,
            operator_identity="dev-smoke-operator@oday-dev-proj.iam.gserviceaccount.com",
            now=self.now,
        )

        self.assertFalse(receipt.success)
        self.assertTrue(any("dev smoke operator identity impersonation" in err for err in receipt.errors))

    def test_hold_ephemeral_staging_success(self) -> None:
        from product_ops.deployment.staging_lifecycle import hold_ephemeral_staging

        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            self._seed_live_state(state_dir)
            receipt = hold_ephemeral_staging(
                release_id=self.release_id,
                project_id=self.project_id,
                owner_task_id="ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001",
                reason="Investigation of staging migration rehearsal failure",
                ttl_hours=24,
                state_dir=state_dir,
                now=self.now,
            )

        self.assertTrue(receipt.success)
        self.assertEqual(receipt.action, "hold")
        self.assertEqual(receipt.release_id, self.release_id)
        self.assertEqual(len(receipt.resources), 1)
        self.assertEqual(receipt.resources[0]["status"], "retained_for_debugging")
        self.assertEqual(receipt.resources[0]["ttl_hours"], 24)
        self.assertEqual(receipt.resources[0]["expires_at"], "2026-08-28T06:00:00Z")
        self.assertTrue(receipt.metadata.get("secret_values_redacted"))

    def test_hold_ephemeral_staging_missing_reason(self) -> None:
        from product_ops.deployment.staging_lifecycle import hold_ephemeral_staging

        receipt = hold_ephemeral_staging(
            release_id=self.release_id,
            project_id=self.project_id,
            owner_task_id="ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001",
            reason="",
            now=self.now,
        )

        self.assertFalse(receipt.success)
        self.assertTrue(any("reason" in err.lower() for err in receipt.errors))

    def test_cli_verify_and_hold_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            create_receipt = Path(tmpdir) / "staging-create.json"
            verify_receipt = Path(tmpdir) / "staging-verify.json"
            hold_receipt = Path(tmpdir) / "staging-hold.json"

            code = main([
                "create",
                "--release-id", self.release_id,
                "--candidate-sha", self.candidate_sha,
                "--manifest-digest", self.manifest_digest,
                "--project-id", self.project_id,
                "--api-image", "asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "c" * 64,
                "--web-image", "asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "d" * 64,
                "--worker-image", "asia-east1-docker.pkg.dev/proj/repo/worker@sha256:" + "e" * 64,
                "--scheduler-image", "asia-east1-docker.pkg.dev/proj/repo/scheduler@sha256:" + "f" * 64,
                "--owner-task-id", "ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001",
                "--release-profile", "core-ops",
                "--kms-key-id", "projects/oday-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
                "--deployer-service-account-email", "deployer@oday-staging-proj.iam.gserviceaccount.com",
                "--dry-run",
                "--receipt", str(create_receipt),
            ])
            self.assertEqual(code, 0)
            self.assertTrue(create_receipt.is_file())
            data = json.loads(create_receipt.read_text(encoding="utf-8"))
            self.assertEqual(data["action"], "create")
            self.assertTrue(data["success"])

            code = main([
                "verify",
                "--release-id", self.release_id,
                "--candidate-sha", self.candidate_sha,
                "--manifest-digest", self.manifest_digest,
                "--project-id", self.project_id,
                "--worker-image", "asia-east1-docker.pkg.dev/proj/repo/worker@sha256:" + "e" * 64,
                "--scheduler-image", "asia-east1-docker.pkg.dev/proj/repo/scheduler@sha256:" + "f" * 64,
                "--release-profile", "core-ops",
                "--dry-run",
                "--receipt", str(verify_receipt),
            ])
            self.assertEqual(code, 0)
            self.assertTrue(verify_receipt.is_file())
            data = json.loads(verify_receipt.read_text(encoding="utf-8"))
            self.assertEqual(data["action"], "verify")
            self.assertTrue(data["success"])

            code = main([
                "hold",
                "--release-id", self.release_id,
                "--project-id", self.project_id,
                "--owner-task-id", "ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001",
                "--reason", "Test hold retention",
                "--ttl-hours", "48",
                "--state-dir", tmpdir,
                "--receipt", str(hold_receipt),
            ])
            self.assertEqual(code, 1)
            self.assertTrue(hold_receipt.is_file())
            data = json.loads(hold_receipt.read_text(encoding="utf-8"))
            self.assertEqual(data["action"], "hold")
            self.assertFalse(data["success"])
            self.assertTrue(any("missing" in error for error in data["errors"]))

    def test_immutable_release_identity_detects_worker_and_scheduler_image_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_root = Path(tmpdir)
            cfg = StagingConfig(
                release_id=self.release_id,
                candidate_sha=self.candidate_sha,
                manifest_digest=self.manifest_digest,
                project_id=self.project_id,
                kms_key_id="projects/oday-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
                deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
                owner_task_id="ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001",
                worker_image="asia-east1-docker.pkg.dev/proj/repo/worker@sha256:" + "e" * 64,
                scheduler_image="asia-east1-docker.pkg.dev/proj/repo/scheduler@sha256:" + "f" * 64,
                created_at="2026-08-24T12:00:00Z",
            )
            tfvars_path = state_root / f"{self.release_id}.tfvars.json"
            tfvars_path.write_text(json.dumps(generate_tfvars(cfg)), encoding="utf-8")

            # Same config passes
            self.assertEqual(validate_immutable_release_identity(cfg, state_root), [])

            # Changing worker_image fails
            changed_worker = dataclasses_replace(
                cfg, worker_image="asia-east1-docker.pkg.dev/proj/repo/worker@sha256:" + "1" * 64
            )
            errors = validate_immutable_release_identity(changed_worker, state_root)
            self.assertTrue(any("immutable worker_image" in err for err in errors), errors)

            # Changing scheduler_image fails
            changed_scheduler = dataclasses_replace(
                cfg, scheduler_image="asia-east1-docker.pkg.dev/proj/repo/scheduler@sha256:" + "2" * 64
            )
            errors = validate_immutable_release_identity(changed_scheduler, state_root)
            self.assertTrue(any("immutable scheduler_image" in err for err in errors), errors)


def _load_gate_fixtures() -> Any:
    """The live gate's own passing-deployment doubles (tests/e2e)."""

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "staging_live_gate_fixtures", ROOT / "tests/e2e/test_live_e2e_gate_dev_admin.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StagingLiveE2EGateTests(unittest.TestCase):
    """ODP-CORE-OPS-STAGING-LIVE-GATE-001: staging verify runs the complete gate."""

    SMOKE_TOKEN = "staging-smoke-id-token-value"
    API_TOKEN = "staging-api-transport-token-value"
    WEB_TOKEN = "staging-web-transport-token-value"

    @classmethod
    def setUpClass(cls) -> None:
        cls.fx = _load_gate_fixtures()

    def setUp(self) -> None:
        self.release_id = "odp-20260824-001"
        # The gate fixtures serve this SHA from /platform/version.
        self.candidate_sha = self.fx.base.EXPECTED_SHA
        self.manifest_digest = "sha256:" + "b" * 64
        self.project_id = "oday-staging-proj"
        self.now = datetime(2026, 8, 27, 6, 0, 0, tzinfo=UTC)
        self.outputs = EphemeralStagingVerificationAndHoldTests._valid_outputs(self)

    def _core_ops_routes(self, profile: str = "core-ops", **overrides: Any) -> dict[str, Any]:
        readiness = self.fx.missing_model_readiness(profile)
        readiness["details"]["deploymentMode"] = "staging"
        return self.fx.api_routes(
            **{"anon GET /readiness": self.fx.base.response(200, readiness), **overrides}
        )

    def _run_stage(
        self,
        routes: dict[str, Any],
        profile: str = "core-ops",
        *,
        worker_ok: bool = True,
        web_http: Any = None,
    ) -> tuple[Mapping[str, Any], dict[str, Any]]:
        from unittest.mock import patch

        from product_ops.deployment import staging_lifecycle as lifecycle

        seen: dict[str, Any] = {"tokens": [], "clients": []}
        tokens = {
            lifecycle.staging_smoke_token_audience(self.release_id): self.SMOKE_TOKEN,
            self.outputs["staging_api_uri"]: self.API_TOKEN,
            self.outputs["staging_web_uri"]: self.WEB_TOKEN,
        }

        def mint(*, audience: str, operator_identity: str, gcloud_bin: str) -> str:
            seen["tokens"].append((audience, operator_identity))
            return tokens[audience]

        def describe(kind: str, name: str, **_kwargs: Any) -> dict[str, Any]:
            return {"template": {"template": {
                "containers": [{"image": self.outputs["staging_worker_image"]}],
                "vpcAccess": {"egress": "ALL_TRAFFIC"},
            }}}

        def clients(config: Any, *, web_transport_token: str, correlation_id: str) -> Any:
            seen["config"] = config
            seen["clients"].append((web_transport_token, correlation_id))
            return (
                self.fx.base.FakeHttp(routes),
                web_http if web_http is not None else self.fx.base.passing_web_http(),
            )

        def driver(config: Any, *, job: str, region: str, project_id: str, gcloud_bin: str) -> Any:
            seen["driver"] = (job, region, project_id)
            return self.fx.base.FakeWorkerDriver(ok=worker_ok)

        with patch.object(lifecycle, "_live_gcloud", side_effect=lambda argv, **kw: argv[3] if argv[0] == "iam" else ""), \
                patch.object(lifecycle, "_live_cloud_run_description", side_effect=describe), \
                patch.object(lifecycle, "_live_identity_token", side_effect=mint), \
                patch.object(lifecycle, "_live_gate_clients", side_effect=clients), \
                patch.object(lifecycle, "_live_gate_worker_driver", side_effect=driver), \
                patch.object(lifecycle, "_live_gate_correlation_id", return_value=self.fx.base.CORRELATION_ID), \
                patch.object(lifecycle, "STAGING_LIVE_E2E_POLL_INTERVAL_SECONDS", 0.001):
            executor = lifecycle.make_live_rehearsal_executor(
                self.outputs,
                project_id=self.project_id,
                region="asia-east1",
                operator_identity=self.outputs["staging_runtime_service_account"],
            )
            result = executor(
                "live_e2e_gate",
                {"release_profile": profile, "manifest_digest": self.manifest_digest},
            )
        return result, seen

    def test_the_gate_is_the_last_rehearsal_stage(self) -> None:
        from product_ops.deployment.staging_lifecycle import REHEARSAL_STAGE_NAMES

        self.assertEqual(REHEARSAL_STAGE_NAMES[-1], "live_e2e_gate")
        self.assertEqual(len(set(REHEARSAL_STAGE_NAMES)), 10)

    def test_core_ops_staging_passes_the_complete_gate_with_missing_models_refused(self) -> None:
        from product_ops.deployment.staging_lifecycle import (
            STAGING_SMOKE_ROLES,
            staging_smoke_token_audience,
        )

        result, seen = self._run_stage(self._core_ops_routes())

        self.assertTrue(result["success"], result.get("detail"))
        proof = result["live_e2e_gate"]
        self.assertTrue(proof["ok"])
        names = {check["name"] for check in proof["checks"]}
        # The whole gate ran, not only the model subset staging already had.
        self.assertTrue({
            "config:release_profile_deployment",
            "release:platform_version",
            "runtime:readiness",
            "runtime:persistence",
            "runtime:release_profile",
            "runtime:provider",
            "runtime:model_limitation_reported",
            "auth:anonymous_denied",
            "auth:operator_bootstrap",
            "auth:web_operator_requires_login",
            "models:registry_refused",
            "worker:enqueue",
            "worker:idempotent_replay",
            "worker:drain_trigger",
            "worker:terminal_disabled",
            "audit:durable_receipt",
            "audit:receipt_integrity",
        } <= names, names)
        self.assertNotIn("runtime:model_bindings", names)
        self.assertFalse(any(name.startswith(("session:", "admin:", "config:dev_admin")) for name in names))
        self.assertEqual(
            proof["release_profile"],
            {"name": "core-ops", "model_readiness_claimed": False, "full_product_acceptance_claimed": False},
        )
        # Inputs come from the release's own Terraform outputs and identity.
        config = seen["config"]
        runtime_sa = self.outputs["staging_runtime_service_account"]
        self.assertEqual(
            sorted(seen["tokens"]),
            sorted([
                (staging_smoke_token_audience(self.release_id), runtime_sa),
                (self.outputs["staging_api_uri"], runtime_sa),
                (self.outputs["staging_web_uri"], runtime_sa),
            ]),
        )
        self.assertEqual(config.bearer_token, self.SMOKE_TOKEN)
        self.assertEqual(config.api_transport_token, self.API_TOKEN)
        self.assertEqual(seen["clients"][0][0], self.WEB_TOKEN)
        self.assertEqual(config.expected_deployment, "staging")
        self.assertEqual(config.expected_sha, self.candidate_sha)
        self.assertEqual(config.operator_role, ",".join(STAGING_SMOKE_ROLES))
        self.assertEqual(config.operator_tenant, self.outputs["staging_tenant_id"])
        self.assertEqual((config.external_provider_mode, config.required_provider_ids), ("disabled", ()))
        self.assertEqual(config.expected_manifest_digest, self.manifest_digest)
        self.assertEqual(
            seen["driver"], (self.outputs["staging_worker_job_name"], "asia-east1", self.project_id)
        )

    def test_full_profile_is_held_to_the_full_model_assertions(self) -> None:
        result, _ = self._run_stage(self._core_ops_routes("full"), "full")

        self.assertFalse(result["success"])
        failed = {check["name"] for check in result["live_e2e_gate"]["checks"] if not check["ok"]}
        self.assertIn("runtime:model_bindings", failed)
        self.assertIn("runtime:model_bindings", result["detail"])

    def test_each_gate_section_failure_fails_the_stage(self) -> None:
        base = self.fx.base
        cases = {
            "runtime:persistence": dict(routes=self._failed_persistence_routes()),
            "auth:operator_bootstrap": dict(routes=self._core_ops_routes(**{
                "GET /api/v1/operator/bootstrap": base.response(401, {"detail": "UNKNOWN_SERVICE"}),
            })),
            "auth:web_operator_requires_login": dict(
                routes=self._core_ops_routes(),
                web_http=base.FakeHttp({"anon GET /operator": base.response(403)}),
            ),
            "worker:drain_trigger": dict(routes=self._core_ops_routes(), worker_ok=False),
            "models:registry_refused": dict(routes=self._core_ops_routes(**{
                "GET /api/v1/learninghub/models": base.response(200, {"items": [{"name": "x"}]}),
            })),
        }
        for check, kwargs in cases.items():
            with self.subTest(check=check):
                result, _ = self._run_stage(kwargs.pop("routes"), **kwargs)
                self.assertFalse(result["success"])
                self.assertFalse(result["live_e2e_gate"]["ok"])
                self.assertIn(check, result["detail"])

    def _failed_persistence_routes(self) -> dict[str, Any]:
        readiness = self.fx.missing_model_readiness("core-ops")
        readiness["details"]["deploymentMode"] = "staging"
        readiness["details"]["persistence"]["reachable"] = False
        return self.fx.api_routes(**{"anon GET /readiness": self.fx.base.response(200, readiness)})

    def test_a_profile_staging_may_not_serve_never_reaches_the_gate(self) -> None:
        for profile in ("", "dev-admin"):
            with self.subTest(profile=profile):
                with self.assertRaisesRegex(RuntimeError, "may not run in staging"):
                    self._run_stage(self._core_ops_routes(), profile)

    def test_gate_failure_fails_live_verify_and_leaves_the_release_holdable(self) -> None:
        from unittest.mock import patch

        from product_ops.deployment import staging_lifecycle as lifecycle

        stage_result, _ = self._run_stage(self._failed_persistence_routes())

        def executor(stage: str, _context: Mapping[str, Any]) -> Mapping[str, Any]:
            return stage_result if stage == "live_e2e_gate" else {"success": True}

        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            EphemeralStagingVerificationAndHoldTests._seed_live_state(self, state_dir)
            marker_path = lifecycle._lifecycle_state_path(self.release_id, state_dir)
            seeded = json.loads(marker_path.read_text(encoding="utf-8"))
            marker_path.write_text(json.dumps({**seeded, "outputs": self.outputs}), encoding="utf-8")
            with patch.object(lifecycle, "_write_lifecycle_state", wraps=lifecycle._write_lifecycle_state) as write:
                receipt = lifecycle.verify_ephemeral_staging(
                    release_id=self.release_id,
                    candidate_sha=self.candidate_sha,
                    manifest_digest=self.manifest_digest,
                    project_id=self.project_id,
                    state_dir=state_dir,
                    operator_identity=self.outputs["staging_runtime_service_account"],
                    lifecycle_outputs=self.outputs,
                    stage_executor=executor,
                    now=self.now,
                    release_profile="core-ops",
                )
            marker = json.loads(lifecycle._lifecycle_state_path(self.release_id, state_dir).read_text())

        self.assertFalse(receipt.success)
        self.assertTrue(any(
            error.startswith("Stage live_e2e_gate failed.") and "runtime:persistence" in error
            for error in receipt.errors
        ), receipt.errors)
        stage = next(r for r in receipt.resources if r["stage"] == "live_e2e_gate")
        self.assertEqual(stage["status"], "failed")
        self.assertFalse(stage["proof"]["live_e2e_gate"]["ok"])
        self.assertIn("postgresql", stage["proof"]["live_e2e_gate"]["blocking_dependencies"])
        # The existing hold path accepts a release in this state.
        self.assertEqual(write.call_args.kwargs["status"], "verification_failed")
        self.assertEqual(marker["status"], "verification_failed")

    def test_tokens_never_reach_the_receipt_or_its_errors(self) -> None:
        base = self.fx.base
        echo = {
            "detail": f"Bearer {self.SMOKE_TOKEN}",
            "transport": self.API_TOKEN,
            "web": self.WEB_TOKEN,
        }
        stage_result, _ = self._run_stage(self._core_ops_routes(**{
            "GET /api/v1/operator/bootstrap": base.response(500, echo),
        }))
        self.assertFalse(stage_result["success"])

        from product_ops.deployment.staging_lifecycle import verify_ephemeral_staging

        receipt = verify_ephemeral_staging(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            project_id=self.project_id,
            dry_run=True,
            stage_executor=lambda stage, _c: stage_result if stage == "live_e2e_gate" else True,
            now=self.now,
        )
        published = json.dumps(receipt.to_dict())
        self.assertIn("auth:operator_bootstrap", published)
        for secret in (self.SMOKE_TOKEN, self.API_TOKEN, self.WEB_TOKEN):
            self.assertNotIn(secret, published)
            self.assertNotIn(secret, json.dumps(stage_result))

    def test_an_exception_inside_the_gate_is_reported_by_type_only(self) -> None:
        from unittest.mock import patch

        from product_ops.deployment import staging_lifecycle as lifecycle

        with patch.object(
            lifecycle._live_e2e_gate(),
            "evaluate_gate",
            side_effect=ValueError(f"boom {self.SMOKE_TOKEN}"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self._run_stage(self._core_ops_routes())
        self.assertEqual(str(ctx.exception), "live E2E gate could not run: ValueError")

    def test_staging_gate_config_never_reads_dev_inputs_from_the_environment(self) -> None:
        import os
        from unittest.mock import patch

        from product_ops.deployment.staging_lifecycle import staging_live_gate_config

        dev_env = {
            "ODP_DEV_ADMIN_USERNAME": "dev-admin-user",
            "ODP_DEV_ADMIN_PASSWORD": "dev-admin-password",
            "ODP_DEV_ADMIN_CREDENTIAL_BUNDLE": '{"username": "x"}',
            "ODP_DEV_BOOTSTRAP_ADMIN_PASSWORD": "bootstrap-password",
            "ODP_OPERATOR_SMOKE_BEARER_TOKEN": "dev-smoke-token",
            "ODP_API_INVOKER_TOKEN": "dev-invoker-token",
            "ODP_OPERATOR_SMOKE_ROLE": "platform_admin",
            "ODP_RELEASE_PROFILE": "dev-admin",
            "ODP_EXTERNAL_PROVIDER_MODE": "live",
        }
        with patch.dict(os.environ, dev_env):
            config = staging_live_gate_config(
                self.outputs,
                release_profile="core-ops",
                manifest_digest=self.manifest_digest,
                bearer_token=self.SMOKE_TOKEN,
                api_transport_token=self.API_TOKEN,
            )
        rendered = repr(config) + json.dumps(
            {k: v for k, v in vars(config).items() if isinstance(v, str)}
        )
        for value in dev_env.values():
            if value not in {"core-ops", "live"}:
                self.assertNotIn(value, rendered)
        self.assertFalse(config.dev_admin)
        self.assertEqual(config.release_profile, "core-ops")
        self.assertEqual(config.external_provider_mode, "disabled")

    def test_the_worker_driver_executes_the_release_scoped_worker_job(self) -> None:
        from unittest.mock import patch

        from product_ops.deployment import staging_lifecycle as lifecycle

        config = lifecycle.staging_live_gate_config(
            self.outputs,
            release_profile="core-ops",
            manifest_digest=self.manifest_digest,
            bearer_token=self.SMOKE_TOKEN,
            api_transport_token=self.API_TOKEN,
        )
        driver = lifecycle._live_gate_worker_driver(
            config,
            job=self.outputs["staging_worker_job_name"],
            region="asia-east1",
            project_id=self.project_id,
            gcloud_bin="/opt/gcloud",
        )
        with patch.object(lifecycle._live_e2e_gate(), "_run_subprocess", return_value=(0, "")) as run:
            ok, _detail = driver.drain()
        self.assertTrue(ok)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:5], ["/opt/gcloud", "run", "jobs", "execute", self.outputs["staging_worker_job_name"]])
        self.assertIn(f"--project={self.project_id}", argv)
        self.assertIn("--wait", argv)

    def test_both_clients_are_bound_to_the_release_origins_with_transport_tokens(self) -> None:
        from product_ops.deployment import staging_lifecycle as lifecycle

        config = lifecycle.staging_live_gate_config(
            self.outputs,
            release_profile="core-ops",
            manifest_digest=self.manifest_digest,
            bearer_token=self.SMOKE_TOKEN,
            api_transport_token=self.API_TOKEN,
        )
        api, web = lifecycle._live_gate_clients(
            config, web_transport_token=self.WEB_TOKEN, correlation_id="corr-1"
        )
        self.assertEqual(api._base_url, self.outputs["staging_api_uri"])
        self.assertEqual((api._bearer_token, api._transport_token), (self.SMOKE_TOKEN, self.API_TOKEN))
        self.assertEqual(web._base_url, self.outputs["staging_web_uri"])
        self.assertEqual((web._bearer_token, web._transport_token), ("", self.WEB_TOKEN))

    # --- the smoke principal is release-scoped Terraform, nothing else ---

    def _api_service_block(self, main_text: str) -> str:
        start = main_text.index('resource "google_cloud_run_v2_service" "staging_api"')
        end = main_text.index('resource "google_cloud_run_v2_service" "staging_web"')
        return main_text[start:end]

    def test_terraform_smoke_principal_matches_the_pinned_python_contract(self) -> None:
        from product_ops.deployment.staging_lifecycle import (
            STAGING_SMOKE_ROLES,
            STAGING_SMOKE_TOKEN_AUDIENCE_PREFIX,
            staging_smoke_token_audience,
        )

        main_text = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        audience = re.search(r'smoke_token_audience\s*=\s*"([^"]+)"', main_text)
        self.assertIsNotNone(audience)
        self.assertEqual(audience.group(1), STAGING_SMOKE_TOKEN_AUDIENCE_PREFIX + "${local.release_label}")
        self.assertEqual(
            staging_smoke_token_audience(self.release_id),
            STAGING_SMOKE_TOKEN_AUDIENCE_PREFIX + release_label_value(self.release_id),
        )
        roles = re.search(r"smoke_roles\s*=\s*\[([^\]]*)\]", main_text)
        self.assertIsNotNone(roles)
        self.assertEqual(tuple(re.findall(r'"([^"]+)"', roles.group(1))), STAGING_SMOKE_ROLES)

        api = self._api_service_block(main_text)
        for name, value in (
            ("ODP_AUTH_SERVICE_ISSUER", '"https://accounts.google.com"'),
            ("ODP_AUTH_SERVICE_JWKS_URI", '"https://www.googleapis.com/oauth2/v3/certs"'),
            ("ODP_AUTH_SERVICE_AUDIENCES", "local.smoke_token_audience"),
        ):
            self.assertRegex(api, rf'name\s*=\s*"{name}"\s*\n\s*value\s*=\s*{re.escape(value)}')
        principal_map = re.search(
            r'name\s*=\s*"ODP_AUTH_PRINCIPAL_MAP"\s*\n\s*value\s*=\s*jsonencode\(\{(.*?)\}\)\n', api, re.S
        )
        self.assertIsNotNone(principal_map)
        body = principal_map.group(1)
        # Exactly one principal: this release's runtime account, by numeric id.
        self.assertEqual(re.findall(r"\(([^)]+)\)\s*=", body), ["google_service_account.staging_runtime.unique_id"])
        self.assertIn("roles     = local.smoke_roles", body)
        self.assertIn("tenant_id = local.tenant_id", body)
        self.assertNotIn("var.", body)
        # Only the API verifies application tokens.
        self.assertEqual(main_text.count('"ODP_AUTH_PRINCIPAL_MAP"'), 1)

    def test_the_smoke_principal_adds_no_secret_iam_or_dev_input(self) -> None:
        main_text = (MODULE_DIR / "main.tf").read_text(encoding="utf-8")
        variables_text = (MODULE_DIR / "variables.tf").read_text(encoding="utf-8")
        for forbidden in (
            "ODP_DEV_ADMIN",
            "ODP_OPERATOR_SMOKE",
            "ODP_AUTH_PRINCIPAL_MAP_SECRET",
            "ODP_IDENTITY_BOOTSTRAP",
            "ODP_IDENTITY_TOKEN_SIGNING_KEY",
            "ODP_AUTH_HS256_KEYS",
        ):
            self.assertNotIn(forbidden, main_text)
            self.assertNotIn(forbidden, variables_text)
        self.assertNotRegex(variables_text, r'variable\s+"[^"]*(smoke|principal)[^"]*"')
        # The release-scoped secrets and IAM grants are unchanged.
        self.assertEqual(
            re.findall(r'resource "google_secret_manager_secret" "(\w+)"', main_text),
            ["staging_database_url", "staging_cursor_signing_key", "staging_web_session"],
        )
        self.assertEqual(len(re.findall(r'resource "random_password"', main_text)), 3)
        self.assertEqual(
            re.findall(r'resource "google_[a-z0-9_]*iam_member" "(\w+)"', main_text),
            [
                "staging_runtime_cloudsql",
                "staging_runtime_db_url",
                "staging_runtime_data",
                "deployer_sa_user_runtime",
                "deployer_sa_user_web",
                "deployer_sa_user_worker",
                "staging_web_invokes_api",
                "staging_worker_invokes_api",
                "staging_runtime_publisher",
                "staging_worker_subscriber",
                "staging_runtime_cursor_key",
                "staging_web_session",
            ],
        )

    def test_the_smoke_principal_is_a_release_owned_resource_destroyed_with_the_release(self) -> None:
        cfg = StagingConfig(
            release_id=self.release_id,
            candidate_sha=self.candidate_sha,
            manifest_digest=self.manifest_digest,
            project_id=self.project_id,
            owner_task_id="ODP-CORE-OPS-STAGING-LIVE-GATE-001",
            kms_key_id="projects/oday-staging-proj/locations/asia-east1/keyRings/staging/cryptoKeys/release",
            deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
            created_at="2026-08-27T06:00:00Z",
        )
        names = get_ephemeral_resource_names(self.release_id, self.project_id)
        planned = plan_staging_resources(cfg, now=self.now)
        smoke = [
            resource for resource in planned
            if resource.resource_type == "google_service_account"
            and resource.resource_name == names["sa_runtime"]
        ]
        self.assertEqual(len(smoke), 1)
        self.assertTrue(is_staging_ephemeral_resource(smoke[0].labels, self.release_id))
        # The verify operator (whose tokens the gate uses) is that same account.
        self.assertEqual(
            self.outputs["staging_runtime_service_account"],
            f"{names['sa_runtime']}@{self.project_id}.iam.gserviceaccount.com",
        )


def dataclasses_replace(obj: StagingConfig, **changes: Any) -> StagingConfig:
    d = obj.to_dict()
    d.update(changes)
    return StagingConfig(**d)


if __name__ == "__main__":
    unittest.main()
