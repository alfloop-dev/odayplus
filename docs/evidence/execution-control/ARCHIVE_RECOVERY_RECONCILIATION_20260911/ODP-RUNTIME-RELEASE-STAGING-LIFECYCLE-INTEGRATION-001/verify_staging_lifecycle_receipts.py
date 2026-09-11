#!/usr/bin/env python3
"""Narrow offline verification script for ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001.

Demonstrates that the ephemeral staging lifecycle state machine generates valid,
secret-free receipts across create, verify (all 9 rehearsal stages), hold, and cleanup
actions in an isolated offline / contract environment without live cloud deployment.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from product_ops.deployment.staging_lifecycle import (
    REHEARSAL_STAGE_NAMES,
    StagingConfig,
    create_ephemeral_staging,
    cleanup_ephemeral_staging,
    generate_staging_labels,
    hold_ephemeral_staging,
    verify_ephemeral_staging,
)


def run_offline_receipt_verification() -> dict[str, bool]:
    now = datetime.now(UTC)
    release_id = "rel-20260911-reconciliation-001"
    candidate_sha = "fada677569265ed258649b841e5a67fe7bb5dd80"
    manifest_digest = "sha256:" + "a" * 64
    project_id = "oday-staging-proj"
    owner_task = "ODP-RUNTIME-RELEASE-STAGING-LIFECYCLE-INTEGRATION-001"

    results: dict[str, bool] = {}

    # 1. Create Stage (Dry Run / Contract Mode)
    config = StagingConfig(
        release_id=release_id,
        candidate_sha=candidate_sha,
        manifest_digest=manifest_digest,
        project_id=project_id,
        owner_task_id=owner_task,
        kms_key_id="projects/oday-staging-proj/locations/asia-east1/keyRings/ring/cryptoKeys/key",
        deployer_service_account_email="deployer@oday-staging-proj.iam.gserviceaccount.com",
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    create_receipt = create_ephemeral_staging(config, dry_run=True, now=now)
    assert create_receipt.success, f"Create receipt failed: {create_receipt.errors}"
    assert create_receipt.action == "create"
    assert create_receipt.release_id == release_id
    assert create_receipt.candidate_sha == candidate_sha
    results["create_receipt_generated"] = True

    # 2. Verify Stage (All 9 Rehearsal Stages, Secret-Free)
    verify_receipt = verify_ephemeral_staging(
        release_id=release_id,
        candidate_sha=candidate_sha,
        manifest_digest=manifest_digest,
        project_id=project_id,
        dry_run=True,
        now=now,
    )
    assert verify_receipt.success, f"Verify receipt failed: {verify_receipt.errors}"
    assert verify_receipt.action == "verify"
    assert verify_receipt.metadata.get("secret_values_redacted") is True
    assert verify_receipt.metadata.get("public_egress") == "default_deny"
    assert verify_receipt.metadata.get("external_sources_expected_enabled") == []
    assert verify_receipt.metadata.get("identity_scope") == "release_scoped_least_privilege"

    executed_stages = [r["stage"] for r in verify_receipt.resources]
    assert len(executed_stages) == len(REHEARSAL_STAGE_NAMES)
    for expected_stage in REHEARSAL_STAGE_NAMES:
        assert expected_stage in executed_stages, f"Missing stage {expected_stage}"
    assert all(r["success"] for r in verify_receipt.resources)
    results["verify_receipt_9_stages_secret_free"] = True

    # 3. Hold Stage (TTL Extension Record)
    hold_receipt = hold_ephemeral_staging(
        release_id=release_id,
        project_id=project_id,
        owner_task_id=owner_task,
        reason="Preserved for acceptance reconciliation verification",
        ttl_hours=48,
        require_live_state=False,
        now=now,
    )
    assert hold_receipt.success, f"Hold receipt failed: {hold_receipt.errors}"
    assert hold_receipt.action == "hold"
    results["hold_receipt_generated"] = True

    # 4. Cleanup Stage (Label-Matched Resource Teardown)
    target_labels = generate_staging_labels(
        release_id=release_id,
        candidate_sha=candidate_sha,
        manifest_digest=manifest_digest,
        owner_task_id=owner_task,
    )
    inventory = [
        {"id": "staging-db-001", "type": "google_sql_database", "labels": target_labels},
        {"id": "staging-bucket-001", "type": "google_storage_bucket", "labels": target_labels},
        {"id": "staging-svc-api", "type": "google_cloud_run_v2_service", "labels": target_labels},
    ]
    cleanup_receipt = cleanup_ephemeral_staging(
        release_id=release_id,
        project_id=project_id,
        resource_inventory=inventory,
        deletion_executor=lambda res: True,
        dry_run=True,
        now=now,
    )
    assert cleanup_receipt.success, f"Cleanup receipt failed: {cleanup_receipt.errors}"
    assert cleanup_receipt.action == "cleanup"
    assert len(cleanup_receipt.resources) == 3
    results["cleanup_receipt_generated"] = True

    # Output sample verify receipt
    sample_path = Path(__file__).parent / "sample-secret-free-verify-receipt.json"
    sample_path.write_text(json.dumps(verify_receipt.to_dict(), indent=2), encoding="utf-8")
    results["sample_receipt_written"] = True

    return results


if __name__ == "__main__":
    results = run_offline_receipt_verification()
    print(json.dumps(results, indent=2))
    print("All offline staging lifecycle receipt verification checks passed!")
