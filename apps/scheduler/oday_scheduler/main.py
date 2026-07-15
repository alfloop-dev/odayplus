from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence
from shared.jobs.queue import JobRequest
from shared.observability import SpanKind, Telemetry, TraceContext, new_correlation_id

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

    def loop(self, stop_event: Any = None, interval: float = 30.0) -> None:
        while stop_event is None or not stop_event.is_set():
            self.run_once()
            time.sleep(interval)

