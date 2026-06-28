"""Structured (JSON) logging primitives.

Source baseline: ODP-SD-11 §4 (Logging 設計). Every log line is a single JSON
object that carries the trace context required to correlate a request across
API / Event / Worker / Data / Model / Decision / Report stages.

ODP-AC-SD11-001 / ODP-R7-001 acceptance: a structured log record must include
``timestamp``, ``service``, ``actor``, ``correlation_id``, ``resource``,
``result`` and (for errors) ``error_code``.

This module is dependency-free on purpose: the platform skeleton has no Cloud
Logging / OpenTelemetry SDK wired in yet, so emission is pluggable via a sink
callable. Production wiring swaps the sink for a Cloud Logging exporter without
changing call sites.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, TextIO

REDACTED = "[REDACTED]"

# Keys whose values must never be written to logs (ODP-SD-11 §4.2.2: no full
# PII, secret, token or private signed URL). Matching is case-insensitive and
# substring-based so ``user_password`` / ``X-Auth-Token`` are caught too.
SENSITIVE_KEY_FRAGMENTS: frozenset[str] = frozenset(
    {
        "password",
        "secret",
        "token",
        "authorization",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "credential",
        "signed_url",
        "ssn",
        "national_id",
    }
)


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def redact(value: Any) -> Any:
    """Recursively replace sensitive mapping values with ``REDACTED``."""

    if isinstance(value, Mapping):
        return {
            key: (REDACTED if _is_sensitive(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


@dataclass(frozen=True)
class StructuredLogRecord:
    """A single structured log line.

    The first block of fields is the ODP-R7-001 acceptance contract; the
    remainder are the optional trace/diagnostic fields from ODP-SD-11 §3/§4.
    """

    service: str
    level: LogLevel
    message: str
    correlation_id: str
    actor: str = "system"
    resource: str = "-"
    result: str = "ok"
    error_code: str | None = None
    action: str | None = None
    retryable: bool | None = None
    job_id: str | None = None
    request_id: str | None = None
    event_id: str | None = None
    workflow_instance_id: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    model_version: str | None = None
    dataset_snapshot_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level.value,
            "service": self.service,
            "actor": self.actor,
            "correlation_id": self.correlation_id,
            "resource": self.resource,
            "action": self.action,
            "result": self.result,
            "error_code": self.error_code,
            "message": self.message,
        }
        optional = {
            "retryable": self.retryable,
            "job_id": self.job_id,
            "request_id": self.request_id,
            "event_id": self.event_id,
            "workflow_instance_id": self.workflow_instance_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "model_version": self.model_version,
            "dataset_snapshot_id": self.dataset_snapshot_id,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        if self.extra:
            payload["extra"] = redact(self.extra)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=False, default=str)


Sink = Callable[[StructuredLogRecord], None]


def stream_sink(stream: TextIO | None = None) -> Sink:
    """Sink that writes one JSON line per record to ``stream`` (stderr)."""

    target = stream if stream is not None else sys.stderr

    def _emit(record: StructuredLogRecord) -> None:
        target.write(record.to_json() + "\n")

    return _emit


class ListSink:
    """In-memory sink used by tests and the reliability harness."""

    def __init__(self) -> None:
        self.records: list[StructuredLogRecord] = []

    def __call__(self, record: StructuredLogRecord) -> None:
        self.records.append(record)

    @property
    def dicts(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.records]


class StructuredLogger:
    """Emits :class:`StructuredLogRecord` lines through a configurable sink.

    ``bind`` returns a child logger that carries default context (typically the
    ``correlation_id`` and ``actor`` for the current request/job) so call sites
    stay terse while every line keeps the full trace contract.
    """

    def __init__(
        self,
        service: str,
        *,
        sink: Sink | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self.service = service
        self._sink: Sink = sink if sink is not None else stream_sink()
        self._context: dict[str, Any] = dict(context or {})

    def bind(self, **context: Any) -> StructuredLogger:
        merged = {**self._context, **{k: v for k, v in context.items() if v is not None}}
        return StructuredLogger(self.service, sink=self._sink, context=merged)

    def log(
        self,
        level: LogLevel,
        message: str,
        *,
        correlation_id: str | None = None,
        **fields: Any,
    ) -> StructuredLogRecord:
        merged: dict[str, Any] = {**self._context}
        merged.update({k: v for k, v in fields.items() if v is not None})
        if correlation_id is not None:
            merged["correlation_id"] = correlation_id
        if "correlation_id" not in merged:
            raise ValueError("structured log requires a correlation_id")
        known = {f for f in StructuredLogRecord.__dataclass_fields__ if f not in {"service", "level", "message"}}
        record_fields = {k: v for k, v in merged.items() if k in known}
        extra = {**merged.get("extra", {}), **{k: v for k, v in merged.items() if k not in known and k != "extra"}}
        record_fields["extra"] = extra
        record = StructuredLogRecord(
            service=self.service,
            level=level,
            message=message,
            **record_fields,
        )
        self._sink(record)
        return record

    def info(self, message: str, **fields: Any) -> StructuredLogRecord:
        return self.log(LogLevel.INFO, message, **fields)

    def warning(self, message: str, **fields: Any) -> StructuredLogRecord:
        return self.log(LogLevel.WARNING, message, **fields)

    def error(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool = False,
        result: str = "error",
        **fields: Any,
    ) -> StructuredLogRecord:
        # ODP-SD-11 §4.2.3: error logs must carry error code, retryable and the
        # correlation id.
        return self.log(
            LogLevel.ERROR,
            message,
            error_code=error_code,
            retryable=retryable,
            result=result,
            **fields,
        )
