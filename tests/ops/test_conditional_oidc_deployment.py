"""Conditional OIDC deployment contract.

ODP-WEB-OIDC-OPTIONAL-DEPLOYMENT-001: password-first is the deployable default
and OIDC is an optional provider. These tests execute the real release-path
code -- the shell resolver, the Web environment serializer embedded in the
release script, and the fail-closed preflight -- rather than asserting on source
text, and they pin the two invariants that a split configuration would break:

* every consumer of "is OIDC on?" resolves it identically, and
* the service-identity token inputs stay present in both modes.

This file lives under ``tests/ops`` rather than ``infra/terraform/tests``
because only ``tests`` is on the pytest testpaths that CI runs; assertions about
Terraform HCL therefore read ``infra/terraform`` from here.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TERRAFORM_ROOT = ROOT / "infra" / "terraform"
DEPLOY_SCRIPT = ROOT / "product_ops" / "deployment" / "deploy_cloud_run_waji.sh"
AUTH_MODE_SCRIPT = ROOT / "product_ops" / "deployment" / "auth_mode.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-dev.yml"

_spec = importlib.util.spec_from_file_location(
    "conditional_oidc_live_validator",
    ROOT / "product_ops" / "deployment" / "validate_cloud_run_live_deployment.py",
)
assert _spec and _spec.loader
validator = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = validator
_spec.loader.exec_module(validator)

_contract_spec = importlib.util.spec_from_file_location(
    "conditional_oidc_terraform_validator",
    TERRAFORM_ROOT / "validate_contract.py",
)
assert _contract_spec and _contract_spec.loader
terraform_validator = importlib.util.module_from_spec(_contract_spec)
_contract_spec.loader.exec_module(terraform_validator)

COMPLETE_OIDC_ENV = {
    "ODP_WEB_OIDC_ISSUER": "https://accounts.example.com",
    "ODP_WEB_OIDC_CLIENT_ID": "web-client-id",
    "ODP_WEB_OIDC_CLIENT_SECRET_SECRET": "web-oidc-client-secret:7",
}

# (case id, environment) -> both resolvers must agree on every one of these.
RESOLUTION_CASES: tuple[tuple[str, dict[str, str]], ...] = (
    ("unset-defaults-to-local", {}),
    ("explicit-local", {"ODP_AUTH_MODE": "local"}),
    ("explicit-local-ignores-leftover-oidc-inputs", {"ODP_AUTH_MODE": "local", **COMPLETE_OIDC_ENV}),
    ("explicit-oidc", {"ODP_AUTH_MODE": "oidc", **COMPLETE_OIDC_ENV}),
    ("explicit-oidc-missing-client-secret", {
        "ODP_AUTH_MODE": "oidc",
        "ODP_WEB_OIDC_ISSUER": COMPLETE_OIDC_ENV["ODP_WEB_OIDC_ISSUER"],
        "ODP_WEB_OIDC_CLIENT_ID": COMPLETE_OIDC_ENV["ODP_WEB_OIDC_CLIENT_ID"],
    }),
    ("legacy-flag-true", {"ODP_AUTH_OIDC_ENABLED": "true", **COMPLETE_OIDC_ENV}),
    ("legacy-flag-false", {"ODP_AUTH_OIDC_ENABLED": "false"}),
    ("legacy-flag-false-with-issuer", {"ODP_AUTH_OIDC_ENABLED": "false", **COMPLETE_OIDC_ENV}),
    ("legacy-issuer-only", COMPLETE_OIDC_ENV),
    ("conflict-local-vs-enabled", {"ODP_AUTH_MODE": "local", "ODP_AUTH_OIDC_ENABLED": "true", **COMPLETE_OIDC_ENV}),
    ("conflict-oidc-vs-disabled", {"ODP_AUTH_MODE": "oidc", "ODP_AUTH_OIDC_ENABLED": "false", **COMPLETE_OIDC_ENV}),
    ("agreeing-mode-and-flag", {"ODP_AUTH_MODE": "oidc", "ODP_AUTH_OIDC_ENABLED": "true", **COMPLETE_OIDC_ENV}),
    ("invalid-mode", {"ODP_AUTH_MODE": "google"}),
    ("invalid-flag", {"ODP_AUTH_OIDC_ENABLED": "yes"}),
    # Normalisation. The preflight folded case and the release script did not,
    # so each of these was accepted by one half and rejected by the other.
    ("uppercase-mode-local", {"ODP_AUTH_MODE": "LOCAL"}),
    ("uppercase-mode-oidc", {"ODP_AUTH_MODE": "OIDC", **COMPLETE_OIDC_ENV}),
    ("mixed-case-mode", {"ODP_AUTH_MODE": "Local"}),
    ("padded-mode", {"ODP_AUTH_MODE": "  local  "}),
    ("uppercase-flag-true", {"ODP_AUTH_OIDC_ENABLED": "TRUE", **COMPLETE_OIDC_ENV}),
    ("uppercase-flag-false", {"ODP_AUTH_OIDC_ENABLED": "False"}),
    ("padded-flag", {"ODP_AUTH_OIDC_ENABLED": " true ", **COMPLETE_OIDC_ENV}),
    ("mixed-case-conflict", {
        "ODP_AUTH_MODE": "LOCAL",
        "ODP_AUTH_OIDC_ENABLED": "True",
        **COMPLETE_OIDC_ENV,
    }),
    ("blank-inputs-fall-through-to-local", {"ODP_AUTH_MODE": "  ", "ODP_AUTH_OIDC_ENABLED": ""}),
    # Placeholder semantics. A placeholder issuer is not a configuration, so it
    # must neither switch a pre-contract environment to OIDC nor satisfy an
    # explicitly selected OIDC mode.
    ("placeholder-issuer-only", {"ODP_WEB_OIDC_ISSUER": "placeholder"}),
    ("placeholder-issuer-uppercase", {"ODP_WEB_OIDC_ISSUER": "CHANGEME"}),
    ("explicit-oidc-placeholder-issuer", {
        "ODP_AUTH_MODE": "oidc",
        **COMPLETE_OIDC_ENV,
        "ODP_WEB_OIDC_ISSUER": "placeholder",
    }),
    ("explicit-oidc-placeholder-client-secret", {
        "ODP_AUTH_MODE": "oidc",
        **COMPLETE_OIDC_ENV,
        "ODP_WEB_OIDC_CLIENT_SECRET_SECRET": "todo",
    }),
)


def _run_shell_resolver(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = (
        "set -euo pipefail\n"
        f"source {AUTH_MODE_SCRIPT}\n"
        "resolve_auth_mode\n"
        'printf "%s %s\\n" "${ODP_AUTH_MODE}" "${ODP_AUTH_OIDC_ENABLED}"\n'
    )
    return subprocess.run(
        ["bash", "-c", script],
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def _shell_resolve(env: dict[str, str]) -> tuple[bool, str, str]:
    """Run the release script's resolver and report (accepted, mode, flag)."""
    completed = _run_shell_resolver(env)
    if completed.returncode != 0:
        return False, "", ""
    mode, flag = completed.stdout.split()
    return True, mode, flag


