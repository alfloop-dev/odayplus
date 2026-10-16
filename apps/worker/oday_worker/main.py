from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from apps.worker.oday_worker.handlers import build_default_registry
from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence
from shared.infrastructure.persistence.job_queue import JobFenceRejectedError
from shared.jobs.queue import JobDeliveryState, JobRecord, JobStatus, NonRetryableJobError
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
        heartbeat: Callable[..., int] | None = None,
    ) -> None:
        self._queue = queue
        self._job_id = job.job_id
        self._fence_token = job.fence_token
        self._interval_seconds = max(interval_seconds, 0.01)
        self._heartbeat = heartbeat or queue.heartbeat
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
        if self._thread.ident is not None:
            self._thread.join()
        with self._state_lock:
            try:
                latest = self._queue.get(self._job_id)
                if (
                    latest is not None
                    and latest.status == JobStatus.RUNNING
                    and latest.fence_token == self._fence_token
                ):
                    self._version = latest.version
            except Exception:
                pass
            return self._version, self._failure

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            for _ in range(3):
                try:
                    latest = self._queue.get(self._job_id)
                except BaseException as exc:
                    with self._state_lock:
                        self._failure = exc
                    self._stop.set()
                    return

                if latest is None:
                    with self._state_lock:
                        self._failure = ValueError(f"Job {self._job_id} not found")
                    self._stop.set()
                    return
                if latest.status != JobStatus.RUNNING:
                    with self._state_lock:
                        self._failure = ValueError(
                            f"Job {self._job_id} is no longer RUNNING (now {latest.status.value})"
                        )
                    self._stop.set()
                    return
                if latest.fence_token != self._fence_token:
                    with self._state_lock:
                        self._failure = JobFenceRejectedError(
                            f"Job {self._job_id} fence moved: expected {self._fence_token}, got {latest.fence_token}"
                        )
                    self._stop.set()
                    return

                try:
                    new_version = self._heartbeat(
                        self._job_id,
                        expected_version=latest.version,
                        fence_token=self._fence_token,
                    )
                    with self._state_lock:
                        self._version = new_version
                    break
                except (JobFenceRejectedError, ValueError) as exc:
                    try:
                        latest_check = self._queue.get(self._job_id)
                    except Exception:
                        latest_check = None
                    if (
                        latest_check is not None
                        and latest_check.status == JobStatus.RUNNING
                        and latest_check.fence_token == self._fence_token
                    ):
                        continue
                    with self._state_lock:
                        self._failure = exc
                    self._stop.set()
                    return
                except BaseException as exc:
                    with self._state_lock:
                        self._failure = exc
                    self._stop.set()
                    return


