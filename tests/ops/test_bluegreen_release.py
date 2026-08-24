from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from product_ops.deployment.bluegreen_release import (
    DataPlatformPointer,
    OperationResult,
    ReleaseState,
    SchedulerTarget,
    ServiceTarget,
    atomic_traffic_switch,
    capture_data_platform_pointer,
    capture_traffic_snapshot,
    execute_bluegreen_switch,
    execute_rollback,
    get_tagged_target_from_description,
    main,
    pause_all_schedulers,
    resolve_tagged_target,
    restore_data_platform_pointer,
    restore_traffic_from_snapshot,
    resume_schedulers,
    switch_job_digests,
)


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
        scheduler_digests={"job1": "sha256:abc"},
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
    assert loaded.scheduler_digests == {"job1": "sha256:abc"}
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
                {"tag": "blue", "revisionName": "api-v1", "percent": 100, "url": "https://blue---api.run.app"},
                {"tag": "green", "revisionName": "api-v2", "percent": 0, "url": "https://green---api.run.app"},
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
            {"status": {"traffic": [{"tag": "green", "revisionName": "", "url": "https://green.run.app"}]}},
            "green",
        )

    # Invalid URL
    with pytest.raises(ValueError, match="no HTTPS URL"):
        get_tagged_target_from_description(
            {"status": {"traffic": [{"tag": "green", "revisionName": "rev-1", "url": "http://insecure.app"}]}},
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
    mock_gcloud.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="service not found")
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
        stdout=json.dumps({
            "status": {
                "traffic": [
                    {"tag": "green", "revisionName": "odp-api-green-001", "url": "https://green---odp-api.run.app"}
                ]
            }
        }),
        stderr="",
    )
    res_ok = resolve_tagged_target(svc, "green")
    assert res_ok.success is True
    assert res_ok.details["revision"] == "odp-api-green-001"
    assert res_ok.details["url"] == "https://green---odp-api.run.app"


# ---------------------------------------------------------------------------
# Traffic operations tests
# ---------------------------------------------------------------------------

@patch("product_ops.deployment.bluegreen_release._run_gcloud")
def test_capture_traffic_snapshot(mock_gcloud: MagicMock, tmp_path: Path) -> None:
    svc = ServiceTarget(service="odp-api", project="p", region="r")
    out_file = tmp_path / "snap.json"

    # Dry run
    res_dry = capture_traffic_snapshot(svc, out_file, dry_run=True)
    assert res_dry.success is True
    assert res_dry.dry_run is True
    assert not out_file.exists()

    # Gcloud failure
    mock_gcloud.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="error")
    res_fail = capture_traffic_snapshot(svc, out_file)
    assert res_fail.success is False
    assert "Failed to describe" in res_fail.message

    # Invalid JSON
    mock_gcloud.return_value = subprocess.CompletedProcess([], 0, stdout="invalid", stderr="")
    res_bad = capture_traffic_snapshot(svc, out_file)
    assert res_bad.success is False
    assert "Invalid JSON" in res_bad.message

    # Success
    mock_gcloud.return_value = subprocess.CompletedProcess([], 0, stdout='{"status": {"traffic": []}}', stderr="")
    res_ok = capture_traffic_snapshot(svc, out_file)
    assert res_ok.success is True
    assert out_file.exists()
    assert json.loads(out_file.read_text(encoding="utf-8")) == {"status": {"traffic": []}}


@patch("product_ops.deployment.bluegreen_release._run_gcloud")
def test_atomic_traffic_switch(mock_gcloud: MagicMock) -> None:
    svc = ServiceTarget(service="odp-api", project="p", region="r")

    # Empty green revision
    res_empty = atomic_traffic_switch(svc, "")
    assert res_empty.success is False

    # Dry run
    res_dry = atomic_traffic_switch(svc, "api-green-1", dry_run=True)
    assert res_dry.success is True
    assert res_dry.dry_run is True

    # Gcloud failure
    mock_gcloud.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="permission denied")
    res_fail = atomic_traffic_switch(svc, "api-green-1")
    assert res_fail.success is False
    assert "permission denied" in res_fail.message

    # Gcloud success
    mock_gcloud.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    res_ok = atomic_traffic_switch(svc, "api-green-1")
    assert res_ok.success is True
    mock_gcloud.assert_called_with(
        [
            "run", "services", "update-traffic", "odp-api",
            "--project=p", "--region=r",
            "--to-revisions=api-green-1=100",
            "--quiet",
        ],
        dry_run=False,
    )


