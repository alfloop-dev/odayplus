#!/usr/bin/env python3
"""Run disjoint Assisted Listing Intake coverage against durable real services."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = (
    ROOT
    / "docs/evidence/completion/ODP-INTAKE-FCL-INTEGRATION-001/coverage"
)


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


def tail(path: Path, lines: int = 120) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def main() -> int:
    api_port = int(os.environ.get("ODP_INTAKE_COVERAGE_API_PORT", "18209"))
    web_port = int(os.environ.get("ODP_INTAKE_COVERAGE_WEB_PORT", "13209"))
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    logs_dir = EVIDENCE / "runtime-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    started_at = utc_now()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (EVIDENCE / "run-metadata.json").write_text(
        json.dumps(
            {
                "api_port": api_port,
                "git_head": head,
                "started_at": started_at,
                "web_port": web_port,
            },
            indent=2,
        )
        + "\n"
    )

    with tempfile.TemporaryDirectory(
        prefix="oday-intake-functional-coverage-"
    ) as temp_dir:
        temp = Path(temp_dir)
        db_path = temp / "intake-functional-coverage.sqlite3"
        web_copy = temp / "web"
        worker_bootstrap = temp / "coverage_worker.py"
        shutil.copytree(
            ROOT / "apps/web",
            web_copy,
            ignore=shutil.ignore_patterns(".next", "node_modules", "tsconfig.tsbuildinfo"),
        )
        (web_copy / "node_modules").symlink_to(ROOT / "node_modules", target_is_directory=True)
        worker_bootstrap.write_text(
            """
import json

from apps.worker.oday_worker.main import ODayWorker
from modules.external_data.application.assisted_intake import RETRIEVAL_CORPUS
from modules.external_data.security import assisted_listing_retrieval
from modules.external_data.security.assisted_listing_retrieval import FetchResponse


_resolve_host = assisted_listing_retrieval._resolve_host


def coverage_resolver(host):
    if host == "synthetic.example" or host.endswith(".synthetic.example"):
        return ("93.184.216.34",)
    return _resolve_host(host)


def coverage_fetcher(_self, url, *, timeout_seconds, max_response_bytes):
    result = RETRIEVAL_CORPUS.get(url)
    if result is None:
        return FetchResponse(
            status_code=404,
            headers={
                "Content-Type": "application/json",
                "X-Failure-Code": "ODP-INTAKE-RETRIEVAL-404",
                "X-Failure-Summary": "Source page was removed or is unavailable.",
                "X-Failure-Next-Action": "Use assisted entry or review the source.",
                "X-Failure-Retryable": "false",
            },
            body=b"{}",
        )
    if result.failure is not None:
        if result.failure.code == "ODP-INTAKE-RETRIEVAL-TIMEOUT":
            raise TimeoutError(result.failure.summary)
        return FetchResponse(
            status_code=500,
            headers={
                "Content-Type": "application/json",
                "X-Failure-Code": result.failure.code,
                "X-Failure-Summary": result.failure.summary,
                "X-Failure-Next-Action": result.failure.next_action,
                "X-Failure-Retryable": str(result.failure.retryable).lower(),
            },
            body=b"{}",
        )
    return FetchResponse(
        status_code=200,
        headers={
            "Content-Type": "text/html",
            "X-Source-Observed-At": result.captured_at,
        },
        body=json.dumps(result.raw).encode(),
    )


assisted_listing_retrieval._resolve_host = coverage_resolver
assisted_listing_retrieval.DefaultRetrievalFetcher.__call__ = coverage_fetcher
ODayWorker().loop()
""".lstrip()
        )
        env = os.environ.copy()
        env.update(
            {
                "CI": "1",
                "NEXT_TELEMETRY_DISABLED": "1",
                "ODP_API_BASE_URL": f"http://127.0.0.1:{api_port}",
                "ODP_API_PORT": str(api_port),
                "ODP_DB_PATH": str(db_path),
                "ODP_PERSISTENCE": "durable",
                "ODP_WEB_BASE_URL": f"http://127.0.0.1:{web_port}",
                "OPSBOARD_PORT": str(web_port),
                "PYTHONPATH": os.pathsep.join(
                    value
                    for value in (str(ROOT), env.get("PYTHONPATH", ""))
                    if value
                ),
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
                str(worker_bootstrap),
            ],
            "web": [
                str(ROOT / "node_modules/.bin/next"),
                "dev",
                str(web_copy),
                "-p",
                str(web_port),
            ],
        }
        processes: dict[str, subprocess.Popen[str]] = {}
        log_handles: dict[str, object] = {}
        result_code = 1
        try:
            for name in ("api",):
                handle = (logs_dir / f"{name}.log").open("w")
                log_handles[name] = handle
                processes[name] = subprocess.Popen(
                    commands[name],
                    cwd=ROOT,
                    env=env,
                    stdout=handle,
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
                handle = (logs_dir / f"{name}.log").open("w")
                log_handles[name] = handle
                processes[name] = subprocess.Popen(
                    commands[name],
                    cwd=ROOT,
                    env=env,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            wait_for(f"http://127.0.0.1:{web_port}", processes["web"], 180)
            wait_for(
                f"http://127.0.0.1:{web_port}/w/expansion/listings",
                processes["web"],
                180,
            )

            result = subprocess.run(
                [
                    "npx",
                    "playwright",
                    "test",
                    "--config=playwright.intake-functional-coverage.config.ts",
                    *sys.argv[1:],
                ],
                cwd=ROOT,
                env=env,
                check=False,
            )
            result_code = result.returncode
            return result_code
        except Exception:
            for name in processes:
                print(
                    f"\n--- {name} log tail ---\n"
                    f"{tail(logs_dir / f'{name}.log')}",
                    file=sys.stderr,
                )
            raise
        finally:
            for process in reversed(list(processes.values())):
                stop(process)
            for handle in log_handles.values():
                handle.close()
            metadata_path = EVIDENCE / "run-metadata.json"
            metadata = json.loads(metadata_path.read_text())
            metadata.update(
                {
                    "exit_code": result_code,
                    "finished_at": utc_now(),
                    "real_api": True,
                    "real_next": True,
                    "route_interception_policy": (
                        "No command interception. GET abort/delay is used only "
                        "to exercise loading/error presentation."
                    ),
                    "synthetic_retrieval_adapter": (
                        "Deterministic corpus injected at the approved worker "
                        "retrieval fetcher boundary."
                    ),
                }
            )
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
            for name in ("api", "worker", "web"):
                source = logs_dir / f"{name}.log"
                (logs_dir / f"{name}-tail.txt").write_text(tail(source) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
