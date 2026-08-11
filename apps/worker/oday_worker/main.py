from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Any
from uuid import uuid4

from apps.worker.oday_worker.handlers import build_default_registry
from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence
from shared.infrastructure.persistence.job_queue import JobFenceRejectedError
from shared.jobs.queue import JobRecord, JobStatus, NonRetryableJobError
from shared.jobs.registry import JobRegistry
from shared.observability import ProductionMetricsExporter, SpanKind, Telemetry, TraceContext

logger = logging.getLogger("oday-worker")


def worker_health() -> dict[str, str]:
    return {"status": "ok", "service": "oday-worker"}


class _LeaseHeartbeat:
    def __init__(
        self,
        *,
        queue: Any,
        job: JobRecord,
        interval_seconds: float,
    ) -> None:
        self._queue = queue
        self._job_id = job.job_id
        self._fence_token = job.fence_token
        self._interval_seconds = max(interval_seconds, 0.01)
        self._version = job.version
        self._failure: BaseException | None = None
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"job-heartbeat-{job.job_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> tuple[int, BaseException | None]:
        self._stop.set()
        self._thread.join()
        with self._state_lock:
            return self._version, self._failure

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            with self._state_lock:
                expected_version = self._version
            try:
                new_version = self._queue.heartbeat(
                    self._job_id,
                    expected_version=expected_version,
                    fence_token=self._fence_token,
                )
            except BaseException as exc:
                with self._state_lock:
                    self._failure = exc
                self._stop.set()
                return
            with self._state_lock:
                self._version = new_version


