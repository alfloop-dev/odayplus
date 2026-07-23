#!/usr/bin/env python3
"""Run the Assisted Listing Intake closure E2E against fresh durable services."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def wait_for(url: str, process: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = "not started"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{process.args!r} exited with {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except urllib.error.HTTPError as exc:
            if exc.code < 500:
                return
            last_error = str(exc)
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=8)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)


def tail(path: Path, lines: int = 80) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def main() -> int:
    api_port = int(os.environ.get("ODP_INTAKE_E2E_API_PORT", "18199"))
    web_port = int(os.environ.get("ODP_INTAKE_E2E_WEB_PORT", "13199"))
    with tempfile.TemporaryDirectory(prefix="oday-intake-functional-") as temp_dir:
        temp = Path(temp_dir)
        db_path = temp / "intake-functional.sqlite3"
        env = os.environ.copy()
        env.update(
            {
                "ODP_PERSISTENCE": "durable",
                "ODP_DB_PATH": str(db_path),
                "ODP_API_BASE_URL": f"http://127.0.0.1:{api_port}",
                "ODP_WEB_BASE_URL": f"http://127.0.0.1:{web_port}",
                "ODP_API_PORT": str(api_port),
                "OPSBOARD_PORT": str(web_port),
                "NEXT_TELEMETRY_DISABLED": "1",
            }
        )
        commands = {
            "api": [
                sys.executable,
                "-m",
                "uvicorn",
                "apps.api.oday_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            "worker": [
                sys.executable,
                "-c",
                "from apps.worker.oday_worker.main import ODayWorker; ODayWorker().loop()",
            ],
            "web": [
                "npm",
                "run",
                "dev",
                "--workspace=@oday-plus/web",
                "--",
                "-p",
                str(web_port),
            ],
        }
        processes: dict[str, subprocess.Popen[str]] = {}
        logs: dict[str, tuple[Path, object]] = {}
        try:
            for name in ("api",):
                log_path = temp / f"{name}.log"
                log_handle = log_path.open("w")
                logs[name] = (log_path, log_handle)
                processes[name] = subprocess.Popen(
                    commands[name],
                    cwd=ROOT,
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            wait_for(
                f"http://127.0.0.1:{api_port}/platform/health",
                processes["api"],
                120,
            )

            for name in ("worker", "web"):
                log_path = temp / f"{name}.log"
                log_handle = log_path.open("w")
                logs[name] = (log_path, log_handle)
                processes[name] = subprocess.Popen(
                    commands[name],
                    cwd=ROOT,
                    env=env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            wait_for(f"http://127.0.0.1:{web_port}", processes["web"], 120)
            wait_for(
                (
                    f"http://127.0.0.1:{web_port}/w/expansion/listings/intake/"
                    "00000000-0000-4000-8000-000000000000?section=timeline"
                ),
                processes["web"],
                120,
            )

            result = subprocess.run(
                [
                    "npx",
                    "playwright",
                    "test",
                    "--config=playwright.intake-functional.config.ts",
                    *sys.argv[1:],
                ],
                cwd=ROOT,
                env=env,
                check=False,
            )
            if result.returncode:
                for name, (log_path, _) in logs.items():
                    print(f"\n--- {name} log tail ---\n{tail(log_path)}", file=sys.stderr)
            return result.returncode
        except Exception:
            for name, (log_path, _) in logs.items():
                print(f"\n--- {name} log tail ---\n{tail(log_path)}", file=sys.stderr)
            raise
        finally:
            for process in reversed(list(processes.values())):
                stop(process)
            for _, log_handle in logs.values():
                log_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
