from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from product_ops.deployment.bluegreen_release import (
    DataPlatformPointer,
    OperationResult,
    ReleaseState,
    SchedulerTarget,
    ServiceTarget,
    capture_data_platform_pointer,
    capture_traffic_snapshot,
    get_tagged_target_from_description,
    main,
    resolve_tagged_target,
    restore_data_platform_pointer,
)

TRAFFIC_HELPER = Path(__file__).resolve().parents[2] / "product_ops/deployment/cloud_run_traffic.py"

# ---------------------------------------------------------------------------
# Data model and serialization tests
# ---------------------------------------------------------------------------


def test_service_target_and_scheduler_target_args() -> None:
    svc = ServiceTarget(service="odp-api", project="my-project", region="asia-east1")
    assert svc.gcloud_args() == ["--project=my-project", "--region=asia-east1"]

    sch = SchedulerTarget(job_name="daily-sync", project="my-project", location="asia-east1")
    assert sch.gcloud_args() == ["--project=my-project", "--location=asia-east1"]

    ptr = DataPlatformPointer(selector_label="v1", snapshot_id="snap-123", namespace="dp-prod")
    assert ptr.selector_label == "v1"
    assert ptr.snapshot_id == "snap-123"
    assert ptr.namespace == "dp-prod"


def test_release_state_json_roundtrip() -> None:
    state = ReleaseState(
        release_id="rel-001",
        blue_api_revision="api-blue",
        blue_web_revision="web-blue",
        green_api_revision="api-green",
        green_web_revision="web-green",
        api_traffic_snapshot_path="/tmp/api.json",
        web_traffic_snapshot_path="/tmp/web.json",
        scheduler_states={"job1": "ENABLED"},
        scheduler_snapshots={"job1": "scheduler-0.json"},
        data_platform_pointer={"selector_label": "v1", "snapshot_id": "s1"},
        data_platform_pointer_snapshot_path="/tmp/dp.json",
        switch_completed_at="2026-08-24T12:00:00Z",
        rollback_completed_at="2026-08-24T12:30:00Z",
    )
    json_text = state.to_json()
    loaded = ReleaseState.from_json(json_text)
    assert loaded.release_id == "rel-001"
    assert loaded.blue_api_revision == "api-blue"
    assert loaded.green_api_revision == "api-green"
    assert loaded.scheduler_snapshots == {"job1": "scheduler-0.json"}
    assert loaded.data_platform_pointer_snapshot_path == "/tmp/dp.json"
    assert loaded.switch_completed_at == "2026-08-24T12:00:00Z"
    assert loaded.rollback_completed_at == "2026-08-24T12:30:00Z"

    with pytest.raises(ValueError, match="must be an object"):
        ReleaseState.from_json("[]")


def test_operation_result_to_dict() -> None:
    res = OperationResult(
        success=True,
        operation="test_op",
        message="ok",
        dry_run=True,
        details={"key": "val"},
    )
    d = res.to_dict()
    assert d["success"] is True
    assert d["operation"] == "test_op"
    assert d["dry_run"] is True
    assert d["details"]["key"] == "val"


# ---------------------------------------------------------------------------
# Tag resolution tests (Green 0% tag smoke)
# ---------------------------------------------------------------------------


def test_get_tagged_target_from_description_success() -> None:
    desc = {
        "status": {
            "traffic": [
                {
                    "tag": "blue",
                    "revisionName": "api-v1",
                    "percent": 100,
                    "url": "https://blue---api.run.app",
                },
                {
                    "tag": "green",
                    "revisionName": "api-v2",
                    "percent": 0,
                    "url": "https://green---api.run.app",
                },
            ]
        }
    }
    rev, url = get_tagged_target_from_description(desc, "green")
    assert rev == "api-v2"
    assert url == "https://green---api.run.app"


def test_get_tagged_target_from_description_validation_errors() -> None:
    # Missing status
    with pytest.raises(ValueError, match="missing status.traffic"):
        get_tagged_target_from_description({}, "green")

    # Missing tag match
    with pytest.raises(ValueError, match="expected exactly one"):
        get_tagged_target_from_description({"status": {"traffic": [{"tag": "blue"}]}}, "green")

    # Multiple tag matches
    with pytest.raises(ValueError, match="expected exactly one"):
        get_tagged_target_from_description(
            {"status": {"traffic": [{"tag": "green"}, {"tag": "green"}]}}, "green"
        )

    # Missing revisionName
    with pytest.raises(ValueError, match="no immutable revisionName"):
        get_tagged_target_from_description(
            {
                "status": {
                    "traffic": [
                        {"tag": "green", "revisionName": "", "url": "https://green.run.app"}
                    ]
                }
            },
            "green",
        )

    # Invalid URL
    with pytest.raises(ValueError, match="no HTTPS URL"):
        get_tagged_target_from_description(
            {
                "status": {
                    "traffic": [
                        {"tag": "green", "revisionName": "rev-1", "url": "http://insecure.app"}
                    ]
                }
            },
            "green",
        )


@patch("product_ops.deployment.bluegreen_release._run_gcloud")
def test_resolve_tagged_target(mock_gcloud: MagicMock) -> None:
    svc = ServiceTarget(service="odp-api", project="my-p", region="asia-east1")

    # Empty tag
    res = resolve_tagged_target(svc, "")
    assert res.success is False
    assert "non-empty" in res.message

    # Dry run
    res_dry = resolve_tagged_target(svc, "green", dry_run=True)
    assert res_dry.success is True
    assert res_dry.dry_run is True

    # Gcloud failure
    mock_gcloud.return_value = subprocess.CompletedProcess(
        [], 1, stdout="", stderr="service not found"
    )
    res_fail = resolve_tagged_target(svc, "green")
    assert res_fail.success is False
    assert "Failed to describe" in res_fail.message

    # Malformed JSON
    mock_gcloud.return_value = subprocess.CompletedProcess([], 0, stdout="not-json", stderr="")
    res_bad_json = resolve_tagged_target(svc, "green")
    assert res_bad_json.success is False

    # Success
    mock_gcloud.return_value = subprocess.CompletedProcess(
        [],
        0,
        stdout=json.dumps(
            {
                "status": {
                    "traffic": [
                        {
                            "tag": "green",
                            "revisionName": "odp-api-green-001",
                            "url": "https://green---odp-api.run.app",
                        }
                    ]
                }
            }
        ),
        stderr="",
    )
    res_ok = resolve_tagged_target(svc, "green")
    assert res_ok.success is True
    assert res_ok.details["revision"] == "odp-api-green-001"
    assert res_ok.details["url"] == "https://green---odp-api.run.app"


