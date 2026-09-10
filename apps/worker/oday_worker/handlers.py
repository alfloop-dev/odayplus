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
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from shared.jobs.queue import JobRecord, JobStatus, NonRetryableJobError
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

# Batch-imported rows are operator-supplied, so they carry their own source
# identity rather than a retrieval provider's. Nothing in the corpus shares it,
# which is why a batch row can never take the identity-match branch.
BATCH_LISTING_INTAKE_SOURCE_ID = "SRC-BATCH-IMPORT"

# A receipt checkpoint races only with this job's own lease heartbeat, which
# bumps the row version without taking the job away. Two re-reads absorb that;
# anything still rejecting is a real ownership loss.
_CHECKPOINT_WRITE_ATTEMPTS = 3


def batch_listing_intake_id(tenant_id: str, job_id: str, item_id: str) -> str:
    """Stable intake id for one member of one batch job.

    Derived from the tenant, the job and the item -- never from an id the
    submitter puts in the payload. Two consequences the durable contract needs:

    * Two tenants submitting the same row address two different records, so one
      tenant's import cannot overwrite the other's.
    * A replay after a crash between the business write and the receipt
      checkpoint re-addresses the record the interrupted attempt wrote, so the
      row exists exactly once however many attempts it takes.
    """

    digest = sha256(
        "\x1f".join(("batch-listing-intake:v1", tenant_id, job_id, item_id)).encode("utf-8")
    ).hexdigest()
    return f"IN-BATCH-{digest[:16].upper()}"


def build_batch_listing_intake_service(tenant_id: str, persistence: PersistenceBundle) -> Any:
    """The existing assisted-intake application service, bound to this run.

    Built once per job rather than per item: it loads the intake and listing
    corpora on construction, and every item of a batch matches against the same
    corpus.
    """
    from modules.opsboard.application.network_listings import (
        InMemoryAssistedIntakeRepository,
        NetworkListingService,
    )

    intake_repo = getattr(persistence, "operator_intake_repository", None)
    if intake_repo is None:
        intake_repo = InMemoryAssistedIntakeRepository()

    # Prefer this tenant's own listing corpus, the same way the operator API
    # resolves it: the unscoped repository would let a batch row be told it
    # duplicates a listing belonging to somebody else. Fall back to the plain
    # repository when the deployment has no scoped view, rather than silently
    # matching against nothing.
    listing_repository = None
    scoped = getattr(persistence, "listing_repository_for_tenant", None)
    if scoped is not None:
        listing_repository = scoped(tenant_id)
    if listing_repository is None:
        listing_repository = getattr(persistence, "listing_repository", None)

    return NetworkListingService(
        listing_repository=listing_repository,
        intake_repository=intake_repo,
        seed_fixtures=False,
        tenant_id=tenant_id,
    )


def _default_batch_listing_item_executor(
    item: dict[str, Any],
    tenant_id: str,
    persistence: PersistenceBundle,
    *,
    job_id: str,
    item_id: str,
    correlation_id: str | None = None,
    service: Any | None = None,
    actor_name: str | None = None,
) -> tuple[str | None, Any | None]:
    """Land one batch listing row through the assisted-intake business path.

    Returns ``(result_ref, None)`` on success, where ``result_ref`` is the id of
    a durable intake record that can be read back after a restart, or
    ``(None, ItemError)`` when the row itself is the problem.
    """
    from modules.opsboard.application.network_listings import NetworkListingPolicyError
    from shared.jobs.receipts import ItemError

    address_raw = item.get("address_raw") or item.get("addressRaw") or item.get("address")
    if not address_raw or not str(address_raw).strip():
        return None, ItemError(
            code="MISSING_MANDATORY_ADDRESS",
            message="Street address is missing or unparseable",
            retryable=False,
            details={"column": "address_raw", "value": None},
        )

    if service is None:
        service = build_batch_listing_intake_service(tenant_id, persistence)

    try:
        intake = service.record_batch_assisted_entry(
            intake_id=batch_listing_intake_id(tenant_id, job_id, item_id),
            tenant_id=tenant_id,
            row=item,
            source_id=BATCH_LISTING_INTAKE_SOURCE_ID,
            correlation_id=correlation_id,
            idempotency_key=item.get("idempotency_key"),
            actor_name=actor_name,
        )
    except NetworkListingPolicyError as exc:
        return None, ItemError(
            code="INTAKE_SCOPE_REJECTED",
            message=str(exc),
            retryable=False,
        )
    return str(intake["id"]), None