@patch("product_ops.deployment.bluegreen_release.subprocess.run")
@patch("product_ops.deployment.bluegreen_release._run_gcloud")
def test_restore_traffic_from_snapshot(mock_gcloud: MagicMock, mock_subproc: MagicMock, tmp_path: Path) -> None:
    svc = ServiceTarget(service="odp-api", project="p", region="r")
    snap_file = tmp_path / "snap.json"

    # Missing snapshot file
    res_missing = restore_traffic_from_snapshot(svc, snap_file)
    assert res_missing.success is False
    assert "not found" in res_missing.message

    snap_file.write_text('{"status": {}}', encoding="utf-8")

    # Helper fails
    mock_subproc.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="helper error")
    res_helper_fail = restore_traffic_from_snapshot(svc, snap_file)
    assert res_helper_fail.success is False
    assert "Failed to compute restore-arg" in res_helper_fail.message

    # Helper returns empty
    mock_subproc.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    res_empty_arg = restore_traffic_from_snapshot(svc, snap_file)
    assert res_empty_arg.success is False
    assert "Empty traffic restore argument" in res_empty_arg.message

    # Dry run with valid helper output
    mock_subproc.return_value = subprocess.CompletedProcess([], 0, stdout="rev-blue=100\n", stderr="")
    res_dry = restore_traffic_from_snapshot(svc, snap_file, dry_run=True)
    assert res_dry.success is True
    assert res_dry.dry_run is True

    # Gcloud failure
    mock_gcloud.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="gcloud fail")
    res_gcloud_fail = restore_traffic_from_snapshot(svc, snap_file)
    assert res_gcloud_fail.success is False
    assert "gcloud fail" in res_gcloud_fail.message

    # Gcloud success
    mock_gcloud.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    res_ok = restore_traffic_from_snapshot(svc, snap_file)
    assert res_ok.success is True
    mock_gcloud.assert_called_with(
        [
            "run", "services", "update-traffic", "odp-api",
            "--project=p", "--region=r",
            "--to-revisions=rev-blue=100",
            "--quiet",
        ],
        dry_run=False,
    )


# ---------------------------------------------------------------------------
# Scheduler operations tests
# ---------------------------------------------------------------------------

@patch("product_ops.deployment.bluegreen_release._run_gcloud")
def test_pause_and_resume_schedulers(mock_gcloud: MagicMock) -> None:
    targets = [
        SchedulerTarget(job_name="job-1", project="p", location="l"),
        SchedulerTarget(job_name="job-2", project="p", location="l"),
    ]

    # Empty targets
    assert pause_all_schedulers([]).success is True
    assert resume_schedulers([]).success is True

    # Dry run
    assert pause_all_schedulers(targets, dry_run=True).dry_run is True
    assert resume_schedulers(targets, dry_run=True).dry_run is True

    # Pause success
    mock_gcloud.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    res_pause = pause_all_schedulers(targets)
    assert res_pause.success is True
    assert "Paused 2 scheduler(s)" in res_pause.message

    # Pause partial failure
    mock_gcloud.side_effect = [
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        subprocess.CompletedProcess([], 1, stdout="", stderr="job-2 not found"),
    ]
    res_pause_fail = pause_all_schedulers(targets)
    assert res_pause_fail.success is False
    assert "Failed to pause 1/2" in res_pause_fail.message

    # Resume success
    mock_gcloud.side_effect = None
    mock_gcloud.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
    res_resume = resume_schedulers(targets)
    assert res_resume.success is True
    assert "Resumed 2 scheduler(s)" in res_resume.message

    # Resume partial failure
    mock_gcloud.side_effect = [
        subprocess.CompletedProcess([], 1, stdout="", stderr="job-1 locked"),
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    ]
    res_resume_fail = resume_schedulers(targets)
    assert res_resume_fail.success is False
    assert "Failed to resume 1/2" in res_resume_fail.message


