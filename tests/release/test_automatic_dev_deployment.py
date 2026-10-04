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
from tests.release.test_release_manifest import (
    REAL_CANDIDATE_SHA,
    SECOND_REAL_CANDIDATE_SHA,
    sources_off_manifest,
)

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


def live_steps(live, *later):
    """Deploy job step outcomes: the live-commit step, then evidence publication."""
    names = ["Download the successfully deployed manifest",
             "Retain the live-verified dev predecessor for the next update"]
    return [{"name": auto.LIVE_STEP, "conclusion": live, "completed_at": "2026-07-27T14:57:00Z"},
            *({"name": n, "conclusion": c} for n, c in zip(names, later, strict=False))]


def deploy_run(**change):
    return {"id": 90, "path": auto.WORKFLOW, "event": "workflow_dispatch",
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
            "head_branch": "dev", "head_sha": "d" * 40, "run_attempt": 1,
            "display_title": f"Runtime Release dev deploy {SHA}", **change}


def fake_api(manifest, *, artifact_name=auto.DEPLOYED_ARTIFACT, expired=False,
             job_result="success", archive_name="RELEASE_MANIFEST.json", run=None,
             runs=None, attempt_jobs=None, deployment_state="success", seen=None,
             run_manifests=None, run_jobs=None, run_artifacts=None, unproven=()):
    runs_list = runs if runs is not None else [run or deploy_run()]
    def read(path, **kwargs):
        if seen is not None:
            seen.append(path)
        if path.startswith("actions/workflows/"):
            return {"workflow_runs": runs_list}
        if path.endswith("/artifacts?per_page=100"):
            run_id = int(path.split("actions/runs/")[1].split("/")[0])
            if run_artifacts is not None and run_id in run_artifacts:
                return {"artifacts": run_artifacts[run_id]}
            if artifact_name is None:
                return {"artifacts": []}
            return {"artifacts": [{"id": 900 + run_id, "name": artifact_name, "expired": expired}]}
        if "/attempts/" in path and path.endswith("/jobs?per_page=100"):
            parts = path.split("actions/runs/")[1].split("/")
            run_id = int(parts[0])
            attempt = int(parts[2])
            if run_jobs and (run_id, attempt) in run_jobs:
                return {"jobs": run_jobs[(run_id, attempt)]}
            result = (attempt_jobs or {}).get(attempt, job_result)
            if result is None:
                return {"jobs": []}
            return {"jobs": [{"name": auto.DEPLOY_JOB, "conclusion": result,
                              "steps": live_steps("failure")}]}
        if path.startswith("deployments?"):
            assert "environment=dev" in path
            return [{"id": 7}]
        if path == "deployments/7/statuses?per_page=100&page=1":
            if deployment_state is None:
                return []
            return [{"state": deployment_state,
                     "log_url": f"https://github.com/{REPO}/actions/runs/{r['id']}/job/1"}
                    for r in runs_list if r["id"] not in unproven]
        if path.endswith("/zip"):
            art_id = int(path.split("actions/artifacts/")[1].split("/")[0])
            run_id = art_id - 900
            m = (run_manifests or {}).get(run_id, manifest)
            return archive(m, archive_name)
        if path == "actions/runs/100":
            return ci()
        if path == "branches/dev":
            return branch()
        raise AssertionError(path)
    return read


def test_build_only_green_run_is_not_rollback_or_deployed_proof(monkeypatch, manifest):
    monkeypatch.setattr(auto, "api", fake_api(manifest, job_result=None))
    assert auto.previous_deployment() is None


