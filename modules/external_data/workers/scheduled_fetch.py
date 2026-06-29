from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from modules.external_data.providers import ListingPartnerFeedProvider
from shared.observability import new_correlation_id


class FetchProvider(Protocol):
    def fetch_and_ingest(
        self,
        *,
        ingestion_time: datetime | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        ...


ProviderFactory = Callable[[], FetchProvider]


@dataclass(frozen=True)
class ExternalFetchJobSpec:
    provider_id: str
    schedule_id: str
    interval: timedelta = timedelta(hours=1)
    freshness_sla: timedelta = timedelta(hours=24)


@dataclass(frozen=True)
class ExternalFetchResiliencePolicy:
    max_consecutive_failures: int = 2
    circuit_cooldown: timedelta = timedelta(minutes=15)
    backoff_base: timedelta = timedelta(seconds=30)


@dataclass(frozen=True)
class ExternalFetchAlert:
    event_id: str
    event_type: str
    provider_id: str
    severity: str
    reason_code: str
    occurred_at: datetime
    correlation_id: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "provider_id": self.provider_id,
            "severity": self.severity,
            "reason_code": self.reason_code,
            "occurred_at": self.occurred_at.isoformat(),
            "correlation_id": self.correlation_id,
            "message": self.message,
        }


@dataclass(frozen=True)
class ExternalFetchRun:
    job_id: str
    provider_id: str
    schedule_id: str
    idempotency_key: str
    status: str
    data_status: str
    window_start: datetime
    window_end: datetime
    started_at: datetime
    completed_at: datetime
    source_snapshot_ids: tuple[str, ...]
    raw_snapshot_id: str
    canonical_snapshot_id: str
    source_snapshot_id: str
    provider_observed_at: datetime | None
    ingested_at: datetime | None
    last_success_watermark_before: datetime | None
    last_success_watermark_after: datetime | None
    correlation_id: str
    message: str = ""
    retry_after: datetime | None = None
    alerts: tuple[ExternalFetchAlert, ...] = ()
    audit_events: tuple[ExternalFetchAlert, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "provider_id": self.provider_id,
            "schedule_id": self.schedule_id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "data_status": self.data_status,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "raw_snapshot_id": self.raw_snapshot_id,
            "canonical_snapshot_id": self.canonical_snapshot_id,
            "source_snapshot_id": self.source_snapshot_id,
            "provider_observed_at": self.provider_observed_at.isoformat() if self.provider_observed_at else None,
            "ingested_at": self.ingested_at.isoformat() if self.ingested_at else None,
            "last_success_watermark_before": self.last_success_watermark_before.isoformat()
            if self.last_success_watermark_before
            else None,
            "last_success_watermark_after": self.last_success_watermark_after.isoformat()
            if self.last_success_watermark_after
            else None,
            "correlation_id": self.correlation_id,
            "message": self.message,
            "retry_after": self.retry_after.isoformat() if self.retry_after else None,
            "alerts": [alert.to_dict() for alert in self.alerts],
            "audit_events": [event.to_dict() for event in self.audit_events],
        }


@dataclass(frozen=True)
class SourceFreshnessEvidence:
    provider_id: str
    source_snapshot_id: str
    data_status: str
    provider_observed_at: datetime | None
    ingested_at: datetime | None
    freshness_sla_seconds: int
    correlation_id: str
    quality_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "source_snapshot_id": self.source_snapshot_id,
            "data_status": self.data_status,
            "provider_observed_at": self.provider_observed_at.isoformat() if self.provider_observed_at else None,
            "ingested_at": self.ingested_at.isoformat() if self.ingested_at else None,
            "freshness_sla_seconds": self.freshness_sla_seconds,
            "correlation_id": self.correlation_id,
            "quality_flags": list(self.quality_flags),
        }


