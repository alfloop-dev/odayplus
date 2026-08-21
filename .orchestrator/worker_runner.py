#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def normalize_command(raw: list[str]) -> list[str]:
    if raw and raw[0] == "--":
        raw = raw[1:]
    return raw


def process_tree_activity(root_pid: int | None) -> dict[str, int]:
    """Return monotonic CPU/I/O counters for a Linux process tree.

    The runner heartbeat proves only that the wrapper is alive.  Sampling the
    child tree lets the supervisor distinguish a quiet-but-working CLI from a
    process that is genuinely making no progress.  Every read is best-effort:
    processes may exit between /proc reads and non-Linux hosts simply report
    no counters.
    """

    if not root_pid:
        return {}
    pending = [int(root_pid)]
    seen: set[int] = set()
    totals = {
        "processes": 0,
        "cpu_ticks": 0,
        "rchar": 0,
        "wchar": 0,
        "read_bytes": 0,
        "write_bytes": 0,
    }
    while pending:
        pid = pending.pop()
        if pid in seen:
            continue
        seen.add(pid)
        proc_root = Path(f"/proc/{pid}")
        try:
            stat_text = (proc_root / "stat").read_text(encoding="utf-8")
            # comm is parenthesized and may contain spaces, so fields after the
            # final ')' are safer than a plain split of the complete record.
            stat_fields = stat_text.rsplit(")", 1)[1].strip().split()
            totals["cpu_ticks"] += int(stat_fields[11]) + int(stat_fields[12])
            totals["processes"] += 1
        except (OSError, IndexError, ValueError):
            continue
        try:
            children = (proc_root / "task" / str(pid) / "children").read_text(encoding="utf-8")
            pending.extend(int(value) for value in children.split())
        except (OSError, ValueError):
            pass
        try:
            for line in (proc_root / "io").read_text(encoding="utf-8").splitlines():
                key, separator, raw_value = line.partition(":")
                if separator and key in totals:
                    totals[key] += int(raw_value.strip())
        except (OSError, ValueError):
            pass
    return totals if totals["processes"] else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an auto-worker command with heartbeat and terminal markers.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--heartbeat-path", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=15.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = normalize_command(list(args.command))
    if not command:
        print("worker_runner: missing command after --", file=sys.stderr)
        return 2

    heartbeat_path = Path(args.heartbeat_path)
    status_path = Path(args.status_path)
    interval = max(1.0, float(args.heartbeat_interval_seconds or 15.0))
    started_at = utc_now()
    child: subprocess.Popen[str] | None = None
    terminating_signal: int | None = None
    last_activity_sample: dict[str, int] = {}

    status: dict[str, Any] = {
        "run_id": args.run_id,
        "status": "starting",
        "pid": os.getpid(),
        "child_pid": None,
        "command": command,
        "started_at": started_at,
        "last_heartbeat_at": started_at,
        "last_process_activity_at": started_at,
        "process_activity": {},
        "finished_at": None,
        "exit_code": None,
        "signal": None,
    }

    def publish(next_status: str) -> None:
        nonlocal last_activity_sample
        now = utc_now()
        activity_sample = process_tree_activity(child.pid if child is not None else None)
        if activity_sample and activity_sample != last_activity_sample:
            status["last_process_activity_at"] = now
            last_activity_sample = activity_sample
        status["process_activity"] = activity_sample
        status["status"] = next_status
        status["last_heartbeat_at"] = now
        write_json(heartbeat_path, {
            "run_id": args.run_id,
            "status": next_status,
            "pid": os.getpid(),
            "child_pid": status.get("child_pid"),
            "updated_at": now,
            "last_process_activity_at": status.get("last_process_activity_at"),
            "process_activity": activity_sample,
        })
        write_json(status_path, status)

    def forward_signal(signum: int, _frame: Any) -> None:
        nonlocal terminating_signal
        terminating_signal = signum
        status["signal"] = signum
        if child is not None and child.poll() is None:
            try:
                child.send_signal(signum)
            except OSError:
                pass

    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    try:
        publish("starting")
        child = subprocess.Popen(command, text=True)
        status["child_pid"] = child.pid
        publish("running")
        next_heartbeat = time.monotonic() + interval
        while True:
            exit_code = child.poll()
            if exit_code is not None:
                status["exit_code"] = exit_code
                status["finished_at"] = utc_now()
                publish("completed" if exit_code == 0 else "failed")
                if exit_code < 0:
                    return 128 + abs(exit_code)
                return exit_code
            if time.monotonic() >= next_heartbeat:
                publish("running")
                next_heartbeat = time.monotonic() + interval
            time.sleep(min(1.0, interval))
    except BaseException as exc:
        status["status"] = "failed"
        status["finished_at"] = utc_now()
        status["error"] = f"{type(exc).__name__}: {exc}"
        if terminating_signal is not None:
            status["signal"] = terminating_signal
        try:
            write_json(status_path, status)
        except OSError:
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
