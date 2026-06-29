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
    last_success_watermark_before: datetime | None
    last_success_watermark_after: datetime | None
    correlation_id: str
    message: str = ""

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
            "last_success_watermark_before": self.last_success_watermark_before.isoformat()
            if self.last_success_watermark_before
            else None,
            "last_success_watermark_after": self.last_success_watermark_after.isoformat()
            if self.last_success_watermark_after
            else None,
            "correlation_id": self.correlation_id,
            "message": self.message,
        }


class InMemoryExternalFetchStateStore:
    """Durable-state interface used by tests; production can swap in DB storage."""

    def __init__(self) -> None:
        self._runs_by_key: dict[str, ExternalFetchRun] = {}
        self._last_success: dict[str, datetime] = {}

    def get_run(self, idempotency_key: str) -> ExternalFetchRun | None:
        return self._runs_by_key.get(idempotency_key)

    def save_run(self, run: ExternalFetchRun) -> ExternalFetchRun:
        existing = self._runs_by_key.get(run.idempotency_key)
        if existing is not None:
            return existing
        self._runs_by_key[run.idempotency_key] = run
        if run.status == "SUCCEEDED" and run.last_success_watermark_after is not None:
            self._last_success[run.provider_id] = run.last_success_watermark_after
        return run

    def last_success_watermark(self, provider_id: str) -> datetime | None:
        return self._last_success.get(provider_id)


class ExternalFetchScheduler:
    def __init__(
        self,
        *,
        state_store: InMemoryExternalFetchStateStore | None = None,
        provider_factories: Mapping[str, ProviderFactory] | None = None,
    ) -> None:
        self.state_store = state_store or InMemoryExternalFetchStateStore()
        self.provider_factories = dict(provider_factories or {})

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
                last_success_watermark_before=watermark_before,
                last_success_watermark_after=effective_end,
                correlation_id=corr,
                message=f"latest provider observation {observed_at.isoformat()}",
            )
        except Exception as exc:
            run = ExternalFetchRun(
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
                last_success_watermark_before=watermark_before,
                last_success_watermark_after=watermark_before,
                correlation_id=corr,
                message=f"{type(exc).__name__}: {exc}",
            )
        return self.state_store.save_run(run)

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


def _idempotency_key(spec: ExternalFetchJobSpec, window_start: datetime, window_end: datetime) -> str:
    return f"{spec.provider_id}:{spec.schedule_id}:{window_start.isoformat()}:{window_end.isoformat()}"


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
    "ExternalFetchRun",
    "ExternalFetchScheduler",
    "InMemoryExternalFetchStateStore",
    "run_external_fetch_backfill",
]