@patch("product_ops.deployment.bluegreen_release._run_gcloud")
def test_switch_job_digests(mock_gcloud: MagicMock) -> None:
    targets = [
        SchedulerTarget(job_name="sync-job", project="p", location="l"),
    ]

    # Empty digest
    res_empty = switch_job_digests(targets, "")
    assert res_empty.success is False

    # Empty targets
    res_no_targets = switch_job_digests([], "sha256:green")
    assert res_no_targets.success is True

    # Dry run
    res_dry = switch_job_digests(targets, "sha256:green", dry_run=True)
    assert res_dry.success is True
    assert res_dry.dry_run is True

    # Describe failure
    mock_gcloud.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="describe error")
    res_desc_fail = switch_job_digests(targets, "sha256:green")
    assert res_desc_fail.success is False

    # Idempotent skip: body already has target digest
    current_body = json.dumps({"image_digest": "sha256:green", "env": "prod"})
    b64_body = base64.b64encode(current_body.encode("utf-8")).decode("ascii")
    mock_gcloud.return_value = subprocess.CompletedProcess(
        [], 0, stdout=json.dumps({"httpTarget": {"body": b64_body}}), stderr=""
    )
    res_skip = switch_job_digests(targets, "sha256:green")
    assert res_skip.success is True
    assert "sync-job" in res_skip.details["skipped"]
    assert len(res_skip.details["switched"]) == 0

    # Normal update from old digest to new digest (with base64 body)
    old_body = json.dumps({"image_digest": "sha256:blue", "env": "prod"})
    old_b64 = base64.b64encode(old_body.encode("utf-8")).decode("ascii")
    mock_gcloud.side_effect = [
        # Describe
        subprocess.CompletedProcess([], 0, stdout=json.dumps({"httpTarget": {"body": old_b64}}), stderr=""),
        # Update
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    ]
    res_update = switch_job_digests(targets, "sha256:green")
    assert res_update.success is True
    assert "sync-job" in res_update.details["switched"]

    # Raw JSON string body instead of base64
    mock_gcloud.side_effect = [
        subprocess.CompletedProcess([], 0, stdout=json.dumps({"httpTarget": {"body": json.dumps({"image_digest": "sha256:blue"})}}), stderr=""),
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    ]
    res_raw_json = switch_job_digests(targets, "sha256:green")
    assert res_raw_json.success is True

    # Dict body
    mock_gcloud.side_effect = [
        subprocess.CompletedProcess([], 0, stdout=json.dumps({"httpTarget": {"body": {"image_digest": "sha256:blue"}}}), stderr=""),
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    ]
    res_dict = switch_job_digests(targets, "sha256:green")
    assert res_dict.success is True

    # Update command failure
    mock_gcloud.side_effect = [
        subprocess.CompletedProcess([], 0, stdout=json.dumps({"httpTarget": {"body": old_b64}}), stderr=""),
        subprocess.CompletedProcess([], 1, stdout="", stderr="update failed"),
    ]
    res_update_fail = switch_job_digests(targets, "sha256:green")
    assert res_update_fail.success is False


# ---------------------------------------------------------------------------
# Data platform pointer operations tests
# ---------------------------------------------------------------------------

def test_capture_and_restore_data_platform_pointer(tmp_path: Path) -> None:
    pointer = DataPlatformPointer(selector_label="prod-green", snapshot_id="snap-999", namespace="data-prod")
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

