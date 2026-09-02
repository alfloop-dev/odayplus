"""The GitHub environment binding gate for Runtime Release.

`odayplus` keeps every deployment variable at environment scope -- the
repository has no repository-level Actions variables at all. GitHub only
injects those into `vars.*` for a job that carries an `environment:` binding,
and an unbound job does not error: `vars.GCP_PROJECT_ID` simply expands to the
empty string. These tests hold the consequence, which is the part a workflow
run can actually observe: an empty required variable refuses the phase, in
zh-TW, naming the environment to fix -- and the receipt that refusal writes
records presence, never values.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from delivery_toolchain.release.check_release_environment import (
    REQUIRED_VARIABLES,
    SCOPES,
    binding_errors,
    main,
    missing_variables,
    required_variables,
)

SHA = "b" * 40


def resolved(scope: str, **overrides: str) -> dict[str, str]:
    """Every variable this scope needs, resolved to a plausible non-empty value."""

    values = {name: f"resolved-{name.lower()}" for name in required_variables(scope)}
    values.update(overrides)
    return values


def errors_for(scope: str = "build", **overrides):
    kwargs = {
        "scope": scope,
        "environment": "dev",
        "github_environment": "dev-build",
        "values": resolved(scope),
    }
    kwargs.update(overrides)
    return binding_errors(**kwargs)


# --------------------------------------------------------------------------
# A bound job with resolved variables is admitted
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scope", SCOPES)
def test_a_fully_resolved_scope_is_admitted(scope: str) -> None:
    assert (
        errors_for(
            scope,
            github_environment="dev-build" if scope == "build" else "dev",
        )
        == []
    )


# --------------------------------------------------------------------------
# The failure this gate exists for
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scope", SCOPES)
def test_an_unbound_job_sees_every_variable_empty_and_is_refused(scope: str) -> None:
    """This is exactly what an unbound job observes: empty strings, no error."""

    unbound = dict.fromkeys(required_variables(scope), "")
    errors = errors_for(scope, values=unbound)
    assert errors
    joined = "\n".join(errors)
    for name in required_variables(scope):
        assert name in joined, f"a refusal that does not name {name} is not actionable"


@pytest.mark.parametrize("scope", SCOPES)
def test_a_single_missing_variable_still_refuses(scope: str) -> None:
    victim = required_variables(scope)[-1]
    errors = errors_for(scope, values=resolved(scope, **{victim: ""}))
    assert errors
    assert victim in "\n".join(errors)


def test_whitespace_is_not_a_resolved_value() -> None:
    """A variable set to spaces resolves to something that cannot be used."""

    assert missing_variables("build", resolved("build", GCP_AR_REPO="   ")) == [
        "GCP_AR_REPO"
    ]


def test_the_refusal_says_the_environment_may_simply_not_exist() -> None:
    """GitHub auto-creates a referenced environment, empty, instead of failing.

    Without that sentence the operator reads "variable missing" and goes
    looking at `dev`, which has the variable, rather than at `dev-build`,
    which is where the binding actually points.
    """

    errors = errors_for(values=dict.fromkeys(required_variables("build"), ""))
    joined = "\n".join(errors)
    assert "dev-build" in joined
    assert "自動建" in joined


def test_an_unbound_job_is_refused_even_before_variables_are_read() -> None:
    errors = errors_for(github_environment="")
    assert any("environment:" in error for error in errors)


def test_an_unknown_scope_is_refused() -> None:
    errors = binding_errors(
        scope="promote",
        environment="dev",
        github_environment="dev-build",
        values={},
    )
    assert any("scope 必須是" in error for error in errors)


def test_asking_for_an_unknown_scopes_variables_raises_rather_than_returning_none() -> None:
    """A silent empty tuple would make an unknown scope pass every check."""

    with pytest.raises(ValueError, match="promote"):
        required_variables("promote")


# --------------------------------------------------------------------------
# What each scope actually needs
# --------------------------------------------------------------------------


def test_every_scope_requires_federated_identity() -> None:
    for scope in SCOPES:
        assert "GCP_WORKLOAD_IDENTITY_PROVIDER" in required_variables(scope)
        assert "GCP_SERVICE_ACCOUNT" in required_variables(scope)


def test_the_build_scope_can_address_the_registry_it_publishes_to() -> None:
    """`REPO_PATH` is built from these three; empty ones give `-docker.pkg.dev//`."""

    for name in ("GCP_PROJECT_ID", "GCP_REGION", "GCP_AR_REPO"):
        assert name in required_variables("build")


def test_the_build_scope_does_not_demand_deploy_only_variables() -> None:
    """The migration job is deployed, never built; requiring it would be a fake gate."""

    assert "ODP_CLOUD_RUN_MIGRATION_JOB" not in required_variables("build")
    assert "ODP_CLOUD_RUN_MIGRATION_JOB" in required_variables("deploy")


def test_admission_needs_the_shared_lease_store_not_the_registry() -> None:
    """Admission verifies a signature against shared state; it never builds."""

    assert "ODP_RELEASE_LEASE_PUBLIC_KEY" in required_variables("admission")
    assert "ODP_RELEASE_LEASE_STATE_URI" in required_variables("admission")
    assert "GCP_AR_REPO" not in required_variables("admission")


def test_no_scope_requires_a_variable_twice() -> None:
    for scope, names in REQUIRED_VARIABLES.items():
        assert len(names) == len(set(names)), f"{scope} lists a variable twice"


# --------------------------------------------------------------------------
# The receipt
# --------------------------------------------------------------------------


def _run(tmp_path: Path, env: dict[str, str], *args: str) -> tuple[int, dict]:
    receipt_path = tmp_path / "environment-receipt.json"
    import os

    previous = {name: os.environ.get(name) for name in env}
    os.environ.update(env)
    try:
        code = main([*args, "--receipt", str(receipt_path)])
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return code, json.loads(receipt_path.read_text(encoding="utf-8"))


def test_a_refusal_writes_a_zh_tw_receipt_naming_the_environment(
    tmp_path: Path,
) -> None:
    code, receipt = _run(
        tmp_path,
        dict.fromkeys(required_variables("build"), ""),
        "--scope",
        "build",
        "--environment",
        "dev",
        "--github-environment",
        "dev-build",
        "--release-sha",
        SHA,
        "--task-id",
        "ODP-RELEASE-BUILD-PHASE-BOOTSTRAP-001",
    )
    assert code == 1
    assert receipt["admitted"] is False
    assert receipt["github_environment"] == "dev-build"
    assert receipt["blockers_zh_tw"], "a refusal with no stated reason is not evidence"
    assert set(receipt["missing_variables"]) == set(required_variables("build"))
    assert receipt["task_id"] == "ODP-RELEASE-BUILD-PHASE-BOOTSTRAP-001"


def test_an_admitted_receipt_records_presence_but_never_a_value(
    tmp_path: Path,
) -> None:
    """The WIF provider path and service account identify the cloud identity."""

    values = resolved(
        "build",
        GCP_WORKLOAD_IDENTITY_PROVIDER="projects/1/locations/global/x/y",
        GCP_SERVICE_ACCOUNT="releaser@odayplus-runtime.iam.gserviceaccount.com",
    )
    code, receipt = _run(
        tmp_path,
        values,
        "--scope",
        "build",
        "--environment",
        "dev",
        "--github-environment",
        "dev-build",
    )
    assert code == 0
    assert receipt["admitted"] is True
    assert receipt["variables_resolved"] == dict.fromkeys(
        required_variables("build"), True
    )
    assert receipt["missing_variables"] == []
    assert receipt["secret_values_redacted"] is True

    serialized = json.dumps(receipt, ensure_ascii=False)
    assert (
        receipt["resolved_non_secret_values"]["ODP_CLOUD_RUN_VPC_EGRESS"]
        == values["ODP_CLOUD_RUN_VPC_EGRESS"]
    )
    for name, value in values.items():
        if name == "ODP_CLOUD_RUN_VPC_EGRESS":
            continue
        assert value not in serialized


def test_the_receipt_reports_the_scope_it_checked(tmp_path: Path) -> None:
    code, receipt = _run(
        tmp_path,
        resolved("deploy"),
        "--scope",
        "deploy",
        "--environment",
        "production",
        "--github-environment",
        "production",
    )
    assert code == 0
    assert receipt["scope"] == "deploy"
    assert receipt["environment"] == "production"
    assert receipt["github_environment"] == "production"
