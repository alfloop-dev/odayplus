"""Fail-closed contract tests for the staging rollout dry-run record.

This task has no successful staging run to attest.  The tests therefore guard
against the exact regression that caused the previous review rejection:
shape-valid placeholder digests and self-authored status fields must never be
accepted as deployed evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

from delivery_toolchain.release.release_manifest import (
    is_placeholder_digest,
    load_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs/evidence/gates/RELEASE_MANIFEST.json"
EVIDENCE_DIR = ROOT / "docs/evidence/runtime/ODP-EPHEMERAL-STAGING-ROLLOUT-001"
DRY_RUN_PATH = EVIDENCE_DIR / "staging-rollout-dry-run.json"
README_PATH = EVIDENCE_DIR / "README.md"


def test_only_dry_run_record_exists() -> None:
    assert DRY_RUN_PATH.is_file()
    assert README_PATH.is_file()
    evidence_files = {path.name for path in EVIDENCE_DIR.iterdir() if path.is_file()}
    assert evidence_files == {"README.md", "staging-rollout-dry-run.json"}


def test_dry_run_record_cannot_claim_staging_verified() -> None:
    record = json.loads(DRY_RUN_PATH.read_text(encoding="utf-8"))
    assert record["evidence_mode"] == "dry-run"
    assert record["evidence_status"] == "blocked"
    assert record["deployment_observed"] is False
    assert record["stage"] == "not-admitted"
    assert record["deployment_path"]["staging_dispatch_observed"] is False
    assert record["deployment_path"]["successful_staging_run"] is False
    assert record["deployment_path"]["remote_staging_proof"] is None
    assert "ghcr.io" not in record["deployment_path"]["configured_registry"]
    assert record["deployment_path"]["configured_registry"].endswith("/oday-plus-dev")
    assert "staging-verified" not in json.dumps(record)


def test_source_manifest_placeholder_digests_match_recorded_blocker_state() -> None:
    """The record must track the composed source manifest, not a stale one.

    Composing origin/dev brought in a dev Runtime Release manifest whose
    digests are real Artifact Registry references.  The record may therefore
    mark B1 as resolved, but only at the dev layer: it must still describe the
    exact manifest it read and keep the superseded placeholder record for
    audit, so the previous 11-placeholder state cannot silently disappear.
    """
    manifest, errors = load_manifest(MANIFEST_PATH)
    assert errors == []
    assert manifest is not None

    digest_values: list[str] = []
    for component in manifest["components"].values():
        digest_values.append(component["image"])
    digest_values.extend(
        manifest[field]
        for field in ("migration_digest", "data_contract_digest", "source_policy_digest")
    )
    digest_values.extend(manifest["sbom_refs"])
    digest_values.extend(manifest["signature_refs"])

    placeholder_values = [value for value in digest_values if is_placeholder_digest(value)]
    record = json.loads(DRY_RUN_PATH.read_text(encoding="utf-8"))
    recorded = record["release_manifest"]
    assert len(placeholder_values) == recorded["placeholder_digest_count"]
    assert recorded["manifest_digest"] == manifest["manifest_digest"]
    assert recorded["candidate_sha"] == manifest["candidate_sha"]
    assert recorded["release_id"] == manifest["release_id"]
    assert recorded["superseded_record"]["placeholder_digest_count"] == 11

    blockers = {blocker["id"]: blocker for blocker in record["blockers"]}
    if placeholder_values:
        assert blockers["B1"].get("status", "open") == "open"
    else:
        assert blockers["B1"]["status"] == "resolved_at_dev_layer"
    for component in manifest["components"].values():
        assert "ghcr.io" not in component["image"]


def test_resolved_dev_layer_blockers_do_not_admit_staging() -> None:
    record = json.loads(DRY_RUN_PATH.read_text(encoding="utf-8"))
    statuses = {blocker["id"]: blocker.get("status", "open") for blocker in record["blockers"]}
    allowed = {"open", "partially_resolved", "resolved_at_dev_layer"}
    assert set(statuses.values()) <= allowed
    assert any(status != "resolved_at_dev_layer" for status in statuses.values())
    assert record["evidence_status"] == "blocked"
    assert record["latest_recheck"]["deployment_observed"] is False
    assert record["latest_recheck"]["apply_or_deploy_attempted"] is False


def test_placeholder_predicate_rejects_shape_valid_fixture_digests() -> None:
    assert is_placeholder_digest("sha256:" + "1" * 64)
    assert is_placeholder_digest("asia-east1-docker.pkg.dev/project/repo/api@sha256:" + "ab" * 32)
    assert not is_placeholder_digest("sha256:" + "0123456789abcdef" * 4)
    assert not is_placeholder_digest("sha256:not-a-digest")


def test_dry_run_record_preserves_required_desired_state_without_attesting_it() -> None:
    record = json.loads(DRY_RUN_PATH.read_text(encoding="utf-8"))
    desired = record["desired_state_not_observed"]
    assert desired["data_platform_before_application"] is True
    assert desired["isolated_database_bucket_tenant_iam"] is True
    assert desired["migration_e2e_worker_scheduler_backup_restore_rollback"] is True
    assert desired["external_sources_expected_enabled"] == []
    assert desired["provider_credentials_present"] is False
    assert desired["public_egress"] == "default-deny"
    assert desired["failure_retention_ttl_hours"] == 24
