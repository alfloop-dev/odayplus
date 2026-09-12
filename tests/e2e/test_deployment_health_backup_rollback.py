from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from delivery_toolchain.e2e import verify_deployment_health_backup_rollback as vdr


def test_entrypoint_subprocess_help_executes_from_repo_root() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "delivery_toolchain/e2e/verify_deployment_health_backup_rollback.py",
            "--help",
        ],
        cwd=vdr.ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Run E2E deployment health" in result.stdout


def test_sanitize_text_redacts_tokens_and_secrets() -> None:
    raw = "Bearer secret-token-12345 postgres://user:secretpass@127.0.0.1:5432/db"
    sanitized = vdr.sanitize_text(raw)
    assert "secret-token-12345" not in sanitized
    assert "secretpass" not in sanitized
    assert "Bearer [REDACTED]" in sanitized or "Bearer <redacted>" in sanitized or "[REDACTED]" in sanitized


def test_sanitize_command_redacts_arguments() -> None:
    command = ["docker", "compose", "--token", "Bearer my-secret-auth-token"]
    sanitized = vdr.sanitize_command(command)
    assert "my-secret-auth-token" not in " ".join(sanitized)


def test_collect_env_secrets() -> None:
    env = {
        "ODP_E2E_API_PORT": "8099",
        "API_SECRET_KEY": "super_secret_token_123",
        "DATABASE_PASSWORD": "db_secret_password_456",
        "AUTH_TYPE": "none",
        "KEY_ENABLED": "true",
        "GIT_AUTHOR_NAME": "Lupin",
        "SECRET_NAME": "secret-name-not-value",
        "CONFIG_PATH": "/var/log/app.log",
    }
    secrets = vdr.collect_env_secrets(env)
    assert "super_secret_token_123" in secrets
    assert "db_secret_password_456" in secrets
    assert "8099" not in secrets
    assert "none" not in secrets
    assert "true" not in secrets
    assert "Lupin" not in secrets
    assert "secret-name-not-value" not in secrets
    assert "/var/log/app.log" not in secrets


def test_run_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        vdr.run(["docker", "compose", "ps"], env={}, timeout=0)

    with pytest.raises(ValueError, match="timeout must be positive"):
        vdr.run(["docker", "compose", "ps"], env={}, timeout=-5)


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_run_rejects_non_finite_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout must be positive and finite"):
        vdr.run(["docker", "compose", "ps"], env={}, timeout=timeout)


def test_run_success(monkeypatch) -> None:
    def fake_subprocess_run(command, **kwargs):
        assert kwargs.get("timeout") == 45.0
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="service is running",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    result = vdr.run(["docker", "compose", "ps"], env={}, timeout=45.0, capture=True)
    assert result.returncode == 0
    assert result.stdout == "service is running"
    assert result.timed_out is False
    assert result.timeout_seconds == 45.0


def test_run_timeout_fails_closed_when_check_true(monkeypatch) -> None:
    def fake_subprocess_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs.get("timeout", 10.0),
            output="partial output with Bearer secret-abc",
            stderr="timeout stderr",
        )

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    with pytest.raises(RuntimeError) as excinfo:
        vdr.run(["docker", "compose", "up"], env={}, timeout=30.0, check=True)

    message = str(excinfo.value)
    assert "command timed out after 30.0s" in message
    assert "secret-abc" not in message


def test_run_timeout_bounded_when_check_false(monkeypatch) -> None:
    def fake_subprocess_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs.get("timeout", 15.0),
            output="some log output",
            stderr="timed out",
        )

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    result = vdr.run(
        ["docker", "compose", "logs"],
        env={},
        timeout=15.0,
        check=False,
    )
    assert result.returncode == 124
    assert result.timed_out is True
    assert result.timeout_seconds == 15.0
    assert "command timed out after 15.0s" in result.stderr
    assert "docker compose logs" in result.diagnostic_text()


