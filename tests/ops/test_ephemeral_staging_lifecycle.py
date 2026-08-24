#!/usr/bin/env python3
"""Tests for ephemeral staging IaC, lifecycle management, cleanup, and orphan scanning."""

from __future__ import annotations

import json
import re
import sys
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
            kms_key_id="projects/p/locations/asia-east1/keyRings/r/cryptoKeys/k",
            deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
            api_image="asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "c" * 64,
            web_image="asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "d" * 64,
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
                "--owner-task-id", "ODP-EPHEMERAL-STAGING-IAC-001",
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
            kms_key_id="projects/p/locations/asia-east1/keyRings/r/cryptoKeys/k",
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
                "--owner-task-id", "ODP-EPHEMERAL-STAGING-IAC-001",
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
            region="asia-east1",
            api_image="asia-east1-docker.pkg.dev/proj/repo/api@sha256:" + "c" * 64,
            web_image="asia-east1-docker.pkg.dev/proj/repo/web@sha256:" + "d" * 64,
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


def dataclasses_replace(obj: StagingConfig, **changes: Any) -> StagingConfig:
    d = obj.to_dict()
    d.update(changes)
    return StagingConfig(**d)


if __name__ == "__main__":
    unittest.main()
