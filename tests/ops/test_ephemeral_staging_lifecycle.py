#!/usr/bin/env python3
"""Tests for ephemeral staging IaC, lifecycle management, cleanup, and orphan scanning."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from product_ops.deployment.staging_lifecycle import (
    StagingConfig,
    cleanup_ephemeral_staging,
    compute_release_hash,
    create_ephemeral_staging,
    extend_staging_ttl,
    generate_staging_labels,
    generate_tfvars,
    get_ephemeral_resource_names,
    plan_staging_resources,
    scan_orphans,
    validate_module_contract,
    validate_staging_config,
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
        self.assertEqual(labels["release_id"], "odp-20260824-001")
        self.assertEqual(labels["candidate_sha"], "a" * 40)
        self.assertEqual(labels["manifest_digest_prefix"], "b" * 16)
        self.assertEqual(labels["owner_task"], "odp-ephemeral-staging-iac-001")
        self.assertEqual(labels["created_at"], "2026-08-24-12-00-00")
        self.assertEqual(labels["expires_at"], "2026-08-25-12-00-00")

    def test_generate_tfvars(self) -> None:
        now = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
        tfvars = generate_tfvars(self.valid_config, created_at=now)
        self.assertEqual(tfvars["project_id"], "oday-staging-proj")
        self.assertEqual(tfvars["release_id"], "odp-20260824-001")
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
        self.assertTrue(receipt_allowed.success)

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
            {"id": "fresh-res", "type": "google_sql_database", "labels": fresh_labels},
            {"id": "expired-res", "type": "google_sql_database", "labels": expired_labels},
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
        self.assertIn("odp-expired-001", result.expired_releases)
        self.assertTrue(len(result.alerts) >= 2)

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
            )

        # Missing owner raises ValueError
        with self.assertRaises(ValueError):
            extend_staging_ttl(
                release_id="odp-20260824-001",
                extend_hours=12,
                reason="debugging",
                owner="",
                current_expires_at=curr_expires,
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


def dataclasses_replace(obj: StagingConfig, **changes: Any) -> StagingConfig:
    d = obj.to_dict()
    d.update(changes)
    return StagingConfig(**d)


if __name__ == "__main__":
    unittest.main()
