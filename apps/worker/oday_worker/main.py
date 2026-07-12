from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from modules.forecastops.application.forecasting import ForecastInput, ForecastOpsService
from shared.infrastructure.persistence.factory import PersistenceBundle, build_persistence
from shared.jobs.queue import JobRecord, JobStatus

logger = logging.getLogger("oday-worker")


def worker_health() -> dict[str, str]:
    return {"status": "ok", "service": "oday-worker"}


class ODayWorker:
    def __init__(self, persistence: PersistenceBundle | None = None) -> None:
        self.persistence = persistence or build_persistence()
        self.job_queue = self.persistence.job_queue

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
        if job.job_type == "forecast":
            store_id = job.payload.get("store_id")
            if not store_id:
                raise ValueError("Forecast job payload missing store_id")

            # Ingest/Mock timeseries if not present
            repo = self.persistence.forecastops_repository
            series = repo.get_series(store_id)
            if series is None or not series.observations:
                from datetime import date

                from modules.forecastops.domain.forecasting import StoreDayObservation
                # Produce default observations
                observations = tuple(
                    StoreDayObservation(
                        store_id=store_id,
                        business_date=date(2026, 6, day),
                        actual_revenue=float(80000 - day * 2000),
                        machine_cycles=int((80000 - day * 2000) / 100),
                        site_score_baseline_p50=100000.0,
                        source_snapshot_ids=(f"pos-202606{day:02d}",),
                    )
                    for day in range(20, 27)
                )
            else:
                observations = series.observations

            service = ForecastOpsService(repository=repo)
            service.forecast(
                [
                    ForecastInput(
                        store_id=store_id,
                        observations=observations,
                        prediction_origin_time=datetime.now(UTC),
                    )
                ]
            )
        elif job.job_type == "external-fetch":
            provider_id = job.payload.get("provider_id", "listing.partner_feed")
            schedule_id = job.payload.get("schedule_id", "hourly-listing")
            freshness_sla_hours = job.payload.get("freshness_sla_hours", 6)
            
            from datetime import timedelta

            from modules.external_data.workers.scheduled_fetch import (
                ExternalFetchJobSpec,
                ExternalFetchScheduler,
            )
            
            scheduler = ExternalFetchScheduler(
                state_store=self.persistence.external_fetch_state_store,
            )
            spec = ExternalFetchJobSpec(
                provider_id=provider_id,
                schedule_id=schedule_id,
                freshness_sla=timedelta(hours=freshness_sla_hours),
            )
            run = scheduler.run_once(
                spec,
                scheduled_at=datetime.now(UTC),
                correlation_id=job.correlation_id,
            )
            if run.status == "FAILED":
                raise RuntimeError(f"External fetch failed: {run.message}")
        else:
            raise ValueError(f"Unknown job_type: {job.job_type}")

    def loop(self, stop_event: Any = None) -> None:
        while stop_event is None or not stop_event.is_set():
            executed = self.run_once()
            if not executed:
                time.sleep(1.0)