@patch("product_ops.deployment.bluegreen_release.resume_schedulers")
@patch("product_ops.deployment.bluegreen_release.switch_job_digests")
@patch("product_ops.deployment.bluegreen_release.atomic_traffic_switch")
def test_execute_bluegreen_switch(
    mock_traffic: MagicMock,
    mock_switch_digests: MagicMock,
    mock_resume_sched: MagicMock,
) -> None:
    api_target = ServiceTarget(service="api", project="p", region="r")
    web_target = ServiceTarget(service="web", project="p", region="r")
    sched_targets = [SchedulerTarget(job_name="j1", project="p", location="r")]
    state = ReleaseState(release_id="rel-1")

    # Success full sequence
    mock_traffic.return_value = OperationResult(success=True, operation="traffic", message="ok")
    mock_switch_digests.return_value = OperationResult(success=True, operation="digests", message="ok")
    mock_resume_sched.return_value = OperationResult(success=True, operation="sched", message="ok")

    results = execute_bluegreen_switch(
        api_target=api_target,
        web_target=web_target,
        green_api_revision="api-green",
        green_web_revision="web-green",
        scheduler_targets=sched_targets,
        green_job_digest="sha256:green",
        release_state=state,
    )
    assert len(results) == 4
    assert all(r.success for r in results)
    assert state.switch_completed_at != ""

    # API traffic failure -> aborts early
    state.switch_completed_at = ""
    mock_traffic.side_effect = [
        OperationResult(success=False, operation="api_traffic", message="api switch failed"),
    ]
    results_api_fail = execute_bluegreen_switch(
        api_target=api_target,
        web_target=web_target,
        green_api_revision="api-green",
        green_web_revision="web-green",
        scheduler_targets=sched_targets,
        green_job_digest="sha256:green",
        release_state=state,
    )
    assert len(results_api_fail) == 1
    assert results_api_fail[0].success is False
    assert state.switch_completed_at == ""


@patch("product_ops.deployment.bluegreen_release.restore_data_platform_pointer")
@patch("product_ops.deployment.bluegreen_release.switch_job_digests")
@patch("product_ops.deployment.bluegreen_release.restore_traffic_from_snapshot")
@patch("product_ops.deployment.bluegreen_release.pause_all_schedulers")
def test_execute_rollback(
    mock_pause: MagicMock,
    mock_restore_traffic: MagicMock,
    mock_switch_digests: MagicMock,
    mock_restore_ptr: MagicMock,
    tmp_path: Path,
) -> None:
    api_target = ServiceTarget(service="api", project="p", region="r")
    web_target = ServiceTarget(service="web", project="p", region="r")
    sched_targets = [SchedulerTarget(job_name="j1", project="p", location="r")]
    api_snap = tmp_path / "api-snap.json"
    web_snap = tmp_path / "web-snap.json"
    ptr_snap = tmp_path / "dp-snap.json"
    state = ReleaseState(
        release_id="rel-1",
        api_traffic_snapshot_path=str(api_snap),
        web_traffic_snapshot_path=str(web_snap),
        data_platform_pointer_snapshot_path=str(ptr_snap),
    )

    mock_pause.return_value = OperationResult(success=True, operation="pause", message="ok")
    mock_restore_traffic.return_value = OperationResult(success=True, operation="restore_traffic", message="ok")
    mock_switch_digests.return_value = OperationResult(success=True, operation="digests", message="ok")
    mock_restore_ptr.return_value = OperationResult(success=True, operation="restore_ptr", message="ok")

    results = execute_rollback(
        api_target=api_target,
        web_target=web_target,
        api_snapshot_path=api_snap,
        web_snapshot_path=web_snap,
        scheduler_targets=sched_targets,
        blue_job_digest="sha256:blue",
        data_platform_pointer_path=ptr_snap,
        release_state=state,
    )
    assert len(results) == 5
    assert all(r.success for r in results)
    assert state.rollback_completed_at != ""


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

@patch("product_ops.deployment.bluegreen_release.capture_traffic_snapshot")
@patch("product_ops.deployment.bluegreen_release.capture_data_platform_pointer")
def test_cli_capture_state(mock_cap_ptr: MagicMock, mock_cap_traffic: MagicMock, tmp_path: Path) -> None:
    mock_cap_traffic.return_value = OperationResult(success=True, operation="cap", message="ok")
    mock_cap_ptr.return_value = OperationResult(success=True, operation="ptr", message="ok")

    code = main([
        "--project=my-p",
        "--region=asia-east1",
        "capture-state",
        "--api-service=api",
        "--web-service=web",
        "--output-dir", str(tmp_path),
        "--release-id=rel-001",
        "--selector-label=v1",
        "--snapshot-id=s1",
        "--namespace=dp",
    ])
    assert code == 0
    state_file = tmp_path / "release-state.json"
    assert state_file.exists()


