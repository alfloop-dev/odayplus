"""Contract tests for the unified release receipt envelope."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from delivery_toolchain.release.release_receipts import (
    RUNTIME_RELEASE_ARTIFACT_ALLOWLIST,
    ReceiptValidationError,
    build_receipt,
    read_receipt,
    validate_artifact_allowlist,
    validate_receipt,
    write_receipt,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/deploy-dev.yml"
DEPLOY_SCRIPT = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"
SHA = "a" * 40
MANIFEST_DIGEST = "sha256:" + "b" * 64
ARTIFACT = ".odp_data/deployment/cloud-run-smoke.json"


def receipt(**overrides: object) -> dict:
    values: dict[str, object] = {
        "receipt_id": "ODP-RECEIPT-001",
        "receipt_kind": "verification",
        "release_id": "odp-20260824-001",
        "manifest_ref": "docs/evidence/gates/RELEASE_MANIFEST.json",
        "manifest_digest": MANIFEST_DIGEST,
        "release_sha": SHA,
        "environment": "staging",
        "stage": "staging-verified",
        "result": "pass",
        "recorded_by": "Codex",
        "artifacts": [ARTIFACT],
        "details": {"check": "health", "secret_value": "opaque-provider-token"},
        "secret_values": ["opaque-provider-token"],
    }
    values.update(overrides)
    return build_receipt(**values)


def test_receipt_binds_manifest_identity_and_redacts_nested_values() -> None:
    generated = receipt()

    assert generated["release_id"] == "odp-20260824-001"
    assert generated["candidate_sha"] == SHA
    assert generated["release_sha"] == SHA
    assert generated["manifest_digest"] == MANIFEST_DIGEST
    assert generated["environment"] == "staging"
    assert generated["stage"] == "staging-verified"
    assert generated["secret_values_redacted"] is True
    assert generated["details"]["secret_value"] == "[REDACTED]"
    assert validate_receipt(
        generated,
        expected_release_id="odp-20260824-001",
        expected_candidate_sha=SHA,
        expected_manifest_digest=MANIFEST_DIGEST,
    ) == []
    assert "opaque-provider-token" not in json.dumps(generated)


@pytest.mark.parametrize(
    ("environment", "stage"),
    (("dev", "candidate-built"), ("staging", "staging-verified"), ("prod", "prod-admitted")),
)
def test_all_release_environments_are_supported_with_stage_binding(
    environment: str, stage: str
) -> None:
    generated = receipt(environment=environment, stage=stage)
    assert generated["environment"] == environment
    assert validate_receipt(generated) == []


def test_production_alias_is_canonicalized_to_prod() -> None:
    generated = receipt(environment="production", stage="prod-switched")
    assert generated["environment"] == "prod"
    assert validate_receipt(generated) == []


def test_stage_environment_mismatch_fails_closed() -> None:
    generated = receipt()
    generated["environment"] = "dev"
    errors = validate_receipt(generated)
    assert any("requires environment 'staging'" in error for error in errors)


def test_unredacted_sensitive_value_is_rejected() -> None:
    generated = receipt()
    generated["details"]["api_token"] = "plaintext-token"
    errors = validate_receipt(generated)
    assert any("unredacted sensitive value" in error for error in errors)


@pytest.mark.parametrize("kind", ("cleanup", "rollback"))
def test_cleanup_and_rollback_are_first_class_receipt_kinds(kind: str) -> None:
    generated = receipt(receipt_id=f"ODP-{kind.upper()}-001", receipt_kind=kind)
    assert generated["receipt_kind"] == kind
    assert validate_receipt(generated) == []


def test_allowlist_is_literal_and_rejects_raw_cloud_run_dumps() -> None:
    assert validate_artifact_allowlist([ARTIFACT]) == []
    errors = validate_artifact_allowlist(
        [".odp_data/deployment/cloud-run-jobs/worker-job.json", ".odp_data/deployment/*.json"]
    )
    assert any("unredacted Cloud Run dump" in error for error in errors)
    assert any("must be literal, not a glob" in error for error in errors)


def test_allowlist_matches_workflow_uploads_and_script_producers() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    upload_steps = [
        step
        for step in workflow["jobs"]["deploy"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    validation_steps = [
        step
        for step in upload_steps
        if "validation" in str(step["with"]["name"])
    ]
    assert len(validation_steps) == 1
    uploaded = {
        line.strip().replace("${{ github.run_id }}", "{run_id}")
        for line in validation_steps[0]["with"]["path"].splitlines()
        if line.strip()
    }
    assert uploaded == set(RUNTIME_RELEASE_ARTIFACT_ALLOWLIST)

    # The allowlist governs deployment reports. Any *other* upload the deploy
    # job makes -- the environment binding receipt, for one -- must come from the
    # release receipt staging root, so it cannot smuggle out a raw Cloud Run dump
    # under a name the allowlist never had to approve.
    #
    # ODP-RELEASE-WORKFLOW-DISPATCH-PARSER-001: that root used to be
    # `${{ runner.temp }}/`, which cost the workflow its `jobs.build.env` entry
    # and with it the ability to compile at all. The separation it bought is the
    # part worth keeping, and `.odp_data/release/` keeps it: `capture_job_proof`
    # writes its raw `describe` dumps under `.odp_data/deployment/`, which is a
    # sibling of the staging root, never a parent of it.
    receipt_dir = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["env"][
        "RELEASE_RECEIPT_DIR"
    ].rstrip("/")
    report_dir = ".odp_data/deployment"
    assert not report_dir.startswith(f"{receipt_dir}/"), (
        f"{receipt_dir} contains the Cloud Run report directory; staging receipts "
        "there would re-expose the raw describe dumps this separation exists for"
    )
    for step in upload_steps:
        if step in validation_steps:
            continue
        for line in str(step["with"]["path"]).splitlines():
            entry = line.strip()
            if not entry:
                continue
            assert entry.startswith(f"{receipt_dir}/"), (
                f"{step['with']['name']} publishes {entry} from outside "
                f"{receipt_dir} without an allowlist entry"
            )
            # A literal file, for the same reason the allowlist is literal: a
            # glob publishes whatever else lands in the tree.
            assert not set(entry) & set("*?[]"), f"{entry}: name the file, not a pattern"
            assert "${{" not in entry, f"{entry}: a receipt path needs no expression"

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    job_kinds = set(re.findall(r'^\s*execute_job "([a-z]+)"', script, flags=re.MULTILINE))
    assert {f".odp_data/deployment/cloud-run-jobs/{kind}-validation.json" for kind in job_kinds} <= uploaded
    for variable in ("PREFLIGHT_REPORT", "SMOKE_REPORT", "MIGRATION_COMPAT_REPORT", "LIVE_E2E_REPORT"):
        match = re.search(
            rf'^{variable}="\$\{{{variable}:-([^}}]+)\}}"$', script, flags=re.MULTILINE
        )
        assert match, f"{variable} producer is missing from deploy script"
        assert match.group(1) in uploaded


def test_receipt_write_and_read_validate_the_same_identity(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    generated = receipt()
    write_receipt(path, generated)

    loaded, errors = read_receipt(
        path,
        expected_release_id=generated["release_id"],
        expected_candidate_sha=SHA,
        expected_manifest_digest=MANIFEST_DIGEST,
    )
    assert errors == []
    assert loaded == generated


def test_builder_rejects_unbound_artifact() -> None:
    with pytest.raises(ReceiptValidationError, match="not produced by Runtime Release"):
        receipt(artifacts=[".odp_data/deployment/secret-values.json"])
