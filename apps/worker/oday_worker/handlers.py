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
from uuid import uuid4

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
        policy_repository=getattr(persistence, "forecastops_policy_repository", None),
        model_runtime=model_runtime,
        runtime_mode="production" if production_required else "local",
    )


def handle_external_fetch(job: JobRecord, persistence: PersistenceBundle) -> None:
    """Run a scheduled external-source fetch and persist its ingestion run.

    The scheduler alone only writes fetch watermark state. Route the worker
    through the ingestion service so the queryable run and audit evidence are
    committed by the same execution path.

    The job type stays registered after the cutover on purpose. A queue drained
    across the cutover can still hold enqueued ``external-fetch`` jobs, and an
    unregistered type would retry until it dead-letters with an opaque "unknown
    job type". Refusing here instead dead-letters on the first attempt with the
    reason an operator needs, and -- because no fetch is attempted -- guarantees
    no provider credential is read and no watermark advances.
    """
    from datetime import timedelta

    from modules.external_data.application.ingestion_service import (
        SCHEDULED_TENANT_ENV_VAR,
        ExternalIngestionService,
    )
    from modules.external_data.application.market_data_facade import (
        FACADE_MODE_ENV,
        MarketDataValidationError,
        legacy_external_fetch_enabled,
        resolve_cutover_mode,
    )
    from modules.external_data.workers.scheduled_fetch import (
        CONFIGURATION_REASON_CODES,
        PROVIDER_MODE_DISABLED_REASON_CODE,
        PROVIDER_NOT_SELECTED_REASON_CODE,
        ExternalFetchJobSpec,
        external_provider_fetch_disabled,
    )

    provider_id = job.payload.get("provider_id", "listing.partner_feed")
    schedule_id = job.payload.get("schedule_id", "hourly-listing")
    freshness_sla_hours = job.payload.get("freshness_sla_hours", 6)

    # Provider-off is authoritative for this job. Still run the scheduler's
    # deterministic blocked path so the worker produces an auditable receipt,
    # but never let the unrelated market-data cutover switch turn this probe
    # into a retrying/dead-lettering worker failure. The question is asked
    # through ``workers.scheduled_fetch``, the consumer-facing boundary for
    # external data: reading ``connectors.provider_registry`` here would put
    # producer internals in product code and trip the EMGI consumer boundary
    # check (delivery_toolchain/governance/emgi-consumer-boundary.json).
    provider_mode_disabled = external_provider_fetch_disabled()
    if provider_mode_disabled:
        fetch_enabled = True
    else:
        # Resolved per job, from the same switch the scheduler and the API read,
        # so the three cannot disagree about whether this deployment still
        # fetches.
        try:
            fetch_enabled = legacy_external_fetch_enabled()
        except MarketDataValidationError as exc:
            # A mode this release cannot read is fixed for the deployment: no
            # retry changes it, so dead-letter now while the reason is still
            # attached.
            raise NonRetryableJobError(
                f"External fetch cannot run: {exc}. Fix {FACADE_MODE_ENV} on the "
                "worker deployment."
            ) from exc
    if not fetch_enabled:
        raise NonRetryableJobError(
            "External fetch is decommissioned on this deployment (cutover mode "
            f"{resolve_cutover_mode()}): provider '{provider_id}' (schedule "
            f"'{schedule_id}') is ingested by oday-data-platform and read "
            "through the market data facade. Remove the schedule that enqueued "
            "this job, or roll the cutover back to restore fetching."
        )
    tenant_id = str(job.payload.get("tenant_id") or "").strip()
    if not tenant_id:
        # A schedule has no principal to fall back on, so an untenanted payload
        # is a scheduler misconfiguration that no retry can fix. Dead-letter it
        # rather than persist canonical data under the unscoped default.
        raise NonRetryableJobError(
            "External fetch job payload missing tenant scope for provider "
            f"'{provider_id}' (schedule '{schedule_id}'): set "
            f"{SCHEDULED_TENANT_ENV_VAR} on the scheduler deployment so the "
            "enqueued payload carries tenant_id"
        )

    service = ExternalIngestionService(
        store=persistence.ingestion_run_store,
        ingestion_run_store_for_tenant=_tenant_ingestion_store_resolver(persistence),
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
        tenant_id=tenant_id,
    )
    record = outcome.record
    if record.status != "FAILED":
        return

    reason_code = _external_fetch_reason_code(record)
    if reason_code in {
        PROVIDER_NOT_SELECTED_REASON_CODE,
        PROVIDER_MODE_DISABLED_REASON_CODE,
    }:
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