def _extract_heredoc(script: str, marker: str) -> str:
    """Return the body of the ``<<'PY'`` heredoc opened by ``marker``."""
    start = script.index(marker)
    body_start = script.index("\n", start) + 1
    body_end = script.index("\nPY\n", body_start)
    return script[body_start:body_end]


def _render_web_env(env: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """Execute the release script's Web environment serializer verbatim."""
    serializer = _extract_heredoc(
        DEPLOY_SCRIPT.read_text(encoding="utf-8"),
        'python3 - "${WEB_ENV_FILE}" "${API_URL}" "${API_SERVICE_AUDIENCE}" <<\'PY\'',
    )
    out = tmp_path / "web-env.json"
    base = {
        "ODP_DEPLOY_ENV": "dev",
        "ODAY_RELEASE_SHA": "a" * 40,
        "ODP_REQUIRE_LIVE_DATA": "true",
        "ODP_DATA_BINDING_MODE": "live",
        "ODP_PRODUCT_MODE": "poc",
        "ODP_WEB_BASE_URL": "https://web.example.com",
    }
    subprocess.run(
        [sys.executable, "-c", serializer, str(out), "https://api.example.com", "https://api.example.com"],
        env={"PATH": "/usr/bin:/bin", **base, **env},
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return json.loads(out.read_text(encoding="utf-8"))


PAIRS = {"{": "}", "(": ")", "[": "]"}


def _hcl_block(text: str, header: str) -> str:
    """Return the balanced body of the block or expression opened by ``header``.

    Terraform blocks open with ``{`` and the ``merge``/``concat``/``toset``
    expressions open with ``(``, so the delimiter is taken from whichever comes
    first after the header.
    """
    start = text.index(header)
    opener_index = min(
        (index for index in (text.find(char, start) for char in PAIRS) if index >= 0),
        default=-1,
    )
    assert opener_index >= 0, f"no opening delimiter after {header!r}"
    opener = text[opener_index]
    closer = PAIRS[opener]
    depth = 0
    for index in range(opener_index, len(text)):
        if text[index] == opener:
            depth += 1
        elif text[index] == closer:
            depth -= 1
            if depth == 0:
                return text[opener_index + 1:index]
    raise AssertionError(f"unbalanced HCL block for {header!r}")


# --------------------------------------------------------------------------
# One resolved mode, shared by every consumer
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case_id, env", RESOLUTION_CASES, ids=[case[0] for case in RESOLUTION_CASES])
def test_shell_and_preflight_resolvers_agree(case_id: str, env: dict[str, str]) -> None:
    """The release script and the preflight must never disagree about the mode.

    A disagreement is what produces a revision with the OIDC client secret bound
    but no issuer injected, so the two implementations are pinned to each other
    across the whole input space, not just the happy path.
    """
    shell_accepted, shell_mode, shell_flag = _shell_resolve(env)
    python_mode, python_error = validator.resolve_auth_mode(env)

    if shell_accepted:
        assert python_error is None, f"{case_id}: preflight rejected what the deploy accepts"
        assert shell_mode == python_mode, case_id
        assert shell_flag == ("true" if shell_mode == "oidc" else "false"), case_id
    else:
        # The shell also refuses an incomplete OIDC configuration, which the
        # preflight reports through its own per-variable checks instead. Either
        # way the release must not get through the gate, so assert on the gate's
        # actual verdict rather than on how it happens to be spelled. Only the
        # auth checks count here: the shared fixture leaves unrelated checks
        # failing, and those would make this assertion vacuous.
        failing = [
            name
            for name, check in _named_checks(_preflight_env(**env)).items()
            if not check.ok
            and (name == "auth-mode" or name.startswith(("oidc-config:", "oidc-secret-reference:")))
        ]
        assert python_error is not None or failing, (
            f"{case_id}: deploy rejected a configuration the preflight approves"
        )


@pytest.mark.parametrize("case_id, env", RESOLUTION_CASES, ids=[case[0] for case in RESOLUTION_CASES])
def test_both_resolvers_reject_for_the_same_stated_reason(case_id: str, env: dict[str, str]) -> None:
    """Agreeing on the verdict is not enough; they must agree on the reason.

    A shared verdict reached from different rules drifts back apart on the next
    input, so where the preflight names a reason the release script must fail
    with that same reason rather than one of its own.
    """
    _, python_error = validator.resolve_auth_mode(env)
    if python_error is None:
        pytest.skip("configuration is not rejected by the preflight resolver")
    completed = _run_shell_resolver(env)
    assert completed.returncode != 0, f"{case_id}: deploy accepted what the preflight rejects"
    assert python_error in completed.stderr, f"{case_id}: {completed.stderr.strip()!r}"


def test_the_two_resolvers_share_one_placeholder_vocabulary() -> None:
    """Placeholder tokens are duplicated across languages, so pin them together.

    The lists diverging is exactly how ``ODP_WEB_OIDC_ISSUER=placeholder`` came
    to mean "OIDC is on" to the deploy and "OIDC is off" to the preflight.
    """
    shell_tokens = set(
        re.search(
            r'^AUTH_MODE_PLACEHOLDER_VALUES="([^"]*)"',
            AUTH_MODE_SCRIPT.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        .group(1)
        .split()
    )
    # The shell list cannot carry the empty string through word splitting; it
    # rejects an empty value before consulting the list at all.
    assert shell_tokens == validator.PLACEHOLDER_VALUES - {""}
    assert _shell_resolve({"ODP_WEB_OIDC_ISSUER": ""})[1] == "local"


def test_the_resolver_needs_no_external_commands() -> None:
    """It runs before the release script has proven anything about the runner.

    Normalising through ``tr``/``sed`` made the resolver depend on PATH, which
    turns a minimal environment into "command not found" partway through the
    deploy rather than a clean auth-mode decision.
    """
    bash = shutil.which("bash")
    assert bash, "bash is required to run the release-path resolver"
    completed = subprocess.run(
        [
            bash,
            "-c",
            f"source {AUTH_MODE_SCRIPT}\n"
            "resolve_auth_mode\n"
            'printf "%s %s\\n" "${ODP_AUTH_MODE}" "${ODP_AUTH_OIDC_ENABLED}"\n',
        ],
        # An empty PATH, so the resolver cannot reach any helper binary.
        env={"PATH": "", "ODP_AUTH_MODE": "  LOCAL  "},
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["local", "false"]


def test_normalisation_is_shared_by_both_resolvers() -> None:
    """Case and padding are not a configuration difference."""
    for raw, expected in (("LOCAL", "local"), ("Local", "local"), ("  local  ", "local")):
        assert _shell_resolve({"ODP_AUTH_MODE": raw}) == (True, expected, "false")
        assert validator.resolve_auth_mode({"ODP_AUTH_MODE": raw}) == (expected, None)
    assert _shell_resolve({"ODP_AUTH_MODE": "OIDC", **COMPLETE_OIDC_ENV}) == (True, "oidc", "true")
    assert _shell_resolve({"ODP_AUTH_OIDC_ENABLED": "TRUE", **COMPLETE_OIDC_ENV}) == (
        True,
        "oidc",
        "true",
    )


def test_a_placeholder_issuer_never_turns_oidc_on() -> None:
    """A placeholder is not a configuration in either half of the resolver."""
    for token in sorted(validator.PLACEHOLDER_VALUES - {""}):
        env = {"ODP_WEB_OIDC_ISSUER": token}
        assert _shell_resolve(env) == (True, "local", "false"), token
        assert validator.resolve_auth_mode(env) == ("local", None), token
    # And it does not satisfy an explicitly selected OIDC mode either.
    env = {"ODP_AUTH_MODE": "oidc", **COMPLETE_OIDC_ENV, "ODP_WEB_OIDC_ISSUER": "placeholder"}
    assert _shell_resolve(env)[0] is False
    by_name = _named_checks(_preflight_env(**env))
    assert by_name["oidc-config:ODP_WEB_OIDC_ISSUER"].ok is False


def test_password_first_is_the_default() -> None:
    assert _shell_resolve({}) == (True, "local", "false")
    assert validator.resolve_auth_mode({}) == ("local", None)


def test_contradicting_mode_and_legacy_flag_fail_closed() -> None:
    """A split configuration is rejected rather than resolved to either half."""
    split = {"ODP_AUTH_MODE": "local", "ODP_AUTH_OIDC_ENABLED": "true", **COMPLETE_OIDC_ENV}
    assert _shell_resolve(split)[0] is False
    _, error = validator.resolve_auth_mode(split)
    assert error is not None and "conflicts with" in error


def test_oidc_mode_requires_the_complete_client_registration() -> None:
    """OIDC stays strictly validated when it is the selected mode."""
    for missing in COMPLETE_OIDC_ENV:
        env = {"ODP_AUTH_MODE": "oidc", **{k: v for k, v in COMPLETE_OIDC_ENV.items() if k != missing}}
        accepted, _, _ = _shell_resolve(env)
        assert accepted is False, f"OIDC mode accepted a release missing {missing}"


def test_legacy_issuer_only_environments_keep_deploying_oidc() -> None:
    """Environments predating ODP_AUTH_MODE must not silently lose OIDC."""
    assert _shell_resolve(dict(COMPLETE_OIDC_ENV)) == (True, "oidc", "true")


# --------------------------------------------------------------------------
# Cloud Run receives only the enabled provider's configuration
# --------------------------------------------------------------------------


def test_local_mode_web_env_carries_no_oidc_configuration(tmp_path: Path) -> None:
    payload = _render_web_env({"ODP_AUTH_MODE": "local", "ODP_AUTH_OIDC_ENABLED": "false"}, tmp_path)
    assert payload["ODP_AUTH_MODE"] == "local"
    assert payload["ODP_AUTH_OIDC_ENABLED"] == "false"
    assert not [name for name in payload if name.startswith("ODP_WEB_OIDC_")]


def test_oidc_mode_web_env_carries_the_full_client_configuration(tmp_path: Path) -> None:
    payload = _render_web_env(
        {"ODP_AUTH_MODE": "oidc", "ODP_AUTH_OIDC_ENABLED": "true", **COMPLETE_OIDC_ENV},
        tmp_path,
    )
    assert payload["ODP_AUTH_MODE"] == "oidc"
    assert payload["ODP_AUTH_OIDC_ENABLED"] == "true"
    assert payload["ODP_WEB_OIDC_ISSUER"] == COMPLETE_OIDC_ENV["ODP_WEB_OIDC_ISSUER"]
    assert payload["ODP_WEB_OIDC_CLIENT_ID"] == COMPLETE_OIDC_ENV["ODP_WEB_OIDC_CLIENT_ID"]
    assert payload["ODP_WEB_OIDC_ALLOWED_ALGS"] == "RS256"


def test_web_env_follows_the_resolved_flag_not_a_second_signal(tmp_path: Path) -> None:
    """A stale issuer in the environment cannot re-enable OIDC on its own.

    The serializer used to key off ``ODP_WEB_OIDC_ISSUER`` while the secret
    binding keyed off the flag, so a leftover issuer produced a Web revision
    advertising OIDC with no client secret mounted.
    """
    payload = _render_web_env(
        {"ODP_AUTH_MODE": "local", "ODP_AUTH_OIDC_ENABLED": "false", **COMPLETE_OIDC_ENV},
        tmp_path,
    )
    assert payload["ODP_AUTH_OIDC_ENABLED"] == "false"
    assert "ODP_WEB_OIDC_ISSUER" not in payload


def test_release_payload_carries_the_canonical_web_origin_in_both_modes(tmp_path: Path) -> None:
    """The Web revision must never ship without ``ODP_WEB_BASE_URL``.

    Terraform already injects it unconditionally, but the workflow deploys
    through the release script, whose serializer omitted it entirely. That
    produced a production Web revision with no canonical origin, which
    ``resolveWebBaseUrl`` fails closed on, so the two paths must agree.
    """
    for mode_env in (
        {"ODP_AUTH_MODE": "local", "ODP_AUTH_OIDC_ENABLED": "false"},
        {"ODP_AUTH_MODE": "oidc", "ODP_AUTH_OIDC_ENABLED": "true", **COMPLETE_OIDC_ENV},
    ):
        payload = _render_web_env(
            {**mode_env, "ODP_WEB_BASE_URL": "https://ops.example.com"}, tmp_path
        )
        assert payload["ODP_WEB_BASE_URL"] == "https://ops.example.com"


def test_release_payload_refuses_to_omit_the_web_origin(tmp_path: Path) -> None:
    """An absent origin aborts the release instead of silently deploying."""
    serializer = _extract_heredoc(
        DEPLOY_SCRIPT.read_text(encoding="utf-8"),
        'python3 - "${WEB_ENV_FILE}" "${API_URL}" "${API_SERVICE_AUDIENCE}" <<\'PY\'',
    )
    completed = subprocess.run(
        [sys.executable, "-c", serializer, str(tmp_path / "web-env.json"), "https://api", "https://api"],
        env={
            "PATH": "/usr/bin:/bin",
            "ODP_DEPLOY_ENV": "dev",
            "ODAY_RELEASE_SHA": "a" * 40,
            "ODP_REQUIRE_LIVE_DATA": "true",
            "ODP_DATA_BINDING_MODE": "live",
            "ODP_PRODUCT_MODE": "poc",
            "ODP_AUTH_MODE": "local",
            "ODP_AUTH_OIDC_ENABLED": "false",
        },
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert completed.returncode != 0
    assert "ODP_WEB_BASE_URL" in completed.stderr


def test_web_secret_bindings_are_gated_on_the_same_resolved_flag() -> None:
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    binding = text[text.index('WEB_SECRET_BINDINGS="'):text.index("# gcloud's shortcut")]
    assert 'WEB_SECRET_BINDINGS="ODP_WEB_SESSION_SECRET=' in binding
    assert (
        'if [ "${ODP_AUTH_OIDC_ENABLED}" = "true" ]; then\n'
        '  WEB_SECRET_BINDINGS+=",ODP_WEB_OIDC_CLIENT_SECRET='
        '${ODP_WEB_OIDC_CLIENT_SECRET_SECRET}"\n'
        "fi\n"
    ) in binding


def test_release_script_resolves_the_mode_before_the_preflight_runs() -> None:
    """The preflight must see the same resolved values the deploy will use."""
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert text.index("source product_ops/deployment/auth_mode.sh") < text.index("resolve_auth_mode\n")
    assert text.index("resolve_auth_mode\n") < text.index(
        "validate_cloud_run_live_deployment.py preflight"
    )


# --------------------------------------------------------------------------
# Fail-closed preflight
# --------------------------------------------------------------------------


def _preflight_env(**overrides: str) -> dict[str, str]:
    env = {name: f"configured-{name.lower()}" for name in validator.REQUIRED_PUBLIC_CONFIG}
    env.update(
        {name: f"secret-name-{index}:latest" for index, name in enumerate(validator.REQUIRED_SECRET_REFERENCES)}
    )
    env.update(validator.REQUIRED_RUNTIME_VALUES)
    env["ODP_DEPLOY_ENV"] = "dev"
    env["ODP_FORECAST_ENGINE"] = "statsforecast"
    env["ODP_FORECAST_MODEL"] = "seasonal_naive"
    env["ODP_SCHEDULED_INGESTION_TENANT_ID"] = "tenant-dev"
    env["ODP_TENANT_ID"] = "tenant-dev"
    env.update(overrides)
    return env


def _named_checks(env: dict[str, str]) -> dict[str, object]:
    checks = validator.preflight_checks(
        env=env,
        expected_environment="dev",
        expected_sha=env.get("ODAY_RELEASE_SHA", ""),
        root=ROOT,
    )
    return {check.name: check for check in checks}


def test_preflight_passes_without_any_oidc_configuration() -> None:
    """Password-first deployments must not be blocked by absent OIDC inputs."""
    by_name = _named_checks(_preflight_env())
    assert by_name["auth-mode"].ok
    assert by_name["auth-mode"].detail == "local"
    assert not [name for name in by_name if name.startswith(("oidc-config:", "oidc-secret-reference:"))]


def test_preflight_still_requires_complete_oidc_when_it_is_selected() -> None:
    by_name = _named_checks(_preflight_env(ODP_AUTH_MODE="oidc"))
    assert by_name["auth-mode"].ok
    assert by_name["oidc-config:ODP_WEB_OIDC_ISSUER"].ok is False
    assert by_name["oidc-config:ODP_WEB_OIDC_CLIENT_ID"].ok is False
    assert by_name["oidc-secret-reference:ODP_WEB_OIDC_CLIENT_SECRET_SECRET"].ok is False

    by_name = _named_checks(
        _preflight_env(
            ODP_AUTH_MODE="oidc",
            ODP_WEB_OIDC_ISSUER="https://accounts.example.com",
            ODP_WEB_OIDC_CLIENT_ID="web-client-id",
            ODP_WEB_OIDC_CLIENT_SECRET_SECRET="web-oidc-client-secret:7",
        )
    )
    assert by_name["oidc-config:ODP_WEB_OIDC_ISSUER"].ok
    assert by_name["oidc-config:ODP_WEB_OIDC_CLIENT_ID"].ok
    assert by_name["oidc-secret-reference:ODP_WEB_OIDC_CLIENT_SECRET_SECRET"].ok


def test_preflight_rejects_a_split_auth_configuration() -> None:
    by_name = _named_checks(_preflight_env(ODP_AUTH_MODE="local", ODP_AUTH_OIDC_ENABLED="true"))
    assert by_name["auth-mode"].ok is False


def test_preflight_requires_the_canonical_web_origin_in_local_mode() -> None:
    """The fail-closed gate covers the origin, not only the OIDC inputs."""
    assert "ODP_WEB_BASE_URL" in validator.REQUIRED_PUBLIC_CONFIG
    assert "ODP_WEB_BASE_URL" not in validator.OIDC_REQUIRED_PUBLIC_CONFIG
    by_name = _named_checks(_preflight_env(ODP_WEB_BASE_URL=""))
    assert by_name["auth-mode"].detail == "local"
    assert by_name["config:ODP_WEB_BASE_URL"].ok is False


def test_service_identity_inputs_stay_required_in_every_mode() -> None:
    """ODP_AUTH_* verify the deployment smoke token, so they are not OIDC-only."""
    for name in ("ODP_AUTH_ISSUER", "ODP_AUTH_AUDIENCES", "ODP_AUTH_JWKS_URI"):
        assert name in validator.REQUIRED_PUBLIC_CONFIG
        assert name not in validator.OIDC_REQUIRED_PUBLIC_CONFIG
    assert "ODP_WEB_OIDC_CLIENT_SECRET_SECRET" not in validator.REQUIRED_SECRET_REFERENCES
    assert "ODP_WEB_SESSION_SECRET_SECRET" in validator.REQUIRED_SECRET_REFERENCES
    by_name = _named_checks(_preflight_env(ODP_AUTH_ISSUER=""))
    assert by_name["config:ODP_AUTH_ISSUER"].ok is False


# --------------------------------------------------------------------------
# Terraform
# --------------------------------------------------------------------------


def test_terraform_contract_validation_passes() -> None:
    assert terraform_validator.validate(TERRAFORM_ROOT) == []


def test_terraform_is_formatted() -> None:
    if shutil.which("terraform") is None:
        pytest.skip("terraform CLI is not available")
    completed = subprocess.run(
        ["terraform", "fmt", "-check", "-recursive", str(TERRAFORM_ROOT)],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout


def test_api_runtime_auth_env_is_injected_in_every_mode() -> None:
    """The API keeps its issuer/JWKS/audience bindings when OIDC is off."""
    main_tf = (TERRAFORM_ROOT / "main.tf").read_text(encoding="utf-8")
    block = _hcl_block(main_tf, "fixed_runtime_env = ")
    for name in ("ODP_AUTH_ISSUER", "ODP_AUTH_JWKS_URI", "ODP_AUTH_AUDIENCES"):
        assert re.search(rf"^\s*{name}\s*=", block, re.MULTILINE), name
    assert "oidc_enabled" not in block, "API auth env must not be gated on the OIDC mode"


def test_api_runtime_receives_the_resolved_auth_mode() -> None:
    """The API is told the mode; it must not infer one from leftover inputs.

    ``modules.opsboard.auth.config.config_from_env`` treats ODP_AUTH_MODE as
    the authoritative "is the OIDC provider on?" gate. A release that resolved
    the mode for the Web service and the preflight but never forwarded it to
    the API left that gate reading an absent variable, so an environment that
    had switched to password-first kept verifying OIDC tokens against its old
    issuer (ODP-WEB-LOCAL-AUTH-API-TRUST-001).
    """
    main_tf = (TERRAFORM_ROOT / "main.tf").read_text(encoding="utf-8")
    block = _hcl_block(main_tf, "fixed_runtime_env = ")
    assert re.search(r"^\s*ODP_AUTH_MODE\s*=\s*var\.auth_mode", block, re.MULTILINE)

    deploy_text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    api_env_block = deploy_text.partition('python3 - "${API_ENV_FILE}"')[2].partition("PY\n")[0]
    assert '"ODP_AUTH_MODE"' in api_env_block
    # The mode is forwarded alone. Both halves of the pair could only reach the
    # API split by a later edit, and a split pair is a configuration the
    # boundary refuses rather than resolves.
    assert '"ODP_AUTH_OIDC_ENABLED"' not in api_env_block


def test_declared_runtime_env_names_match_what_is_injected() -> None:
    """Every name the contract claims Terraform owns must actually be set."""
    main_tf = (TERRAFORM_ROOT / "main.tf").read_text(encoding="utf-8")
    declared = set(re.findall(r'"([A-Z0-9_]+)"', _hcl_block(main_tf, "fixed_runtime_env_names = toset(")))
    injected = set(re.findall(r"^\s*([A-Z0-9_]+)\s*=", _hcl_block(main_tf, "fixed_runtime_env = "), re.MULTILINE))
    assert declared == injected


def test_web_oidc_env_and_secret_are_gated_on_the_oidc_mode() -> None:
    main_tf = (TERRAFORM_ROOT / "main.tf").read_text(encoding="utf-8")
    web_env = _hcl_block(main_tf, "web_plain_env = merge(")
    unconditional, _, conditional = web_env.partition("local.oidc_enabled ?")
    assert "ODP_AUTH_MODE" in unconditional
    assert "ODP_WEB_BASE_URL" in unconditional
    # The Web runtime is told the mode explicitly in both directions, matching
    # what the release script writes, so "OIDC is off" is never inferred from an
    # absent variable.
    assert "ODP_AUTH_OIDC_ENABLED                = tostring(local.oidc_enabled)" in unconditional
    for name in ("ODP_WEB_OIDC_CLIENT_ID", "ODP_WEB_OIDC_ISSUER"):
        assert name not in unconditional, name
        assert name in conditional, name
    assert re.search(
        r"web_oidc_secret_refs\s*=\s*local\.oidc_enabled &&", main_tf
    ), "the Web OIDC client secret must only be mounted in OIDC mode"


def test_web_base_url_is_a_production_requirement_in_every_mode() -> None:
    """A local-mode production deploy must not pass with an empty web origin."""
    main_tf = (TERRAFORM_ROOT / "main.tf").read_text(encoding="utf-8")
    values = _hcl_block(main_tf, "production_contract_values = concat(")
    unconditional = values.partition("local.oidc_enabled ?")[0]
    assert "var.web_base_url" in unconditional
    assert "local.auth_issuer" in unconditional
    assert "local.auth_jwks_uri" in unconditional

    checks_tf = (TERRAFORM_ROOT / "checks.tf").read_text(encoding="utf-8")
    assert re.search(
        r"condition\s*=\s*!local\.is_prod \|\| startswith\(var\.web_base_url, \"https://\"\)",
        checks_tf,
    )


def test_production_requires_the_service_identity_contract_in_every_mode() -> None:
    checks_tf = (TERRAFORM_ROOT / "checks.tf").read_text(encoding="utf-8")
    identity = _hcl_block(checks_tf, 'check "production_identity_contract"')
    ungated, _, gated = identity.partition("local.oidc_enabled")
    for expression in ("local.auth_issuer", "local.auth_jwks_uri", "local.auth_audiences"):
        assert expression in ungated, expression
    assert "var.oidc_issuer" in gated


def test_web_oidc_client_secret_ref_production_contract_is_gated_on_oidc_mode() -> None:
    """In local mode, leftover OIDC client secret ref must not enter production_contract_values."""
    main_tf = (TERRAFORM_ROOT / "main.tf").read_text(encoding="utf-8")
    values = _hcl_block(main_tf, "production_contract_values = concat(")
    assert re.search(
        r"\(?\s*local\.oidc_enabled\s*&&\s*var\.web_oidc_client_secret_ref\s*!=\s*null\s*\)?\s*\?\s*\[",
        values,
    ), "web_oidc_client_secret_ref in production_contract_values must be gated on local.oidc_enabled"


def test_auth_mode_variable_defaults_to_password_first() -> None:
    variables = (TERRAFORM_ROOT / "variables.tf").read_text(encoding="utf-8")
    auth_mode = _hcl_block(variables, 'variable "auth_mode"')
    assert re.search(r'default\s*=\s*"local"', auth_mode)
    assert re.search(r'contains\(\["local", "oidc"\], var\.auth_mode\)', auth_mode)
    for name in ("service_auth_issuer", "service_auth_jwks_uri", "service_auth_audiences"):
        assert f'variable "{name}"' in variables, name


# --------------------------------------------------------------------------
# Workflow and documentation
# --------------------------------------------------------------------------


def test_deploy_workflow_passes_both_the_mode_and_its_legacy_alias() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    envs = [job.get("env", {}) for job in workflow["jobs"].values()]
    deploy_env = next(env for env in envs if "ODP_WEB_OIDC_CLIENT_SECRET_SECRET" in env)
    assert deploy_env["ODP_AUTH_MODE"] == "${{ vars.ODP_AUTH_MODE }}"
    assert deploy_env["ODP_AUTH_OIDC_ENABLED"] == "${{ vars.ODP_AUTH_OIDC_ENABLED }}"
    for name in ("ODP_AUTH_ISSUER", "ODP_AUTH_AUDIENCES", "ODP_AUTH_JWKS_URI"):
        assert name in deploy_env, f"{name} verifies the smoke token in every mode"


def test_deploy_workflow_supplies_the_canonical_web_origin() -> None:
    """A variable the release script requires must be bound by the job.

    The deploy job reads environment-scoped ``vars.*``; a name that is never
    listed here arrives unset rather than empty, so the omission is invisible
    until the Web revision is already live.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    envs = [job.get("env", {}) for job in workflow["jobs"].values()]
    deploy_env = next(env for env in envs if "ODP_WEB_OIDC_CLIENT_SECRET_SECRET" in env)
    assert deploy_env["ODP_WEB_BASE_URL"] == "${{ vars.ODP_WEB_BASE_URL }}"


def test_documentation_states_the_single_auth_mode_contract() -> None:
    web_readme = (ROOT / "apps" / "web" / "README.md").read_text(encoding="utf-8")
    all_modes_table = web_readme.partition("Required environment (all modes):")[2].partition("###")[0]
    assert "`ODP_AUTH_MODE`" in all_modes_table
    assert "`ODP_WEB_BASE_URL`" in all_modes_table
    assert "ODP_WEB_OIDC_" not in all_modes_table

    oidc_mode_table = web_readme.partition("### OIDC mode (`ODP_AUTH_MODE=oidc`)")[2].partition("Optional OIDC environment:")[0]
    assert "`ODP_WEB_OIDC_CLIENT_SECRET`" in oidc_mode_table
    assert "omitted for a public PKCE client" not in web_readme

    guide = (ROOT / "docs" / "deployment" / "GCP_DEPLOY_GUIDE.md").read_text(encoding="utf-8")
    assert "`ODP_AUTH_MODE`" in guide
    assert "product_ops/deployment/auth_mode.sh" in guide
    # The guide claims one resolver, so it has to state the two rules that make
    # the claim true rather than leaving them implicit in two implementations.
    assert "lower-cased before they are compared" in guide
    assert "placeholder token" in guide
    assert "`ODP_WEB_BASE_URL`" in guide

    terraform_readme = (TERRAFORM_ROOT / "README.md").read_text(encoding="utf-8")
    assert "service_auth_issuer" in terraform_readme
    assert 'when `auth_mode = "oidc"`' in terraform_readme