# ---------------------------------------------------------------------------
# Traffic operations tests
# ---------------------------------------------------------------------------


def _listed(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout=f"{name}\n", stderr="")


@patch("product_ops.deployment.bluegreen_release._run_gcloud")
def test_capture_traffic_snapshot(mock_gcloud: MagicMock, tmp_path: Path) -> None:
    svc = ServiceTarget(service="odp-api", project="p", region="r")
    out_file = tmp_path / "snap.json"

    # Dry run
    res_dry = capture_traffic_snapshot(svc, out_file, dry_run=True)
    assert res_dry.success is True
    assert res_dry.dry_run is True
    assert not out_file.exists()

    # Listing failure is not mistaken for an absent service
    mock_gcloud.side_effect = [subprocess.CompletedProcess([], 1, stdout="", stderr="error")]
    res_list_fail = capture_traffic_snapshot(svc, out_file)
    assert res_list_fail.success is False
    assert "Failed to list" in res_list_fail.message
    assert not out_file.exists()

    # Ambiguous lookup
    mock_gcloud.side_effect = [_listed("odp-api\nodp-api-2")]
    res_ambiguous = capture_traffic_snapshot(svc, out_file)
    assert res_ambiguous.success is False
    assert "ambiguous" in res_ambiguous.message

    # Gcloud describe failure
    mock_gcloud.side_effect = [
        _listed("odp-api"),
        subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
    ]
    res_fail = capture_traffic_snapshot(svc, out_file)
    assert res_fail.success is False
    assert "Failed to describe" in res_fail.message

    # Invalid JSON
    mock_gcloud.side_effect = [
        _listed("odp-api"),
        subprocess.CompletedProcess([], 0, stdout="invalid", stderr=""),
    ]
    res_bad = capture_traffic_snapshot(svc, out_file)
    assert res_bad.success is False
    assert "Invalid JSON" in res_bad.message

    # No traffic block
    mock_gcloud.side_effect = [
        _listed("odp-api"),
        subprocess.CompletedProcess([], 0, stdout='{"status": {}}', stderr=""),
    ]
    res_no_traffic = capture_traffic_snapshot(svc, out_file)
    assert res_no_traffic.success is False
    assert "status.traffic" in res_no_traffic.message
    assert not out_file.exists()

    # Success keeps only what rollback needs, never the env block or secrets
    description = {
        "metadata": {"name": "odp-api"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "env": [
                                {"name": "ODP_TENANT_ID", "value": "tenant-prod"},
                                {
                                    "name": "DB",
                                    "valueFrom": {"secretKeyRef": {"name": "db-url", "key": "latest"}},
                                },
                            ]
                        }
                    ]
                }
            }
        },
        "status": {
            "url": "https://odp-api.run.app",
            "traffic": [{"revisionName": "odp-api-00001", "percent": 100}],
        },
    }
    mock_gcloud.side_effect = [
        _listed("odp-api"),
        subprocess.CompletedProcess([], 0, stdout=json.dumps(description), stderr=""),
    ]
    res_ok = capture_traffic_snapshot(svc, out_file)
    assert res_ok.success is True
    assert res_ok.details["exists"] is True
    written = json.loads(out_file.read_text(encoding="utf-8"))
    assert written == {
        "schema_version": 1,
        "kind": "cloud-run-service-traffic-snapshot",
        "exists": True,
        "service": "odp-api",
        "status": {
            "url": "https://odp-api.run.app",
            "traffic": [{"revisionName": "odp-api-00001", "percent": 100}],
        },
    }
    assert "tenant-prod" not in out_file.read_text(encoding="utf-8")
    assert "db-url" not in out_file.read_text(encoding="utf-8")
    traffic = subprocess.run(
        [sys.executable, str(TRAFFIC_HELPER), "restore-arg", f"--description={out_file}"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert traffic.stdout.strip() == "odp-api-00001=100"


@patch("product_ops.deployment.bluegreen_release._run_gcloud")
def test_capture_traffic_snapshot_records_a_first_release_as_absent(
    mock_gcloud: MagicMock, tmp_path: Path
) -> None:
    """Production's first release has no blue; capture must say so, not fail."""

    svc = ServiceTarget(service="odp-web", project="p", region="r")
    out_file = tmp_path / "web.json"
    mock_gcloud.side_effect = [subprocess.CompletedProcess([], 0, stdout="", stderr="")]

    res = capture_traffic_snapshot(svc, out_file)

    assert res.success is True
    assert res.details["exists"] is False
    assert mock_gcloud.call_count == 1
    assert json.loads(out_file.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "kind": "cloud-run-service-traffic-snapshot",
        "exists": False,
        "service": "odp-web",
    }
    exists = subprocess.run(
        [sys.executable, str(TRAFFIC_HELPER), "exists", f"--description={out_file}"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert exists.stdout.strip() == "false"








# ---------------------------------------------------------------------------
# Scheduler operations tests
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# Data platform pointer operations tests
# ---------------------------------------------------------------------------


def test_capture_and_restore_data_platform_pointer(tmp_path: Path) -> None:
    pointer = DataPlatformPointer(
        selector_label="prod-green", snapshot_id="snap-999", namespace="data-prod"
    )
    out_file = tmp_path / "pointer.json"

    # Capture dry run
    res_cap_dry = capture_data_platform_pointer(pointer, out_file, dry_run=True)
    assert res_cap_dry.success is True
    assert not out_file.exists()

    # Capture execution
    res_cap = capture_data_platform_pointer(pointer, out_file)
    assert res_cap.success is True
    assert out_file.exists()
    captured_data = json.loads(out_file.read_text(encoding="utf-8"))
    assert captured_data["selector_label"] == "prod-green"
    assert captured_data["snapshot_id"] == "snap-999"

    # Restore missing file
    missing_file = tmp_path / "missing.json"
    res_rest_missing = restore_data_platform_pointer(missing_file)
    assert res_rest_missing.success is False
    assert "not found" in res_rest_missing.message

    # Restore invalid json
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("invalid json", encoding="utf-8")
    res_bad = restore_data_platform_pointer(bad_file)
    assert res_bad.success is False

    # Restore non-dict json
    list_file = tmp_path / "list.json"
    list_file.write_text("[]", encoding="utf-8")
    res_list = restore_data_platform_pointer(list_file)
    assert res_list.success is False

    # Restore dry run
    res_rest_dry = restore_data_platform_pointer(out_file, dry_run=True)
    assert res_rest_dry.success is True
    assert res_rest_dry.dry_run is True

    # Restore execution with custom output path
    custom_restore = tmp_path / "custom-restore.json"
    res_rest = restore_data_platform_pointer(out_file, output_path=custom_restore)
    assert res_rest.success is True
    assert custom_restore.exists()
    restored_data = json.loads(custom_restore.read_text(encoding="utf-8"))
    assert restored_data["selector_label"] == "prod-green"
    assert "restore_requested_at" in restored_data


# ---------------------------------------------------------------------------
# Composite operations tests (Full Switch and Rollback)
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------










@patch("product_ops.deployment.bluegreen_release.resolve_tagged_target")
def test_cli_resolve_tag(mock_resolve: MagicMock) -> None:
    mock_resolve.return_value = OperationResult(success=True, operation="tag", message="ok")
    code = main(
        [
            "--project=my-p",
            "--region=asia-east1",
            "resolve-tag",
            "--service=api",
            "--tag=green",
        ]
    )
    assert code == 0


@patch("product_ops.deployment.bluegreen_release.pause_all_schedulers")
def test_cli_pause_schedulers(mock_pause: MagicMock) -> None:
    mock_pause.return_value = OperationResult(success=True, operation="pause", message="ok")
    code = main(
        [
            "--project=my-p",
            "--region=asia-east1",
            "pause-schedulers",
            "--jobs",
            "job1",
            "job2",
        ]
    )
    assert code == 0






@patch("product_ops.deployment.bluegreen_release.capture_data_platform_pointer")
def test_cli_capture_pointer(mock_cap: MagicMock, tmp_path: Path) -> None:
    mock_cap.return_value = OperationResult(success=True, operation="cap", message="ok")
    code = main(
        [
            "--project=my-p",
            "--region=asia-east1",
            "capture-pointer",
            "--selector-label=v1",
            "--snapshot-id=s1",
            "--namespace=dp",
            "--output-file",
            str(tmp_path / "ptr.json"),
        ]
    )
    assert code == 0


@patch("product_ops.deployment.bluegreen_release.restore_data_platform_pointer")
def test_cli_restore_pointer(mock_rest: MagicMock, tmp_path: Path) -> None:
    mock_rest.return_value = OperationResult(success=True, operation="rest", message="ok")
    snap_file = tmp_path / "ptr.json"
    snap_file.write_text("{}", encoding="utf-8")
    code = main(
        [
            "--project=my-p",
            "--region=asia-east1",
            "restore-pointer",
            "--snapshot-file",
            str(snap_file),
        ]
    )
    assert code == 0






# Stateful gcloud double: both public entrypoints run the actual shared shell
# owner and actual data/readback contracts. No cloud resources are contacted.
FAKE_RELEASE_GCLOUD = r"""#!/usr/bin/env python3
import base64, json, os, sys
from pathlib import Path
args = sys.argv[1:]
path = Path(os.environ["RELEASE_CLOUD_STATE"])
data = json.loads(path.read_text())
with Path(os.environ["RELEASE_CLOUD_LOG"]).open("a") as stream:
    stream.write(json.dumps(args) + "\n")
def save():
    for trigger in data["triggers"].values():
        if trigger.get("description") == "":
            trigger.pop("description")
        retry = trigger.get("retryConfig", {})
        if retry.get("retryCount") == 0:
            retry.pop("retryCount")
        if retry.get("maxRetryDuration") == "0s":
            retry.pop("maxRetryDuration")
    path.write_text(json.dumps(data))
def flag(name, default=None):
    prefix = "--" + name + "="
    return next((arg[len(prefix):] for arg in args if arg.startswith(prefix)), default)
control = data.setdefault("control", {})
if control.get("fail_once") and " ".join(args).startswith(control["fail_once"]):
    del control["fail_once"]
    save()
    sys.exit(17)
if args[:4] == ["artifacts", "docker", "images", "describe"]:
    print(data["artifact_digests"][args[4]])
elif args[:3] == ["run", "jobs", "execute"]:
    pass
elif args[:4] == ["run", "jobs", "executions", "describe"]:
    print(json.dumps(data["executions"][args[4]]))
elif args[:3] == ["run", "jobs", "describe"]:
    print(json.dumps(data["jobs"][args[3]]))
elif args[:3] == ["run", "services", "list"]:
    name = flag("filter").split("metadata.name=", 1)[1]
    if name in data["services"]:
        print(name)
elif args[:3] == ["run", "services", "describe"]:
    print(json.dumps(data["services"][args[3]]))
elif args[:3] == ["run", "services", "delete"]:
    data["services"].pop(args[3], None)
    save()
elif args[:3] == ["run", "services", "update-traffic"]:
    service = data["services"][args[3]]
    service["status"]["traffic"] = [
        {"revisionName": revision, "percent": int(percent)}
        for revision, percent in (target.split("=") for target in flag("to-revisions").split(","))
    ]
    save()
elif args[:3] == ["scheduler", "jobs", "list"]:
    name = flag("filter").split("/jobs/", 1)[1]
    if name in data["triggers"]:
        print("projects/p/locations/r/jobs/" + name)
elif args[:3] == ["scheduler", "jobs", "describe"]:
    print(json.dumps(data["triggers"][args[3]]))
elif args[:3] == ["scheduler", "jobs", "delete"]:
    data["triggers"].pop(args[3], None)
    save()
elif args[:3] in (["scheduler", "jobs", "pause"], ["scheduler", "jobs", "resume"]):
    if not (args[2] == "pause" and control.get("ignore_pause")):
        data["triggers"][args[3]]["state"] = "PAUSED" if args[2] == "pause" else "ENABLED"
    save()
    if args[2] == "resume" and control.get("lose_resume_response") == args[3]:
        control.pop("lose_resume_response")
        save()
        sys.exit(17)
elif args[:3] in (["scheduler", "jobs", "create"], ["scheduler", "jobs", "update"]):
    name = args[4]
    if args[2] == "create":
        data["triggers"][name] = {
            "name": "projects/p/locations/r/jobs/" + name,
            "state": "ENABLED", "httpTarget": {},
            "retryConfig": {"minBackoffDuration": "5s", "maxBackoffDuration": "3600s", "maxDoublings": 5},
            "attemptDeadline": "180s",
        }
    value = data["triggers"][name]
    target = value["httpTarget"]
    for option, key in (("schedule", "schedule"), ("time-zone", "timeZone"),
                        ("attempt-deadline", "attemptDeadline"), ("description", "description")):
        if flag(option) is not None:
            value[key] = flag(option)
    for option, key in (("uri", "uri"), ("http-method", "httpMethod")):
        if flag(option) is not None:
            target[key] = flag(option)
    if flag("message-body") is not None:
        target["body"] = base64.b64encode(flag("message-body").encode()).decode()
    if "--clear-headers" in args:
        target["headers"] = {}
    for option in ("headers", "update-headers"):
        if flag(option) is not None:
            target.setdefault("headers", {}).update(dict(pair.split("=", 1) for pair in flag(option).split(",")))
    for auth, token, extra in (("oauth", "oauthToken", "scope"), ("oidc", "oidcToken", "audience")):
        if flag(auth + "-service-account-email") is not None:
            target.pop("oidcToken" if auth == "oauth" else "oauthToken", None)
            target[token] = {"serviceAccountEmail": flag(auth + "-service-account-email")}
            option = auth + "-token-" + ("scope" if auth == "oauth" else "audience")
            if flag(option) is not None:
                target[token][extra] = flag(option)
    retry = value.setdefault("retryConfig", {})
    for option, key in (("max-retry-attempts", "retryCount"), ("max-retry-duration", "maxRetryDuration"),
                        ("min-backoff", "minBackoffDuration"), ("max-backoff", "maxBackoffDuration"),
                        ("max-doublings", "maxDoublings")):
        if flag(option) is not None:
            retry[key] = int(flag(option)) if key in ("retryCount", "maxDoublings") else flag(option)
    if control.get("drift_trigger") == name and flag("message-body") is not None:
        target["uri"] += "-drift"
        control.pop("drift_trigger")
    save()
else:
    raise SystemExit("unhandled fake gcloud command: " + repr(args))
"""

RELEASE_SHA = "a" * 40
IMAGES = {kind: f"registry/{kind}@sha256:" + digit * 64
          for kind, digit in (("worker", "b"), ("scheduler", "c"))}


def _job(kind: str, name: str, *, old: bool = False) -> dict:
    sha = "d" * 40 if old else RELEASE_SHA
    return {
        "metadata": {"name": name, "labels": {"oday-release-sha": sha}},
        "spec": {"template": {"spec": {"template": {"spec": {"containers": [{
            "image": f"registry/{kind}@sha256:" + "e" * 64 if old else IMAGES["worker" if kind == "migration" else kind],
            "args": ["product_ops/deployment/cloud_run_job_entrypoint.py", "migrate" if kind == "migration" else kind],
            "env": [{"name": "ODP_PRODUCTION_PROVIDER_IDS", "value": "admin_boundary.official_dataset"},
                    {"name": "ODAY_DATABASE_URL", "valueFrom": {
                        "secretKeyRef": {"name": "database", "key": "latest"}}},
                    {"name": "ODP_ADMIN_BOUNDARY_PROVIDER_TOKEN", "valueFrom": {
                        "secretKeyRef": {"name": "boundary-token", "key": "latest"}}}],
        }]}}}}},
    }


def _trigger(kind: str, state: str) -> dict:
    return {
        "name": f"projects/p/locations/r/jobs/{kind}-trigger",
        "schedule": "17 * * * *", "timeZone": "Asia/Taipei", "state": state,
        "attemptDeadline": "180s",
        "retryConfig": {"minBackoffDuration": "5s",
                        "maxBackoffDuration": "3600s", "maxDoublings": 5},
        "httpTarget": {
            "uri": f"https://run.googleapis.com/v2/projects/p/locations/r/jobs/{kind}-old:run",
            "httpMethod": "POST", "body": "e30=",
            "headers": {"Content-Type": "application/json", "X-Owner": "ops"},
            "oauthToken": {"serviceAccountEmail": "scheduler@example.test",
                           "scope": "https://www.googleapis.com/auth/cloud-platform"},
        },
    }


@pytest.fixture
def release_cloud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "gcloud"
    fake.write_text(FAKE_RELEASE_GCLOUD, encoding="utf-8")
    fake.chmod(0o755)
    path = tmp_path / "cloud.json"
    data = {
        "jobs": {f"{kind}-{version}": _job(kind, f"{kind}-{version}", old=version == "old")
                 for kind in ("worker", "scheduler", "migration") for version in ("old", "new")},
        "triggers": {f"{kind}-trigger": _trigger(kind, state)
                     for kind, state in (("worker", "ENABLED"), ("scheduler", "PAUSED"))},
        "services": {
            name: {"status": {"url": f"https://{name}.run.app",
                             "traffic": [{"revisionName": f"{name}-old", "percent": 100}]}}
            for name in ("api", "web")
        },
        "control": {},
        "executions": {},
    }
    path.write_text(json.dumps(data))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("RELEASE_CLOUD_STATE", str(path))
    monkeypatch.setenv("RELEASE_CLOUD_LOG", str(tmp_path / "calls.jsonl"))
    return {"path": path, "tmp": tmp_path, "baseline": copy.deepcopy(data)}


def _cloud_data(cloud: dict) -> dict:
    return json.loads(cloud["path"].read_text())


def _set_cloud(cloud: dict, data: dict) -> None:
    cloud["path"].write_text(json.dumps(data))


def _calls(cloud: dict) -> list[list[str]]:
    path = cloud["tmp"] / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _capture(cloud: dict, *, dry_run: bool = False) -> Path:
    directory = cloud["tmp"] / "snapshot"
    args = ["--project=p", "--region=r", "capture-state", "--release-id=release",
            "--release-sha=" + RELEASE_SHA, "--api-service=api", "--web-service=web",
            "--scheduler-jobs", "worker-trigger", "scheduler-trigger", "--output-dir", str(directory)]
    assert main((["--dry-run"] if dry_run else []) + args) == 0
    return directory / "release-state.json"


def _switch(state: Path, *, new_state: str = "PAUSED") -> int:
    args = ["--project=p", "--region=r", "switch", "--state-file", str(state),
            "--api-service=api", "--web-service=web", "--green-api-revision=api-new",
            "--green-web-revision=web-new", "--time-zone=Asia/Taipei",
            "--service-account=scheduler@example.test", "--new-trigger-state=" + new_state]
    for kind in ("worker", "scheduler"):
        args += [f"--{kind}-trigger={kind}-trigger", f"--{kind}-job={kind}-new",
                 f"--{kind}-image={IMAGES[kind]}", f"--{kind}-cron=17 * * * *"]
    return main(args)


def _receipt(cloud: dict, kind: str) -> Path:
    from product_ops.deployment import validate_cloud_run_live_deployment as validator
    job = _cloud_data(cloud)["jobs"][f"{kind}-new"]
    execution = {
        "metadata": {"name": f"{kind}-new-execution", "labels": {"run.googleapis.com/job": f"{kind}-new"}},
        "spec": {"template": {"spec": {"containers": [{"image": IMAGES[kind]}]}}},
        "status": {"succeededCount": 1, "failedCount": 0,
                   "conditions": [{"type": "Completed", "status": "True"}]},
    }
    data = _cloud_data(cloud)
    data["executions"][f"{kind}-new-execution"] = execution
    _set_cloud(cloud, data)
    checks, report = validator.cloud_run_job_checks(
        kind=kind, job_description=job, execution=execution, expected_sha=RELEASE_SHA,
        expected_image=IMAGES[kind], expected_job=f"projects/p/locations/r/jobs/{kind}-new",
        release_provider_ids="admin_boundary.official_dataset",
    )
    assert all(check.ok for check in checks), [check for check in checks if not check.ok]
    report.update(ok=True, schema_version=1, generated_at=datetime.now(UTC).isoformat(),
                  checks=[asdict(check) for check in checks])
    path = cloud["tmp"] / f"{kind}-validation.json"
    path.write_text(json.dumps(report))
    return path


def _resume(cloud: dict, state: Path) -> int:
    return main(["--project=p", "--region=r", "resume-schedulers", "--state-file", str(state),
                 "--api-service=api", "--web-service=web",
                 "--worker-receipt", str(cloud["tmp"] / "worker-validation.json"),
                 "--scheduler-receipt", str(cloud["tmp"] / "scheduler-validation.json")])


def _rollback(state: Path) -> int:
    return main(["--project=p", "--region=r", "rollback", "--state-file", str(state),
                 "--api-service=api", "--web-service=web"])


def test_capture_populates_portable_scheduler_snapshots_and_real_old_images(release_cloud: dict) -> None:
    state_path = _capture(release_cloud)
    state = json.loads(state_path.read_text())
    assert state["scheduler_states"] == {"worker-trigger": "ENABLED", "scheduler-trigger": "PAUSED"}
    assert "scheduler_digests" not in state
    for relative in state["scheduler_snapshots"].values():
        assert not Path(relative).is_absolute()
        snapshot = json.loads((state_path.parent / relative).read_text())
        assert snapshot["jobBinding"]["job_image"].endswith("@sha256:" + "e" * 64)
        assert snapshot["httpTarget"]["body"] == "e30="
    assert all(call[:3] in (["run", "services", "list"], ["run", "services", "describe"],
                           ["run", "jobs", "describe"], ["scheduler", "jobs", "list"],
                           ["scheduler", "jobs", "describe"]) for call in _calls(release_cloud))


def test_switch_stays_paused_then_receipt_resume_preserves_original_paused(release_cloud: dict) -> None:
    state = _capture(release_cloud)
    assert _switch(state) == 0
    data = _cloud_data(release_cloud)
    assert {value["state"] for value in data["triggers"].values()} == {"PAUSED"}
    assert not any(call[:3] == ["scheduler", "jobs", "resume"] for call in _calls(release_cloud))
    for kind in ("worker", "scheduler"):
        target = data["triggers"][f"{kind}-trigger"]["httpTarget"]
        assert target["uri"].endswith(f"/jobs/{kind}-new:run")
        assert base64.b64decode(target["body"]) == b"{}"
        assert target["headers"]["X-Owner"] == "ops"
        _receipt(release_cloud, kind)
    assert _resume(release_cloud, state) == 0
    data = _cloud_data(release_cloud)
    assert data["triggers"]["worker-trigger"]["state"] == "ENABLED"
    assert data["triggers"]["scheduler-trigger"]["state"] == "PAUSED"


@pytest.mark.parametrize("damage", ["missing", "failed", "old-schema", "wrong-release", "wrong-image", "wrong-job", "failed-check"])
def test_unproven_receipt_cannot_resume(release_cloud: dict, damage: str) -> None:
    state = _capture(release_cloud)
    assert _switch(state) == 0
    for kind in ("worker", "scheduler"):
        _receipt(release_cloud, kind)
    path = release_cloud["tmp"] / "worker-validation.json"
    receipt = json.loads(path.read_text())
    if damage == "missing":
        path.unlink()
    else:
        if damage == "failed":
            receipt["ok"] = False
        elif damage == "old-schema":
            receipt.pop("job_image")
        elif damage == "wrong-release":
            receipt["expected_sha"] = "f" * 40
        elif damage == "wrong-image":
            receipt["job_image"] = IMAGES["scheduler"]
        elif damage == "wrong-job":
            receipt["execution_job_name"] = "another-job"
        else:
            receipt["checks"][0]["ok"] = False
        path.write_text(json.dumps(receipt))
    assert _resume(release_cloud, state) == 1
    assert {value["state"] for value in _cloud_data(release_cloud)["triggers"].values()} == {"PAUSED"}
    assert not any(call[:3] == ["scheduler", "jobs", "resume"] for call in _calls(release_cloud))


@pytest.mark.parametrize("drift", ["image", "uri", "cron", "oauth", "original-paused-enabled", "execution-image", "execution-failed"])
def test_resume_fresh_readback_rejects_drift(release_cloud: dict, drift: str) -> None:
    state = _capture(release_cloud)
    assert _switch(state) == 0
    for kind in ("worker", "scheduler"):
        _receipt(release_cloud, kind)
    data = _cloud_data(release_cloud)
    trigger = data["triggers"]["worker-trigger"]
    if drift == "image":
        data["jobs"]["worker-new"]["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["image"] = IMAGES["scheduler"]
    elif drift == "uri":
        trigger["httpTarget"]["uri"] = trigger["httpTarget"]["uri"].replace("worker-new", "scheduler-new")
    elif drift == "cron":
        trigger["schedule"] = "3 * * * *"
    elif drift == "oauth":
        trigger["httpTarget"]["oauthToken"]["serviceAccountEmail"] = "other@example.test"
    elif drift == "original-paused-enabled":
        data["triggers"]["scheduler-trigger"]["state"] = "ENABLED"
    elif drift == "execution-image":
        data["executions"]["worker-new-execution"]["spec"]["template"]["spec"]["containers"][0]["image"] = IMAGES["scheduler"]
    else:
        data["executions"]["worker-new-execution"]["status"]["failedCount"] = 1
    _set_cloud(release_cloud, data)
    assert _resume(release_cloud, state) == 1
    assert not any(call[:3] == ["scheduler", "jobs", "resume"] for call in _calls(release_cloud))


def test_job_image_mismatch_blocks_switch_before_mutation(release_cloud: dict) -> None:
    state = _capture(release_cloud)
    data = _cloud_data(release_cloud)
    data["jobs"]["worker-new"]["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]["image"] = IMAGES["scheduler"]
    _set_cloud(release_cloud, data)
    assert _switch(state) == 1
    assert _cloud_data(release_cloud)["triggers"] == release_cloud["baseline"]["triggers"]
    assert not any(call[:3] == ["scheduler", "jobs", "pause"] for call in _calls(release_cloud))


def test_partial_switch_failure_restores_both_traffic_and_trigger_snapshots(release_cloud: dict) -> None:
    state = _capture(release_cloud)
    data = _cloud_data(release_cloud)
    data["control"]["fail_once"] = "run services update-traffic web"
    _set_cloud(release_cloud, data)
    assert _switch(state) == 1
    data = _cloud_data(release_cloud)
    assert data["services"] == release_cloud["baseline"]["services"]
    assert data["triggers"] == release_cloud["baseline"]["triggers"]


def test_manual_rollback_uses_downloaded_scheduler_and_traffic_snapshots(release_cloud: dict) -> None:
    import shutil
    state = _capture(release_cloud)
    assert _switch(state) == 0
    downloaded = release_cloud["tmp"] / "downloaded"
    shutil.copytree(state.parent, downloaded)
    shutil.rmtree(state.parent)
    assert _rollback(downloaded / state.name) == 0
    data = _cloud_data(release_cloud)
    assert data["services"] == release_cloud["baseline"]["services"]
    assert data["triggers"] == release_cloud["baseline"]["triggers"]


@pytest.mark.parametrize("first_state", ["PAUSED", "ENABLED"])
def test_first_create_bootstraps_get_without_body_then_pauses_before_post(release_cloud: dict, first_state: str) -> None:
    data = _cloud_data(release_cloud)
    data["triggers"] = {}
    _set_cloud(release_cloud, data)
    state = _capture(release_cloud)
    assert json.loads(state.read_text())["scheduler_states"] == {
        "worker-trigger": "ABSENT", "scheduler-trigger": "ABSENT"}
    assert _switch(state, new_state=first_state) == 0
    calls = _calls(release_cloud)
    for kind in ("worker", "scheduler"):
        name = f"{kind}-trigger"
        create_index = next(i for i, call in enumerate(calls) if call[:5] == ["scheduler", "jobs", "create", "http", name])
        create = calls[create_index]
        assert "--http-method=GET" in create
        assert not any(arg.startswith("--message-body") for arg in create)
        pause_index = next(i for i, call in enumerate(calls) if i > create_index and call[:4] == ["scheduler", "jobs", "pause", name])
        post_index = next(i for i, call in enumerate(calls) if i > pause_index and call[:5] == ["scheduler", "jobs", "update", "http", name])
        assert any(call[:4] == ["scheduler", "jobs", "describe", name] for call in calls[pause_index + 1:post_index])
        _receipt(release_cloud, kind)
    assert _resume(release_cloud, state) == 0
    assert {trigger["state"] for trigger in _cloud_data(release_cloud)["triggers"].values()} == {first_state}
    assert _rollback(state) == 0
    assert not _cloud_data(release_cloud)["triggers"]


def test_dry_capture_preserves_existing_state_and_never_calls_cloud(release_cloud: dict) -> None:
    directory = release_cloud["tmp"] / "snapshot"
    directory.mkdir()
    path = directory / "release-state.json"
    path.write_text("preserve existing state")
    _capture(release_cloud, dry_run=True)
    assert path.read_text() == "preserve existing state"
    assert not _calls(release_cloud)


def test_partial_resume_failure_compensates_through_captured_snapshots(release_cloud: dict) -> None:
    data = _cloud_data(release_cloud)
    data["triggers"]["scheduler-trigger"]["state"] = "ENABLED"
    release_cloud["baseline"] = copy.deepcopy(data)
    _set_cloud(release_cloud, data)
    state = _capture(release_cloud)
    assert _switch(state) == 0
    for kind in ("worker", "scheduler"):
        _receipt(release_cloud, kind)
    data = _cloud_data(release_cloud)
    data["control"]["fail_once"] = "scheduler jobs resume scheduler-trigger"
    _set_cloud(release_cloud, data)
    assert _resume(release_cloud, state) == 1
    data = _cloud_data(release_cloud)
    assert data["services"] == release_cloud["baseline"]["services"]
    assert data["triggers"] == release_cloud["baseline"]["triggers"]


def test_post_install_readback_drift_restores_snapshots(release_cloud: dict) -> None:
    state = _capture(release_cloud)
    data = _cloud_data(release_cloud)
    data["control"]["drift_trigger"] = "worker-trigger"
    _set_cloud(release_cloud, data)
    assert _switch(state) == 1
    data = _cloud_data(release_cloud)
    assert data["services"] == release_cloud["baseline"]["services"]
    assert data["triggers"] == release_cloud["baseline"]["triggers"]


def test_bootstrap_never_installs_post_without_paused_readback(release_cloud: dict) -> None:
    data = _cloud_data(release_cloud)
    data["triggers"] = {}
    data["control"]["ignore_pause"] = True
    _set_cloud(release_cloud, data)
    state = _capture(release_cloud)
    assert _switch(state) == 1
    assert not _cloud_data(release_cloud)["triggers"]
    assert not any("--http-method=POST" in call for call in _calls(release_cloud))


def test_first_release_rollback_deletes_previously_absent_services_and_triggers(release_cloud: dict) -> None:
    data = _cloud_data(release_cloud)
    data["triggers"] = {}
    data["services"] = {}
    _set_cloud(release_cloud, data)
    state = _capture(release_cloud)
    data = _cloud_data(release_cloud)
    data["services"] = copy.deepcopy(release_cloud["baseline"]["services"])
    _set_cloud(release_cloud, data)
    assert _switch(state) == 0
    assert _rollback(state) == 0
    assert not _cloud_data(release_cloud)["services"]
    assert not _cloud_data(release_cloud)["triggers"]


def test_old_digest_cli_is_removed_and_incomplete_state_refuses_recovery(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--project=p", "--region=r", "switch-digests", "--jobs=j", "--digest=d"])
    state = tmp_path / "release-state.json"
    state.write_text(ReleaseState(release_id="legacy").to_json())
    assert _rollback(state) == 1


@pytest.mark.parametrize("damage", ["uri-job", "binding-job", "trigger", "project", "location", "swapped-files"])
def test_snapshot_identity_mismatch_refuses_switch_and_downloaded_rollback_before_mutation(release_cloud: dict, damage: str) -> None:
    state_path = _capture(release_cloud)
    state = json.loads(state_path.read_text())
    worker_path = state_path.parent / state["scheduler_snapshots"]["worker-trigger"]
    payload = json.loads(worker_path.read_text())
    if damage == "swapped-files":
        state["scheduler_snapshots"]["worker-trigger"] = state["scheduler_snapshots"]["scheduler-trigger"]
        state_path.write_text(json.dumps(state))
    else:
        if damage == "uri-job":
            payload["httpTarget"]["uri"] = payload["httpTarget"]["uri"].replace("worker-old", "scheduler-old")
        elif damage == "binding-job":
            payload["jobBinding"]["job_name"] = "scheduler-old"
        elif damage == "trigger":
            payload["name"] = payload["name"].replace("worker-trigger", "scheduler-trigger")
        elif damage == "project":
            payload["name"] = payload["name"].replace("projects/p/", "projects/foreign/")
            payload["httpTarget"]["uri"] = payload["httpTarget"]["uri"].replace("projects/p/", "projects/foreign/")
        else:
            payload["name"] = payload["name"].replace("locations/r/", "locations/foreign/")
            payload["httpTarget"]["uri"] = payload["httpTarget"]["uri"].replace("locations/r/", "locations/foreign/")
        worker_path.write_text(json.dumps(payload))
    assert _switch(state_path) == 1
    assert _rollback(state_path) == 1
    assert _cloud_data(release_cloud)["triggers"] == release_cloud["baseline"]["triggers"]
    assert not any(call[:3] in (["scheduler", "jobs", "pause"], ["scheduler", "jobs", "update"],
                               ["run", "services", "update-traffic"]) for call in _calls(release_cloud))


def test_snapshot_binding_accepts_the_exact_v2_job_resource_name(release_cloud: dict) -> None:
    state_path = _capture(release_cloud)
    state = json.loads(state_path.read_text())
    for relative in state["scheduler_snapshots"].values():
        path = state_path.parent / relative
        payload = json.loads(path.read_text())
        payload["jobBinding"]["job_name"] = "projects/p/locations/r/jobs/" + payload["jobBinding"]["job_name"]
        path.write_text(json.dumps(payload))
    assert _switch(state_path) == 0
    assert _rollback(state_path) == 0


def test_actual_deploy_resume_loop_checks_both_receipts_before_any_resume(release_cloud: dict) -> None:
    data = _cloud_data(release_cloud)
    data["triggers"]["scheduler-trigger"]["state"] = "ENABLED"
    _set_cloud(release_cloud, data)
    state_path = _capture(release_cloud)
    assert _switch(state_path) == 0
    for kind in ("worker", "scheduler"):
        _receipt(release_cloud, kind)
    receipt_path = release_cloud["tmp"] / "worker-validation.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["ok"] = False
    receipt_path.write_text(json.dumps(receipt))
    root = Path(__file__).resolve().parents[2]
    script = (root / "product_ops/deployment/deploy_cloud_run_waji.sh").read_text()
    start = script.index("for resume_mode in verify-only resume; do")
    end = script.index("DEPLOYMENT_COMMITTED=true", start)
    state = json.loads(state_path.read_text())
    env = {**os.environ, "GCP_PROJECT": "p", "GCP_REGION": "r", "ODAY_RELEASE_SHA": RELEASE_SHA,
           "ODP_SCHEDULER_TIME_ZONE": "Asia/Taipei", "ODP_CLOUD_SCHEDULER_SERVICE_ACCOUNT": "scheduler@example.test",
           "JOB_REPORT_DIR": str(release_cloud["tmp"])}
    for kind in ("worker", "scheduler"):
        prefix = kind.upper()
        env.update({f"{prefix}_SCHEDULE_NAME": f"{kind}-trigger", f"{prefix}_CANDIDATE_JOB": f"{kind}-new",
                    f"{prefix}_IMAGE": IMAGES[kind], f"ODP_{prefix}_CRON": "17 * * * *",
                    f"{prefix}_TRIGGER_SNAPSHOT": str(state_path.parent / state["scheduler_snapshots"][f"{kind}-trigger"])})
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", 'source "$1"; eval "$2"', "deploy-resume-test",
         str(root / "product_ops/deployment/cloud_run_release_traffic.sh"), script[start:end]],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert not any(call[:3] == ["scheduler", "jobs", "resume"] for call in _calls(release_cloud))
    assert {trigger["state"] for trigger in _cloud_data(release_cloud)["triggers"].values()} == {"PAUSED"}


def test_actual_deploy_execute_job_checks_image_before_one_shot(release_cloud: dict) -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "product_ops/deployment/deploy_cloud_run_waji.sh").read_text()
    function = script[script.index("execute_job() {"):script.index("write_public_egress_probe_receipt() {")]
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", 'source "$1"; eval "$2"; execute_job scheduler scheduler-new',
         "deploy-one-shot-test", str(root / "product_ops/deployment/cloud_run_release_traffic.sh"), function],
        cwd=root, env={**os.environ, "GCP_PROJECT": "p", "GCP_REGION": "r", "ODAY_RELEASE_SHA": RELEASE_SHA,
                       "SCHEDULER_IMAGE": IMAGES["worker"]},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert not any(call[:3] == ["run", "jobs", "execute"] for call in _calls(release_cloud))


@pytest.mark.parametrize("kind", ["migration", "worker", "scheduler"])
def test_actual_deploy_execute_job_uses_the_image_deployed_for_each_kind(release_cloud: dict, kind: str) -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "product_ops/deployment/deploy_cloud_run_waji.sh").read_text()
    function = script[script.index("execute_job() {"):script.index("write_public_egress_probe_receipt() {")]
    proof = release_cloud["tmp"] / "captured-proof"
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         'source "$1"; eval "$2"; capture_job_proof() { printf "%s\\n" "$*" >"$PROOF_PATH"; }; execute_job "$3" "$3-new"',
         "deploy-one-shot-test", str(root / "product_ops/deployment/cloud_run_release_traffic.sh"), function, kind],
        cwd=root, env={**os.environ, "GCP_PROJECT": "p", "GCP_REGION": "r", "ODAY_RELEASE_SHA": RELEASE_SHA,
                       "SCHEDULER_IMAGE": IMAGES["scheduler"], "WORKER_IMAGE": IMAGES["worker"],
                       "API_IMAGE": "registry/api@sha256:" + "f" * 64, "PROOF_PATH": str(proof)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    expected = IMAGES["worker" if kind == "migration" else kind]
    assert proof.read_text().strip() == f"{kind} {kind}-new {expected}"
    assert any(call[:4] == ["run", "jobs", "execute", kind + "-new"] for call in _calls(release_cloud))


def test_successful_resume_and_lost_response_replay_are_idempotent(release_cloud: dict) -> None:
    state = _capture(release_cloud)
    assert _switch(state) == 0
    for kind in ("worker", "scheduler"):
        _receipt(release_cloud, kind)
    data = _cloud_data(release_cloud)
    data["control"]["lose_resume_response"] = "worker-trigger"
    _set_cloud(release_cloud, data)
    assert _resume(release_cloud, state) == 0
    completed = _cloud_data(release_cloud)
    calls = len(_calls(release_cloud))
    assert _resume(release_cloud, state) == 0
    assert _cloud_data(release_cloud) == completed
    assert not any(call[:3] in (["scheduler", "jobs", "pause"], ["scheduler", "jobs", "resume"])
                   for call in _calls(release_cloud)[calls:])


def test_resume_keeps_original_error_and_failed_pause_compensation() -> None:
    from product_ops.deployment.bluegreen_release import resume_schedulers
    targets = [SchedulerTarget(kind + "-trigger", "p", "r", kind=kind) for kind in ("worker", "scheduler")]
    state = ReleaseState(release_id="release", switch_completed_at="completed",
                         scheduler_snapshots={target.job_name: "saved.json" for target in targets},
                         scheduler_bindings={target.job_name: {} for target in targets})
    original = OperationResult(False, "receipt", "original receipt failure")
    compensation = OperationResult(False, "pause_all_schedulers", "actual pause failure")
    with patch("product_ops.deployment.bluegreen_release._run_release_helper", return_value=original), \
         patch("product_ops.deployment.bluegreen_release.pause_all_schedulers", return_value=compensation):
        result = resume_schedulers(targets, release_state=state, state_dir=Path("/unused"),
                                   receipts={target.job_name: Path("/unused/receipt.json") for target in targets})
    assert result.message == "original receipt failure"
    assert result.details["pause_compensation"]["message"] == "actual pause failure"
    assert result.details["pause_compensation"]["success"] is False
    assert result.details["mutation_started"] is True


def test_scheduler_recovery_snapshot_keeps_config_and_rejects_actual_secrets() -> None:
    from product_ops.deployment.cloud_scheduler_trigger import recovery_snapshot
    snapshot = _trigger("worker", "ENABLED")
    snapshot.update(status={"debug": "irrelevant"}, lastAttemptTime="provider timestamp", userUpdateTime="provider metadata")
    recovered = recovery_snapshot(snapshot)
    assert recovered["httpTarget"] == snapshot["httpTarget"]
    assert recovered["retryConfig"] == snapshot["retryConfig"]
    assert not {"status", "lastAttemptTime", "userUpdateTime"}.intersection(recovered)
    snapshot["httpTarget"]["headers"]["Authorization"] = "Bearer actual-secret-value"
    with pytest.raises(ValueError, match="sensitive"):
        recovery_snapshot(snapshot)


def test_rollback_checks_paused_configuration_before_resuming_previous_enabled_trigger(release_cloud: dict) -> None:
    state_path = _capture(release_cloud)
    assert _switch(state_path) == 0
    data = _cloud_data(release_cloud)
    data["control"]["drift_trigger"] = "worker-trigger"
    _set_cloud(release_cloud, data)
    previous_calls = len(_calls(release_cloud))
    assert _rollback(state_path) == 1
    assert not any(call[:4] == ["scheduler", "jobs", "resume", "worker-trigger"]
                   for call in _calls(release_cloud)[previous_calls:])
    assert _cloud_data(release_cloud)["triggers"]["worker-trigger"]["state"] == "PAUSED"


@pytest.mark.parametrize("skip_build", ["false", "true"])
def test_actual_tag_build_entrypoint_pins_and_verifies_the_same_runtime_images(release_cloud: dict, skip_build: str) -> None:
    root = Path(__file__).resolve().parents[2]
    script = (root / "product_ops/deployment/deploy_cloud_run_waji.sh").read_text()
    first = script[script.index("build_publish_sign() {"):script.index("API_SECRET_BINDINGS=")]
    web_start = script.index('build_publish_sign "Web"')
    web = script[web_start:script.index('echo "Deploying immutable Web candidate', web_start)]
    expected = {"API": "registry/api@sha256:" + "f" * 64, "WORKER": IMAGES["worker"],
                "SCHEDULER": IMAGES["scheduler"], "WEB": "registry/web@sha256:" + "a" * 64}
    data = _cloud_data(release_cloud)
    data["artifact_digests"] = {f"registry/{name.lower()}:release": image.split("@", 1)[1]
                                for name, image in expected.items()}
    _set_cloud(release_cloud, data)
    fake_tool = '#!/usr/bin/env bash\nprintf "%s %s\\n" "${0##*/}" "$*" >>"${SUPPLY_CHAIN_LOG}"\n'
    for name in ("docker", "cosign"):
        path = release_cloud["tmp"] / "bin" / name
        path.write_text(fake_tool)
        path.chmod(0o755)
    verify = release_cloud["tmp"] / "delivery_toolchain/security/sign_images.sh"
    verify.parent.mkdir(parents=True)
    verify.write_text(fake_tool)
    verify.chmod(0o755)
    log = release_cloud["tmp"] / "supply-chain.log"
    env = {**os.environ, "SUPPLY_CHAIN_LOG": str(log), "ODAY_RELEASE_SHA": RELEASE_SHA,
           "ODP_DEPLOY_BY_DIGEST": "false", "ODP_SKIP_BUILD": skip_build,
           "API_URL": "https://api.example.test", "ODP_REQUIRE_LIVE_DATA": "true",
           "ODP_DATA_BINDING_MODE": "live", "ODP_PRODUCT_MODE": "production", "GCP_PROJECT": "p", "GCP_REGION": "r",
           "ODP_JOB_VALIDATOR": str(root / "product_ops/deployment/validate_cloud_run_live_deployment.py")}
    env.update({name + "_IMAGE": f"registry/{name.lower()}:release" for name in expected})
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         'source "$1"; eval "$2"; eval "$3"; verify_release_job worker-new "$WORKER_IMAGE" "$ODAY_RELEASE_SHA"; printf "%s\\n" "$API_IMAGE" "$WORKER_IMAGE" "$SCHEDULER_IMAGE" "$WEB_IMAGE"',
         "tag-entrypoint-test", str(root / "product_ops/deployment/cloud_run_release_traffic.sh"), first, web],
        cwd=release_cloud["tmp"], env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-4:] == list(expected.values())
    supply_chain = log.read_text().splitlines()
    for image in expected.values():
        assert "sign_images.sh verify " + image in supply_chain
        assert ("cosign sign --yes " + image in supply_chain) is (skip_build == "false")
    assert not any(call[:2] == ["run", "deploy"] for call in _calls(release_cloud))
    workflow = (root / ".github/workflows/deploy-dev.yml").read_text()
    assert "resolve_image_digest() {" not in workflow
    assert "source product_ops/deployment/cloud_run_release_traffic.sh" in workflow
