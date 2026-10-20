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
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from apps.api.oday_api import runtime_mode
from delivery_toolchain.release.build_release_handoff import HandoffError, build_handoff
from delivery_toolchain.release.check_release_phase import phase_errors
from delivery_toolchain.release.check_runtime_admission import main as admission_main
from delivery_toolchain.release.release_lease import (
    STATE_CONSUMED,
    STATE_ISSUED,
    LeaseStateStore,
    build_lease,
    generate_keypair,
)
from delivery_toolchain.release.release_manifest import (
    RELEASE_PROFILE_CORE_OPS,
    RELEASE_PROFILE_DEV_ADMIN,
    RELEASE_PROFILE_ENVIRONMENTS,
    RELEASE_PROFILE_FULL,
    RELEASE_PROFILES,
    build_release_manifest,
    build_release_profile,
    compute_data_contract_digest,
    compute_manifest_digest,
    manifest_release_profile,
    release_profile_admissible_environments,
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


def core_ops(target: str = "staging") -> dict:
    return {"name": "core-ops", "target_environment": target, "model_readiness": "not_claimed"}


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


# --------------------------------------------------------------------------
# core-ops (ODP-CORE-OPS-RELEASE-PROFILE-001)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["dev", "staging"])
def test_core_ops_binds_its_build_target_and_claims_no_model_readiness(target: str) -> None:
    assert build_release_profile(RELEASE_PROFILE_CORE_OPS, target_environment=target) == (
        core_ops(target)
    )


def test_core_ops_is_never_sealed_for_production() -> None:
    # Production only ever admits the staging-sealed manifest, so a production
    # core-ops release cannot carry digests staging never rehearsed.
    with pytest.raises(ValueError, match="admits only the manifest sealed for \\['staging'\\]"):
        build_release_profile(RELEASE_PROFILE_CORE_OPS, target_environment="production")


@pytest.mark.parametrize("target", ["", "qa", "Production"])
def test_core_ops_cannot_be_built_for_an_unknown_target(target: str) -> None:
    with pytest.raises(ValueError, match="may only target"):
        build_release_profile(RELEASE_PROFILE_CORE_OPS, target_environment=target)


@pytest.mark.parametrize(
    ("target", "environment", "admitted"),
    [
        ("dev", "dev", True),
        ("dev", "staging", False),
        ("dev", "production", False),
        ("staging", "staging", True),
        # The staging-rehearsed manifest is promoted unchanged.
        ("staging", "production", True),
        ("staging", "dev", False),
    ],
)
def test_core_ops_admission_follows_the_promotion_path(
    target: str, environment: str, admitted: bool
) -> None:
    manifest = base_manifest(release_profile=core_ops(target))

    assert validate_manifest(manifest, expected_candidate_sha=SHA) == []
    errors = release_profile_errors(manifest, environment=environment)
    assert (errors == []) is admitted, errors
    if not admitted:
        assert any(f"cannot be admitted into '{environment}'" in e for e in errors)


def test_a_core_ops_manifest_has_its_own_digest_per_target_and_profile() -> None:
    digests = {
        base_manifest()["manifest_digest"],
        base_manifest(release_profile=DEV_ADMIN)["manifest_digest"],
        base_manifest(release_profile=core_ops("dev"))["manifest_digest"],
        base_manifest(release_profile=core_ops("staging"))["manifest_digest"],
    }
    assert len(digests) == 4


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda m: m.pop("release_profile"), id="profile-stripped-to-full"),
        pytest.param(lambda m: m["release_profile"].update(name="dev-admin"), id="renamed"),
        pytest.param(
            lambda m: m["release_profile"].update(target_environment="dev"), id="retargeted"
        ),
        pytest.param(
            lambda m: m["release_profile"].update(model_readiness="claimed"), id="claimed"
        ),
    ],
)
def test_editing_a_core_ops_profile_after_sealing_breaks_the_digest(mutate) -> None:
    manifest = base_manifest(release_profile=core_ops("staging"))
    mutate(manifest)

    assert "manifest.manifest_digest does not match its canonical immutable payload" in (
        validate_manifest(manifest)
    )


