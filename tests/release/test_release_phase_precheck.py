"""Fail-closed precheck for the two Runtime Release phases.

The point of the phase split is that a build must be reachable without a lease,
and a deploy must be unreachable without one. These tests hold both halves: a
lease offered to the build phase is a refusal (accepting it would put the
circular dependency back), and a missing artifact or a missing lease refuses
rather than degrading.

Environment-scoped configuration is deliberately *not* asserted here. The job
that runs this precheck binds no GitHub environment, so it cannot observe
`vars.*` at all; that check lives in `test_release_environment_precheck.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from delivery_toolchain.release import check_release_phase
from delivery_toolchain.release.check_release_phase import (
    HANDOFF_COMPONENTS,
    main,
    phase_errors,
)

SHA = "a" * 40
IMAGE = "asia-east1-docker.pkg.dev/p/r/{name}@sha256:" + "1" * 64

# The coordinates that let a deploy fetch the exact manifest its build produced:
# which build run published it, and which digest the lease was issued against.
MANIFEST_RUN_ID = "17654321098"
MANIFEST_DIGEST = "sha256:" + "2" * 64


def images(**overrides: str) -> dict[str, str]:
    built = {name: IMAGE.format(name=name) for name in HANDOFF_COMPONENTS}
    built.update(overrides)
    return built


def errors_for(**overrides) -> list[str]:
    phase = overrides.get("phase", "deploy")
    kwargs = {
        "phase": "deploy",
        "release_sha": SHA,
        "environment": "dev",
        "images": images(),
        "lease_supplied": True,
        # A deploy must name the manifest it is authorised for; a build produces
        # that manifest, so being handed one is a refusal there.
        "manifest_run_id": MANIFEST_RUN_ID if phase == "deploy" else "",
        "manifest_digest": MANIFEST_DIGEST if phase == "deploy" else "",
    }
    kwargs.update(overrides)
    return phase_errors(**kwargs)


# --------------------------------------------------------------------------
# The build phase must not need deploy authority
# --------------------------------------------------------------------------


def test_a_build_phase_with_no_lease_and_no_handoff_is_admitted() -> None:
    assert (
        errors_for(
            phase="build",
            images=dict.fromkeys(HANDOFF_COMPONENTS, ""),
            lease_supplied=False,
        )
        == []
    )


def test_a_lease_offered_to_the_build_phase_is_refused() -> None:
    """Consuming a lease to build is what made the manifest unreachable."""

    errors = errors_for(
        phase="build",
        images=dict.fromkeys(HANDOFF_COMPONENTS, ""),
        lease_supplied=True,
    )
    assert any("lease 只授權 deploy 階段" in error for error in errors)


def test_the_build_phase_refuses_a_pre_supplied_handoff() -> None:
    errors = errors_for(
        phase="build",
        images=images(api="", web="", worker=""),
        lease_supplied=False,
    )
    assert any("build 階段不得預先指定 image handoff" in error for error in errors)


# --------------------------------------------------------------------------
# The deploy phase must not be reachable without the artifact or the lease
# --------------------------------------------------------------------------


def test_a_complete_deploy_handoff_with_a_lease_is_admitted() -> None:
    assert errors_for() == []


def test_a_deploy_without_a_lease_is_refused() -> None:
    errors = errors_for(lease_supplied=False)
    assert any("缺少簽章 Supervisor lease" in error for error in errors)


@pytest.mark.parametrize("missing", HANDOFF_COMPONENTS)
def test_a_deploy_missing_any_component_is_refused(missing: str) -> None:
    errors = errors_for(images=images(**{missing: ""}))
    assert any("缺少 build-once artifact handoff" in error for error in errors)
    assert any(missing in error for error in errors)


def test_a_mutable_tag_is_not_an_artifact_handoff() -> None:
    errors = errors_for(images=images(api="asia-east1-docker.pkg.dev/p/r/api:latest"))
    assert any("immutable @sha256 reference" in error for error in errors)


def test_a_deploy_phase_never_falls_back_to_building() -> None:
    """Every deploy refusal reason has to be about the missing artifact."""

    errors = errors_for(images=dict.fromkeys(HANDOFF_COMPONENTS, ""))
    assert errors
    assert all("deploy 階段不得重新 build" in error or "缺少" in error for error in errors)


# --------------------------------------------------------------------------
# Shared preconditions
# --------------------------------------------------------------------------


@pytest.mark.parametrize("phase", ["build", "deploy"])
def test_the_input_gate_never_judges_environment_scoped_configuration(phase: str) -> None:
    """An unbound job reading `vars.*` would refuse every run, correct or not.

    `HAS_WIF` used to be derived here from `vars.GCP_WORKLOAD_IDENTITY_PROVIDER`
    inside a job with no `environment:` binding, where it can only ever expand
    to the empty string -- making "缺少 OIDC" an unconditional verdict on a
    correctly configured repository. The check moved to the jobs that are
    actually bound; this module must not grow it back.
    """

    source = Path(check_release_phase.__file__).read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]
    assert "GCP_WORKLOAD_IDENTITY_PROVIDER" not in body
    assert "oidc" not in body.lower()

    assert (
        errors_for(
            phase=phase,
            images=images()
            if phase == "deploy"
            else dict.fromkeys(HANDOFF_COMPONENTS, ""),
            lease_supplied=phase == "deploy",
        )
        == []
    )


def test_a_branch_name_is_not_an_exact_release_sha() -> None:
    assert any("40 字元小寫 git SHA" in error for error in errors_for(release_sha="dev"))


def test_an_unknown_phase_is_refused() -> None:
    assert any("phase 必須是" in error for error in errors_for(phase="promote"))


# --------------------------------------------------------------------------
# The receipt
# --------------------------------------------------------------------------


def _run(tmp_path: Path, *args: str) -> tuple[int, dict]:
    receipt_path = tmp_path / "phase-receipt.json"
    code = main([*args, "--receipt", str(receipt_path)])
    return code, json.loads(receipt_path.read_text(encoding="utf-8"))


def test_a_refusal_still_writes_a_zh_tw_receipt(tmp_path: Path) -> None:
    code, receipt = _run(
        tmp_path,
        "--phase",
        "deploy",
        "--environment",
        "dev",
        "--release-sha",
        SHA,
        "--task-id",
        "ODP-RELEASE-BUILD-PHASE-BOOTSTRAP-001",
        "--lease-supplied",
        "false",
    )
    assert code == 1
    assert receipt["admitted"] is False
    assert receipt["blockers_zh_tw"], "a refusal with no stated reason is not evidence"
    assert receipt["summary_zh_tw"]
    assert receipt["secret_values_redacted"] is True
    assert receipt["phase"] == "deploy"
    assert receipt["task_id"] == "ODP-RELEASE-BUILD-PHASE-BOOTSTRAP-001"


def test_the_receipt_records_lease_presence_but_never_the_lease(tmp_path: Path) -> None:
    """A publishable refusal cannot carry the signed document that was offered."""

    code, receipt = _run(
        tmp_path,
        "--phase",
        "build",
        "--environment",
        "dev",
        "--release-sha",
        SHA,
        "--lease-supplied",
        "true",
    )
    assert code == 1
    assert receipt["lease_supplied"] is True
    serialized = json.dumps(receipt, ensure_ascii=False)
    for field in ("signature", "nonce", "lease_id", "release_lease"):
        assert field not in serialized


def test_an_admitted_build_phase_receipt_names_no_handoff(tmp_path: Path) -> None:
    code, receipt = _run(
        tmp_path,
        "--phase",
        "build",
        "--environment",
        "dev",
        "--release-sha",
        SHA,
        "--lease-supplied",
        "false",
    )
    assert code == 0
    assert receipt["admitted"] is True
    assert receipt["image_handoff"] == dict.fromkeys(HANDOFF_COMPONENTS, None)
    assert receipt["manifest_handoff"] == {"run_id": None, "manifest_digest": None}


def test_an_admitted_deploy_receipt_names_the_exact_artifacts(tmp_path: Path) -> None:
    built = images()
    code, receipt = _run(
        tmp_path,
        "--phase",
        "deploy",
        "--environment",
        "staging",
        "--release-sha",
        SHA,
        "--api-image",
        built["api"],
        "--web-image",
        built["web"],
        "--worker-image",
        built["worker"],
        "--scheduler-image",
        built["scheduler"],
        "--lease-supplied",
        "true",
        "--manifest-run-id",
        MANIFEST_RUN_ID,
        "--manifest-digest",
        MANIFEST_DIGEST,
    )
    assert code == 0
    assert receipt["image_handoff"] == built
    assert receipt["manifest_handoff"] == {
        "run_id": MANIFEST_RUN_ID,
        "manifest_digest": MANIFEST_DIGEST,
    }


# --------------------------------------------------------------------------
# The manifest a deploy is authorised for has to be nameable
# --------------------------------------------------------------------------
#
# ODP-SOURCES-OFF-RELEASE-ADMISSION-REMEDIATION-001: build and deploy are
# separate dispatches, so the deploy checkout only ever holds the manifest
# committed at the release SHA. Admission used to fall back to that file, which
# made a build's sources-off attestation and its `manifest_digest` unverifiable
# without committing a new manifest onto an immutable SHA. Deploy therefore has
# to say which build run published its manifest and which digest the lease
# names, or there is nothing exact to transport.


def test_a_deploy_without_a_manifest_run_id_is_refused() -> None:
    errors = errors_for(manifest_run_id="")
    assert any("缺少 manifest_run_id" in error for error in errors)


def test_a_deploy_without_a_manifest_digest_is_refused() -> None:
    errors = errors_for(manifest_digest="")
    assert any("缺少 manifest_digest" in error for error in errors)


@pytest.mark.parametrize(
    "run_id",
    ["0", "-1", "17654321098abc", "latest", "1.5", " "],
)
def test_a_manifest_run_id_that_is_not_a_run_id_is_refused(run_id: str) -> None:
    errors = errors_for(manifest_run_id=run_id)
    assert any("manifest_run_id" in error for error in errors)


@pytest.mark.parametrize(
    "digest",
    [
        "2" * 64,
        "sha256:" + "2" * 63,
        "sha256:" + "F" * 64,
        "sha1:" + "2" * 40,
        "sha256:not-a-digest",
    ],
)
def test_a_manifest_digest_that_is_not_a_sha256_digest_is_refused(digest: str) -> None:
    errors = errors_for(manifest_digest=digest)
    assert any("manifest_digest 必須是 sha256" in error for error in errors)


def test_a_deploy_naming_its_manifest_is_admitted() -> None:
    assert errors_for() == []


@pytest.mark.parametrize(
    ("field", "value"),
    [("manifest_run_id", MANIFEST_RUN_ID), ("manifest_digest", MANIFEST_DIGEST)],
)
def test_the_build_phase_refuses_pre_supplied_manifest_coordinates(
    field: str, value: str
) -> None:
    """A build that accepts a manifest coordinate is claiming its own output.

    The build phase is what produces the candidate manifest a lease is later
    issued against. Letting it be handed one would let a dispatch assert that
    the artifact it is about to build has already been authorised.
    """

    errors = errors_for(
        phase="build",
        images=dict.fromkeys(HANDOFF_COMPONENTS, ""),
        lease_supplied=False,
        **{field: value},
    )
    assert any("build 階段不得帶入 manifest_run_id" in error for error in errors)


def test_a_refused_deploy_receipt_still_names_the_manifest_coordinates(
    tmp_path: Path,
) -> None:
    """Run id and digest are audit values, not secrets: a refusal states them."""

    code, receipt = _run(
        tmp_path,
        "--phase",
        "deploy",
        "--environment",
        "dev",
        "--release-sha",
        SHA,
        "--lease-supplied",
        "false",
        "--manifest-run-id",
        MANIFEST_RUN_ID,
        "--manifest-digest",
        MANIFEST_DIGEST,
    )
    assert code == 1
    assert receipt["manifest_handoff"] == {
        "run_id": MANIFEST_RUN_ID,
        "manifest_digest": MANIFEST_DIGEST,
    }
