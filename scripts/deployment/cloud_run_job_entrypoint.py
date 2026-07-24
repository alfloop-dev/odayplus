#!/usr/bin/env python3
"""Run one bounded Cloud Run Job task with a verifiable runtime receipt."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from apps.api.server import bootstrap_runtime, build_scheduler, build_worker
from apps.cli.oday_cli.ops import OpsPlanError, build_migration_run
from shared.jobs.queue import JobRecord, JobStatus

EXIT_FAILED = 1
EXIT_CONTRACT_INVALID = 2
EXIT_RETRY_QUEUED = 75


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _emit_receipt(kind: str, status: str, **details: Any) -> None:
    payload = {
        "schema_version": 1,
        "receipt_kind": kind,
        "status": status,
        "release_sha": os.environ.get("ODAY_RELEASE_SHA", ""),
        "environment": os.environ.get("ODP_DEPLOY_ENV") or os.environ.get("ODAY_ENV", ""),
        "cloud_run_execution": os.environ.get("CLOUD_RUN_EXECUTION", ""),
        "cloud_run_task_index": os.environ.get("CLOUD_RUN_TASK_INDEX", ""),
        "recorded_at": _now(),
        **details,
    }
    print(json.dumps(payload, sort_keys=True), flush=True)


def _database_urls(value: str) -> tuple[str, str]:
    """Return SQLAlchemy/psycopg forms without exposing credentials."""

    if value.startswith("postgresql+psycopg://"):
        return value, "postgresql://" + value.removeprefix("postgresql+psycopg://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://"), value
    if value.startswith("postgres://"):
        suffix = value.removeprefix("postgres://")
        return "postgresql+psycopg://" + suffix, "postgresql://" + suffix
    raise ValueError("ODAY_DATABASE_URL must use postgres:// or postgresql://")


def _verify_runtime_schema(database_url: str) -> None:
    from shared.infrastructure.persistence.postgresql import PostgresEngine

    engine = PostgresEngine(database_url, bootstrap=True, validate_schema=True)
    engine.close()


class TrackingJobQueue:
    """Delegate queue operations while retaining worker/scheduler receipts."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.claimed: list[JobRecord] = []
        self.enqueued: list[tuple[JobRecord, bool]] = []
        self.claim_errors: list[Exception] = []
        self.enqueue_errors: list[Exception] = []

    def claim_next(self, worker_id: str = "cloud-run-worker") -> JobRecord | None:
        try:
            record = self._delegate.claim_next(worker_id=worker_id)
        except Exception as exc:
            self.claim_errors.append(exc)
            raise
        if record is not None:
            self.claimed.append(record)
        return record

    def enqueue(self, request: Any, *, correlation_id: str) -> tuple[JobRecord, bool]:
        try:
            receipt = self._delegate.enqueue(request, correlation_id=correlation_id)
        except Exception as exc:
            self.enqueue_errors.append(exc)
            raise
        self.enqueued.append(receipt)
        return receipt

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


def run_migration() -> int:
    original_database_url = os.environ.get("ODAY_DATABASE_URL", "")
    try:
        sqlalchemy_url, psycopg_url = _database_urls(original_database_url)
        os.environ["ODAY_DATABASE_URL"] = sqlalchemy_url
        receipt = build_migration_run(
            environment=os.environ.get("ODP_DEPLOY_ENV", ""),
            target_revision="head",
            dry_run=False,
        )
        os.environ["ODAY_DATABASE_URL"] = psycopg_url
        _verify_runtime_schema(psycopg_url)
    except (OpsPlanError, OSError, RuntimeError, ValueError) as exc:
        _emit_receipt(
            "migration",
            "failed",
            error_class=type(exc).__name__,
            error="migration or runtime schema verification failed",
        )
        return EXIT_FAILED
    finally:
        if original_database_url:
            os.environ["ODAY_DATABASE_URL"] = original_database_url
        else:
            os.environ.pop("ODAY_DATABASE_URL", None)
    _emit_receipt(
        "migration",
        "succeeded",
        target_revision=receipt.target_revision,
        manifest_sha256=receipt.manifest_sha256,
        checksum_status=receipt.checksum_status,
        returncode=receipt.returncode,
        runtime_schema_verified=True,
    )
    return 0