@pytest.mark.parametrize(
    ("profile", "fragment"),
    [
        (core_ops("production"), "target_environment must be one of"),
        (core_ops("qa"), "target_environment must be one of"),
        ({**core_ops(), "model_readiness": "claimed"}, "model_readiness must be"),
        ({**core_ops(), "promoted_to": "production"}, "must carry exactly"),
        ({"name": "core-ops", "target_environment": "staging"}, "must carry exactly"),
    ],
)
def test_a_malformed_core_ops_profile_is_invalid_even_when_resealed(
    profile: dict, fragment: str
) -> None:
    manifest = base_manifest(release_profile=profile)

    errors = validate_manifest(manifest)
    assert any(fragment in error for error in errors), errors
    # A resealed production target is not a production admission either.
    assert release_profile_errors(manifest, environment="production") != []


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
    for name in RELEASE_PROFILE_ENVIRONMENTS:
        environments = release_profile_admissible_environments(name)
        assert gate.RELEASE_PROFILE_DEPLOYMENTS[name] == environments
        assert runtime_mode._RELEASE_PROFILE_DEPLOYMENTS[name] == environments
    assert gate.RELEASE_PROFILE_DEPLOYMENTS[RELEASE_PROFILE_FULL] is None
    assert runtime_mode._RELEASE_PROFILE_DEPLOYMENTS[RELEASE_PROFILE_FULL] is None
    # Only full claims model readiness; every narrowed profile is held to the
    # missing-model refusal scope by the gate.
    assert gate.MODEL_READINESS_CLAIMING_PROFILES == frozenset({RELEASE_PROFILE_FULL})


# --------------------------------------------------------------------------
# Anti-drift (ODP-CORE-OPS-RELEASE-PROFILE-001): every pinned mirror of the
# profile vocabulary -- Python, workflow, shell and TypeScript -- must equal
# release_manifest.RELEASE_PROFILES, so adding or removing a profile anywhere
# without the others fails here rather than at a live deploy.
# --------------------------------------------------------------------------


def _deploy_script_profile_labels() -> set[str]:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index('case "${ODP_RELEASE_PROFILE}" in')
    block = script[start:script.index("\nesac\n", start)]
    return set(re.findall(r"^  ([a-z][a-z-]*)\)$", block, flags=re.MULTILINE))


def _ts_string_union(source: str, type_name: str) -> set[str]:
    match = re.search(rf"export type {type_name} = ([^;]+);", source)
    assert match, type_name
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _ts_profile_deployments(source: str) -> dict[str, frozenset[str] | None]:
    match = re.search(
        r"export const RELEASE_PROFILE_DEPLOYMENTS[^=]*= \{(.*?)\n\};", source, flags=re.DOTALL
    )
    assert match
    result: dict[str, frozenset[str] | None] = {}
    for key, value in re.findall(r'^\s*"?([a-z-]+)"?: (null|\[[^\]]*\]),$', match.group(1), re.M):
        result[key] = None if value == "null" else frozenset(re.findall(r'"([^"]+)"', value))
    return result


def test_every_profile_mirror_matches_the_manifest_vocabulary() -> None:
    from delivery_toolchain.e2e import live_business_journeys

    profiles = set(RELEASE_PROFILES)

    assert set(live_business_journeys.KNOWN_RELEASE_PROFILES) == profiles

    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare ``on:`` key as boolean True.
    dispatch_inputs = workflow[True]["workflow_dispatch"]["inputs"]
    assert set(dispatch_inputs["release_profile"]["options"]) == profiles
    assert dispatch_inputs["release_profile"]["default"] == RELEASE_PROFILE_FULL
    (extract,) = [
        step
        for step in workflow["jobs"]["admission"]["steps"]
        if step.get("id") == "extract-admitted-manifest"
    ]
    (case_pattern,) = re.findall(r"^\s*([a-z|-]+)\) ;;$", extract["run"], flags=re.MULTILINE)
    assert set(case_pattern.split("|")) == profiles

    assert _deploy_script_profile_labels() == profiles

    notice = (ROOT / "apps/web/features/operator/OperatorReleaseNotice.tsx").read_text(
        encoding="utf-8"
    )
    assert _ts_string_union(notice, "OperatorReleaseProfile") == profiles
    for profile in profiles:
        # Each profile has its own operator wording; none falls through to unknown.
        assert f'status.profile === "{profile}"' in notice

    # Staging runs exactly the profiles admissible into staging (full runs
    # everywhere), in the lifecycle and in the Terraform module validation.
    from product_ops.deployment import staging_lifecycle

    staging_profiles = {RELEASE_PROFILE_FULL} | {
        name
        for name in RELEASE_PROFILE_ENVIRONMENTS
        if "staging" in release_profile_admissible_environments(name)
    }
    assert set(staging_lifecycle.STAGING_RELEASE_PROFILES) == staging_profiles
    assert staging_lifecycle.MODEL_READINESS_CLAIMING_PROFILES == frozenset(
        {RELEASE_PROFILE_FULL}
    )
    staging_vars = (ROOT / "infra/terraform/modules/ephemeral_staging/variables.tf").read_text(
        encoding="utf-8"
    )
    (tf_pattern,) = re.findall(
        r'regex\("\^\(([a-z|-]+)\)\$", var\.release_profile\)', staging_vars
    )
    assert set(tf_pattern.split("|")) == staging_profiles

    status = (ROOT / "apps/web/src/lib/auth/operatorReleaseStatus.ts").read_text(
        encoding="utf-8"
    )
    deployments = _ts_profile_deployments(status)
    assert set(deployments) == profiles
    for profile in profiles:
        expected = (
            None
            if profile == RELEASE_PROFILE_FULL
            else release_profile_admissible_environments(profile)
        )
        assert deployments[profile] == expected, profile


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


