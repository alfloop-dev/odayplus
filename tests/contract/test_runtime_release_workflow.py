"""Run Runtime Release's actual publication shell with offline tool spies."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/deploy-dev.yml"
REPO = "region-docker.pkg.dev/project/repository"
SHA = "b" * 40
DIGEST = "sha256:" + "a" * 64

# Every external command is a spy. The workflow and helper themselves are real.
SPY = r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
state_file = pathlib.Path(os.environ["STATE"])
state = json.loads(state_file.read_text())
with open(os.environ["CALLS"], "a") as f:
    f.write(json.dumps({"tool": name, "args": args}) + "\n")
rc = 0
if name == "gcloud" and args[:4] == ["artifacts", "docker", "images", "describe"]:
    ref = args[4]
    if os.environ.get("MISSING_ATTESTATION") and ref.endswith(".att"):
        rc = 1
    elif ref in state["refs"]:
        print(state["refs"][ref])
    else:
        rc = 1
elif name == "docker" and args[0] == "push":
    state["refs"][args[1]] = os.environ["DIGEST"]
elif name == "cosign":
    op, image = args[0], args[-1]
    if op == "verify" and os.environ.get("VERIFY_FAIL"):
        rc = 31
    elif op == os.environ.get("FAIL_OPERATION") and "/worker" in image:
        state["failures"] += 1
        if state["failures"] <= int(os.environ["FAILURES"]):
            print(os.environ["ERROR"], file=sys.stderr)
            print("private-oidc-token-never-publish", file=sys.stderr)
            rc = 23
    if rc == 0 and op in ("sign", "attest"):
        repo = image.split("@")[0] if "@" in image else image.rsplit(":", 1)[0]
        suffix = "sig" if op == "sign" else "att"
        ref = repo + ":sha256-" + os.environ["DIGEST"].split(":")[1] + "." + suffix
        state["refs"][ref] = "sha256:" + ("c" if op == "sign" else "d") * 64
state_file.write_text(json.dumps(state))
sys.exit(rc)
'''


@pytest.fixture
def publication(tmp_path: Path):
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    (step,) = [s for s in jobs["build"]["steps"] if s.get("id") == "publish-images"]
    tools = tmp_path / "bin"
    tools.mkdir()
    for tool in ("cosign", "docker", "gcloud", "sleep"):
        path = tools / tool
        path.write_text(f"#!{sys.executable}\n" + SPY, encoding="utf-8")
        path.chmod(0o755)
    helper = tmp_path / "delivery_toolchain/security/sign_images.sh"
    helper.parent.mkdir(parents=True)
    helper.symlink_to(ROOT / "delivery_toolchain/security/sign_images.sh")
    sbom = tmp_path / "sbom.json"
    sbom.write_text('{}\n', encoding="utf-8")
    env = dict(
        os.environ,
        PATH=f"{tools}:{os.environ['PATH']}",
        GCP_REGION="region", GCP_PROJECT="project", GCP_AR_REPO="repository",
        API_SERVICE="api", WEB_SERVICE="web", WORKER_JOB="worker", SCHEDULER_JOB="scheduler",
        ODAY_RELEASE_SHA=SHA, IMAGE_TAG=f"release-{SHA}", SBOM_PATH=str(sbom),
        GITHUB_OUTPUT=str(tmp_path / "output"), STATE=str(tmp_path / "state.json"),
        CALLS=str(tmp_path / "calls.jsonl"), DIGEST=DIGEST,
        ERROR="fetching ambient OIDC credentials: invalid character 'u' looking for beginning of value",
        FAILURES="0", FAIL_OPERATION="",
    )

    def run(existing: int = 0, **overrides):
        env.update(overrides)
        refs = {}
        for component in ("api", "web", "worker", "scheduler")[:existing]:
            repo = f"{REPO}/{component}"
            refs[f"{repo}:release-{SHA}"] = DIGEST
            refs[f"{repo}:sha256-{'a' * 64}.sig"] = "sha256:" + "c" * 64
            refs[f"{repo}:sha256-{'a' * 64}.att"] = "sha256:" + "d" * 64
        Path(env["STATE"]).write_text(json.dumps({"refs": refs, "failures": 0}))
        result = subprocess.run(
            ["/bin/bash", "-c", step["run"]], cwd=tmp_path, env=env,
            capture_output=True, text=True, timeout=15, check=False,
        )
        calls = [json.loads(line) for line in Path(env["CALLS"]).read_text().splitlines()]
        output = Path(env["GITHUB_OUTPUT"])
        assert "private-oidc-token-never-publish" not in result.stdout + result.stderr
        return result, calls, output.read_text() if output.exists() else ""

    return run


@pytest.mark.parametrize("operation", ["sign", "attest"])
def test_new_images_retry_only_failed_oidc_operation_not_build_or_push(publication, operation):
    result, calls, output = publication(FAIL_OPERATION=operation, FAILURES="2")
    assert result.returncode == 0, result.stderr
    assert len([c for c in calls if c["tool"] == "docker" and c["args"][0] == "build"]) == 4
    assert len([c for c in calls if c["tool"] == "docker" and c["args"][0] == "push"]) == 4
    for op in ("sign", "verify", "attest"):
        selected = [c for c in calls if c["tool"] == "cosign" and c["args"][0] == op]
        assert len(selected) == (6 if op == operation else 4)
    assert [c["args"] for c in calls if c["tool"] == "sleep"] == [["2"], ["4"]]
    for component in ("api", "web", "worker", "scheduler"):
        assert f"{component}_image={REPO}/{component}@{DIGEST}" in output
    assert "signature_refs=" in output and "sbom_refs=" in output


@pytest.mark.parametrize("operation", ["sign", "attest"])
@pytest.mark.parametrize("error,attempts", [
    ("fetching ambient OIDC credentials: invalid character 'u' looking for beginning of value", 3),
    ("403 Forbidden", 1),
    ("x509: certificate signed by unknown authority", 1),
    ("registry publish: 503 Service Unavailable", 1),
])
def test_failed_worker_stops_without_handoff_or_publishing_remaining_images(
    publication, operation, error, attempts,
):
    result, calls, output = publication(FAIL_OPERATION=operation, FAILURES="20", ERROR=error)
    assert result.returncode == 23
    assert output == ""
    pushes = [c["args"][1] for c in calls if c["tool"] == "docker" and c["args"][0] == "push"]
    assert pushes == [f"{REPO}/{component}:release-{SHA}" for component in ("api", "worker")]
    failed_op = [c for c in calls if c["tool"] == "cosign" and c["args"][0] == operation
                 and "/worker" in c["args"][-1]]
    assert len(failed_op) == attempts


def test_existing_complete_tag_set_is_verified_without_signing_or_attesting(publication):
    result, calls, output = publication(existing=4)
    assert result.returncode == 0, result.stderr
    assert not any(c["tool"] in ("docker", "sleep") for c in calls)
    assert [c["args"][0] for c in calls if c["tool"] == "cosign"] == ["verify"] * 4
    assert "signature_refs=" in output and "sbom_refs=" in output


@pytest.mark.parametrize("existing", [1, 2, 3])
def test_partial_tags_are_held_without_build_sign_or_repair(publication, existing):
    result, calls, output = publication(existing=existing)
    assert result.returncode != 0
    assert "refusing to rebuild or move an existing tag" in result.stderr
    assert not any(c["tool"] in ("docker", "cosign", "sleep") for c in calls)
    assert output == ""


@pytest.mark.parametrize("existing", [0, 4])
def test_verification_failure_is_not_retried_or_accepted(publication, existing):
    result, calls, output = publication(existing=existing, VERIFY_FAIL="1")
    assert result.returncode == 31
    assert not any(c["tool"] == "sleep" for c in calls)
    assert len([c for c in calls if c["tool"] == "cosign" and c["args"][0] == "verify"]) == 1
    assert output == ""


def test_missing_supply_chain_digest_still_blocks_handoff(publication):
    result, _, output = publication(existing=4, MISSING_ATTESTATION="1")
    assert result.returncode != 0
    assert "no SBOM attestation artifact resolves" in result.stderr
    assert "signature_refs=" not in output and "sbom_refs=" not in output


def _workflow_bundle(environment: str, admitted_profile: str, secret: str) -> str:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    (step,) = [s for s in jobs["deploy"]["steps"] if s.get("id") == "live-deploy"]
    # Pin the actual Actions expression before modelling its && / || semantics.
    # In particular, use admission output, never a deploy-time profile input.
    assert step["env"]["ODP_DEV_ADMIN_CREDENTIAL_BUNDLE"] == (
        "${{ inputs.environment == 'dev' && needs.admission.outputs.release_profile == 'dev-admin' "
        "&& secrets.ODP_DEV_ADMIN_CREDENTIAL_BUNDLE || '' }}"
    )
    return secret if environment == "dev" and admitted_profile == "dev-admin" else ""


def test_deploy_workflow_only_consumes_matched_bundle_never_provisions() -> None:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    (step,) = [s for s in jobs["deploy"]["steps"] if s.get("id") == "live-deploy"]
    assert _workflow_bundle("dev", "dev-admin", "populated-secret") == "populated-secret"
    assert _workflow_bundle("dev", "full", "populated-secret") == ""
    assert _workflow_bundle("production", "dev-admin", "populated-secret") == ""
    assert _workflow_bundle("dev", "dev-admin", "") == ""
    assert step["run"] == "./product_ops/deployment/deploy_cloud_run_waji.sh"
    script = (ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh").read_text()
    assert "WebInvitationExecutor" not in script and "DevCredentialBundleExecutor" not in script
    assert "read_dev_credential_bundle" in script
    assert script.index("read_dev_credential_bundle") < script.index("DEPLOYMENT_COMMITTED=false")
    assert script.index("check_live_e2e_gate.py") < script.index("DEPLOYMENT_COMMITTED=true")
    assert "ODP_DEV_SMOKE_FOREGROUND_FD" not in WORKFLOW.read_text()
    assert script.index('promote_service_traffic "${WEB_SERVICE}"') < script.index('reply = channel.recv(16)')
    assert script.index('reply = channel.recv(16)') < script.index("DEPLOYMENT_COMMITTED=true")
    assert "trap handle_deployment_exit EXIT" in script


@pytest.mark.parametrize("environment,profile,workflow_injection,malformed,passes", [
    ("dev", "dev-admin", True, False, True),
    ("dev", "full", True, False, True),
    ("production", "full", True, False, True),
    ("dev", "dev-admin", True, True, False),
    ("dev", "dev-admin", False, False, True),
    ("dev", "dev-admin", False, True, False),
    ("dev", "full", False, False, False),
    ("staging", "dev-admin", False, False, False),
    ("production", "full", False, False, False),
])
def test_actual_deploy_bundle_preflight_refuses_before_cloud_without_decoding_shell(
    environment: str, profile: str, workflow_injection: bool, malformed: bool, passes: bool,
) -> None:
    import shlex

    from delivery_toolchain.release.provision_dev_smoke import AUTHORIZATION_ID, TENANT_ID
    bundle = {"schema_version": 1, "authorization_id": AUTHORIZATION_ID,
              "execution_id": "747efb4e-230d-4864-9bb9-194bec13045b",
              "repository": "alfloop-dev/odayplus", "environment": "dev", "tenant_id": TENANT_ID,
              "account_id": "5f0c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b",
              "username": "new.admin", "password": "private-exact-bundle-password"}
    script = (ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh").read_text()
    # Execute the unchanged real profile/preflight block. The downstream marker
    # models first cloud mutation; subprocess Python is the frozen test interpreter.
    block = script[script.index('ODP_RELEASE_PROFILE="'):script.index('if [ "${ODP_DEPLOY_ENV}" = "production" ]; then')]
    shell = ('set -euo pipefail\nrun_locked_python() { printf "bundle-preflight-invoked\\n"; ' +
             shlex.quote(sys.executable) + ' "$@"; }\n' + block +
             '\ntest "${ODP_RELEASE_PROFILE}" = "${EXPECTED_PROFILE}"\n'
             'test "${ODP_DEV_ADMIN_USERNAME:-}" = "${EXPECTED_USERNAME}"\n'
             'test "${ODP_DEV_ADMIN_PASSWORD:-}" = "${EXPECTED_PASSWORD}"\n'
             'printf "cloud-boundary-reached\\n"\n')
    stored_secret = "{private-malformed-password" if malformed else json.dumps(bundle)
    injected_bundle = (_workflow_bundle(environment, profile, stored_secret)
                       if workflow_injection else stored_secret)
    env = dict(os.environ, ODP_DEPLOY_ENV=environment, ODP_RELEASE_PROFILE=profile,
               ODP_DEV_ADMIN_CREDENTIAL_BUNDLE=injected_bundle,
               ODP_DEV_ADMIN_DENIED_OPERATOR_ROLE="cs-lead", EXPECTED_PROFILE=profile)
    # A scoped bundle needs no standing pair. Full keeps its original credentials
    # and acceptance profile even when the environment secret is populated.
    for key in ("ODP_DEV_ADMIN_USERNAME", "ODP_DEV_ADMIN_PASSWORD", "ODP_DEV_BOOTSTRAP_ADMIN_USERNAME",
                "ODP_DEV_BOOTSTRAP_ADMIN_PASSWORD", "ODP_DEV_SMOKE_FOREGROUND_FD"):
        env.pop(key, None)
    env["EXPECTED_USERNAME"] = "standing.admin" if profile == "full" else ""
    env["EXPECTED_PASSWORD"] = "private-standing-password" if profile == "full" else ""
    if profile == "full":
        env["ODP_DEV_ADMIN_USERNAME"] = env["EXPECTED_USERNAME"]
        env["ODP_DEV_ADMIN_PASSWORD"] = env["EXPECTED_PASSWORD"]
    env["ODP_DEV_ADMIN_INITIAL_PASSWORD"] = "private-stale-initial-password"
    result = subprocess.run(["bash", "-c", shell], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=20, check=False)
    assert (result.returncode == 0) is passes, result.stderr
    assert ("cloud-boundary-reached" in result.stdout) is passes
    if passes:
        assert ("bundle-preflight-invoked" in result.stdout) is bool(injected_bundle)
    for secret in (bundle["password"], "private-malformed-password", "private-standing-password",
                   env["ODP_DEV_ADMIN_INITIAL_PASSWORD"]):
        assert secret not in result.stdout + result.stderr
    assert bundle["password"] not in shell


@pytest.mark.parametrize("environment,profile,descriptor", [
    ("production", "dev-admin", "7"), ("dev", "full", "7"),
    ("dev", "dev-admin", "999999"), ("dev", "dev-admin", "private-invalid-value"),
])
def test_actual_foreground_channel_preflight_refuses_without_cloud_or_secret_echo(
    environment: str, profile: str, descriptor: str,
) -> None:
    script = (ROOT / "product_ops/deployment/deploy_cloud_run_waji.sh").read_text()
    block = script[script.index('ODP_RELEASE_PROFILE="'):script.index('case "${ODP_RELEASE_PROFILE}" in')]
    env = dict(os.environ, ODP_DEPLOY_ENV=environment, ODP_RELEASE_PROFILE=profile,
               ODP_DEV_SMOKE_FOREGROUND_FD=descriptor)
    env.pop("ODP_DEV_ADMIN_CREDENTIAL_BUNDLE", None)
    result = subprocess.run(["/bin/bash", "-c", 'set -euo pipefail\n' + block +
                             '\nprintf "cloud-boundary-reached\\n"'], env=env,
                            capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode != 0 and "cloud-boundary-reached" not in result.stdout
    assert "private-invalid-value" not in result.stdout + result.stderr
    assert "PROVISIONING_FOREGROUND_" in result.stderr
