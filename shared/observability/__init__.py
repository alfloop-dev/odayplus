"""Shared observability primitives.

Baseline: ODP-SD-11 (Observability & Audit Design). Provides correlation,
structured logging, metrics, tracing and a Telemetry facade that an
application/worker stage uses to emit all three signals under one
``correlation_id``.
"""

from shared.observability.alerts import AlertRouter
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
    OVERFLOW_LABEL_VALUE,
    PLATFORM_METRICS,
    CardinalityPolicy,
    MetricCategory,
    MetricDefinition,
    MetricsRegistry,
    MetricType,
    ProductionMetricsExporter,
    default_registry,
    render_dashboard_provisioning,
)
from shared.observability.routes import (
    UNMATCHED_ROUTE_TEMPLATE,
    RouteTemplateResolver,
    compile_route_template,
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
from shared.observability.watch_window import (
    record_deployment_watch_window_status,
    verify_watch_window_receipt,
)

__all__ = [
    "AlertRouter",
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
    "CardinalityPolicy",
    "OVERFLOW_LABEL_VALUE",
    "RouteTemplateResolver",
    "UNMATCHED_ROUTE_TEMPLATE",
    "compile_route_template",
    "MetricCategory",
    "MetricDefinition",
    "MetricsRegistry",
    "MetricType",
    "PLATFORM_METRICS",
    "ProductionMetricsExporter",
    "default_registry",
    "render_dashboard_provisioning",
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
    "record_deployment_watch_window_status",
    "verify_watch_window_receipt",
]