def run_scheduler() -> int:
    bundle = bootstrap_runtime()
    tracking_queue = TrackingJobQueue(bundle.job_queue)
    scheduler = build_scheduler(replace(bundle, job_queue=tracking_queue))
    before = tracking_queue.count_active_jobs()
    scheduler.run_once()
    after = tracking_queue.count_active_jobs()

    if tracking_queue.enqueue_errors:
        exc = tracking_queue.enqueue_errors[-1]
        _emit_receipt(
            "scheduler",
            "failed",
            reason="enqueue_exception",
            error_class=type(exc).__name__,
            active_jobs_before=before,
            active_jobs_after=after,
        )
        return EXIT_FAILED

    if not tracking_queue.enqueued:
        _emit_receipt(
            "scheduler",
            "failed",
            reason="no_enqueue_receipt",
            active_jobs_before=before,
            active_jobs_after=after,
        )
        return EXIT_FAILED

    record, created = tracking_queue.enqueued[-1]
    persisted = tracking_queue.get(record.job_id)
    if persisted is None or persisted.status not in {
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.SUCCEEDED,
    }:
        _emit_receipt(
            "scheduler",
            "failed",
            reason="enqueue_receipt_not_persisted",
            job_id=record.job_id,
            persisted_status=persisted.status.value if persisted else None,
            active_jobs_before=before,
            active_jobs_after=after,
        )
        return EXIT_FAILED

    _emit_receipt(
        "scheduler",
        "succeeded",
        job_id=persisted.job_id,
        job_type=persisted.job_type,
        job_status=persisted.status.value,
        idempotency_key=persisted.idempotency_key,
        created=created,
        active_jobs_before=before,
        active_jobs_after=after,
    )
    return 0


def _worker_result(record: JobRecord) -> tuple[int, str]:
    if record.status == JobStatus.SUCCEEDED:
        return 0, "succeeded"
    if record.status == JobStatus.QUEUED:
        return EXIT_RETRY_QUEUED, "retry_queued"
    if record.status == JobStatus.FAILED:
        return EXIT_FAILED, "failed"
    if record.status == JobStatus.CANCELLED:
        return EXIT_FAILED, "cancelled"
    return EXIT_CONTRACT_INVALID, "invalid_terminal_state"


def run_worker(*, max_jobs: int, require_job: bool) -> int:
    bundle = bootstrap_runtime()
    tracking_queue = TrackingJobQueue(bundle.job_queue)
    worker = build_worker(replace(bundle, job_queue=tracking_queue))
    processed: list[dict[str, Any]] = []

    for _ in range(max_jobs):
        claimed_before = len(tracking_queue.claimed)
        worker.run_once()
        if tracking_queue.claim_errors:
            exc = tracking_queue.claim_errors[-1]
            _emit_receipt(
                "worker",
                "failed",
                reason="claim_exception",
                error_class=type(exc).__name__,
                processed=processed,
            )
            return EXIT_FAILED
        if len(tracking_queue.claimed) == claimed_before:
            if not processed and require_job:
                _emit_receipt(
                    "worker",
                    "failed",
                    reason="required_job_not_claimed",
                    queue_active=tracking_queue.count_active_jobs(),
                )
                return EXIT_FAILED
            _emit_receipt(
                "worker",
                "drained",
                processed=processed,
                processed_count=len(processed),
                queue_active=tracking_queue.count_active_jobs(),
            )
            return 0

        claimed = tracking_queue.claimed[-1]
        persisted = tracking_queue.get(claimed.job_id)
        if persisted is None:
            _emit_receipt(
                "worker",
                "failed",
                reason="claimed_job_receipt_missing",
                job_id=claimed.job_id,
            )
            return EXIT_CONTRACT_INVALID

        exit_code, outcome = _worker_result(persisted)
        item = {
            "job_id": persisted.job_id,
            "job_type": persisted.job_type,
            "job_status": persisted.status.value,
            "attempts": persisted.attempts,
            "retry_count": persisted.payload.get("_retry_count", 0),
            "outcome": outcome,
            "has_error": bool(persisted.error_message),
        }
        processed.append(item)
        if exit_code != 0:
            _emit_receipt(
                "worker",
                outcome,
                processed=processed,
                processed_count=len(processed),
                queue_active=tracking_queue.count_active_jobs(),
            )
            return exit_code

    _emit_receipt(
        "worker",
        "bounded_complete",
        processed=processed,
        processed_count=len(processed),
        queue_active=tracking_queue.count_active_jobs(),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate")
    subparsers.add_parser("scheduler")
    worker = subparsers.add_parser("worker")
    worker.add_argument("--max-jobs", type=int, default=100)
    worker.add_argument("--require-job", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    if args.command == "migrate":
        return run_migration()
    if args.command == "scheduler":
        return run_scheduler()
    if args.max_jobs < 1:
        parser.error("--max-jobs must be at least 1")
    return run_worker(max_jobs=args.max_jobs, require_job=args.require_job)


if __name__ == "__main__":
    sys.exit(main())
