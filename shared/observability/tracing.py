"""Lightweight distributed-tracing primitives.

Source baseline: ODP-SD-11 §3 (Trace Context) and §6 (Trace 設計). The platform
has no OpenTelemetry SDK wired in yet, so this module provides an in-process
tracer with the same shape (spans, kinds, parent links, attributes) that an OTel
exporter can later consume.

ODP-AC-SD11-001 / ODP-R7-001 acceptance: at least one end-to-end trace must link
the API → Event → Worker → Data → Model → Decision → Report stages under a
single ``correlation_id``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from types import TracebackType
from typing import Any
from uuid import uuid4

from shared.observability.correlation import new_correlation_id


class SpanKind(StrEnum):
    """Stages that OpenTelemetry instrumentation should cover (ODP-SD-11 §6)."""

    API = "api"
    DB = "db"
    EVENT = "event"
    WORKER = "worker"
    DATA = "data"
    MODEL = "model"
    DECISION = "decision"
    REPORT = "report"
    EXTERNAL = "external"
    WORKFLOW = "workflow"


# The minimal chain that ODP-R7-001 acceptance requires a single correlation id
# to link end to end.
E2E_TRACE_KINDS: tuple[SpanKind, ...] = (
    SpanKind.API,
    SpanKind.EVENT,
    SpanKind.WORKER,
    SpanKind.DATA,
    SpanKind.MODEL,
    SpanKind.DECISION,
    SpanKind.REPORT,
)


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True)
class TraceContext:
    """The propagated trace context (ODP-SD-11 §3)."""

    correlation_id: str = field(default_factory=new_correlation_id)
    request_id: str | None = None
    event_id: str | None = None
    job_id: str | None = None
    workflow_instance_id: str | None = None
    actor_id: str = "system"
    entity_type: str | None = None
    entity_id: str | None = None
    model_version: str | None = None
    dataset_snapshot_id: str | None = None

    def with_(self, **changes: Any) -> TraceContext:
        from dataclasses import replace

        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Span:
    name: str
    kind: SpanKind
    correlation_id: str
    span_id: str = field(default_factory=lambda: uuid4().hex[:16])
    parent_id: str | None = None
    actor_id: str = "system"
    attributes: dict[str, Any] = field(default_factory=dict)
    status: SpanStatus = SpanStatus.OK
    error_code: str | None = None
    start: float = 0.0
    end: float | None = None

    @property
    def duration_ms(self) -> float:
        if self.end is None:
            return 0.0
        return (self.end - self.start) * 1000.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind.value,
            "correlation_id": self.correlation_id,
            "actor_id": self.actor_id,
            "status": self.status.value,
            "error_code": self.error_code,
            "duration_ms": round(self.duration_ms, 6),
            "attributes": self.attributes,
        }


class Tracer:
    """Records spans in memory; ``export`` yields OTel-shaped span dicts."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._spans: list[Span] = []

    @property
    def spans(self) -> list[Span]:
        return list(self._spans)

    def start_span(
        self,
        name: str,
        kind: SpanKind,
        *,
        context: TraceContext,
        parent: Span | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> _SpanScope:
        span = Span(
            name=name,
            kind=kind,
            correlation_id=context.correlation_id,
            parent_id=parent.span_id if parent is not None else None,
            actor_id=context.actor_id,
            attributes={**context.to_dict(), **(attributes or {})},
        )
        return _SpanScope(self, span)

    def _record(self, span: Span) -> None:
        self._spans.append(span)

    def spans_for(self, correlation_id: str) -> list[Span]:
        return [s for s in self._spans if s.correlation_id == correlation_id]

    def export(self) -> list[dict[str, Any]]:
        return [span.to_dict() for span in self._spans]

    def linked_chain(self, correlation_id: str) -> tuple[SpanKind, ...]:
        """Ordered span kinds recorded under one correlation id."""

        return tuple(s.kind for s in self.spans_for(correlation_id))


class _SpanScope:
    def __init__(self, tracer: Tracer, span: Span) -> None:
        self._tracer = tracer
        self.span = span

    def __enter__(self) -> Span:
        self.span.start = self._tracer._clock()
        return self.span

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self.span.end = self._tracer._clock()
        if exc is not None and self.span.status is SpanStatus.OK:
            self.span.status = SpanStatus.ERROR
            self.span.error_code = type(exc).__name__
        self._tracer._record(self.span)
        return False