def test_run_command_failure_fails_closed_when_check_true(monkeypatch) -> None:
    def fake_subprocess_run(command, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=2,
            stdout="",
            stderr="container failed to start with Bearer secret123",
        )

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    with pytest.raises(RuntimeError) as excinfo:
        vdr.run(["docker", "compose", "up"], env={}, timeout=60.0, check=True)

    message = str(excinfo.value)
    assert "command failed (2)" in message
    assert "secret123" not in message


def test_run_command_failure_returns_result_when_check_false(monkeypatch) -> None:
    def fake_subprocess_run(command, **kwargs):
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr="error occurred",
        )

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    result = vdr.run(["docker", "compose", "down"], env={}, timeout=60.0, check=False)
    assert result.returncode == 1
    assert result.timed_out is False


def test_command_result_write_to(tmp_path: Path) -> None:
    completed = subprocess.CompletedProcess(
        args=["docker", "compose", "ps"],
        returncode=0,
        stdout="NAME   IMAGE   STATUS\nweb    app:1   Up\n",
        stderr="",
    )
    result = vdr.CommandResult(completed)
    target = tmp_path / "compose-ps.txt"
    result.write_to(target)
    assert target.read_text(encoding="utf-8") == "NAME   IMAGE   STATUS\nweb    app:1   Up\n"


def test_wait_for_worker_heartbeat_success(monkeypatch) -> None:
    heartbeat_payload = {"worker": "product-e2e-scheduler", "timestamp": "2026-06-29T12:00:00Z"}

    def fake_run(command, **kwargs):
        assert kwargs.get("timeout") == vdr.DEFAULT_HEARTBEAT_EXEC_TIMEOUT
        completed = subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=json.dumps(heartbeat_payload),
            stderr="",
        )
        return vdr.CommandResult(completed)

    monkeypatch.setattr(vdr, "run", fake_run)

    heartbeat = vdr.wait_for_worker_heartbeat(["docker", "compose"], env={})
    assert heartbeat == heartbeat_payload


def test_wait_for_worker_heartbeat_timeout(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        completed = subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )
        return vdr.CommandResult(completed)

    monkeypatch.setattr(vdr, "run", fake_run)
    monkeypatch.setattr(vdr.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="worker heartbeat not observed"):
        vdr.wait_for_worker_heartbeat(["docker", "compose"], env={}, timeout_seconds=0)


def test_wait_for_worker_heartbeat_reports_sanitized_timeout_diagnostics(monkeypatch) -> None:
    secret = "heartbeat-secret-123456"

    def fake_subprocess_run(command, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=command,
            timeout=kwargs["timeout"],
            output=f"Bearer {secret}",
            stderr="worker exec timed out",
        )

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    clock = iter([0.0, 0.0, 61.0])
    monkeypatch.setattr(vdr.time, "time", lambda: next(clock))
    monkeypatch.setattr(vdr.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError) as excinfo:
        vdr.wait_for_worker_heartbeat(
            ["docker", "compose"],
            env={},
            exec_timeout=15.0,
            secret_values=[secret],
        )

    message = str(excinfo.value)
    assert "command timed out after 15.0s" in message
    assert "docker compose exec -T worker" in message
    assert secret not in message


def test_create_and_restore_backup(monkeypatch) -> None:
    backup_data = {"path": "/storage/backups/test.backup", "sha256": "abc123sha", "size_bytes": 1024}
    restore_data = {"restored_path": "/data/test.sqlite3", "sha256": "abc123sha", "size_bytes": 1024}

    def fake_run(command, **kwargs):
        if "exec" in command:
            stdout = json.dumps(backup_data)
        else:
            stdout = json.dumps(restore_data)
        completed = subprocess.CompletedProcess(args=command, returncode=0, stdout=stdout, stderr="")
        return vdr.CommandResult(completed)

    monkeypatch.setattr(vdr, "run", fake_run)

    b = vdr.create_backup(["docker", "compose"], env={})
    assert b == backup_data

    r = vdr.restore_backup(["docker", "compose"], env={})
    assert r == restore_data


