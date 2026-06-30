from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from modules.external_data.providers import ListingProviderRateLimitError
from modules.external_data.workers import (
    ExternalFetchJobSpec,
    ExternalFetchResiliencePolicy,
    ExternalFetchScheduler,
    InMemoryExternalFetchStateStore,
)


@dataclass
class CountingProvider:
    snapshot_id: str = "listing-live-20260628"
    observed_at: str = "2026-06-28T09:00:00Z"
    calls: int = 0
    fail: bool = False
    exception: Exception | None = None

    def fetch_and_ingest(self, *, ingestion_time: datetime | None = None, correlation_id: str | None = None) -> Any:
        self.calls += 1
        if self.exception is not None:
            raise self.exception
        if self.fail:
            raise RuntimeError("provider unavailable")
        return SimpleNamespace(
            raw_snapshot=SimpleNamespace(
                snapshot_id=self.snapshot_id,
                fetched_at=ingestion_time,
                records=(
                    {
                        "snapshot_id": self.snapshot_id,
                        "source_snapshot_time": self.observed_at,
                    },
                ),
            ),
            canonical_snapshot=SimpleNamespace(snapshot_id=f"canonical-{self.snapshot_id}"),
            correlation_id=correlation_id,
        )


def test_scheduled_fetch_creates_durable_snapshot_ids_and_watermark() -> None:
    provider = CountingProvider()
    store = InMemoryExternalFetchStateStore()
    scheduler = ExternalFetchScheduler(
        state_store=store,
        provider_factories={"listing.partner_feed": lambda: provider},
    )
    spec = ExternalFetchJobSpec(
        provider_id="listing.partner_feed",
        schedule_id="hourly-listing",
        interval=timedelta(hours=1),
        freshness_sla=timedelta(hours=6),
    )
    scheduled_at = datetime(2026, 6, 28, 10, tzinfo=UTC)

    run = scheduler.run_once(spec, scheduled_at=scheduled_at, correlation_id="corr-fetch-001")

    assert run.status == "SUCCEEDED"
    assert run.data_status == "FRESH"
    assert run.source_snapshot_ids == ("listing-live-20260628", "canonical-listing-live-20260628")
    assert run.last_success_watermark_before is None
    assert run.last_success_watermark_after == scheduled_at
    assert store.last_success_watermark("listing.partner_feed") == scheduled_at
    assert provider.calls == 1


def test_backfill_is_idempotent_for_same_windows() -> None:
    provider = CountingProvider()
    scheduler = ExternalFetchScheduler(provider_factories={"listing.partner_feed": lambda: provider})
    spec = ExternalFetchJobSpec(
        provider_id="listing.partner_feed",
        schedule_id="hourly-listing",
        interval=timedelta(hours=1),
    )
    start = datetime(2026, 6, 28, 8, tzinfo=UTC)
    end = datetime(2026, 6, 28, 11, tzinfo=UTC)

    first = scheduler.backfill(spec, start=start, end=end)
    second = scheduler.backfill(spec, start=start, end=end)

    assert [run.idempotency_key for run in first] == [run.idempotency_key for run in second]
    assert [run.job_id for run in first] == [run.job_id for run in second]
    assert len(first) == 3
    assert provider.calls == 3


def test_stale_source_clock_marks_data_status_stale_without_fabricating_freshness() -> None:
    provider = CountingProvider(observed_at="2026-06-20T00:00:00Z")
    scheduler = ExternalFetchScheduler(provider_factories={"listing.partner_feed": lambda: provider})
    spec = ExternalFetchJobSpec(
        provider_id="listing.partner_feed",
        schedule_id="hourly-listing",
        freshness_sla=timedelta(hours=12),
    )

    run = scheduler.run_once(spec, scheduled_at=datetime(2026, 6, 28, 10, tzinfo=UTC))

    assert run.status == "SUCCEEDED"
    assert run.data_status == "STALE"
    assert "2026-06-20T00:00:00+00:00" in run.message