class InMemoryExternalFetchStateStore:
    """Durable-state interface used by tests; production can swap in DB storage."""

    def __init__(self) -> None:
        self._runs_by_key: dict[str, ExternalFetchRun] = {}
        self._last_success: dict[str, datetime] = {}
        self._consecutive_failures: dict[str, int] = {}
        self._circuit_open_until: dict[str, datetime] = {}

    def get_run(self, idempotency_key: str) -> ExternalFetchRun | None:
        return self._runs_by_key.get(idempotency_key)

    def save_run(self, run: ExternalFetchRun) -> ExternalFetchRun:
        existing = self._runs_by_key.get(run.idempotency_key)
        if existing is not None:
            return existing
        self._runs_by_key[run.idempotency_key] = run
        if run.status == "SUCCEEDED" and run.last_success_watermark_after is not None:
            self._last_success[run.provider_id] = run.last_success_watermark_after
            self._consecutive_failures[run.provider_id] = 0
        return run

    def last_success_watermark(self, provider_id: str) -> datetime | None:
        return self._last_success.get(provider_id)

    def record_failure(
        self,
        provider_id: str,
        *,
        at: datetime,
        policy: ExternalFetchResiliencePolicy,
    ) -> tuple[int, datetime | None]:
        failures = self._consecutive_failures.get(provider_id, 0) + 1
        self._consecutive_failures[provider_id] = failures
        if failures >= policy.max_consecutive_failures:
            open_until = at + policy.circuit_cooldown
            self._circuit_open_until[provider_id] = open_until
            return failures, open_until
        return failures, None

    def circuit_open_until(self, provider_id: str, at: datetime) -> datetime | None:
        open_until = self._circuit_open_until.get(provider_id)
        if open_until is not None and open_until > at:
            return open_until
        if open_until is not None:
            self._circuit_open_until.pop(provider_id, None)
        return None


