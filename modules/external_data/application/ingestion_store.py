"""Queryable, persistable run-state for the external-data ingestion loop.

The scheduled/manual fetch path (``modules.external_data.workers.scheduled_fetch``)
produces an :class:`ExternalFetchRun` plus a rich provider
:class:`ListingFeedIngestionResult` (canonical records, DQ quarantine, and the
per-record lineage envelope). Those live only in-process today. This module
turns one run into a single durable, queryable aggregate — an
:class:`IngestionRunRecord` — and defines the in-memory store the API and UI
read from. A drop-in durable (SQLite) twin lives in
``shared.infrastructure.persistence.external_data`` and mirrors this surface
exactly, so the same code path survives a process restart (ODP-FLOW-001:
"scheduled and manual ingestion persist canonical outputs"; "DQ quarantine
lineage and freshness are queryable"; "API and UI read persisted run state").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from modules.external_data.workers.scheduled_fetch import (
    ExternalFetchAlert,
    ExternalFetchRun,
    SourceFreshnessEvidence,
    freshness_evidence_from_run,
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True)
class QuarantineRecord:
    """One DQ-quarantined source record, with its reasons preserved."""

    source_system: str
    source_record_id: str
    canonical_target: str
    quarantine_reasons: tuple[str, ...]
    issues: tuple[dict[str, str | None], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_system": self.source_system,
            "source_record_id": self.source_record_id,
            "canonical_target": self.canonical_target,
            "quarantine_reasons": list(self.quarantine_reasons),
            "issues": [dict(issue) for issue in self.issues],
        }


@dataclass(frozen=True)
class LineageRecord:
    """Provenance envelope for one landed source record (ODP-DATA-07 §2)."""

    contract_id: str
    source_system: str
    source_id: str
    source_record_id: str
    canonical_target: str
    mapping_id: str
    schema_version: str
    accepted: bool
    event_time: datetime | None
    observation_time: datetime | None
    ingestion_time: datetime
    quarantine_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "source_system": self.source_system,
            "source_id": self.source_id,
            "source_record_id": self.source_record_id,
            "canonical_target": self.canonical_target,
            "mapping_id": self.mapping_id,
            "schema_version": self.schema_version,
            "accepted": self.accepted,
            "event_time": _iso(self.event_time),
            "observation_time": _iso(self.observation_time),
            "ingestion_time": _iso(self.ingestion_time),
            "quarantine_reasons": list(self.quarantine_reasons),
        }


@dataclass(frozen=True)
class IngestionRunRecord:
    """A single persisted, queryable ingestion run (canonical output + lineage)."""

    run_id: str
    provider_id: str
    schedule_id: str
    trigger: str
    idempotency_key: str
    status: str
    data_status: str
    window_start: datetime
    window_end: datetime
    started_at: datetime
    completed_at: datetime
    raw_snapshot_id: str
    canonical_snapshot_id: str
    source_snapshot_id: str
    provider_observed_at: datetime | None
    ingested_at: datetime | None
    last_success_watermark_before: datetime | None
    last_success_watermark_after: datetime | None
    correlation_id: str
    accepted_count: int
    quarantined_count: int
    total_count: int
    freshness: SourceFreshnessEvidence
    message: str = ""
    api_idempotency_key: str | None = None
    retry_after: datetime | None = None
    source_snapshot_ids: tuple[str, ...] = ()
    quarantine: tuple[QuarantineRecord, ...] = ()
    lineage: tuple[LineageRecord, ...] = ()
    alerts: tuple[dict[str, Any], ...] = ()
    audit_events: tuple[dict[str, Any], ...] = ()

    def freshness_dict(self) -> dict[str, Any]:
        return self.freshness.to_dict()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "provider_id": self.provider_id,
            "schedule_id": self.schedule_id,
            "trigger": self.trigger,
            "idempotency_key": self.idempotency_key,
            "api_idempotency_key": self.api_idempotency_key,
            "status": self.status,
            "data_status": self.data_status,
            "window_start": _iso(self.window_start),
            "window_end": _iso(self.window_end),
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
            "raw_snapshot_id": self.raw_snapshot_id,
            "canonical_snapshot_id": self.canonical_snapshot_id,
            "source_snapshot_id": self.source_snapshot_id,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "provider_observed_at": _iso(self.provider_observed_at),
            "ingested_at": _iso(self.ingested_at),
            "last_success_watermark_before": _iso(self.last_success_watermark_before),
            "last_success_watermark_after": _iso(self.last_success_watermark_after),
            "correlation_id": self.correlation_id,
            "accepted_count": self.accepted_count,
            "quarantined_count": self.quarantined_count,
            "total_count": self.total_count,
            "retry_after": _iso(self.retry_after),
            "message": self.message,
            "freshness": self.freshness.to_dict(),
            "quarantine": [record.to_dict() for record in self.quarantine],
            "lineage": [record.to_dict() for record in self.lineage],
            "alerts": [dict(alert) for alert in self.alerts],
            "audit_events": [dict(event) for event in self.audit_events],
        }

    def to_external_fetch_run(self) -> ExternalFetchRun:
        """Reconstruct the scheduler run used to re-seed watermark/idempotency.

        Alerts/audit events are not needed to restore idempotency or watermark
        state, so they are dropped here; the fully-detailed copy lives on the
        record and is what the API/UI read.
        """
        return ExternalFetchRun(
            job_id=self.run_id,
            provider_id=self.provider_id,
            schedule_id=self.schedule_id,
            idempotency_key=self.idempotency_key,
            status=self.status,
            data_status=self.data_status,
            window_start=self.window_start,
            window_end=self.window_end,
            started_at=self.started_at,
            completed_at=self.completed_at,
            source_snapshot_ids=self.source_snapshot_ids,
            raw_snapshot_id=self.raw_snapshot_id,
            canonical_snapshot_id=self.canonical_snapshot_id,
            source_snapshot_id=self.source_snapshot_id,
            provider_observed_at=self.provider_observed_at,
            ingested_at=self.ingested_at,
            last_success_watermark_before=self.last_success_watermark_before,
            last_success_watermark_after=self.last_success_watermark_after,
            correlation_id=self.correlation_id,
            message=self.message,
            retry_after=self.retry_after,
        )


def build_ingestion_run_record(
    *,
    run: ExternalFetchRun,
    ingestion_result: Any | None,
    freshness_sla: timedelta,
    trigger: str,
    api_idempotency_key: str | None = None,
) -> IngestionRunRecord:
    """Fold a scheduler run + provider result into one persistable aggregate."""

    quarantine: list[QuarantineRecord] = []
    lineage: list[LineageRecord] = []
    accepted_count = 0
    total_count = 0

    connector_run = None
    if ingestion_result is not None:
        connector_run = getattr(ingestion_result, "connector_run", None)
    if connector_run is not None:
        accepted_count = connector_run.accepted_count
        total_count = connector_run.total
        for record in connector_run.records:
            lin = record.lineage
            lineage.append(
                LineageRecord(
                    contract_id=lin.contract_id,
                    source_system=lin.source_system,
                    source_id=lin.source_id,
                    source_record_id=lin.source_record_id,
                    canonical_target=lin.canonical_target,
                    mapping_id=lin.mapping_id,
                    schema_version=lin.schema_version,
                    accepted=record.accepted,
                    event_time=lin.event_time,
                    observation_time=lin.observation_time,
                    ingestion_time=lin.ingestion_time,
                    quarantine_reasons=tuple(lin.quarantine_reasons),
                )
            )
            if not record.accepted:
                quarantine.append(
                    QuarantineRecord(
                        source_system=lin.source_system,
                        source_record_id=lin.source_record_id,
                        canonical_target=lin.canonical_target,
                        quarantine_reasons=tuple(lin.quarantine_reasons),
                        issues=tuple(
                            {
                                "code": issue.code,
                                "message": issue.message,
                                "field": getattr(issue, "field", None),
                            }
                            for issue in record.issues
                        ),
                    )
                )

    freshness = freshness_evidence_from_run(run, freshness_sla=freshness_sla)

    return IngestionRunRecord(
        run_id=run.job_id,
        provider_id=run.provider_id,
        schedule_id=run.schedule_id,
        trigger=trigger,
        idempotency_key=run.idempotency_key,
        api_idempotency_key=api_idempotency_key,
        status=run.status,
        data_status=run.data_status,
        window_start=run.window_start,
        window_end=run.window_end,
        started_at=run.started_at,
        completed_at=run.completed_at,
        raw_snapshot_id=run.raw_snapshot_id,
        canonical_snapshot_id=run.canonical_snapshot_id,
        source_snapshot_id=run.source_snapshot_id,
        source_snapshot_ids=tuple(run.source_snapshot_ids),
        provider_observed_at=run.provider_observed_at,
        ingested_at=run.ingested_at,
        last_success_watermark_before=run.last_success_watermark_before,
        last_success_watermark_after=run.last_success_watermark_after,
        correlation_id=run.correlation_id,
        accepted_count=accepted_count,
        quarantined_count=len(quarantine),
        total_count=total_count,
        message=run.message,
        retry_after=run.retry_after,
        freshness=freshness,
        quarantine=tuple(quarantine),
        lineage=tuple(lineage),
        alerts=tuple(alert.to_dict() for alert in run.alerts),
        audit_events=tuple(_alert_to_dict(event) for event in run.audit_events),
    )


def _alert_to_dict(event: ExternalFetchAlert) -> dict[str, Any]:
    return event.to_dict()


class InMemoryIngestionRunStore:
    """Process-local store; the durable twin keeps the same public surface."""

    def __init__(self) -> None:
        self._runs: dict[str, IngestionRunRecord] = {}
        self._order: list[str] = []
        self._by_window_key: dict[str, str] = {}
        self._api_index: dict[str, str] = {}
        self._latest_by_provider: dict[str, str] = {}

    def save(self, record: IngestionRunRecord) -> IngestionRunRecord:
        if record.run_id not in self._runs:
            self._order.append(record.run_id)
        self._runs[record.run_id] = record
        self._by_window_key[record.idempotency_key] = record.run_id
        if record.api_idempotency_key:
            self._api_index[record.api_idempotency_key] = record.run_id
        self._latest_by_provider[record.provider_id] = record.run_id
        return record

    def link_api_key(self, api_idempotency_key: str, run_id: str) -> None:
        if api_idempotency_key and run_id in self._runs:
            self._api_index[api_idempotency_key] = run_id

    def get(self, run_id: str) -> IngestionRunRecord | None:
        return self._runs.get(run_id)

    def get_by_window_key(self, idempotency_key: str) -> IngestionRunRecord | None:
        run_id = self._by_window_key.get(idempotency_key)
        return self._runs.get(run_id) if run_id else None

    def get_by_api_key(self, api_idempotency_key: str) -> IngestionRunRecord | None:
        run_id = self._api_index.get(api_idempotency_key)
        return self._runs.get(run_id) if run_id else None

    def list_runs(self, *, provider_id: str | None = None) -> list[IngestionRunRecord]:
        runs = [self._runs[run_id] for run_id in self._order]
        if provider_id is not None:
            runs = [run for run in runs if run.provider_id == provider_id]
        return runs

    def latest_per_provider(self) -> list[IngestionRunRecord]:
        return [self._runs[run_id] for run_id in self._latest_by_provider.values()]

    def freshness(self) -> list[SourceFreshnessEvidence]:
        return [record.freshness for record in self.latest_per_provider()]

    def quarantine_records(
        self, *, provider_id: str | None = None
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in self.list_runs(provider_id=provider_id):
            for item in record.quarantine:
                payload = item.to_dict()
                payload["run_id"] = record.run_id
                payload["provider_id"] = record.provider_id
                rows.append(payload)
        return rows


__all__ = [
    "IngestionRunRecord",
    "InMemoryIngestionRunStore",
    "LineageRecord",
    "QuarantineRecord",
    "build_ingestion_run_record",
]