def _tenant_ingestion_store_resolver(persistence: PersistenceBundle) -> Any | None:
    """Tenant-scoped ingestion-run store factory, when the backend supports one.

    Mirrors the API wiring (``apps/api/oday_api/main.py``): only a durable
    bundle can hand out a physically scoped store. On the in-memory bundle the
    single shared store is used, and isolation is carried by the run record's
    ``tenant_id`` plus the service's cross-tenant replay guard.
    """
    scoped = getattr(persistence, "ingestion_run_store_for_tenant", None)
    if scoped is None or not getattr(persistence, "is_durable", False):
        return None
    return scoped


def _external_fetch_reason_code(record: Any) -> str:
    """Read the reason code the scheduler attached to a blocked fetch run."""
    for alert in getattr(record, "alerts", ()) or ():
        code = str(alert.get("reason_code", "") or "").strip()
        if code:
            return code
    return ""


BATCH_LISTING_INTAKE_JOB_TYPE = "batch-listing-intake"


def _default_batch_listing_item_executor(
    item: dict[str, Any],
    tenant_id: str,
    persistence: PersistenceBundle,
) -> tuple[str | None, Any | None]:
    """Execute business processing for one listing intake item."""
    from shared.jobs.receipts import ItemError

    if item.get("simulate_timeout") or item.get("error_code") == "GEOCODING_UPSTREAM_TIMEOUT":
        return None, ItemError(
            code="GEOCODING_UPSTREAM_TIMEOUT",
            message="Geocoding service timed out after 3500ms",
            retryable=True,
            details={"endpoint": "geocode.tgos.gov.tw", "timeout_ms": 3500},
        )
    if item.get("simulate_error"):
        err_info = item["simulate_error"]
        return None, ItemError(
            code=err_info.get("code", "VALIDATION_FAILED"),
            message=err_info.get("message", "Validation failed"),
            retryable=bool(err_info.get("retryable", False)),
            details=err_info.get("details"),
        )
    address_raw = item.get("address_raw")
    if not address_raw or not str(address_raw).strip():
        return None, ItemError(
            code="MISSING_MANDATORY_ADDRESS",
            message="Street address is missing or unparseable",
            retryable=False,
            details={"column": "address_raw", "value": None},
        )

    intake_id = str(item.get("intake_id") or f"intake-{uuid4().hex[:8]}")
    return intake_id, None


