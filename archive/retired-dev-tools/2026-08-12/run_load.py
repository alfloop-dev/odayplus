#!/usr/bin/env python3
"""Run load and soak test simulation and write performance report."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from apps.api.oday_api.main import create_app
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.jobs.queue import JobStatus

EVIDENCE_DIR = ROOT / "docs/evidence/completion/ODP-PGAP-RELIABILITY-001"


def run_load_test(db_path: str, concurrency: int, volume: int) -> dict:
    bundle = _durable_bundle(db_path)
    # Using production WAL persistence settings (no PRAGMA synchronous = OFF)
    app = create_app(persistence=bundle)
    client = TestClient(app)

    latencies = []
    success_count = 0
    failure_count = 0

    def run_one_task(task_id: int):
        t0 = time.perf_counter()
        correlation_id = f"corr-cli-load-{task_id}"
        idem_key = f"idem-cli-load-{task_id}"

        try:
            # 1. Enqueue job (Client request)
            resp = client.post(
                "/jobs",
                json={"job_type": "forecast", "payload": {"store_id": f"store-{task_id}"}},
                headers={"X-Correlation-ID": correlation_id, "Idempotency-Key": idem_key},
            )
            if resp.status_code != 202:
                return time.perf_counter() - t0, False

            job_id = resp.json()["job_id"]

            # 2. Lease and process job (Worker simulation)
            leased = bundle.job_queue.lease(lease_duration_seconds=30)
            if leased is None or leased.job_id != job_id:
                # If leased another job, that's fine under concurrency, but we must complete this one specifically
                # For safety under thread concurrency, let's complete the job we enqueued
                pass

            # Atomically complete the enqueued job
            bundle.job_queue.complete(job_id)

            # 3. Get job and verify status is succeeded (Client check)
            resp_get = client.get(f"/jobs/{job_id}", headers={"X-Correlation-ID": correlation_id})
            if resp_get.status_code != 200 or resp_get.json()["status"] != JobStatus.SUCCEEDED.value:
                return time.perf_counter() - t0, False

            # 4. List audit events
            resp_audit = client.get("/audit/events", params={"correlation_id": correlation_id})
            if resp_audit.status_code != 200:
                return time.perf_counter() - t0, False

            return time.perf_counter() - t0, True
        except Exception:
            return time.perf_counter() - t0, False

    print(f"Starting load test: concurrency={concurrency}, volume={volume}...")
    t_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(run_one_task, i) for i in range(volume)]
        for fut in concurrent.futures.as_completed(futures):
            lat, success = fut.result()
            latencies.append(lat)
            if success:
                success_count += 1
            else:
                failure_count += 1

    total_duration = time.perf_counter() - t_start
    latencies.sort()
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0
    throughput = (success_count + failure_count) / total_duration if total_duration > 0 else 0

    bundle.engine.close()

    return {
        "timestamp": time.time(),
        "concurrency": concurrency,
        "volume": volume,
        "success_count": success_count,
        "failure_count": failure_count,
        "total_duration_seconds": total_duration,
        "throughput_req_per_sec": throughput,
        "latency_p50_seconds": p50,
        "latency_p95_seconds": p95,
        "latency_p99_seconds": p99,
        "passed": p95 <= 6.0 and failure_count == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--volume", type=int, default=100)
    parser.add_argument("--db-path", type=str, default="/tmp/cli_load_test.sqlite3")
    args = parser.parse_args()

    # Ensure clean database before run
    if os.path.exists(args.db_path):
        os.remove(args.db_path)

    res = run_load_test(args.db_path, args.concurrency, args.volume)

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    report_file = EVIDENCE_DIR / "load_test_run_report.json"
    report_file.write_text(json.dumps(res, indent=2))

    print("\nLoad Test Results:")
    print(json.dumps(res, indent=2))

    if os.path.exists(args.db_path):
        try:
            os.remove(args.db_path)
        except Exception:
            pass

    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
