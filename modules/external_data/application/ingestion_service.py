"""Closed-loop external-data ingestion service (ODP-FLOW-001).

This service is the single entry point for both *scheduled* and *manual*
ingestion. It composes the existing pieces rather than replacing them:

- ``ExternalFetchScheduler`` still owns window idempotency, the last-success
  watermark, freshness/staleness classification, retry backoff, and the
  provider circuit breaker.
- The provider (``ListingPartnerFeedProvider``) still owns fetch, canonical
  mapping, DQ/quarantine, and lineage.

What the service adds is the *closed loop*: it captures the provider result,
folds the run into one queryable :class:`IngestionRunRecord`, persists it to a
durable store, emits a ``shared.audit`` event (``accepted`` vs
``idempotent_replay``), and — on construction — rehydrates the scheduler's
watermark/idempotency state from the store so a restarted process rejects
duplicate windows and keeps advancing from the persisted watermark.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from modules.external_data.application.ingestion_store import (
    IngestionRunRecord,
    InMemoryIngestionRunStore,
    build_ingestion_run_record,
)
from modules.external_data.workers.scheduled_fetch import (
    ExternalFetchJobSpec,
    ExternalFetchResiliencePolicy,
    ExternalFetchScheduler,
    TenantScopedExternalFetchStateStore,
    default_external_fetch_provider_factories,
)
from shared.audit import AuditEvent, InMemoryAuditLog

DEFAULT_FRESHNESS_SLA = timedelta(hours=24)
DEFAULT_INTERVAL = timedelta(hours=1)

ProviderFactory = Callable[[], Any]

#: Environment variable naming the tenant every *scheduled* ingest runs as.
SCHEDULED_TENANT_ENV_VAR = "ODP_SCHEDULED_INGESTION_TENANT_ID"


class ScheduledIngestionTenantError(RuntimeError):
    """Fail-closed: a scheduled ingest arrived without a tenant scope.

    The manual/API path resolves its tenant from a verified principal. The
    scheduled path has no principal, so an empty ``tenant_id`` here means the
    deployment never configured one — persisting the run under the unscoped
    default would silently mix a real tenant's canonical data into the shared
    store.
    """

    code = "scheduled_ingestion_tenant_missing"


class CrossTenantIngestionRunError(RuntimeError):
    """Fail-closed: a persisted run belonging to another tenant was reachable.

    Window and API idempotency keys are not tenant-qualified, so on a store
    shared by more than one tenant a replay lookup can surface a foreign run.
    Returning it would leak another tenant's canonical counts, lineage, and
    snapshot ids, so the lookup is refused instead.
    """

    code = "cross_tenant_ingestion_run"

    def __init__(self, *, expected_tenant_id: str, record_tenant_id: str, run_id: str) -> None:
        self.expected_tenant_id = expected_tenant_id
        self.record_tenant_id = record_tenant_id
        self.run_id = run_id
        super().__init__(
            "Ingestion run belongs to a different tenant "
            f"(run_id={run_id}, record_tenant_id={record_tenant_id or '<unscoped>'}, "
            f"requested_tenant_id={expected_tenant_id or '<unscoped>'}, code={self.code})"
        )


def _clean_tenant_id(tenant_id: Any) -> str:
    return str(tenant_id).strip() if tenant_id else ""


@dataclass(frozen=True)
class IngestionOutcome:
    """Result of one ingestion call: the persisted run plus replay/audit info."""

    record: IngestionRunRecord
    created: bool
    audit_event_id: str


class ExternalIngestionService:
    def __init__(
        self,
        *,
        store: Any | None = None,
        ingestion_run_store_for_tenant: Any | None = None,
        state_store: Any | None = None,
        audit_log: Any | None = None,
        provider_factories: dict[str, ProviderFactory] | None = None,
        resilience_policy: ExternalFetchResiliencePolicy | None = None,
        freshness_sla: timedelta = DEFAULT_FRESHNESS_SLA,
        default_interval: timedelta = DEFAULT_INTERVAL,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store or InMemoryIngestionRunStore()
        self.ingestion_run_store_for_tenant = ingestion_run_store_for_tenant
        self.audit_log = audit_log or InMemoryAuditLog()
        self.freshness_sla = freshness_sla
        self.default_interval = default_interval
        self.env = os.environ if env is None else env
        self.provider_factories = (
            dict(provider_factories) if provider_factories is not None else None
        )
        self.resilience_policy = resilience_policy
        self.initial_state_store = state_store

        self._tenant_schedulers: dict[str, ExternalFetchScheduler] = {}
        self._tenant_captures: dict[str, dict[str, Any]] = {}
        if self.ingestion_run_store_for_tenant is None:
            self._get_scheduler_and_captures("")

    def _resolve_store(self, tenant_id: str = "") -> Any:
        clean_tid = _clean_tenant_id(tenant_id)
        if clean_tid and self.ingestion_run_store_for_tenant is not None:
            scoped = self.ingestion_run_store_for_tenant(clean_tid)
            if scoped is None:
                raise RuntimeError(f"Tenant store factory returned None for tenant '{clean_tid}'")
            return scoped
        return self.store

    @staticmethod
    def _seed_scheduler_state(
        scheduler: ExternalFetchScheduler, target_store: Any, clean_tid: str
    ) -> None:
        """Re-seed watermark/idempotency from *this tenant's* persisted runs only.

        A store shared by several tenants would otherwise hand tenant B the
        watermark and window dedupe of tenant A, which is cross-tenant run
        visibility through the back door.
        """
        if not hasattr(target_store, "list_runs"):
            return
        for record in target_store.list_runs():
            if _clean_tenant_id(getattr(record, "tenant_id", "")) != clean_tid:
                continue
            scheduler.state_store.save_run(record.to_external_fetch_run())

    def _get_scheduler_and_captures(
        self, tenant_id: str = "", target_store: Any | None = None
    ) -> tuple[ExternalFetchScheduler, dict[str, Any]]:
        clean_tid = _clean_tenant_id(tenant_id)
        if target_store is None:
            target_store = self._resolve_store(clean_tid)

        if clean_tid in self._tenant_schedulers:
            scheduler = self._tenant_schedulers[clean_tid]
            captures = self._tenant_captures[clean_tid]
            self._seed_scheduler_state(scheduler, target_store, clean_tid)
            return scheduler, captures

        if target_store is None:
            target_store = self._resolve_store(clean_tid)
        captures: dict[str, Any] = {}

        base_factories = dict(
            default_external_fetch_provider_factories(self.env)
            if self.provider_factories is None
            else self.provider_factories
        )

        def _wrap_factory(provider_id: str, factory: ProviderFactory) -> ProviderFactory:
            def make() -> Any:
                inner = factory()

                class _CapturingProvider:
                    def fetch_and_ingest(self, **kwargs: Any) -> Any:
                        result = inner.fetch_and_ingest(**kwargs)
                        captures[provider_id] = result
                        return result

                return _CapturingProvider()

            return make

        wrapped = {
            provider_id: _wrap_factory(provider_id, factory)
            for provider_id, factory in base_factories.items()
        }

        # The deployment provisions one fetch-state backend. Unscoped callers
        # use it directly; a tenant gets a namespaced view of the *same* backend
        # so its watermark and circuit state stay durable without ever being
        # readable as another tenant's.
        if self.initial_state_store is None:
            state_store = None
        elif clean_tid == "":
            state_store = self.initial_state_store
        else:
            state_store = TenantScopedExternalFetchStateStore(
                self.initial_state_store, clean_tid
            )

        scheduler = ExternalFetchScheduler(
            state_store=state_store,
            provider_factories=wrapped,
            resilience_policy=self.resilience_policy,
            env=self.env,
        )

        self._seed_scheduler_state(scheduler, target_store, clean_tid)

        self._tenant_schedulers[clean_tid] = scheduler
        self._tenant_captures[clean_tid] = captures
        return scheduler, captures

    @property
    def scheduler(self) -> ExternalFetchScheduler:
        if "" in self._tenant_schedulers:
            return self._tenant_schedulers[""]
        scheduler, _ = self._get_scheduler_and_captures("")
        return scheduler

    @property
    def _captures(self) -> dict[str, Any]:
        if "" in self._tenant_captures:
            return self._tenant_captures[""]
        _, captures = self._get_scheduler_and_captures("")
        return captures

    # -- public API -------------------------------------------------------

    def ingest(
        self,
        *,
        provider_id: str = "listing.partner_feed",
        schedule_id: str = "manual",
        trigger: str = "manual",
        actor: str = "system",
        interval: timedelta | None = None,
        freshness_sla: timedelta | None = None,
        scheduled_at: datetime | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        correlation_id: str | None = None,
        api_idempotency_key: str | None = None,
        tenant_id: str = "",
    ) -> IngestionOutcome:
        sla = freshness_sla or self.freshness_sla
        clean_tid = _clean_tenant_id(tenant_id)
        target_store = self._resolve_store(clean_tid)

        # Route-level idempotency: an ``Idempotency-Key`` replay never re-runs
        # the provider and is recorded as ``idempotent_replay``.
        if api_idempotency_key:
            existing = self._assert_same_tenant(
                target_store.get_by_api_key(api_idempotency_key), clean_tid
            )
            if existing is not None:
                return self._replay(
                    existing,
                    trigger=trigger,
                    actor=actor,
                    correlation_id=correlation_id or existing.correlation_id,
                    api_idempotency_key=api_idempotency_key,
                )

        spec = ExternalFetchJobSpec(
            provider_id=provider_id,
            schedule_id=schedule_id,
            interval=interval or self.default_interval,
            freshness_sla=sla,
        )
        scheduler, captures = self._get_scheduler_and_captures(clean_tid, target_store=target_store)
        captures.pop(provider_id, None)
        run = scheduler.run_once(
            spec,
            scheduled_at=scheduled_at,
            window_start=window_start,
            window_end=window_end,
            correlation_id=correlation_id,
        )

        # Window idempotency: the scheduler already deduped this window, so the
        # persisted run for that window is the authoritative replay target.
        existing = self._assert_same_tenant(
            target_store.get_by_window_key(run.idempotency_key), clean_tid
        )
        if existing is not None:
            if api_idempotency_key:
                target_store.link_api_key(api_idempotency_key, existing.run_id)
            return self._replay(
                existing,
                trigger=trigger,
                actor=actor,
                correlation_id=correlation_id or run.correlation_id,
                api_idempotency_key=api_idempotency_key,
            )

        record = build_ingestion_run_record(
            run=run,
            ingestion_result=captures.get(provider_id),
            freshness_sla=sla,
            trigger=trigger,
            api_idempotency_key=api_idempotency_key,
            tenant_id=clean_tid,
        )
        saved = target_store.save(record)
        from shared.observability.metrics import record_data_signal

        freshness_h = (
            saved.freshness.freshness_age_seconds / 3600.0
            if (
                saved.freshness
                and getattr(saved.freshness, "freshness_age_seconds", None) is not None
            )
            else 0.0
        )
        q_score = (saved.accepted_count / saved.total_count) if saved.total_count > 0 else 1.0
        record_data_signal(
            source=saved.provider_id,
            view="ingestion_run",
            freshness_hours=max(0.0, float(freshness_h)),
            quality_score=min(1.0, max(0.0, float(q_score))),
            feature_null_rate=0.0,
        )
        audit = self._record_audit(saved, created=True, actor=actor, correlation_id=run.correlation_id)
        return IngestionOutcome(record=saved, created=True, audit_event_id=audit.event_id)

    def run_scheduled(
        self,
        spec: ExternalFetchJobSpec,
        *,
        scheduled_at: datetime | None = None,
        actor: str = "scheduler",
        correlation_id: str | None = None,
        tenant_id: str = "",
    ) -> IngestionOutcome:
        """Scheduled entry point: persists exactly like the manual path.

        ``tenant_id`` is mandatory here even though :meth:`ingest` tolerates an
        empty one for the legacy unscoped in-process store: a schedule carries
        no principal to fall back on, so an empty tenant is a deployment
        misconfiguration, not a default.
        """

        clean_tid = _clean_tenant_id(tenant_id)
        if not clean_tid:
            raise ScheduledIngestionTenantError(
                "Scheduled ingestion requires an explicit tenant scope for "
                f"provider '{spec.provider_id}' (schedule '{spec.schedule_id}'). "
                f"Set {SCHEDULED_TENANT_ENV_VAR} on the scheduler deployment, or "
                "pass tenant_id= explicitly; scheduled runs are never persisted "
                f"under the unscoped default (code={ScheduledIngestionTenantError.code})"
            )

        return self.ingest(
            provider_id=spec.provider_id,
            schedule_id=spec.schedule_id,
            trigger="scheduled",
            actor=actor,
            interval=spec.interval,
            freshness_sla=spec.freshness_sla,
            scheduled_at=scheduled_at,
            correlation_id=correlation_id,
            tenant_id=clean_tid,
        )

    # -- internals --------------------------------------------------------

    @staticmethod
    def _assert_same_tenant(
        record: IngestionRunRecord | None, clean_tid: str
    ) -> IngestionRunRecord | None:
        """Refuse a replay lookup that resolved to another tenant's run."""
        if record is None:
            return None
        if _clean_tenant_id(record.tenant_id) != clean_tid:
            raise CrossTenantIngestionRunError(
                expected_tenant_id=clean_tid,
                record_tenant_id=_clean_tenant_id(record.tenant_id),
                run_id=record.run_id,
            )
        return record

    def _replay(
        self,
        record: IngestionRunRecord,
        *,
        trigger: str,
        actor: str,
        correlation_id: str,
        api_idempotency_key: str | None,
    ) -> IngestionOutcome:
        audit = self._record_audit(
            record,
            created=False,
            actor=actor,
            correlation_id=correlation_id,
            api_idempotency_key=api_idempotency_key,
        )
        return IngestionOutcome(record=record, created=False, audit_event_id=audit.event_id)

    def _record_audit(
        self,
        record: IngestionRunRecord,
        *,
        created: bool,
        actor: str,
        correlation_id: str,
        api_idempotency_key: str | None = None,
    ) -> AuditEvent:
        return self.audit_log.record(
            AuditEvent(
                event_type="external_data.ingested.v1",
                actor=actor,
                action="ingest",
                resource=f"external-data/{record.provider_id}",
                outcome="accepted" if created else "idempotent_replay",
                correlation_id=correlation_id,
                job_id=record.run_id,
                metadata={
                    "tenant_id": record.tenant_id,
                    "trigger": record.trigger,
                    "api_idempotency_key": api_idempotency_key or record.api_idempotency_key,
                    "data_status": record.data_status,
                    "status": record.status,
                    "accepted_count": record.accepted_count,
                    "quarantined_count": record.quarantined_count,
                    "created": created,
                },
            )
        )

    def _wrap_factory(self, provider_id: str, factory: ProviderFactory) -> ProviderFactory:
        service = self

        def make() -> Any:
            inner = factory()

            class _CapturingProvider:
                def fetch_and_ingest(self, **kwargs: Any) -> Any:
                    result = inner.fetch_and_ingest(**kwargs)
                    service._captures[provider_id] = result
                    return result

            return _CapturingProvider()

        return make

    def _rehydrate(self) -> None:
        """Re-seed scheduler watermark/idempotency from persisted runs across active tenants."""
        if not self._tenant_schedulers:
            self._get_scheduler_and_captures("")
        for clean_tid, scheduler in list(self._tenant_schedulers.items()):
            self._seed_scheduler_state(scheduler, self._resolve_store(clean_tid), clean_tid)


__all__ = [
    "DEFAULT_FRESHNESS_SLA",
    "DEFAULT_INTERVAL",
    "SCHEDULED_TENANT_ENV_VAR",
    "CrossTenantIngestionRunError",
    "ExternalIngestionService",
    "IngestionOutcome",
    "ScheduledIngestionTenantError",
]