@patch("product_ops.deployment.bluegreen_release.execute_bluegreen_switch")
def test_cli_switch(mock_switch: MagicMock, tmp_path: Path) -> None:
    state_file = tmp_path / "release-state.json"
    state_file.write_text(ReleaseState(release_id="rel-1").to_json(), encoding="utf-8")

    mock_switch.return_value = [OperationResult(success=True, operation="sw", message="ok")]
    code = main([
        "--project=my-p",
        "--region=asia-east1",
        "switch",
        "--api-service=api",
        "--web-service=web",
        "--green-api-revision=api-g",
        "--green-web-revision=web-g",
        "--green-job-digest=sha256:g",
        "--scheduler-jobs", "job1", "job2",
        "--state-file", str(state_file),
    ])
    assert code == 0


@patch("product_ops.deployment.bluegreen_release.execute_rollback")
def test_cli_rollback(mock_rb: MagicMock, tmp_path: Path) -> None:
    state_file = tmp_path / "release-state.json"
    state = ReleaseState(
        release_id="rel-1",
        api_traffic_snapshot_path=str(tmp_path / "api.json"),
        web_traffic_snapshot_path=str(tmp_path / "web.json"),
    )
    state_file.write_text(state.to_json(), encoding="utf-8")

    mock_rb.return_value = [OperationResult(success=True, operation="rb", message="ok")]
    code = main([
        "--project=my-p",
        "--region=asia-east1",
        "rollback",
        "--api-service=api",
        "--web-service=web",
        "--blue-job-digest=sha256:b",
        "--scheduler-jobs", "job1",
        "--state-file", str(state_file),
    ])
    assert code == 0


@patch("product_ops.deployment.bluegreen_release.resolve_tagged_target")
def test_cli_resolve_tag(mock_resolve: MagicMock) -> None:
    mock_resolve.return_value = OperationResult(success=True, operation="tag", message="ok")
    code = main([
        "--project=my-p",
        "--region=asia-east1",
        "resolve-tag",
        "--service=api",
        "--tag=green",
    ])
    assert code == 0


@patch("product_ops.deployment.bluegreen_release.pause_all_schedulers")
def test_cli_pause_schedulers(mock_pause: MagicMock) -> None:
    mock_pause.return_value = OperationResult(success=True, operation="pause", message="ok")
    code = main([
        "--project=my-p",
        "--region=asia-east1",
        "pause-schedulers",
        "--jobs", "job1", "job2",
    ])
    assert code == 0


@patch("product_ops.deployment.bluegreen_release.resume_schedulers")
def test_cli_resume_schedulers(mock_resume: MagicMock) -> None:
    mock_resume.return_value = OperationResult(success=True, operation="resume", message="ok")
    code = main([
        "--project=my-p",
        "--region=asia-east1",
        "resume-schedulers",
        "--jobs", "job1", "job2",
    ])
    assert code == 0


@patch("product_ops.deployment.bluegreen_release.switch_job_digests")
def test_cli_switch_digests(mock_switch: MagicMock) -> None:
    mock_switch.return_value = OperationResult(success=True, operation="switch", message="ok")
    code = main([
        "--project=my-p",
        "--region=asia-east1",
        "switch-digests",
        "--jobs", "job1",
        "--digest=sha256:green",
    ])
    assert code == 0


@patch("product_ops.deployment.bluegreen_release.capture_data_platform_pointer")
def test_cli_capture_pointer(mock_cap: MagicMock, tmp_path: Path) -> None:
    mock_cap.return_value = OperationResult(success=True, operation="cap", message="ok")
    code = main([
        "--project=my-p",
        "--region=asia-east1",
        "capture-pointer",
        "--selector-label=v1",
        "--snapshot-id=s1",
        "--namespace=dp",
        "--output-file", str(tmp_path / "ptr.json"),
    ])
    assert code == 0


@patch("product_ops.deployment.bluegreen_release.restore_data_platform_pointer")
def test_cli_restore_pointer(mock_rest: MagicMock, tmp_path: Path) -> None:
    mock_rest.return_value = OperationResult(success=True, operation="rest", message="ok")
    snap_file = tmp_path / "ptr.json"
    snap_file.write_text("{}", encoding="utf-8")
    code = main([
        "--project=my-p",
        "--region=asia-east1",
        "restore-pointer",
        "--snapshot-file", str(snap_file),
    ])
    assert code == 0
