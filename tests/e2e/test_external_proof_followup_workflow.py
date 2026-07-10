from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_external_proof_followup_workflow.py"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_external_proof_followup_workflow", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_external_proof_followup_workflow_contract_is_valid() -> None:
    checker = load_checker_module()

    assert checker.validate_local_workflow() == []


def test_live_external_proof_followup_workflow_requires_active_default_branch_workflow() -> None:
    checker = load_checker_module()

    errors = checker.validate_live_workflow(
        [
            {
                "name": "CI",
                "state": "active",
                "path": ".github/workflows/ci.yml",
            }
        ]
    )

    assert any("not listed by GitHub Actions" in error for error in errors)


def test_live_external_proof_followup_workflow_rejects_inactive_state() -> None:
    checker = load_checker_module()

    errors = checker.validate_live_workflow(
        [
            {
                "name": "External Proof Follow-up",
                "state": "disabled_manually",
                "path": ".github/workflows/external-proof-followup.yml",
            }
        ]
    )

    assert "state must be active" in errors[0]


def test_external_proof_followup_workflow_cli_local_json() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["local_ok"] is True
    assert payload["live_checked"] is False
