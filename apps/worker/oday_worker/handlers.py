"""Default runtime job handlers (ODP-FLOW-011).

These are the durable jobs the first-version ``worker`` deployment unit
(ODP-SD-03 §4) executes beyond a heartbeat. Each handler is a small, isolated
function registered into a :class:`~shared.jobs.registry.JobRegistry`; the
worker loop owns claim/retry/dead-letter (ODP-SD-08 §3.2), the handlers own the
domain work. Adding a domain job means adding a handler + one ``register`` call,
never editing a central switch.

Domain services are imported lazily inside each handler so importing this module
(and therefore constructing a worker) does not eagerly pull every domain
package into the process.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from shared.jobs.queue import JobRecord
from shared.jobs.registry import JobRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from shared.infrastructure.persistence.factory import PersistenceBundle

FORECAST_JOB_TYPE = "forecast"
EXTERNAL_FETCH_JOB_TYPE = "external-fetch"


def handle_forecast(job: JobRecord, persistence: PersistenceBundle) -> None:
    """Run a ForecastOps scoring pass for a store and persist the result."""
    from modules.forecastops.application.forecasting import ForecastInput, ForecastOpsService

    store_id = job.payload.get("store_id")
    if not store_id:
        raise ValueError("Forecast job payload missing store_id")

    # Ingest / mock the timeseries if the repository has none yet.
    repo = persistence.forecastops_repository
    series = repo.get_series(store_id)
    if series is None or not series.observations:
        from datetime import date

        from modules.forecastops.domain.forecasting import StoreDayObservation

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


def handle_external_fetch(job: JobRecord, persistence: PersistenceBundle) -> None:
    """Run a scheduled external-source fetch and advance its watermark."""
    from datetime import timedelta

    from modules.external_data.workers.scheduled_fetch import (
        ExternalFetchJobSpec,
        ExternalFetchScheduler,
    )

    provider_id = job.payload.get("provider_id", "listing.partner_feed")
    schedule_id = job.payload.get("schedule_id", "hourly-listing")
    freshness_sla_hours = job.payload.get("freshness_sla_hours", 6)

    scheduler = ExternalFetchScheduler(
        state_store=persistence.external_fetch_state_store,
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


def build_default_registry() -> JobRegistry:
    """The registry the runtime worker uses by default."""
    registry = JobRegistry()
    registry.register(FORECAST_JOB_TYPE, handle_forecast)
    registry.register(EXTERNAL_FETCH_JOB_TYPE, handle_external_fetch)
    return registry


__all__ = [
    "EXTERNAL_FETCH_JOB_TYPE",
    "FORECAST_JOB_TYPE",
    "build_default_registry",
    "handle_external_fetch",
    "handle_forecast",
]
