from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUEUE = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
STATUS_BOARD = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json"
CHECKER = ROOT / "scripts/e2e/check_external_proof_handback_status_board.py"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_external_proof_handback_status_board", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_external_proof_handback_status_board_checker_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "External proof handback status board checks passed." in result.stdout


def test_external_proof_handback_status_board_matches_closeout_queue() -> None:
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    status_board = json.loads(STATUS_BOARD.read_text(encoding="utf-8"))
    queue_entries = {entry["task_id"]: entry for entry in queue["queue"]}
    status_entries = {entry["task_id"]: entry for entry in status_board["tasks"]}

    assert set(queue_entries) == set(status_entries)
    assert status_board["release_target"]["pr"] == 82
    assert "headRefOid" in status_board["release_target"]["authority"]
    assert status_board["source_of_truth"] == "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
    assert status_board["bundle_status"]["status"] == "pending_external_handbacks"

    for task_id, queue_entry in queue_entries.items():
        status_entry = status_entries[task_id]
        assert status_entry["tracking_issue"] == queue_entry["tracking_issue"]
        assert status_entry["status"] == "pending_external_handback"
        assert status_entry["handback_artifact_path"] is None
        assert status_entry["artifact_check_passed"] is False
        assert status_entry["accepted_release_head_ref_oid"] is None
        assert "check_external_proof_handback_artifact.py" in status_entry["artifact_check_command"]
        assert status_entry["next_action"]


def test_external_proof_handback_status_board_rejects_unproven_acceptance_claim() -> None:
    checker = load_checker_module()
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    status_board = json.loads(STATUS_BOARD.read_text(encoding="utf-8"))
    candidate = copy.deepcopy(status_board)
    candidate["tasks"][0]["status"] = "accepted"

    errors = checker.validate_status_board(queue, candidate)

    assert any("accepted handback must set artifact_check_passed true" in error for error in errors)
    assert any("accepted handback missing accepted_release_head_ref_oid" in error for error in errors)
    assert any("accepted must set handback_artifact_path" in error for error in errors)


def test_external_proof_handback_status_board_rejects_queue_drift() -> None:
    checker = load_checker_module()
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    status_board = json.loads(STATUS_BOARD.read_text(encoding="utf-8"))
    candidate = copy.deepcopy(status_board)
    candidate["tasks"].pop()

    errors = checker.validate_status_board(queue, candidate)

    assert any("task ids must match external proof queue" in error for error in errors)
