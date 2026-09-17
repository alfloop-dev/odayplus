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
    VPC_BINDING_MODES,
    VPC_BINDING_SCOPES,
    VPC_BINDING_VARIABLES,
    binding_errors,
    declared_variables,
    main,
    missing_variables,
    required_variables,
    resolved_vpc_binding_mode,
)

SHA = "b" * 40


def resolved(scope: str, **overrides: str) -> dict[str, str]:
    """Every variable this scope needs, resolved to a plausible non-empty value.

    A scope that deploys to Cloud Run also needs exactly one VPC binding mode.
    The connector is the default here because `dev` still deploys through one;
    the Direct VPC tests below override it explicitly.
    """

    values = {name: f"resolved-{name.lower()}" for name in required_variables(scope)}
    if scope in VPC_BINDING_SCOPES:
        for name in VPC_BINDING_MODES["connector"]:
            values[name] = f"resolved-{name.lower()}"
    values.update(overrides)
    return values


def direct_vpc(scope: str = "deploy", **overrides: str) -> dict[str, str]:
    """A production-shaped deploy resolution: Direct VPC egress, no connector."""

    values = resolved(scope)
    for name in VPC_BINDING_MODES["connector"]:
        values[name] = ""
    for name in VPC_BINDING_MODES["direct_vpc"]:
        values[name] = "oday-prod-runtime"
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

    unbound = dict.fromkeys(declared_variables(scope), "")
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
    for scope in SCOPES:
        names = declared_variables(scope)
        assert len(names) == len(set(names)), f"{scope} declares a variable twice"


# --------------------------------------------------------------------------
# Cloud Run VPC binding: a connector or Direct VPC egress, exactly one
#
# `infra/terraform/cloud_run.tf` attaches production to the VPC with
# `vpc_access.network_interfaces` (Direct VPC egress) and creates no Serverless
# VPC Access connector. A gate that demanded `ODP_CLOUD_RUN_VPC_CONNECTOR`
# unconditionally could therefore only ever be satisfied by inventing a
# resource the IaC does not produce (ODP-PROD-NETWORK-PARITY-PLAN-001 §4).
# --------------------------------------------------------------------------


def test_the_build_scope_needs_no_vpc_network_binding_but_still_resolves_the_egress_mode() -> None:
    """The build never opens a network; the handoff does record the egress mode.

    `build_release_handoff.py` reads `ODP_CLOUD_RUN_VPC_EGRESS` from the build
    job and refuses a sources-off handoff whose egress is unresolved, so the
    egress mode stays in the build gate where a missing value is refused with
    a receipt naming the `<environment>-build` twin to fix.
    """

    for name in VPC_BINDING_VARIABLES:
        assert name not in declared_variables("build"), name
    assert "ODP_CLOUD_RUN_VPC_EGRESS" in required_variables("build")


def test_the_deploy_scope_declares_both_binding_modes_and_requires_neither_unconditionally() -> None:
    for name in VPC_BINDING_VARIABLES:
        assert name in declared_variables("deploy"), name
        assert name not in required_variables("deploy"), name
    assert "ODP_CLOUD_RUN_VPC_EGRESS" in required_variables("deploy")
    assert set(VPC_BINDING_MODES) == {"connector", "direct_vpc"}
    assert VPC_BINDING_MODES["direct_vpc"] == ("ODP_PROD_VPC_NETWORK", "ODP_PROD_VPC_SUBNETWORK")


def test_scopes_that_never_deploy_do_not_read_a_vpc_binding() -> None:
    for scope in ("build", "admission", "staging"):
        assert scope not in VPC_BINDING_SCOPES
        assert declared_variables(scope) == required_variables(scope)


def test_a_direct_vpc_production_deploy_without_a_connector_is_admitted() -> None:
    values = direct_vpc()
    assert values["ODP_CLOUD_RUN_VPC_CONNECTOR"] == ""
    assert (
        errors_for(
            "deploy",
            environment="production",
            github_environment="production",
            values=values,
        )
        == []
    )
    assert resolved_vpc_binding_mode(values) == "direct_vpc"


def test_a_connector_deploy_is_still_admitted() -> None:
    values = resolved("deploy")
    assert errors_for("deploy", github_environment="dev", values=values) == []
    assert resolved_vpc_binding_mode(values) == "connector"


@pytest.mark.parametrize(
    ("present", "missing"),
    [
        ("ODP_PROD_VPC_NETWORK", "ODP_PROD_VPC_SUBNETWORK"),
        ("ODP_PROD_VPC_SUBNETWORK", "ODP_PROD_VPC_NETWORK"),
    ],
)
def test_a_half_configured_direct_vpc_binding_is_refused_naming_the_missing_half(
    present: str, missing: str
) -> None:
    """gcloud would reject `--network` without `--subnet` only at the first mutation."""

    values = direct_vpc(**{missing: ""})
    errors = errors_for(
        "deploy", environment="production", github_environment="production", values=values
    )
    assert errors
    joined = "\n".join(errors)
    assert missing in joined
    assert present in joined
    assert "一半" in joined
    assert resolved_vpc_binding_mode(values) is None


