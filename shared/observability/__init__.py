"""Shared observability primitives.

Baseline: ODP-SD-11 (Observability & Audit Design). Provides correlation,
structured logging, metrics, tracing and a Telemetry facade that an
application/worker stage uses to emit all three signals under one
``correlation_id``.
"""

from shared.observability.correlation import (
    CORRELATION_ID_HEADER,
    CorrelationContext,
    new_correlation_id,
)
from shared.observability.logging import (
    ListSink,
    LogLevel,
    StructuredLogger,
    StructuredLogRecord,
    redact,
    stream_sink,
)
from shared.observability.metrics import (
    PLATFORM_METRICS,
    MetricCategory,
    MetricDefinition,
    MetricsRegistry,
    MetricType,
    default_registry,
)
from shared.observability.runtime import Telemetry
from shared.observability.tracing import (
    E2E_TRACE_KINDS,
    Span,
    SpanKind,
    SpanStatus,
    TraceContext,
    Tracer,
)

__all__ = [
    "CORRELATION_ID_HEADER",
    "CorrelationContext",
    "new_correlation_id",
    "ListSink",
    "LogLevel",
    "StructuredLogger",
    "StructuredLogRecord",
    "redact",
    "stream_sink",
    "MetricCategory",
    "MetricDefinition",
    "MetricsRegistry",
    "MetricType",
    "PLATFORM_METRICS",
    "default_registry",
    "Telemetry",
    "E2E_TRACE_KINDS",
    "Span",
    "SpanKind",
    "SpanStatus",
    "TraceContext",
    "Tracer",
]
