"""Admission tests for the single authoritative runtime release check.

The old check accepted any `--lease` string that matched an identifier regex,
so these tests exist mainly to prove that is gone: a release is admitted only
by a Supervisor-issued lease that is signed, current, unconsumed, and bound to
this exact task, SHA, manifest, environment, and action, *and* by a staged gate
registry that admits the requested environment.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from delivery_toolchain.release.check_runtime_admission import (
    main,
    registry_admission_errors,
)
from delivery_toolchain.release.release_lease import (
    STATE_CONSUMED,
    STATE_ISSUED,
    LeaseStateStore,
    build_lease,
    generate_keypair,
    sign_lease,
)
from delivery_toolchain.release.release_manifest import compute_manifest_digest

SHA = "e" * 40
TASK_ID = "SINGLE-RUNTIME-RELEASE-0D1603CF"
RELEASE_ID = "odp-20260824-001"


def build_manifest(candidate_sha: str = SHA) -> dict:
    manifest = {
        "schema_version": 1,
        "release_id": RELEASE_ID,
        "candidate_sha": candidate_sha,
        "components": {
            "api": {"image": "ghcr.io/example/api@sha256:" + "1" * 64},
        },
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
    *,
    candidate_sha: str = SHA,
    manifest_digest: str | None = None,
    stage: str = "candidate-built",
    environment: str = "dev",
    admission_target: str = "dev",
) -> dict:
    return {
        "schema_version": "2.0.0",
        "release": {
            "candidate_sha": candidate_sha,
            "candidate_ref": "origin/dev",
            "manifest_ref": "docs/evidence/gates/RELEASE_MANIFEST.json",
            "manifest_digest": manifest_digest or build_manifest(candidate_sha)["manifest_digest"],
            "stage": stage,
            "environment": environment,
            "admission_target": admission_target,
            "decision": "go",
        },
        "gates": [
            {
                "id": f"gate-{index}",
                "status": "passed",
                "release_sha": candidate_sha,
                "stage": stage,
                "environment": environment,
                "admission_target": admission_target,
                "receipts": [
                    {
                        "receipt_id": f"receipt-{index}",
                        "release_sha": candidate_sha,
                        "result": "pass",
                    }
                ],
            }
            for index in range(7)
        ],
    }


@pytest.fixture
def release(tmp_path: Path) -> dict:
    """A fully valid dev release: keys, registry, manifest, and an issued lease."""

    private_pem, public_pem = generate_keypair()
    private_key = load_pem_private_key(private_pem, password=None)

    public_key_path = tmp_path / "lease.pub"
    public_key_path.write_bytes(public_pem)

    manifest = build_manifest()
    manifest_path = tmp_path / "RELEASE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    registry = build_registry(manifest_digest=manifest["manifest_digest"])
    registry_path = tmp_path / "RELEASE_GATE_REGISTRY.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    state_dir = tmp_path / "release-leases"
    store = LeaseStateStore(state_dir)

    lease = build_lease(
        task_id=TASK_ID,
        release_id=RELEASE_ID,
        candidate_sha=SHA,
        manifest_digest=manifest["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=private_key,
    )
    store.record_issued(lease)
    lease_path = tmp_path / "lease.json"
    lease_path.write_text(json.dumps(lease), encoding="utf-8")

    return {
        "tmp_path": tmp_path,
        "private_key": private_key,
        "public_key_path": public_key_path,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "registry": registry,
        "registry_path": registry_path,
        "state_dir": state_dir,
        "store": store,
        "lease": lease,
        "lease_path": lease_path,
        "receipt_path": tmp_path / "receipt.json",
    }


def run_admission(release: dict, **overrides) -> tuple[int, dict]:
    argv = [
        "--sha",
        overrides.get("sha", SHA),
        "--environment",
        overrides.get("environment", "dev"),
        "--task-id",
        overrides.get("task_id", TASK_ID),
        "--lease-file",
        str(overrides.get("lease_file", release["lease_path"])),
        "--lease-state-dir",
        str(overrides.get("state_dir", release["state_dir"])),
        "--public-key-file",
        str(release["public_key_path"]),
        "--registry",
        str(overrides.get("registry_path", release["registry_path"])),
        "--manifest",
        str(overrides.get("manifest_path", release["manifest_path"])),
        "--receipt",
        str(release["receipt_path"]),
    ]
    if "action" in overrides:
        argv += ["--action", overrides["action"]]
    code = main(argv)
    receipt = json.loads(release["receipt_path"].read_text(encoding="utf-8"))
    return code, receipt


def rewrite(path: Path, mutate) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------
# End-to-end admission through the CLI
# --------------------------------------------------------------------------


def test_a_valid_lease_and_go_registry_admits_and_consumes(release) -> None:
    code, receipt = run_admission(release)
    assert code == 0
    assert receipt["admitted"] is True
    assert receipt["consumed_at"] is not None
    assert release["store"].get(release["lease"]["lease_id"])["state"] == STATE_CONSUMED


def test_the_same_lease_cannot_deploy_twice(release) -> None:
    assert run_admission(release)[0] == 0
    code, receipt = run_admission(release)
    assert code == 1
    assert receipt["admitted"] is False
    assert any("already used, or revoked" in error for error in receipt["errors"])


def test_an_invented_lease_is_refused(release) -> None:
    """The exact attack the shape-only check could not stop."""

    forged = dict(release["lease"])
    forged["lease_id"] = "lease-" + "9" * 32
    forged["signature"] = {
        "algorithm": "ed25519",
        "key_id": release["lease"]["signature"]["key_id"],
        "value": "00" * 64,
    }
    forged_path = release["tmp_path"] / "forged.json"
    forged_path.write_text(json.dumps(forged), encoding="utf-8")

    code, receipt = run_admission(release, lease_file=forged_path)
    assert code == 1
    assert any("signature does not verify" in error for error in receipt["errors"])


def test_a_lease_signed_by_a_different_key_is_refused(release) -> None:
    other_private_pem, _ = generate_keypair()
    other_key = load_pem_private_key(other_private_pem, password=None)
    lease = dict(release["lease"])
    lease["signature"] = sign_lease(lease, private_key=other_key)
    path = release["tmp_path"] / "other-key.json"
    path.write_text(json.dumps(lease), encoding="utf-8")

    code, receipt = run_admission(release, lease_file=path)
    assert code == 1
    assert any("not issued by the configured verification key" in e for e in receipt["errors"])


def test_a_dev_lease_cannot_deploy_staging(release) -> None:
    def promote_to_staging(payload: dict) -> None:
        staged = {"stage": "dev-verified", "admission_target": "staging"}
        payload["release"].update(staged)
        for gate in payload["gates"]:
            gate.update(staged)

    rewrite(release["registry_path"], promote_to_staging)
    code, receipt = run_admission(release, environment="staging")
    assert code == 1
    assert any("lease.target_environment" in error for error in receipt["errors"])
    assert release["store"].get(release["lease"]["lease_id"])["state"] == STATE_ISSUED


def test_a_lease_for_another_task_is_refused(release) -> None:
    code, receipt = run_admission(release, task_id="SOME-OTHER-TASK-001")
    assert code == 1
    assert any("lease.task_id" in error for error in receipt["errors"])


def test_a_lease_for_another_action_is_refused(release) -> None:
    code, receipt = run_admission(release, action="destroy")
    assert code == 1
    assert any("lease.allowed_action" in error for error in receipt["errors"])


def test_an_expired_lease_is_refused(release) -> None:
    stale = build_lease(
        task_id=TASK_ID,
        release_id=RELEASE_ID,
        candidate_sha=SHA,
        manifest_digest=release["manifest"]["manifest_digest"],
        target_environment="dev",
        allowed_action="deploy",
        private_key=release["private_key"],
        ttl_seconds=60,
        issued_at=datetime.now(UTC) - timedelta(hours=2),
    )
    release["store"].record_issued(stale)
    path = release["tmp_path"] / "stale.json"
    path.write_text(json.dumps(stale), encoding="utf-8")

    code, receipt = run_admission(release, lease_file=path)
    assert code == 1
    assert any("lease expired at" in error for error in receipt["errors"])


def test_a_revoked_lease_is_refused(release) -> None:
    release["store"].revoke(release["lease"], reason="candidate withdrawn")
    code, receipt = run_admission(release)
    assert code == 1
    assert any("'revoked'" in error for error in receipt["errors"])


def test_a_no_go_registry_does_not_consume_the_lease(release) -> None:
    rewrite(release["registry_path"], lambda payload: payload["release"].update({"decision": "no-go"}))
    code, receipt = run_admission(release)
    assert code == 1
    assert any("expected 'go'" in error for error in receipt["errors"])
    assert release["store"].get(release["lease"]["lease_id"])["state"] == STATE_ISSUED


def test_a_manifest_that_disagrees_with_the_registry_blocks_admission(release) -> None:
    rewrite(
        release["registry_path"],
        lambda payload: payload["release"].update({"manifest_digest": "sha256:" + "f" * 64}),
    )
    code, receipt = run_admission(release)
    assert code == 1
    assert any("manifest_digest" in error for error in receipt["errors"])


def test_a_runner_local_state_directory_is_refused(release) -> None:
    """Consuming a lease against a directory the Supervisor cannot see is not consuming it."""

    code, receipt = run_admission(release, state_dir=release["tmp_path"] / "not-created")
    assert code == 1
    assert any("refusing to create a throwaway store" in error for error in receipt["errors"])
    assert not (release["tmp_path"] / "not-created").exists()


def test_a_missing_lease_file_fails_closed(release) -> None:
    code, receipt = run_admission(release, lease_file=release["tmp_path"] / "absent.json")
    assert code == 1
    assert receipt["admitted"] is False
    assert any("lease file does not exist" in error for error in receipt["errors"])


def test_the_receipt_is_written_even_when_admission_is_blocked(release) -> None:
    rewrite(release["registry_path"], lambda payload: payload["release"].update({"decision": "no-go"}))
    code, receipt = run_admission(release)
    assert code == 1
    assert release["receipt_path"].exists()
    assert receipt["verifier"].endswith("check_runtime_admission.py")
    assert release["lease"]["nonce"] not in json.dumps(receipt)
    assert release["lease"]["signature"]["value"] not in json.dumps(receipt)


# --------------------------------------------------------------------------
# Staged gate registry admission
# --------------------------------------------------------------------------


def kwargs(**overrides) -> dict:
    base = {"release_sha": SHA, "environment": "dev"}
    base.update(overrides)
    return base


def test_a_staged_go_registry_admits_its_own_target() -> None:
    assert registry_admission_errors(build_registry(), **kwargs()) == []


def test_staging_is_admitted_by_the_dev_verified_boundary() -> None:
    registry = build_registry(
        stage="dev-verified", environment="dev", admission_target="staging"
    )
    assert registry_admission_errors(registry, **kwargs(environment="staging")) == []


def test_a_dev_stage_registry_does_not_admit_staging() -> None:
    errors = registry_admission_errors(build_registry(), **kwargs(environment="staging"))
    assert any("registry admits 'dev', not 'staging'" in error for error in errors)


def test_a_stage_that_contradicts_its_admission_target_is_blocked() -> None:
    registry = build_registry(stage="candidate-built", admission_target="staging")
    errors = registry_admission_errors(registry, **kwargs(environment="staging"))
    assert any("does not match stage 'candidate-built'" in error for error in errors)


def test_a_legacy_v1_registry_has_no_admission_boundary() -> None:
    registry = build_registry()
    registry["schema_version"] = "1.0.0"
    errors = registry_admission_errors(registry, **kwargs())
    assert any("has no admission boundary" in error for error in errors)


def test_no_go_is_blocked_even_when_all_receipts_exist() -> None:
    registry = build_registry()
    registry["release"]["decision"] = "no-go"
    errors = registry_admission_errors(registry, **kwargs())
    assert any("expected 'go'" in error for error in errors)


def test_production_is_admitted_when_staging_verified() -> None:
    registry = build_registry(
        stage="staging-verified", environment="staging", admission_target="production"
    )
    for gate in registry["gates"]:
        gate["stage"] = "staging-verified"
        gate["environment"] = "staging"
        gate["admission_target"] = "production"
    errors = registry_admission_errors(registry, **kwargs(environment="production"))
    assert errors == []


def test_invalid_environment_is_rejected() -> None:
    registry = build_registry()
    errors = registry_admission_errors(registry, **kwargs(environment="sandbox"))
    assert "environment must be one of ['dev', 'staging', 'production']" in errors


def test_gate_count_must_equal_seven() -> None:
    registry = build_registry()
    registry["gates"].pop()
    errors = registry_admission_errors(registry, **kwargs())
    assert "registry must contain exactly seven gates" in errors


def test_a_gate_at_this_boundary_must_be_cleared() -> None:
    registry = build_registry()
    registry["gates"][1]["status"] = "failed"
    errors = registry_admission_errors(registry, **kwargs())
    assert any("gate-1 status is 'failed'" in error for error in errors)


def test_a_gate_at_another_boundary_does_not_block_this_one() -> None:
    """The break in the Gate 0-6 circularity: staging evidence is not a dev prerequisite."""

    registry = build_registry()
    registry["gates"][6].update(
        {
            "status": "not-started",
            "stage": "staging-verified",
            "environment": "staging",
            "admission_target": "production",
            "receipts": [],
        }
    )
    assert registry_admission_errors(registry, **kwargs()) == []


def test_a_registry_with_no_gate_for_this_environment_admits_nothing() -> None:
    registry = build_registry()
    for gate in registry["gates"]:
        gate["admission_target"] = "production"
    errors = registry_admission_errors(registry, **kwargs())
    assert any("the registry admits nothing for this environment" in error for error in errors)


def test_a_stale_receipt_is_not_evidence() -> None:
    registry = build_registry()
    registry["gates"][0]["receipts"][0]["release_sha"] = "f" * 40
    errors = registry_admission_errors(registry, **kwargs())
    assert any("no passing receipt bound to candidate_sha" in error for error in errors)


def test_a_failing_receipt_does_not_clear_a_gate() -> None:
    registry = build_registry()
    registry["gates"][0]["receipts"][0]["result"] = "fail"
    errors = registry_admission_errors(registry, **kwargs())
    assert any("no passing receipt bound to candidate_sha" in error for error in errors)


def test_missing_receipt_is_blocked() -> None:
    registry = build_registry()
    registry["gates"][0]["receipts"] = []
    errors = registry_admission_errors(registry, **kwargs())
    assert "gate-0 has no release receipt" in errors


def test_a_not_applicable_gate_needs_no_receipt() -> None:
    registry = build_registry()
    registry["gates"][0].update({"status": "not-applicable", "receipts": []})
    assert registry_admission_errors(registry, **kwargs()) == []


def test_gate_release_sha_mismatch_is_blocked() -> None:
    registry = build_registry()
    registry["gates"][0]["release_sha"] = "f" * 40
    errors = registry_admission_errors(registry, **kwargs())
    assert "gate-0 release_sha does not match candidate_sha" in errors


def test_sha_mismatch_is_blocked() -> None:
    errors = registry_admission_errors(build_registry(), **kwargs(release_sha="f" * 40))
    assert any("candidate_sha" in error for error in errors)


def test_invalid_environment_is_blocked() -> None:
    errors = registry_admission_errors(build_registry(), **kwargs(environment="wherever"))
    assert "environment must be one of ['dev', 'staging', 'production']" in errors


# --------------------------------------------------------------------------
# Real-git ancestry: evidence-only descendants stay admissible
# --------------------------------------------------------------------------


def make_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def run_git(*args: str) -> str:
        res = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True
        )
        return res.stdout.strip()

    run_git("init")
    run_git("config", "user.email", "test@example.com")
    run_git("config", "user.name", "Test")
    return repo, run_git


def test_candidate_ancestry_real_git_evidence_only_is_admitted(tmp_path: Path) -> None:
    repo, run_git = make_repo(tmp_path)
    (repo / "docs" / "evidence").mkdir(parents=True)
    (repo / "docs" / "evidence" / "gate.md").write_text("initial evidence\n")
    run_git("add", ".")
    run_git("commit", "-m", "candidate commit")
    candidate_sha = run_git("rev-parse", "HEAD")

    (repo / "docs" / "evidence" / "gate2.md").write_text("extra evidence\n")
    run_git("add", ".")
    run_git("commit", "-m", "evidence commit")
    release_sha = run_git("rev-parse", "HEAD")

    registry = build_registry(candidate_sha=candidate_sha)
    assert registry_admission_errors(
        registry, release_sha=release_sha, environment="dev", root=repo
    ) == []


def test_candidate_ancestry_real_git_non_evidence_change_blocked(tmp_path: Path) -> None:
    repo, run_git = make_repo(tmp_path)
    (repo / "main.py").write_text("print('v1')\n")
    run_git("add", ".")
    run_git("commit", "-m", "candidate commit")
    candidate_sha = run_git("rev-parse", "HEAD")

    (repo / "main.py").write_text("print('v2')\n")
    run_git("add", ".")
    run_git("commit", "-m", "product commit")
    release_sha = run_git("rev-parse", "HEAD")

    registry = build_registry(candidate_sha=candidate_sha)
    errors = registry_admission_errors(
        registry, release_sha=release_sha, environment="dev", root=repo
    )
    assert any("intervening commits touch non-evidence paths" in error for error in errors)
    assert any("main.py" in error for error in errors)


def test_candidate_ancestry_real_git_not_an_ancestor_blocked(tmp_path: Path) -> None:
    repo, run_git = make_repo(tmp_path)
    (repo / "a.txt").write_text("a\n")
    run_git("add", ".")
    run_git("commit", "-m", "commit a")
    sha_a = run_git("rev-parse", "HEAD")

    run_git("checkout", "--orphan", "branch-b")
    run_git("rm", "-rf", ".")
    (repo / "b.txt").write_text("b\n")
    run_git("add", ".")
    run_git("commit", "-m", "commit b")
    sha_b = run_git("rev-parse", "HEAD")

    registry = build_registry(candidate_sha=sha_a)
    errors = registry_admission_errors(
        registry, release_sha=sha_b, environment="dev", root=repo
    )
    assert any("not an ancestor of expected SHA" in error for error in errors)
