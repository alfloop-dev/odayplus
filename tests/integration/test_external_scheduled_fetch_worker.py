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

    def fetch_and_ingest(
        self, *, ingestion_time: datetime | None = None, correlation_id: str | None = None
    ) -> Any:
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


def test_disabled_provider_mode_blocks_before_factory_or_credential_access() -> None:
    provider = CountingProvider()
    scheduler = ExternalFetchScheduler(
        provider_factories={"listing.partner_feed": lambda: provider},
        env={"ODP_EXTERNAL_PROVIDER_MODE": "disabled"},
    )

    run = scheduler.run_once(
        ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="live-e2e-gate"),
        scheduled_at=datetime(2026, 6, 28, 10, tzinfo=UTC),
    )

    assert run.status == "FAILED"
    assert run.data_status == "BLOCKED"
    assert run.alerts[0].reason_code == "provider_mode_disabled"
    assert run.source_snapshot_ids == ()
    assert provider.calls == 0


def _circuit_opened_by_real_provider_failures(
    store: InMemoryExternalFetchStateStore,
    spec: ExternalFetchJobSpec,
    policy: ExternalFetchResiliencePolicy,
) -> datetime:
    """Open the circuit the way production does: repeated live provider faults."""
    live = ExternalFetchScheduler(
        state_store=store,
        provider_factories={
            "listing.partner_feed": lambda: CountingProvider(
                exception=ListingProviderRateLimitError("429 rate limited")
            )
        },
        resilience_policy=policy,
        env={"ODP_EXTERNAL_PROVIDER_MODE": "live", "ODP_DEPLOY_ENV": "dev"},
    )
    for minute in range(policy.max_consecutive_failures):
        live.run_once(spec, scheduled_at=datetime(2026, 7, 29, 1, minute, tzinfo=UTC))
    open_until = store.circuit_open_until(
        spec.provider_id, datetime(2026, 7, 29, 1, 5, tzinfo=UTC)
    )
    assert open_until is not None
    return open_until


def test_disabled_mode_outranks_a_circuit_opened_before_sources_were_closed() -> None:
    """Closing every third-party source must not report the old health verdict.

    The circuit was opened by the release that still fetched. Reading it first
    makes the run say "circuit_open", which the worker does not recognise as
    terminal (it retries a provider this release never contacts) and which the
    live E2E gate rejects because it asserts the disabled reason code on the
    blocked receipt.
    """
    store = InMemoryExternalFetchStateStore()
    policy = ExternalFetchResiliencePolicy(max_consecutive_failures=2)
    spec = ExternalFetchJobSpec(
        provider_id="listing.partner_feed",
        schedule_id="live-e2e-gate",
    )
    open_until = _circuit_opened_by_real_provider_failures(store, spec, policy)
    inside_cooldown = open_until - timedelta(minutes=1)

    provider = CountingProvider()
    disabled = ExternalFetchScheduler(
        state_store=store,
        provider_factories={"listing.partner_feed": lambda: provider},
        resilience_policy=policy,
        env={"ODP_EXTERNAL_PROVIDER_MODE": "disabled"},
    )

    run = disabled.run_once(spec, scheduled_at=inside_cooldown)

    assert run.status == "FAILED"
    assert run.data_status == "BLOCKED"
    assert run.alerts[0].reason_code == "provider_mode_disabled"
    assert run.source_snapshot_ids == ()
    assert provider.calls == 0
    # No fetch can prove the provider healthy again while sources are closed,
    # so the stale verdict is dropped rather than counted down over a window
    # with zero traffic.
    assert store.circuit_open_until("listing.partner_feed", inside_cooldown) is None


