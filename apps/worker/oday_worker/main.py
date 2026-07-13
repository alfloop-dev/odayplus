from __future__ import annotations

import logging
import time
from typing import Any

from apps.worker.oday_worker.handlers import build_default_registry
from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence
from shared.jobs.queue import JobRecord, JobStatus
from shared.jobs.registry import JobRegistry

logger = logging.getLogger("oday-worker")


def worker_health() -> dict[str, str]:
    return {"status": "ok", "service": "oday-worker"}


class ODayWorker:
    def __init__(
        self,
        persistence: PersistenceBundle | None = None,
        registry: JobRegistry | None = None,
    ) -> None:
        self.persistence = persistence or build_persistence()
        self.job_queue = self.persistence.job_queue
        # The registry composes domain jobs modularly (ODP-SD-03 §11); the loop
        # below owns the shared claim/retry/dead-letter state machine.
        self.registry = registry or build_default_registry()

    def run_once(self) -> bool:
        """Claim and execute the next queued job. Returns True if a job was executed."""
        try:
            job = self.job_queue.claim_next()
        except Exception as exc:
            logger.error("Failed to claim next job: %s", exc)
            return False

        if job is None:
            return False

        logger.info("Executing job %s (type: %s)", job.job_id, job.job_type)
        try:
            self.execute_job(job)
            self.job_queue.update_status(job.job_id, JobStatus.SUCCEEDED)
            logger.info("Job %s succeeded", job.job_id)
        except Exception as exc:
            logger.error("Job %s failed: %s", job.job_id, exc)
            # Retry behavior
            payload = dict(job.payload)
            retries = payload.get("_retry_count", 0)
            if retries < 3:
                payload["_retry_count"] = retries + 1
                self.job_queue.update_status(job.job_id, JobStatus.QUEUED, payload=payload)
                logger.info("Job %s queued for retry (attempt %d/3)", job.job_id, retries + 1)
            else:
                self.job_queue.update_status(job.job_id, JobStatus.FAILED)
                logger.info("Job %s marked failed (max retries reached)", job.job_id)

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