def test_both_vpc_binding_modes_at_once_are_refused() -> None:
    """`--vpc-connector` and `--network/--subnet` are mutually exclusive on Cloud Run."""

    values = direct_vpc(ODP_CLOUD_RUN_VPC_CONNECTOR="projects/p/locations/l/connectors/c")
    errors = errors_for(
        "deploy", environment="production", github_environment="production", values=values
    )
    assert errors
    joined = "\n".join(errors)
    assert "互斥" in joined
    assert "connector" in joined and "direct_vpc" in joined
    assert resolved_vpc_binding_mode(values) is None


def test_no_vpc_binding_at_all_is_refused_naming_both_options() -> None:
    values = resolved("deploy", ODP_CLOUD_RUN_VPC_CONNECTOR="")
    for name in VPC_BINDING_MODES["direct_vpc"]:
        values[name] = ""
    errors = errors_for(
        "deploy", environment="production", github_environment="production", values=values
    )
    assert errors
    joined = "\n".join(errors)
    assert "二擇一" in joined
    for name in VPC_BINDING_VARIABLES:
        assert name in joined, f"a refusal that does not name {name} is not actionable"
    assert resolved_vpc_binding_mode(values) is None


def test_a_direct_vpc_deploy_still_requires_the_egress_mode() -> None:
    """Direct VPC without an egress mode would leave public destinations on public egress."""

    values = direct_vpc(ODP_CLOUD_RUN_VPC_EGRESS="")
    errors = errors_for(
        "deploy", environment="production", github_environment="production", values=values
    )
    assert errors
    assert "ODP_CLOUD_RUN_VPC_EGRESS" in "\n".join(errors)


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


def test_a_direct_vpc_deploy_receipt_records_the_mode_but_never_the_network_name(
    tmp_path: Path,
) -> None:
    network = "secret-prod-network-name-777"
    values = direct_vpc(ODP_PROD_VPC_NETWORK=network, ODP_PROD_VPC_SUBNETWORK=network)
    code, receipt = _run(
        tmp_path,
        values,
        "--scope",
        "deploy",
        "--environment",
        "production",
        "--github-environment",
        "production",
        "--release-sha",
        SHA,
        "--task-id",
        "ODP-PROD-RUNTIME-RELEASE-PATH-001",
    )
    assert code == 0
    assert receipt["admitted"] is True
    assert receipt["missing_variables"] == []
    assert receipt["vpc_binding"]["mode"] == "direct_vpc"
    assert receipt["vpc_binding"]["modes"]["direct_vpc"] == {
        "ODP_PROD_VPC_NETWORK": True,
        "ODP_PROD_VPC_SUBNETWORK": True,
    }
    assert receipt["vpc_binding"]["modes"]["connector"] == {
        "ODP_CLOUD_RUN_VPC_CONNECTOR": False,
    }
    assert set(receipt["variables_resolved"]) == set(declared_variables("deploy"))
    assert receipt["variables_resolved"]["ODP_CLOUD_RUN_VPC_CONNECTOR"] is False
    assert "direct_vpc" in receipt["summary_zh_tw"]
    assert network not in json.dumps(receipt, ensure_ascii=False)


def test_a_deploy_refused_for_a_missing_vpc_binding_writes_a_receipt_with_no_mode(
    tmp_path: Path,
) -> None:
    values = resolved("deploy", ODP_CLOUD_RUN_VPC_CONNECTOR="")
    for name in VPC_BINDING_MODES["direct_vpc"]:
        values[name] = ""
    code, receipt = _run(
        tmp_path,
        values,
        "--scope",
        "deploy",
        "--environment",
        "production",
        "--github-environment",
        "production",
    )
    assert code == 1
    assert receipt["admitted"] is False
    # Every unconditional variable resolved; the refusal is the binding alone.
    assert receipt["missing_variables"] == []
    assert receipt["vpc_binding"]["mode"] is None
    assert any("二擇一" in blocker for blocker in receipt["blockers_zh_tw"])
    assert "網路綁定" in receipt["summary_zh_tw"]


def test_a_build_receipt_carries_no_vpc_binding_section(tmp_path: Path) -> None:
    code, receipt = _run(
        tmp_path,
        resolved("build"),
        "--scope",
        "build",
        "--environment",
        "production",
        "--github-environment",
        "production-build",
    )
    assert code == 0
    assert receipt["vpc_binding"] is None
    assert "ODP_CLOUD_RUN_VPC_CONNECTOR" not in receipt["variables_resolved"]