class ExternalFetchScheduler:
    def __init__(
        self,
        *,
        state_store: InMemoryExternalFetchStateStore | None = None,
        provider_factories: Mapping[str, ProviderFactory] | None = None,
        resilience_policy: ExternalFetchResiliencePolicy | None = None,
    ) -> None:
        self.state_store = state_store or InMemoryExternalFetchStateStore()
        self.provider_factories = dict(provider_factories or {})
        self.resilience_policy = resilience_policy or ExternalFetchResiliencePolicy()

    def run_once(
        self,
        spec: ExternalFetchJobSpec,
        *,
        scheduled_at: datetime | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ExternalFetchRun:
        effective_end = _ensure_utc(window_end or scheduled_at or datetime.now(UTC))
        watermark_before = self.state_store.last_success_watermark(spec.provider_id)
        effective_start = _ensure_utc(window_start or watermark_before or (effective_end - spec.interval))
        idempotency_key = _idempotency_key(spec, effective_start, effective_end)
        existing = self.state_store.get_run(idempotency_key)
        if existing is not None:
            return existing

        corr = correlation_id or new_correlation_id()
        started_at = effective_end
        circuit_open_until = self.state_store.circuit_open_until(spec.provider_id, effective_end)
        if circuit_open_until is not None:
            alert = _alert(
                provider_id=spec.provider_id,
                reason_code="circuit_open",
                occurred_at=effective_end,
                correlation_id=corr,
                message=f"provider circuit open until {circuit_open_until.isoformat()}",
            )
            return self.state_store.save_run(
                self._blocked_run(
                    spec,
                    effective_start=effective_start,
                    effective_end=effective_end,
                    started_at=started_at,
                    watermark_before=watermark_before,
                    correlation_id=corr,
                    idempotency_key=idempotency_key,
                    reason_code="circuit_open",
                    message=alert.message,
                    retry_after=circuit_open_until,
                    alert=alert,
                )
            )

        try:
            provider = self._provider_for(spec.provider_id)
            result = provider.fetch_and_ingest(ingestion_time=effective_end, correlation_id=corr)
            raw_snapshot_id = str(result.raw_snapshot.snapshot_id)
            canonical_snapshot_id = str(result.canonical_snapshot.snapshot_id)
            observed_at = _latest_observed_at(result.raw_snapshot.records) or result.raw_snapshot.fetched_at
            data_status = "FRESH" if effective_end - observed_at <= spec.freshness_sla else "STALE"
            run = ExternalFetchRun(
                job_id=f"external-fetch:{spec.provider_id}:{raw_snapshot_id}:{effective_end.strftime('%Y%m%d%H%M%S')}",
                provider_id=spec.provider_id,
                schedule_id=spec.schedule_id,
                idempotency_key=idempotency_key,
                status="SUCCEEDED",
                data_status=data_status,
                window_start=effective_start,
                window_end=effective_end,
                started_at=started_at,
                completed_at=effective_end,
                source_snapshot_ids=tuple(
                    dict.fromkeys(snapshot for snapshot in (raw_snapshot_id, canonical_snapshot_id) if snapshot)
                ),
                raw_snapshot_id=raw_snapshot_id,
                canonical_snapshot_id=canonical_snapshot_id,
                source_snapshot_id=raw_snapshot_id,
                provider_observed_at=observed_at,
                ingested_at=effective_end,
                last_success_watermark_before=watermark_before,
                last_success_watermark_after=effective_end,
                correlation_id=corr,
                message=f"latest provider observation {observed_at.isoformat()}",
            )
        except Exception as exc:
            failures, circuit_until = self.state_store.record_failure(
                spec.provider_id,
                at=effective_end,
                policy=self.resilience_policy,
            )
            reason_code = _provider_failure_code(exc)
            retry_after = circuit_until or (
                effective_end + self.resilience_policy.backoff_base * max(1, failures)
            )
            alert = _alert(
                provider_id=spec.provider_id,
                reason_code=reason_code,
                occurred_at=effective_end,
                correlation_id=corr,
                message=(
                    f"{type(exc).__name__}: {exc}; consecutive_failures={failures}; "
                    f"retry_after={retry_after.isoformat()}"
                ),
            )
            run = self._blocked_run(
                spec,
                effective_start=effective_start,
                effective_end=effective_end,
                started_at=started_at,
                watermark_before=watermark_before,
                correlation_id=corr,
                idempotency_key=idempotency_key,
                reason_code=reason_code,
                message=alert.message,
                retry_after=retry_after,
                alert=alert,
            )
        return self.state_store.save_run(run)

    def _blocked_run(
        self,
        spec: ExternalFetchJobSpec,
        *,
        effective_start: datetime,
        effective_end: datetime,
        started_at: datetime,
        watermark_before: datetime | None,
        correlation_id: str,
        idempotency_key: str,
        reason_code: str,
        message: str,
        retry_after: datetime,
        alert: ExternalFetchAlert,
    ) -> ExternalFetchRun:
        del reason_code
        return ExternalFetchRun(
            job_id=f"external-fetch:{spec.provider_id}:blocked:{effective_end.strftime('%Y%m%d%H%M%S')}",
            provider_id=spec.provider_id,
            schedule_id=spec.schedule_id,
            idempotency_key=idempotency_key,
            status="FAILED",
            data_status="BLOCKED",
            window_start=effective_start,
            window_end=effective_end,
            started_at=started_at,
            completed_at=effective_end,
            source_snapshot_ids=(),
            raw_snapshot_id="",
            canonical_snapshot_id="",
            source_snapshot_id="",
            provider_observed_at=None,
            ingested_at=effective_end,
            last_success_watermark_before=watermark_before,
            last_success_watermark_after=watermark_before,
            correlation_id=correlation_id,
            message=message,
            retry_after=retry_after,
            alerts=(alert,),
            audit_events=(alert,),
        )

    def backfill(
        self,
        spec: ExternalFetchJobSpec,
        *,
        start: datetime,
        end: datetime,
        step: timedelta | None = None,
        correlation_id: str | None = None,
    ) -> tuple[ExternalFetchRun, ...]:
        runs: list[ExternalFetchRun] = []
        cursor = _ensure_utc(start)
        effective_end = _ensure_utc(end)
        interval = step or spec.interval
        while cursor < effective_end:
            next_cursor = min(cursor + interval, effective_end)
            runs.append(
                self.run_once(
                    spec,
                    window_start=cursor,
                    window_end=next_cursor,
                    correlation_id=correlation_id,
                )
            )
            cursor = next_cursor
        return tuple(runs)

    def _provider_for(self, provider_id: str) -> FetchProvider:
        factory = self.provider_factories.get(provider_id)
        if factory is not None:
            return factory()
        if provider_id == "listing.partner_feed":
            return ListingPartnerFeedProvider()
        raise ValueError(f"scheduled fetch provider {provider_id} is not configured")


def run_external_fetch_backfill(
    *,
    provider_id: str,
    start: datetime,
    end: datetime,
    schedule_id: str = "manual-backfill",
    interval: timedelta = timedelta(hours=1),
    freshness_sla: timedelta = timedelta(hours=24),
) -> tuple[ExternalFetchRun, ...]:
    spec = ExternalFetchJobSpec(
        provider_id=provider_id,
        schedule_id=schedule_id,
        interval=interval,
        freshness_sla=freshness_sla,
    )
    return ExternalFetchScheduler().backfill(spec, start=start, end=end, step=interval)


def freshness_evidence_from_run(
    run: ExternalFetchRun,
    *,
    freshness_sla: timedelta,
) -> SourceFreshnessEvidence:
    quality_flags: list[str] = []
    if run.data_status in {"STALE", "BLOCKED"}:
        quality_flags.append(run.data_status.lower())
    return SourceFreshnessEvidence(
        provider_id=run.provider_id,
        source_snapshot_id=run.source_snapshot_id or run.raw_snapshot_id,
        data_status=run.data_status,
        provider_observed_at=run.provider_observed_at,
        ingested_at=run.ingested_at,
        freshness_sla_seconds=int(freshness_sla.total_seconds()),
        correlation_id=run.correlation_id,
        quality_flags=tuple(quality_flags),
    )


def _idempotency_key(spec: ExternalFetchJobSpec, window_start: datetime, window_end: datetime) -> str:
    return f"{spec.provider_id}:{spec.schedule_id}:{window_start.isoformat()}:{window_end.isoformat()}"


def _provider_failure_code(exc: Exception) -> str:
    code = str(getattr(exc, "code", "") or "").lower()
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "rate" in code or "quota" in code or "rate" in name or "quota" in message:
        return "rate_limited"
    if "unauthorized" in code or "auth" in name or "401" in message or "403" in message:
        return "unauthorized"
    if "timeout" in code or "timeout" in name or "timed out" in message:
        return "timeout"
    if "server" in code or "5xx" in message:
        return "server_error"
    return "provider_failure"


def _alert(
    *,
    provider_id: str,
    reason_code: str,
    occurred_at: datetime,
    correlation_id: str,
    message: str,
) -> ExternalFetchAlert:
    severity = "P1" if reason_code in {"rate_limited", "circuit_open"} else "P2"
    return ExternalFetchAlert(
        event_id=f"external-fetch-alert:{provider_id}:{reason_code}:{correlation_id}",
        event_type="external_data.provider_degraded.v1",
        provider_id=provider_id,
        severity=severity,
        reason_code=reason_code,
        occurred_at=occurred_at,
        correlation_id=correlation_id,
        message=message,
    )


def _latest_observed_at(records: Iterable[Mapping[str, Any]]) -> datetime | None:
    timestamps = tuple(
        timestamp
        for record in records
        for timestamp in (
            _parse_datetime(record.get("source_snapshot_time"))
            or _parse_datetime(record.get("snapshot_time"))
            or _parse_datetime(record.get("observed_at"))
            or _parse_datetime(record.get("last_verified_at")),
        )
        if timestamp is not None
    )
    return max(timestamps) if timestamps else None


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    return _ensure_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "ExternalFetchJobSpec",
    "ExternalFetchAlert",
    "ExternalFetchResiliencePolicy",
    "ExternalFetchRun",
    "ExternalFetchScheduler",
    "InMemoryExternalFetchStateStore",
    "SourceFreshnessEvidence",
    "freshness_evidence_from_run",
    "run_external_fetch_backfill",
]