class ODayWorker:
    def __init__(
        self,
        persistence: PersistenceBundle | None = None,
        registry: JobRegistry | None = None,
        telemetry: Telemetry | None = None,
        worker_id: str | None = None,
        heartbeat_interval_seconds: float = 15.0,
    ) -> None:
        self.persistence = persistence or build_persistence()
        self.job_queue = self.persistence.job_queue
        # The registry composes domain jobs modularly (ODP-SD-03 §11); the loop
        # below owns the shared claim/retry/dead-letter state machine.
        self.registry = registry or build_default_registry()
        self.telemetry = telemetry or Telemetry("oday-worker")
        self.worker_id = worker_id or _default_worker_id()
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

    def run_once(self) -> bool:
        """Claim and execute the next queued job. Returns True if a job was executed."""
        try:
            job = self.job_queue.claim_next(worker_id=self.worker_id)
        except Exception as exc:
            self.telemetry.logger.error(
                "Failed to claim next job: %s",
                correlation_id="unknown",
                resource="job/queue",
                error_code=type(exc).__name__,
            )
            return False

        if job is None:
            return False

        context = TraceContext(
            correlation_id=job.correlation_id,
            actor_id="worker",
            job_id=job.job_id,
        )

        with self.telemetry.tracer.start_span(
            f"worker-{job.job_type}", SpanKind.WORKER, context=context
        ):
            self.telemetry.logger.info(
                f"Executing job {job.job_id} (type: {job.job_type})",
                correlation_id=job.correlation_id,
                actor="worker",
                resource=f"job/{job.job_type}",
                action="execute",
            )

            start_time = time.monotonic()
            heartbeat = _LeaseHeartbeat(
                queue=self.job_queue,
                job=job,
                interval_seconds=self.heartbeat_interval_seconds,
            )
            heartbeat.start()
            try:
                self.execute_job(job)
                current_version, heartbeat_failure = heartbeat.stop()
                if heartbeat_failure is not None:
                    self._record_stale_worker(job, heartbeat_failure)
                    return True
                duration = time.monotonic() - start_time
                try:
                    self.job_queue.update_status(
                        job.job_id,
                        JobStatus.SUCCEEDED,
                        expected_version=current_version,
                        fence_token=job.fence_token,
                    )
                except (JobFenceRejectedError, ValueError) as exc:
                    self._record_stale_worker(job, exc)
                    return True

                # Record metrics
                self.telemetry.metrics.observe(
                    "job_duration_seconds",
                    duration,
                    labels={"job_type": job.job_type, "status": "success"},
                )
                self.telemetry.logger.info(
                    f"Job {job.job_id} succeeded",
                    correlation_id=job.correlation_id,
                    actor="worker",
                    resource=f"job/{job.job_type}",
                    action="execute",
                    result="success",
                )
            except Exception as exc:
                current_version, heartbeat_failure = heartbeat.stop()
                duration = time.monotonic() - start_time
                latest_job = self.job_queue.get(job.job_id)
                if latest_job and latest_job.status == JobStatus.CANCELLED:
                    self.telemetry.logger.info(
                        f"Job {job.job_id} execution aborted because it was CANCELLED",
                        correlation_id=job.correlation_id,
                        actor="worker",
                        resource=f"job/{job.job_type}",
                        action="cancel",
                    )
                elif heartbeat_failure is not None:
                    self._record_stale_worker(job, heartbeat_failure)
                else:
                    # Record metrics
                    self.telemetry.metrics.observe(
                        "job_duration_seconds",
                        duration,
                        labels={"job_type": job.job_type, "status": "failure"},
                    )
                    self.telemetry.metrics.increment(
                        "job_failure_count",
                        labels={"job_type": job.job_type, "error_class": type(exc).__name__},
                    )
                    self.telemetry.logger.error(
                        f"Job {job.job_id} failed: {exc}",
                        correlation_id=job.correlation_id,
                        actor="worker",
                        resource=f"job/{job.job_type}",
                        action="execute",
                        result="error",
                        error_code=type(exc).__name__,
                    )

                    # Retry behavior
                    is_retryable = not isinstance(exc, NonRetryableJobError)
                    payload = dict(job.payload)
                    retries = payload.get("_retry_count", 0)
                    if is_retryable and retries < 3:
                        payload["_retry_count"] = retries + 1
                        target_status = JobStatus.QUEUED
                    else:
                        target_status = JobStatus.FAILED
                    try:
                        self.job_queue.update_status(
                            job.job_id,
                            target_status,
                            payload=payload if target_status == JobStatus.QUEUED else None,
                            expected_version=current_version,
                            fence_token=job.fence_token,
                            error_message=str(exc),
                        )
                    except (JobFenceRejectedError, ValueError) as fence_exc:
                        self._record_stale_worker(job, fence_exc)
                    else:
                        if target_status == JobStatus.QUEUED:
                            self.telemetry.logger.info(
                                f"Job {job.job_id} queued for retry (attempt {retries + 1}/3)",
                                correlation_id=job.correlation_id,
                                actor="worker",
                                resource=f"job/{job.job_type}",
                                action="retry",
                            )
                        else:
                            self.telemetry.logger.info(
                                f"Job {job.job_id} marked failed (max retries reached or non-retryable)",
                                correlation_id=job.correlation_id,
                                actor="worker",
                                resource=f"job/{job.job_type}",
                                action="fail",
                            )

        return True

    def _record_stale_worker(self, job: JobRecord, exc: BaseException) -> None:
        self.telemetry.metrics.increment(
            "job_failure_count",
            labels={
                "job_type": job.job_type,
                "error_class": "STALE_WORKER_FENCE_REJECTED",
            },
        )
        self.telemetry.logger.error(
            f"Job {job.job_id} lease ownership was lost: {exc}",
            correlation_id=job.correlation_id,
            actor="worker",
            resource=f"job/{job.job_type}",
            action="fence",
            result="rejected",
            error_code="STALE_WORKER_FENCE_REJECTED",
        )

    def execute_job(self, job: JobRecord) -> None:
        # Dispatch through the registry: no monolithic switch over job_type.
        # An unregistered job_type raises UnknownJobTypeError (a ValueError),
        # which the loop retries and finally dead-letters.
        self.registry.handle(job, self.persistence)

    def loop(self, stop_event: Any = None) -> None:
        while stop_event is None or not stop_event.is_set():
            executed = self.run_once()
            if executed:
                try:
                    self.export_metrics()
                except Exception as exc:
                    self.telemetry.logger.error(
                        f"Worker lifecycle export_metrics failed: {exc}",
                        correlation_id="unknown",
                        resource="worker/metrics",
                        error_code=type(exc).__name__,
                    )
            if not executed:
                time.sleep(1.0)

    def export_metrics(self) -> dict[str, Any] | None:
        """Export worker metrics snapshot via ProductionMetricsExporter if exact 40-char release SHA is present in environment."""
        from shared.runtime_config import get_release_identity

        sha = get_release_identity().lower()
        if sha and len(sha) == 40 and sha != "local":
            try:
                exporter = ProductionMetricsExporter(release_sha=sha, registry=self.telemetry.metrics)
                return exporter.export_metrics()
            except Exception as exc:
                self.telemetry.logger.error(
                    f"Worker metrics export failed: {exc}",
                    correlation_id="unknown",
                    resource="worker/metrics",
                    error_code=type(exc).__name__,
                )
                raise
        return None


def _default_worker_id() -> str:
    configured = os.getenv("ODP_WORKER_ID", "").strip()
    if configured:
        return configured
    revision = os.getenv("K_REVISION", "").strip()
    hostname = socket.gethostname().strip()
    instance = f"{revision}:{hostname}".strip(":")
    return instance or f"worker-{uuid4()}"
