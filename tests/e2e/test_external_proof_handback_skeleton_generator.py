from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
GENERATOR = ROOT / "scripts/e2e/generate_external_proof_handback_skeleton.py"
CHECKER = ROOT / "scripts/e2e/check_external_proof_handback_artifact.py"
EXAMPLE_SHA = "1111111111111111111111111111111111111111"

TASK_GUIDANCE_TOKENS = {
    "ODP-EXT-PROD-001": ("production credential", "secret owner", "rotation", "startup validation", "fail closed"),
    "ODP-EXT-PROD-002": ("allowed-use", "license", "production listing", "canonical", "freshness SLA", "watermark"),
    "ODP-EXT-PROD-003": ("production geocoder", "observed timestamp", "confidence mapping", "low-confidence", "rate-limit"),
    "ODP-MAP-STAGE-001": ("remote staging", "live tile endpoint", "attribution", "terms", "tile outage", "list/ranking/detail"),
    "ODP-MAP-STAGE-002": ("remote staging", "live geocoder", "attribution", "terms", "geocoder outage", "listing workflow"),
    "ODP-PV-STAGE-001": ("ODP_STAGING_DEPLOY_URL", "ODP_STAGING_API_URL", "ODP_STAGING_SECRET_OWNER", "ODAY_RELEASE_SHA", "/platform/health", "headRefOid"),
    "ODP-PV-STAGE-002": ("same staging target", "product smoke", "API smoke", "backup", "restore", "rollback", "post-drill"),
}


def run_generator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GENERATOR), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_skeleton_generator_outputs_task_specific_handback_shape() -> None:
    result = run_generator("--task", "ODP-PV-STAGE-002", "--release-sha", EXAMPLE_SHA)

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["task_id"] == "ODP-PV-STAGE-002"
    assert payload["tracking_issue"].endswith("/138")
    assert payload["release_head_ref_oid"] == EXAMPLE_SHA
    assert payload["environment"] == "remote_staging"
    assert {artifact["artifact_type"] for artifact in payload["artifacts"]} == {
        "workflow_run",
        "backup_restore_record",
        "report",
    }
    assert payload["completion_attestation"]["decision"] == "needs_revision"
    payload_text = json.dumps(payload)
    for token in TASK_GUIDANCE_TOKENS["ODP-PV-STAGE-002"]:
        assert token in payload_text
    for forbidden_shortcut in ("mock://", "localhost", "127.0.0.1", "fixture", "mock-live"):
        assert forbidden_shortcut in payload_text


def test_skeleton_generator_writes_all_external_proof_tasks(tmp_path) -> None:
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    expected_task_ids = {entry["task_id"] for entry in queue["queue"]}

    result = run_generator("--task", "ALL", "--output-dir", str(tmp_path), "--release-sha", EXAMPLE_SHA)

    assert result.returncode == 0, result.stdout + result.stderr
    generated = {path.name.removesuffix(".handback.skeleton.json") for path in tmp_path.glob("*.json")}
    assert generated == expected_task_ids

    for task_id in expected_task_ids:
        payload = json.loads((tmp_path / f"{task_id}.handback.skeleton.json").read_text(encoding="utf-8"))
        payload_text = json.dumps(payload)
        for token in TASK_GUIDANCE_TOKENS[task_id]:
            assert token in payload_text


def test_generated_skeleton_is_not_accepted_closeout_artifact(tmp_path) -> None:
    skeleton = tmp_path / "ODP-MAP-STAGE-001.handback.skeleton.json"
    result = run_generator("--task", "ODP-MAP-STAGE-001", "--output-dir", str(tmp_path), "--release-sha", EXAMPLE_SHA)
    assert result.returncode == 0, result.stdout + result.stderr

    check = subprocess.run(
        [sys.executable, str(CHECKER), str(skeleton), "--expected-sha", EXAMPLE_SHA],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert check.returncode == 1
    assert "commands_run[0].exit_code must be 0" in check.stdout
    assert "completion_attestation.decision must be accepted before closeout" in check.stdout
