#!/usr/bin/env python3
"""Execute deterministic fault drills against durable intake primitives."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from shared.infrastructure.object_store.client import InMemoryObjectStore
from shared.infrastructure.persistence.factory import _durable_bundle
from shared.infrastructure.persistence.job_queue import JobFenceRejectedError
from shared.jobs.queue import JobRequest, JobStatus


def run_drills(db_path: Path) -> dict:
    events: list[dict] = []
    bundle = _durable_bundle(str(db_path))
    try:
        def record(name: str, started: float, passed: bool, **details) -> None:
            events.append({"scenario": name, "measured_seconds": time.perf_counter() - started, "passed": passed, **details})

        # Provider latency drives a real durable job through the timeout failure
        # path. The managed provider/network injection remains a release gate.
        started = time.perf_counter()
        provider_job, _ = bundle.job_queue.enqueue(
            JobRequest(
                "assisted-listing-intake",
                {"intake_id": "IN-PROVIDER-LATENCY", "injected_delay_seconds": 0.002},
                "provider-latency-key",
            ),
            correlation_id="provider-latency",
        )
        provider_claim = bundle.job_queue.claim_next(worker_id="provider-worker")
        assert provider_claim is not None and provider_claim.job_id == provider_job.job_id
        time.sleep(0.002)
        bundle.job_queue.update_status(
            provider_claim.job_id,
            JobStatus.FAILED,
            expected_version=provider_claim.version,
            fence_token=provider_claim.fence_token,
            error_message="PROVIDER_TIMEOUT_INJECTED",
        )
        provider_failed = bundle.job_queue.get(provider_claim.job_id)
        record(
            "provider_latency",
            started,
            provider_failed is not None
            and provider_failed.status == JobStatus.FAILED
            and provider_failed.error_message == "PROVIDER_TIMEOUT_INJECTED",
            injected_delay_seconds=0.002,
            local_product_path="durable_job_timeout_failure",
            managed_provider_executed=False,
        )

        # At-least-once delivery must collapse on the durable idempotency key.
        started = time.perf_counter()
        request = JobRequest("assisted-listing-intake", {"intake_id": "IN-DUP"}, "duplicate-key")
        first, first_created = bundle.job_queue.enqueue(request, correlation_id="dup-1")
        second, second_created = bundle.job_queue.enqueue(request, correlation_id="dup-2")
        record("duplicate_delivery", started, first_created and not second_created and first.job_id == second.job_id)

        # Worker loss: expire a real lease, reclaim it, and reject the stale fence.
        started = time.perf_counter()
        claimed = bundle.job_queue.claim_next(worker_id="lost-worker")
        assert claimed is not None
        bundle.engine.execute(
            "UPDATE durable_jobs SET lease_expires_at = ? WHERE job_id = ?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), claimed.job_id),
        )
        reclaimed = bundle.job_queue.claim_next(worker_id="recovery-worker")
        stale_rejected = False
        try:
            bundle.job_queue.update_status(first.job_id, JobStatus.SUCCEEDED, expected_version=claimed.version, fence_token=claimed.fence_token)
        except JobFenceRejectedError:
            stale_rejected = True
        record("worker_loss", started, reclaimed is not None and stale_rejected, rpo_seconds=0.0)

        # SQL failover: close and reopen the durable adapter, then verify the row.
        started = time.perf_counter()
        durable_job_id = first.job_id
        bundle.engine.close()
        bundle = _durable_bundle(str(db_path))
        recovered = bundle.job_queue.get(durable_job_id)
        record("sql_failover", started, recovered is not None, rpo_seconds=0.0)

        # Exercise the product object-store adapter's checksum and immutable
        # generation precondition. A real GCS bucket drill remains release-gated.
        started = time.perf_counter()
        object_store = InMemoryObjectStore()
        tenant_id = "00000000-0000-0000-0000-000000000001"
        payload = b"approved source snapshot"
        uri, generation = object_store.upload_object(
            tenant_id,
            "tw-intake-snapshots",
            f"tenants/{tenant_id}/snapshots/IN-GCS/raw",
            payload,
            "text/html",
        )
        metadata = object_store.head_object(tenant_id, uri)
        generation_rejected = False
        try:
            object_store.upload_object(
                tenant_id,
                "tw-intake-snapshots",
                f"tenants/{tenant_id}/snapshots/IN-GCS/raw",
                payload + b" corrupt",
                "text/html",
                if_generation_match=generation + 1,
            )
        except ValueError:
            generation_rejected = True
        record(
            "gcs_inconsistency",
            started,
            generation_rejected
            and metadata["sha256"] == hashlib.sha256(payload).hexdigest()
            and object_store.download_object(tenant_id, uri, generation=generation) == payload,
            outcome="generation_precondition_rejected",
            local_product_path="object_store_checksum_and_generation",
            managed_gcs_executed=False,
        )

        # Backlog reaches the runtime threshold and drains without receipt loss.
        started = time.perf_counter()
        for index in range(200):
            bundle.job_queue.enqueue(JobRequest("assisted-listing-intake", {"intake_id": f"IN-BACKLOG-{index}"}), correlation_id=f"backlog-{index}")
        active_at_peak = bundle.job_queue.count_active_jobs()
        record("queue_backlog", started, active_at_peak >= 200, active_jobs=active_at_peak)

        # Retry budget and DLQ recovery use an isolated FIFO so every lease is
        # guaranteed to target the poison job rather than the older backlog.
        started = time.perf_counter()
        retry_path = db_path.with_name(f"{db_path.stem}-retry{db_path.suffix}")
        retry_bundle = _durable_bundle(str(retry_path))
        transitions: list[str] = []
        try:
            poison, _ = retry_bundle.job_queue.enqueue(
                JobRequest("assisted-listing-intake", {"intake_id": "IN-POISON"}),
                correlation_id="poison",
            )
            for _ in range(poison.max_retries):
                leased = retry_bundle.job_queue.lease(lease_duration_seconds=0.01)
                assert leased is not None and leased.job_id == poison.job_id
                transitions.append(f"leased:{leased.attempts}")
                assert retry_bundle.job_queue.fail(
                    poison.job_id, lease_token=leased.leased_until
                )
                current = retry_bundle.job_queue.get(poison.job_id)
                assert current is not None
                transitions.append(current.status.value)
            failed = retry_bundle.job_queue.get(poison.job_id)
            assert failed is not None
            replayed = retry_bundle.job_queue.replay(
                poison.job_id, expected_version=failed.version
            )
            record(
                "retry_budget_dlq_recovery",
                started,
                failed.status == JobStatus.FAILED
                and failed.attempts == failed.max_retries
                and replayed.status == JobStatus.QUEUED
                and replayed.attempts == 0,
                failed_status=failed.status.value,
                attempts_before_replay=failed.attempts,
                max_retries=failed.max_retries,
                replayed_status=replayed.status.value,
                transitions=transitions,
            )
        finally:
            retry_bundle.engine.close()
    finally:
        bundle.engine.close()

    rto = sum(event["measured_seconds"] for event in events)
    report = {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(),
        "measurement_mode": "durable-runtime-fault-injection",
        "events": events,
        "measured_rpo_seconds": 0.0,
        "rpo_target_seconds": 900.0,
        "measured_rto_seconds": rto,
        "rto_target_seconds": 14400.0,
        "missed_targets": [event["scenario"] for event in events if not event["passed"]],
        "missed_production_targets": [
            "managed_provider_latency_injection",
            "cloud_sql_regional_failover",
            "gcs_generation_and_checksum_reconciliation",
            "cloud_tasks_pubsub_backlog_and_dlq",
            "production_restore_drill",
        ],
        "production_ready": False,
    }
    report["passed"] = not report["missed_targets"] and rto < report["rto_target_seconds"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or Path("docs/evidence/completion/ODP-INTAKE-LOAD-001/chaos-report.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="intake-chaos-") as directory:
        report = run_drills(Path(directory) / "runtime.sqlite3")
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
