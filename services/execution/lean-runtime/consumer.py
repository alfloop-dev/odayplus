"""Fail-closed signal consumer for the LEAN execution runtime.

The runtime owns validation and delivery semantics, while a broker adapter owns
network connections, authentication, offset commits, and dead-letter routing.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker

SIGNAL_SCHEMA_PATH = Path(__file__).parents[2] / "research" / "schema.json"
SUPPORTED_SIGNAL_MAJOR = 1


class ConsumptionOutcome(StrEnum):
    CONSUMED = "consumed"
    REPLAYED = "replayed"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    RETRYABLE_FAILURE = "retryable_failure"


class BrokerMessage(Protocol):
    """Minimal boundary an actual Kafka/Redpanda adapter must implement."""

    @property
    def body(self) -> bytes: ...

    def ack(self) -> None:
        """Commit the message offset only after a durable processing receipt."""

    def reject(self, *, retryable: bool, reason: str) -> None:
        """Route permanently invalid messages to DLQ; retry transient failures."""


class ProcessingReceiptStore(Protocol):
    """Durable idempotency boundary, keyed within a tenant."""

    def result_for(self, *, tenant_id: str, idempotency_key: str) -> str | None: ...

    def record(
        self, *, tenant_id: str, idempotency_key: str, signal_id: str, result_ref: str
    ) -> None: ...


SignalHandler = Callable[[Mapping[str, Any]], str]


@dataclass(frozen=True, slots=True)
class BrokerConfig:
    """Non-secret broker coordinates supplied to the transport adapter."""

    bootstrap_servers: tuple[str, ...]
    topic: str
    consumer_group: str
    security_protocol: str = "SASL_SSL"
    dead_letter_topic: str | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> BrokerConfig:
        servers = tuple(
            item.strip()
            for item in values.get("LEAN_SIGNAL_BROKER_SERVERS", "").split(",")
            if item.strip()
        )
        topic = values.get("LEAN_SIGNAL_TOPIC", "").strip()
        group = values.get("LEAN_SIGNAL_CONSUMER_GROUP", "").strip()
        protocol = values.get("LEAN_SIGNAL_SECURITY_PROTOCOL", "SASL_SSL").strip()
        if not servers or not topic or not group:
            raise ValueError(
                "LEAN_SIGNAL_BROKER_SERVERS, LEAN_SIGNAL_TOPIC, and "
                "LEAN_SIGNAL_CONSUMER_GROUP are required"
            )
        if protocol not in {"PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"}:
            raise ValueError(f"unsupported LEAN_SIGNAL_SECURITY_PROTOCOL: {protocol}")
        return cls(
            bootstrap_servers=servers,
            topic=topic,
            consumer_group=group,
            security_protocol=protocol,
            dead_letter_topic=values.get("LEAN_SIGNAL_DEAD_LETTER_TOPIC") or None,
        )


class InMemoryReceiptStore:
    """Test/local implementation; production must inject a durable store."""

    def __init__(self) -> None:
        self._receipts: dict[tuple[str, str], tuple[str, str]] = {}

    def result_for(self, *, tenant_id: str, idempotency_key: str) -> str | None:
        receipt = self._receipts.get((tenant_id, idempotency_key))
        return receipt[1] if receipt else None

    def record(
        self, *, tenant_id: str, idempotency_key: str, signal_id: str, result_ref: str
    ) -> None:
        key = (tenant_id, idempotency_key)
        existing = self._receipts.get(key)
        if existing is not None and existing[0] != signal_id:
            raise ValueError("idempotency key is already bound to another signal")
        self._receipts[key] = (signal_id, result_ref)


class LeanSignalConsumer:
    def __init__(
        self,
        *,
        handler: SignalHandler,
        receipts: ProcessingReceiptStore,
        now: Callable[[], datetime] | None = None,
        schema_path: Path = SIGNAL_SCHEMA_PATH,
    ) -> None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self._validator = Draft202012Validator(schema, format_checker=FormatChecker())
        self._handler = handler
        self._receipts = receipts
        self._now = now or (lambda: datetime.now(UTC))

    def consume(self, message: BrokerMessage) -> ConsumptionOutcome:
        try:
            envelope = self._decode(message.body)
            self._validate(envelope)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            message.reject(retryable=False, reason=f"invalid_signal: {exc}")
            return ConsumptionOutcome.REJECTED

        now = self._as_utc(self._now())
        effective_at = self._timestamp(envelope.get("effective_at"))
        expires_at = self._timestamp(envelope.get("expires_at"))
        if effective_at is not None and effective_at > now:
            message.reject(retryable=True, reason="signal_not_yet_effective")
            return ConsumptionOutcome.DEFERRED
        if expires_at is not None and expires_at <= now:
            message.reject(retryable=False, reason="signal_expired")
            return ConsumptionOutcome.REJECTED

        tenant_id = envelope["tenant_id"]
        idempotency_key = envelope["idempotency_key"]
        if self._receipts.result_for(
            tenant_id=tenant_id, idempotency_key=idempotency_key
        ) is not None:
            message.ack()
            return ConsumptionOutcome.REPLAYED

        try:
            result_ref = self._handler(envelope)
            if not result_ref:
                raise ValueError("handler returned an empty result reference")
            self._receipts.record(
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                signal_id=envelope["signal_id"],
                result_ref=result_ref,
            )
            message.ack()
        except Exception as exc:
            message.reject(retryable=True, reason=f"handler_failure: {type(exc).__name__}")
            return ConsumptionOutcome.RETRYABLE_FAILURE
        return ConsumptionOutcome.CONSUMED

    @staticmethod
    def _decode(body: bytes) -> dict[str, Any]:
        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite JSON number: {value}")

        decoded = json.loads(body.decode("utf-8"), parse_constant=reject_constant)
        if not isinstance(decoded, dict):
            raise TypeError("signal envelope must be a JSON object")
        return decoded

    def _validate(self, envelope: Mapping[str, Any]) -> None:
        version = envelope.get("signal_version")
        if not isinstance(version, str):
            raise ValueError("signal_version must be a string")
        try:
            major = int(version.split(".", 1)[0])
        except (ValueError, IndexError) as exc:
            raise ValueError("signal_version is not semantic-version shaped") from exc
        if major != SUPPORTED_SIGNAL_MAJOR:
            raise ValueError(f"unsupported signal_version major: {major}")
        errors = sorted(self._validator.iter_errors(envelope), key=lambda error: list(error.path))
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.absolute_path) or "<envelope>"
            raise ValueError(f"schema violation at {location}: {error.message}")

    @staticmethod
    def _timestamp(value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("timestamp must be a string or null")
        return LeanSignalConsumer._as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("runtime clock must be timezone-aware")
        return value.astimezone(UTC)

