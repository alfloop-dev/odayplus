from __future__ import annotations

import logging
import os
import time
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


def scheduler_health() -> dict[str, str]:
    return {"status": "ok", "service": "oday-scheduler"}


class ODayScheduler:
    def __init__(
        self,
        persistence: PersistenceBundle | None = None,
        telemetry: Telemetry | None = None,
    ) -> None:
        self.persistence = persistence or build_persistence()
        self.job_queue = self.persistence.job_queue
        self.telemetry = telemetry or Telemetry("oday-scheduler")

    def run_once(self) -> None:
        """Orchestrate and enqueue the scheduled tasks."""
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
            try:
                self.job_queue.enqueue(
                    JobRequest(
                        job_type="external-fetch",
                        payload={
                            "provider_id": "listing.partner_feed",
                            "schedule_id": "hourly-listing",
                            "freshness_sla_hours": 6,
                        },
                        idempotency_key=f"scheduled-fetch:{datetime.now(UTC).strftime('%Y%m%d%H%M')}",
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
        sha = (os.getenv("RELEASE_SHA") or os.getenv("GITHUB_SHA") or "").strip().lower()
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
            self.run_once()
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