def test_write_report_redacts_secrets_and_hashes(tmp_path: Path) -> None:
    diagnostics_dir = tmp_path / "diag"
    diagnostics_dir.mkdir()
    report = {
        "project": "oday-plus-e2e",
        "secret_token": "Bearer super-secret-12345",
        "result": "passed",
    }
    redacted = vdr.write_report(diagnostics_dir, report)
    assert redacted["secret_values_redacted"] is True
    assert "super-secret-12345" not in json.dumps(redacted)
    assert "report_sha256" in redacted

    report_file = diagnostics_dir / vdr.REPORT_NAME
    assert report_file.exists()

    content = json.loads(report_file.read_text(encoding="utf-8"))
    assert content["secret_values_redacted"] is True
    assert "super-secret-12345" not in json.dumps(content)
    assert "report_sha256" in content
    assert content["report_sha256"] == redacted["report_sha256"]


def test_main_full_drill_success(tmp_path: Path, monkeypatch, capsys) -> None:
    diagnostics_dir = tmp_path / "diag"
    executed_commands: list[tuple[list[str], float, bool]] = []

    def fake_run(command, *, env, capture=False, check=True, timeout=vdr.DEFAULT_SUBPROCESS_TIMEOUT, secret_values=()):
        executed_commands.append((command, timeout, check))
        cmd_str = " ".join(command)
        stdout = ""
        if "seed_product_e2e_data.py" in cmd_str:
            stdout = json.dumps({"seeded": True})
        elif "exec -T worker" in cmd_str:
            stdout = json.dumps({"worker": "product-e2e-scheduler"})
        elif "exec -T api" in cmd_str and "sqlite3" in cmd_str:
            stdout = json.dumps({"path": "/storage/backups/product-e2e.sqlite3.backup", "sha256": "sha123", "size_bytes": 100})
        elif "run --rm --no-deps api" in cmd_str:
            stdout = json.dumps({"restored_path": "/data/product-e2e.sqlite3", "sha256": "sha123", "size_bytes": 100})
        elif "ps" in cmd_str:
            stdout = "api Up\nweb Up\nworker Up\n"
        elif "logs" in cmd_str:
            stdout = "logs output\n"

        completed = subprocess.CompletedProcess(args=command, returncode=0, stdout=stdout, stderr="")
        return vdr.CommandResult(completed)

    monkeypatch.setattr(vdr, "run", fake_run)
    monkeypatch.setattr(vdr, "wait_for_json", lambda url, **kwargs: {"status": "ok"})
    monkeypatch.setattr(vdr, "wait_for_url", lambda url, **kwargs: 200)

    cases_state = {"probe_store_id": None}

    def fake_list_cases(api_url):
        cases = [{"store_id": "e2e-store-taipei-001"}]
        if cases_state["probe_store_id"]:
            cases.append({"store_id": cases_state["probe_store_id"]})
        return cases

    def fake_create_probe_case(api_url, store_id):
        cases_state["probe_store_id"] = store_id
        return {"case_id": "case-001", "store_id": store_id}

    def fake_restore_backup(compose, env, **kwargs):
        cases_state["probe_store_id"] = None
        return {"restored_path": "/data/product-e2e.sqlite3", "sha256": "sha123", "size_bytes": 100}

    monkeypatch.setattr(vdr, "list_cases", fake_list_cases)
    monkeypatch.setattr(vdr, "create_probe_case", fake_create_probe_case)
    monkeypatch.setattr(vdr, "restore_backup", fake_restore_backup)

    argv = [
        "--diagnostics-dir",
        str(diagnostics_dir),
        "--subprocess-timeout",
        "50",
        "--compose-up-timeout",
        "100",
        "--cleanup-timeout",
        "30",
    ]

    rc = vdr.main(argv)
    assert rc == 0

    # Verify diagnostic files
    assert (diagnostics_dir / "compose-ps.txt").exists()
    assert (diagnostics_dir / "compose-tail.log").exists()
    report_file = diagnostics_dir / vdr.REPORT_NAME
    assert report_file.exists()
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_data["result"] == "passed"
    assert report_data["rollback"]["probe_removed"] is True

    # Verify stdout was redacted and contains digest and redaction marker
    captured = capsys.readouterr()
    stdout_report = json.loads(captured.out)
    assert stdout_report["result"] == "passed"
    assert stdout_report["secret_values_redacted"] is True
    assert "report_sha256" in stdout_report
    assert stdout_report["report_sha256"] == report_data["report_sha256"]

    # Verify all commands executed with finite timeouts
    for _cmd, timeout, _check in executed_commands:
        assert timeout > 0


