#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Three missed 15s writes fail readiness before the DR consumer's 60s wait.
# Docker probes every 5s; Linux task identity also rejects dead/reused writers
# and either dead background thread without waiting for freshness expiry.
HEARTBEAT_MAX_AGE = 45.0
HEARTBEAT_TAIL_BYTES = 8192


def validate_heartbeat(event: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Shared wire contract; host DR rechecks shape/freshness, not remote /proc."""
    if not isinstance(event, dict):
        raise ValueError("heartbeat must be an object")
    if event.get("worker") != "product-e2e-scheduler" or event.get("status") != "alive":
        raise ValueError("heartbeat is not alive")
    if not isinstance(event.get("persistence"), str) or not isinstance(event.get("db_path"), str):
        raise ValueError("invalid heartbeat persistence metadata")
    for key in ("pid", "worker_tid", "scheduler_tid"):
        if type(event.get(key)) is not int or event[key] <= 0:
            raise ValueError("invalid heartbeat task identity")
    if not isinstance(event.get("process_start"), str) or not event["process_start"].isdigit():
        raise ValueError("invalid heartbeat process identity")
    try:
        timestamp = datetime.fromisoformat(event["time"])
        if timestamp.tzinfo is None:
            raise ValueError("heartbeat time must have a timezone")
        age = ((now or datetime.now(UTC)) - timestamp).total_seconds()
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("invalid heartbeat time") from exc
    if not 0 <= age <= HEARTBEAT_MAX_AGE:
        raise ValueError("heartbeat is stale or in the future")
    return event


def process_start(pid: int) -> str:
    # comm may contain spaces/parentheses; fields after its final ')' start at
    # field 3 (state). Field 22 binds a PID to this process incarnation.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if fields[0] in {"Z", "X"}:
        raise ValueError("heartbeat process has exited")
    return fields[19]


def check_heartbeat(path: Path) -> dict[str, Any]:
    """Read only a bounded final record; never import or initialize the runtime."""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - HEARTBEAT_TAIL_BYTES))
        tail = handle.read(HEARTBEAT_TAIL_BYTES)
    if not tail.endswith(b"\n"):
        raise ValueError("heartbeat final record is incomplete")
    event = validate_heartbeat(json.loads(tail.split(b"\n")[-2]))
    pid = event["pid"]
    if process_start(pid) != event["process_start"]:
        raise ValueError("heartbeat process identity changed")
    for key in ("worker_tid", "scheduler_tid"):
        if not Path(f"/proc/{pid}/task/{event[key]}").is_dir():
            raise ValueError("heartbeat background thread has exited")
    return event


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Minimal E2E worker/scheduler heartbeat.")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--check", action="store_true", help="Read-only readiness check; no runtime startup.")
    args = parser.parse_args(argv)
    path = Path(os.environ.get("ODP_E2E_WORKER_HEARTBEAT", "/storage/worker-heartbeat.jsonl"))
    if args.check:
        try:
            event = check_heartbeat(path)
        except (OSError, ValueError, IndexError) as exc:
            print(f"worker heartbeat not ready: {type(exc).__name__}", file=sys.stderr)
            return 1
        print(json.dumps(event, sort_keys=True))
        return 0
    if not math.isfinite(args.interval) or not 0 < args.interval <= 15:
        parser.error("interval must be positive, finite, and at most 15 seconds")

    # Runtime imports must stay behind --check: health probes cannot construct
    # persistence, import model integrations, start jobs, or contact services.
    from apps.scheduler.oday_scheduler.main import ODayScheduler
    from apps.worker.oday_worker.main import ODayWorker
    from shared.infrastructure.persistence.factory import build_persistence

    path.parent.mkdir(parents=True, exist_ok=True)
    persistence = build_persistence()
    stop_event = threading.Event()
    worker = ODayWorker(persistence)
    scheduler = ODayScheduler(persistence)
    worker_thread = threading.Thread(target=worker.loop, args=(stop_event,), daemon=True)
    scheduler_thread = threading.Thread(target=scheduler.loop, args=(stop_event, 30.0), daemon=True)
    worker_thread.start()
    scheduler_thread.start()
    identity = {
        "pid": os.getpid(),
        "process_start": process_start(os.getpid()),
        "worker_tid": worker_thread.native_id,
        "scheduler_tid": scheduler_thread.native_id,
    }
    next_write = 0.0
    try:
        while True:
            alive = worker_thread.is_alive() and scheduler_thread.is_alive()
            if not alive or time.monotonic() >= next_write:
                event = {
                    "time": datetime.now(UTC).isoformat(),
                    "worker": "product-e2e-scheduler",
                    "status": "alive" if alive else "failed",
                    "persistence": os.environ.get("ODP_PERSISTENCE", "memory"),
                    "db_path": os.environ.get("ODP_DB_PATH", ""),
                    **identity,
                }
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, sort_keys=True) + "\n")
                if not alive:
                    return 1
                next_write = time.monotonic() + args.interval
            stop_event.wait(min(1.0, args.interval))
    except KeyboardInterrupt:
        return 0
    finally:
        stop_event.set()


if __name__ == "__main__":
    raise SystemExit(main())