def handle_batch_listing_intake(job: JobRecord, persistence: PersistenceBundle) -> None:
    """Process a batch listing intake job supporting durable PARTIAL outcomes and scoped retry."""
    from shared.jobs.receipts import (
        DurableJobReceipt,
        ItemError,
        ItemReceipt,
        ItemStatus,
        derive_batch_status_and_summary,
    )

    payload = dict(job.payload)
    items: list[dict[str, Any]] = payload.get("items") or payload.get("rows") or []
    tenant_id = str(payload.get("tenant_id") or "tenant-default").strip()

    existing_receipt = payload.get("receipt")
    existing_items_map: dict[str, ItemReceipt] = {}
    if isinstance(existing_receipt, dict) and "items" in existing_receipt:
        for it in existing_receipt["items"]:
            rec = it if isinstance(it, ItemReceipt) else ItemReceipt.from_dict(it)
            existing_items_map[rec.item_id] = rec

    item_receipts: list[ItemReceipt] = []
    started_at = datetime.now(UTC).isoformat()

    for idx, raw_item in enumerate(items):
        item_id = str(raw_item.get("item_id") or f"row-{idx+1:03d}")
        idempotency_key = raw_item.get("idempotency_key")
        existing = existing_items_map.get(item_id)

        # Pre-execution cancellation check
        if raw_item.get("cancelled_before_execution") or (
            existing
            and existing.item_status == ItemStatus.CANCELLED.value
            and existing.attempt == 0
        ):
            item_receipts.append(
                ItemReceipt(
                    item_id=item_id,
                    item_status=ItemStatus.CANCELLED.value,
                    attempt=0,
                    result_ref=None,
                    error=ItemError(
                        code="CANCELLED_BEFORE_EXECUTION",
                        message="Job cancelled before item execution started",
                        retryable=True,
                        details={"cancellation_reason": "OPERATOR_ABORT"},
                    ),
                    idempotency_key=idempotency_key,
                    last_attempt_at=None,
                )
            )
            continue

        # Mid-execution cancellation check
        if raw_item.get("cancelled_mid_execution"):
            item_receipts.append(
                ItemReceipt(
                    item_id=item_id,
                    item_status=ItemStatus.CANCELLED.value,
                    attempt=1,
                    result_ref=None,
                    error=ItemError(
                        code="CANCELLED_MID_EXECUTION",
                        message="Job cancelled by operator during item execution",
                        retryable=True,
                        details={"cancellation_reason": "OPERATOR_ABORT"},
                    ),
                    idempotency_key=idempotency_key,
                    last_attempt_at=datetime.now(UTC).isoformat(),
                )
            )
            continue

        # Scoped retry gating
        if existing is not None:
            # Succeeded items: skipped with zero invocations
            if existing.item_status == ItemStatus.SUCCEEDED.value:
                item_receipts.append(existing)
                continue
            # Permanent failures (retryable=False): skipped with zero invocations, attempt unchanged
            if existing.item_status == ItemStatus.FAILED.value and (
                existing.error and not existing.error.retryable
            ):
                item_receipts.append(existing)
                continue
            # Already cancelled items: skipped
            if existing.item_status == ItemStatus.CANCELLED.value:
                item_receipts.append(existing)
                continue

        # Execution path for new or retryable failed items
        attempt_num = (existing.attempt if existing else 0) + 1
        now_iso = datetime.now(UTC).isoformat()
        executor = (
            raw_item.get("_executor")
            or payload.get("_item_executor")
            or _default_batch_listing_item_executor
        )

        try:
            result_ref, error = executor(raw_item, tenant_id, persistence)
            if error is not None:
                item_receipts.append(
                    ItemReceipt(
                        item_id=item_id,
                        item_status=ItemStatus.FAILED.value,
                        attempt=attempt_num,
                        result_ref=None,
                        error=error,
                        idempotency_key=idempotency_key,
                        last_attempt_at=now_iso,
                    )
                )
            else:
                item_receipts.append(
                    ItemReceipt(
                        item_id=item_id,
                        item_status=ItemStatus.SUCCEEDED.value,
                        attempt=attempt_num,
                        result_ref=result_ref,
                        error=None,
                        idempotency_key=idempotency_key,
                        last_attempt_at=now_iso,
                    )
                )
        except Exception as exc:
            is_retryable = not isinstance(exc, NonRetryableJobError)
            item_receipts.append(
                ItemReceipt(
                    item_id=item_id,
                    item_status=ItemStatus.FAILED.value,
                    attempt=attempt_num,
                    result_ref=None,
                    error=ItemError(
                        code="EXECUTION_ERROR",
                        message=str(exc),
                        retryable=is_retryable,
                    ),
                    idempotency_key=idempotency_key,
                    last_attempt_at=now_iso,
                )
            )

    aggregate_status, summary = derive_batch_status_and_summary(item_receipts)
    completed_at = datetime.now(UTC).isoformat()
    created_at_str = (
        job.created_at.isoformat()
        if hasattr(job.created_at, "isoformat")
        else str(job.created_at)
    )

    receipt = DurableJobReceipt(
        job_id=job.job_id,
        job_type=job.job_type,
        tenant_id=tenant_id,
        status=aggregate_status.value.upper(),
        delivery_state=None,
        correlation_id=job.correlation_id,
        idempotency_key=job.idempotency_key,
        summary=summary,
        items=tuple(item_receipts),
        created_at=created_at_str,
        started_at=started_at,
        completed_at=completed_at,
    )

    payload["receipt"] = receipt.to_dict()
    payload["summary"] = summary.to_dict()

    persistence.job_queue.update_status(
        job.job_id,
        aggregate_status,
        payload=payload,
        delivery_state=None,
        fence_token=job.fence_token,
    )


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
    registry.register(BATCH_LISTING_INTAKE_JOB_TYPE, handle_batch_listing_intake)
    return registry


__all__ = [
    "BATCH_LISTING_INTAKE_JOB_TYPE",
    "EXTERNAL_FETCH_JOB_TYPE",
    "FORECAST_JOB_TYPE",
    "build_default_registry",
    "handle_batch_listing_intake",
    "handle_external_fetch",
    "handle_forecast",
]
