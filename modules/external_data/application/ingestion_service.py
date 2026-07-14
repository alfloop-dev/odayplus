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

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from modules.external_data.application.ingestion_store import (
    IngestionRunRecord,
    InMemoryIngestionRunStore,
    build_ingestion_run_record,
)
from modules.external_data.providers import ListingPartnerFeedProvider
from modules.external_data.workers.scheduled_fetch import (
    ExternalFetchJobSpec,
    ExternalFetchResiliencePolicy,
    ExternalFetchScheduler,
    InMemoryExternalFetchStateStore,
)
from shared.audit import AuditEvent, InMemoryAuditLog

DEFAULT_FRESHNESS_SLA = timedelta(hours=24)
DEFAULT_INTERVAL = timedelta(hours=1)

ProviderFactory = Callable[[], Any]

_DEFAULT_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
    "listing.partner_feed": ListingPartnerFeedProvider,
}


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
        audit_log: Any | None = None,
        provider_factories: dict[str, ProviderFactory] | None = None,
        resilience_policy: ExternalFetchResiliencePolicy | None = None,
        freshness_sla: timedelta = DEFAULT_FRESHNESS_SLA,
        default_interval: timedelta = DEFAULT_INTERVAL,
    ) -> None:
        self.store = store or InMemoryIngestionRunStore()
        self.audit_log = audit_log or InMemoryAuditLog()
        self.freshness_sla = freshness_sla
        self.default_interval = default_interval
        self._captures: dict[str, Any] = {}

        base_factories = dict(provider_factories or _DEFAULT_PROVIDER_FACTORIES)
        wrapped = {
            provider_id: self._wrap_factory(provider_id, factory)
            for provider_id, factory in base_factories.items()
        }
        self.scheduler = ExternalFetchScheduler(
            state_store=InMemoryExternalFetchStateStore(),
            provider_factories=wrapped,
            resilience_policy=resilience_policy,
        )
        self._rehydrate()

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
    ) -> IngestionOutcome:
        sla = freshness_sla or self.freshness_sla

        # Route-level idempotency: an ``Idempotency-Key`` replay never re-runs
        # the provider and is recorded as ``idempotent_replay``.
        if api_idempotency_key:
            existing = self.store.get_by_api_key(api_idempotency_key)
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
        self._captures.pop(provider_id, None)
        run = self.scheduler.run_once(
            spec,
            scheduled_at=scheduled_at,
            window_start=window_start,
            window_end=window_end,
            correlation_id=correlation_id,
        )

        # Window idempotency: the scheduler already deduped this window, so the
        # persisted run for that window is the authoritative replay target.
        existing = self.store.get_by_window_key(run.idempotency_key)
        if existing is not None:
            if api_idempotency_key:
                self.store.link_api_key(api_idempotency_key, existing.run_id)
            return self._replay(
                existing,
                trigger=trigger,
                actor=actor,
                correlation_id=correlation_id or run.correlation_id,
                api_idempotency_key=api_idempotency_key,
            )

        record = build_ingestion_run_record(
            run=run,
            ingestion_result=self._captures.get(provider_id),
            freshness_sla=sla,
            trigger=trigger,
            api_idempotency_key=api_idempotency_key,
        )
        saved = self.store.save(record)
        audit = self._record_audit(saved, created=True, actor=actor, correlation_id=run.correlation_id)
        return IngestionOutcome(record=saved, created=True, audit_event_id=audit.event_id)

    def run_scheduled(
        self,
        spec: ExternalFetchJobSpec,
        *,
        scheduled_at: datetime | None = None,
        actor: str = "scheduler",
        correlation_id: str | None = None,
    ) -> IngestionOutcome:
        """Scheduled entry point: persists exactly like the manual path."""

        return self.ingest(
            provider_id=spec.provider_id,
            schedule_id=spec.schedule_id,
            trigger="scheduled",
            actor=actor,
            interval=spec.interval,
            freshness_sla=spec.freshness_sla,
            scheduled_at=scheduled_at,
            correlation_id=correlation_id,
        )

    # -- internals --------------------------------------------------------

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
        """Re-seed scheduler watermark/idempotency from persisted runs."""

        for record in self.store.list_runs():
            self.scheduler.state_store.save_run(record.to_external_fetch_run())


__all__ = [
    "DEFAULT_FRESHNESS_SLA",
    "DEFAULT_INTERVAL",
    "ExternalIngestionService",
    "IngestionOutcome",
]