@pytest.mark.parametrize("target", ["dev", "staging"])
def test_the_build_seals_core_ops_into_its_target_manifest(target: str) -> None:
    _, manifest = _handoff(target_environment=target, release_profile="core-ops")

    assert manifest["release_profile"] == core_ops(target)
    assert release_profile_errors(manifest, environment=target) == []


def test_the_build_refuses_core_ops_for_production() -> None:
    with pytest.raises(HandoffError) as excinfo:
        _handoff(target_environment="production", release_profile="core-ops")

    assert any("may only target ['dev', 'staging']" in error for error in excinfo.value.errors)


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
    stage, registry_env = {
        "dev": ("candidate-built", "dev"),
        "staging": ("dev-verified", "dev"),
        "production": ("staging-verified", "staging"),
    }[environment]
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


@pytest.mark.parametrize(
    ("target", "environment"), [("dev", "dev"), ("staging", "staging"), ("staging", "production")]
)
def test_core_ops_admission_matches_full_on_its_promotion_path(
    tmp_path: Path, target: str, environment: str
) -> None:
    """On its promotion path core-ops is admitted wherever full is, lease consumed."""

    (tmp_path / "full").mkdir()
    (tmp_path / "core-ops").mkdir()
    full_code, full_receipt, _, _ = _admit(
        tmp_path / "full", base_manifest(), environment=environment
    )
    code, receipt, lease, store = _admit(
        tmp_path / "core-ops", base_manifest(release_profile=core_ops(target)),
        environment=environment,
    )

    assert full_code == 0, full_receipt.get("errors")
    assert code == 0, receipt.get("errors")
    assert receipt["admitted"] is True
    assert receipt["release_profile"] == "core-ops"
    assert store.get(lease["lease_id"])["state"] == STATE_CONSUMED


@pytest.mark.parametrize(
    ("target", "environment"), [("dev", "staging"), ("dev", "production"), ("staging", "dev")]
)
def test_core_ops_off_its_promotion_path_is_refused_with_the_lease_unconsumed(
    tmp_path: Path, target: str, environment: str
) -> None:
    code, receipt, lease, store = _admit(
        tmp_path, base_manifest(release_profile=core_ops(target)), environment=environment
    )

    assert code != 0
    assert receipt["admitted"] is False
    assert any(f"cannot be admitted into '{environment}'" in e for e in receipt["errors"])
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


def test_deploy_refuses_dev_admin_without_its_password(tmp_path: Path) -> None:
    result = _run_deploy(
        tmp_path,
        ODP_DEPLOY_ENV="dev",
        ODP_RELEASE_PROFILE="dev-admin",
        ODP_DEV_ADMIN_USERNAME="ops-admin",
    )

    assert result.returncode != 0
    assert "ODP_DEV_ADMIN_PASSWORD" in result.stderr
    assert result.calls == ""  # type: ignore[attr-defined]


def test_deploy_refuses_dev_admin_without_its_denied_role(tmp_path: Path) -> None:
    result = _run_deploy(
        tmp_path,
        ODP_DEPLOY_ENV="dev",
        ODP_RELEASE_PROFILE="dev-admin",
        ODP_DEV_ADMIN_USERNAME="ops-admin",
        ODP_DEV_ADMIN_PASSWORD="not-a-real-password",
    )

    assert result.returncode != 0
    assert "ODP_DEV_ADMIN_DENIED_OPERATOR_ROLE" in result.stderr
    assert result.calls == ""  # type: ignore[attr-defined]


def _run_profile_case(**env: str) -> subprocess.CompletedProcess:
    """Run only the deploy script's profile block, up to its cloud boundary."""

    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    block = script[
        script.index('ODP_RELEASE_PROFILE="${ODP_RELEASE_PROFILE:-full}"'):
        script.index("export ODP_RELEASE_PROFILE\n")
    ]
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + block + '\nprintf "profile=%s\\n" "${ODP_RELEASE_PROFILE}"'],
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
def test_deploy_accepts_core_ops_everywhere_without_dev_admin_inputs(environment: str) -> None:
    # No ODP_DEV_ADMIN_* at all: they never become a core-ops prerequisite.
    result = _run_profile_case(ODP_DEPLOY_ENV=environment, ODP_RELEASE_PROFILE="core-ops")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "profile=core-ops"


@pytest.mark.parametrize("environment", ["qa", "development", ""])
def test_deploy_refuses_core_ops_outside_its_environments(environment: str) -> None:
    result = _run_profile_case(ODP_DEPLOY_ENV=environment, ODP_RELEASE_PROFILE="core-ops")

    assert result.returncode != 0
    assert "may only deploy to dev, staging or production" in result.stderr
    assert "profile=" not in result.stdout


