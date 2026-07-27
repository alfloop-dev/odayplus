"""Versioned control-plane routing contract for research signals.

The router owns validation and destination selection. Queue delivery, retries,
dead-letter persistence, and business authorization remain adapter concerns.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROUTER_CONTRACT_VERSION = "1.0.0"
SUPPORTED_SIGNAL_MAJOR = 1
SIGNAL_SCHEMA_PATH = Path(__file__).parents[2] / "research" / "schema.json"


class RouteErrorCode(StrEnum):
    INVALID_SIGNAL = "invalid_signal"
    UNSUPPORTED_VERSION = "unsupported_version"
    NOT_EFFECTIVE = "not_effective"
    EXPIRED = "expired"
    ROUTE_NOT_FOUND = "route_not_found"
    DOWNSTREAM_UNAVAILABLE = "downstream_unavailable"
    DELIVERY_REJECTED = "delivery_rejected"
    RETRY_EXHAUSTED = "retry_exhausted"


class FailureDisposition(StrEnum):
    RETRY = "retry"
    DEAD_LETTER = "dead_letter"
    DROP = "drop"


@dataclass(frozen=True, slots=True)
class RouteTarget:
    name: str
    owner: str


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    contract_version: str
    signal_id: str
    tenant_id: str
    destination: str
    destination_owner: str
    idempotency_key: str
    correlation_id: str


@dataclass(frozen=True, slots=True)
class RouterFailure:
    code: RouteErrorCode
    disposition: FailureDisposition
    retryable: bool
    detail: str
    retry_after_seconds: int | None = None


class SignalRouteError(Exception):
    """A stable routing failure that adapters can translate without guessing."""

    def __init__(self, failure: RouterFailure) -> None:
        super().__init__(failure.detail)
        self.failure = failure


DEFAULT_ROUTES: Mapping[tuple[str, str], RouteTarget] = {
    ("sitescore", "decision_recommended"): RouteTarget("site-review", "network-platform"),
    ("forecast", "decision_recommended"): RouteTarget("forecast-review", "planning-platform"),
    ("intervention", "execution_requested"): RouteTarget(
        "intervention-execution", "operations-platform"
    ),
    ("pricing", "execution_requested"): RouteTarget("pricing-execution", "pricing-platform"),
    ("adlift", "decision_recommended"): RouteTarget("campaign-review", "growth-platform"),
    ("valuation", "decision_recommended"): RouteTarget("valuation-review", "network-platform"),
    ("netplan", "decision_recommended"): RouteTarget("network-review", "network-platform"),
    ("model_release", "model_release_requested"): RouteTarget(
        "model-release-review", "ml-platform"
    ),
    ("model_release", "rollback_requested"): RouteTarget("model-rollback", "ml-platform"),
}


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class SignalRouter:
    """Validate an envelope and produce a deterministic, side-effect-free route."""

    def __init__(
        self,
        schema: Mapping[str, Any],
        *,
        routes: Mapping[tuple[str, str], RouteTarget] = DEFAULT_ROUTES,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self._routes = dict(routes)
        self._now = now

    def route(self, envelope: Mapping[str, Any]) -> RoutingDecision:
        errors = sorted(self._validator.iter_errors(envelope), key=lambda error: list(error.path))
        if errors:
            error = errors[0]
            path = ".".join(str(part) for part in error.absolute_path) or "<envelope>"
            code = (
                RouteErrorCode.UNSUPPORTED_VERSION
                if list(error.absolute_path) == ["signal_version"]
                else RouteErrorCode.INVALID_SIGNAL
            )
            raise SignalRouteError(
                RouterFailure(
                    code,
                    FailureDisposition.DEAD_LETTER,
                    False,
                    f"{path}: {error.message}",
                )
            )

        version = str(envelope["signal_version"])
        if int(version.split(".", maxsplit=1)[0]) != SUPPORTED_SIGNAL_MAJOR:
            raise SignalRouteError(
                RouterFailure(
                    RouteErrorCode.UNSUPPORTED_VERSION,
                    FailureDisposition.DEAD_LETTER,
                    False,
                    f"unsupported signal_version {version}",
                )
            )

        now = self._now().astimezone(UTC)
        effective_at = envelope["effective_at"]
        if effective_at is not None and _instant(effective_at) > now:
            retry_after = max(1, int((_instant(effective_at) - now).total_seconds()))
            raise SignalRouteError(
                RouterFailure(
                    RouteErrorCode.NOT_EFFECTIVE,
                    FailureDisposition.RETRY,
                    True,
                    "signal is not effective yet",
                    retry_after,
                )
            )
        expires_at = envelope["expires_at"]
        if expires_at is not None and _instant(expires_at) <= now:
            raise SignalRouteError(
                RouterFailure(
                    RouteErrorCode.EXPIRED,
                    FailureDisposition.DROP,
                    False,
                    "signal has expired",
                )
            )

        target = self._routes.get((str(envelope["domain"]), str(envelope["intent"])))
        if target is None:
            raise SignalRouteError(
                RouterFailure(
                    RouteErrorCode.ROUTE_NOT_FOUND,
                    FailureDisposition.DEAD_LETTER,
                    False,
                    f"no route for {envelope['domain']}/{envelope['intent']}",
                )
            )

        return RoutingDecision(
            contract_version=ROUTER_CONTRACT_VERSION,
            signal_id=str(envelope["signal_id"]),
            tenant_id=str(envelope["tenant_id"]),
            destination=target.name,
            destination_owner=target.owner,
            idempotency_key=str(envelope["idempotency_key"]),
            correlation_id=str(envelope["trace"]["correlation_id"]),
        )


def delivery_failure(
    *,
    attempt: int,
    max_attempts: int,
    downstream_retryable: bool,
    retry_after_seconds: int | None = None,
) -> RouterFailure:
    """Translate adapter delivery failure into the contract retry semantics."""

    if downstream_retryable and attempt < max_attempts:
        return RouterFailure(
            RouteErrorCode.DOWNSTREAM_UNAVAILABLE,
            FailureDisposition.RETRY,
            True,
            "downstream delivery failed; retry with the same idempotency key",
            retry_after_seconds,
        )
    code = (
        RouteErrorCode.RETRY_EXHAUSTED
        if downstream_retryable
        else RouteErrorCode.DELIVERY_REJECTED
    )
    return RouterFailure(
        code,
        FailureDisposition.DEAD_LETTER,
        False,
        "delivery attempts exhausted" if downstream_retryable else "downstream rejected signal",
    )


METRIC_CONTRACT: Mapping[str, tuple[str, ...]] = {
    "control_plane_router_decisions_total": ("destination", "domain", "intent"),
    "control_plane_router_failures_total": ("code", "disposition"),
    "control_plane_router_delivery_attempts_total": ("destination", "outcome"),
    "control_plane_router_route_duration_seconds": ("destination",),
    "control_plane_router_oldest_retry_age_seconds": ("destination",),
}