def checkpoint_batch_item_result(
    job: JobRecord, persistence: PersistenceBundle, result: Any
) -> tuple[list[Any], bool]:
    """Deliver one result through the persisted job/item/attempt and lease fence.

    The attempt-start receipt is the authority. Every CAS retry reloads it so
    neither duplicate results nor another item's checkpoint can be overwritten
    from a worker's old in-memory receipt.
    """
    import logging
    from dataclasses import replace

    from shared.jobs.queue import JobFenceRejectedError
    from shared.jobs.receipts import (
        DurableJobReceipt,
        apply_item_result,
        derive_batch_status_and_summary,
    )

    def discarded(reason: str) -> None:
        logging.getLogger(__name__).warning(
            "Batch item result discarded: job=%s item=%s attempt=%s reason=%s",
            job.job_id, result.item_id, result.attempt, reason,
        )

    for _ in range(_CHECKPOINT_WRITE_ATTEMPTS):
        latest = persistence.job_queue.get(job.job_id)
        if (
            latest is None
            or latest.status != JobStatus.RUNNING
            or latest.fence_token != job.fence_token
            or latest.job_type != job.job_type
            or latest.payload.get("tenant_id") != job.payload.get("tenant_id")
        ):
            discarded("execution ownership changed")
            raise JobFenceRejectedError("Batch result no longer owns a RUNNING job lease")
        raw_receipt = latest.payload.get("receipt")
        if not isinstance(raw_receipt, dict):
            discarded("attempt checkpoint missing")
            raise JobFenceRejectedError("Batch result has no persisted attempt checkpoint")
        receipt = DurableJobReceipt.from_dict(raw_receipt)
        current = list(receipt.items)
        existing = next((item for item in current if item.item_id == result.item_id), None)
        if existing is None or existing.attempt != result.attempt:
            discarded("item attempt mismatch")
            return current, False
        # Timestamps and item keys belong to the persisted attempt, not to an
        # incoming result that can be duplicated or delayed.
        trusted_result = replace(
            result, last_attempt_at=existing.last_attempt_at,
            idempotency_key=existing.idempotency_key,
        )
        updated, applied = apply_item_result(current, trusted_result)
        if not applied:
            discarded("duplicate or settled item")
            return current, False
        _, summary = derive_batch_status_and_summary(updated)
        payload = dict(latest.payload)
        payload["receipt"] = replace(
            receipt, items=tuple(updated), summary=summary,
            status=JobStatus.RUNNING.value.upper(), completed_at=None,
        ).to_dict()
        payload["summary"] = summary.to_dict()
        try:
            persistence.job_queue.update_status(
                job.job_id, JobStatus.RUNNING, payload=payload,
                expected_version=latest.version, fence_token=job.fence_token,
            )
        except (JobFenceRejectedError, ValueError):
            continue
        return updated, True
    discarded("checkpoint CAS retries exhausted")
    raise JobFenceRejectedError("Batch result checkpoint did not persist")


