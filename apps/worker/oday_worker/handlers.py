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
SITESCORE_CANDIDATE_JOB_TYPE = "sitescore-candidate"


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


def handle_sitescore_candidate(
    job: JobRecord,
    persistence: PersistenceBundle,
) -> None:
    """Score one promoted candidate and advance its durable promotion saga."""
    from modules.listing.domain.intake_states import (
        Actor,
        PrincipalRole,
        PromotionAggregate,
        PromotionState,
        PromotionStateMachine,
        TransitionContext,
    )
    from modules.sitescore.workers.scoring_worker import (
        run_sitescore_batch_score,
    )
    from shared.jobs.queue import JobStatus

    payload = job.payload
    promotion_decision_id = payload.get("promotion_decision_id")
    features = payload.get("features")
    if not promotion_decision_id:
        raise ValueError("SiteScore candidate job missing promotion_decision_id")
    if not isinstance(features, list) or not features:
        raise ValueError("SiteScore candidate job missing features")

    repository = persistence.operator_intake_repository
    if repository is None:
        raise RuntimeError("SiteScore candidate job requires intake persistence")
    promotion = repository.get_promotion(promotion_decision_id)
    if promotion is None:
        raise ValueError(
            f"Promotion decision {promotion_decision_id} not found"
        )
    if promotion.get("site_score_job_id") != job.job_id:
        raise ValueError("SiteScore job does not match promotion decision")

    actor = Actor(
        actor_id="sitescore-worker",
        role=PrincipalRole.SVC_PROMOTION,
        tenant_id=promotion["tenant_id"],
    )
    context = TransitionContext(
        actor=actor,
        idempotency_key=(
            job.idempotency_key or f"sitescore-job:{job.job_id}"
        ),
        correlation_id=job.correlation_id,
    )
    aggregate = PromotionAggregate(
        id=promotion_decision_id,
        tenant_id=promotion["tenant_id"],
        status=PromotionState(promotion["status"]),
        version=int(promotion["version"]),
        proposer_id=promotion.get("proposer_subject_id")
        or promotion.get("proposer"),
    )

    if aggregate.status == PromotionState.SCORE_FAILED:
        PromotionStateMachine.transition(
            aggregate,
            PromotionState.SCORE_QUEUED,
            context,
        )
        promotion["status"] = PromotionState.SCORE_QUEUED.value
        promotion["version"] = aggregate.version
        repository.save_promotion(promotion)
    elif aggregate.status != PromotionState.SCORE_QUEUED:
        raise ValueError(
            f"Promotion decision is in {aggregate.status.value}, expected SCORE_QUEUED"
        )

    try:
        result = run_sitescore_batch_score(
            job_id=job.job_id,
            features=features,
            repository=persistence.sitescore_repository,
        )
        reports = [report.to_dict() for report in result.reports]
        updated_payload = dict(payload)
        updated_payload["result"] = {
            "status": result.status,
            "completed_at": result.completed_at.isoformat(),
            "report_ids": [report["report_id"] for report in reports],
            "sitescore_run_ids": [
                report["sitescore_run_id"] for report in reports
            ],
        }
        persistence.job_queue.update_status(
            job.job_id,
            JobStatus.RUNNING,
            payload=updated_payload,
        )
    except Exception:
        PromotionStateMachine.transition(
            aggregate,
            PromotionState.SCORE_FAILED,
            context,
        )
        promotion["status"] = PromotionState.SCORE_FAILED.value
        promotion["version"] = aggregate.version
        promotion["score_failed_at"] = datetime.now(UTC).isoformat()
        repository.save_promotion(promotion)
        raise

    PromotionStateMachine.transition(
        aggregate,
        PromotionState.COMPLETED,
        context,
    )
    promotion["status"] = PromotionState.COMPLETED.value
    promotion["version"] = aggregate.version
    promotion["completed_at"] = result.completed_at.isoformat()
    promotion["site_score_report_ids"] = [
        report.report_id for report in result.reports
    ]
    promotion["site_score_run_ids"] = [
        report.sitescore_run_id for report in result.reports
    ]
    repository.save_promotion(promotion)


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
    registry.register(SITESCORE_CANDIDATE_JOB_TYPE, handle_sitescore_candidate)
    return registry


__all__ = [
    "EXTERNAL_FETCH_JOB_TYPE",
    "FORECAST_JOB_TYPE",
    "SITESCORE_CANDIDATE_JOB_TYPE",
    "build_default_registry",
    "handle_external_fetch",
    "handle_forecast",
    "handle_sitescore_candidate",
]
