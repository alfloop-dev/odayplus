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
