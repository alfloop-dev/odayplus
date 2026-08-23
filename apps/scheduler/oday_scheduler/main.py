from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence
from shared.jobs.queue import JobRequest
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
INVALID_CUTOVER_MODE_REASON_CODE = "external_data_cutover_mode_invalid"
CUTOVER_SKIP_REASON_CODE = "external_fetch_decommissioned"

#: The one recurring job a tick enqueues while odayplus still owns external
#: fetch. ODP-XR-CUTOVER-PREP-002 made that conditional rather than removing it:
#: see :meth:`ODayScheduler.recurring_job_types`.
EXTERNAL_FETCH_JOB_TYPE = "external-fetch"


class SchedulerTenantConfigurationError(RuntimeError):
    """Fail-closed: the scheduler has no configured tenant to enqueue work for.

    A scheduled ingest carries no authenticated principal, so the tenant can
    only come from deployment config. Enqueueing without one would hand the
    worker a job it must either reject or — worse — run against the unscoped
    default store, so the tick refuses to enqueue at all.
    """

    code = MISSING_TENANT_REASON_CODE


class SchedulerCutoverConfigurationError(RuntimeError):
    """Fail-closed: the deployment's cutover mode is not one this release knows.

    Treated exactly like a missing tenant: the tick enqueues nothing and says so
    every time, until an operator fixes the variable. Guessing a mode would mean
    either fetching from a deployment an operator believes is cut over, or
    silently stopping ingestion on one they believe is not.
    """

    code = INVALID_CUTOVER_MODE_REASON_CODE


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
            "No external-fetch job was enqueued "
            f"(code={MISSING_TENANT_REASON_CODE})"
        )

    def _resolve_cutover_mode(self) -> str:
        """Read the cutover switch for this tick.

        Imported lazily: the switch is defined by the read facade, and a
        scheduler process should not pay for that import unless a tick actually
        runs. Resolved per tick, never cached, so pulling the kill switch takes
        effect on the next tick instead of the next redeploy.
        """
        from modules.external_data.application.market_data_facade import (
            MarketDataValidationError,
            resolve_cutover_mode,
        )

        try:
            return resolve_cutover_mode(self.env)
        except MarketDataValidationError as exc:
            raise SchedulerCutoverConfigurationError(
                f"{exc} No external-fetch job was enqueued "
                f"(code={INVALID_CUTOVER_MODE_REASON_CODE})"
            ) from exc

    def recurring_job_types(self) -> tuple[str, ...]:
        """Job types a tick enqueues under the current cutover mode.

        ``external-fetch`` is the only recurring job this scheduler owns, so
        after the cutover this is empty and the deployment schedules nothing.
        The deployment unit stays either way: health and metrics still report,
        and a future recurring job registers here.
        """
        from modules.external_data.application.market_data_facade import (
            legacy_external_fetch_enabled,
        )

        return (EXTERNAL_FETCH_JOB_TYPE,) if legacy_external_fetch_enabled(self.env) else ()

    def run_once(self) -> None:
        """Orchestrate and enqueue the scheduled tasks.

        Raises :class:`SchedulerTenantConfigurationError` when no tenant is
        configured and :class:`SchedulerCutoverConfigurationError` when the
        cutover mode is unreadable; nothing is enqueued in either case.

        Once the cutover selects ``PLATFORM_PRIMARY`` the tick still runs and
        still checks its tenant -- a deployment that lost its scheduled-ingestion
        tenant is misconfigured whether or not there is work to enqueue -- but it
        enqueues nothing, so no provider credential can be reached on a schedule.
        Moving the mode back, or pulling the kill switch, restores enqueueing on
        the very next tick without a redeploy.
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
            mode = self._resolve_cutover_mode()
            tenant_id = self._require_tenant_id()
            if EXTERNAL_FETCH_JOB_TYPE not in self.recurring_job_types():
                self.telemetry.logger.info(
                    "Scheduler tick enqueued no jobs: external fetch is "
                    f"decommissioned on this deployment (cutover mode {mode})",
                    correlation_id=correlation_id,
                    actor="scheduler",
                    resource="scheduler/tick",
                    action="skip",
                    result="ok",
                    error_code=CUTOVER_SKIP_REASON_CODE,
                )
                return
            try:
                self.job_queue.enqueue(
                    JobRequest(
                        job_type=EXTERNAL_FETCH_JOB_TYPE,
                        payload={
                            "tenant_id": tenant_id,
                            "provider_id": "listing.partner_feed",
                            "schedule_id": "hourly-listing",
                            "freshness_sla_hours": 6,
                        },
                        # The tenant belongs in the key: two tenants sharing one
                        # window must not collapse into a single queued job.
                        idempotency_key=(
                            f"scheduled-fetch:{tenant_id}:"
                            f"{datetime.now(UTC).strftime('%Y%m%d%H%M')}"
                        ),
                    ),
                    correlation_id=correlation_id,
                )
                self.telemetry.logger.info(
                    "Scheduler enqueued external-fetch job",
                    correlation_id=correlation_id,
                    actor="scheduler",
                    resource="job/external-fetch",
                    action="enqueue",
                    result="ok",
                )
            except Exception as exc:
                self.telemetry.logger.error(
                    f"Failed to enqueue scheduled job: {exc}",
                    correlation_id=correlation_id,
                    actor="scheduler",
                    resource="scheduler/tick",
                    error_code=type(exc).__name__,
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
            except (
                SchedulerTenantConfigurationError,
                SchedulerCutoverConfigurationError,
            ) as exc:
                # Fail closed and stay loud: nothing was enqueued, and every
                # tick keeps reporting the misconfiguration until an operator
                # sets the tenant or fixes the cutover mode. Crashing the process
                # instead would only trade a visible error for a restart loop.
                self.telemetry.logger.error(
                    f"Scheduler tick skipped: {exc}",
                    correlation_id="unknown",
                    actor="scheduler",
                    resource="scheduler/tick",
                    error_code=exc.code,
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