def test_reopening_sources_after_a_disabled_window_fetches_immediately() -> None:
    """Switching back to live must not inherit the pre-shutdown circuit."""
    store = InMemoryExternalFetchStateStore()
    policy = ExternalFetchResiliencePolicy(max_consecutive_failures=2)
    spec = ExternalFetchJobSpec(
        provider_id="listing.partner_feed",
        schedule_id="hourly-listing",
    )
    open_until = _circuit_opened_by_real_provider_failures(store, spec, policy)
    inside_cooldown = open_until - timedelta(minutes=5)

    ExternalFetchScheduler(
        state_store=store,
        provider_factories={"listing.partner_feed": lambda: CountingProvider()},
        resilience_policy=policy,
        env={"ODP_EXTERNAL_PROVIDER_MODE": "disabled"},
    ).run_once(spec, scheduled_at=inside_cooldown)

    recovered = CountingProvider(observed_at=inside_cooldown.isoformat())
    relive = ExternalFetchScheduler(
        state_store=store,
        provider_factories={"listing.partner_feed": lambda: recovered},
        resilience_policy=policy,
        env={"ODP_EXTERNAL_PROVIDER_MODE": "live", "ODP_DEPLOY_ENV": "dev"},
    )

    run = relive.run_once(spec, scheduled_at=inside_cooldown + timedelta(minutes=1))

    assert run.status == "SUCCEEDED"
    assert recovered.calls == 1


def test_provider_not_selected_outranks_the_circuit_without_erasing_it() -> None:
    """The ordering rule is about the reason code, not about forgiving faults.

    An allowlist decision only says this deployment does not run the provider;
    unlike closing every source it says nothing about the recorded health
    verdict, so the circuit stays for whoever does run it.
    """
    store = InMemoryExternalFetchStateStore()
    policy = ExternalFetchResiliencePolicy(max_consecutive_failures=2)
    spec = ExternalFetchJobSpec(
        provider_id="listing.partner_feed",
        schedule_id="hourly-listing",
    )
    open_until = _circuit_opened_by_real_provider_failures(store, spec, policy)
    inside_cooldown = open_until - timedelta(minutes=1)

    deselected = ExternalFetchScheduler(
        state_store=store,
        provider_factories={"listing.partner_feed": lambda: CountingProvider()},
        resilience_policy=policy,
        env={
            "ODP_EXTERNAL_PROVIDER_MODE": "live",
            "ODP_DEPLOY_ENV": "dev",
            "ODP_PRODUCTION_PROVIDER_IDS": "poi.commercial_api",
        },
    )

    run = deselected.run_once(spec, scheduled_at=inside_cooldown)

    assert run.alerts[0].reason_code == "provider_not_selected"
    assert store.circuit_open_until("listing.partner_feed", inside_cooldown) == open_until


def test_backfill_is_idempotent_for_same_windows() -> None:
    provider = CountingProvider()
    scheduler = ExternalFetchScheduler(
        provider_factories={"listing.partner_feed": lambda: provider}
    )
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
    scheduler = ExternalFetchScheduler(
        provider_factories={"listing.partner_feed": lambda: provider}
    )
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
    assert run.alerts[0].reason_code == "provider_factory_missing"
    assert "no registered runtime factory" in run.message


@pytest.mark.parametrize(
    ("provider_id", "reason_code"),
    [
        ("unknown.provider", "provider_not_registered"),
        ("geocode.primary_api", "provider_not_schedulable"),
    ],
)
def test_registry_rejects_unknown_and_lookup_only_schedules(
    provider_id: str,
    reason_code: str,
) -> None:
    scheduler = ExternalFetchScheduler(provider_factories={})
    run = scheduler.run_once(
        ExternalFetchJobSpec(
            provider_id=provider_id,
            schedule_id="invalid-schedule",
        ),
        scheduled_at=datetime(2026, 6, 28, 10, tzinfo=UTC),
    )

    assert run.status == "FAILED"
    assert run.data_status == "BLOCKED"
    assert run.alerts[0].reason_code == reason_code