def handle_batch_listing_intake(job: JobRecord, persistence: PersistenceBundle) -> None:
    """Process a batch listing intake job with durable per-item receipts.

    Every item goes through three durable writes rather than one write at the
    end: an attempt-start checkpoint, the business write, then a result
    checkpoint. That ordering is what makes a crash recoverable. A replay can
    see that an attempt had already started, and the business write it may be
    repeating is addressed by :func:`batch_listing_intake_id`, so repeating it
    converges on the record the interrupted attempt wrote instead of creating a
    second one.
    """
    from shared.infrastructure.persistence.job_queue import JobFenceRejectedError
    from shared.jobs.receipts import (
        DurableJobReceipt,
        ItemError,
        ItemReceipt,
        ItemStatus,
        derive_batch_status_and_summary,
    )

    payload = dict(job.payload)
    items: list[dict[str, Any]] = payload.get("items") or payload.get("rows") or []
    tenant_id = str(payload.get("tenant_id") or "").strip()
    if not tenant_id:
        raise NonRetryableJobError("Batch listing intake job payload missing authenticated tenant scope")

    seen_ids: set[str] = set()
    for idx, raw_item in enumerate(items):
        item_id = str(raw_item.get("item_id") or f"row-{idx+1:03d}").strip()
        if not item_id or item_id in seen_ids:
            raise NonRetryableJobError(
                f"Batch listing intake contains duplicate item_id '{item_id}'"
            )
        seen_ids.add(item_id)

    started_at = payload.get("started_at") or datetime.now(UTC).isoformat()
    payload["started_at"] = started_at
    created_at_str = (
        job.created_at.isoformat()
        if hasattr(job.created_at, "isoformat")
        else str(job.created_at)
    )

    def _cancelled_item(
        existing: ItemReceipt | None,
        item_id: str,
        idempotency_key: Any,
    ) -> ItemReceipt:
        """Settle a member the job never got a result for.

        ``attempt`` distinguishes the two cases the contract separates: a member
        cancelled before it ever ran keeps ``attempt == 0``, while one whose
        attempt-start checkpoint had already landed keeps ``attempt >= 1``.
        """
        attempt = existing.attempt if existing else 0
        started = attempt >= 1
        return ItemReceipt(
            item_id=item_id,
            item_status=ItemStatus.CANCELLED.value,
            attempt=attempt,
            result_ref=None,
            error=ItemError(
                code="CANCELLED_MID_EXECUTION" if started else "CANCELLED_BEFORE_EXECUTION",
                message=(
                    "Job cancelled after the item attempt started"
                    if started
                    else "Job cancelled before item execution started"
                ),
                retryable=True,
                details={"cancellation_reason": "OPERATOR_ABORT"},
            ),
            idempotency_key=idempotency_key,
            last_attempt_at=existing.last_attempt_at if existing else None,
        )

    def _write_receipt(
        receipt_items: list[ItemReceipt],
        job_status: JobStatus,
        *,
        completed_at: str | None = None,
        require_running: bool = False,
    ) -> None:
        """Persist the receipt derived from ``receipt_items``.

        A rejected write is not swallowed. It means this worker no longer owns
        the job, and executing further items would produce results nobody owns.
        The lease heartbeat also bumps this row's version while we legitimately
        hold it, so a version-only rejection is re-read and retried; a moved
        fence token or a job that left ``RUNNING`` is checked explicitly and
        raises immediately.
        """
        _, receipt_summary = derive_batch_status_and_summary(receipt_items)
        receipt = DurableJobReceipt(
            job_id=job.job_id,
            job_type=job.job_type,
            tenant_id=tenant_id,
            status=job_status.value.upper(),
            summary=receipt_summary,
            items=tuple(receipt_items),
            created_at=created_at_str,
            started_at=started_at,
            completed_at=completed_at,
            correlation_id=job.correlation_id,
            idempotency_key=job.idempotency_key,
        )
        payload["receipt"] = receipt.to_dict()
        payload["summary"] = receipt_summary.to_dict()

        rejection: Exception | None = None
        for _ in range(_CHECKPOINT_WRITE_ATTEMPTS):
            latest = persistence.job_queue.get(job.job_id)
            if latest is None:
                raise JobFenceRejectedError(f"Job {job.job_id} not found in queue")
            if (
                latest.fence_token is not None
                and job.fence_token is not None
                and latest.fence_token != job.fence_token
            ):
                raise JobFenceRejectedError(
                    f"Job fence token moved: expected {job.fence_token}, "
                    f"got {latest.fence_token}"
                )
            if latest.status != JobStatus.RUNNING and require_running:
                raise JobFenceRejectedError(
                    f"Job {job.job_id} left RUNNING mid-batch "
                    f"(now {latest.status.value})"
                )
            try:
                persistence.job_queue.update_status(
                    job.job_id,
                    job_status,
                    payload=payload,
                    delivery_state=None,
                    expected_version=latest.version,
                    fence_token=job.fence_token,
                )
            except (JobFenceRejectedError, ValueError) as exc:
                rejection = exc
                continue
            return
        if rejection is not None:
            raise rejection
        raise JobFenceRejectedError(
            f"Job {job.job_id} receipt checkpoint did not persist"
        )

    def _settle_if_cancelled() -> bool:
        # Cancellation is settled atomically by the queue from its persisted
        # checkpoint. A late worker must never re-settle it from local results.
        latest = persistence.job_queue.get(job.job_id)
        if latest is None:
            raise JobFenceRejectedError(f"Job {job.job_id} not found in queue")
        return latest.status in (
            JobStatus.CANCELLED, JobStatus.SUCCEEDED, JobStatus.PARTIAL, JobStatus.FAILED,
        )

    existing_receipt = payload.get("receipt")
    current_items: list[ItemReceipt] = []
    if isinstance(existing_receipt, dict) and "items" in existing_receipt:
        for it in existing_receipt["items"]:
            rec = it if isinstance(it, ItemReceipt) else ItemReceipt.from_dict(it)
            current_items.append(rec)
    else:
        for idx, raw_item in enumerate(items):
            item_id = str(raw_item.get("item_id") or f"row-{idx+1:03d}")
            current_items.append(
                ItemReceipt(
                    item_id=item_id,
                    item_status=ItemStatus.PENDING.value,
                    attempt=0,
                    result_ref=None,
                    error=None,
                    idempotency_key=raw_item.get("idempotency_key"),
                    last_attempt_at=None,
                )
            )

    # One service per job, not per item: it loads the intake and listing corpora
    # on construction and every member matches against the same corpus.
    service = build_batch_listing_intake_service(tenant_id, persistence)

    for idx, raw_item in enumerate(items):
        item_id = str(raw_item.get("item_id") or f"row-{idx+1:03d}")
        idempotency_key = raw_item.get("idempotency_key")

        # 1. Read the live job row: cancellation and fencing are decided from
        # persisted state, never from a flag carried in the payload.
        latest_job = persistence.job_queue.get(job.job_id)
        if latest_job is None:
            raise JobFenceRejectedError(f"Job {job.job_id} not found in queue")

        if latest_job.status in (JobStatus.CANCELLED, JobStatus.FAILED, JobStatus.SUCCEEDED, JobStatus.PARTIAL):
            _settle_if_cancelled()
            return

        if latest_job.fence_token is not None and job.fence_token is not None:
            if latest_job.fence_token != job.fence_token:
                raise JobFenceRejectedError(
                    f"Job fence token moved: expected {job.fence_token}, got {latest_job.fence_token}"
                )

        existing = next((it for it in current_items if it.item_id == item_id), None)

        # Scoped retry gating: a retry pass re-runs only retryable failures.
        # Succeeded, permanently failed and cancelled members are left as they
        # are, so a retry can never duplicate work that already landed.
        if existing is not None and existing.item_status != ItemStatus.PENDING.value:
            if existing.item_status == ItemStatus.SUCCEEDED.value:
                continue
            if existing.item_status == ItemStatus.FAILED.value and existing.error and not existing.error.retryable:
                continue
            if existing.item_status == ItemStatus.CANCELLED.value:
                continue

        attempt_num = (existing.attempt if existing and existing.attempt > 0 else 0) + 1
        now_iso = datetime.now(UTC).isoformat()

        # 2. Checkpoint that this attempt started, before the business write.
        # Without it, a crash mid-item is indistinguishable from a member that
        # never ran, and a cancellation cannot tell attempt 0 from attempt >= 1.
        candidate_items = [
            ItemReceipt(
                item_id=item_id,
                item_status=ItemStatus.PENDING.value,
                attempt=attempt_num,
                result_ref=None,
                error=None,
                idempotency_key=idempotency_key,
                last_attempt_at=now_iso,
            )
            if it.item_id == item_id
            else it
            for it in current_items
        ]
        try:
            _write_receipt(candidate_items, JobStatus.RUNNING, require_running=True)
        except JobFenceRejectedError:
            if _settle_if_cancelled():
                return
            raise
        current_items = candidate_items

        try:
            actor_name = payload.get("actor_name") or payload.get("submitter") or "批次匯入"
            result_ref, error = _default_batch_listing_item_executor(
                raw_item,
                tenant_id,
                persistence,
                job_id=job.job_id,
                item_id=item_id,
                correlation_id=job.correlation_id,
                service=service,
                actor_name=actor_name,
            )
            if error is not None:
                item_result = ItemReceipt(
                    item_id=item_id,
                    item_status=ItemStatus.FAILED.value,
                    attempt=attempt_num,
                    result_ref=None,
                    error=error,
                    idempotency_key=idempotency_key,
                    last_attempt_at=now_iso,
                )
            else:
                item_result = ItemReceipt(
                    item_id=item_id,
                    item_status=ItemStatus.SUCCEEDED.value,
                    attempt=attempt_num,
                    result_ref=result_ref,
                    error=None,
                    idempotency_key=idempotency_key,
                    last_attempt_at=now_iso,
                )
        except JobFenceRejectedError:
            raise
        except Exception as exc:
            is_retryable = not isinstance(exc, NonRetryableJobError)
            item_result = ItemReceipt(
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

        # Deliver through the same durable result boundary used by retries
        # and delayed/duplicate result deliveries.
        try:
            current_items, _ = checkpoint_batch_item_result(job, persistence, item_result)
        except JobFenceRejectedError:
            if _settle_if_cancelled():
                return
            raise

    # Terminal settlement: anything still unresolved was never given a result.
    current_items = [
        _cancelled_item(it, it.item_id, it.idempotency_key)
        if it.item_status == ItemStatus.PENDING.value
        else it
        for it in current_items
    ]

    aggregate_status, _ = derive_batch_status_and_summary(current_items)
    try:
        _write_receipt(
            current_items,
            aggregate_status,
            completed_at=datetime.now(UTC).isoformat(),
            require_running=True,
        )
    except JobFenceRejectedError:
        if _settle_if_cancelled():
            return
        raise


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