@pytest.mark.parametrize("kwargs", [
    {"expired": True}, {"artifact_name": None},
    {"deployment_state": "failure"}, {"deployment_state": None},
    {"archive_name": "../../outside.json"},
    {"run": deploy_run(display_title="Runtime Release dev deploy " + "a" * 40)},
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
    monkeypatch.setattr(auto, "api", fake_api(manifest, runs=[]))
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


def test_signed_manual_dev_deploy_from_another_ref_is_discovered(monkeypatch, manifest, context):
    seen = []
    run = deploy_run(head_branch="hotfix/odp-1", head_sha="f" * 40)
    monkeypatch.setattr(auto, "api", fake_api(manifest, run=run, seen=seen))
    found = auto.previous_deployment()
    assert found is not None and found[0] == manifest
    assert not any("branch=" in path for path in seen)


@pytest.mark.parametrize("title", [
    f"Runtime Release staging deploy {SHA}", f"Runtime Release production auto {SHA}",
])
def test_other_environment_runs_are_never_dev_predecessors(monkeypatch, manifest, context, title):
    monkeypatch.setattr(auto, "api", fake_api(manifest, run=deploy_run(display_title=title)))
    assert auto.previous_deployment() is None


def test_fork_dispatch_run_is_ignored(monkeypatch, manifest, context):
    run = deploy_run(head_repository={"full_name": "fork/odayplus"})
    monkeypatch.setattr(auto, "api", fake_api(manifest, run=run))
    assert auto.previous_deployment() is None


def test_rerun_of_already_deployed_current_run_deduplicates(monkeypatch, manifest, context, tmp_path):
    # Attempt 1 of this very run deployed; attempt 2 is the in-progress rerun.
    monkeypatch.setenv("GITHUB_RUN_ID", "90")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    run = deploy_run(run_attempt=2, display_title=f"Runtime Release dev auto {SHA}")
    monkeypatch.setattr(auto, "api", fake_api(manifest, run=run, attempt_jobs={2: None}))
    assert auto.main(["preflight", "--candidate", SHA, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 0
    assert "proceed=false" in Path(context["GITHUB_OUTPUT"]).read_text()


def test_first_attempt_of_current_run_not_counted_before_it_deploys(monkeypatch, manifest, context):
    monkeypatch.setenv("GITHUB_RUN_ID", "90")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    run = deploy_run(display_title=f"Runtime Release dev auto {SHA}")
    monkeypatch.setattr(auto, "api", fake_api(manifest, run=run))
    assert auto.previous_deployment() is None


def test_failed_rerun_before_live_step_does_not_hide_earlier_successful_deployment(
    monkeypatch, manifest, context
):
    run = deploy_run(run_attempt=2, conclusion="failure")
    run_jobs = {(90, 2): [{"name": auto.DEPLOY_JOB, "conclusion": "failure",
                           "steps": [{"name": "Run the live runtime preflight", "conclusion": "failure"},
                                     *live_steps("skipped", "skipped", "skipped")]}]}
    monkeypatch.setattr(auto, "api", fake_api(manifest, run=run, run_jobs=run_jobs))
    found = auto.previous_deployment()
    assert found is not None and found[0] == manifest


def test_failed_rerun_inside_live_step_hides_earlier_deployment_without_readback(
    monkeypatch, manifest, context
):
    run = deploy_run(run_attempt=2, conclusion="failure")
    monkeypatch.setattr(auto, "api", fake_api(manifest, run=run, attempt_jobs={2: "failure"}))
    with pytest.raises(auto.Refused, match="no live read-back proves"):
        auto.previous_deployment()


def test_automatic_admission_rereads_dev_deploy_targets_before_mutation():
    jobs = yaml.safe_load((ROOT / auto.WORKFLOW).read_text())["jobs"]
    steps = jobs["automatic_admission"]["steps"]
    probe = next(i for i, step in enumerate(steps)
                 if "probe_release_target_absence.py" in step.get("run", ""))
    admit_step = next(i for i, step in enumerate(steps) if step.get("id") == "admit")
    auth = next(i for i, step in enumerate(steps)
                if step.get("uses", "").startswith("google-github-actions/auth"))
    assert auth < probe < admit_step
    run = steps[probe]["run"]
    assert "--manifest .odp_data/release/automatic-dev/RELEASE_MANIFEST.json" in run
    assert "--receipt" in run and "--output" not in run
    for target in ("api", "web", "migration", "worker", "scheduler"):
        assert f'--target "{target}=' in run
    env = steps[probe]["env"]
    assert env["API_SERVICE"] == "${{ vars.ODP_CLOUD_RUN_API_SERVICE }}"
    assert "if" not in steps[probe]
    assert jobs["automatic_admission"]["environment"]["name"] == "dev"


def test_both_build_entrances_share_one_immutable_publication_lane():
    release = yaml.safe_load((ROOT / auto.WORKFLOW).read_text())
    group = release["jobs"]["build"]["concurrency"]["group"]
    assert release["jobs"]["build"]["concurrency"]["cancel-in-progress"] is False
    assert "inputs.phase" not in group
    assert "inputs.environment" in group and "inputs.release_sha" in group
    assert group != release["concurrency"]["group"]
    assert release["permissions"]["deployments"] == "read"


def test_deployment_ordering_selects_latest_completion_over_creation_order(monkeypatch, context):
    sha1 = REAL_CANDIDATE_SHA
    sha2 = SECOND_REAL_CANDIDATE_SHA
    manifest1 = sources_off_manifest(candidate_sha=sha1, release_profile={
        "name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed",
    })
    manifest2 = sources_off_manifest(candidate_sha=sha2, release_profile={
        "name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed",
    })
    run1 = deploy_run(id=101, head_sha=sha1, run_attempt=2, created_at="2026-07-27T14:50:00Z",
                      display_title=f"Runtime Release dev deploy {sha1}")
    run2 = deploy_run(id=102, head_sha=sha2, run_attempt=1, created_at="2026-07-27T14:55:00Z",
                      display_title=f"Runtime Release dev deploy {sha2}")
    runs = [run2, run1]
    run_jobs = {
        (101, 2): [{"name": auto.DEPLOY_JOB, "conclusion": "success", "completed_at": "2026-07-27T15:00:00Z"}],
        (101, 1): [{"name": auto.DEPLOY_JOB, "conclusion": "failure", "completed_at": "2026-07-27T14:52:00Z",
                     "steps": live_steps("failure")}],
        (102, 1): [{"name": auto.DEPLOY_JOB, "conclusion": "success", "completed_at": "2026-07-27T14:58:00Z"}],
    }
    run_manifests = {101: manifest1, 102: manifest2}
    monkeypatch.setattr(auto, "api", fake_api(manifest1, runs=runs, run_jobs=run_jobs, run_manifests=run_manifests))

    selected_manifest, selected_run = auto.previous_deployment()
    assert selected_run["id"] == 101
    assert selected_manifest["candidate_sha"] == sha1


def test_deduplication_after_later_deployment_by_older_run(monkeypatch, context, tmp_path):
    sha1 = REAL_CANDIDATE_SHA
    sha2 = SECOND_REAL_CANDIDATE_SHA
    monkeypatch.setenv("GITHUB_SHA", sha1)
    manifest1 = sources_off_manifest(candidate_sha=sha1, release_profile={
        "name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed",
    })
    manifest2 = sources_off_manifest(candidate_sha=sha2, release_profile={
        "name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed",
    })
    run1 = deploy_run(id=101, head_sha=sha1, run_attempt=2, created_at="2026-07-27T14:50:00Z",
                      display_title=f"Runtime Release dev deploy {sha1}")
    run2 = deploy_run(id=102, head_sha=sha2, run_attempt=1, created_at="2026-07-27T14:55:00Z",
                      display_title=f"Runtime Release dev deploy {sha2}")
    runs = [run2, run1]
    run_jobs = {
        (101, 2): [{"name": auto.DEPLOY_JOB, "conclusion": "success", "completed_at": "2026-07-27T15:00:00Z"}],
        (101, 1): [{"name": auto.DEPLOY_JOB, "conclusion": "failure", "completed_at": "2026-07-27T14:52:00Z",
                     "steps": live_steps("failure")}],
        (102, 1): [{"name": auto.DEPLOY_JOB, "conclusion": "success", "completed_at": "2026-07-27T14:58:00Z"}],
    }
    run_manifests = {101: manifest1, 102: manifest2}

    def read_api(path, **kwargs):
        if path == "actions/runs/100":
            return {**ci(), "head_sha": sha1}
        if path == "branches/dev":
            return {"protected": True, "commit": {"sha": sha1}}
        return fake_api(manifest1, runs=runs, run_jobs=run_jobs, run_manifests=run_manifests)(path, **kwargs)

    monkeypatch.setattr(auto, "api", read_api)

    assert auto.main(["preflight", "--candidate", sha1, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 0
    assert "proceed=false" in Path(context["GITHUB_OUTPUT"]).read_text()


def test_newer_deployment_wins_when_creation_and_completion_align(monkeypatch, context):
    sha1 = REAL_CANDIDATE_SHA
    sha2 = SECOND_REAL_CANDIDATE_SHA
    manifest1 = sources_off_manifest(candidate_sha=sha1, release_profile={
        "name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed",
    })
    manifest2 = sources_off_manifest(candidate_sha=sha2, release_profile={
        "name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed",
    })
    run1 = deploy_run(id=101, head_sha=sha1, run_attempt=1, created_at="2026-07-27T14:50:00Z",
                      display_title=f"Runtime Release dev deploy {sha1}")
    run2 = deploy_run(id=102, head_sha=sha2, run_attempt=1, created_at="2026-07-27T14:55:00Z",
                      display_title=f"Runtime Release dev deploy {sha2}")
    runs = [run2, run1]
    run_jobs = {
        (101, 1): [{"name": auto.DEPLOY_JOB, "conclusion": "success", "completed_at": "2026-07-27T14:52:00Z"}],
        (102, 1): [{"name": auto.DEPLOY_JOB, "conclusion": "success", "completed_at": "2026-07-27T14:58:00Z"}],
    }
    run_manifests = {101: manifest1, 102: manifest2}
    monkeypatch.setattr(auto, "api", fake_api(manifest1, runs=runs, run_jobs=run_jobs, run_manifests=run_manifests))

    selected_manifest, selected_run = auto.previous_deployment()
    assert selected_run["id"] == 102
    assert selected_manifest["candidate_sha"] == sha2


def test_latest_successful_run_missing_artifact_refuses_without_fallback_to_older_valid_artifact(
    monkeypatch, context, tmp_path
):
    sha1 = REAL_CANDIDATE_SHA
    sha2 = SECOND_REAL_CANDIDATE_SHA
    sha3 = "f" * 40

    manifest1 = sources_off_manifest(candidate_sha=sha1, release_profile={
        "name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed",
    })
    manifest2 = sources_off_manifest(candidate_sha=sha2, release_profile={
        "name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed",
    })

    run1 = deploy_run(id=101, head_sha=sha1, run_attempt=1, created_at="2026-07-27T14:50:00Z",
                      display_title=f"Runtime Release dev deploy {sha1}")
    run2 = deploy_run(id=102, head_sha=sha2, run_attempt=1, created_at="2026-07-27T14:55:00Z",
                      display_title=f"Runtime Release dev deploy {sha2}")
    runs = [run2, run1]
    run_jobs = {
        (101, 1): [{"name": auto.DEPLOY_JOB, "conclusion": "success", "completed_at": "2026-07-27T14:52:00Z"}],
        (102, 1): [{"name": auto.DEPLOY_JOB, "conclusion": "success", "completed_at": "2026-07-27T14:58:00Z"}],
    }
    # Run 101 has valid artifact, but latest Run 102 has missing artifact
    run_artifacts = {
        101: [{"id": 1001, "name": auto.DEPLOYED_ARTIFACT, "expired": False}],
        102: [],
    }
    run_manifests = {101: manifest1, 102: manifest2}

    def read_api(candidate_sha):
        base = fake_api(manifest1, runs=runs, run_jobs=run_jobs, run_manifests=run_manifests,
                        run_artifacts=run_artifacts)
        def read(path, **kwargs):
            if path == "actions/runs/100":
                return {**ci(), "head_sha": candidate_sha}
            if path == "branches/dev":
                return {"protected": True, "commit": {"sha": candidate_sha}}
            return base(path, **kwargs)
        return read

    # 1. Direct discovery raises Refused and does not fall back to Run 101
    monkeypatch.setattr(auto, "api", read_api(sha3))
    with pytest.raises(auto.Refused, match="Previous dev deployment artifact is missing"):
        auto.previous_deployment()

    # 2. Recovery refuses explicitly, never binding older Run 101 as rollback
    assert auto.main(["recovery", "--candidate", sha3, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()
    assert not (tmp_path / "previous-release.json").exists()

    # 3. Preflight for older SHA (sha1) refuses explicitly, never falsely deduplicating
    monkeypatch.setenv("GITHUB_SHA", sha1)
    monkeypatch.setattr(auto, "api", read_api(sha1))
    assert auto.main(["preflight", "--candidate", sha1, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()


def test_pagination_limit_reached_with_qualifying_records_fails_closed(monkeypatch, manifest, context, tmp_path):
    run = deploy_run(id=101, head_sha=SHA, run_attempt=1, created_at="2026-07-27T14:50:00Z",
                     display_title=f"Runtime Release dev deploy {SHA}")
    page_100_runs = [run] * 100

    def mock_api(path, **kwargs):
        if path.startswith("actions/workflows/deploy-dev.yml/runs"):
            # Every page returns 100 runs, so reached_end remains False after 10 pages
            return {"workflow_runs": page_100_runs}
        if path.endswith("/artifacts?per_page=100"):
            return {"artifacts": [{"id": 1001, "name": auto.DEPLOYED_ARTIFACT, "expired": False}]}
        if "/attempts/" in path and path.endswith("/jobs?per_page=100"):
            return {"jobs": [{"name": auto.DEPLOY_JOB, "conclusion": "success", "completed_at": "2026-07-27T14:52:00Z"}]}
        if path.startswith("deployments?"):
            return [{"id": 7}]
        if path == "deployments/7/statuses?per_page=100&page=1":
            return [{"state": "success", "log_url": f"https://github.com/{REPO}/actions/runs/101/job/1"}]
        if path.endswith("/zip"):
            return archive(manifest)
        if path == "actions/runs/100":
            return ci()
        if path == "branches/dev":
            return branch()
        raise AssertionError(path)

    monkeypatch.setattr(auto, "api", mock_api)

    with pytest.raises(auto.Refused, match="Deployment history scan limit reached"):
        auto.previous_deployment()

    assert auto.main(["preflight", "--candidate", SHA, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()

    assert auto.main(["recovery", "--candidate", SHA, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()


def dev_manifest(sha):
    return sources_off_manifest(candidate_sha=sha, release_profile={
        "name": "dev-admin", "target_environment": "dev", "model_readiness": "not_claimed",
    })


def a_then_b_runs():
    run_a = deploy_run(id=101, head_sha="a" * 40,
                       display_title=f"Runtime Release dev deploy {REAL_CANDIDATE_SHA}")
    run_b = deploy_run(id=102, head_sha="b" * 40,
                       display_title=f"Runtime Release dev deploy {SECOND_REAL_CANDIDATE_SHA}")
    run_jobs = {
        (101, 1): [{"name": auto.DEPLOY_JOB, "conclusion": "success",
                    "completed_at": "2026-07-27T14:52:00Z"}],
        (102, 1): [{"name": auto.DEPLOY_JOB, "conclusion": "success",
                    "completed_at": "2026-07-27T14:58:00Z"}],
    }
    manifests = {101: dev_manifest(REAL_CANDIDATE_SHA), 102: dev_manifest(SECOND_REAL_CANDIDATE_SHA)}
    return [run_b, run_a], run_jobs, manifests


def with_ci(base, candidate):
    def read(path, **kwargs):
        if path == "actions/runs/100":
            return {**ci(), "head_sha": candidate}
        if path == "branches/dev":
            return {"protected": True, "commit": {"sha": candidate}}
        return base(path, **kwargs)
    return read


def test_latest_deploy_without_environment_proof_never_falls_back_to_older_release(
    monkeypatch, context, tmp_path
):
    runs, run_jobs, manifests = a_then_b_runs()
    base = fake_api(manifests[101], runs=runs, run_jobs=run_jobs,
                    run_manifests=manifests, unproven={102})
    monkeypatch.setattr(auto, "api", base)
    with pytest.raises(auto.Refused, match="lacks dev environment proof"):
        auto.previous_deployment()

    # Recovery for a new candidate must not bind A as rollback for live B.
    candidate = "c" * 40
    monkeypatch.setenv("GITHUB_SHA", candidate)
    monkeypatch.setattr(auto, "api", with_ci(base, candidate))
    assert auto.main(["recovery", "--candidate", candidate, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()
    assert not (tmp_path / "previous-release.json").exists()

    # Preflight for A must not deduplicate against a replaced release.
    monkeypatch.setenv("GITHUB_SHA", REAL_CANDIDATE_SHA)
    monkeypatch.setattr(auto, "api", with_ci(base, REAL_CANDIDATE_SHA))
    assert auto.main(["preflight", "--candidate", REAL_CANDIDATE_SHA, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()


def evidence_api(manifests, runs, run_jobs, *, deployments, statuses):
    base = fake_api(manifests[101], runs=runs, run_jobs=run_jobs, run_manifests=manifests)
    def read(path, **kwargs):
        if path.startswith("deployments?"):
            assert "environment=dev" in path and "per_page=100" in path
            return deployments(path)
        if path.startswith("deployments/"):
            return statuses(path)
        return base(path, **kwargs)
    return read


def proof(run_id):
    return {"state": "success", "log_url": f"https://github.com/{REPO}/actions/runs/{run_id}/job/1"}


def test_environment_proof_beyond_first_page_is_found(monkeypatch, context):
    runs, run_jobs, manifests = a_then_b_runs()
    def deployments(path):
        if "sha=" + "b" * 40 in path:
            # 100 newer dev records (refused before mutation) push B's to page 2.
            return [{"id": 1000 + i} for i in range(100)] if path.endswith("page=1") else [{"id": 8}]
        return [{"id": 9}]
    def statuses(path):
        deployment = int(path.split("/")[1])
        if deployment == 8:
            if path.endswith("page=1"):
                return [{"state": "in_progress"}] * 100
            return [proof(102)]
        if deployment == 9:
            return [proof(101)]
        return [{"state": "failure", "log_url": "https://github.com/x/actions/runs/555/job/1"}]
    monkeypatch.setattr(auto, "api", evidence_api(manifests, runs, run_jobs,
                                                  deployments=deployments, statuses=statuses))
    found_manifest, found_run = auto.previous_deployment()
    assert found_run["id"] == 102
    assert found_manifest["candidate_sha"] == SECOND_REAL_CANDIDATE_SHA


def test_unbounded_environment_evidence_refuses(monkeypatch, context):
    runs, run_jobs, manifests = a_then_b_runs()
    def deployments(path):
        return [{"id": 1000 + i} for i in range(100)]
    def statuses(path):
        return []
    monkeypatch.setattr(auto, "api", evidence_api(manifests, runs, run_jobs,
                                                  deployments=deployments, statuses=statuses))
    with pytest.raises(auto.Refused, match="evidence scan limit"):
        auto.previous_deployment()


def b_committed_then_evidence_failed(*, with_a=True):
    """A deployed; B passed every live gate but a later artifact step failed."""
    runs, run_jobs, manifests = a_then_b_runs()
    run_jobs[(102, 1)] = [{"name": auto.DEPLOY_JOB, "status": "completed", "conclusion": "failure",
                           "completed_at": "2026-07-27T14:58:00Z",
                           "steps": live_steps("success", "success", "failure")}]
    if not with_a:
        runs = runs[:1]
    # B's job failed, so GitHub records no successful dev environment status
    # and the retained predecessor artifact was never published.
    return fake_api(manifests[101], runs=runs, run_jobs=run_jobs, run_manifests=manifests,
                    unproven={102}, run_artifacts={102: []})


@pytest.mark.parametrize("with_a", [True, False], ids=["recurring", "first-release"])
def test_live_commit_followed_by_evidence_failure_is_never_skipped(
    monkeypatch, context, tmp_path, with_a
):
    base = b_committed_then_evidence_failed(with_a=with_a)
    monkeypatch.setattr(auto, "api", base)
    with pytest.raises(auto.Refused, match="committed live but its job failed"):
        auto.previous_deployment()

    # Recovery for a newer candidate binds neither stale A nor a first release.
    candidate = "c" * 40
    monkeypatch.setenv("GITHUB_SHA", candidate)
    monkeypatch.setattr(auto, "api", with_ci(base, candidate))
    assert auto.main(["recovery", "--candidate", candidate, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)]) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()
    assert not (tmp_path / "previous-release.json").exists()

    # Retrying B neither rebuilds nor redeploys over its own live release.
    monkeypatch.setenv("GITHUB_SHA", SECOND_REAL_CANDIDATE_SHA)
    monkeypatch.setattr(auto, "api", with_ci(base, SECOND_REAL_CANDIDATE_SHA))
    for action in ("preflight", "recovery"):
        assert auto.main([action, "--candidate", SECOND_REAL_CANDIDATE_SHA, "--environment", "dev",
                          "--ci-run-id", "100", "--directory", str(tmp_path)]) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()


A_SHA, B_SHA, C_SHA = REAL_CANDIDATE_SHA, SECOND_REAL_CANDIDATE_SHA, "c" * 40


def b_deploy_job(live, *, status="completed", started=True, readback=None, job="failure"):
    """B's deploy job after A deployed. ``readback`` is the read-back step conclusion."""
    steps = [{"name": "Run the live runtime preflight", "conclusion": "success"},
             {"name": auto.LIVE_STEP, "status": status, "conclusion": live,
              "started_at": "2026-07-27T14:56:00Z" if started else None,
              "completed_at": "2026-07-27T14:57:00Z" if status == "completed" else None}]
    if readback is not None:
        steps.append({"name": auto.RESTORE_STEP, "status": "completed", "conclusion": readback})
    steps.append({"name": "Download the successfully deployed manifest", "conclusion": "skipped"})
    return [{"name": auto.DEPLOY_JOB, "status": "completed", "conclusion": job,
             "completed_at": "2026-07-27T14:58:00Z", "steps": steps}]


def readback_zip(live_release, *, run_id=102, attempt=1, failed=B_SHA, kind=auto.RESTORE_KIND,
                 name=auto.RESTORE_FILE):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zipped:
        zipped.writestr(name, json.dumps({
            "kind": kind, "environment": "dev", "failed_candidate_sha": failed,
            "run_id": str(run_id), "run_attempt": str(attempt), "live_release": live_release}))
    return data.getvalue()


def b_failed_api(job, *, with_a=True, receipt=None, artifact=None):
    runs, run_jobs, manifests = a_then_b_runs()
    run_jobs[(102, 1)] = job
    if not with_a:
        runs = runs[:1]
    readbacks = [] if receipt is None else [
        {"id": 5000, "name": f"{auto.RESTORE_ARTIFACT}-1", "expired": False, **(artifact or {})}]
    base = fake_api(manifests[101], runs=runs, run_jobs=run_jobs, run_manifests=manifests,
                    unproven={102}, run_artifacts={102: readbacks})
    def read(path, **kwargs):
        if path == "actions/artifacts/5000/zip":
            return receipt
        return base(path, **kwargs)
    return read


def run_action(monkeypatch, base, action, candidate, tmp_path):
    monkeypatch.setenv("GITHUB_SHA", candidate)
    monkeypatch.setattr(auto, "api", with_ci(base, candidate))
    return auto.main([action, "--candidate", candidate, "--environment", "dev",
                      "--ci-run-id", "100", "--directory", str(tmp_path)])


UNPROVEN_LIVE_STEPS = {
    # A live, B promoted, its live gate failed and rollback/scheduler restore
    # failed too: the read-back step ran and refused, so nothing was published.
    "rollback-failed-readback-refused": b_deploy_job("failure", readback="failure"),
    # Same failure from a run whose read-back never ran (for example a
    # pre-repair run): the failed conclusion alone proves nothing.
    "failed-without-readback": b_deploy_job("failure"),
    "readback-skipped": b_deploy_job("failure", readback="skipped"),
    "cancelled-live-step": b_deploy_job("cancelled", job="cancelled"),
    "interrupted-live-step": b_deploy_job(None, status="in_progress", job="failure"),
    "timed-out-live-step": b_deploy_job("timed_out", job="failure"),
}


@pytest.mark.parametrize("job", UNPROVEN_LIVE_STEPS.values(), ids=UNPROVEN_LIVE_STEPS.keys())
def test_unproven_failed_live_step_fails_closed_for_recovery_and_dedup(
    monkeypatch, context, tmp_path, job
):
    base = b_failed_api(job)
    monkeypatch.setattr(auto, "api", base)
    with pytest.raises(auto.Refused, match="no live read-back proves"):
        auto.previous_deployment()

    # Recovery for a later candidate must not bind A as the proven predecessor.
    assert run_action(monkeypatch, base, "recovery", C_SHA, tmp_path) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()
    assert not (tmp_path / "previous-release.json").exists()

    # An automatic retry for A must not be deduplicated against unproven A.
    assert run_action(monkeypatch, base, "preflight", A_SHA, tmp_path) == 1
    assert not Path(context["GITHUB_OUTPUT"]).exists()


def test_readback_step_success_without_published_receipt_fails_closed(monkeypatch, context):
    monkeypatch.setattr(auto, "api", b_failed_api(b_deploy_job("failure", readback="success")))
    with pytest.raises(auto.Refused, match="no live read-back proves"):
        auto.previous_deployment()


def test_positively_proven_rollback_keeps_predecessor_and_deduplicates(
    monkeypatch, context, tmp_path
):
    base = b_failed_api(b_deploy_job("failure", readback="success"),
                        receipt=readback_zip(A_SHA[:16]))
    monkeypatch.setattr(auto, "api", base)
    found_manifest, found_run = auto.previous_deployment()
    assert found_run["id"] == 101
    assert found_manifest["candidate_sha"] == A_SHA

    assert run_action(monkeypatch, base, "recovery", C_SHA, tmp_path) == 0
    assert "initial_release=false" in Path(context["GITHUB_OUTPUT"]).read_text()
    assert json.loads((tmp_path / "previous-release.json").read_text())["candidate_sha"] == A_SHA

    # Live dev is proven to be A, so retrying A really is a duplicate.
    Path(context["GITHUB_OUTPUT"]).unlink()
    assert run_action(monkeypatch, base, "preflight", A_SHA, tmp_path) == 0
    assert "proceed=false" in Path(context["GITHUB_OUTPUT"]).read_text()


@pytest.mark.parametrize("receipt,artifact", [
    (readback_zip(A_SHA[:16], run_id=999), None),
    (readback_zip(A_SHA[:16], attempt=2), None),
    (readback_zip(A_SHA[:16], failed=C_SHA), None),
    (readback_zip(A_SHA[:16], kind="something-else"), None),
    (readback_zip(A_SHA[:16], name="../outside.json"), None),
    (readback_zip(B_SHA[:16]), None),
    (readback_zip("A" * 16), None),
    (readback_zip(A_SHA[:16]), {"expired": True}),
    (readback_zip(A_SHA[:16]), {"name": f"{auto.RESTORE_ARTIFACT}-2"}),
], ids=["other-run", "other-attempt", "other-candidate", "wrong-kind", "path-escape",
        "candidate-still-live", "malformed-release", "expired", "other-attempt-artifact"])
def test_readback_receipt_must_bind_this_failed_attempt(monkeypatch, context, receipt, artifact):
    monkeypatch.setattr(auto, "api", b_failed_api(
        b_deploy_job("failure", readback="success"), receipt=receipt, artifact=artifact))
    with pytest.raises(auto.Refused, match="no live read-back proves"):
        auto.previous_deployment()


def test_readback_naming_an_unretained_release_refuses(monkeypatch, context):
    monkeypatch.setattr(auto, "api", b_failed_api(
        b_deploy_job("failure", readback="success"), receipt=readback_zip("f" * 16)))
    with pytest.raises(auto.Refused, match="no retained successful deployment"):
        auto.previous_deployment()


@pytest.mark.parametrize("job", [
    b_deploy_job("skipped", started=False),
    b_deploy_job(None, status="pending", started=False),
], ids=["skipped", "never-started"])
def test_live_step_that_never_started_keeps_predecessor(monkeypatch, context, job):
    monkeypatch.setattr(auto, "api", b_failed_api(job))
    found_manifest, found_run = auto.previous_deployment()
    assert found_run["id"] == 101
    assert found_manifest["candidate_sha"] == A_SHA


@pytest.mark.parametrize("job,receipt", [
    (b_deploy_job("failure"), None),
    (b_deploy_job(None, status="in_progress"), None),
    (b_deploy_job("failure", readback="success"), readback_zip(None)),
], ids=["unproven", "interrupted", "proven-empty"])
def test_failed_first_release_falls_through_to_fresh_absence_readback(
    monkeypatch, context, tmp_path, job, receipt
):
    # Nothing was ever live, so the only honest next step is a first release;
    # the build and admission absence read-backs refuse if B left anything.
    base = b_failed_api(job, with_a=False, receipt=receipt)
    assert run_action(monkeypatch, base, "preflight", C_SHA, tmp_path) == 0
    assert "proceed=true" in Path(context["GITHUB_OUTPUT"]).read_text()
    Path(context["GITHUB_OUTPUT"]).unlink()
    assert run_action(monkeypatch, base, "recovery", C_SHA, tmp_path) == 0
    text = Path(context["GITHUB_OUTPUT"]).read_text()
    assert "initial_release=true" in text and "rollback_manifest=\n" in text


def test_proven_empty_target_is_a_first_release_even_after_an_older_release(monkeypatch, context):
    # The read-back observed an empty target, so a first release (re-proven by
    # the build/admission absence read-backs) is the true state, not stale A.
    monkeypatch.setattr(auto, "api", b_failed_api(
        b_deploy_job("failure", readback="success"), receipt=readback_zip(None)))
    assert auto.previous_deployment() is None


def test_workflow_reads_back_live_state_only_after_a_failed_dev_live_step():
    steps = yaml.safe_load((ROOT / auto.WORKFLOW).read_text())["jobs"]["deploy"]["steps"]
    names = [step.get("name") for step in steps]
    live = names.index(auto.LIVE_STEP)
    readback = names.index(auto.RESTORE_STEP)
    publish = next(i for i, step in enumerate(steps)
                   if str(step.get("with", {}).get("name", "")).startswith(auto.RESTORE_ARTIFACT))
    assert live < readback < publish
    assert steps[live]["id"] == "live-deploy"
    condition = steps[readback]["if"]
    assert "failure()" in condition and "steps.live-deploy.outcome == 'failure'" in condition
    assert "inputs.environment == 'dev'" in condition
    assert "automatic_dev live-readback" in steps[readback]["run"]
    assert auto.RESTORE_FILE in steps[readback]["run"]
    upload = steps[publish]
    assert "steps.live-readback.outcome == 'success'" in upload["if"]
    assert upload["with"]["name"] == auto.RESTORE_ARTIFACT + "-${{ github.run_attempt }}"
    assert upload["with"]["path"].endswith("/" + auto.RESTORE_FILE)
    assert upload["with"]["if-no-files-found"] == "error"


READBACK_ENV = {
    "ODP_DEPLOY_ENV": "dev", "ODAY_RELEASE_SHA": B_SHA, "GCP_PROJECT": "proj",
    "GCP_REGION": "asia-east1", "API_SERVICE": "api", "WEB_SERVICE": "web",
    "MIGRATION_JOB": "migrate", "WORKER_JOB": "worker", "SCHEDULER_JOB": "scheduler",
    "WORKER_SCHEDULE_NAME": "worker-trigger", "SCHEDULER_SCHEDULE_NAME": "scheduler-trigger",
    "GITHUB_RUN_ID": "102", "GITHUB_RUN_ATTEMPT": "1",
}


def described(name, serving, tags=(("rev-a", A_SHA), ("rev-b", B_SHA)), latest=False):
    traffic = [{"revisionName": rev, "percent": pct, **({"latestRevision": True} if latest else {})}
               for rev, pct in serving]
    traffic += [{"revisionName": rev, "tag": f"candidate-{sha[:16]}", "percent": 0}
                for rev, sha in tags]
    return {"metadata": {"name": name}, "status": {"traffic": traffic}}


def trigger_uri(base, sha):
    job = f"{base}-r-{sha[:12]}"
    return f"https://run.googleapis.com/v2/projects/proj/locations/asia-east1/jobs/{job}:run"


def fake_gcloud(services, *, triggers=None):
    triggers = triggers or {"scheduler-trigger": trigger_uri("scheduler", A_SHA),
                            "worker-trigger": trigger_uri("worker", A_SHA)}
    def call(*args):
        flags = dict(arg[2:].split("=", 1) for arg in args if arg.startswith("--"))
        if args[:3] == ("run", "services", "list"):
            name = flags["filter"].split("=", 1)[1]
            return [services[name]] if services.get(name) else []
        if args[:3] == ("scheduler", "jobs", "list"):
            name = flags["filter"].split(":", 1)[1]
            return [{"name": f"projects/proj/locations/asia-east1/jobs/{name}",
                     "httpTarget": {"uri": triggers[name]}}] if name in triggers else []
        raise AssertionError(args)
    return call


def test_live_readback_proves_restored_predecessor_and_round_trips(monkeypatch, context, tmp_path):
    services = {"api": described("api", [("rev-a", 100)]), "web": described("web", [("rev-a", 100)])}
    monkeypatch.setattr(auto, "gcloud_json", fake_gcloud(services))
    for key, value in READBACK_ENV.items():
        monkeypatch.setenv(key, value)
    receipt = tmp_path / auto.RESTORE_FILE
    assert auto.main(["live-readback", "--output", str(receipt)]) == 0
    payload = json.loads(receipt.read_text())
    assert payload["live_release"] == A_SHA[:16]
    assert payload["failed_candidate_sha"] == B_SHA

    # The published receipt is exactly what history accepts as restoration.
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as zipped:
        zipped.writestr(auto.RESTORE_FILE, receipt.read_text())
    monkeypatch.setenv("GITHUB_RUN_ID", "200")
    monkeypatch.setattr(auto, "api", b_failed_api(
        b_deploy_job("failure", readback="success"), receipt=data.getvalue()))
    assert auto.previous_deployment()[0]["candidate_sha"] == A_SHA


@pytest.mark.parametrize("services,triggers", [
    ({"api": described("api", [("rev-a", 50), ("rev-b", 50)]),
      "web": described("web", [("rev-a", 100)])}, None),
    ({"api": described("api", [("rev-b", 100)]), "web": described("web", [("rev-b", 100)])}, None),
    ({"api": described("api", [("rev-a", 100)]), "web": described("web", [("rev-b", 100)])}, None),
    ({"api": described("api", [("rev-a", 100)], latest=True),
      "web": described("web", [("rev-a", 100)])}, None),
    ({"api": described("api", [("rev-x", 100)]), "web": described("web", [("rev-x", 100)])}, None),
    ({"api": described("api", [("rev-a", 90)]), "web": described("web", [("rev-a", 90)])}, None),
    ({"api": described("api", [("rev-a", 100)]), "web": None}, None),
    ({"api": described("api", [("rev-a", 100)]), "web": described("web", [("rev-a", 100)])},
     {"scheduler-trigger": trigger_uri("scheduler", B_SHA),
      "worker-trigger": trigger_uri("worker", A_SHA)}),
    ({"api": described("api", [("rev-a", 100)]), "web": described("web", [("rev-a", 100)])},
     {"scheduler-trigger": trigger_uri("scheduler", A_SHA)}),
], ids=["mixed-traffic", "candidate-serving", "api-web-split", "unpinned-latest",
        "untagged-revision", "partial-traffic", "half-absent", "scheduler-on-candidate",
        "worker-trigger-missing"])
def test_live_readback_refuses_unproven_or_mixed_state(monkeypatch, context, tmp_path,
                                                       services, triggers):
    monkeypatch.setattr(auto, "gcloud_json", fake_gcloud(services, triggers=triggers))
    for key, value in READBACK_ENV.items():
        monkeypatch.setenv(key, value)
    receipt = tmp_path / auto.RESTORE_FILE
    assert auto.main(["live-readback", "--output", str(receipt)]) == 1
    assert not receipt.exists()


def test_live_readback_empty_target_requires_full_absence_probe(monkeypatch, context, tmp_path):
    from delivery_toolchain.release import probe_release_target_absence as probe

    monkeypatch.setattr(auto, "gcloud_json", fake_gcloud({}))
    seen = []
    monkeypatch.setattr(probe, "probe_target_absence", lambda **kw: seen.append(kw) or {})
    payload = auto.failed_live_step_readback(READBACK_ENV)
    assert payload["live_release"] is None
    assert seen[0]["candidate_sha"] == B_SHA
    assert set(seen[0]["targets"]) == {"api", "web", "migration", "worker", "scheduler"}

    def present(**kw):
        raise probe.ProbeError(["worker candidate job remains"])
    monkeypatch.setattr(probe, "probe_target_absence", present)
    with pytest.raises(auto.Refused, match="not empty"):
        auto.failed_live_step_readback(READBACK_ENV)


@pytest.mark.parametrize("change", [{"ODP_DEPLOY_ENV": "production"}, {"ODAY_RELEASE_SHA": "main"},
                                    {"WORKER_SCHEDULE_NAME": ""}])
def test_live_readback_is_dev_only_and_fully_bound(monkeypatch, change):
    monkeypatch.setattr(auto, "gcloud_json", fake_gcloud({}))
    with pytest.raises(auto.Refused):
        auto.failed_live_step_readback({**READBACK_ENV, **change})


def test_unreadable_failed_deploy_steps_refuse(monkeypatch, context):
    runs, run_jobs, manifests = a_then_b_runs()
    run_jobs[(102, 1)] = [{"name": auto.DEPLOY_JOB, "conclusion": "failure"}]
    monkeypatch.setattr(auto, "api", fake_api(manifests[101], runs=runs, run_jobs=run_jobs,
                                              run_manifests=manifests, unproven={102}))
    with pytest.raises(auto.Refused, match="step outcomes are unreadable"):
        auto.previous_deployment()


def test_live_step_name_matches_workflow():
    steps = yaml.safe_load((ROOT / auto.WORKFLOW).read_text())["jobs"]["deploy"]["steps"]
    names = [step.get("name") for step in steps]
    assert auto.LIVE_STEP in names
    assert names.index(auto.LIVE_STEP) < names.index(
        "Retain the live-verified dev predecessor for the next update")
