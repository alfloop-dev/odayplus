#!/usr/bin/env python3
"""Verify E2E deployment health, backup/restore, and data rollback evidence."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from delivery_toolchain.release.release_receipts import (  # noqa: E402
    _is_sensitive_key,
    redact,
    redact_secrets,
)

COMPOSE_FILE = "infra/docker/docker-compose.e2e.yml"
DB_PATH = "/data/product-e2e.sqlite3"
BACKUP_PATH = "/storage/backups/product-e2e.sqlite3.backup"
REPORT_NAME = "deployment-health-backup-rollback-report.json"

DEFAULT_SUBPROCESS_TIMEOUT: float = float(os.environ.get("ODP_E2E_SUBPROCESS_TIMEOUT", "120.0"))
DEFAULT_COMPOSE_UP_TIMEOUT: float = float(os.environ.get("ODP_E2E_COMPOSE_UP_TIMEOUT", "300.0"))
DEFAULT_CLEANUP_TIMEOUT: float = float(os.environ.get("ODP_E2E_CLEANUP_TIMEOUT", "60.0"))
DEFAULT_SEED_TIMEOUT: float = float(os.environ.get("ODP_E2E_SEED_TIMEOUT", "180.0"))
DEFAULT_DIAGNOSTICS_TIMEOUT: float = float(os.environ.get("ODP_E2E_DIAGNOSTICS_TIMEOUT", "30.0"))
DEFAULT_BACKUP_TIMEOUT: float = float(os.environ.get("ODP_E2E_BACKUP_TIMEOUT", "60.0"))
DEFAULT_RESTORE_TIMEOUT: float = float(os.environ.get("ODP_E2E_RESTORE_TIMEOUT", "60.0"))
DEFAULT_HEARTBEAT_EXEC_TIMEOUT: float = float(os.environ.get("ODP_E2E_HEARTBEAT_EXEC_TIMEOUT", "15.0"))


def sanitize_text(text: str, *, secret_values: Sequence[str] = ()) -> str:
    if not text:
        return ""
    return str(redact(text, secret_values=secret_values))


def sanitize_command(command: Sequence[str], *, secret_values: Sequence[str] = ()) -> list[str]:
    return [str(redact(arg, secret_values=secret_values)) for arg in command]


IGNORED_SECRET_VALUES = frozenset(
    {
        "true",
        "false",
        "none",
        "null",
        "disabled",
        "enabled",
        "default",
        "undefined",
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
    }
)


def collect_env_secrets(env: Mapping[str, str], *, min_length: int = 8) -> list[str]:
    secrets: list[str] = []
    for k, v in env.items():
        if not v or len(v) < min_length:
            continue
        v_stripped = v.strip()
        if v_stripped.lower() in IGNORED_SECRET_VALUES:
            continue
        if v_stripped.isdigit():
            continue
        if (v_stripped.startswith("/") or v_stripped.startswith("./")) and not any(
            s in v_stripped.lower() for s in ("secret", "token", "password", "key", "cred")
        ):
            continue
        if _is_sensitive_key(k):
            secrets.append(v)
    return secrets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run E2E deployment health, backup/restore, and rollback proof."
    )
    parser.add_argument("--project", default=os.environ.get("ODP_E2E_PROJECT", "oday-plus-e2e-pv014"))
    parser.add_argument("--api-port", default=os.environ.get("ODP_E2E_API_PORT", "8099"))
    parser.add_argument("--web-port", default=os.environ.get("ODP_E2E_WEB_PORT", "3100"))
    parser.add_argument(
        "--source-stub-port",
        default=os.environ.get("ODP_E2E_SOURCE_STUB_PORT", "8077"),
    )
    parser.add_argument(
        "--diagnostics-dir",
        default=os.environ.get(
            "ODP_E2E_DIAGNOSTICS_DIR",
            ".odp_data/deployment-health-backup-rollback",
        ),
    )
    parser.add_argument("--keep-stack", action="store_true")
    parser.add_argument(
        "--subprocess-timeout",
        type=float,
        default=DEFAULT_SUBPROCESS_TIMEOUT,
        help="Default timeout for subprocess commands in seconds.",
    )
    parser.add_argument(
        "--compose-up-timeout",
        type=float,
        default=DEFAULT_COMPOSE_UP_TIMEOUT,
        help="Timeout for docker compose up --build in seconds.",
    )
    parser.add_argument(
        "--cleanup-timeout",
        type=float,
        default=DEFAULT_CLEANUP_TIMEOUT,
        help="Timeout for cleanup docker compose operations in seconds.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    diagnostics_dir = ROOT / args.diagnostics_dir
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    compose = [
        "docker",
        "compose",
        "-p",
        args.project,
        "-f",
        COMPOSE_FILE,
    ]
    env = {
        **os.environ,
        "ODP_E2E_API_PORT": str(args.api_port),
        "ODP_E2E_WEB_PORT": str(args.web_port),
        "ODP_E2E_SOURCE_STUB_PORT": str(args.source_stub_port),
    }
    env_secrets = collect_env_secrets(env)
    api_url = f"http://127.0.0.1:{args.api_port}"
    web_url = f"http://127.0.0.1:{args.web_port}"
    source_url = f"http://127.0.0.1:{args.source_stub_port}"
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "project": args.project,
        "compose_file": COMPOSE_FILE,
        "diagnostics_dir": str(diagnostics_dir),
    }

    try:
        initial_cleanup = run(
            compose + ["down", "--remove-orphans", "--volumes"],
            env=env,
            check=False,
            timeout=args.cleanup_timeout,
            secret_values=env_secrets,
        )
        initial_cleanup.write_to(diagnostics_dir / "compose-initial-cleanup.txt")
        run(
            compose + ["up", "-d", "--build"],
            env=env,
            timeout=args.compose_up_timeout,
            secret_values=env_secrets,
        )
        wait_for_json(f"{api_url}/platform/health")
        wait_for_url(f"{web_url}/")
        wait_for_json(f"{source_url}/external/listing_raw_snapshot.valid.json")

        seed = run(
            [
                sys.executable,
                "delivery_toolchain/e2e/seed_product_e2e_data.py",
                "--wait",
                "--api-url",
                api_url,
                "--source-stub-url",
                source_url,
                "--diagnostics-dir",
                str(diagnostics_dir),
            ],
            env=env,
            capture=True,
            timeout=DEFAULT_SEED_TIMEOUT,
            secret_values=env_secrets,
        )
        report["seed_stdout"] = seed.stdout.strip()
        health = {
            "api": wait_for_json(f"{api_url}/platform/health"),
            "web_status": wait_for_url(f"{web_url}/"),
            "source_fixture": wait_for_json(f"{source_url}/external/listing_raw_snapshot.valid.json"),
            "worker": wait_for_worker_heartbeat(compose, env, secret_values=env_secrets),
        }
        report["health"] = health

        before_cases = list_cases(api_url)
        backup = create_backup(compose, env, timeout=DEFAULT_BACKUP_TIMEOUT, secret_values=env_secrets)
        report["backup"] = backup

        probe_store_id = f"pv014-rollback-probe-{int(time.time())}"
        probe_case = create_probe_case(api_url, probe_store_id)
        after_probe_cases = list_cases(api_url)
        assert_case_present(after_probe_cases, probe_store_id)

        run(
            compose + ["stop", "web", "worker", "api"],
            env=env,
            timeout=args.subprocess_timeout,
            secret_values=env_secrets,
        )
        restore = restore_backup(compose, env, timeout=DEFAULT_RESTORE_TIMEOUT, secret_values=env_secrets)
        run(
            compose + ["up", "-d", "api", "web", "worker"],
            env=env,
            timeout=args.subprocess_timeout,
            secret_values=env_secrets,
        )
        wait_for_json(f"{api_url}/platform/health")
        wait_for_url(f"{web_url}/")
        restored_cases = list_cases(api_url)

        if has_store(restored_cases, probe_store_id):
            raise RuntimeError("rollback probe still exists after restoring the backup")
        if not has_store(restored_cases, "e2e-store-taipei-001"):
            raise RuntimeError("seed AVM case is missing after restore")

        report["rollback"] = {
            "probe_store_id": probe_store_id,
            "probe_case_id": probe_case["case_id"],
            "case_count_before_probe": len(before_cases),
            "case_count_after_probe": len(after_probe_cases),
            "case_count_after_restore": len(restored_cases),
            "probe_removed": True,
            "seed_case_preserved": True,
            "restore": restore,
        }
        report["unsupported_or_documented"] = {
            "model_artifact_rollback": "not mutated by this deployment drill; Learning Hub alias rollback is covered by PV-007 product E2E",
            "policy_rollback": "policy files are immutable in the image for this E2E stack; image rollback is represented by redeploying the previous image tag",
            "remote_staging_rollout": "not configured because ODP_STAGING_DEPLOY_URL/host variables are placeholders",
        }
        report["result"] = "passed"
        final_report = write_report(diagnostics_dir, report, secret_values=env_secrets)
        print(json.dumps(final_report, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        report["result"] = "failed"
        report["error"] = sanitize_text(str(exc), secret_values=env_secrets)
        write_report(diagnostics_dir, report, secret_values=env_secrets)
        raise
    finally:
        run(
            compose + ["ps"],
            env=env,
            capture=True,
            check=False,
            timeout=DEFAULT_DIAGNOSTICS_TIMEOUT,
            secret_values=env_secrets,
        ).write_to(diagnostics_dir / "compose-ps.txt")
        run(
            compose + ["logs", "--no-color", "--tail=200"],
            env=env,
            capture=True,
            check=False,
            timeout=DEFAULT_DIAGNOSTICS_TIMEOUT,
            secret_values=env_secrets,
        ).write_to(diagnostics_dir / "compose-tail.log")
        if not args.keep_stack and os.environ.get("ODP_E2E_KEEP_STACK") != "1":
            final_cleanup = run(
                compose + ["down", "--remove-orphans", "--volumes"],
                env=env,
                check=False,
                timeout=args.cleanup_timeout,
                secret_values=env_secrets,
            )
            final_cleanup.write_to(diagnostics_dir / "compose-cleanup.txt")


class CommandResult:
    def __init__(
        self,
        completed: subprocess.CompletedProcess[str],
        *,
        timed_out: bool = False,
        timeout_seconds: float | None = None,
        secret_values: Sequence[str] = (),
        command: Sequence[str] | None = None,
    ) -> None:
        self.returncode = completed.returncode
        self.stdout = sanitize_text(completed.stdout or "", secret_values=secret_values)
        self.stderr = sanitize_text(completed.stderr or "", secret_values=secret_values)
        self.timed_out = timed_out
        self.timeout_seconds = timeout_seconds
        raw_command = command
        if raw_command is None and isinstance(completed.args, (list, tuple)):
            raw_command = [str(arg) for arg in completed.args]
        self.command = sanitize_command(raw_command or (), secret_values=secret_values)

    def timeout_diagnostic(self) -> str:
        command = " ".join(self.command)
        command_suffix = f": {command}" if command else ""
        if self.timeout_seconds is None:
            return f"command timed out{command_suffix}"
        return f"command timed out after {self.timeout_seconds}s{command_suffix}"

    def diagnostic_text(self) -> str:
        """Return sanitized command diagnostics, including bounded failures."""
        output = self.stderr.strip()
        if self.timed_out:
            prefix = self.timeout_diagnostic()
            if output.startswith(prefix):
                return output
            return f"{prefix}\n{output}".strip()
        if self.returncode != 0:
            command = " ".join(self.command)
            prefix = f"command failed ({self.returncode})"
            if command:
                prefix += f": {command}"
            if output.startswith(prefix):
                return output
            return f"{prefix}\n{output}".strip()
        return output

    def write_to(self, path: Path) -> None:
        if self.timed_out or self.returncode != 0:
            output = self.stdout
            diagnostic = self.diagnostic_text()
            if output and diagnostic and not output.endswith("\n"):
                output += "\n"
            output += diagnostic
            if output and not output.endswith("\n"):
                output += "\n"
        else:
            output = self.stdout + self.stderr
        path.write_text(output, encoding="utf-8")


def run(
    command: list[str],
    *,
    env: dict[str, str],
    capture: bool = False,
    check: bool = True,
    timeout: float = DEFAULT_SUBPROCESS_TIMEOUT,
    secret_values: Sequence[str] = (),
) -> CommandResult:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"subprocess timeout must be positive and finite, got {timeout}")

    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raw_stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.output if isinstance(exc.output, str) else "")
        raw_stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        output = sanitize_text(f"{raw_stdout}\n{raw_stderr}".strip(), secret_values=secret_values)
        cmd_str = " ".join(sanitize_command(command, secret_values=secret_values))
        diag = f"command timed out after {timeout}s: {cmd_str}"
        if output:
            diag = f"{diag}\n{output}"

        if check:
            raise RuntimeError(diag) from exc

        completed = subprocess.CompletedProcess(
            args=command,
            returncode=124,
            stdout="",
            stderr=diag + "\n",
        )
        return CommandResult(
            completed,
            timed_out=True,
            timeout_seconds=timeout,
            secret_values=secret_values,
            command=command,
        )
    except Exception as exc:
        cmd_str = " ".join(sanitize_command(command, secret_values=secret_values))
        diag = sanitize_text(f"{type(exc).__name__}: {exc}", secret_values=secret_values)
        if check:
            raise RuntimeError(f"command failed to start: {cmd_str}\n{diag}") from exc
        completed = subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr=f"{cmd_str}\n{diag}\n",
        )
        return CommandResult(
            completed,
            timed_out=False,
            timeout_seconds=timeout,
            secret_values=secret_values,
            command=command,
        )

    if check and completed.returncode != 0:
        output = sanitize_text(f"{completed.stdout or ''}\n{completed.stderr or ''}".strip(), secret_values=secret_values)
        cmd_str = " ".join(sanitize_command(command, secret_values=secret_values))
        raise RuntimeError(f"command failed ({completed.returncode}): {cmd_str}\n{output}".strip())

    return CommandResult(
        completed,
        timed_out=False,
        timeout_seconds=timeout,
        secret_values=secret_values,
        command=command,
    )


def wait_for_json(url: str, *, timeout_seconds: int = 120) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return get_json(url)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def wait_for_url(url: str, *, timeout_seconds: int = 120) -> int:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=10) as response:
                return int(response.status)
        except (
            ConnectionResetError,
            HTTPError,
            URLError,
            TimeoutError,
            http.client.RemoteDisconnected,
        ) as exc:
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def get_json(url: str, *, timeout: int = 10) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "x-subject-id": "verify-backup-rollback",
            "x-roles": "finance_legal,expansion_user,operations_manager,auditor,data_owner,platform_admin",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any], *, correlation_id: str, timeout: int = 20) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-correlation-id": correlation_id,
            "x-subject-id": "verify-backup-rollback",
            "x-roles": "finance_legal,expansion_user,operations_manager,auditor,data_owner,platform_admin",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def list_cases(api_url: str) -> list[dict[str, Any]]:
    payload = get_json(f"{api_url}/avm/cases")
    return list(payload["items"])


def has_store(cases: list[dict[str, Any]], store_id: str) -> bool:
    return any(case.get("store_id") == store_id for case in cases)


def assert_case_present(cases: list[dict[str, Any]], store_id: str) -> None:
    if not has_store(cases, store_id):
        raise RuntimeError(f"expected AVM case for {store_id}")


def create_probe_case(api_url: str, store_id: str) -> dict[str, Any]:
    return post_json(
        f"{api_url}/avm/cases",
        {
            "store_id": store_id,
            "gm_ttm": 100_000,
            "forecast_gm_next_12m": 105_000,
            "asset_book_value": 50_000,
            "equipment_fair_value": 20_000,
            "lease_liability": 5_000,
            "working_capital": 7_000,
            "comparable_multiples": [2.5, 2.7],
            "liquidity_discount": 0.1,
            "quality_score": 0.9,
            "source_snapshot_ids": ["pv014-rollback-probe"],
            "prediction_origin_time": "2026-06-29T00:00:00Z",
            "created_by": "pv014-rollback-probe",
            "idempotency_key": store_id,
        },
        correlation_id="corr-pv014-backup-restore-rollback",
    )


def wait_for_worker_heartbeat(
    compose: list[str],
    env: dict[str, str],
    *,
    timeout_seconds: int = 60,
    exec_timeout: float = DEFAULT_HEARTBEAT_EXEC_TIMEOUT,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_output = ""
    last_diagnostic = ""
    while time.time() < deadline:
        result = run(
            compose
            + [
                "exec",
                "-T",
                "worker",
                "python",
                "-c",
                (
                    "from pathlib import Path; import json; "
                    "p=Path('/storage/worker-heartbeat.jsonl'); "
                    "print(p.read_text().strip().splitlines()[-1] if p.exists() and p.read_text().strip() else '')"
                ),
            ],
            env=env,
            capture=True,
            check=False,
            timeout=exec_timeout,
            secret_values=secret_values,
        )
        last_output = result.stdout.strip()
        if result.timed_out or result.returncode != 0:
            last_diagnostic = result.diagnostic_text()
        if last_output:
            try:
                return json.loads(last_output)
            except json.JSONDecodeError:
                pass
        time.sleep(2)
    message = f"worker heartbeat not observed: {last_output}"
    if last_diagnostic:
        message += f"\nlast subprocess diagnostic: {last_diagnostic}"
    raise RuntimeError(message)


def create_backup(
    compose: list[str],
    env: dict[str, str],
    *,
    timeout: float = DEFAULT_BACKUP_TIMEOUT,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    code = (
        "from pathlib import Path; import hashlib, json, sqlite3; "
        f"src=Path('{DB_PATH}'); dst=Path('{BACKUP_PATH}'); dst.parent.mkdir(parents=True, exist_ok=True); "
        "dst.unlink(missing_ok=True); "
        "source=sqlite3.connect(src); backup=sqlite3.connect(dst); "
        "source.backup(backup); backup.close(); source.close(); "
        "digest=hashlib.sha256(dst.read_bytes()).hexdigest(); "
        "print(json.dumps({'path': str(dst), 'sha256': digest, 'size_bytes': dst.stat().st_size}))"
    )
    result = run(
        compose + ["exec", "-T", "api", "python", "-c", code],
        env=env,
        capture=True,
        timeout=timeout,
        secret_values=secret_values,
    )
    return parse_last_json_line(result.stdout)


def restore_backup(
    compose: list[str],
    env: dict[str, str],
    *,
    timeout: float = DEFAULT_RESTORE_TIMEOUT,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    code = (
        "from pathlib import Path; import hashlib, json, shutil; "
        f"src=Path('{BACKUP_PATH}'); dst=Path('{DB_PATH}'); "
        "Path(str(dst) + '-wal').unlink(missing_ok=True); Path(str(dst) + '-shm').unlink(missing_ok=True); "
        "shutil.copy2(src, dst); "
        "digest=hashlib.sha256(dst.read_bytes()).hexdigest(); "
        "print(json.dumps({'restored_path': str(dst), 'sha256': digest, 'size_bytes': dst.stat().st_size}))"
    )
    result = run(
        compose + ["run", "--rm", "--no-deps", "api", "python", "-c", code],
        env=env,
        capture=True,
        timeout=timeout,
        secret_values=secret_values,
    )
    return parse_last_json_line(result.stdout)


def write_report(
    diagnostics_dir: Path,
    report: dict[str, Any],
    *,
    secret_values: Sequence[str] = (),
) -> dict[str, Any]:
    redacted_report, _ = redact_secrets(report, secret_values=secret_values)
    redacted_report["secret_values_redacted"] = True
    redacted_report["report_sha256"] = sha256_json(redacted_report)
    (diagnostics_dir / REPORT_NAME).write_text(
        json.dumps(redacted_report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return redacted_report


def sha256_json(payload: dict[str, Any]) -> str:
    clean = {key: value for key, value in payload.items() if key != "report_sha256"}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def parse_last_json_line(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return json.loads(stripped)
    raise RuntimeError(f"no JSON object found in command output: {output}")


if __name__ == "__main__":
    raise SystemExit(main())
