"""Telemetry facade: one object that ties structured logging, metrics and
tracing to a shared trace context.

This is the glue an application/worker stage uses so that a single logical
operation emits a span, a latency metric and a structured log line that all
carry the same ``correlation_id`` (ODP-SD-11 §2.1 — every API/event/job/workflow
has a correlation id).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from shared.observability.logging import LogLevel, StructuredLogger
from shared.observability.metrics import MetricsRegistry, default_registry
from shared.observability.tracing import Span, SpanKind, TraceContext, Tracer


class Telemetry:
    """Bundles the three observability signals for one service."""

    def __init__(
        self,
        service: str,
        *,
        logger: StructuredLogger | None = None,
        metrics: MetricsRegistry | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.service = service
        self.logger = logger or StructuredLogger(service)
        self.metrics = metrics or default_registry()
        self.tracer = tracer or Tracer()

    @contextmanager
    def operation(
        self,
        name: str,
        kind: SpanKind,
        *,
        context: TraceContext,
        resource: str,
        action: str | None = None,
        parent: Span | None = None,
        latency_metric: str | None = "api_latency_ms",
        latency_labels: dict[str, str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[Span]:
        """Run a stage: open a span, time it, then emit a structured log line.

        On success the span status is OK and an INFO line is written; on error
        the span is marked ERROR and an ERROR line with the exception class as
        ``error_code`` is written before the exception re-raises.
        """

        scope = self.tracer.start_span(name, kind, context=context, parent=parent, attributes=attributes)
        with scope as span:
            try:
                yield span
            except Exception as exc:
                self.logger.error(
                    f"{name} failed",
                    correlation_id=context.correlation_id,
                    actor=context.actor_id,
                    resource=resource,
                    action=action or name,
                    error_code=type(exc).__name__,
                    retryable=False,
                    job_id=context.job_id,
                    model_version=context.model_version,
                    dataset_snapshot_id=context.dataset_snapshot_id,
                )
                raise
        if latency_metric is not None:
            self.metrics.observe(
                latency_metric,
                span.duration_ms,
                labels=latency_labels or {"service": self.service, "route": name},
            )
        self.logger.log(
            LogLevel.INFO,
            f"{name} ok",
            correlation_id=context.correlation_id,
            actor=context.actor_id,
            resource=resource,
            action=action or name,
            result="ok",
            job_id=context.job_id,
            model_version=context.model_version,
            dataset_snapshot_id=context.dataset_snapshot_id,
        )
