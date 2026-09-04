from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from delivery_toolchain.security import npm_audit_gate, pip_audit_gate

# --- CI and Makefile Timeout / Wiring Regressions ---


def test_ci_workflow_enforces_job_timeouts() -> None:
    ci_yml_path = ROOT / ".github/workflows/ci.yml"
    assert ci_yml_path.is_file(), "ci.yml must exist"

    content = ci_yml_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)

    jobs = data.get("jobs", {})
    assert "product" in jobs, "CI must contain product job"
    assert "orchestrator" in jobs, "CI must contain orchestrator job"
    assert "performance-gate" in jobs, "CI must contain performance-gate job"
    assert "product-e2e-gate" in jobs, "CI must contain product-e2e-gate job"

    # Verify all execution jobs have bounded timeout-minutes
    assert jobs["product"].get("timeout-minutes") is not None
    assert 45 <= jobs["product"]["timeout-minutes"] <= 60

    assert jobs["orchestrator"].get("timeout-minutes") is not None
    assert 0 < jobs["orchestrator"]["timeout-minutes"] <= 20

    assert jobs["performance-gate"].get("timeout-minutes") is not None
    assert 0 < jobs["performance-gate"]["timeout-minutes"] <= 30

    assert jobs["product-e2e-gate"].get("timeout-minutes") is not None
    assert 0 < jobs["product-e2e-gate"]["timeout-minutes"] <= 45


def test_makefile_wires_both_audit_gates_without_bypass() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "dependency-audit: bootstrap" in makefile or "dependency-audit:" in makefile
    assert "npm run audit:security" in makefile
    assert "delivery_toolchain/security/pip_audit_gate.py" in makefile
    assert "security: bootstrap dependency-audit" in makefile
    assert "delivery_toolchain/security/dependency_audit.py" not in makefile
    for variable in (
        "NPM_AUDIT_TIMEOUT_SECONDS",
        "PIP_AUDIT_SOCKET_TIMEOUT_SECONDS",
        "PIP_AUDIT_PROCESS_TIMEOUT_SECONDS",
    ):
        assert variable in makefile
    assert "--socket-timeout" in makefile
    assert "--process-timeout" in makefile


def test_audit_gates_have_finite_defaults() -> None:
    assert 0 < npm_audit_gate.DEFAULT_TIMEOUT_SECONDS <= 300
    assert 0 < pip_audit_gate.DEFAULT_SOCKET_TIMEOUT <= 60
    assert 0 < pip_audit_gate.DEFAULT_PROCESS_TIMEOUT <= 300
    assert npm_audit_gate.DEFAULT_ATTEMPTS > 0
    assert pip_audit_gate.DEFAULT_ATTEMPTS > 0


def test_code_boundary_inventory_tracks_audit_gates() -> None:
    inventory = (ROOT / "docs/audits/code-boundary-inventory.csv").read_text(encoding="utf-8")
    assert "delivery_toolchain/security/npm_audit_gate.py" in inventory
    assert "delivery_toolchain/security/pip_audit_gate.py" in inventory


# --- npm audit gate regressions ---


def test_npm_audit_fails_closed_on_timeout() -> None:
    mock_outcome = npm_audit_gate.AuditOutcome(
        npm_audit_gate.UNAVAILABLE, None, "npm audit timed out after 30s"
    )
    code, verdict = npm_audit_gate.evaluate(mock_outcome, "high")
    assert code == npm_audit_gate.EXIT_AUDIT_UNAVAILABLE
    assert "AUDIT UNAVAILABLE" in verdict
    assert "timed out after 30s" in verdict


