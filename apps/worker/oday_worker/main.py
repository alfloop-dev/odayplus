from __future__ import annotations

import logging
import time
from typing import Any

from apps.worker.oday_worker.handlers import build_default_registry
from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence
from shared.jobs.queue import JobRecord, JobStatus, NonRetryableJobError
from shared.jobs.registry import JobRegistry
from shared.observability import SpanKind, Telemetry, TraceContext

logger = logging.getLogger("oday-worker")


def worker_health() -> dict[str, str]:
    return {"status": "ok", "service": "oday-worker"}


class ODayWorker:
    def __init__(
        self,
        persistence: PersistenceBundle | None = None,
        registry: JobRegistry | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.persistence = persistence or build_persistence()
        self.job_queue = self.persistence.job_queue
        # The registry composes domain jobs modularly (ODP-SD-03 §11); the loop
        # below owns the shared claim/retry/dead-letter state machine.
        self.registry = registry or build_default_registry()
        self.telemetry = telemetry or Telemetry("oday-worker")

    def run_once(self) -> bool:
        """Claim and execute the next queued job. Returns True if a job was executed."""
        try:
            job = self.job_queue.claim_next()
        except Exception as exc:
            self.telemetry.logger.error("Failed to claim next job: %s", correlation_id="unknown", resource="job/queue", error_code=type(exc).__name__)
            return False

        if job is None:
            return False

        context = TraceContext(
            correlation_id=job.correlation_id,
            actor_id="worker",
            job_id=job.job_id,
        )

        with self.telemetry.tracer.start_span(f"worker-{job.job_type}", SpanKind.WORKER, context=context):
            self.telemetry.logger.info(
                f"Executing job {job.job_id} (type: {job.job_type})",
                correlation_id=job.correlation_id,
                actor="worker",
                resource=f"job/{job.job_type}",
                action="execute",
            )

            start_time = time.monotonic()
            try:
                self.execute_job(job)
                duration = time.monotonic() - start_time
                self.job_queue.update_status(job.job_id, JobStatus.SUCCEEDED)

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
                else:
                    self.job_queue.update_status(job.job_id, JobStatus.FAILED, error_message=str(exc))

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
                        self.job_queue.update_status(job.job_id, JobStatus.QUEUED, payload=payload)
                        self.telemetry.logger.info(
                            f"Job {job.job_id} queued for retry (attempt {retries + 1}/3)",
                            correlation_id=job.correlation_id,
                            actor="worker",
                            resource=f"job/{job.job_type}",
                            action="retry",
                        )
                    else:
                        self.job_queue.update_status(job.job_id, JobStatus.FAILED, error_message=str(exc))
                        self.telemetry.logger.info(
                            f"Job {job.job_id} marked failed (max retries reached or non-retryable)",
                            correlation_id=job.correlation_id,
                            actor="worker",
                            resource=f"job/{job.job_type}",
                            action="fail",
                        )

        return True

    def execute_job(self, job: JobRecord) -> None:
        # Dispatch through the registry: no monolithic switch over job_type.
        # An unregistered job_type raises UnknownJobTypeError (a ValueError),
        # which the loop retries and finally dead-letters.
        self.registry.handle(job, self.persistence)

    def loop(self, stop_event: Any = None) -> None:
        while stop_event is None or not stop_event.is_set():
            executed = self.run_once()
            if not executed:
                time.sleep(1.0)