@pytest.mark.parametrize(
    "provider_id",
    [
        "poi.commercial_api",
        "admin_boundary.official_dataset",
    ],
)
def test_worker_blocks_unselected_snapshot_provider_before_factory_execution(
    provider_id: str,
) -> None:
    factory_calls = 0

    def provider_factory() -> CountingProvider:
        nonlocal factory_calls
        factory_calls += 1
        return CountingProvider()

    scheduler = ExternalFetchScheduler(
        provider_factories={provider_id: provider_factory},
        env={
            "ODP_EXTERNAL_PROVIDER_MODE": "live",
            "ODP_DEPLOY_ENV": "production",
            "ODP_PRODUCTION_PROVIDER_IDS": "listing.partner_feed",
        },
    )
    run = scheduler.run_once(
        ExternalFetchJobSpec(
            provider_id=provider_id,
            schedule_id="unselected-snapshot",
        ),
        scheduled_at=datetime(2026, 6, 28, 10, tzinfo=UTC),
    )

    assert run.status == "FAILED"
    assert run.alerts[0].reason_code == "provider_not_selected"
    assert factory_calls == 0


@pytest.mark.parametrize(
    ("allowlist", "reason_code"),
    [
        ("", "provider_allowlist_required"),
        ("poi.commercial_api", "provider_not_selected"),
    ],
)
def test_worker_blocks_unselected_listing_provider_before_factory_execution(
    allowlist: str,
    reason_code: str,
) -> None:
    factory_calls = 0

    def provider_factory() -> CountingProvider:
        nonlocal factory_calls
        factory_calls += 1
        return CountingProvider()

    scheduler = ExternalFetchScheduler(
        provider_factories={"listing.partner_feed": provider_factory},
        env={
            "ODP_EXTERNAL_PROVIDER_MODE": "live",
            "ODP_DEPLOY_ENV": "production",
            "ODP_PRODUCTION_PROVIDER_IDS": allowlist,
            "ODP_LISTING_PROVIDER_API_KEY": "configured-but-not-selected",
        },
    )
    spec = ExternalFetchJobSpec(
        provider_id="listing.partner_feed",
        schedule_id="hourly-listing",
    )

    run = scheduler.run_once(
        spec,
        scheduled_at=datetime(2026, 6, 28, 10, tzinfo=UTC),
        correlation_id=f"corr-worker-{reason_code}",
    )

    assert run.status == "FAILED"
    assert run.data_status == "BLOCKED"
    assert run.alerts[0].reason_code == reason_code
    assert reason_code in run.message
    assert factory_calls == 0


def test_backfill_command_outputs_durable_batch_json(capsys: pytest.CaptureFixture[str]) -> None:
    from product_ops.external_data_backfill import main

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


def test_backfill_command_returns_failure_for_unregistered_provider(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from product_ops.external_data_backfill import main

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "sys.argv",
            [
                "external_data_backfill.py",
                "--provider-id",
                "unknown.provider",
                "--start",
                "2026-06-28T08:00:00Z",
                "--end",
                "2026-06-28T09:00:00Z",
            ],
        )
        assert main() == 10

    output = capsys.readouterr().out
    assert '"reason_code": "provider_not_registered"' in output
    assert '"status": "FAILED"' in output


def test_configuration_rejection_does_not_poison_the_provider_circuit() -> None:
    """ODP-DEPLOY-WORKER-JOB-EXECUTION-001.

    In Cloud Run execution oday-worker-r-79cf9b67e62c-6fhw5 the first two
    attempts reported the real cause (``provider_not_selected``); attempts 3
    and 4 reported ``provider circuit open until ...`` instead, because each
    allowlist rejection had been counted as a consecutive provider failure. The
    provider is never contacted for a configuration rejection, so it must not
    move the circuit breaker and must never mask its own reason code.
    """
    factory_calls = 0

    def provider_factory() -> CountingProvider:
        nonlocal factory_calls
        factory_calls += 1
        return CountingProvider()

    store = InMemoryExternalFetchStateStore()
    scheduler = ExternalFetchScheduler(
        state_store=store,
        provider_factories={"listing.partner_feed": provider_factory},
        resilience_policy=ExternalFetchResiliencePolicy(max_consecutive_failures=2),
        env={
            "ODP_EXTERNAL_PROVIDER_MODE": "live",
            "ODP_DEPLOY_ENV": "dev",
            "ODP_PRODUCTION_PROVIDER_IDS": "poi.commercial_api",
        },
    )
    spec = ExternalFetchJobSpec(
        provider_id="listing.partner_feed",
        schedule_id="hourly-listing",
    )

    runs = [
        scheduler.run_once(spec, scheduled_at=datetime(2026, 7, 29, 1, minute, tzinfo=UTC))
        for minute in (7, 8, 9, 10)
    ]

    assert [run.alerts[0].reason_code for run in runs] == ["provider_not_selected"] * 4
    assert store.circuit_open_until(
        "listing.partner_feed", datetime(2026, 7, 29, 1, 11, tzinfo=UTC)
    ) is None
    assert factory_calls == 0
    assert all("provider_health_unaffected=true" in run.message for run in runs)


