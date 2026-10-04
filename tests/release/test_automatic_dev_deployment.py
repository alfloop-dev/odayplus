"""Dev automation composes real admission with authenticated CI/deploy facts."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from delivery_toolchain.release import automatic_dev as auto
from delivery_toolchain.release.check_release_phase import phase_errors
from tests.release.test_release_manifest import REAL_CANDIDATE_SHA, sources_off_manifest

SHA = REAL_CANDIDATE_SHA
REPO = "alfloop-dev/odayplus"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def context(monkeypatch, tmp_path):
    values = {
        "GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/dev", "GITHUB_SHA": SHA,
        "GITHUB_WORKFLOW_REF": f"{REPO}/{auto.WORKFLOW}@refs/heads/dev",
        "GITHUB_RUN_ID": "200", "GITHUB_OUTPUT": str(tmp_path / "output"),
        "ODP_CLOUD_RUN_VPC_EGRESS": "all-traffic",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def ci():
    return {"event": "push", "head_branch": "dev", "head_sha": SHA,
            "path": auto.CI_WORKFLOW, "repository": {"full_name": REPO},
            "head_repository": {"full_name": REPO}, "status": "completed",
            "conclusion": "success"}


def branch():
    return {"protected": True, "commit": {"sha": SHA}}


@pytest.mark.parametrize("target", ["staging", "production", "", "DEV"])
def test_automatic_authority_never_crosses_environment(context, target):
    assert auto.context_errors(context, candidate=SHA, target=target)
    assert phase_errors(phase="auto", release_sha=SHA, environment=target,
                        images={}, lease_supplied=False)


@pytest.mark.parametrize("key,value", [
    ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REF", "refs/heads/main"),
    ("GITHUB_SHA", "a" * 40), ("GITHUB_WORKFLOW_REF", "fork/repo/workflow@dev"),
])
def test_automatic_context_rejects_wrong_workflow_or_sha(context, key, value):
    context[key] = value
    assert auto.context_errors(context, candidate=SHA, target="dev")


@pytest.mark.parametrize("change", [
    {"event": "pull_request"}, {"head_branch": "main"}, {"head_sha": "a" * 40},
    {"path": ".github/workflows/fake.yml"}, {"repository": {"full_name": "fork/repo"}},
    {"head_repository": {"full_name": "fork/repo"}}, {"status": "in_progress"},
    {"conclusion": "failure"}, {"conclusion": "cancelled"}, {"conclusion": "skipped"},
])
def test_ci_cannot_be_forged_by_a_green_unrelated_run(change):
    assert auto.ci_errors({**ci(), **change}, branch(), candidate=SHA, repo=REPO)


def test_only_protected_current_successful_dev_ci_passes(context):
    assert auto.context_errors(context, candidate=SHA, target="dev") == []
    assert auto.ci_errors(ci(), branch(), candidate=SHA, repo=REPO) == []
    assert auto.ci_errors(ci(), {**branch(), "protected": False}, candidate=SHA, repo=REPO)
    assert auto.ci_errors(ci(), {"protected": True, "commit": {"sha": "a" * 40}},
                          candidate=SHA, repo=REPO)


@pytest.mark.parametrize("target", ["staging", "production"])
def test_manual_deploy_still_requires_signed_lease(target):
    errors = phase_errors(phase="deploy", release_sha=SHA, environment=target,
                          images={}, lease_supplied=False)
    assert any("lease" in error for error in errors)


@pytest.fixture
def manifest(context):
    components = {name: {"image": f"registry.example.invalid/{name}@sha256:" + str(i) * 64}
                  for i, name in enumerate(auto.COMPONENTS, 1)}
    return sources_off_manifest(components=components, release_profile={
        "name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed",
    })


def admit(tmp_path, manifest, **kwargs):
    path = tmp_path / "RELEASE_MANIFEST.json"
    path.write_text(json.dumps(manifest))
    args = {"candidate": manifest["candidate_sha"], "digest": manifest["manifest_digest"],
            "images": {k: v["image"] for k, v in manifest["components"].items()}, "ci_run_id": "100"}
    return auto.automatic_admission(path, **{**args, **kwargs})


def test_real_manifest_is_admitted_without_fabricating_human_or_live_proof(tmp_path, manifest):
    result = admit(tmp_path, manifest)
    assert result["authority"] == auto.AUTHORITY
    assert result["release_profile"] == "dev-admin"
    assert result["live_deployment_verified"] is False
    assert not {"lease", "human_signoff", "approved_by"} & result.keys()


@pytest.mark.parametrize("change", [
    {"candidate": "a" * 40}, {"digest": "sha256:" + "0" * 64}, {"digest": ""},
    {"images": {}}, {"images": {name: f"repo/{name}:latest" for name in auto.COMPONENTS}},
])
def test_bad_artifact_handoff_refused(tmp_path, manifest, change):
    with pytest.raises(auto.Refused):
        admit(tmp_path, manifest, **change)


def test_tampered_manifest_refused_before_any_deploy(tmp_path, manifest):
    manifest["release_profile"]["name"] = "full"
    with pytest.raises(auto.Refused):
        admit(tmp_path, manifest)


def archive(manifest, name="RELEASE_MANIFEST.json"):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zipped:
        zipped.writestr(name, json.dumps(manifest))
    return data.getvalue()


def fake_api(manifest, *, artifact_name=auto.DEPLOYED_ARTIFACT, expired=False,
             job_result="success", archive_name="RELEASE_MANIFEST.json"):
    def read(path, **kwargs):
        if path.startswith("actions/workflows/"):
            return {"workflow_runs": [{"id": 90, "path": auto.WORKFLOW, "event": "workflow_dispatch"}]}
        if path.endswith("/artifacts?per_page=100"):
            return {"artifacts": [{"id": 900, "name": artifact_name, "expired": expired}]}
        if path.endswith("/jobs?per_page=100"):
            return {"jobs": [{"name": auto.DEPLOY_JOB, "conclusion": job_result}]}
        if path.endswith("/zip"):
            return archive(manifest, archive_name)
        if path == "actions/runs/100":
            return ci()
        if path == "branches/dev":
            return branch()
        raise AssertionError(path)
    return read


def test_build_only_green_run_is_not_rollback_or_deployed_proof(monkeypatch, manifest):
    monkeypatch.setattr(auto, "api", fake_api(manifest, artifact_name="runtime-release-manifest-" + SHA))
    assert auto.previous_deployment() is None


@pytest.mark.parametrize("kwargs", [
    {"expired": True}, {"job_result": "failure"}, {"job_result": "skipped"},
    {"archive_name": "../../outside.json"},
])
def test_untrustworthy_previous_release_is_not_silently_first_release(monkeypatch, manifest, kwargs):
    monkeypatch.setattr(auto, "api", fake_api(manifest, **kwargs))
    with pytest.raises(auto.Refused):
        auto.previous_deployment()


def test_duplicate_successful_release_stops_before_build(monkeypatch, manifest, context, tmp_path):
    monkeypatch.setattr(auto, "api", fake_api(manifest))
    assert auto.main(["preflight", "--candidate", SHA, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 0
    assert "proceed=false" in Path(context["GITHUB_OUTPUT"]).read_text()


def test_first_release_selects_absence_probe_not_a_fake_predecessor(monkeypatch, manifest, context, tmp_path):
    monkeypatch.setattr(auto, "api", fake_api(manifest, artifact_name="build-only"))
    assert auto.main(["recovery", "--candidate", SHA, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 0
    text = Path(context["GITHUB_OUTPUT"]).read_text()
    assert "initial_release=true" in text
    assert "rollback_manifest=\n" in text
    assert not (tmp_path / "previous-release.json").exists()


def test_subsequent_update_selects_real_previous_manifest(monkeypatch, manifest, context, tmp_path):
    next_sha = "e" * 40
    monkeypatch.setenv("GITHUB_SHA", next_sha)
    previous_api = fake_api(manifest)
    def read(path, **kwargs):
        if path == "actions/runs/100":
            return {**ci(), "head_sha": next_sha}
        if path == "branches/dev":
            return {"protected": True, "commit": {"sha": next_sha}}
        return previous_api(path, **kwargs)
    monkeypatch.setattr(auto, "api", read)
    assert auto.main(["recovery", "--candidate", next_sha, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 0
    assert "initial_release=false" in Path(context["GITHUB_OUTPUT"]).read_text()
    assert json.loads((tmp_path / "previous-release.json").read_text()) == manifest


def test_failed_github_read_cannot_claim_no_previous_release(monkeypatch, context, tmp_path):
    def failed(*args, **kwargs):
        raise auto.Refused("GitHub unavailable")
    monkeypatch.setattr(auto, "api", failed)
    assert auto.main(["recovery", "--candidate", SHA, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()


def test_ci_dispatches_only_dev_and_does_not_claim_live_success(monkeypatch, context):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    requests = []
    def call(path, **kwargs):
        if path == "branches/dev":
            return branch()
        requests.append((path, kwargs["data"]))
    monkeypatch.setattr(auto, "api", call)
    assert auto.main(["dispatch", "--candidate", SHA]) == 0
    assert requests[0][1]["inputs"]["environment"] == "dev"
    assert requests[0][1]["inputs"]["phase"] == "auto"
    assert "release_lease" not in requests[0][1]["inputs"]


def test_workflow_keeps_one_deployment_implementation_and_manual_gate():
    release = yaml.safe_load((ROOT / auto.WORKFLOW).read_text())
    jobs = release["jobs"]
    policy_checkout = jobs["release_phase"]["steps"][0]["with"]["ref"]
    assert "inputs.phase == 'auto' && github.sha" in policy_checkout
    manual = jobs["admission"]
    assert "inputs.phase == 'deploy'" in manual["if"]
    assert manual["environment"]["name"] == "${{ inputs.environment }}"
    assert any("check_runtime_admission.py" in step.get("run", "") for step in manual["steps"])
    assert jobs["automatic_admission"]["environment"]["name"] == "dev"
    assert "inputs.environment == 'dev'" in jobs["automatic_admission"]["if"]
    assert "automatic_admission" in jobs["deploy"]["needs"]
    assert "inputs.phase == 'auto'" in jobs["deploy"]["if"]
    assert "cancel-in-progress" in release["concurrency"]
    assert release["concurrency"]["cancel-in-progress"] is False
    assert "'deploy'" in release["concurrency"]["group"]
    steps = jobs["deploy"]["steps"]
    check = next(i for i, step in enumerate(steps) if "automatic_dev deploy-check" in step.get("run", ""))
    deploy = next(i for i, step in enumerate(steps) if step.get("name") == "Deploy Cloud Run by immutable digest")
    retained = next(i for i, step in enumerate(steps) if step.get("with", {}).get("name") == auto.DEPLOYED_ARTIFACT)
    assert check < deploy < retained
    assert "success()" in steps[retained]["if"]
    ci_jobs = yaml.safe_load((ROOT / CI_WORKFLOW_PATH).read_text())["jobs"]
    trigger = ci_jobs["automatic-dev-release"]
    assert "github.event_name == 'push'" in trigger["if"]
    assert "refs/heads/dev" in trigger["if"]
    assert set(trigger["needs"]) == {"orchestrator", "product", "performance-gate", "product-e2e-gate"}
    assert trigger["permissions"]["actions"] == "write"


CI_WORKFLOW_PATH = auto.CI_WORKFLOW