# --------------------------------------------------------------------------
# Staging scope storage boundary validation
# --------------------------------------------------------------------------


def test_staging_scope_requires_foundation_variables_including_recovery_bundle_bucket() -> None:
    required = required_variables("staging")
    assert "ODP_STAGING_TERRAFORM_STATE_BUCKET" in required
    assert "ODP_STAGING_RECOVERY_BUNDLE_BUCKET" in required
    assert "ODP_STAGING_KMS_KEY_ID" in required
    assert "ODP_STAGING_DEPLOYER_SERVICE_ACCOUNT" in required


def test_staging_scope_fails_closed_when_recovery_and_state_buckets_are_identical() -> None:
    errors = errors_for(
        "staging",
        environment="staging",
        github_environment="staging",
        values=resolved(
            "staging",
            ODP_STAGING_TERRAFORM_STATE_BUCKET="odayplus-staging-bucket",
            ODP_STAGING_RECOVERY_BUNDLE_BUCKET="odayplus-staging-bucket",
        ),
    )
    assert errors
    joined = "\n".join(errors)
    assert "ODP_STAGING_RECOVERY_BUNDLE_BUCKET" in joined
    assert "ODP_STAGING_TERRAFORM_STATE_BUCKET" in joined
    assert "不得與" in joined
    assert "相同" in joined


@pytest.mark.parametrize("placeholder", ["placeholder", "changeme", "dummy", "todo", "PLACEHOLDER"])
def test_staging_scope_fails_closed_on_placeholder_recovery_bucket(placeholder: str) -> None:
    errors = errors_for(
        "staging",
        environment="staging",
        github_environment="staging",
        values=resolved(
            "staging",
            ODP_STAGING_TERRAFORM_STATE_BUCKET="odayplus-staging-state-bucket",
            ODP_STAGING_RECOVERY_BUNDLE_BUCKET=placeholder,
        ),
    )
    assert errors
    joined = "\n".join(errors)
    assert "placeholder" in joined or "佔位值" in joined


def test_staging_scope_admits_distinct_valid_buckets() -> None:
    errors = errors_for(
        "staging",
        environment="staging",
        github_environment="staging",
        values=resolved(
            "staging",
            ODP_STAGING_TERRAFORM_STATE_BUCKET="odayplus-staging-state-bucket",
            ODP_STAGING_RECOVERY_BUNDLE_BUCKET="odayplus-staging-recovery-bucket",
        ),
    )
    assert errors == []


def test_staging_scope_refusal_receipt_redacts_identical_bucket_values(tmp_path: Path) -> None:
    secret_bucket = "secret-staging-shared-bucket-name-999"
    values = resolved(
        "staging",
        ODP_STAGING_TERRAFORM_STATE_BUCKET=secret_bucket,
        ODP_STAGING_RECOVERY_BUNDLE_BUCKET=secret_bucket,
    )
    code, receipt = _run(
        tmp_path,
        values,
        "--scope",
        "staging",
        "--environment",
        "staging",
        "--github-environment",
        "staging",
        "--release-sha",
        SHA,
        "--task-id",
        "ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001",
    )
    assert code == 1
    assert receipt["admitted"] is False
    assert receipt["secret_values_redacted"] is True
    assert receipt["blockers_zh_tw"]

    # Presence-only guarantee: raw bucket values must never appear in the serialized receipt.
    serialized = json.dumps(receipt, ensure_ascii=False)
    assert secret_bucket not in serialized
    for name, value in values.items():
        if name == "ODP_CLOUD_RUN_VPC_EGRESS":
            continue
        assert value not in serialized


def test_staging_scope_refusal_receipt_redacts_placeholder_bucket_values(tmp_path: Path) -> None:
    secret_state_bucket = "secret-staging-state-bucket-only-888"
    placeholder_val = "changeme"
    values = resolved(
        "staging",
        ODP_STAGING_TERRAFORM_STATE_BUCKET=secret_state_bucket,
        ODP_STAGING_RECOVERY_BUNDLE_BUCKET=placeholder_val,
    )
    code, receipt = _run(
        tmp_path,
        values,
        "--scope",
        "staging",
        "--environment",
        "staging",
        "--github-environment",
        "staging",
        "--release-sha",
        SHA,
        "--task-id",
        "ODP-STAGING-RECOVERY-BUNDLE-STORAGE-001",
    )
    assert code == 1
    assert receipt["admitted"] is False
    assert receipt["secret_values_redacted"] is True
    assert receipt["blockers_zh_tw"]

    serialized = json.dumps(receipt, ensure_ascii=False)
    assert secret_state_bucket not in serialized
    assert placeholder_val not in serialized
    assert "佔位值" in receipt["blockers_zh_tw"][0]

