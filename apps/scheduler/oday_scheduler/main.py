from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from typing import Any

from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence
from shared.observability import (
    ProductionMetricsExporter,
    SpanKind,
    Telemetry,
    TraceContext,
    new_correlation_id,
)

logger = logging.getLogger("oday-scheduler")

#: Primary env var naming the tenant every scheduled ingest job runs as, with
#: the process-wide ``ODP_TENANT_ID`` (already used by the backfill CLI) as the
#: fallback for single-tenant deployments.
SCHEDULED_TENANT_ENV_VAR = "ODP_SCHEDULED_INGESTION_TENANT_ID"
FALLBACK_TENANT_ENV_VAR = "ODP_TENANT_ID"
MISSING_TENANT_REASON_CODE = "scheduled_ingestion_tenant_missing"


class SchedulerTenantConfigurationError(RuntimeError):
    """Fail-closed: the scheduler has no configured tenant to enqueue work for.

    A scheduled ingest carries no authenticated principal, so the tenant can
    only come from deployment config. Enqueueing without one would hand the
    worker a job it must either reject or — worse — run against the unscoped
    default store, so the tick refuses to enqueue at all.
    """

    code = MISSING_TENANT_REASON_CODE


def scheduler_health() -> dict[str, str]:
    return {"status": "ok", "service": "oday-scheduler"}


def resolve_scheduled_tenant_id(env: Mapping[str, str] | None = None) -> str:
    """Read the configured scheduled-ingestion tenant; ``""`` when unset."""
    source = os.environ if env is None else env
    for name in (SCHEDULED_TENANT_ENV_VAR, FALLBACK_TENANT_ENV_VAR):
        value = str(source.get(name, "") or "").strip()
        if value:
            return value
    return ""


class ODayScheduler:
    #: Job types a tick enqueues. XR-CUTOVER-001 emptied this: ``external-fetch``
    #: was the only one, and the providers behind it were decommissioned. The
    #: Cloud Run entrypoint reads it to tell "this deployment schedules nothing"
    #: apart from "a recurring job failed to enqueue", which stays a failure.
    RECURRING_JOB_TYPES: tuple[str, ...] = ()

    def __init__(
        self,
        persistence: PersistenceBundle | None = None,
        telemetry: Telemetry | None = None,
        tenant_id: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.persistence = persistence or build_persistence()
        self.job_queue = self.persistence.job_queue
        self.telemetry = telemetry or Telemetry("oday-scheduler")
        self.env = os.environ if env is None else env
        self.tenant_id = (
            str(tenant_id).strip()
            if tenant_id is not None
            else resolve_scheduled_tenant_id(self.env)
        )

    def _require_tenant_id(self) -> str:
        if self.tenant_id:
            return self.tenant_id
        raise SchedulerTenantConfigurationError(
            "Scheduled ingestion has no configured tenant scope: set "
            f"{SCHEDULED_TENANT_ENV_VAR} (or {FALLBACK_TENANT_ENV_VAR}) on the "
            "oday-scheduler deployment, or construct ODayScheduler(tenant_id=...). "
            "The tick was skipped "
            f"(code={MISSING_TENANT_REASON_CODE})"
        )

    def run_once(self) -> None:
        """Tick without enqueueing external ingestion (XR-CUTOVER-001).

        ``external-fetch`` was the only recurring job this scheduler owned, and
        the cutover removed the providers, the fetch state and the ingestion
        service it drove: those datasets are produced by oday-data-platform and
        read through the market data facade. The deployment unit stays — health
        and metrics still report, and a future recurring job registers here —
        but a tick no longer enqueues anything, so no provider credential can be
        reached on a schedule.

        The tenant guard is kept and still runs first. A deployment that lost
        its scheduled-ingestion tenant is misconfigured whether or not there is
        work to enqueue, and ``loop`` already surfaces that as a loud, non-fatal
        tick error.
        """
        correlation_id = new_correlation_id()
        context = TraceContext(
            correlation_id=correlation_id,
            actor_id="scheduler",
        )
        with self.telemetry.tracer.start_span("scheduler-tick", SpanKind.WORKER, context=context):
            self.telemetry.logger.info(
                "Scheduler tick start",
                correlation_id=correlation_id,
                actor="scheduler",
                resource="scheduler/tick",
            )
            self._require_tenant_id()
            self.telemetry.logger.info(
                "Scheduler tick enqueued no jobs: external ingestion is "
                "decommissioned (XR-CUTOVER-001)",
                correlation_id=correlation_id,
                actor="scheduler",
                resource="scheduler/tick",
                action="skip",
                result="ok",
            )

    def export_metrics(self) -> dict[str, Any] | None:
        """Export scheduler metrics snapshot via ProductionMetricsExporter if exact 40-char release SHA is present in environment."""
        from shared.runtime_config import get_release_identity

        sha = get_release_identity().lower()
        if sha and len(sha) == 40 and sha != "local":
            try:
                exporter = ProductionMetricsExporter(release_sha=sha, registry=self.telemetry.metrics)
                return exporter.export_metrics()
            except Exception as exc:
                self.telemetry.logger.error(
                    f"Scheduler metrics export failed: {exc}",
                    correlation_id="unknown",
                    resource="scheduler/metrics",
                    error_code=type(exc).__name__,
                )
                raise
        return None

    def loop(self, stop_event: Any = None, interval: float = 30.0) -> None:
        while stop_event is None or not stop_event.is_set():
            try:
                self.run_once()
            except SchedulerTenantConfigurationError as exc:
                # Fail closed and stay loud: nothing was enqueued, and every
                # tick keeps reporting the misconfiguration until an operator
                # sets the tenant. Crashing the process instead would only
                # trade a visible error for a restart loop.
                self.telemetry.logger.error(
                    f"Scheduler tick skipped: {exc}",
                    correlation_id="unknown",
                    actor="scheduler",
                    resource="scheduler/tick",
                    error_code=MISSING_TENANT_REASON_CODE,
                )
            try:
                self.export_metrics()
            except Exception as exc:
                self.telemetry.logger.error(
                    f"Scheduler lifecycle export_metrics failed: {exc}",
                    correlation_id="unknown",
                    resource="scheduler/metrics",
                    error_code=type(exc).__name__,
                )
            time.sleep(interval)
