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

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from shared.jobs.queue import JobRecord, NonRetryableJobError
from shared.jobs.registry import JobRegistry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from shared.infrastructure.persistence.factory import PersistenceBundle

FORECAST_JOB_TYPE = "forecast"
EXTERNAL_FETCH_JOB_TYPE = "external-fetch"


def handle_forecast(job: JobRecord, persistence: PersistenceBundle) -> None:
    """Run a ForecastOps scoring pass for a store and persist the result."""
    from models.shared_ml import MlflowProductionModelRuntime
    from modules.forecastops.application.forecasting import ForecastInput
    from modules.forecastops.runtime import forecastops_production_required
    from modules.forecastops.workers import run_forecastops_batch_forecast

    store_id = job.payload.get("store_id")
    if not store_id:
        raise ValueError("Forecast job payload missing store_id")
    tenant_id = str(job.payload.get("tenant_id") or "").strip()
    if not tenant_id:
        raise NonRetryableJobError("Forecast job payload missing authenticated tenant scope")

    repo = persistence.forecastops_repository
    series = repo.get_series(tenant_id, store_id)
    if series is None or not series.observations:
        raise ValueError(
            f"Forecast job has no persisted timeseries for tenant {tenant_id}, "
            f"store {store_id}; "
            "synthetic runtime fallback is prohibited"
        )

    production_required = forecastops_production_required()
    model_runtime = MlflowProductionModelRuntime.from_environment() if production_required else None
    raw_origin = job.payload.get("prediction_origin_time")
    if isinstance(raw_origin, datetime):
        prediction_origin = raw_origin
    elif raw_origin:
        prediction_origin = datetime.fromisoformat(
            str(raw_origin).replace("Z", "+00:00")
        )
    else:
        latest_business_date = max(
            observation.business_date for observation in series.observations
        )
        prediction_origin = datetime.combine(
            latest_business_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=UTC,
        )
    if prediction_origin.tzinfo is None:
        prediction_origin = prediction_origin.replace(tzinfo=UTC)

    run_forecastops_batch_forecast(
        inputs=(
            ForecastInput(
                store_id=store_id,
                observations=series.observations,
                tenant_id=tenant_id,
                prediction_origin_time=prediction_origin,
            ),
        ),
        job_id=job.job_id,
        prediction_origin_time=prediction_origin,
        scored_at=job.created_at,
        repository=repo,
        model_runtime=model_runtime,
        runtime_mode="production" if production_required else "local",
    )


def handle_external_fetch(job: JobRecord, persistence: PersistenceBundle) -> None:
    """Run a scheduled external-source fetch and persist its ingestion run.

    The scheduler alone only writes fetch watermark state. Route the worker
    through the ingestion service so the queryable run and audit evidence are
    committed by the same execution path.
    """
    from datetime import timedelta

    from modules.external_data.application.ingestion_service import (
        ExternalIngestionService,
    )
    from modules.external_data.workers.scheduled_fetch import (
        CONFIGURATION_REASON_CODES,
        PROVIDER_NOT_SELECTED_REASON_CODE,
        ExternalFetchJobSpec,
    )

    provider_id = job.payload.get("provider_id", "listing.partner_feed")
    schedule_id = job.payload.get("schedule_id", "hourly-listing")
    freshness_sla_hours = job.payload.get("freshness_sla_hours", 6)

    service = ExternalIngestionService(
        store=persistence.ingestion_run_store,
        state_store=persistence.external_fetch_state_store,
        audit_log=persistence.audit_log,
    )
    spec = ExternalFetchJobSpec(
        provider_id=provider_id,
        schedule_id=schedule_id,
        freshness_sla=timedelta(hours=freshness_sla_hours),
    )
    outcome = service.run_scheduled(
        spec,
        scheduled_at=datetime.now(UTC),
        correlation_id=job.correlation_id,
    )
    record = outcome.record
    if record.status != "FAILED":
        return

    reason_code = _external_fetch_reason_code(record)
    if reason_code == PROVIDER_NOT_SELECTED_REASON_CODE:
        # ODP_PRODUCTION_PROVIDER_IDS is the operator's explicit statement of
        # which providers this deployment runs live. A schedule for a provider
        # left off that list is not applicable here, not broken: the blocked run
        # and its alert are already persisted for audit, no snapshot was written
        # and no watermark advanced. Dead-lettering the queue job on top of that
        # turns a correct operator decision into a permanent deployment blocker,
        # because the scheduler re-enqueues the same provider every tick.
        return
    if reason_code in CONFIGURATION_REASON_CODES:
        # Fail-closed and fixed for this release + environment: retrying cannot
        # change the outcome, so dead-letter now while the first attempt still
        # carries the real reason code.
        raise NonRetryableJobError(
            f"External fetch is fail-closed for {provider_id} ({reason_code}): "
            f"{record.message or 'no provider message'}"
        )
    raise RuntimeError(
        f"External fetch failed for {provider_id}: "
        f"{record.message or 'no provider message'}"
    )


def _external_fetch_reason_code(record: Any) -> str:
    """Read the reason code the scheduler attached to a blocked fetch run."""
    for alert in getattr(record, "alerts", ()) or ():
        code = str(alert.get("reason_code", "") or "").strip()
        if code:
            return code
    return ""


def build_default_registry() -> JobRegistry:
    """The registry the runtime worker uses by default."""
    from apps.worker.assisted_listing_intake.worker import (
        INTAKE_JOB_TYPE,
        handle_assisted_listing_intake,
    )

    registry = JobRegistry()
    registry.register(FORECAST_JOB_TYPE, handle_forecast)
    registry.register(EXTERNAL_FETCH_JOB_TYPE, handle_external_fetch)
    registry.register(INTAKE_JOB_TYPE, handle_assisted_listing_intake)
    return registry


__all__ = [
    "EXTERNAL_FETCH_JOB_TYPE",
    "FORECAST_JOB_TYPE",
    "build_default_registry",
    "handle_external_fetch",
    "handle_forecast",
]
