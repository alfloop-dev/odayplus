#!/usr/bin/env python3
"""Tooling regression tests for dependency audit timeout and fail-closed boundaries."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from delivery_toolchain.security.dependency_audit import (
    DEFAULT_NPM_TIMEOUT_SECONDS,
    DEFAULT_PIP_TIMEOUT_SECONDS,
    resolve_timeouts,
    run_dependency_audit,
    run_npm_audit,
    run_pip_audit,
)

ROOT = Path(__file__).resolve().parents[2]


def test_resolve_timeouts_defaults() -> None:
    npm_timeout, pip_timeout = resolve_timeouts()
    assert npm_timeout == DEFAULT_NPM_TIMEOUT_SECONDS
    assert pip_timeout == DEFAULT_PIP_TIMEOUT_SECONDS
    assert npm_timeout > 0
    assert pip_timeout > 0


def test_resolve_timeouts_cli_overrides() -> None:
    npm_timeout, pip_timeout = resolve_timeouts(cli_npm_timeout=30.0, cli_pip_timeout=45.0)
    assert npm_timeout == 30.0
    assert pip_timeout == 45.0


def test_resolve_timeouts_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ODP_AUDIT_TIMEOUT", "15.0")
    npm_timeout, pip_timeout = resolve_timeouts()
    assert npm_timeout == 15.0
    assert pip_timeout == 15.0

    monkeypatch.setenv("ODP_NPM_AUDIT_TIMEOUT", "20.0")
    monkeypatch.setenv("ODP_PIP_AUDIT_TIMEOUT", "25.0")
    npm_timeout, pip_timeout = resolve_timeouts()
    assert npm_timeout == 20.0
    assert pip_timeout == 25.0


def test_resolve_timeouts_invalid_negative() -> None:
    with pytest.raises(ValueError, match="npm audit timeout must be positive"):
        resolve_timeouts(cli_npm_timeout=-5.0)

    with pytest.raises(ValueError, match="pip-audit timeout must be positive"):
        resolve_timeouts(cli_pip_timeout=0.0)


def test_run_npm_audit_passes_when_lockfile_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    res = run_npm_audit(root=tmp_path)
    assert res == 0
    captured = capsys.readouterr()
    assert "package-lock.json is not present" in captured.out


def test_run_npm_audit_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert cmd == ["npm", "audit", "--omit=dev", "--audit-level=high"]
        assert kwargs.get("timeout") == 60.0
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="found 0 vulnerabilities", stderr="")

    res = run_npm_audit(root=tmp_path, timeout=60.0, runner=mock_runner)
    assert res == 0
    captured = capsys.readouterr()
    assert "npm audit passed" in captured.out


def test_run_npm_audit_fails_closed_on_timeout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=30.0)

    res = run_npm_audit(root=tmp_path, timeout=30.0, runner=mock_runner)
    assert res != 0
    captured = capsys.readouterr()
    assert "[FAIL CLOSED]" in captured.err
    assert "timed out after 30.0s contacting npm registry" in captured.err
    assert "Failure source: npm registry response timeout" in captured.err


def test_run_npm_audit_fails_closed_on_503_registry_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            returncode=1,
            stdout="",
            stderr="npm ERR! code E503\nnpm ERR! 503 Service Unavailable - GET https://registry.npmjs.org",
        )

    res = run_npm_audit(root=tmp_path, runner=mock_runner)
    assert res != 0
    captured = capsys.readouterr()
    assert "[FAIL CLOSED]" in captured.err
    assert "503 Service Unavailable" in captured.err
    assert "Refusing to ignore registry error" in captured.err


def test_run_npm_audit_fails_closed_on_vulnerabilities(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            returncode=1,
            stdout="2 high severity vulnerabilities found in production dependencies",
            stderr="",
        )

    res = run_npm_audit(root=tmp_path, runner=mock_runner)
    assert res != 0
    captured = capsys.readouterr()
    assert "[FAIL CLOSED]" in captured.err
    assert "npm vulnerabilities found" in captured.err


def test_run_npm_audit_fails_closed_when_npm_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("No such file or directory: 'npm'")

    res = run_npm_audit(root=tmp_path, runner=mock_runner)
    assert res != 0
    captured = capsys.readouterr()
    assert "[FAIL CLOSED]" in captured.err
    assert "npm executable not found" in captured.err


def test_run_pip_audit_success(capsys: pytest.CaptureFixture[str]) -> None:
    def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert cmd == ["uv", "run", "--with", "pip-audit", "pip-audit", "--local"]
        assert kwargs.get("timeout") == 45.0
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="No known vulnerabilities found", stderr="")

    res = run_pip_audit(root=ROOT, timeout=45.0, runner=mock_runner)
    assert res == 0
    captured = capsys.readouterr()
    assert "pip-audit passed" in captured.out


def test_run_pip_audit_fails_closed_on_timeout(capsys: pytest.CaptureFixture[str]) -> None:
    def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=20.0)

    res = run_pip_audit(root=ROOT, timeout=20.0, runner=mock_runner)
    assert res != 0
    captured = capsys.readouterr()
    assert "[FAIL CLOSED]" in captured.err
    assert "pip-audit timed out after 20.0s" in captured.err
    assert "Failure source: PyPI/OSV vulnerability database timeout" in captured.err


def test_run_pip_audit_fails_closed_on_vulnerabilities(capsys: pytest.CaptureFixture[str]) -> None:
    def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            cmd,
            returncode=1,
            stdout="Found 1 known vulnerability in urllib3 (GHSA-xxxx)",
            stderr="",
        )

    res = run_pip_audit(root=ROOT, runner=mock_runner)
    assert res != 0
    captured = capsys.readouterr()
    assert "[FAIL CLOSED]" in captured.err
    assert "Python package vulnerabilities found" in captured.err


def test_run_pip_audit_fails_closed_when_uv_missing(capsys: pytest.CaptureFixture[str]) -> None:
    def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("No such file or directory: 'uv'")

    res = run_pip_audit(root=ROOT, runner=mock_runner)
    assert res != 0
    captured = capsys.readouterr()
    assert "[FAIL CLOSED]" in captured.err
    assert "uv executable not found" in captured.err


def test_run_dependency_audit_fails_closed_if_any_step_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    def mock_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "npm" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="passed", stderr="")
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="failed", stderr="")

    res = run_dependency_audit(root=tmp_path, runner=mock_runner)
    assert res == 1
    captured = capsys.readouterr()
    assert "[FAIL CLOSED] Dependency audit gate failed for: pip-audit (exit 1)" in captured.err


def test_makefile_and_ci_wire_dependency_audit_gate() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    ci_yml = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "delivery_toolchain/security/dependency_audit.py" in makefile
    assert "dependency-audit:" in makefile
    assert "security: bootstrap dependency-audit" in makefile
    assert "make security" in ci_yml