def test_provider_failure_still_opens_the_circuit() -> None:
    """The resilience circuit must keep reacting to real provider ill-health."""
    store = InMemoryExternalFetchStateStore()
    scheduler = ExternalFetchScheduler(
        state_store=store,
        provider_factories={
            "listing.partner_feed": lambda: CountingProvider(
                exception=ListingProviderRateLimitError("429 rate limited")
            )
        },
        resilience_policy=ExternalFetchResiliencePolicy(max_consecutive_failures=2),
    )
    spec = ExternalFetchJobSpec(
        provider_id="listing.partner_feed",
        schedule_id="hourly-listing",
    )

    for minute in (7, 8):
        scheduler.run_once(spec, scheduled_at=datetime(2026, 7, 29, 1, minute, tzinfo=UTC))

    assert store.circuit_open_until(
        "listing.partner_feed", datetime(2026, 7, 29, 1, 9, tzinfo=UTC)
    ) is not None


@pytest.mark.parametrize(
    "garbage_mode",
    ["garbage", "GARBAGE", "  unknown  ", "livee", "disabledd", "0", "true", "yes"],
)
def test_unknown_provider_mode_fails_closed_as_disabled(garbage_mode: str) -> None:
    """Regression: ODP_EXTERNAL_PROVIDER_MODE=garbage must not crash the worker.

    Before the fix, ``external_provider_mode()`` raised ``ValueError`` for
    unrecognised values.  The ``ValueError`` escaped
    ``_configuration_refusal()``'s ``except ExternalFetchProviderConfigurationError``
    and crashed the worker without producing a FAILED/BLOCKED scheduler run or
    audit receipt, breaking the fail-closed worker contract.

    Codex reproduced this with ``ODP_EXTERNAL_PROVIDER_MODE=garbage`` (review of
    HEAD b056d92d).
    """
    provider = CountingProvider()
    scheduler = ExternalFetchScheduler(
        provider_factories={"listing.partner_feed": lambda: provider},
        env={"ODP_EXTERNAL_PROVIDER_MODE": garbage_mode},
    )

    run = scheduler.run_once(
        ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="live-e2e-gate"),
        scheduled_at=datetime(2026, 6, 28, 10, tzinfo=UTC),
    )

    assert run.status == "FAILED"
    assert run.data_status == "BLOCKED"
    assert run.alerts[0].reason_code == "provider_mode_disabled"
    assert run.source_snapshot_ids == ()
    assert provider.calls == 0


def test_unknown_provider_mode_does_not_contact_provider_factory() -> None:
    """Regression: unknown mode must not instantiate or call any provider factory."""
    factory_calls = 0

    def provider_factory() -> CountingProvider:
        nonlocal factory_calls
        factory_calls += 1
        return CountingProvider()

    scheduler = ExternalFetchScheduler(
        provider_factories={"listing.partner_feed": provider_factory},
        env={"ODP_EXTERNAL_PROVIDER_MODE": "garbage"},
    )

    run = scheduler.run_once(
        ExternalFetchJobSpec(provider_id="listing.partner_feed", schedule_id="live-e2e-gate"),
        scheduled_at=datetime(2026, 6, 28, 10, tzinfo=UTC),
    )

    assert run.status == "FAILED"
    assert run.alerts[0].reason_code == "provider_mode_disabled"
    assert factory_calls == 0

