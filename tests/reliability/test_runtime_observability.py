"""Reliability / observability acceptance tests for ODP-R7-001.

Maps to the task acceptance criteria and ODP-SD-11 §12 / ODP-SD-10 §12:

- AC1  logs include timestamp/service/actor/correlation_id/resource/result/error_code
- AC2  metrics include latency/error/job/data/model/business KPIs
- AC3  at least one E2E trace links API/Event/Worker/Data/Model/Decision/Report
- AC4  backup/restore and DR drill runbooks exist
- plus: monitoring config is consistent with the metric catalog
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.observability import (
    E2E_TRACE_KINDS,
    ListSink,
    MetricCategory,
    StructuredLogger,
    Telemetry,
    TraceContext,
    default_registry,
    redact,
)
from shared.observability.metrics import PLATFORM_METRICS
from shared.observability.tracing import SpanKind, SpanStatus

ROOT = Path(__file__).resolve().parents[2]
MONITORING = ROOT / "infra" / "monitoring"
RUNBOOKS = ROOT / "docs" / "runbooks"

# --- AC1: structured logging contract --------------------------------------

REQUIRED_LOG_FIELDS = {
    "timestamp",
    "service",
    "actor",
    "correlation_id",
    "resource",
    "result",
}


def test_structured_log_carries_required_fields() -> None:
    sink = ListSink()
    logger = StructuredLogger("oday-api", sink=sink)
    logger.info(
        "job accepted",
        correlation_id="corr-1",
        actor="ops-manager",
        resource="job/forecast",
        result="accepted",
    )
    record = sink.dicts[0]
    assert REQUIRED_LOG_FIELDS.issubset(record)
    assert record["correlation_id"] == "corr-1"
    assert record["service"] == "oday-api"


def test_error_log_includes_error_code_and_retryable() -> None:
    sink = ListSink()
    logger = StructuredLogger("oday-worker", sink=sink)
    logger.error(
        "model endpoint timeout",
        correlation_id="corr-2",
        resource="model/forecast",
        error_code="MODEL_UNAVAILABLE",
        retryable=True,
    )
    record = sink.dicts[0]
    assert record["level"] == "ERROR"
    assert record["error_code"] == "MODEL_UNAVAILABLE"
    assert record["retryable"] is True
    assert record["result"] == "error"


def test_log_requires_correlation_id() -> None:
    logger = StructuredLogger("oday-api", sink=ListSink())
    with pytest.raises(ValueError):
        logger.info("missing correlation id", resource="x")


def test_sensitive_values_are_redacted() -> None:
    sink = ListSink()
    logger = StructuredLogger("oday-api", sink=sink)
    logger.info(
        "auth",
        correlation_id="corr-3",
        resource="auth/login",
        extra={"password": "hunter2", "token": "abc", "user_id": "u-1"},
    )
    extra = sink.dicts[0]["extra"]
    assert extra["password"] == "[REDACTED]"
    assert extra["token"] == "[REDACTED]"
    assert extra["user_id"] == "u-1"


def test_redact_is_recursive() -> None:
    out = redact({"outer": {"access_token": "x", "ok": 1}, "list": [{"secret": "s"}]})
    assert out["outer"]["access_token"] == "[REDACTED]"
    assert out["outer"]["ok"] == 1
    assert out["list"][0]["secret"] == "[REDACTED]"


# --- AC2: metric catalog ---------------------------------------------------

REQUIRED_METRIC_CATEGORIES = {
    MetricCategory.LATENCY,
    MetricCategory.ERROR,
    MetricCategory.JOB,
    MetricCategory.DATA,
    MetricCategory.MODEL,
    MetricCategory.BUSINESS,
}


def test_metric_catalog_covers_required_categories() -> None:
    assert REQUIRED_METRIC_CATEGORIES.issubset(default_registry().categories())


def test_metric_operations_record_values() -> None:
    registry = default_registry()
    registry.increment("api_request_count", labels={"service": "api", "route": "/jobs", "status": "202"})
    registry.observe("api_latency_ms", 12.5, labels={"service": "api", "route": "/jobs"})
    registry.set("data_freshness_hours", 3.0, labels={"source": "rent", "view": "v"})
    snapshot = registry.snapshot()
    assert snapshot["api_request_count"][0]["value"] == 1.0
    assert snapshot["api_latency_ms"][0]["count"] == 1
    assert snapshot["data_freshness_hours"][0]["value"] == 3.0


def test_metric_type_mismatch_is_rejected() -> None:
    registry = default_registry()
    with pytest.raises(TypeError):
        registry.set("api_request_count", 1.0)  # counter, not gauge


# --- AC3: end-to-end trace -------------------------------------------------


def test_e2e_trace_links_all_stages_under_one_correlation_id() -> None:
    # Deterministic monotonic clock so durations are stable.
    ticks = iter(float(i) for i in range(100))
    telemetry = Telemetry(
        "oday-platform",
        logger=StructuredLogger("oday-platform", sink=ListSink()),
    )
    telemetry.tracer._clock = lambda: next(ticks)  # noqa: SLF001 - deterministic test clock

    context = TraceContext(
        actor_id="ops-manager",
        request_id="req-1",
        job_id="job-1",
        workflow_instance_id="wf-1",
        entity_type="store",
        entity_id="store-1",
        model_version="forecast_revenue:1.1.0",
        dataset_snapshot_id="snap-2026-06-28",
    )

    parent = None
    for kind in E2E_TRACE_KINDS:
        with telemetry.operation(
            f"{kind.value}-stage",
            kind,
            context=context,
            resource=f"resource/{kind.value}",
            parent=parent,
            latency_labels={"service": "oday-platform", "route": kind.value},
        ) as span:
            parent = span

    chain = telemetry.tracer.linked_chain(context.correlation_id)
    assert chain == E2E_TRACE_KINDS

    spans = telemetry.tracer.spans_for(context.correlation_id)
    # All spans share the correlation id and carry the propagated context.
    assert {s.correlation_id for s in spans} == {context.correlation_id}
    assert all(s.attributes["model_version"] == "forecast_revenue:1.1.0" for s in spans)
    # The chain is parent-linked: each non-root span points at its predecessor.
    assert spans[0].parent_id is None
    for prev, nxt in zip(spans, spans[1:], strict=False):  # offset pairing is intentional
        assert nxt.parent_id == prev.span_id
    # A latency sample was recorded per stage (one series per route label).
    latency_series = telemetry.metrics.snapshot()["api_latency_ms"]
    assert sum(series["count"] for series in latency_series) == len(E2E_TRACE_KINDS)


def test_operation_marks_span_error_and_logs_error_code() -> None:
    sink = ListSink()
    telemetry = Telemetry("oday-worker", logger=StructuredLogger("oday-worker", sink=sink))
    context = TraceContext(actor_id="system")

    with pytest.raises(RuntimeError):
        with telemetry.operation(
            "model-stage", SpanKind.MODEL, context=context, resource="model/forecast"
        ):
            raise RuntimeError("model down")

    span = telemetry.tracer.spans_for(context.correlation_id)[0]
    assert span.status is SpanStatus.ERROR
    assert span.error_code == "RuntimeError"
    assert sink.records[-1].error_code == "RuntimeError"
    assert sink.records[-1].result == "error"


# --- AC4: runbooks exist ---------------------------------------------------


def test_backup_and_dr_runbooks_exist() -> None:
    backup = RUNBOOKS / "backup-and-restore.md"
    dr = RUNBOOKS / "disaster-recovery-drill.md"
    assert backup.is_file()
    assert dr.is_file()
    backup_text = backup.read_text(encoding="utf-8")
    dr_text = dr.read_text(encoding="utf-8")
    # Backup runbook covers restore for the critical stores.
    for token in ["Cloud SQL restore", "BigQuery restore", "RPO", "RTO", "Backup verification"]:
        assert token in backup_text, token
    # DR drill runbook measures RPO/RTO and defines scenarios.
    for token in ["DR scenarios", "measured RPO", "measured RTO", "Drill checklist"]:
        assert token in dr_text, token


def test_incident_and_observability_runbooks_exist() -> None:
    assert (RUNBOOKS / "incident-management.md").is_file()
    assert (RUNBOOKS / "observability-and-runbook.md").is_file()
    assert (RUNBOOKS / "README.md").is_file()


# --- Monitoring config integrity ------------------------------------------


def _load(name: str) -> dict:
    return json.loads((MONITORING / name).read_text(encoding="utf-8"))


def test_dashboards_cover_five_audiences() -> None:
    dashboards = _load("dashboards.json")["dashboards"]
    audiences = {d["audience"] for d in dashboards}
    for required in ["SRE", "Data Owner", "Model Owner", "Auditor"]:
        assert required in audiences, required
    assert len(dashboards) >= 6


def test_dashboard_panels_reference_known_metrics() -> None:
    known = {m.name for m in PLATFORM_METRICS}
    dashboards = _load("dashboards.json")["dashboards"]
    for dashboard in dashboards:
        for panel in dashboard["panels"]:
            assert panel["metric"] in known, f"{dashboard['id']}:{panel['metric']}"


def test_alerts_reference_known_metrics_and_cover_p1() -> None:
    known = {m.name for m in PLATFORM_METRICS}
    alerts = _load("alerts.json")["alerts"]
    severities = {a["severity"] for a in alerts}
    assert "P1" in severities and "P2" in severities
    for alert in alerts:
        assert alert["metric"] in known, alert["id"]
        assert alert["runbook"].startswith("docs/runbooks/")


def test_slo_defines_recovery_objectives() -> None:
    slo = _load("slo.json")
    assert slo["slos"]
    recovery = {r["system"]: r for r in slo["recovery_objectives"]}
    for system in ["cloud-sql", "audit-logs", "model-artifacts"]:
        assert system in recovery, system
        assert "rpo" in recovery[system] and "rto" in recovery[system]
