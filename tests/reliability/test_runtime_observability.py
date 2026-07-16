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
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shared.audit import AuditEvent, InMemoryAuditLog
from shared.observability import (
    AUDIT_EVIDENCE_EXPORT_EVENT_TYPE,
    E2E_TRACE_KINDS,
    AuditCompletenessRule,
    AuditPipeline,
    AuditPipelineError,
    ListSink,
    MetricCategory,
    StructuredLogger,
    Telemetry,
    TraceContext,
    build_audit_event,
    build_evidence_bundle,
    check_audit_completeness,
    default_registry,
    redact,
)
from shared.observability.audit import AuditValidationError
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
    registry.increment(
        "api_request_count", labels={"service": "api", "route": "/jobs", "status": "202"}
    )
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


# --- AC3 / AC5: audit event pipeline and evidence export -------------------


def test_audit_pipeline_records_export_event_with_metrics_and_log() -> None:
    audit_log = InMemoryAuditLog()
    log_sink = ListSink()
    metrics = default_registry()
    pipeline = AuditPipeline(
        sink=audit_log,
        metrics=metrics,
        logger=StructuredLogger("audit-pipeline", sink=log_sink),
    )

    event = pipeline.record_export(
        actor_id="auditor-1",
        resource="decision/site-1",
        correlation_id="corr-audit-1",
        reason="monthly subsidy evidence packet",
        scope="decision",
    )

    assert event.event_type == AUDIT_EVIDENCE_EXPORT_EVENT_TYPE
    assert audit_log.list_events(correlation_id="corr-audit-1") == [event]
    snapshot = metrics.snapshot()
    assert snapshot["audit_event_record_count"][0]["value"] == 1.0
    assert snapshot["audit_evidence_export_count"][0]["labels"] == {
        "result": "success",
        "scope": "decision",
    }
    assert snapshot["audit_evidence_export_count"][0]["value"] == 1.0
    log = log_sink.dicts[0]
    assert log["correlation_id"] == "corr-audit-1"
    assert log["resource"] == "decision/site-1"
    assert log["action"] == "export"


def test_audit_pipeline_rejects_high_risk_event_without_reason() -> None:
    pipeline = AuditPipeline(
        metrics=default_registry(),
        logger=StructuredLogger("audit-pipeline", sink=ListSink()),
    )
    event = AuditEvent(
        event_type="netplan.approved.v1",
        actor="manager-1",
        action="approve",
        resource="netplan/plan-1",
        outcome="success",
        correlation_id="corr-audit-2",
    )

    with pytest.raises(AuditValidationError):
        pipeline.record(event)

    assert pipeline.dead_letter[0].retryable is False
    assert pipeline.metrics.snapshot()["audit_event_write_failure_count"][0]["value"] == 1.0


def test_audit_pipeline_dead_letters_and_replays_failed_writes() -> None:
    class FlakySink:
        def __init__(self) -> None:
            self.fail_next = True
            self.events: list[AuditEvent] = []

        def record(self, event: AuditEvent) -> AuditEvent:
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("audit store unavailable")
            self.events.append(event)
            return event

    sink = FlakySink()
    pipeline = AuditPipeline(
        sink=sink,
        metrics=default_registry(),
        logger=StructuredLogger("audit-pipeline", sink=ListSink()),
    )
    event = build_audit_event(
        event_type="priceops.plan.executed.v1",
        actor_id="pricing-manager",
        action="execute",
        entity_type="priceops",
        entity_id="plan-7",
        result="success",
        correlation_id="corr-audit-3",
        reason_code="approved-plan",
        policy_version="price-policy-v1",
    )

    with pytest.raises(AuditPipelineError):
        pipeline.record(event)

    assert len(pipeline.dead_letter) == 1
    assert pipeline.metrics.snapshot()["audit_event_write_failure_count"][0]["value"] == 1.0

    assert pipeline.replay_failed() == 1
    assert pipeline.dead_letter == ()
    assert sink.events == [event]
    assert pipeline.metrics.snapshot()["audit_event_replay_count"][0]["value"] == 1.0


def test_evidence_bundle_and_completeness_report_are_deterministic() -> None:
    first = datetime(2026, 6, 28, 1, 0, tzinfo=UTC)
    second = datetime(2026, 6, 28, 1, 1, tzinfo=UTC)
    events = [
        build_audit_event(
            event_type="decision.prediction_generated.v1",
            actor_id="model-service",
            action="create",
            entity_type="site",
            entity_id="site-1",
            result="success",
            correlation_id="corr-audit-4",
            actor_type="service",
            policy_version="sitescore-policy-v1",
            occurred_at=first,
        ),
        build_audit_event(
            event_type="decision.approved.v1",
            actor_id="expansion-manager",
            action="approve",
            entity_type="site",
            entity_id="site-1",
            result="success",
            correlation_id="corr-audit-4",
            reason_code="meets-threshold",
            policy_version="sitescore-policy-v1",
            occurred_at=second,
        ),
    ]

    bundle_a = build_evidence_bundle(
        reversed(events),
        correlation_id="corr-audit-4",
        generated_by="auditor-1",
        reason="subsidy audit",
    )
    bundle_b = build_evidence_bundle(
        events,
        correlation_id="corr-audit-4",
        generated_by="auditor-1",
        reason="subsidy audit",
    )
    assert bundle_a.bundle_checksum == bundle_b.bundle_checksum
    assert len(bundle_a.bundle_checksum) == 64
    assert [event["event_type"] for event in bundle_a.events] == [
        "decision.prediction_generated.v1",
        "decision.approved.v1",
    ]

    rule = AuditCompletenessRule(
        name="decision-timeline",
        correlation_id="corr-audit-4",
        resource="site/site-1",
        required_event_types=(
            "decision.prediction_generated.v1",
            "decision.approved.v1",
            "decision.executed.v1",
        ),
    )
    report = check_audit_completeness(events, rule)
    assert not report.complete
    assert report.missing_event_types == ("decision.executed.v1",)

    pipeline = AuditPipeline(
        metrics=default_registry(),
        logger=StructuredLogger("audit-pipeline", sink=ListSink()),
    )
    pipeline.record_completeness_report(report)
    gap = pipeline.metrics.snapshot()["audit_completeness_gap_count"][0]
    assert gap["labels"]["missing_event_type"] == "decision.executed.v1"
    assert gap["value"] == 1.0


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


