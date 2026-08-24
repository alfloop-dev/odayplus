"""Tests for the Supervisor-side release lease issuer.

Issuance is where a lease stops being a document and becomes an authorisation,
so the interesting cases are the refusals: an unfinished dependency, a
dependency the Supervisor cannot even resolve, a registry that has not reached
the stage admitting the requested environment, and a manifest that does not
describe the candidate the registry names.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from release_lease import dependency_errors, issue_release_lease, main

from delivery_toolchain.release.release_lease import (
    STATE_ISSUED,
    STATE_REVOKED,
    LeaseIssuanceError,
    LeaseStateStore,
    generate_keypair,
    verify_lease,
)
from delivery_toolchain.release.release_manifest import compute_manifest_digest

CANDIDATE_SHA = "e" * 40
TASK_ID = "ODP-RELEASE-DEPLOY-001"
DEPENDENCY_ID = "ODP-RELEASE-MANIFEST-GATES-001"
RELEASE_ID = "odp-20260824-001"


def build_manifest(candidate_sha: str = CANDIDATE_SHA) -> dict:
    manifest = {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "candidate_sha": candidate_sha,
        "components": {"api": {"image": "ghcr.io/example/api@sha256:" + "1" * 64}},
        "migration_digest": "sha256:" + "a" * 64,
        "data_contract_digest": "sha256:" + "b" * 64,
        "source_policy_digest": "sha256:" + "c" * 64,
        "external_sources_expected_enabled": [],
        "sbom_refs": ["oci://ghcr.io/example/sbom@sha256:" + "7" * 64],
        "signature_refs": ["oci://ghcr.io/example/sig@sha256:" + "8" * 64],
        "created_at": "2026-08-24T12:00:00+00:00",
        "created_by_workflow": "github://example/actions/runtime-release.yml/run-1",
    }
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    return manifest


def build_registry(
    manifest: dict,
    *,
    stage: str = "candidate-built",
    environment: str = "dev",
    admission_target: str = "dev",
    decision: str = "go",
) -> dict:
    return {
        "schema_version": "2.0.0",
        "release": {
            "candidate_sha": manifest["candidate_sha"],
            "manifest_digest": manifest["manifest_digest"],
            "stage": stage,
            "environment": environment,
            "admission_target": admission_target,
            "decision": decision,
        },
        "gates": [
            {
                "id": f"gate-{index}",
                "status": "passed",
                "release_sha": manifest["candidate_sha"],
                "stage": stage,
                "environment": environment,
                "admission_target": admission_target,
                "receipts": [
                    {
                        "receipt_id": f"receipt-{index}",
                        "release_sha": manifest["candidate_sha"],
                        "result": "pass",
                    }
                ],
            }
            for index in range(7)
        ],
    }


def build_status(*, dependency_status: str = "done", depends_on: list[str] | None = None) -> dict:
    return {
        "tasks": [
            {"id": DEPENDENCY_ID, "status": dependency_status},
            {
                "id": TASK_ID,
                "status": "in_progress",
                "depends_on": [DEPENDENCY_ID] if depends_on is None else depends_on,
            },
        ]
    }


@pytest.fixture
def supervisor(tmp_path: Path) -> dict:
    private_pem, public_pem = generate_keypair()
    private_key_path = tmp_path / "lease.key"
    private_key_path.write_bytes(private_pem)

    manifest = build_manifest()
    manifest_path = tmp_path / "RELEASE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    registry = build_registry(manifest)
    registry_path = tmp_path / "RELEASE_GATE_REGISTRY.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    status_path = tmp_path / "ai-status.json"
    status_path.write_text(json.dumps(build_status()), encoding="utf-8")

    from delivery_toolchain.release.release_lease import load_private_key

    return {
        "tmp_path": tmp_path,
        "private_key": load_private_key(key_path=private_key_path),
        "private_key_path": private_key_path,
        "public_key": load_pem_public_key(public_pem),
        "manifest": manifest,
        "manifest_path": manifest_path,
        "registry": registry,
        "registry_path": registry_path,
        "status": build_status(),
        "status_path": status_path,
        "state_dir": tmp_path / "release-leases",
    }


def issue(supervisor: dict, **overrides) -> dict:
    store = overrides.pop("state_store", None) or LeaseStateStore(supervisor["state_dir"])
    kwargs = {
        "task_id": TASK_ID,
        "target_environment": "dev",
        "status": supervisor["status"],
        "registry": supervisor["registry"],
        "manifest": supervisor["manifest"],
        "manifest_errors": [],
        "private_key": supervisor["private_key"],
        "state_store": store,
        "root": supervisor["tmp_path"],
    }
    kwargs.update(overrides)
    return issue_release_lease(**kwargs)


# --------------------------------------------------------------------------
# Dependency preconditions
# --------------------------------------------------------------------------


def test_a_satisfied_dependency_graph_is_no_blocker() -> None:
    assert dependency_errors(build_status(), TASK_ID) == []


def test_an_unfinished_dependency_blocks_issuance() -> None:
    errors = dependency_errors(build_status(dependency_status="in_progress"), TASK_ID)
    assert errors == [
        f"dependency {DEPENDENCY_ID} of {TASK_ID} is 'in_progress', expected 'done'"
    ]


def test_an_unresolvable_dependency_is_a_blocker_not_a_pass() -> None:
    errors = dependency_errors(build_status(depends_on=["ODP-GHOST-001"]), TASK_ID)
    assert any("cannot be resolved" in error for error in errors)


def test_a_dependency_completed_before_archival_still_resolves(tmp_path: Path) -> None:
    archive = tmp_path / "tasks"
    archive.mkdir()
    (archive / "odp-archived-001.json").write_text(
        json.dumps({"id": "ODP-ARCHIVED-001", "status": "done"}), encoding="utf-8"
    )
    status = build_status(depends_on=["ODP-ARCHIVED-001"])
    assert dependency_errors(status, TASK_ID, archive_dir=archive) == []


def test_an_archived_dependency_that_never_finished_still_blocks(tmp_path: Path) -> None:
    archive = tmp_path / "tasks"
    archive.mkdir()
    (archive / "odp-archived-001.json").write_text(
        json.dumps({"id": "ODP-ARCHIVED-001", "status": "blocked"}), encoding="utf-8"
    )
    status = build_status(depends_on=["ODP-ARCHIVED-001"])
    errors = dependency_errors(status, TASK_ID, archive_dir=archive)
    assert any("is 'blocked', expected 'done'" in error for error in errors)


def test_an_unknown_task_cannot_request_a_lease() -> None:
    errors = dependency_errors(build_status(), "ODP-NOT-A-TASK-001")
    assert errors == [
        "task ODP-NOT-A-TASK-001 is not present in the Supervisor status document"
    ]


def test_a_task_with_no_dependencies_is_not_blocked_by_the_graph() -> None:
    assert dependency_errors(build_status(depends_on=[]), TASK_ID) == []


# --------------------------------------------------------------------------
# Issuance
# --------------------------------------------------------------------------


def test_an_issued_lease_verifies_against_the_public_key(supervisor) -> None:
    store = LeaseStateStore(supervisor["state_dir"])
    lease = issue(supervisor, state_store=store)

    assert lease["task_id"] == TASK_ID
    assert lease["target_environment"] == "dev"
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED
    assert verify_lease(
        lease,
        public_key=supervisor["public_key"],
        state_store=store,
        expected_task_id=TASK_ID,
        expected_candidate_sha=CANDIDATE_SHA,
        expected_manifest_digest=supervisor["manifest"]["manifest_digest"],
        expected_environment="dev",
    ) == []


def test_the_release_binding_is_derived_from_committed_truth(supervisor) -> None:
    """A caller picks the task and environment; it never asserts the artifact."""

    lease = issue(supervisor)
    assert lease["candidate_sha"] == supervisor["registry"]["release"]["candidate_sha"]
    assert lease["manifest_digest"] == supervisor["manifest"]["manifest_digest"]
    assert lease["release_id"] == supervisor["manifest"]["release_id"]


def test_every_lease_is_unique(supervisor) -> None:
    store = LeaseStateStore(supervisor["state_dir"])
    first = issue(supervisor, state_store=store)
    second = issue(supervisor, state_store=store)
    assert first["lease_id"] != second["lease_id"]
    assert first["nonce"] != second["nonce"]


def test_an_unfinished_dependency_yields_no_lease(supervisor) -> None:
    with pytest.raises(LeaseIssuanceError) as excinfo:
        issue(supervisor, status=build_status(dependency_status="in_progress"))
    assert any("expected 'done'" in error for error in excinfo.value.errors)
    assert not list(LeaseStateStore(supervisor["state_dir"]).directory.glob("*.json"))


def test_a_no_go_registry_yields_no_lease(supervisor) -> None:
    registry = build_registry(supervisor["manifest"], decision="no-go")
    with pytest.raises(LeaseIssuanceError) as excinfo:
        issue(supervisor, registry=registry)
    assert any("expected 'go'" in error for error in excinfo.value.errors)


def test_a_manifest_the_registry_does_not_name_yields_no_lease(supervisor) -> None:
    with pytest.raises(LeaseIssuanceError) as excinfo:
        issue(supervisor, manifest_errors=["manifest.candidate_sha does not match"])
    assert "manifest.candidate_sha does not match" in excinfo.value.errors


def test_staging_needs_the_dev_verified_stage(supervisor) -> None:
    with pytest.raises(LeaseIssuanceError) as excinfo:
        issue(supervisor, target_environment="staging")
    assert any("registry admits 'dev', not 'staging'" in e for e in excinfo.value.errors)

    promoted = build_registry(
        supervisor["manifest"], stage="dev-verified", admission_target="staging"
    )
    lease = issue(supervisor, target_environment="staging", registry=promoted)
    assert lease["target_environment"] == "staging"


def test_production_is_refused_until_staging_is_verified(supervisor) -> None:
    with pytest.raises(LeaseIssuanceError) as excinfo:
        issue(supervisor, target_environment="production")
    assert any("registry admits 'dev', not 'production'" in e for e in excinfo.value.errors)

    promoted = build_registry(
        supervisor["manifest"],
        stage="staging-verified",
        environment="staging",
        admission_target="production",
    )
    lease = issue(supervisor, target_environment="production", registry=promoted)
    assert lease["target_environment"] == "production"


def test_an_unknown_environment_is_refused(supervisor) -> None:
    with pytest.raises(LeaseIssuanceError) as excinfo:
        issue(supervisor, target_environment="wherever")
    assert any("target_environment 'wherever'" in error for error in excinfo.value.errors)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def cli_issue_args(supervisor: dict, output: Path, **extra) -> list[str]:
    argv = [
        "issue",
        "--task-id",
        TASK_ID,
        "--environment",
        extra.get("environment", "dev"),
        "--status",
        str(supervisor["status_path"]),
        "--registry",
        str(supervisor["registry_path"]),
        "--manifest",
        str(supervisor["manifest_path"]),
        "--state-dir",
        str(supervisor["state_dir"]),
        "--private-key-file",
        str(supervisor["private_key_path"]),
        "--output",
        str(output),
    ]
    return argv


def test_cli_issue_writes_a_lease_and_records_it(supervisor, monkeypatch) -> None:
    monkeypatch.chdir(supervisor["tmp_path"])
    output = supervisor["tmp_path"] / "lease.json"
    assert main(cli_issue_args(supervisor, output)) == 0

    lease = json.loads(output.read_text(encoding="utf-8"))
    record = LeaseStateStore(supervisor["state_dir"], require_existing=True).get(lease["lease_id"])
    assert record["state"] == STATE_ISSUED
    assert record["issued_by"] == ".orchestrator/release_lease.py"


def test_cli_issue_refuses_a_blocked_release(supervisor, capsys) -> None:
    supervisor["status_path"].write_text(
        json.dumps(build_status(dependency_status="blocked")), encoding="utf-8"
    )
    output = supervisor["tmp_path"] / "lease.json"
    assert main(cli_issue_args(supervisor, output)) == 1
    assert not output.exists()
    assert "release lease issuance blocked" in capsys.readouterr().err


def test_cli_revoke_then_show_reports_the_state_without_the_credential(
    supervisor, capsys
) -> None:
    output = supervisor["tmp_path"] / "lease.json"
    assert main(cli_issue_args(supervisor, output)) == 0
    lease = json.loads(output.read_text(encoding="utf-8"))
    capsys.readouterr()

    common = ["--state-dir", str(supervisor["state_dir"])]
    assert main(["revoke", lease["lease_id"], "--reason", "candidate withdrawn", *common]) == 0
    capsys.readouterr()

    assert main(["show", lease["lease_id"], *common]) == 1
    printed = capsys.readouterr().out
    assert STATE_REVOKED in printed
    assert lease["nonce"] not in printed
    assert lease["signature"]["value"] not in printed


def test_cli_show_fails_closed_for_an_unknown_lease(supervisor, capsys) -> None:
    LeaseStateStore(supervisor["state_dir"])
    assert (
        main(["show", "lease-" + "0" * 32, "--state-dir", str(supervisor["state_dir"])]) == 1
    )
    assert "is not in the durable state store" in capsys.readouterr().err
