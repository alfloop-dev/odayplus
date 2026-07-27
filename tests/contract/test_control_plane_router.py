"""Contract tests for control-plane research signal routing."""

from __future__ import annotations

import json
import runpy
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
CONTRACT_PATH = ROOT / "services" / "control-plane" / "router" / "contract.py"
SCHEMA_PATH = ROOT / "services" / "research" / "schema.json"
SIGNAL_STORE_PATH = ROOT / "services" / "signal-store" / "client.py"
NOW = datetime(2026, 6, 26, 12, tzinfo=UTC)


def _contract() -> dict[str, object]:
    return runpy.run_path(str(CONTRACT_PATH))


def _example() -> dict[str, object]:
    return deepcopy(runpy.run_path(str(SIGNAL_STORE_PATH))["EXAMPLE_SIGNAL_PAYLOAD"])


def _router(contract: dict[str, object]):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return contract["SignalRouter"](schema, now=lambda: NOW)


def test_router_contract_is_versioned_and_preserves_delivery_identity() -> None:
    contract = _contract()

    decision = _router(contract).route(_example())

    assert decision.contract_version == "1.0.0"
    assert decision.destination == "site-review"
    assert decision.destination_owner == "network-platform"
    assert decision.signal_id == "sig_01J2SITE000000000000000001"
    assert decision.tenant_id == "tenant_oday"
    assert decision.idempotency_key == "sitescore:ssr_123:decision_recommended:v1"
    assert decision.correlation_id == "corr_01J2SITE"


@pytest.mark.parametrize(
    ("mutate", "code", "disposition", "retryable"),
    [
        (lambda signal: signal.pop("tenant_id"), "invalid_signal", "dead_letter", False),
        (
            lambda signal: signal.update(signal_version="2.0.0"),
            "unsupported_version",
            "dead_letter",
            False,
        ),
        (
            lambda signal: signal.update(effective_at="2026-06-26T13:00:00Z"),
            "not_effective",
            "retry",
            True,
        ),
        (
            lambda signal: signal.update(expires_at="2026-06-26T11:00:00Z"),
            "expired",
            "drop",
            False,
        ),
        (
            lambda signal: signal.update(intent="monitoring_alert"),
            "route_not_found",
            "dead_letter",
            False,
        ),
    ],
)
def test_failures_have_explicit_retry_and_disposition_semantics(
    mutate, code: str, disposition: str, retryable: bool
) -> None:
    contract = _contract()
    signal = _example()
    mutate(signal)

    with pytest.raises(contract["SignalRouteError"]) as exc_info:
        _router(contract).route(signal)

    failure = exc_info.value.failure
    assert failure.code == code
    assert failure.disposition == disposition
    assert failure.retryable is retryable


def test_not_effective_failure_exposes_minimum_retry_delay() -> None:
    contract = _contract()
    signal = _example()
    signal["effective_at"] = "2026-06-26T12:01:00Z"

    with pytest.raises(contract["SignalRouteError"]) as exc_info:
        _router(contract).route(signal)

    assert exc_info.value.failure.retry_after_seconds == 60


def test_delivery_failure_retries_then_dead_letters_with_same_semantics() -> None:
    contract = _contract()

    retry = contract["delivery_failure"](
        attempt=1, max_attempts=3, downstream_retryable=True, retry_after_seconds=30
    )
    exhausted = contract["delivery_failure"](
        attempt=3, max_attempts=3, downstream_retryable=True
    )
    rejected = contract["delivery_failure"](
        attempt=1, max_attempts=3, downstream_retryable=False
    )

    assert (retry.code, retry.disposition, retry.retryable, retry.retry_after_seconds) == (
        "downstream_unavailable",
        "retry",
        True,
        30,
    )
    assert (exhausted.code, exhausted.disposition, exhausted.retryable) == (
        "retry_exhausted",
        "dead_letter",
        False,
    )
    assert (rejected.code, rejected.disposition, rejected.retryable) == (
        "delivery_rejected",
        "dead_letter",
        False,
    )


def test_monitoring_metric_labels_exclude_unbounded_identifiers() -> None:
    metric_contract = _contract()["METRIC_CONTRACT"]

    assert set(metric_contract) == {
        "control_plane_router_decisions_total",
        "control_plane_router_failures_total",
        "control_plane_router_delivery_attempts_total",
        "control_plane_router_route_duration_seconds",
        "control_plane_router_oldest_retry_age_seconds",
    }
    forbidden = {"tenant_id", "signal_id", "correlation_id", "idempotency_key"}
    assert all(not forbidden.intersection(labels) for labels in metric_contract.values())


def test_every_default_route_has_an_explicit_owner() -> None:
    routes = _contract()["DEFAULT_ROUTES"]

    assert routes
    assert all(target.name and target.owner for target in routes.values())
