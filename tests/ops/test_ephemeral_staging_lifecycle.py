#!/usr/bin/env python3
"""Tests for ephemeral staging IaC, lifecycle management, cleanup, and orphan scanning."""

from __future__ import annotations

import json
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
    is_staging_ephemeral_resource,
    plan_staging_resources,
    release_label_value,
    scan_orphans,
    validate_immutable_release_identity,
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
        self.assertEqual(tfvars["tenant_id"], "")
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
        self.assertIn("No matching ephemeral staging resources", receipt.errors[0])

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

            from product_ops.deployment.staging_lifecycle import _terraform_state_paths, make_terraform_creation_executor
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
        expires_time = datetime(2026, 9, 1, 14, 0, 0, tzinfo=UTC)  # 168h TTL extension

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
        expires_time = datetime(2026, 9, 1, 14, 0, 0, tzinfo=UTC)
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
        from product_ops.deployment.staging_lifecycle import _terraform_state_paths, make_terraform_creation_executor

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
        from product_ops.deployment.staging_lifecycle import _terraform_state_paths, make_terraform_creation_executor

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
        from product_ops.deployment.staging_lifecycle import _terraform_state_paths, make_terraform_creation_executor

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
        from product_ops.deployment.staging_lifecycle import _terraform_state_paths, generate_tfvars, main

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
        from product_ops.deployment.staging_lifecycle import _terraform_state_paths, generate_tfvars, validate_immutable_release_identity

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


def dataclasses_replace(obj: StagingConfig, **changes: Any) -> StagingConfig:
    d = obj.to_dict()
    d.update(changes)
    return StagingConfig(**d)


if __name__ == "__main__":
    unittest.main()
