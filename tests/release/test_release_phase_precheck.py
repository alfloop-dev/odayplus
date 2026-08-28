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


def images(**overrides: str) -> dict[str, str]:
    built = {name: IMAGE.format(name=name) for name in HANDOFF_COMPONENTS}
    built.update(overrides)
    return built


def errors_for(**overrides) -> list[str]:
    kwargs = {
        "phase": "deploy",
        "release_sha": SHA,
        "environment": "dev",
        "images": images(),
        "lease_supplied": True,
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
    )
    assert code == 0
    assert receipt["image_handoff"] == built