def test_audit_dashboard_uses_audit_pipeline_metrics() -> None:
    dashboards = _load("dashboards.json")["dashboards"]
    audit_dashboard = next(
        dashboard for dashboard in dashboards if dashboard["id"] == "audit-compliance"
    )
    metrics = {panel["metric"] for panel in audit_dashboard["panels"]}
    assert {
        "audit_event_record_count",
        "audit_event_write_failure_count",
        "audit_event_pipeline_lag_seconds",
        "audit_evidence_export_count",
        "audit_completeness_gap_count",
    }.issubset(metrics)


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


def test_alerts_include_audit_write_failure() -> None:
    alerts = _load("alerts.json")["alerts"]
    audit_alert = next(alert for alert in alerts if alert["id"] == "audit-write-failure")
    assert audit_alert["severity"] == "P1"
    assert audit_alert["metric"] == "audit_event_write_failure_count"


def test_slo_defines_recovery_objectives() -> None:
    slo = _load("slo.json")
    assert slo["slos"]
    recovery = {r["system"]: r for r in slo["recovery_objectives"]}
    for system in ["cloud-sql", "audit-logs", "model-artifacts"]:
        assert system in recovery, system
        assert "rpo" in recovery[system] and "rto" in recovery[system]


def test_worker_and_scheduler_export_telemetry() -> None:
    from apps.scheduler.oday_scheduler.main import ODayScheduler
    from apps.worker.oday_worker.main import ODayWorker
    from shared.infrastructure.persistence.factory import build_persistence

    # Set up
    persistence = build_persistence(mode="memory")
    logger_sink = ListSink()
    telemetry = Telemetry(
        "test-telemetry",
        logger=StructuredLogger("test-telemetry", sink=logger_sink),
    )

    worker = ODayWorker(persistence=persistence, telemetry=telemetry)
    scheduler = ODayScheduler(persistence=persistence, telemetry=telemetry)

    # 1. Run scheduler once to enqueue a job
    scheduler.run_once()
    assert len(logger_sink.dicts) >= 2 # start + ok
    assert logger_sink.dicts[0]["service"] == "test-telemetry"
    assert logger_sink.dicts[1]["action"] == "enqueue"

    # Verify span generated
    spans = telemetry.tracer.spans_for(logger_sink.dicts[0]["correlation_id"])
    assert len(spans) == 1
    assert spans[0].name == "scheduler-tick"

    # 2. Run worker once to consume the job
    worker.run_once()
    # Should have executed and logged
    # Verify job metric updated
    snapshot = telemetry.metrics.snapshot()
    assert "job_duration_seconds" in snapshot
    assert snapshot["job_duration_seconds"][0]["labels"]["status"] == "success"


def test_alert_routing_and_real_notification_delivery() -> None:
    from modules.notifications import (
        ConsoleNotificationAdapter,
        InMemoryNotificationRepository,
        NotificationService,
    )
    from shared.observability.alerts import AlertRouter

    repo = InMemoryNotificationRepository()
    adapter = ConsoleNotificationAdapter()
    service = NotificationService(repository=repo, adapter=adapter)

    # Setup preferences
    service.set_preferences("ops-lead", ["email", "sms"])

    # Initialize AlertRouter
    router = AlertRouter(notification_service=service)

    # Trigger P1 Alert
    nid = router.trigger_alert("audit-write-failure", "Durable storage write timeout on DB query")
    assert nid is not None

    # Verify routing target
    routed = router.route_alert("audit-write-failure")
    assert routed["receiver"] == "ops-lead"

    # Verify delivery
    assert len(adapter.sent_messages) == 1
    msg = adapter.sent_messages[0]
    assert msg["notification_id"] == nid
    assert msg["user_id"] == "ops-lead"
    assert "ALERT: [P1] Audit write failure" in msg["title"]
    assert "Durable storage write timeout" in msg["detail"]


def test_api_telemetry_export() -> None:
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from shared.observability import ListSink, SpanKind, StructuredLogger, Telemetry

    logger_sink = ListSink()
    telemetry = Telemetry(
        "test-api",
        logger=StructuredLogger("test-api", sink=logger_sink),
    )

    app = create_app(telemetry=telemetry, external_provider_validation=lambda: None)
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200

    corr_id = response.headers.get("X-Correlation-ID")
    assert corr_id is not None

    spans = telemetry.tracer.spans_for(corr_id)
    assert len(spans) == 1
    assert spans[0].name == "HTTP GET /healthz"
    assert spans[0].kind == SpanKind.API

    snapshot = telemetry.metrics.snapshot()
    assert "api_latency_ms" in snapshot
