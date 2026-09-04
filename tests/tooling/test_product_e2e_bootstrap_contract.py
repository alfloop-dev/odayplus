#!/usr/bin/env python3
"""Regression tests for the unified Product E2E bootstrap contract.

Verifies:
1. Unified bootstrap script exists, is executable, and locks Node, Python, Chromium,
   and OS dependencies.
2. Chromium prerequisite checker fails closed with actionable remediation when
   Node, npm, Playwright, or Chromium host shared libraries are missing.
3. run_product_e2e.sh invokes preflight before starting Docker services or writing raw receipts.
4. Makefile and CI workflow adhere to the single bootstrap contract.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from delivery_toolchain.e2e.check_chromium_prerequisites import (
    check_chromium_prerequisites,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP_SCRIPT = REPO_ROOT / "delivery_toolchain" / "e2e" / "bootstrap_product_e2e.sh"
CHECK_SCRIPT = REPO_ROOT / "delivery_toolchain" / "e2e" / "check_chromium_prerequisites.py"
RUNNER_SCRIPT = REPO_ROOT / "delivery_toolchain" / "e2e" / "run_product_e2e.sh"
MAKEFILE_PATH = REPO_ROOT / "Makefile"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def test_bootstrap_script_exists_and_executable() -> None:
    assert BOOTSTRAP_SCRIPT.is_file(), f"Missing bootstrap script: {BOOTSTRAP_SCRIPT}"
    assert os.access(BOOTSTRAP_SCRIPT, os.X_OK), f"Bootstrap script is not executable: {BOOTSTRAP_SCRIPT}"

    content = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in content
    assert "uv sync --frozen" in content
    assert "npm ci" in content
    assert "npx playwright install --with-deps chromium" in content
    assert "check_chromium_prerequisites.py" in content


def test_prerequisites_check_script_exists_and_executable() -> None:
    assert CHECK_SCRIPT.is_file(), f"Missing check script: {CHECK_SCRIPT}"
    assert os.access(CHECK_SCRIPT, os.X_OK), f"Check script is not executable: {CHECK_SCRIPT}"


def test_prerequisites_check_passes_in_ready_env() -> None:
    ok, message = check_chromium_prerequisites(REPO_ROOT)
    assert ok is True, f"Prerequisites check failed unexpectedly: {message}"
    assert "Chromium browser and Playwright dependencies verified successfully." in message


def test_prerequisites_fail_closed_when_node_missing() -> None:
    with patch("shutil.which", return_value=None):
        ok, message = check_chromium_prerequisites(REPO_ROOT)
        assert ok is False
        assert "Node.js ('node') executable is not installed" in message
        assert "make product-e2e-bootstrap" in message


def test_prerequisites_fail_closed_when_npm_missing() -> None:
    def fake_which(cmd: str) -> str | None:
        if cmd == "node":
            return "/usr/bin/node"
        return None

    with patch("shutil.which", side_effect=fake_which):
        ok, message = check_chromium_prerequisites(REPO_ROOT)
        assert ok is False
        assert "npm executable is not installed" in message
        assert "make product-e2e-bootstrap" in message


def test_prerequisites_fail_closed_on_missing_host_dependencies() -> None:
    mock_result = MagicMock(
        returncode=1,
        stdout="",
        stderr="error while loading shared libraries: libnspr4.so: cannot open shared object file",
    )
    with patch("shutil.which", return_value="/usr/bin/node"):
        with patch("subprocess.run", return_value=mock_result):
            ok, message = check_chromium_prerequisites(REPO_ROOT)
            assert ok is False
            assert "Chromium browser or host system dependencies check failed" in message
            assert "libnspr4.so" in message
            assert "make product-e2e-bootstrap" in message
            assert "npx playwright install --with-deps chromium" in message


def test_runner_executes_preflight_before_services_and_receipts() -> None:
    runner_content = RUNNER_SCRIPT.read_text(encoding="utf-8")

    preflight_pos = runner_content.find("check_chromium_prerequisites.py")
    assert preflight_pos != -1, "check_chromium_prerequisites.py not called in run_product_e2e.sh"

    compose_up_pos = runner_content.find("up -d --build")
    assert compose_up_pos != -1, "compose up not found in run_product_e2e.sh"
    assert preflight_pos < compose_up_pos, "Preflight check must run before docker compose up"

    playwright_record_pos = runner_content.find("record_playwright_results.py")
    assert playwright_record_pos != -1, "record_playwright_results.py not found in run_product_e2e.sh"
    assert preflight_pos < playwright_record_pos, "Preflight check must run before record_playwright_results.py"

    receipt_gen_pos = runner_content.find("generate_product_e2e_receipt.py")
    assert receipt_gen_pos != -1, "generate_product_e2e_receipt.py not found in run_product_e2e.sh"
    assert preflight_pos < receipt_gen_pos, "Preflight check must run before generate_product_e2e_receipt.py"


def test_makefile_has_product_e2e_bootstrap_target() -> None:
    makefile_content = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "product-e2e-bootstrap:" in makefile_content
    assert "delivery_toolchain/e2e/bootstrap_product_e2e.sh" in makefile_content


def test_ci_workflow_uses_unified_bootstrap_target() -> None:
    ci_content = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    ci_yaml = yaml.safe_load(ci_content)

    jobs = ci_yaml.get("jobs", {})
    assert "product-e2e-gate" in jobs, "product-e2e-gate job must exist in ci.yml"

    steps = jobs["product-e2e-gate"].get("steps", [])
    install_step = next((s for s in steps if s.get("name") == "Install product E2E dependencies"), None)
    assert install_step is not None, "Install product E2E dependencies step must exist in product-e2e-gate"
    assert install_step.get("run") == "make product-e2e-bootstrap"


