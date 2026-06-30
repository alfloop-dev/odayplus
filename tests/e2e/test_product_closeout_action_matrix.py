from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "scripts/e2e/check_product_closeout_action_matrix.py"
ACTION_TEST = ROOT / "tests/e2e/test_product_closeout_action_checker.py"
QUEUE = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_closeout_action_matrix_marks_ready_and_waiting_rows() -> None:
    matrix = load_module(MATRIX, "check_product_closeout_action_matrix")
    action_test = load_module(ACTION_TEST, "test_product_closeout_action_checker")
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))

    rows = matrix.evaluate_matrix(
        queue,
        action_test.status_payload(xcut_status="in_progress"),
        pr_payload=action_test.pr_payload(),
    )
    by_key = {(row["task_id"], row["actor"], row["action_type"]): row for row in rows}

    assert by_key[("ODP-FE-XCUT-001", "Claude2", "owner_handoff")]["readiness"] == "ready"
    assert by_key[("ODP-FE-XCUT-001", "Codex", "reviewer_approve_or_reopen")]["readiness"] == "waiting"


def test_closeout_action_matrix_blocks_on_pr_checks() -> None:
    matrix = load_module(MATRIX, "check_product_closeout_action_matrix")
    action_test = load_module(ACTION_TEST, "test_product_closeout_action_checker")
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))

    rows = matrix.evaluate_matrix(
        queue,
        action_test.status_payload(xcut_status="in_progress"),
        pr_payload=action_test.pr_payload(conclusion="FAILURE"),
    )

    assert any(row["readiness"] == "blocked_by_pr_checks" for row in rows)


def test_closeout_action_matrix_cli_reports_markdown(tmp_path: Path) -> None:
    action_test = load_module(ACTION_TEST, "test_product_closeout_action_checker")
    status_path = tmp_path / "ai-status.json"
    pr_path = tmp_path / "pr.json"
    status_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": "ODP-PV-008", "status": "review", "owner": "Codex2", "reviewer": "Human/Ops"},
                    {"id": "ODP-FE-XCUT-001", "status": "in_progress", "owner": "Claude2", "reviewer": "Codex"},
                    {"id": "ODP-FE-R0-001", "status": "review_approved", "owner": "Claude", "reviewer": "Codex"},
                    {"id": "ODP-FE-EXP-001", "status": "review", "owner": "Codex", "reviewer": "Claude"},
                    {"id": "ODP-FE-ASSET-001", "status": "in_progress", "owner": "Claude", "reviewer": "Codex2"},
                    {
                        "id": "ODP-FE-XCUT-DOMAIN-001",
                        "status": "review_approved",
                        "owner": "Claude",
                        "reviewer": "Codex2",
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    pr_path.write_text(json.dumps(action_test.pr_payload(), indent=2), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(MATRIX),
            "--status-path",
            str(status_path),
            "--pr-json",
            str(pr_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "# Product Closeout Action Matrix" in result.stdout
    assert "ODP-FE-XCUT-001" in result.stdout
    assert "ready" in result.stdout
    assert "waiting" in result.stdout
