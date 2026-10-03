"""Release profile is immutable release scope (ODP-DEV-ADMIN-RELEASE-READINESS-001).

``dev-admin`` narrows what the live gate holds a release to, so it must be
impossible to obtain except by building for it: it is sealed into the manifest
digest at build time, bound to the deploy target at admission (before the lease
is consumed), and absent -- never spelled -- for the complete ``full`` scope so
every existing manifest digest is unchanged.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from apps.api.oday_api import runtime_mode
from delivery_toolchain.release.build_release_handoff import HandoffError, build_handoff
from delivery_toolchain.release.check_runtime_admission import main as admission_main
from delivery_toolchain.release.release_lease import (
    STATE_CONSUMED,
    STATE_ISSUED,
    LeaseStateStore,
    build_lease,
    generate_keypair,
)
from delivery_toolchain.release.release_manifest import (
    RELEASE_PROFILE_DEV_ADMIN,
    RELEASE_PROFILE_ENVIRONMENTS,
    RELEASE_PROFILE_FULL,
    RELEASE_PROFILES,
    build_release_manifest,
    build_release_profile,
    compute_data_contract_digest,
    compute_manifest_digest,
    manifest_release_profile,
    release_profile_errors,
    validate_manifest,
    validate_release_admission,
)

ROOT = Path(__file__).resolve().parents[2]
SHA = "e" * 40
TASK_ID = "ODP-DEV-ADMIN-RELEASE-PROFILE-TEST"
RELEASE_ID = "odp-20261003-001"
DEV_ADMIN = {
    "name": "dev-admin",
    "target_environment": "dev",
    "model_readiness": "not_claimed",
}


def sealed(manifest: dict) -> dict:
    manifest = copy.deepcopy(manifest)
    manifest["manifest_digest"] = compute_manifest_digest(manifest)
    return manifest


def base_manifest(*, release_profile: dict | None = None) -> dict:
    manifest = {
        "schema_version": 2,
        "release_id": RELEASE_ID,
        "candidate_sha": SHA,
        "components": {"api": {"image": "ghcr.io/example/api@sha256:" + "1" * 64}},
        "migration_digest": "sha256:" + "a" * 64,
        "data_contract_digest": "sha256:" + "b" * 64,
        "source_policy_digest": "sha256:" + "c" * 64,
        "external_sources_expected_enabled": [],
        "sbom_refs": ["oci://ghcr.io/example/sbom@sha256:" + "7" * 64],
        "signature_refs": ["oci://ghcr.io/example/sig@sha256:" + "8" * 64],
        "created_at": "2026-10-03T12:00:00+00:00",
        "created_by_workflow": "github://example/actions/runtime-release.yml/run-1",
        "data_snapshot": {
            "id": "snap-test-001",
            "uri": "gs://odayplus-snapshots/masked/snap-test-001.tar.gz",
            "object_generation": 123,
            "content_sha256": "sha256:" + "d" * 64,
            "data_contract_digest": "sha256:" + "b" * 64,
            "masked": True,
        },
        "rollback_release": {
            "release_id": "odp-prev-001",
            "candidate_sha": "0" * 40,
            "manifest_digest": "sha256:" + "e" * 64,
            "components": {
                "api": {"image": "ghcr.io/example/api@sha256:" + "2" * 64},
                "web": {"image": "ghcr.io/example/web@sha256:" + "3" * 64},
            },
            "data_snapshot": {
                "id": "snap-prev-001",
                "uri": "gs://odayplus-snapshots/masked/snap-prev-001.tar.gz",
                "object_generation": 122,
                "content_sha256": "sha256:" + "f" * 64,
                "data_contract_digest": "sha256:" + "b" * 64,
                "masked": True,
            },
        },
    }
    if release_profile is not None:
        manifest["release_profile"] = copy.deepcopy(release_profile)
    return sealed(manifest)


# --------------------------------------------------------------------------
# Building the binding
# --------------------------------------------------------------------------


def test_full_is_the_absence_of_a_binding() -> None:
    assert build_release_profile(RELEASE_PROFILE_FULL, target_environment="production") is None
    assert manifest_release_profile(base_manifest()) == RELEASE_PROFILE_FULL


def test_dev_admin_binds_dev_and_claims_no_model_readiness() -> None:
    assert build_release_profile(RELEASE_PROFILE_DEV_ADMIN, target_environment="dev") == DEV_ADMIN


@pytest.mark.parametrize("environment", ["staging", "production", ""])
def test_dev_admin_cannot_be_built_for_any_other_target(environment: str) -> None:
    with pytest.raises(ValueError, match="may only target"):
        build_release_profile(RELEASE_PROFILE_DEV_ADMIN, target_environment=environment)


@pytest.mark.parametrize("name", ["admin", "dev_admin", "DEV-ADMIN", "", "models-off"])
def test_an_unknown_profile_cannot_be_built(name: str) -> None:
    with pytest.raises(ValueError, match="unknown release profile"):
        build_release_profile(name, target_environment="dev")


# --------------------------------------------------------------------------
# Digest binding and tamper resistance
# --------------------------------------------------------------------------


def test_a_dev_admin_manifest_is_valid_and_has_its_own_digest() -> None:
    full = base_manifest()
    narrowed = base_manifest(release_profile=DEV_ADMIN)

    assert validate_manifest(narrowed, expected_candidate_sha=SHA) == []
    assert manifest_release_profile(narrowed) == RELEASE_PROFILE_DEV_ADMIN
    # The scope is part of the identity a lease is issued against.
    assert narrowed["manifest_digest"] != full["manifest_digest"]


def test_existing_full_manifests_keep_their_digest() -> None:
    manifest = base_manifest()
    assert "release_profile" not in manifest
    assert validate_manifest(manifest) == []


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda m: m.pop("release_profile"), id="profile-stripped"),
        pytest.param(lambda m: m["release_profile"].update(name="full"), id="renamed-full"),
        pytest.param(
            lambda m: m["release_profile"].update(target_environment="production"),
            id="retargeted",
        ),
    ],
)
def test_editing_the_profile_after_sealing_breaks_the_digest(mutate) -> None:
    manifest = base_manifest(release_profile=DEV_ADMIN)
    mutate(manifest)

    errors = validate_manifest(manifest)

    assert "manifest.manifest_digest does not match its canonical immutable payload" in errors


def test_narrowing_a_full_manifest_after_sealing_breaks_the_digest() -> None:
    manifest = base_manifest()
    manifest["release_profile"] = copy.deepcopy(DEV_ADMIN)

    assert "manifest.manifest_digest does not match its canonical immutable payload" in (
        validate_manifest(manifest)
    )


@pytest.mark.parametrize(
    ("profile", "fragment"),
    [
        ({**DEV_ADMIN, "name": "full"}, "must be omitted for the full profile"),
        ({**DEV_ADMIN, "name": "ops-admin"}, "name must be one of"),
        ({**DEV_ADMIN, "target_environment": "staging"}, "target_environment must be one of"),
        ({**DEV_ADMIN, "model_readiness": "claimed"}, "model_readiness must be"),
        ({**DEV_ADMIN, "extra": True}, "must carry exactly"),
        ("dev-admin", "must be an object"),
    ],
)
def test_a_malformed_profile_is_invalid_even_when_resealed(profile, fragment: str) -> None:
    manifest = base_manifest(release_profile=profile if isinstance(profile, dict) else None)
    if not isinstance(profile, dict):
        manifest["release_profile"] = profile
        manifest = sealed(manifest)

    assert any(fragment in error for error in validate_manifest(manifest)), validate_manifest(
        manifest
    )


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_admission_refuses_dev_admin_for_staging_and_production(environment: str) -> None:
    manifest = base_manifest(release_profile=DEV_ADMIN)

    errors = validate_release_admission(manifest, environment=environment)

    assert any(f"cannot be admitted into '{environment}'" in error for error in errors)
    assert release_profile_errors(manifest, environment="dev") == []


def test_profile_vocabularies_agree_across_manifest_runtime_and_gate() -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "check_live_e2e_gate_profile_drift",
        ROOT / "delivery_toolchain/e2e/check_live_e2e_gate.py",
    )
    assert spec and spec.loader
    gate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gate
    spec.loader.exec_module(gate)

    assert set(RELEASE_PROFILES) == set(gate.RELEASE_PROFILE_DEPLOYMENTS)
    assert set(RELEASE_PROFILES) == set(runtime_mode._RELEASE_PROFILE_DEPLOYMENTS)
    for name, environments in RELEASE_PROFILE_ENVIRONMENTS.items():
        assert gate.RELEASE_PROFILE_DEPLOYMENTS[name] == environments
        assert runtime_mode._RELEASE_PROFILE_DEPLOYMENTS[name] == environments
    assert gate.RELEASE_PROFILE_DEPLOYMENTS[RELEASE_PROFILE_FULL] is None


# --------------------------------------------------------------------------
# Build handoff
# --------------------------------------------------------------------------


# A real commit: the handoff dates the manifest from the candidate's own commit.
HANDOFF_SHA = "596b9c9a1788d952811a2bf8d4bba8a4e4d76b12"
HANDOFF_REPO = "asia-east1-docker.pkg.dev/odayplus/oday-plus-dev"


def _ref(name: str, fill: str) -> str:
    return f"{HANDOFF_REPO}/{name}@sha256:{fill * 64}"


def _snapshot(snapshot_id: str, generation: int, fill: str) -> dict:
    return {
        "id": snapshot_id,
        "uri": f"gs://odayplus-snapshots/masked/{snapshot_id}.tar.gz",
        "object_generation": generation,
        "content_sha256": "sha256:" + fill * 64,
        "data_contract_digest": compute_data_contract_digest(root=ROOT),
        "masked": True,
    }


def _previous_release() -> dict:
    prev_sha = "0" * 40
    return build_release_manifest(
        release_id="odp-prev-001",
        candidate_sha=prev_sha,
        components={
            name: {"image": _ref(name, fill)}
            for name, fill in (("api", "a"), ("web", "b"), ("worker", "c"), ("scheduler", "d"))
        },
        sbom_refs=[_ref("api", "5")],
        signature_refs=[_ref("api", "6")],
        created_at="2026-08-25T12:00:00+00:00",
        created_by_workflow=(
            "github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml@" + prev_sha
        ),
        data_snapshot=_snapshot("snap-prev-001", 121, "c"),
        rollback_release={
            "release_id": "odp-older-001",
            "candidate_sha": "9" * 40,
            "manifest_digest": "sha256:" + "8" * 64,
            "components": {
                "api": {"image": _ref("api", "a")},
                "web": {"image": _ref("web", "b")},
            },
            "data_snapshot": _snapshot("snap-older-001", 120, "e"),
        },
        release_status="ready",
        root=ROOT,
    )


def _handoff(**overrides):
    kwargs = {
        "release_sha": HANDOFF_SHA,
        "components": {
            name: _ref(name, fill)
            for name, fill in (("api", "1"), ("web", "2"), ("worker", "3"), ("scheduler", "4"))
        },
        "sbom_refs": [_ref("api", "5")],
        "signature_refs": [_ref("api", "6")],
        "data_snapshot": _snapshot("snap-handoff-001", 123, "7"),
        "rollback_release": _previous_release(),
        "created_at": "2026-08-26T12:00:00+00:00",
        "created_by_workflow": (
            "github://alfloop-dev/odayplus/.github/workflows/deploy-dev.yml@" + HANDOFF_SHA
        ),
    }
    kwargs.update(overrides)
    return build_handoff(**kwargs)


def test_the_build_seals_dev_admin_into_a_dev_manifest() -> None:
    _, manifest = _handoff(target_environment="dev", release_profile="dev-admin")

    assert manifest["release_profile"] == DEV_ADMIN
    assert validate_release_admission(manifest, environment="dev") == []


def test_the_default_build_is_full_and_unchanged() -> None:
    _, default = _handoff(target_environment="dev")
    _, explicit = _handoff(target_environment="dev", release_profile="full")

    assert "release_profile" not in default
    assert default["manifest_digest"] == explicit["manifest_digest"]


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_build_refuses_dev_admin_for_staging_and_production(environment: str) -> None:
    with pytest.raises(HandoffError) as excinfo:
        _handoff(target_environment=environment, release_profile="dev-admin")

    assert any("may only target" in error for error in excinfo.value.errors)


def test_the_build_refuses_an_unknown_profile() -> None:
    with pytest.raises(HandoffError) as excinfo:
        _handoff(target_environment="dev", release_profile="admin-only")

    assert any("unknown release profile" in error for error in excinfo.value.errors)


# --------------------------------------------------------------------------
# Runtime admission (lease-bound) -- refused before the lease is consumed
# --------------------------------------------------------------------------


def _registry(manifest_digest: str, *, stage: str, environment: str, target: str) -> dict:
    return {
        "schema_version": "2.0.0",
        "release": {
            "candidate_sha": SHA,
            "candidate_ref": "origin/dev",
            "manifest_ref": "docs/evidence/gates/RELEASE_MANIFEST.json",
            "manifest_digest": manifest_digest,
            "stage": stage,
            "environment": environment,
            "admission_target": target,
            "decision": "go",
        },
        "gates": [
            {
                "id": f"gate-{index}",
                "status": "passed",
                "release_sha": SHA,
                "stage": stage,
                "environment": environment,
                "admission_target": target,
                "receipts": [
                    {"receipt_id": f"receipt-{index}", "release_sha": SHA, "result": "pass"}
                ],
            }
            for index in range(7)
        ],
    }


def _admit(
    tmp_path: Path, manifest: dict, *, environment: str
) -> tuple[int, dict, dict, LeaseStateStore]:
    private_pem, public_pem = generate_keypair()
    private_key = load_pem_private_key(private_pem, password=None)
    (tmp_path / "lease.pub").write_bytes(public_pem)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    stage, registry_env = (
        ("candidate-built", "dev") if environment == "dev" else ("dev-verified", "dev")
    )
    (tmp_path / "registry.json").write_text(
        json.dumps(
            _registry(
                manifest["manifest_digest"],
                stage=stage,
                environment=registry_env,
                target=environment,
            )
        ),
        encoding="utf-8",
    )
    store = LeaseStateStore(tmp_path / "leases")
    lease = build_lease(
        task_id=TASK_ID,
        release_id=RELEASE_ID,
        candidate_sha=SHA,
        manifest_digest=manifest["manifest_digest"],
        target_environment=environment,
        allowed_action="deploy",
        private_key=private_key,
    )
    store.record_issued(lease)
    (tmp_path / "lease.json").write_text(json.dumps(lease), encoding="utf-8")
    code = admission_main(
        [
            "--sha",
            SHA,
            "--environment",
            environment,
            "--task-id",
            TASK_ID,
            "--lease-file",
            str(tmp_path / "lease.json"),
            "--lease-state-dir",
            str(tmp_path / "leases"),
            "--public-key-file",
            str(tmp_path / "lease.pub"),
            "--registry",
            str(tmp_path / "registry.json"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--manifest-digest",
            manifest["manifest_digest"],
            "--require-manifest-digest",
            "--receipt",
            str(tmp_path / "receipt.json"),
        ]
    )
    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    return code, receipt, lease, store


def test_admission_admits_dev_admin_into_dev_and_records_the_profile(tmp_path: Path) -> None:
    code, receipt, lease, store = _admit(
        tmp_path, base_manifest(release_profile=DEV_ADMIN), environment="dev"
    )

    assert code == 0, receipt.get("errors")
    assert receipt["release_profile"] == "dev-admin"
    assert store.get(lease["lease_id"])["state"] == STATE_CONSUMED


def test_admission_records_full_for_an_unnarrowed_manifest(tmp_path: Path) -> None:
    code, receipt, _, _ = _admit(tmp_path, base_manifest(), environment="dev")

    assert code == 0, receipt.get("errors")
    assert receipt["release_profile"] == "full"


def test_a_dev_admin_manifest_with_a_valid_staging_lease_is_refused_unconsumed(
    tmp_path: Path,
) -> None:
    code, receipt, lease, store = _admit(
        tmp_path, base_manifest(release_profile=DEV_ADMIN), environment="staging"
    )

    assert code != 0
    assert receipt["admitted"] is False
    assert any("cannot be admitted into 'staging'" in e for e in receipt["errors"])
    # Refused before anything is spent: the lease is still issued.
    assert store.get(lease["lease_id"])["state"] == STATE_ISSUED


# --------------------------------------------------------------------------
# Deploy entrypoint refuses before any cloud mutation
# --------------------------------------------------------------------------

DEPLOY_SCRIPT = ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh"
_REQUIRED_ENV = {
    "ODAY_RELEASE_SHA": SHA,
    "API_SERVICE": "api",
    "WEB_SERVICE": "web",
    "MIGRATION_JOB": "migrate",
    "WORKER_JOB": "worker",
    "SCHEDULER_JOB": "scheduler",
    "WORKER_SCHEDULE_NAME": "worker-trigger",
    "SCHEDULER_SCHEDULE_NAME": "scheduler-trigger",
    "ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT": "sched@example.iam.gserviceaccount.com",
    "ODP_WORKER_CRON": "* * * * *",
    "ODP_SCHEDULER_CRON": "* * * * *",
    "ODP_SCHEDULER_TIME_ZONE": "UTC",
    "ODP_FORECAST_ENGINE": "statsforecast",
    "ODP_FORECAST_MODEL": "seasonal_naive",
    "ODP_OPERATOR_SMOKE_SERVICE_ACCOUNT": "smoke@example.iam.gserviceaccount.com",
    "ODP_PROD_DEPLOY_URL": "https://oday.example.com",
    "ODP_PROD_API_URL": "https://api.oday.example.com",
}


def _run_deploy(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    # A PATH with nothing on it but a recorder: any gcloud/docker/uv call that
    # escapes the early refusal is visible as a recorded invocation.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("gcloud", "docker", "uv", "cosign", "terraform"):
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho {tool} "$@" >> {log}\nexit 97\n', encoding="utf-8")
        stub.chmod(0o755)
    full_env = {"PATH": f"{bin_dir}:/usr/bin:/bin", **_REQUIRED_ENV, **env}
    result = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT)],
        cwd=ROOT,
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    result.calls = log.read_text(encoding="utf-8") if log.exists() else ""  # type: ignore[attr-defined]
    return result


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_deploy_refuses_dev_admin_outside_dev_before_any_cloud_call(
    tmp_path: Path, environment: str
) -> None:
    result = _run_deploy(
        tmp_path,
        ODP_DEPLOY_ENV=environment,
        ODP_RELEASE_PROFILE="dev-admin",
        ODP_DEV_ADMIN_USERNAME="ops-admin",
        ODP_DEV_ADMIN_PASSWORD="not-a-real-password",
        ODP_DEV_ADMIN_DENIED_OPERATOR_ROLE="cs-lead",
    )

    assert result.returncode != 0
    assert "may only deploy to dev" in result.stderr
    assert result.calls == ""  # type: ignore[attr-defined]


def test_deploy_refuses_an_unknown_profile_before_any_cloud_call(tmp_path: Path) -> None:
    result = _run_deploy(tmp_path, ODP_DEPLOY_ENV="dev", ODP_RELEASE_PROFILE="admin")

    assert result.returncode != 0
    assert "unknown release profile" in result.stderr
    assert result.calls == ""  # type: ignore[attr-defined]


def test_deploy_refuses_dev_admin_without_its_sign_in_account(tmp_path: Path) -> None:
    result = _run_deploy(tmp_path, ODP_DEPLOY_ENV="dev", ODP_RELEASE_PROFILE="dev-admin")

    assert result.returncode != 0
    assert "ODP_DEV_ADMIN_USERNAME" in result.stderr
    assert result.calls == ""  # type: ignore[attr-defined]


def test_deploy_refuses_dev_admin_without_the_bootstrap_admin_account(tmp_path: Path) -> None:
    result = _run_deploy(
        tmp_path,
        ODP_DEPLOY_ENV="dev",
        ODP_RELEASE_PROFILE="dev-admin",
        ODP_DEV_ADMIN_USERNAME="ops-admin",
        ODP_DEV_ADMIN_PASSWORD="not-a-real-password",
        ODP_DEV_ADMIN_DENIED_OPERATOR_ROLE="cs-lead",
    )

    assert result.returncode != 0
    assert "ODP_DEV_BOOTSTRAP_ADMIN_USERNAME" in result.stderr
    assert result.calls == ""  # type: ignore[attr-defined]