def test_npm_audit_invocation_uses_process_timeout(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps(
                {
                    "auditReportVersion": 2,
                    "metadata": {"vulnerabilities": {"high": 0, "critical": 0}},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(npm_audit_gate.subprocess, "run", fake_run)
    outcome = npm_audit_gate.run_npm_audit(cwd=ROOT, timeout=41)

    assert outcome.has_report
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert cmd == ["npm", "audit", "--omit=dev", "--json"]
    assert kwargs["timeout"] == 41


def test_npm_audit_accurately_attributes_404_retired_endpoint() -> None:
    # Reproduce B2: 404 on quick audit must NOT be attributed to vulnerabilities
    quick_404 = json.dumps(
        {
            "message": "404 Not Found - POST https://registry.npmjs.org/-/npm/v1/security/audits/quick",
            "method": "POST",
            "uri": "https://registry.npmjs.org/-/npm/v1/security/audits/quick",
            "statusCode": 404,
            "body": "Not Found",
        }
    )
    outcome = npm_audit_gate.classify_audit_output(quick_404, "")
    assert not outcome.has_report
    code, verdict = npm_audit_gate.evaluate(outcome, "high")
    assert code == npm_audit_gate.EXIT_AUDIT_UNAVAILABLE
    assert "statusCode=404" in verdict
    assert "VULNERABILITIES FOUND" not in verdict


def test_npm_audit_fails_closed_on_503_service_unavailable() -> None:
    registry_503 = json.dumps(
        {
            "message": "503 Service Unavailable",
            "statusCode": 503,
            "body": "Service Unavailable",
        }
    )
    outcome = npm_audit_gate.classify_audit_output(registry_503, "")
    assert not outcome.has_report
    code, verdict = npm_audit_gate.evaluate(outcome, "high")
    assert code == npm_audit_gate.EXIT_AUDIT_UNAVAILABLE
    assert "503" in verdict


def test_npm_audit_fails_closed_on_vulnerabilities() -> None:
    report = json.dumps(
        {
            "auditReportVersion": 2,
            "metadata": {
                "vulnerabilities": {"info": 0, "low": 0, "moderate": 0, "high": 2, "critical": 1, "total": 3}
            },
        }
    )
    outcome = npm_audit_gate.classify_audit_output(report, "")
    assert outcome.has_report
    code, verdict = npm_audit_gate.evaluate(outcome, "high")
    assert code == npm_audit_gate.EXIT_VULNERABLE
    assert "VULNERABILITIES FOUND" in verdict
    assert "2 high" in verdict
    assert "1 critical" in verdict


# --- pip audit gate regressions ---


def test_pip_audit_fails_closed_on_process_timeout() -> None:
    outcome = pip_audit_gate.AuditOutcome(
        pip_audit_gate.UNAVAILABLE,
        None,
        "pip-audit process timed out after 180s contacting vulnerability database",
    )
    code, verdict = pip_audit_gate.evaluate(outcome)
    assert code == pip_audit_gate.EXIT_AUDIT_UNAVAILABLE
    assert "AUDIT UNAVAILABLE" in verdict
    assert "timed out" in verdict


def test_pip_audit_fails_closed_on_socket_timeout_message() -> None:
    err_output = "pip-audit: error: HTTPSConnectionPool(host='pypi.org', port=443): Read timed out."
    outcome = pip_audit_gate.classify_pip_audit_output("", err_output)
    assert not outcome.has_report
    code, verdict = pip_audit_gate.evaluate(outcome)
    assert code == pip_audit_gate.EXIT_AUDIT_UNAVAILABLE
    assert "timeout" in verdict.lower()


def test_pip_audit_fails_closed_on_503_service_unavailable() -> None:
    err_output = "pip-audit: error: 503 Service Unavailable: Backing service temporary failure"
    outcome = pip_audit_gate.classify_pip_audit_output("", err_output)
    assert not outcome.has_report
    code, verdict = pip_audit_gate.evaluate(outcome)
    assert code == pip_audit_gate.EXIT_AUDIT_UNAVAILABLE
    assert "503" in verdict


def test_pip_audit_fails_closed_on_vulnerabilities() -> None:
    payload = json.dumps(
        {
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.20.0",
                    "vulns": [{"id": "PYSEC-2023-001", "description": "Cert verification bypass"}],
                }
            ],
            "fixes": [],
        }
    )
    outcome = pip_audit_gate.classify_pip_audit_output(payload, "")
    assert outcome.has_report
    code, verdict = pip_audit_gate.evaluate(outcome)
    assert code == pip_audit_gate.EXIT_VULNERABLE
    assert "VULNERABILITIES FOUND" in verdict
    assert "requests 2.20.0 (PYSEC-2023-001)" in verdict


def test_pip_audit_passes_on_clean_report() -> None:
    payload = json.dumps(
        {
            "dependencies": [
                {"name": "fastapi", "version": "0.110.0", "vulns": []},
                {"name": "pydantic", "version": "2.6.0", "vulns": []},
            ],
            "fixes": [],
        }
    )
    outcome = pip_audit_gate.classify_pip_audit_output(payload, "")
    assert outcome.has_report
    code, verdict = pip_audit_gate.evaluate(outcome)
    assert code == pip_audit_gate.EXIT_OK
    assert "PASS" in verdict
    assert "2 dependencies audited" in verdict


def test_pip_audit_handles_prefixed_stdout() -> None:
    stdout = "No known vulnerabilities found\n" + json.dumps(
        {"dependencies": [{"name": "safe-pkg", "version": "1.0.0", "vulns": []}], "fixes": []}
    )
    outcome = pip_audit_gate.classify_pip_audit_output(stdout, "")
    assert outcome.has_report
    code, verdict = pip_audit_gate.evaluate(outcome)
    assert code == pip_audit_gate.EXIT_OK


def test_pip_audit_fails_closed_on_empty_dependency_report() -> None:
    outcome = pip_audit_gate.classify_pip_audit_output(
        json.dumps({"dependencies": [], "fixes": []}), ""
    )
    assert not outcome.has_report
    code, verdict = pip_audit_gate.evaluate(outcome)
    assert code == pip_audit_gate.EXIT_AUDIT_UNAVAILABLE
    assert "no packages were audited" in verdict


def test_pip_audit_invocation_uses_socket_and_process_timeouts() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps(
                {"dependencies": [{"name": "safe-pkg", "version": "1.0.0", "vulns": []}]}
            ),
            stderr="",
        )

    outcome = pip_audit_gate.run_pip_audit(
        cwd=ROOT, socket_timeout=19, process_timeout=47, runner=fake_runner
    )

    assert outcome.has_report
    assert len(calls) == 1
    cmd, kwargs = calls[0]
    assert "--path" in cmd
    assert pip_audit_gate.DEFAULT_INSTALLATION_PATH in cmd
    assert "--timeout" in cmd
    assert "19" in cmd
    assert kwargs["timeout"] == 47