class ODayWorker:
    def __init__(
        self,
        persistence: PersistenceBundle | None = None,
        registry: JobRegistry | None = None,
        telemetry: Telemetry | None = None,
        worker_id: str | None = None,
        heartbeat_interval_seconds: float = 15.0,
        audit_only: bool = False,
    ) -> None:
        self.persistence = persistence or build_persistence()
        self.job_queue = self.persistence.job_queue
        # The registry composes domain jobs modularly (ODP-SD-03 §11); the loop
        # below owns the shared claim/retry/dead-letter state machine.
        self.registry = registry or build_default_registry()
        self.telemetry = telemetry or Telemetry("oday-worker")
        self.worker_id = worker_id or _default_worker_id()
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.audit_only = audit_only

    def run_once(self) -> bool:
        """Claim and execute the next queued job. Returns True if a job was executed."""
        self.sample_audit_backlog()
        try:
            if self.audit_only:
                job = self.job_queue.claim_next(worker_id=self.worker_id, audit_only=True)
            else:
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

        from modules.listing.application.intake_score_delivery import (
            INTAKE_SITESCORE_JOB_TYPE,
            IntakeScoreJobRepository,
        )
        from modules.sitescore.application.score_delivery import (
            SITESCORE_EXECUTION_JOB_TYPE,
            ScoreExecutionJobRepository,
        )
        canonical_score_job = job.job_type in {INTAKE_SITESCORE_JOB_TYPE, SITESCORE_EXECUTION_JOB_TYPE}
        canonical_identity_job = job.job_type == "identity-execution"
        canonical_native_start_job = job.job_type == "native-intake-start"
        canonical_native_identity_job = job.job_type == "native-intake-identity"
        canonical_native_source_policy_job = job.job_type == "native-intake-source-policy"
        canonical_native_source_policy_recheck_job = job.job_type == "native-intake-source-policy-recheck"
        native_sql_command = (
            (canonical_native_start_job or canonical_native_identity_job or canonical_native_source_policy_job
             or canonical_native_source_policy_recheck_job)
            and getattr(getattr(self.persistence, "engine", None), "dialect", None) == "postgresql"
        )
        canonical_transaction_owned_job = (
            canonical_score_job or canonical_identity_job or canonical_native_start_job
            or canonical_native_identity_job or canonical_native_source_policy_job or canonical_native_source_policy_recheck_job
        )
        score_owner = ScoreExecutionJobRepository if job.job_type == SITESCORE_EXECUTION_JOB_TYPE else IntakeScoreJobRepository

        def score_heartbeat(job_id: str, *, expected_version: int, fence_token: int) -> int:
            # This adapter renews both the authoritative SQL lease and its
            # delivery lease in one transaction. Resolve it lazily so a
            # dependency failure enters the heartbeat's ownership handling.
            return score_owner(self.persistence).heartbeat(
                job_id, expected_version=expected_version, fence_token=fence_token,
            )

        def identity_heartbeat(job_id: str, *, expected_version: int, fence_token: int) -> int:
            from modules.listing.application.identity_delivery import IdentityJobAdmission

            return IdentityJobAdmission(self.persistence.canonical_identity_graph()).heartbeat(
                job_id, expected_version=expected_version, fence_token=fence_token)

        def native_intake_heartbeat(job_id: str, *, expected_version: int, fence_token: int) -> int:
            engine = getattr(self.persistence, "engine", None)
            if getattr(engine, "dialect", None) != "postgresql":
                return self.job_queue.heartbeat(job_id, expected_version=expected_version, fence_token=fence_token)
            # Bound the pre-command renewal independently. The native owner
            # subsequently checks this physical lease in its own SQL root.
            with engine.transaction():
                engine.query_one("SELECT set_config('lock_timeout',?,true)", ("100ms",))
                engine.query_one("SELECT set_config('statement_timeout',?,true)", ("500ms",))
                return self.job_queue.heartbeat(job_id, expected_version=expected_version, fence_token=fence_token)

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
                # Native Intake phases are SQL-only: physical heartbeat renews
                # their lease before the atomic phase/result transaction locks
                # that row. It must never invoke the identity authenticator.
                heartbeat=(score_heartbeat if canonical_score_job else identity_heartbeat if canonical_identity_job
                           else native_intake_heartbeat if (canonical_native_start_job or canonical_native_identity_job
                                                          or canonical_native_source_policy_job
                                                          or canonical_native_source_policy_recheck_job) else None),
            )
            try:
                if native_sql_command:
                    # Renew once before the SQL command opens its SERIALIZABLE
                    # root. A background UPDATE can invalidate the root's first
                    # physical-row snapshot, including protective settlement.
                    # Current caps are 40s for native start, 30s for identity,
                    # and 15s for policy evaluation. Each owner independently
                    # checks the actual 45s lease during command/settlement;
                    # no renewal runs concurrently with that root.
                    native_intake_heartbeat(job.job_id, expected_version=job.version, fence_token=job.fence_token)
                else:
                    heartbeat.start()
                self.execute_job(job)
                current_version, heartbeat_failure = heartbeat.stop()
                duration = time.monotonic() - start_time
                latest_job = self.job_queue.get(job.job_id)
                if latest_job and latest_job.status in (
                    JobStatus.SUCCEEDED,
                    JobStatus.PARTIAL,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                ):
                    status_label = latest_job.status.value
                    self.telemetry.metrics.observe(
                        "job_duration_seconds",
                        duration,
                        labels={"job_type": job.job_type, "status": status_label},
                    )
                    self.telemetry.logger.info(
                        f"Job {job.job_id} completed with status {status_label}",
                        correlation_id=job.correlation_id,
                        actor="worker",
                        resource=f"job/{job.job_type}",
                        action="execute",
                        result=status_label,
                    )
                    return True

                if canonical_transaction_owned_job:
                    if (latest_job is not None
                            and latest_job.status == JobStatus.QUEUED
                            and latest_job.delivery_state == JobDeliveryState.RETRYING):
                        self.telemetry.logger.info(
                            f"Job {job.job_id} retry committed by canonical score handler",
                            correlation_id=job.correlation_id,
                            resource=f"job/{job.job_type}", action="retry", result="queued",
                        )
                    else:
                        self._record_stale_worker(
                            job, heartbeat_failure or JobFenceRejectedError(
                                "Canonical score handler did not commit a terminal or retry checkpoint"
                            ),
                        )
                    # Only the canonical handler can commit the SQL job and
                    # delivery together. Never turn a return into a delivery-
                    # only success; retain unresolved leases for recovery.
                    return True

                if (
                    latest_job is None
                    or latest_job.status != JobStatus.RUNNING
                    or latest_job.fence_token != job.fence_token
                ):
                    self._record_stale_worker(job, JobFenceRejectedError("Job execution ownership changed"))
                    return True
                if heartbeat_failure is not None:
                    self._record_stale_worker(job, heartbeat_failure)
                    return True

                try:
                    self.job_queue.update_status(
                        job.job_id,
                        JobStatus.SUCCEEDED,
                        expected_version=latest_job.version,
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
                if latest_job and latest_job.status in (
                    JobStatus.CANCELLED,
                    JobStatus.SUCCEEDED,
                    JobStatus.PARTIAL,
                    JobStatus.FAILED,
                ):
                    self.telemetry.logger.info(
                        f"Job {job.job_id} execution stopped (status is {latest_job.status.value})",
                        correlation_id=job.correlation_id,
                        actor="worker",
                        resource=f"job/{job.job_type}",
                        action="cancel" if latest_job.status == JobStatus.CANCELLED else "execute",
                    )
                elif (
                    latest_job is None
                    or latest_job.status != JobStatus.RUNNING
                    or latest_job.fence_token != job.fence_token
                ):
                    self._record_stale_worker(job, JobFenceRejectedError("Job execution ownership changed"))
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

                    if canonical_transaction_owned_job:
                        # A storage/authorization/fence failure must not be
                        # converted into a separate delivery-only retry. The
                        # authoritative checkpoint stays recoverable.
                        return True

                    # Retry behavior
                    is_retryable = not isinstance(exc, NonRetryableJobError)
                    payload = (
                        dict(latest_job.payload)
                        if latest_job is not None and isinstance(latest_job.payload, dict)
                        else dict(job.payload)
                    )
                    expected_ver = (
                        latest_job.version if latest_job is not None else current_version
                    )
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
                            delivery_state=(
                                JobDeliveryState.RETRYING
                                if target_status == JobStatus.QUEUED
                                else JobDeliveryState.DEAD_LETTER
                            ),
                            expected_version=expected_ver,
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

    def sample_audit_backlog(self):
        self.sample_durable_audit_backlog()
        if getattr(getattr(self.persistence, "engine", None), "dialect", None) != "postgresql":
            return None

        from shared.audit.backlog import CanonicalAuditBacklog
        try:
            health = CanonicalAuditBacklog(self.persistence.engine).all_tenants()
            labels = {"service": "oday-worker"}
            self.telemetry.metrics.set("audit_worm_pending_count", health.pending, labels=labels)
            self.telemetry.metrics.set("audit_worm_oldest_pending_seconds", health.oldest_seconds, labels=labels)
            return health
        except Exception as exc:
            try:
                self.telemetry.metrics.increment("audit_worm_backlog_check_failure_count", labels={"service": "oday-worker", "error_class": type(exc).__name__})
            except Exception as metric_exc:
                self.telemetry.logger.error(
                    "Canonical audit backlog failure metric unavailable",
                    correlation_id="unknown", resource="audit/backlog",
                    error_code=type(metric_exc).__name__,
                )
            self.telemetry.logger.error("Canonical audit backlog observation unavailable", correlation_id="unknown", resource="audit/backlog", error_code=type(exc).__name__)
            return None

    def sample_durable_audit_backlog(self):
        engine = getattr(self.persistence, "engine", None)
        if engine is None:
            return None
        from shared.audit.backlog import DurableAuditBacklog

        sink = getattr(self.persistence.audit_log, "_worm_sink", None)
        labels = {"service": "oday-worker"}
        try:
            health = DurableAuditBacklog(engine, getattr(sink, "sink_id", None)).all_tenants()
            self.telemetry.metrics.set("durable_audit_worm_pending_count", health.pending, labels=labels)
            self.telemetry.metrics.set("durable_audit_worm_oldest_pending_seconds", health.oldest_seconds, labels=labels)
            return health
        except Exception as exc:
            try:
                self.telemetry.metrics.increment(
                    "durable_audit_worm_backlog_check_failure_count",
                    labels={**labels, "error_class": type(exc).__name__},
                )
            except Exception as metric_exc:
                self.telemetry.logger.error(
                    "Durable audit backlog failure metric unavailable",
                    correlation_id="unknown", resource="audit/backlog",
                    error_code=type(metric_exc).__name__,
                )
            self.telemetry.logger.error(
                "Durable audit backlog observation unavailable",
                correlation_id="unknown", resource="audit/backlog", error_code=type(exc).__name__,
            )
            return None

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
