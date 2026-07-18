from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_external_proof_acceptance_readiness.py"
QUEUE = ROOT / "docs/evidence/PRODUCT_EXTERNAL_PROOF_CLOSEOUT_QUEUE.json"
STATUS_BOARD = ROOT / "docs/evidence/EXTERNAL_PROOF_HANDBACK_STATUS_BOARD.json"


def load_checker_module():
    spec = importlib.util.spec_from_file_location(
        "check_external_proof_acceptance_readiness", CHECKER
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_external_proof_acceptance_readiness_reports_pending_handbacks() -> None:
    checker = load_checker_module()
    queue = load_json(QUEUE)
    status_board = load_json(STATUS_BOARD)

    rows = checker.evaluate_readiness(queue, status_board)
    errors = checker.validate_readiness(queue, status_board, rows)

    assert errors == []
    assert {row["task_id"] for row in rows} == {entry["task_id"] for entry in queue["queue"]}
    assert all(row["missing_evidence"] for row in rows)
    assert all(
        "generate_external_proof_handback_skeleton.py" in row["skeleton_command"] for row in rows
    )
    assert all(
        "check_external_proof_handback_artifact.py" in row["acceptance_command"] for row in rows
    )


def test_external_proof_acceptance_readiness_strict_complete_fails_pending() -> None:
    checker = load_checker_module()
    queue = load_json(QUEUE)
    status_board = load_json(STATUS_BOARD)
    rows = checker.evaluate_readiness(queue, status_board)

    errors = checker.validate_readiness(queue, status_board, rows, strict_complete=True)

    assert any("is not accepted" in error for error in errors)
    assert any("bundle_status must be accepted" in error for error in errors)


def test_external_proof_acceptance_readiness_report_names_commands() -> None:
    checker = load_checker_module()
    queue = load_json(QUEUE)
    status_board = load_json(STATUS_BOARD)
    rows = checker.evaluate_readiness(queue, status_board)

    report = checker.render_markdown(rows, status_board)

    assert "External Proof Acceptance Readiness" in report
    assert "ODP-MAP-STAGE-001" in report
    assert "generate_external_proof_handback_skeleton.py" in report
    assert "check_external_proof_handback_artifact.py" in report
    assert "remote staging live tile endpoint" in report


def test_external_proof_acceptance_readiness_cli_report_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--report"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "External Proof Acceptance Readiness" in result.stdout
    assert "Bundle status: `pending_external_handbacks`" in result.stdout
