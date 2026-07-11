from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence
from shared.jobs.queue import JobRequest
from shared.observability import new_correlation_id

logger = logging.getLogger("oday-scheduler")


def scheduler_health() -> dict[str, str]:
    return {"status": "ok", "service": "oday-scheduler"}


class ODayScheduler:
    def __init__(self, persistence: PersistenceBundle | None = None) -> None:
        self.persistence = persistence or build_persistence()
        self.job_queue = self.persistence.job_queue

    def run_once(self) -> None:
        """Orchestrate and enqueue the scheduled tasks."""
        correlation_id = new_correlation_id()
        try:
            logger.info("Scheduler enqueuing external-fetch job")
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
        except Exception as exc:
            logger.error("Failed to enqueue scheduled job: %s", exc)

    def loop(self, stop_event: Any = None, interval: float = 30.0) -> None:
        while stop_event is None or not stop_event.is_set():
            self.run_once()
            time.sleep(interval)