# --------------------------------------------------------------------------
# The build phase publishes, signs and attests images before the handoff seals
# the manifest, so the profile/target refusal must already happen in the pure,
# unbound ``release_phase`` input check that every build job needs.
# --------------------------------------------------------------------------

WORKFLOW = ROOT / ".github/workflows/deploy-dev.yml"
PHASE_CHECK = ROOT / "delivery_toolchain/release/check_release_phase.py"


def _build_phase_errors(environment: str, profile: str) -> list[str]:
    return phase_errors(
        phase="build",
        release_sha="a" * 40,
        environment=environment,
        images={},
        lease_supplied=False,
        release_profile=profile,
    )


@pytest.mark.parametrize("environment", ["dev", "staging", "production"])
def test_the_phase_check_leaves_the_default_full_build_unchanged(environment: str) -> None:
    assert _build_phase_errors(environment, RELEASE_PROFILE_FULL) == []


def test_the_phase_check_admits_a_dev_admin_build_for_dev() -> None:
    assert _build_phase_errors("dev", RELEASE_PROFILE_DEV_ADMIN) == []


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_phase_check_refuses_dev_admin_for_staging_and_production(
    environment: str,
) -> None:
    errors = _build_phase_errors(environment, RELEASE_PROFILE_DEV_ADMIN)
    assert len(errors) == 1
    assert "may only target ['dev']" in errors[0]


@pytest.mark.parametrize("environment", ["dev", "staging"])
def test_the_phase_check_admits_a_core_ops_build_for_dev_and_staging(environment: str) -> None:
    assert _build_phase_errors(environment, RELEASE_PROFILE_CORE_OPS) == []


def test_the_phase_check_refuses_a_core_ops_production_build() -> None:
    errors = _build_phase_errors("production", RELEASE_PROFILE_CORE_OPS)
    assert len(errors) == 1
    assert "may only target ['dev', 'staging']" in errors[0]


@pytest.mark.parametrize("name", ["admin", "Dev-Admin", ""])
def test_the_phase_check_refuses_an_unknown_profile(name: str) -> None:
    errors = _build_phase_errors("dev", name)
    assert len(errors) == 1
    assert "unknown release profile" in errors[0]


@pytest.mark.parametrize("profile", [RELEASE_PROFILE_DEV_ADMIN, "admin"])
def test_the_deploy_phase_takes_no_profile_of_its_own(profile: str) -> None:
    errors = phase_errors(
        phase="deploy",
        release_sha="a" * 40,
        environment="dev",
        images={
            name: f"r/{name}@sha256:" + "1" * 64 for name in ("api", "web", "worker", "scheduler")
        },
        lease_supplied=True,
        manifest_run_id="17654321098",
        manifest_digest="sha256:" + "2" * 64,
        release_profile=profile,
    )
    assert len(errors) == 1
    assert "deploy 階段不得帶入 release_profile" in errors[0]


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_the_phase_cli_refuses_before_any_publishing_tool_runs(
    tmp_path: Path, environment: str
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("gcloud", "docker", "cosign"):
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho {tool} "$@" >> {log}\nexit 97\n', encoding="utf-8")
        stub.chmod(0o755)
    receipt = tmp_path / "receipt.json"
    result = subprocess.run(
        [
            "python3",
            str(PHASE_CHECK),
            "--phase",
            "build",
            "--environment",
            environment,
            "--release-sha",
            "a" * 40,
            "--release-profile",
            RELEASE_PROFILE_DEV_ADMIN,
            "--receipt",
            str(receipt),
        ],
        cwd=tmp_path,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 1
    assert "may only target ['dev']" in result.stderr
    assert not log.exists()
    recorded = json.loads(receipt.read_text(encoding="utf-8"))
    assert recorded["admitted"] is False
    assert recorded["release_profile"] == RELEASE_PROFILE_DEV_ADMIN


def test_the_workflow_validates_the_profile_before_the_build_publishes() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]

    phase_job = jobs["release_phase"]
    # The pure check binds no environment, so it runs before any deploy approval.
    assert "environment" not in phase_job
    (check,) = [
        step for step in phase_job["steps"] if "check_release_phase.py" in str(step.get("run", ""))
    ]
    assert check["env"]["RELEASE_PROFILE_INPUT"] == "${{ inputs.release_profile }}"
    assert '--release-profile "${RELEASE_PROFILE_INPUT:-full}"' in check["run"]

    # Every image push/sign/attest happens inside the build job, which cannot
    # start unless that check succeeded.
    build_job = jobs["build"]
    assert build_job["needs"] == "release_phase"
    assert any("sign_images.sh sign" in str(step.get("run", "")) for step in build_job["steps"])
