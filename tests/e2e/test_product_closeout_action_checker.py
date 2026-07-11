from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_product_closeout_action.py"
QUEUE = ROOT / "docs/evidence/PRODUCT_RELEASE_CLOSEOUT_QUEUE.json"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_product_closeout_action", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def queue_payload() -> dict:
    return json.loads(QUEUE.read_text(encoding="utf-8"))


def status_payload(*, xcut_status: str = "in_progress") -> dict:
    return {
        "tasks": [
            {
                "id": "ODP-FE-XCUT-001",
                "status": xcut_status,
                "owner": "Antigravity3",
                "reviewer": "Antigravity2",
            },
            {
                "id": "ODP-FE-EXP-001",
                "status": "review",
                "owner": "Codex",
                "reviewer": "Claude",
            },
        ]
    }


def pr_payload(*, conclusion: str = "SUCCESS", merge_state: str = "CLEAN") -> dict:
    return {
        "number": 82,
        "state": "OPEN",
        "isDraft": True,
        "headRefOid": "b7b082d11a9fa2050de566382dd2392ea3ad1927",
        "mergeStateStatus": merge_state,
        "statusCheckRollup": [
            {"name": "ci", "status": "COMPLETED", "conclusion": conclusion},
            {"name": "product-e2e-gate", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ],
    }


def test_validate_closeout_action_accepts_ready_owner_handoff() -> None:
    checker = load_checker_module()

    errors = checker.validate_closeout_action(
        queue_payload(),
        status_payload(),
        task_id="ODP-FE-XCUT-001",
        actor="Antigravity3",
        action_type="owner_handoff",
        pr_payload=pr_payload(),
    )

    assert errors == []


def test_validate_closeout_action_rejects_reviewer_before_handoff() -> None:
    checker = load_checker_module()

    errors = checker.validate_closeout_action(
        queue_payload(),
        status_payload(xcut_status="in_progress"),
        task_id="ODP-FE-XCUT-001",
        actor="Antigravity2",
        action_type="reviewer_approve_or_reopen",
        pr_payload=pr_payload(),
    )

    assert any("is not ready" in error for error in errors)


def test_validate_closeout_action_rejects_wrong_actor() -> None:
    checker = load_checker_module()

    errors = checker.validate_closeout_action(
        queue_payload(),
        status_payload(),
        task_id="ODP-FE-XCUT-001",
        actor="Claude",
        action_type="owner_handoff",
        pr_payload=pr_payload(),
    )

    assert any("no closeout queue entry" in error for error in errors)


def test_validate_closeout_action_rejects_failed_pr_check() -> None:
    checker = load_checker_module()

    errors = checker.validate_closeout_action(
        queue_payload(),
        status_payload(),
        task_id="ODP-FE-XCUT-001",
        actor="Antigravity3",
        action_type="owner_handoff",
        pr_payload=pr_payload(conclusion="FAILURE"),
    )

    assert any("check 'ci' must be COMPLETED/SUCCESS" in error for error in errors)


def test_product_closeout_action_checker_cli_uses_fixture_inputs(tmp_path: Path) -> None:
    status_path = tmp_path / "ai-status.json"
    pr_path = tmp_path / "pr.json"
    status_path.write_text(json.dumps(status_payload(), indent=2), encoding="utf-8")
    pr_path.write_text(json.dumps(pr_payload(), indent=2), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--task",
            "ODP-FE-XCUT-001",
            "--actor",
            "Antigravity3",
            "--action-type",
            "owner_handoff",
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
    assert "Product closeout action preflight passed" in result.stdout
