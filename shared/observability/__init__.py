"""Shared observability primitives.

Baseline: ODP-SD-11 (Observability & Audit Design). Provides correlation,
structured logging, metrics, tracing and a Telemetry facade that an
application/worker stage uses to emit all three signals under one
``correlation_id``.
"""

from shared.observability.audit import (
    AUDIT_EVIDENCE_EXPORT_EVENT_TYPE,
    HIGH_RISK_AUDIT_ACTIONS,
    AuditCompletenessReport,
    AuditCompletenessRule,
    AuditEvidenceBundle,
    AuditPipeline,
    AuditPipelineError,
    AuditSink,
    AuditValidationError,
    DeadLetterAuditEvent,
    build_audit_event,
    build_evidence_bundle,
    check_audit_completeness,
)
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
    "AUDIT_EVIDENCE_EXPORT_EVENT_TYPE",
    "HIGH_RISK_AUDIT_ACTIONS",
    "AuditCompletenessReport",
    "AuditCompletenessRule",
    "AuditEvidenceBundle",
    "AuditPipeline",
    "AuditPipelineError",
    "AuditSink",
    "AuditValidationError",
    "DeadLetterAuditEvent",
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
    "build_audit_event",
    "build_evidence_bundle",
    "check_audit_completeness",
]