def test_pip_audit_rejects_unexpected_nonzero_execution_status() -> None:
    def fake_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            3,
            stdout=json.dumps(
                {"dependencies": [{"name": "safe-pkg", "version": "1.0.0", "vulns": []}]}
            ),
            stderr="pip-audit internal error",
        )

    outcome = pip_audit_gate.run_pip_audit(cwd=ROOT, runner=fake_runner)
    code, verdict = pip_audit_gate.evaluate(outcome)
    assert code == pip_audit_gate.EXIT_AUDIT_UNAVAILABLE
    assert "status 3" in verdict


def test_pip_audit_fails_closed_on_missing_executable() -> None:
    def fake_runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("No such file or directory: 'uv'")

    outcome = pip_audit_gate.run_pip_audit(cwd=ROOT, runner=fake_runner)
    assert not outcome.has_report
    code, verdict = pip_audit_gate.evaluate(outcome)
    assert code == pip_audit_gate.EXIT_AUDIT_UNAVAILABLE
    assert "uv executable not found" in verdict


def test_pip_audit_fails_closed_on_invalid_timeout_args() -> None:
    res = subprocess.run(
        [sys.executable, str(ROOT / "delivery_toolchain/security/pip_audit_gate.py"), "--socket-timeout", "-5"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == pip_audit_gate.EXIT_AUDIT_UNAVAILABLE
    assert "[FAIL CLOSED]" in res.stderr
