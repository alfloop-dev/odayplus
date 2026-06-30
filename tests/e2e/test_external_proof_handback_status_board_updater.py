from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATUS_BOARD = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json"
UPDATER = ROOT / "scripts/e2e/update_external_proof_handback_status_board.py"
ARTIFACT_TEST = ROOT / "tests/e2e/test_external_proof_handback_artifact.py"
EXPECTED_SHA = "89d0ccc19c983a3e8f8e908459c65939a62d4dfb"


def load_artifact_test_module():
    spec = importlib.util.spec_from_file_location("test_external_proof_handback_artifact", ARTIFACT_TEST)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_valid_handback(path: Path, task_id: str = "ODP-MAP-STAGE-001") -> None:
    artifact_test = load_artifact_test_module()
    path.write_text(json.dumps(artifact_test.valid_handback(task_id), indent=2), encoding="utf-8")


def copy_status_board(tmp_path: Path) -> Path:
    status_board = tmp_path / "status-board.json"
    status_board.write_text(STATUS_BOARD.read_text(encoding="utf-8"), encoding="utf-8")
    return status_board


def run_updater(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(UPDATER), *[str(arg) for arg in args]],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def task_entry(status_board_path: Path, task_id: str) -> dict:
    payload = json.loads(status_board_path.read_text(encoding="utf-8"))
    return {entry["task_id"]: entry for entry in payload["tasks"]}[task_id]


def test_update_external_proof_handback_status_board_marks_submitted(tmp_path) -> None:
    status_board = copy_status_board(tmp_path)
    handback = tmp_path / "handback.json"
    write_valid_handback(handback)

    result = run_updater(
        "--status-board",
        status_board,
        "--task",
        "ODP-MAP-STAGE-001",
        "--status",
        "handback_submitted",
        "--handback",
        handback,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "External proof handback status board update checks passed." in result.stdout
    entry = task_entry(status_board, "ODP-MAP-STAGE-001")
    assert entry["status"] == "handback_submitted"
    assert entry["handback_artifact_path"] == str(handback)
    assert entry["artifact_check_passed"] is False
    assert entry["accepted_release_head_ref_oid"] is None


def test_update_external_proof_handback_status_board_accepts_valid_handback(tmp_path) -> None:
    status_board = copy_status_board(tmp_path)
    handback = tmp_path / "handback.json"
    write_valid_handback(handback)

    result = run_updater(
        "--status-board",
        status_board,
        "--task",
        "ODP-MAP-STAGE-001",
        "--status",
        "accepted",
        "--handback",
        handback,
        "--expected-sha",
        EXPECTED_SHA,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    entry = task_entry(status_board, "ODP-MAP-STAGE-001")
    assert entry["status"] == "accepted"
    assert entry["handback_artifact_path"] == str(handback)
    assert entry["artifact_check_passed"] is True
    assert entry["accepted_release_head_ref_oid"] == EXPECTED_SHA
    assert entry["accepted_by"] == "Product Validation"
    assert entry["accepted_at"] == "2026-06-30T02:40:00Z"


def test_update_external_proof_handback_status_board_rejects_invalid_accepted_handback(tmp_path) -> None:
    status_board = copy_status_board(tmp_path)
    handback = tmp_path / "handback.json"
    write_valid_handback(handback)
    payload = json.loads(handback.read_text(encoding="utf-8"))
    payload["completion_attestation"]["decision"] = "needs_revision"
    handback.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    result = run_updater(
        "--status-board",
        status_board,
        "--task",
        "ODP-MAP-STAGE-001",
        "--status",
        "accepted",
        "--handback",
        handback,
        "--expected-sha",
        EXPECTED_SHA,
    )

    assert result.returncode == 1
    assert "accepted status requires a valid handback artifact" in result.stdout
    entry = task_entry(status_board, "ODP-MAP-STAGE-001")
    assert entry["status"] == "pending_external_handback"


def test_update_external_proof_handback_status_board_check_only_does_not_write(tmp_path) -> None:
    status_board = copy_status_board(tmp_path)
    handback = tmp_path / "handback.json"
    write_valid_handback(handback)

    result = run_updater(
        "--status-board",
        status_board,
        "--task",
        "ODP-MAP-STAGE-001",
        "--status",
        "handback_submitted",
        "--handback",
        handback,
        "--check-only",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No file was written because --check-only was set." in result.stdout
    entry = task_entry(status_board, "ODP-MAP-STAGE-001")
    assert entry["status"] == "pending_external_handback"