def test_main_fails_closed_and_runs_cleanup_on_up_timeout(tmp_path: Path, monkeypatch) -> None:
    diagnostics_dir = tmp_path / "diag"
    cleanup_called = []

    def fake_subprocess_run(command, **kwargs):
        cmd_str = " ".join(command)
        timeout = kwargs.get("timeout", 10.0)
        if "up" in command and "-d" in command and "--build" in command:
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout, output="", stderr="timed out waiting for build")
        if "ps" in command or "logs" in command or "down" in command:
            cleanup_called.append(cmd_str)
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    argv = ["--diagnostics-dir", str(diagnostics_dir)]

    with pytest.raises(RuntimeError, match="command timed out"):
        vdr.main(argv)

    report_file = diagnostics_dir / vdr.REPORT_NAME
    assert report_file.exists()
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_data["result"] == "failed"
    assert "command timed out" in report_data["error"]

    # Verify cleanup commands ran in finally
    assert any("ps" in cmd for cmd in cleanup_called)
    assert any("logs" in cmd for cmd in cleanup_called)
    assert any("down" in cmd for cmd in cleanup_called)


def test_main_cleanup_timeout_does_not_mask_original_failure(tmp_path: Path, monkeypatch) -> None:
    diagnostics_dir = tmp_path / "diag"

    def fake_run(command, *, env, capture=False, check=True, timeout=vdr.DEFAULT_SUBPROCESS_TIMEOUT, secret_values=()):
        cmd_str = " ".join(command)
        if "up -d --build" in cmd_str:
            raise RuntimeError("up failed with internal error")
        if not check:
            # Simulate timeout in finally cleanup
            completed = subprocess.CompletedProcess(args=command, returncode=124, stdout="", stderr="cleanup timed out")
            return vdr.CommandResult(completed, timed_out=True, timeout_seconds=timeout)
        completed = subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
        return vdr.CommandResult(completed)

    monkeypatch.setattr(vdr, "run", fake_run)

    argv = ["--diagnostics-dir", str(diagnostics_dir)]

    with pytest.raises(RuntimeError, match="up failed with internal error"):
        vdr.main(argv)

    report_file = diagnostics_dir / vdr.REPORT_NAME
    assert report_file.exists()
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_data["result"] == "failed"
    assert "up failed with internal error" in report_data["error"]
    assert (diagnostics_dir / "compose-initial-cleanup.txt").exists()
    assert (diagnostics_dir / "compose-cleanup.txt").exists()
    assert "command timed out after" in (diagnostics_dir / "compose-cleanup.txt").read_text(encoding="utf-8")


def test_main_redacts_secrets_in_error_report(tmp_path: Path, monkeypatch) -> None:
    diagnostics_dir = tmp_path / "diag"

    def fake_run(command, *, env, capture=False, check=True, timeout=vdr.DEFAULT_SUBPROCESS_TIMEOUT, secret_values=()):
        cmd_str = " ".join(command)
        if "up -d --build" in cmd_str:
            raise RuntimeError("failed with Bearer secret-auth-token-999")
        completed = subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
        return vdr.CommandResult(completed)

    monkeypatch.setattr(vdr, "run", fake_run)

    argv = ["--diagnostics-dir", str(diagnostics_dir)]

    with pytest.raises(RuntimeError) as excinfo:
        vdr.main(argv)

    assert "secret-auth-token-999" in str(excinfo.value)

    report_file = diagnostics_dir / vdr.REPORT_NAME
    assert report_file.exists()
    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    assert report_data["result"] == "failed"
    assert "secret-auth-token-999" not in report_data["error"]
    assert "[REDACTED]" in report_data["error"] or "<redacted>" in report_data["error"]
