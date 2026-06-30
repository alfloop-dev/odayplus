from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/e2e/check_release_fleet_dispatch_status.py"


def load_checker_module():
    spec = importlib.util.spec_from_file_location("check_release_fleet_dispatch_status", CHECKER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report_payload(*, check_returncode: int = 0, merge_state: str = "CLEAN") -> dict:
    return {
        "pr": {
            "number": 82,
            "isDraft": True,
            "state": "OPEN",
            "headRefOid": "fc20a1b647861cc81f72e3a2a91bd5c7d7050848",
            "mergeStateStatus": merge_state,
            "url": "https://github.com/alfloop-dev/odayplus/pull/82",
            "statusCheckRollup": [
                {"name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "product-e2e-gate", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        },
        "checks": [
            {
                "label": "external issue sync",
                "command": "python3 scripts/e2e/check_external_proof_issue_sync.py --require-assignees",
                "returncode": check_returncode,
                "output": "External proof issue sync checks passed.",
            },
            {
                "label": "product closeout PR notification",
                "command": "python3 scripts/e2e/check_product_closeout_fleet_notification.py",
                "returncode": 0,
                "output": "Product closeout fleet notification checks passed.",
            },
        ],
    }


def test_release_fleet_dispatch_status_accepts_clean_report() -> None:
    checker = load_checker_module()

    errors = checker.validate_report(report_payload())

    assert errors == []
    rendered = checker.render_markdown(report_payload())
    assert "Release Fleet Dispatch Status" in rendered
    assert "external issue sync" in rendered
    assert "passed" in rendered


def test_release_fleet_dispatch_status_rejects_failed_guard() -> None:
    checker = load_checker_module()

    errors = checker.validate_report(report_payload(check_returncode=1))

    assert any("external issue sync failed" in error for error in errors)


def test_release_fleet_dispatch_status_rejects_unclean_pr() -> None:
    checker = load_checker_module()

    errors = checker.validate_report(report_payload(merge_state="UNSTABLE"))

    assert "PR #82 mergeStateStatus must be CLEAN" in errors


def test_release_fleet_dispatch_status_cli_uses_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "dispatch-status.json"
    fixture.write_text(json.dumps(report_payload(), indent=2), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(CHECKER), "--fixture", str(fixture), "--report"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Release Fleet Dispatch Status" in result.stdout
    assert "product closeout PR notification" in result.stdout