def test_provider_failure_is_blocked_and_does_not_advance_watermark() -> None:
    provider = CountingProvider(fail=True)
    store = InMemoryExternalFetchStateStore()
    scheduler = ExternalFetchScheduler(
        state_store=store,
        provider_factories={"listing.partner_feed": lambda: provider},
    )
    spec = ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="hourly-listing")

    run = scheduler.run_once(spec, scheduled_at=datetime(2026, 6, 28, 10, tzinfo=UTC))

    assert run.status == "FAILED"
    assert run.data_status == "BLOCKED"
    assert run.source_snapshot_ids == ()
    assert store.last_success_watermark("listing.partner_feed") is None
    assert "provider unavailable" in run.message


def test_rate_limit_failure_emits_alert_audit_and_backoff_without_freshness() -> None:
    provider = CountingProvider(
        exception=ListingProviderRateLimitError(
            "quota exhausted",
            provider_id="listing.partner_feed",
            correlation_id="corr-rate",
            code="rate_limited",
        )
    )
    store = InMemoryExternalFetchStateStore()
    scheduler = ExternalFetchScheduler(
        state_store=store,
        provider_factories={"listing.partner_feed": lambda: provider},
        resilience_policy=ExternalFetchResiliencePolicy(
            max_consecutive_failures=3,
            backoff_base=timedelta(minutes=2),
        ),
    )
    spec = ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="hourly-listing")
    scheduled_at = datetime(2026, 6, 28, 10, tzinfo=UTC)

    run = scheduler.run_once(spec, scheduled_at=scheduled_at, correlation_id="corr-rate")

    assert run.status == "FAILED"
    assert run.data_status == "BLOCKED"
    assert run.last_success_watermark_after is None
    assert run.retry_after == scheduled_at + timedelta(minutes=2)
    assert run.alerts[0].reason_code == "rate_limited"
    assert run.alerts[0].severity == "P1"
    assert run.audit_events[0].event_type == "external_data.provider_degraded.v1"
    assert "quota exhausted" in run.message
    assert store.last_success_watermark("listing.partner_feed") is None


def test_circuit_breaker_opens_after_consecutive_failures_and_skips_provider_call() -> None:
    provider = CountingProvider(fail=True)
    store = InMemoryExternalFetchStateStore()
    scheduler = ExternalFetchScheduler(
        state_store=store,
        provider_factories={"listing.partner_feed": lambda: provider},
        resilience_policy=ExternalFetchResiliencePolicy(
            max_consecutive_failures=2,
            circuit_cooldown=timedelta(minutes=20),
        ),
    )
    spec = ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="hourly-listing")

    first = scheduler.run_once(spec, scheduled_at=datetime(2026, 6, 28, 10, tzinfo=UTC))
    second = scheduler.run_once(spec, scheduled_at=datetime(2026, 6, 28, 11, tzinfo=UTC))
    third = scheduler.run_once(spec, scheduled_at=datetime(2026, 6, 28, 11, 5, tzinfo=UTC))

    assert first.data_status == "BLOCKED"
    assert second.alerts[0].reason_code == "provider_failure"
    assert second.retry_after == datetime(2026, 6, 28, 11, 20, tzinfo=UTC)
    assert third.alerts[0].reason_code == "circuit_open"
    assert third.retry_after == datetime(2026, 6, 28, 11, 20, tzinfo=UTC)
    assert provider.calls == 2
    assert store.last_success_watermark("listing.partner_feed") is None


def test_unconfigured_provider_fails_closed_as_blocked() -> None:
    scheduler = ExternalFetchScheduler(provider_factories={})
    spec = ExternalFetchJobSpec(provider_id="poi.commercial_api", schedule_id="hourly-poi")

    run = scheduler.run_once(spec, scheduled_at=datetime(2026, 6, 28, 10, tzinfo=UTC))

    assert run.status == "FAILED"
    assert run.data_status == "BLOCKED"
    assert "not configured" in run.message


def test_backfill_command_outputs_durable_batch_json(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.external_data_backfill import main

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "sys.argv",
            [
                "external_data_backfill.py",
                "--start",
                "2026-06-28T08:00:00Z",
                "--end",
                "2026-06-28T09:00:00Z",
            ],
        )
        assert main() == 0

    output = capsys.readouterr().out
    assert '"provider_id": "listing.partner_feed"' in output
    assert '"source_snapshot_ids"' in output
