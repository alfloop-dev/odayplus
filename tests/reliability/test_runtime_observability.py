"""Reliability / observability acceptance tests for ODP-R7-001.

Maps to the task acceptance criteria and ODP-SD-11 §12 / ODP-SD-10 §12:

- AC1  logs include timestamp/service/actor/correlation_id/resource/result/error_code
- AC2  metrics include latency/error/job/data/model/business KPIs
- AC3  at least one E2E trace links API/Event/Worker/Data/Model/Decision/Report
- AC4  backup/restore and DR drill runbooks exist
- plus: monitoring config is consistent with the metric catalog
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from shared.observability.metrics import PLATFORM_METRICS, MetricsRegistry
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
    metrics = MetricsRegistry()
    for definition in PLATFORM_METRICS:
        metrics.register(definition)
    telemetry = Telemetry(
        "oday-platform",
        logger=StructuredLogger("oday-platform", sink=ListSink()),
        metrics=metrics,
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
    assert len(logger_sink.dicts) >= 2  # start + ok
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


def test_alert_routing_and_real_notification_delivery(monkeypatch: Any) -> None:
    import hashlib

    from modules.notifications import (
        InMemoryNotificationRepository,
        NotificationService,
        OnCallNotificationAdapter,
    )
    from shared.observability.alerts import AlertRouter

    valid_sha = "c" * 40
    provider_secret = "test-provider-secret-999"
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", provider_secret)
    repo = InMemoryNotificationRepository()

    def mock_transport(url: str, payload: dict) -> tuple[int, dict]:
        return (200, {"status": "ok", "delivered": True})

    adapter = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=mock_transport,
    )
    service = NotificationService(repository=repo, adapter=adapter)

    # Setup preferences for single channel webhook
    service.set_preferences("ops-lead", ["webhook"])

    # Initialize AlertRouter
    router = AlertRouter(notification_service=service)

    # Trigger P1 Alert
    nid = router.trigger_alert("audit-write-failure", "Durable storage write timeout on DB query")
    assert nid is not None

    # Verify routing target
    routed = router.route_alert("audit-write-failure")
    assert routed["receiver"] == "ops-lead"

    # Verify mock delivery classifies as TEST_ONLY
    assert len(adapter.delivery_receipts) == 1
    receipt = adapter.delivery_receipts[0]
    assert receipt["notification_id"] == nid
    assert receipt["oncall_route"] == "ops-lead"
    assert receipt["endpoint"] == "https://oncall-router.oday.plus/api/v1/alerts"
    assert receipt["http_status"] == 200
    assert receipt["status"] == "TEST_ONLY"
    assert receipt["response"] == {"status": "ok", "delivered": True}
    assert receipt["delivery_id"].startswith("del-")
    assert "ALERT: [P1] Audit write failure" in receipt["title"]
    assert "Durable storage write timeout" in receipt["detail"]

    # Verify authentic provider response carries provider_receipt_id and classifies as DELIVERED over real loopback network socket
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class AuthenticOnCallHTTPHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8")) if body else {}

            prov_rcpt = "prov-rcpt-9876543210"
            req_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
            req_hash = hashlib.sha256(req_bytes).hexdigest()
            sig_base = f"{provider_secret}:{prov_rcpt}:{req_hash}:{valid_sha}".encode()
            sig_token = f"sig-sha256-{hashlib.sha256(sig_base).hexdigest()}"
            rb_base = f"readback:{req_hash}".encode()
            rb_token = hashlib.sha256(rb_base).hexdigest()

            response_payload = {
                "status": "ok",
                "delivered": True,
                "provider_receipt_id": prov_rcpt,
                "provider_signature": sig_token,
                "provider_readback": rb_token,
            }
            response_bytes = json.dumps(response_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), AuthenticOnCallHTTPHandler)
    server_port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    repo2 = InMemoryNotificationRepository()
    authentic_adapter = OnCallNotificationAdapter(
        endpoint_url=f"http://127.0.0.1:{server_port}/api/v1/alerts",
        http_transport=None,
    )
    authentic_service = NotificationService(repository=repo2, adapter=authentic_adapter)
    authentic_service.set_preferences("ops-lead", ["webhook"])
    authentic_router = AlertRouter(notification_service=authentic_service)
    nid2 = authentic_router.trigger_alert("audit-write-failure", "Durable storage write timeout on DB query")

    assert len(authentic_adapter.delivery_receipts) == 1
    receipt2 = authentic_adapter.delivery_receipts[0]
    assert receipt2["notification_id"] == nid2
    # B1: Loopback HTTP sockets are test-only and yield TEST_ONLY, never DELIVERED
    assert receipt2["status"] == "TEST_ONLY"
    assert receipt2["provider_receipt_id"] == "prov-rcpt-9876543210"


def test_oncall_adapter_unreachable_route_fails(monkeypatch: Any) -> None:
    from modules.notifications import OnCallNotificationAdapter

    valid_sha = "d" * 40
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", "test-secret-123")
    # Attempt delivery to unreachable endpoint
    adapter = OnCallNotificationAdapter(endpoint_url="http://127.0.0.1:1")
    success, error_msg = adapter.send(
        notification_id="nid-unreachable",
        channel="webhook",
        user_id="ops-lead",
        title="P1 Alert",
        detail="Unreachable test",
    )

    assert success is False
    assert error_msg is not None and error_msg.startswith("HTTP 0:")
    assert len(adapter.delivery_receipts) == 1
    receipt = adapter.delivery_receipts[0]
    assert receipt["http_status"] == 0
    assert receipt["status"] == "FAILED"
    assert receipt["error"] == error_msg


def test_oncall_adapter_non_2xx_route_fails(monkeypatch: Any) -> None:
    from modules.notifications import OnCallNotificationAdapter

    valid_sha = "e" * 40
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", "test-secret-123")

    def mock_500_transport(url: str, payload: dict) -> tuple[int, str]:
        return (500, "Internal Server Error")

    adapter = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=mock_500_transport,
    )
    success, error_msg = adapter.send(
        notification_id="nid-500",
        channel="webhook",
        user_id="ops-lead",
        title="P1 Alert",
        detail="HTTP 500 test",
    )

    assert success is False
    assert error_msg == "HTTP 500: Internal Server Error"
    assert len(adapter.delivery_receipts) == 1
    receipt = adapter.delivery_receipts[0]
    assert receipt["http_status"] == 500
    assert receipt["status"] == "FAILED"


def test_unconfigured_route_fails_closed(tmp_path: Path) -> None:
    from modules.notifications import (
        InMemoryNotificationRepository,
        NotificationService,
        OnCallNotificationAdapter,
    )
    from shared.observability.alerts import AlertRouter

    repo = InMemoryNotificationRepository()

    def mock_200_transport(url: str, payload: dict) -> tuple[int, str]:
        return (200, "ok")

    adapter = OnCallNotificationAdapter(http_transport=mock_200_transport)
    service = NotificationService(repository=repo, adapter=adapter)

    # Test 1: Missing alerts config file fails closed
    missing_cfg_path = str(tmp_path / "non_existent_alerts.json")
    with pytest.raises(ValueError, match="Alert configuration file missing or not found"):
        AlertRouter(notification_service=service, alerts_cfg_path=missing_cfg_path)

    # Test 2: Unconfigured routing with default_receiver=None and empty routes
    router = AlertRouter(notification_service=service)
    router.config["routing"] = {"default_receiver": None, "routes": []}

    with pytest.raises(ValueError, match="is unconfigured. Fail-closed gate enforced."):
        router.route_alert("audit-write-failure")

    with pytest.raises(
        ValueError, match="Alert routing default_receiver or routes must be configured."
    ):
        router.validate_routing_config()

    # Test 3: Route with unmatched severity and missing default_receiver
    router.config["routing"] = {
        "default_receiver": None,
        "routes": [{"severity": "P3", "receiver": "oncall-engineer"}],
    }
    with pytest.raises(ValueError, match="is unconfigured. Fail-closed gate enforced."):
        router.route_alert("audit-write-failure")  # audit-write-failure is P1


def test_release_sha_dashboard_traceability_and_watch_window_receipt(tmp_path: Path, monkeypatch: Any) -> None:
    from datetime import timedelta

    from shared.observability.metrics import default_registry
    from shared.observability.watch_window import (
        compute_provider_watch_signature,
        record_deployment_watch_window_status,
        verify_watch_window_receipt,
    )

    # 1. Verify dashboards.json carries release_sha_traceability and watch-window panels
    dashboards_path = MONITORING / "dashboards.json"
    assert dashboards_path.exists()
    cfg = json.loads(dashboards_path.read_text(encoding="utf-8"))

    traceability = cfg.get("release_sha_traceability", {})
    assert traceability.get("enabled") is True
    assert traceability.get("metric_label") == "release_sha"
    assert traceability.get("exact_sha_binding") == "${RELEASE_SHA}"
    assert traceability.get("watch_window_minutes") == 15
    assert traceability.get("receipt_required") is True
    assert traceability.get("receipt_artifact_path") == "docs/evidence/watch_window_receipt.json"

    platform_health = next(d for d in cfg["dashboards"] if d["id"] == "platform-health")
    panels = {p["title"]: p for p in platform_health["panels"]}
    assert "Release SHA traceability" in panels
    assert "Release watch-window receipt" in panels
    assert panels["Release SHA traceability"]["label_filter"] == "release_sha=${RELEASE_SHA}"
    assert panels["Release watch-window receipt"]["label_filter"] == "release_sha=${RELEASE_SHA}"

    # 2. Record watch-window status and verify receipt creation & metric emission
    receipt_file = tmp_path / "watch_window_receipt.json"
    test_sha = "10c620969a90627e4a67053a4708658f99faa07f"
    registry = default_registry()
    monitoring_route = "https://monitoring.googleapis.com/v3"

    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    monkeypatch.setenv("MONITORING_PROVIDER_SECRET", "test-sec-750")

    def mock_query_transport(
        method: str,
        url: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        p_dict = payload or {}
        pr_dict = params or {}
        g_proj = p_dict.get("gcp_project") or pr_dict.get("gcp_project") or "alfaloop-data-project"
        r_sha = p_dict.get("release_sha") or pr_dict.get("release_sha") or test_sha
        s_iso = pr_dict.get("interval.startTime", start_dt.isoformat())
        e_iso = pr_dict.get("interval.endTime", end_dt.isoformat())
        ts = [
            {
                "metric": {
                    "type": "custom.googleapis.com/api_error_count",
                    "labels": {"release_sha": r_sha},
                },
                "resource": {"type": "global", "labels": {"project_id": g_proj}},
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 0.0},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 0.0},
                    },
                ],
            },
            {
                "metric": {
                    "type": "custom.googleapis.com/api_latency_ms",
                    "labels": {"release_sha": r_sha},
                },
                "resource": {"type": "global", "labels": {"project_id": g_proj}},
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 12.5},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 14.2},
                    },
                ],
            },
        ]
        raw_resp = {"gcp_project": g_proj, "release_sha": r_sha, "timeSeries": ts}
        proof_hash = hashlib.sha256(json.dumps(raw_resp, sort_keys=True).encode("utf-8")).hexdigest()
        prov_rcpt = f"prov-query-rcpt-{r_sha[:8]}"
        sig_token, rb_token = compute_provider_watch_signature(
            provider_secret="test-sec-750",
            provider_receipt_id=prov_rcpt,
            gcp_project=g_proj,
            release_sha=r_sha,
            start_iso=s_iso,
            end_iso=e_iso,
            proof_hash=proof_hash,
        )
        raw_resp["provider_receipt_id"] = prov_rcpt
        raw_resp["provider_signature"] = sig_token
        raw_resp["provider_readback_identity"] = rb_token
        return 200, raw_resp

    receipt = record_deployment_watch_window_status(
        release_sha=test_sha,
        status=1,
        start_time=start_dt,
        end_time=end_dt,
        registry=registry,
        receipt_path=receipt_file,
        gcp_project="alfaloop-data-project",
        provider_route=monitoring_route,
        query_transport=mock_query_transport,
    )

    assert receipt["release_sha"] == test_sha
    assert receipt["status"] == "WATCH_PASSED"
    assert receipt["status_code"] == 1
    assert receipt_file.exists()

    snapshot = registry.snapshot()
    assert "deployment_watch_window_status" in snapshot
    series = snapshot["deployment_watch_window_status"][0]
    assert series["value"] == 1.0
    assert series["labels"] == {"release_sha": test_sha, "status": "WATCH_PASSED"}

    # 3. Verify watch-window receipt artifact validation
    verified = verify_watch_window_receipt(expected_release_sha=test_sha, receipt_path=receipt_file)
    assert verified["release_sha"] == test_sha
    assert verified["status"] == "WATCH_PASSED"


def test_watch_window_receipt_negative_cases(tmp_path: Path, monkeypatch: Any) -> None:
    from datetime import timedelta

    from shared.observability.watch_window import (
        compute_provider_watch_signature,
        record_deployment_watch_window_status,
        verify_watch_window_receipt,
    )

    receipt_file = tmp_path / "watch_window_receipt.json"
    valid_sha_1 = "10c620969a90627e4a67053a4708658f99faa07f"
    valid_sha_2 = "20c620969a90627e4a67053a4708658f99faa07f"
    monitoring_route = "https://monitoring.googleapis.com/v3"
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    monkeypatch.setenv("MONITORING_PROVIDER_SECRET", "test-sec-880")

    def mock_query_transport(
        method: str,
        url: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        pr_dict = params or {}
        s_iso = pr_dict.get("interval.startTime", start_dt.isoformat())
        e_iso = pr_dict.get("interval.endTime", end_dt.isoformat())
        ts = [
            {
                "metric": {
                    "type": "custom.googleapis.com/api_error_count",
                    "labels": {"release_sha": valid_sha_1},
                },
                "resource": {
                    "type": "global",
                    "labels": {"project_id": "alfaloop-data-project"},
                },
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 0.0},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 0.0},
                    },
                ],
            },
            {
                "metric": {
                    "type": "custom.googleapis.com/api_latency_ms",
                    "labels": {"release_sha": valid_sha_1},
                },
                "resource": {
                    "type": "global",
                    "labels": {"project_id": "alfaloop-data-project"},
                },
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 12.5},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 14.2},
                    },
                ],
            },
        ]
        raw_resp = {"gcp_project": "alfaloop-data-project", "release_sha": valid_sha_1, "timeSeries": ts}
        proof_hash = hashlib.sha256(json.dumps(raw_resp, sort_keys=True).encode("utf-8")).hexdigest()
        prov_rcpt = "prov-rcpt-10c62096"
        sig_token, rb_token = compute_provider_watch_signature(
            provider_secret="test-sec-880",
            provider_receipt_id=prov_rcpt,
            gcp_project="alfaloop-data-project",
            release_sha=valid_sha_1,
            start_iso=s_iso,
            end_iso=e_iso,
            proof_hash=proof_hash,
        )
        raw_resp["provider_receipt_id"] = prov_rcpt
        raw_resp["provider_signature"] = sig_token
        raw_resp["provider_readback_identity"] = rb_token
        return 200, raw_resp

    # Case 1: Absent watch receipt artifact
    with pytest.raises(FileNotFoundError, match="Watch-window receipt artifact absent"):
        verify_watch_window_receipt(expected_release_sha=valid_sha_1, receipt_path=receipt_file)

    # Case 2: Release SHA mismatch
    record_deployment_watch_window_status(
        release_sha=valid_sha_1,
        status=1,
        start_time=start_dt,
        end_time=end_dt,
        receipt_path=receipt_file,
        gcp_project="alfaloop-data-project",
        provider_route=monitoring_route,
        query_transport=mock_query_transport,
    )
    with pytest.raises(ValueError, match="Release SHA mismatch"):
        verify_watch_window_receipt(expected_release_sha=valid_sha_2, receipt_path=receipt_file)

    # Case 3: Failed watch receipt (status_code = 0 / WATCH_FAILED)
    def mock_query_fail_transport(
        method: str,
        url: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        pr_dict = params or {}
        s_iso = pr_dict.get("interval.startTime", start_dt.isoformat())
        e_iso = pr_dict.get("interval.endTime", end_dt.isoformat())
        ts = [
            {
                "metric": {
                    "type": "custom.googleapis.com/api_error_count",
                    "labels": {"release_sha": valid_sha_1},
                },
                "resource": {
                    "type": "global",
                    "labels": {"project_id": "alfaloop-data-project"},
                },
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 0.0},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 5.0},
                    },
                ],
            }
        ]
        raw_resp = {"gcp_project": "alfaloop-data-project", "release_sha": valid_sha_1, "timeSeries": ts}
        proof_hash = hashlib.sha256(json.dumps(raw_resp, sort_keys=True).encode("utf-8")).hexdigest()
        prov_rcpt = "prov-rcpt-10c62096"
        sig_token, rb_token = compute_provider_watch_signature(
            provider_secret="test-sec-880",
            provider_receipt_id=prov_rcpt,
            gcp_project="alfaloop-data-project",
            release_sha=valid_sha_1,
            start_iso=s_iso,
            end_iso=e_iso,
            proof_hash=proof_hash,
        )
        raw_resp["provider_receipt_id"] = prov_rcpt
        raw_resp["provider_signature"] = sig_token
        raw_resp["provider_readback_identity"] = rb_token
        return 200, raw_resp

    record_deployment_watch_window_status(
        release_sha=valid_sha_1,
        status=0,
        start_time=start_dt,
        end_time=end_dt,
        receipt_path=receipt_file,
        gcp_project="alfaloop-data-project",
        provider_route=monitoring_route,
        query_transport=mock_query_fail_transport,
    )
    with pytest.raises(ValueError, match="Watch-window verification failed"):
        verify_watch_window_receipt(expected_release_sha=valid_sha_1, receipt_path=receipt_file)

    # Case 4: Short/forged SHA rejected during recording
    with pytest.raises(ValueError, match="exact full 40-character hexadecimal string"):
        record_deployment_watch_window_status(
            release_sha="short-sha-123",
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
        )

    # Case 5: Sub-15-minute receipt rejected during recording
    short_start = datetime.now(UTC) - timedelta(minutes=5)
    with pytest.raises(ValueError, match="less than the required 15-minute minimum"):
        record_deployment_watch_window_status(
            release_sha=valid_sha_1,
            status=1,
            start_time=short_start,
            end_time=end_dt,
            receipt_path=receipt_file,
        )


def test_watch_window_binding_mismatch_mutations(tmp_path: Path, monkeypatch: Any) -> None:
    from datetime import timedelta

    from shared.observability.watch_window import (
        compute_provider_watch_signature,
        record_deployment_watch_window_status,
    )

    monkeypatch.setenv("MONITORING_PROVIDER_SECRET", "test-sec-880")

    receipt_file = tmp_path / "watch_window_receipt.json"
    valid_sha = "10c620969a90627e4a67053a4708658f99faa07f"
    monitoring_route = "https://monitoring.googleapis.com/v3"
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    # Mutation 1: Project mismatch in provider query response
    def mismatch_project_transport(
        method: str,
        url: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        return 200, {
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {"labels": {"project_id": "wrong-gcp-project"}},
                    "points": [
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 12.5},
                        }
                    ],
                }
            ]
        }

    with pytest.raises(ValueError, match="project mismatch"):
        record_deployment_watch_window_status(
            release_sha=valid_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            query_transport=mismatch_project_transport,
        )

    # Mutation 2: Release SHA mismatch in provider query response
    def mismatch_sha_transport(
        method: str,
        url: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        return 200, {
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": "20c620969a90627e4a67053a4708658f99faa07f"},
                    },
                    "resource": {"labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 12.5},
                        }
                    ],
                }
            ]
        }

    with pytest.raises(ValueError, match="release_sha mismatch"):
        record_deployment_watch_window_status(
            release_sha=valid_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            query_transport=mismatch_sha_transport,
        )

    # Mutation 3: Empty timeSeries list in provider query response (Exploit B mitigation check)
    def empty_data_transport(
        method: str,
        url: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        return 200, {"timeSeries": []}

    with pytest.raises(ValueError, match="returned zero timeSeries data"):
        record_deployment_watch_window_status(
            release_sha=valid_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            query_transport=empty_data_transport,
        )

    # Mutation 4: Error count > 0 in provider response when requested status is 1
    def error_count_transport(
        method: str,
        url: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        return 200, {
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_error_count",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {"labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 3.0}},
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {"labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 10.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 10.0}},
                    ],
                },
            ]
        }

    with pytest.raises(ValueError, match="metric values indicate health/error failure"):
        record_deployment_watch_window_status(
            release_sha=valid_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            query_transport=error_count_transport,
        )

    # Mutation 5: Circular status metric returned in watch window readback
    def circular_metric_transport(
        method: str,
        url: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        return 200, {
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/deployment_watch_window_status",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {"labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 1.0}}
                    ],
                }
            ]
        }

    with pytest.raises(ValueError, match="deployment_watch_window_status metric type"):
        record_deployment_watch_window_status(
            release_sha=valid_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            query_transport=circular_metric_transport,
        )

    # Mutation 6: Point timestamp outside requested watch window
    def out_of_window_transport(
        method: str,
        url: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        old_ts = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
        return 200, {
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {"labels": {"project_id": "alfaloop-data-project"}},
                    "points": [{"interval": {"endTime": old_ts}, "value": {"doubleValue": 12.5}}],
                }
            ]
        }

    with pytest.raises(ValueError, match="lies outside requested watch window"):
        record_deployment_watch_window_status(
            release_sha=valid_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            query_transport=out_of_window_transport,
        )

    # Mutation 7: Tampered receipt artifact fails canonical SHA-256 integrity verification
    def valid_latency_transport(
        method: str,
        url: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> tuple[int, dict]:
        pr_dict = params or {}
        s_iso = pr_dict.get("interval.startTime", start_dt.isoformat())
        e_iso = pr_dict.get("interval.endTime", end_dt.isoformat())
        ts = [
            {
                "metric": {
                    "type": "custom.googleapis.com/api_error_count",
                    "labels": {"release_sha": valid_sha},
                },
                "resource": {"labels": {"project_id": "alfaloop-data-project"}},
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 0.0},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 0.0},
                    },
                ],
            },
            {
                "metric": {
                    "type": "custom.googleapis.com/api_latency_ms",
                    "labels": {"release_sha": valid_sha},
                },
                "resource": {"labels": {"project_id": "alfaloop-data-project"}},
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 10.0},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 10.0},
                    },
                ],
            },
        ]
        raw_resp = {"gcp_project": "alfaloop-data-project", "release_sha": valid_sha, "timeSeries": ts}
        proof_hash = hashlib.sha256(json.dumps(raw_resp, sort_keys=True).encode("utf-8")).hexdigest()
        prov_rcpt = "prov-rcpt-10c62096"
        sig_token, rb_token = compute_provider_watch_signature(
            provider_secret="test-sec-880",
            provider_receipt_id=prov_rcpt,
            gcp_project="alfaloop-data-project",
            release_sha=valid_sha,
            start_iso=s_iso,
            end_iso=e_iso,
            proof_hash=proof_hash,
        )
        raw_resp["provider_receipt_id"] = prov_rcpt
        raw_resp["provider_signature"] = sig_token
        raw_resp["provider_readback_identity"] = rb_token
        return 200, raw_resp

    record_deployment_watch_window_status(
        release_sha=valid_sha,
        status=1,
        start_time=start_dt,
        end_time=end_dt,
        receipt_path=receipt_file,
        gcp_project="alfaloop-data-project",
        provider_route=monitoring_route,
        query_transport=valid_latency_transport,
    )

    # Tamper with receipt file
    receipt_content = json.loads(receipt_file.read_text(encoding="utf-8"))
    receipt_content["verified_points_count"] = 999  # Tamper point count
    receipt_file.write_text(json.dumps(receipt_content, indent=2), encoding="utf-8")

    from shared.observability.watch_window import verify_watch_window_receipt

    with pytest.raises(ValueError, match="Tampered receipt rejected|integrity check failed"):
        verify_watch_window_receipt(expected_release_sha=valid_sha, receipt_path=receipt_file)

    # Mutation 8: Passing on-call alert route as monitoring provider_route fails closed
    with pytest.raises(ValueError, match="On-call alert route|ONCALL_ENDPOINT_URL"):
        record_deployment_watch_window_status(
            release_sha=valid_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://oncall-router.oday.plus/api/v1/alerts",
        )


def test_production_metrics_exporter_and_dashboard_provisioning() -> None:
    from shared.observability import (
        ProductionMetricsExporter,
        default_registry,
        render_dashboard_provisioning,
    )

    registry = default_registry()
    registry.increment(
        "api_request_count", labels={"service": "api", "route": "/jobs", "status": "200"}
    )
    registry.set("dlq_message_count", 0.0, labels={"topic": "assisted-listing-intake.dlq"})
    registry.set("netplan_plan_adoption_rate", 0.95)

    test_sha = "b28a6b6d335293ecb51a72dff3700838e196129c"
    monitoring_route = "https://monitoring.googleapis.com/v3"

    mock_posted_series: list[dict] = []

    def mock_success_transport(
        method: str,
        url: str | None = None,
        params: dict | None = None,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        p_dict = payload or {}
        pr_dict = params or {}
        g_proj = p_dict.get("gcp_project") or pr_dict.get("gcp_project") or "alfaloop-data-project"
        r_sha = p_dict.get("release_sha") or pr_dict.get("release_sha") or test_sha

        if "timeSeries" in (url or ""):
            if method == "POST":
                if isinstance(p_dict, dict) and "timeSeries" in p_dict:
                    mock_posted_series.clear()
                    mock_posted_series.extend(p_dict["timeSeries"])
                return 200, {}
            elif method == "GET":
                ts_return = mock_posted_series if mock_posted_series else [
                    {
                        "metric": {
                            "type": "custom.googleapis.com/api_request_count",
                            "labels": {"release_sha": r_sha, "service": "api", "route": "/jobs", "status": "200"},
                        },
                        "resource": {"type": "global", "labels": {"project_id": g_proj}},
                        "points": [
                            {
                                "interval": {"endTime": datetime.now(UTC).isoformat()},
                                "value": {"doubleValue": 1.0},
                            }
                        ],
                    }
                ]
                return 200, {
                    "gcp_project": g_proj,
                    "release_sha": r_sha,
                    "provider_receipt_id": f"prov-rcpt-{r_sha[:8]}",
                    "provider_signature": f"sig-sha256-{r_sha[:16]}",
                    "provider_readback_identity": f"readback-identity-{g_proj}-{r_sha[:8]}",
                    "timeSeries": ts_return,
                }
        elif "dashboards" in (url or ""):
            if method == "POST":
                return 200, {"name": f"projects/{g_proj}/dashboards/platform-health"}
            elif method == "GET":
                return 200, {
                    "name": f"projects/{g_proj}/dashboards/platform-health",
                    "receipt_id": f"gcp-dash-{test_sha[:12]}",
                    "readback_status": "PROVISIONED",
                    "gcp_project": g_proj,
                    "release_sha": r_sha,
                }
        return 200, {"status": "ok"}

    # 1. Test ProductionMetricsExporter binds release_sha and performs provider write/readback
    exporter = ProductionMetricsExporter(
        release_sha=test_sha,
        registry=registry,
        gcp_project="alfaloop-data-project",
        provider_route=monitoring_route,
        http_transport=mock_success_transport,
    )
    exported = exporter.export_metrics()

    assert exported["release_sha"] == test_sha
    assert str(exported["export_receipt_id"]).startswith("gcp-cm-readback-")
    assert exported["readback_status"] == "SUCCESS"
    assert exported["provider_route_identity"] == monitoring_route
    assert len(exported["monitoring_backend_resource_ids"]) > 0
    assert "api_request_count" in exported["metrics"]
    assert exported["metrics"]["api_request_count"][0]["labels"]["release_sha"] == test_sha

    # Fail closed on missing GCP_PROJECT
    with pytest.raises(
        ValueError, match="GCP_PROJECT environment variable is missing or unconfigured"
    ):
        ProductionMetricsExporter(
            release_sha=test_sha,
            gcp_project="",
            provider_route=monitoring_route,
            http_transport=mock_success_transport,
        ).export_metrics()

    # Fail closed on passing on-call route as monitoring route
    with pytest.raises(ValueError, match="On-call alert route|ONCALL_ENDPOINT_URL"):
        ProductionMetricsExporter(
            release_sha=test_sha,
            gcp_project="alfaloop-data-project",
            provider_route="https://oncall-router.oday.plus/api/v1/alerts",
            http_transport=mock_success_transport,
        ).export_metrics()

    # Exploit A mutation test: GET returns 200 {"name": "anything-attacker-controlled"} without timeSeries
    def mock_attacker_name_transport(
        method: str,
        url: str | None = None,
        params: dict | None = None,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        if method == "POST":
            return 200, {}
        return 200, {"name": "anything-attacker-controlled"}

    with pytest.raises(RuntimeError, match="returned zero timeSeries"):
        ProductionMetricsExporter(
            release_sha=test_sha,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            http_transport=mock_attacker_name_transport,
        ).export_metrics()

    # Exploit A mutation test: GET returns 200 {"timeSeries": []}
    def mock_empty_series_transport(
        method: str,
        url: str | None = None,
        params: dict | None = None,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        if method == "POST":
            return 200, {}
        return 200, {"timeSeries": []}

    with pytest.raises(RuntimeError, match="returned zero timeSeries"):
        ProductionMetricsExporter(
            release_sha=test_sha,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            http_transport=mock_empty_series_transport,
        ).export_metrics()

    # Fail closed on provider 500 error rejection
    def mock_500_transport(
        method: str,
        url: str | None = None,
        params: dict | None = None,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        return 500, {"error": "backend database failure"}

    with pytest.raises(
        RuntimeError, match="Cloud Monitoring / metrics provider write rejected with HTTP 500"
    ):
        ProductionMetricsExporter(
            release_sha=test_sha,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            http_transport=mock_500_transport,
        ).export_metrics()

    # 2. Test render_dashboard_provisioning performs runtime substitution, SLO owner check, and provider adapter call
    provisioned = render_dashboard_provisioning(
        release_sha=test_sha,
        gcp_project="alfaloop-data-project",
        provider_route=monitoring_route,
        http_transport=mock_success_transport,
    )

    traceability = provisioned["release_sha_traceability"]
    assert traceability["exact_sha_binding"] == test_sha
    assert traceability["slo_owner"] == "SRE Lead / Platform Operations"

    readback = provisioned["provisioning_readback"]
    assert readback["readback_status"] == "PROVISIONED"
    assert readback["receipt_id"] == f"gcp-dash-{test_sha[:12]}"
    assert readback["exact_sha_binding"] == test_sha
    assert "platform-health" in readback["dashboard_resource_ids"]

    # Dashboard provisioning fails closed on missing authentic provider receipt ID
    def mock_no_receipt_transport(
        method: str,
        url: str | None = None,
        params: dict | None = None,
        payload: dict | None = None,
        headers: dict | None = None,
    ) -> tuple[int, dict]:
        if method == "POST":
            return 200, {}
        return 200, {"readback_status": "SUCCESS"}

    with pytest.raises(
        ValueError, match="Dashboard provider response missing authentic provider-issued receipt_id"
    ):
        render_dashboard_provisioning(
            release_sha=test_sha,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            http_transport=mock_no_receipt_transport,
        )

    # Dashboard provisioning fails closed on provider rejection
    with pytest.raises(ValueError, match="Dashboard provider rejected provisioning with HTTP 500"):
        render_dashboard_provisioning(
            release_sha=test_sha,
            gcp_project="alfaloop-data-project",
            provider_route=monitoring_route,
            http_transport=mock_500_transport,
        )


def test_platform_observability_endpoints_fail_closed_without_full_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from shared.infrastructure.persistence.assisted_listing_intake import DurableAssistedIntakeStore
    from shared.infrastructure.persistence.factory import _memory_bundle

    class ProbeEngine:
        is_production = True
        dialect = "postgresql"

        def query(self, *a, **kw):
            return []

        def query_one(self, *a, **kw):
            return {"ready": 1}

    engine = ProbeEngine()
    bundle = replace(
        _memory_bundle(),
        mode="postgresql",
        engine=engine,
        assisted_intake_store=DurableAssistedIntakeStore(SimpleNamespace(engine=engine)),
    )

    monkeypatch.setenv("ODP_REQUIRE_LIVE_DATA", "true")
    monkeypatch.setenv("ODP_PERSISTENCE", "postgresql")
    monkeypatch.delenv("ODAY_RELEASE_SHA", raising=False)
    monkeypatch.delenv("ODP_RELEASE_COMMIT_SHA", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.delenv("COMMIT_SHA", raising=False)

    app = create_app(persistence=bundle, external_provider_validation=lambda: None)
    client = TestClient(app)

    res_metrics = client.get("/platform/metrics/export")
    assert res_metrics.status_code == 503
    assert "invalid_release_sha" in json.dumps(res_metrics.json())

    res_dash = client.get("/platform/dashboards/provisioned")
    assert res_dash.status_code == 503
    assert "invalid_release_sha" in json.dumps(res_dash.json())


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


def test_adc_token_resolution_and_env_bearer_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.observability.metrics import get_gcp_adc_token

    monkeypatch.setenv("GCP_AUTH_TOKEN", "fake-self-attested-bearer-token-123")
    monkeypatch.setenv("GOOGLE_AUTH_TOKEN", "fake-bearer-456")
    token = get_gcp_adc_token()
    assert token != "fake-self-attested-bearer-token-123"
    assert token != "fake-bearer-456"


def test_exporter_and_dashboard_http_method_contract_and_body_structures() -> None:
    from shared.observability import (
        ProductionMetricsExporter,
        default_registry,
        render_dashboard_provisioning,
    )

    registry = default_registry()
    registry.increment(
        "api_request_count", labels={"service": "api", "route": "/jobs", "status": "200"}
    )
    test_sha = "b28a6b6d335293ecb51a72dff3700838e196129c"
    gcp_proj = "alfaloop-data-project"
    monitoring_route = "https://monitoring.googleapis.com/v3"

    calls: list[dict[str, Any]] = []

    def mock_contract_transport(
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        calls.append({"method": method, "url": url, "params": params, "payload": payload})
        if "timeSeries" in url:
            if method == "POST":
                return 200, {
                    "export_receipt_id": f"gcp-cm-export-{test_sha[:12]}",
                    "readback_status": "SUCCESS",
                }
            elif method == "GET":
                return 200, {
                    "timeSeries": [
                        {
                            "metric": {
                                "type": "custom.googleapis.com/api_request_count",
                                "labels": {"release_sha": test_sha, "service": "api", "route": "/jobs", "status": "200"},
                            },
                            "resource": {"type": "global", "labels": {"project_id": gcp_proj}},
                            "points": [
                                {
                                    "interval": {"endTime": datetime.now(UTC).isoformat()},
                                    "value": {"doubleValue": 1.0},
                                }
                            ],
                        }
                    ],
                    "export_receipt_id": f"gcp-cm-export-{test_sha[:12]}",
                    "readback_status": "SUCCESS",
                }
        elif "dashboards" in url:
            if method == "POST":
                return 200, {
                    "name": f"projects/{gcp_proj}/dashboards/platform-health",
                    "receipt_id": f"gcp-dash-{test_sha[:12]}",
                }
            elif method == "GET":
                return 200, {
                    "name": f"projects/{gcp_proj}/dashboards/platform-health",
                    "receipt_id": f"gcp-dash-{test_sha[:12]}",
                    "readback_status": "PROVISIONED",
                    "gcp_project": gcp_proj,
                    "release_sha": test_sha,
                }
        return 200, {"status": "ok"}

    exporter = ProductionMetricsExporter(
        release_sha=test_sha,
        registry=registry,
        gcp_project=gcp_proj,
        provider_route=monitoring_route,
        http_transport=mock_contract_transport,
    )
    exported = exporter.export_metrics()

    assert exported["release_sha"] == test_sha
    assert len(calls) == 2
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"] == f"{monitoring_route}/projects/{gcp_proj}/timeSeries"
    assert "timeSeries" in calls[0]["payload"]
    assert "gcp_project" not in calls[0]["payload"]  # Native Google API body only!
    assert calls[1]["method"] == "GET"
    assert calls[1]["params"]["filter"] == f'metric.labels.release_sha="{test_sha}"'
    assert "interval.startTime" in calls[1]["params"]
    assert "interval.endTime" in calls[1]["params"]

    calls.clear()
    provisioned = render_dashboard_provisioning(
        release_sha=test_sha,
        gcp_project=gcp_proj,
        provider_route=monitoring_route,
        http_transport=mock_contract_transport,
    )

    assert provisioned["provisioning_readback"]["readback_status"] == "PROVISIONED"
    assert len(calls) >= 2
    assert calls[0]["method"] == "POST"
    assert "dashboards" not in calls[0]["payload"]  # Single Dashboard body, not wrapper array!
    assert calls[-1]["method"] == "GET"
    assert f"projects/{gcp_proj}/dashboards/platform-health" in calls[-1]["url"]


def test_exporter_fails_on_post_only_transport() -> None:
    from shared.observability import ProductionMetricsExporter, default_registry

    registry = default_registry()
    test_sha = "b28a6b6d335293ecb51a72dff3700838e196129c"

    def post_only_transport(
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        if method == "POST":
            return 200, {"status": "ok"}
        raise RuntimeError("POST-only transport does not support GET readback!")

    exporter = ProductionMetricsExporter(
        release_sha=test_sha,
        registry=registry,
        gcp_project="alfaloop-data-project",
        provider_route="https://monitoring.googleapis.com/v3",
        http_transport=post_only_transport,
    )
    with pytest.raises(RuntimeError, match="POST-only transport does not support GET readback"):
        exporter.export_metrics()


def test_export_readback_rejects_unexported_attacker_type_or_old_point() -> None:
    from shared.observability import ProductionMetricsExporter, default_registry

    registry = default_registry()
    registry.increment(
        "api_request_count", labels={"service": "api", "route": "/jobs", "status": "200"}
    )
    test_sha = "b28a6b6d335293ecb51a72dff3700838e196129c"
    gcp_proj = "alfaloop-data-project"
    monitoring_route = "https://monitoring.googleapis.com/v3"

    # Attack 1a: Provider returns unexported attacker metric type
    def attacker_type_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        if method == "POST":
            return 200, {}
        return 200, {
            "gcp_project": gcp_proj,
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/attacker_metric",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": gcp_proj}},
                    "points": [
                        {
                            "interval": {"endTime": datetime.now(UTC).isoformat()},
                            "value": {"doubleValue": 1.0},
                        }
                    ],
                }
            ],
        }

    exporter = ProductionMetricsExporter(
        release_sha=test_sha,
        registry=registry,
        gcp_project=gcp_proj,
        provider_route=monitoring_route,
        http_transport=attacker_type_transport,
    )
    with pytest.raises(RuntimeError, match="was not exported in POST body"):
        exporter.export_metrics()

    # Attack 1b: Provider returns point from year 2000 outside query window
    def old_point_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        if method == "POST":
            return 200, {}
        return 200, {
            "gcp_project": gcp_proj,
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_request_count",
                        "labels": {"release_sha": test_sha, "service": "api", "route": "/jobs", "status": "200"},
                    },
                    "resource": {"type": "global", "labels": {"project_id": gcp_proj}},
                    "points": [
                        {
                            "interval": {"endTime": "2000-01-01T00:00:00Z"},
                            "value": {"doubleValue": 1.0},
                        }
                    ],
                }
            ],
        }

    exporter_old = ProductionMetricsExporter(
        release_sha=test_sha,
        registry=registry,
        gcp_project=gcp_proj,
        provider_route=monitoring_route,
        http_transport=old_point_transport,
    )
    with pytest.raises(RuntimeError, match="lies outside requested query window"):
        exporter_old.export_metrics()


def test_watch_window_rejects_single_point_or_unallowlisted_counter(tmp_path: Path) -> None:
    from datetime import timedelta

    from shared.observability.watch_window import record_deployment_watch_window_status

    test_sha = "b28a6b6d335293ecb51a72dff3700838e196129c"
    receipt_file = tmp_path / "watch_receipt.json"
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    # Attack 2a: Single arbitrary positive point with window_coverage_seconds=0
    def single_point_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 12.5},
                        }
                    ],
                }
            ],
        }

    with pytest.raises(ValueError, match="requires multiple timestamped points"):
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=single_point_transport,
        )

    # Attack 2b: Unallowlisted attacker counter metric
    def attacker_counter_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/attacker_counter",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 1.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 1.0},
                        },
                    ],
                }
            ],
        }

    with pytest.raises(ValueError, match="un-allowlisted metric type"):
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=attacker_counter_transport,
        )


def test_verify_watch_window_receipt_rejects_tampered_proof_or_circular_metric(tmp_path: Path, monkeypatch: Any) -> None:
    from datetime import timedelta

    from shared.observability.watch_window import (
        compute_provider_watch_signature,
        record_deployment_watch_window_status,
        verify_watch_window_receipt,
    )

    test_sha = "b28a6b6d335293ecb51a72dff3700838e196129c"
    receipt_file = tmp_path / "watch_receipt.json"
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    monkeypatch.setenv("MONITORING_PROVIDER_SECRET", "test-sec-1950")

    def valid_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        pr_dict = params or {}
        s_iso = pr_dict.get("interval.startTime", start_dt.isoformat())
        e_iso = pr_dict.get("interval.endTime", end_dt.isoformat())
        ts = [
            {
                "metric": {
                    "type": "custom.googleapis.com/api_error_count",
                    "labels": {"release_sha": test_sha},
                },
                "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 0.0},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 0.0},
                    },
                ],
            },
            {
                "metric": {
                    "type": "custom.googleapis.com/api_latency_ms",
                    "labels": {"release_sha": test_sha},
                },
                "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 12.5},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 14.2},
                    },
                ],
            },
        ]
        raw_resp = {"gcp_project": "alfaloop-data-project", "release_sha": test_sha, "timeSeries": ts}
        proof_hash = hashlib.sha256(json.dumps(raw_resp, sort_keys=True).encode("utf-8")).hexdigest()
        prov_rcpt = f"prov-rcpt-{test_sha[:8]}"
        sig_token, rb_token = compute_provider_watch_signature(
            provider_secret="test-sec-1950",
            provider_receipt_id=prov_rcpt,
            gcp_project="alfaloop-data-project",
            release_sha=test_sha,
            start_iso=s_iso,
            end_iso=e_iso,
            proof_hash=proof_hash,
        )
        raw_resp["provider_receipt_id"] = prov_rcpt
        raw_resp["provider_signature"] = sig_token
        raw_resp["provider_readback_identity"] = rb_token
        return 200, raw_resp

    record_deployment_watch_window_status(
        release_sha=test_sha,
        status=1,
        start_time=start_dt,
        end_time=end_dt,
        receipt_path=receipt_file,
        gcp_project="alfaloop-data-project",
        provider_route="https://monitoring.googleapis.com/v3",
        query_transport=valid_transport,
    )

    # Confirm valid receipt verifies cleanly
    assert verify_watch_window_receipt(expected_release_sha=test_sha, receipt_path=receipt_file)["status"] == "WATCH_PASSED"

    # Attack 3a: Tamper provider metric in stored receipt to circular status metric with value 0
    raw_data = json.loads(receipt_file.read_text(encoding="utf-8"))
    raw_data["monitoring_query_execution"]["provider_query_response"]["timeSeries"][0]["metric"]["type"] = "custom.googleapis.com/deployment_watch_window_status"
    raw_data["monitoring_query_execution"]["provider_query_response"]["timeSeries"][0]["points"][0]["value"]["doubleValue"] = 0
    receipt_file.write_text(json.dumps(raw_data), encoding="utf-8")

    with pytest.raises(ValueError, match="circular deployment_watch_window_status metric|integrity check failed"):
        verify_watch_window_receipt(expected_release_sha=test_sha, receipt_path=receipt_file)


def test_round5_reproduced_gaps_mutation_coverage(tmp_path: Path, monkeypatch: Any) -> None:
    from datetime import timedelta

    from shared.observability.watch_window import (
        compute_provider_watch_signature,
        record_deployment_watch_window_status,
        verify_watch_window_receipt,
    )

    test_sha = "b28a6b6d335293ecb51a72dff3700838e196129c"
    receipt_file = tmp_path / "round5_gap_receipt.json"
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    # Round 5 Gap 1: REQUEST_COUNT_ONLY_PASS MUST BE REJECTED when status=1
    monkeypatch.setenv("MONITORING_PROVIDER_SECRET", "test-sec-2070")

    def request_count_only_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        pr_dict = params or {}
        s_iso = pr_dict.get("interval.startTime", start_dt.isoformat())
        e_iso = pr_dict.get("interval.endTime", end_dt.isoformat())
        ts = [
            {
                "metric": {
                    "type": "custom.googleapis.com/api_request_count",
                    "labels": {"release_sha": test_sha},
                },
                "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 10.0},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 15.0},
                    },
                ],
            }
        ]
        raw_resp = {"gcp_project": "alfaloop-data-project", "release_sha": test_sha, "timeSeries": ts}
        proof_hash = hashlib.sha256(json.dumps(raw_resp, sort_keys=True).encode("utf-8")).hexdigest()
        prov_rcpt = f"prov-rcpt-{test_sha[:8]}"
        sig_token, rb_token = compute_provider_watch_signature(
            provider_secret="test-sec-2070",
            provider_receipt_id=prov_rcpt,
            gcp_project="alfaloop-data-project",
            release_sha=test_sha,
            start_iso=s_iso,
            end_iso=e_iso,
            proof_hash=proof_hash,
        )
        raw_resp["provider_receipt_id"] = prov_rcpt
        raw_resp["provider_signature"] = sig_token
        raw_resp["provider_readback_identity"] = rb_token
        return 200, raw_resp

    with pytest.raises(ValueError, match="requires an explicit independent error/failure signal AND latency/health signal"):
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=request_count_only_transport,
        )

    # Round 5 Gap 2: TAMPERED_PROVIDER_PROOF_VERIFY_PASS MUST BE REJECTED by verifier
    monkeypatch.setenv("MONITORING_PROVIDER_SECRET", "test-sec-2070")

    def valid_full_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        pr_dict = params or {}
        s_iso = pr_dict.get("interval.startTime", start_dt.isoformat())
        e_iso = pr_dict.get("interval.endTime", end_dt.isoformat())
        ts = [
            {
                "metric": {
                    "type": "custom.googleapis.com/api_error_count",
                    "labels": {"release_sha": test_sha},
                },
                "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 0.0},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 0.0},
                    },
                ],
            },
            {
                "metric": {
                    "type": "custom.googleapis.com/api_latency_ms",
                    "labels": {"release_sha": test_sha},
                },
                "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                "points": [
                    {
                        "interval": {"endTime": s_iso},
                        "value": {"doubleValue": 12.5},
                    },
                    {
                        "interval": {"endTime": e_iso},
                        "value": {"doubleValue": 14.2},
                    },
                ],
            },
        ]
        raw_resp = {"gcp_project": "alfaloop-data-project", "release_sha": test_sha, "timeSeries": ts}
        proof_hash = hashlib.sha256(json.dumps(raw_resp, sort_keys=True).encode("utf-8")).hexdigest()
        prov_rcpt = f"prov-rcpt-{test_sha[:8]}"
        sig_token, rb_token = compute_provider_watch_signature(
            provider_secret="test-sec-2070",
            provider_receipt_id=prov_rcpt,
            gcp_project="alfaloop-data-project",
            release_sha=test_sha,
            start_iso=s_iso,
            end_iso=e_iso,
            proof_hash=proof_hash,
        )
        raw_resp["provider_receipt_id"] = prov_rcpt
        raw_resp["provider_signature"] = sig_token
        raw_resp["provider_readback_identity"] = rb_token
        return 200, raw_resp

    record_deployment_watch_window_status(
        release_sha=test_sha,
        status=1,
        start_time=start_dt,
        end_time=end_dt,
        receipt_path=receipt_file,
        gcp_project="alfaloop-data-project",
        provider_route="https://monitoring.googleapis.com/v3",
        query_transport=valid_full_transport,
    )

    # Baseline receipt passes
    assert verify_watch_window_receipt(expected_release_sha=test_sha, receipt_path=receipt_file)["status"] == "WATCH_PASSED"

    # Tamper 2a: Change provider project_id inside provider_query_response
    raw = json.loads(receipt_file.read_text(encoding="utf-8"))
    raw["monitoring_query_execution"]["provider_query_response"]["timeSeries"][0]["resource"]["labels"]["project_id"] = "wrong-project"
    receipt_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Stored provider proof project mismatch|project mismatch"):
        verify_watch_window_receipt(expected_release_sha=test_sha, receipt_path=receipt_file)

    # Reset receipt
    record_deployment_watch_window_status(
        release_sha=test_sha,
        status=1,
        start_time=start_dt,
        end_time=end_dt,
        receipt_path=receipt_file,
        gcp_project="alfaloop-data-project",
        provider_route="https://monitoring.googleapis.com/v3",
        query_transport=valid_full_transport,
    )

    # Tamper 2b: Change provider point value inside provider_query_response
    raw = json.loads(receipt_file.read_text(encoding="utf-8"))
    raw["monitoring_query_execution"]["provider_query_response"]["timeSeries"][1]["points"][1]["value"]["doubleValue"] = 9999.0
    receipt_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="Provider signature mismatch|Stored provider proof point_values mismatch|point_values mismatch|failed independent cryptographic authentication"):
        verify_watch_window_receipt(expected_release_sha=test_sha, receipt_path=receipt_file)


def test_round6_reproduced_gaps_mutation_coverage(tmp_path: Path) -> None:
    from datetime import timedelta

    from shared.observability.watch_window import record_deployment_watch_window_status

    test_sha = "b28a6b6d335293ecb51a72dff3700838e196129c"
    receipt_file = tmp_path / "round6_gap_receipt.json"
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    # Round 6 Gap 1: Pooled category coverage (Error at start, Latency at end) MUST BE REJECTED
    def pooled_category_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_error_count",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 12.5},
                        },
                    ],
                },
            ],
        }

    with pytest.raises(
        ValueError,
        match="requires multiple timestamped points across watch window",
    ):
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=pooled_category_transport,
        )

    # Round 6 Gap 1b: Category 1 points only at start_dt (span 0s), Category 2 points only at end_dt (span 0s)
    def pooled_category_sub_window_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_error_count",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 12.5}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 12.5}},
                    ],
                },
            ],
        }

    with pytest.raises(
        ValueError,
        match="Coverage cannot be pooled across different series",
    ):
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=pooled_category_sub_window_transport,
        )

    # Round 6 Gap 2: Negative error count MUST BE REJECTED
    def negative_error_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_error_count",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": -5.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 10.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 10.0}},
                    ],
                },
            ],
        }

    with pytest.raises(
        ValueError, match="has negative value '-5.0'. Finite non-negative domain required"
    ):
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=negative_error_transport,
        )

    # Round 6 Gap 3: Negative latency MUST BE REJECTED
    def negative_latency_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_error_count",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": -100.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 10.0}},
                    ],
                },
            ],
        }

    with pytest.raises(
        ValueError, match="has negative value '-100.0'. Finite non-negative domain required"
    ):
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=negative_latency_transport,
        )

    # Round 6 Gap 4: Non-finite (NaN) metric value MUST BE REJECTED
    def nan_metric_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_error_count",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": float("nan")}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 10.0}},
                    ],
                },
            ],
        }

    with pytest.raises(ValueError, match="has non-finite value|non-numeric value"):
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=nan_metric_transport,
        )


def test_round7_production_metric_recorders_all_categories() -> None:
    from shared.observability.metrics import (
        ProductionMetricsExporter,
        default_registry,
        record_business_kpi_signal,
        record_data_signal,
        record_model_signal,
    )

    reg = default_registry()
    reg.clear()

    record_data_signal("pg16", "forecast_view", freshness_hours=1.5, quality_score=0.98, feature_null_rate=0.01)
    record_model_signal("forecast_ops", "learninghub", prediction_count=100, model_error=12.5, interval_coverage=0.95, drift_score=0.02)
    record_business_kpi_signal("heatzone_topk_adoption_rate", 0.92)
    record_business_kpi_signal("price_hard_constraint_violation_count", 0.0)

    snap = reg.snapshot()
    assert "data_freshness_hours" in snap
    assert "prediction_count" in snap
    assert "heatzone_topk_adoption_rate" in snap

    test_sha = "a" * 40

    def mock_transport(method: str, url: str, params: dict = None, payload: dict = None, headers: dict = None) -> tuple[int, dict]:
        now_iso = datetime.now(UTC).isoformat()
        if method == "POST":
            return 200, {"status": "ok"}
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {"type": f"custom.googleapis.com/{name}", "labels": {**dict(items[0].get("labels", {})), "release_sha": test_sha}},
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [{"interval": {"endTime": now_iso}, "value": {"doubleValue": 1.0}}],
                }
                for name, items in snap.items()
            ],
        }

    exporter = ProductionMetricsExporter(
        release_sha=test_sha,
        registry=reg,
        gcp_project="alfaloop-data-project",
        http_transport=mock_transport,
    )
    receipt = exporter.export_metrics()
    assert receipt["readback_status"] == "SUCCESS"
    assert receipt["export_receipt_id"].startswith("gcp-cm-readback-")


def test_round7_export_receipt_canonical_integrity_and_value_mutation() -> None:
    from shared.observability.metrics import MetricsRegistry, ProductionMetricsExporter

    test_sha = "b" * 40

    def make_exporter_with_val(val: float) -> ProductionMetricsExporter:
        reg = MetricsRegistry()

        from shared.observability.metrics import MetricCategory, MetricDefinition, MetricType
        reg.register(MetricDefinition("adlift_incremental_gm", MetricType.GAUGE, MetricCategory.BUSINESS, "AdLift GM"))
        reg.set("adlift_incremental_gm", val)

        def mock_tp(method: str, url: str, params: dict = None, payload: dict = None, headers: dict = None) -> tuple[int, dict]:
            now_iso = datetime.now(UTC).isoformat()
            if method == "POST":
                return 200, {"status": "ok"}
            return 200, {
                "gcp_project": "alfaloop-data-project",
                "release_sha": test_sha,
                "timeSeries": [
                    {
                        "metric": {"type": "custom.googleapis.com/adlift_incremental_gm", "labels": {"release_sha": test_sha}},
                        "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                        "points": [{"interval": {"endTime": now_iso}, "value": {"doubleValue": val}}],
                    }
                ],
            }

        return ProductionMetricsExporter(
            release_sha=test_sha,
            registry=reg,
            gcp_project="alfaloop-data-project",
            http_transport=mock_tp,
        )

    exp1 = make_exporter_with_val(5.0)
    receipt1 = exp1.export_metrics()

    exp2 = make_exporter_with_val(-5.0)
    receipt2 = exp2.export_metrics()

    # Value mutation MUST produce different receipt IDs (digest collision prevented)
    assert receipt1["export_receipt_id"] != receipt2["export_receipt_id"]


def test_round7_watch_window_receipt_durable_verification(monkeypatch: Any) -> None:
    import json
    from pathlib import Path

    from shared.observability.watch_window import verify_watch_window_receipt

    monkeypatch.setenv("MONITORING_PROVIDER_SECRET", "evidence-provider-trust-root-secret")

    receipt_path = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "watch_window_receipt.json"
    assert receipt_path.exists()

    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    sha = receipt_data["release_sha"]

    verified = verify_watch_window_receipt(expected_release_sha=sha, receipt_path=receipt_path)
    assert verified["status"] == "WATCH_PASSED"
    assert verified["release_sha"] == sha


def test_round8_metric_contract_bounds_enforced() -> None:
    import pytest

    from shared.observability import default_registry

    reg = default_registry()

    with pytest.raises(ValueError, match="below minimum allowed 0.0"):
        reg.set("heatzone_topk_adoption_rate", -1.0)

    with pytest.raises(ValueError, match="exceeds maximum allowed 1.0"):
        reg.set("heatzone_topk_adoption_rate", 2.0)

    with pytest.raises(ValueError, match="exceeds maximum allowed 1.0"):
        reg.set("prediction_interval_coverage", 1.5)

    with pytest.raises(ValueError, match="exceeds maximum allowed 1.0"):
        reg.set("feature_null_rate", 1.1)

    with pytest.raises(ValueError, match="exceeds maximum allowed 1.0"):
        reg.set("data_quality_score", 1.2)


def test_round8_oncall_adapter_authenticity_and_sha_enforced(monkeypatch: Any) -> None:
    import hashlib
    import json

    from modules.notifications.infrastructure.adapters import OnCallNotificationAdapter

    # 1. Blank or non-40-char or 000...000 release_sha must fail closed
    monkeypatch.setenv("RELEASE_SHA", "invalid-short-sha")
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", "a" * 40)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", "secret-123")
    adapter1 = OnCallNotificationAdapter(endpoint_url="https://oncall-router.oday.plus/api/v1/alerts")
    ok, err = adapter1.send("n1", "webhook", "ops-lead", "Title", "Detail")
    assert ok is False
    assert err is not None and ("authentic 40-character release_sha" in err or "missing or invalid" in err)
    assert adapter1.delivery_receipts[-1]["status"] == "FAILED"

    monkeypatch.setenv("RELEASE_SHA", "0" * 40)
    ok_zero, err_zero = adapter1.send("n1_zero", "webhook", "ops-lead", "Title", "Detail")
    assert ok_zero is False
    assert err_zero is not None and ("unauthenticated release" in err_zero or "missing or invalid" in err_zero)
    assert adapter1.delivery_receipts[-1]["status"] == "FAILED"

    # 2. Arbitrary caller-controlled signature strings (sig-authentic-*, sig-sha256-verified-*) stay TEST_ONLY
    valid_sha = "a" * 40
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)

    def mock_transport_attacker_sig(url: str, payload: dict):
        return 200, {
            "provider_receipt_id": "prov-rcpt-authentic-999",
            "provider_signature": "sig-authentic-attacker-controlled",
            "provider_readback": "readback-attacker-controlled",
            "status": "success",
        }

    adapter2 = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=mock_transport_attacker_sig,
    )
    ok2, err2 = adapter2.send("n2", "webhook", "ops-lead", "Title", "Detail")
    assert ok2 is True
    # B1: Caller-controlled string prefixes stay TEST_ONLY and do NOT become DELIVERED
    assert adapter2.delivery_receipts[-1]["status"] == "TEST_ONLY"

    # 3. Authentic provider response with injected transport returns TEST_ONLY
    secret_val = "secret-123"

    def mock_transport_authentic(url: str, payload: dict):
        req_bytes = json.dumps(payload, sort_keys=True).encode()
        req_hash = hashlib.sha256(req_bytes).hexdigest()
        prov_rcpt = "prov-rcpt-authentic-999"
        sig_base = f"{secret_val}:{prov_rcpt}:{req_hash}:{valid_sha}".encode()
        exp_sig = hashlib.sha256(sig_base).hexdigest()

        return 200, {
            "provider_receipt_id": prov_rcpt,
            "provider_signature": f"sig-sha256-{exp_sig}",
            "provider_readback": req_hash,
            "status": "delivered",
        }

    adapter3 = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=mock_transport_authentic,
    )
    ok3, err3 = adapter3.send("n3", "webhook", "ops-lead", "Title", "Detail")
    assert ok3 is True
    assert adapter3.delivery_receipts[-1]["status"] == "TEST_ONLY"

    # 4. Authentic provider response over real loopback network HTTP socket returns DELIVERED
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class AuthenticOnCallHTTPHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8")) if body else {}

            prov_rcpt = "prov-rcpt-authentic-999"
            req_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
            req_hash = hashlib.sha256(req_bytes).hexdigest()
            sig_base = f"{secret_val}:{prov_rcpt}:{req_hash}:{valid_sha}".encode()
            sig_token = f"sig-sha256-{hashlib.sha256(sig_base).hexdigest()}"
            rb_base = f"readback:{req_hash}".encode()
            rb_token = hashlib.sha256(rb_base).hexdigest()

            response_payload = {
                "status": "delivered",
                "provider_receipt_id": prov_rcpt,
                "provider_signature": sig_token,
                "provider_readback": rb_token,
            }
            response_bytes = json.dumps(response_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), AuthenticOnCallHTTPHandler)
    server_port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    adapter4 = OnCallNotificationAdapter(
        endpoint_url=f"http://127.0.0.1:{server_port}/api/v1/alerts",
        http_transport=None,
    )
    ok4, err4 = adapter4.send("n4", "webhook", "ops-lead", "Title", "Detail")
    assert ok4 is True
    assert err4 is None
    # B1: Loopback HTTP sockets are test-only and yield TEST_ONLY, never DELIVERED
    assert adapter4.delivery_receipts[-1]["status"] == "TEST_ONLY"


def test_round10_remediation_findings_b1_b4_verified(monkeypatch: Any, tmp_path: Path) -> None:
    import hashlib
    import json
    from datetime import UTC, datetime, timedelta

    from modules.notifications.infrastructure.adapters import OnCallNotificationAdapter
    from shared.observability.watch_window import (
        record_deployment_watch_window_status,
        verify_watch_window_receipt,
    )

    valid_sha = "a" * 40
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", "test-secret-456")

    # B1 Mutation: Attacker caller strings sig-authentic- / readback- do NOT become DELIVERED
    def attacker_transport(url: str, payload: dict):
        return 200, {
            "provider_receipt_id": "attacker-selected-receipt",
            "provider_signature": "sig-authentic-attacker-controlled",
            "provider_readback": "readback-attacker-controlled",
            "status": "success",
        }

    adapter_b1 = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=attacker_transport,
    )
    ok_b1, err_b1 = adapter_b1.send("n_b1", "webhook", "ops-lead", "P1 Alert", "Detail")
    assert ok_b1 is True
    assert adapter_b1.delivery_receipts[-1]["status"] == "TEST_ONLY"

    # B2 Mutation: Untrusted release SHA (11111...) fails closed when TRUSTED_DEPLOYED_RELEASE_SHA is configured
    monkeypatch.setenv("RELEASE_SHA", "1" * 40)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)
    adapter_b2 = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=attacker_transport,
    )
    ok_b2, err_b2 = adapter_b2.send("n_b2", "webhook", "ops-lead", "P1 Alert", "Detail")
    assert ok_b2 is False
    assert adapter_b2.delivery_receipts[-1]["status"] == "FAILED"
    assert err_b2 is not None and "matching trusted deployed release" in err_b2

    monkeypatch.setenv("RELEASE_SHA", valid_sha)

    # B3 Mutation: Watch receipt provider signature tamper is rejected by verifier
    receipt_file = tmp_path / "watch_window_receipt.json"
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    from shared.observability.watch_window import compute_provider_watch_signature

    def valid_watch_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        prov_sec = "test-secret-456"
        prov_rcpt = "prov-rcpt-aaaaaaaa"
        raw_resp = {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha,
            "timeSeries": [
                {
                    "metric": {"type": "custom.googleapis.com/api_error_count", "labels": {"release_sha": valid_sha}},
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                    ],
                },
                {
                    "metric": {"type": "custom.googleapis.com/api_latency_ms", "labels": {"release_sha": valid_sha}},
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 10.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 12.0}},
                    ],
                },
            ],
        }
        proof_hash = hashlib.sha256(json.dumps(raw_resp, sort_keys=True).encode("utf-8")).hexdigest()
        sig_token, rb_token = compute_provider_watch_signature(
            provider_secret=prov_sec,
            provider_receipt_id=prov_rcpt,
            gcp_project="alfaloop-data-project",
            release_sha=valid_sha,
            start_iso=start_dt.isoformat(),
            end_iso=end_dt.isoformat(),
            proof_hash=proof_hash,
        )
        raw_resp["provider_receipt_id"] = prov_rcpt
        raw_resp["provider_signature"] = sig_token
        raw_resp["provider_readback_identity"] = rb_token
        return 200, raw_resp

    record_deployment_watch_window_status(
        release_sha=valid_sha,
        status=1,
        start_time=start_dt,
        end_time=end_dt,
        receipt_path=receipt_file,
        gcp_project="alfaloop-data-project",
        provider_route="https://monitoring.googleapis.com/v3",
        query_transport=valid_watch_transport,
    )
    assert verify_watch_window_receipt(expected_release_sha=valid_sha, receipt_path=receipt_file)["status"] == "WATCH_PASSED"

    # Tamper provider signature in stored receipt
    raw_rcpt = json.loads(receipt_file.read_text(encoding="utf-8"))
    raw_rcpt["monitoring_query_execution"]["provider_signature"] = "tampered-provider-sig-123"
    receipt_file.write_text(json.dumps(raw_rcpt, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="Provider signature mismatch|integrity check failed|failed independent cryptographic authentication"):
        verify_watch_window_receipt(expected_release_sha=valid_sha, receipt_path=receipt_file)

    # B4 Mutation: HeatZone unmeasured adoption returns None (NO-GO)
    from modules.heatzone.infrastructure import HeatZoneResultStore
    hz_store = HeatZoneResultStore()
    assert hz_store.get_measured_topk_adoption_rate() is None


def test_round11_remediation_findings_verified(monkeypatch: Any, tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from modules.notifications.infrastructure.adapters import OnCallNotificationAdapter
    from shared.observability.watch_window import (
        record_deployment_watch_window_status,
    )

    valid_sha = "f" * 40
    receipt_file = tmp_path / "round11_watch_receipt.json"

    # 1. OnCall delivery without ONCALL_PROVIDER_SECRET fails closed immediately
    monkeypatch.delenv("ONCALL_PROVIDER_SECRET", raising=False)
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)

    adapter_no_secret = OnCallNotificationAdapter(endpoint_url="https://oncall-router.oday.plus/api/v1/alerts")
    ok1, err1 = adapter_no_secret.send("n_r11_1", "webhook", "ops-lead", "Title", "Detail")
    assert ok1 is False
    assert err1 is not None and "ONCALL_PROVIDER_SECRET" in err1
    assert adapter_no_secret.delivery_receipts[-1]["status"] == "FAILED"

    # 2. OnCall delivery without TRUSTED_DEPLOYED_RELEASE_SHA fails closed immediately
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", "provider-secret-xyz")
    monkeypatch.delenv("TRUSTED_DEPLOYED_RELEASE_SHA", raising=False)
    monkeypatch.delenv("EXPECTED_RELEASE_SHA", raising=False)

    adapter_no_trusted = OnCallNotificationAdapter(endpoint_url="https://oncall-router.oday.plus/api/v1/alerts")
    ok2, err2 = adapter_no_trusted.send("n_r11_2", "webhook", "ops-lead", "Title", "Detail")
    assert ok2 is False
    assert err2 is not None and "trusted deployed release binding" in err2
    assert adapter_no_trusted.delivery_receipts[-1]["status"] == "FAILED"

    # 3. Watch window query response missing provider-issued receipt/signature/readback identity fails closed (no local fallbacks)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    def missing_provider_fields_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha,
            "timeSeries": [
                {
                    "metric": {"type": "custom.googleapis.com/api_error_count", "labels": {"release_sha": valid_sha}},
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                    ],
                },
                {
                    "metric": {"type": "custom.googleapis.com/api_latency_ms", "labels": {"release_sha": valid_sha}},
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 10.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 12.0}},
                    ],
                },
            ],
        }

    with pytest.raises(ValueError, match="missing authentic provider-issued provider_receipt_id|strictly forbidden"):
        record_deployment_watch_window_status(
            release_sha=valid_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=missing_provider_fields_transport,
        )


def test_round13_remediation_findings_b1_b2_verified(monkeypatch: Any, tmp_path: Path) -> None:
    import hashlib
    import json
    import threading
    from datetime import UTC, datetime, timedelta
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from modules.notifications.infrastructure.adapters import OnCallNotificationAdapter
    from shared.observability.watch_window import (
        authenticate_provider_watch_signature,
        compute_provider_watch_signature,
        record_deployment_watch_window_status,
        verify_watch_window_receipt,
    )

    valid_sha = "d" * 40
    provider_sec = "r13-secret-proof-1000"
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", provider_sec)
    monkeypatch.setenv("MONITORING_PROVIDER_SECRET", provider_sec)

    # 1. B1 Negative Mutation: Ambient provenance strings + is_production_transport attribute on injected transport still produce TEST_ONLY (never DELIVERED)
    monkeypatch.setenv("ONCALL_SECRET_PROVENANCE", "gcp_secret_manager")
    monkeypatch.setenv("DEPLOYMENT_RELEASE_PROVENANCE", "cloud_run_metadata")

    def injected_transport_caller_controlled(url: str, payload: dict):
        prov_rcpt = "prov-rcpt-123"
        req_bytes = json.dumps(payload, sort_keys=True).encode()
        req_hash = hashlib.sha256(req_bytes).hexdigest()
        sig_base = f"{provider_sec}:{prov_rcpt}:{req_hash}:{valid_sha}".encode()
        exp_sig = f"sig-sha256-{hashlib.sha256(sig_base).hexdigest()}"
        return 200, {
            "provider_receipt_id": prov_rcpt,
            "provider_signature": exp_sig,
            "provider_readback": req_hash,
            "status": "DELIVERED",
        }
    injected_transport_caller_controlled.is_production_transport = True
    injected_transport_caller_controlled.is_production = True

    adapter_b1 = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=injected_transport_caller_controlled,
    )
    ok_b1, err_b1 = adapter_b1.send("n_b1_r13", "webhook", "ops-lead", "Title", "Detail")
    assert ok_b1 is True
    # Must be TEST_ONLY, NEVER DELIVERED
    assert adapter_b1.delivery_receipts[-1]["status"] == "TEST_ONLY"

    # Reset provenance env
    monkeypatch.delenv("ONCALL_SECRET_PROVENANCE", raising=False)
    monkeypatch.delenv("DEPLOYMENT_RELEASE_PROVENANCE", raising=False)

    # 2. B1 Authentic Production path: Real HTTP loopback server with default network transport returns DELIVERED
    class AuthenticOnCallHTTPHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8")) if body else {}

            prov_rcpt = f"prov-auth-{payload.get('delivery_id', '999')}"
            req_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
            req_hash = hashlib.sha256(req_bytes).hexdigest()
            sig_base = f"{provider_sec}:{prov_rcpt}:{req_hash}:{valid_sha}".encode()
            sig_token = f"sig-sha256-{hashlib.sha256(sig_base).hexdigest()}"
            rb_base = f"readback:{req_hash}".encode()
            rb_token = hashlib.sha256(rb_base).hexdigest()

            response_payload = {
                "status": "delivered",
                "route": payload.get("user_id", "ops-lead"),
                "delivery_id": payload.get("delivery_id"),
                "provider_receipt_id": prov_rcpt,
                "provider_signature": sig_token,
                "provider_readback": rb_token,
                "received_at": datetime.now(UTC).isoformat(),
            }
            response_bytes = json.dumps(response_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), AuthenticOnCallHTTPHandler)
    server_port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    adapter_prod = OnCallNotificationAdapter(
        endpoint_url=f"http://127.0.0.1:{server_port}/api/v1/alerts",
        http_transport=None,  # Uses default_http_transport over real loopback network socket
    )
    ok_prod, err_prod = adapter_prod.send("n_prod_r13", "webhook", "ops-lead", "Title", "Detail")
    assert ok_prod is True
    # B1: Loopback HTTP sockets are test-only and yield TEST_ONLY, never DELIVERED
    assert adapter_prod.delivery_receipts[-1]["status"] == "TEST_ONLY"

    # 3. B2 Negative Mutation 1: Arbitrary valid hex signature ("sig-sha256-" + "e"*16) is rejected
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)
    dummy_proof = "a" * 64
    arbitrary_hex_sig = "sig-sha256-" + "e" * 16
    arbitrary_suffix_rb = "anything-ending-" + valid_sha[:8]

    assert not authenticate_provider_watch_signature(
        provider_secret=provider_sec,
        provider_receipt_id="rcpt-123",
        provider_signature=arbitrary_hex_sig,
        provider_readback_identity=arbitrary_suffix_rb,
        gcp_project="alfaloop-data-project",
        release_sha=valid_sha,
        start_iso=start_dt.isoformat(),
        end_iso=end_dt.isoformat(),
        proof_hash=dummy_proof,
    )

    # 4. B2 Negative Mutation 2: Suffix-only readback identity is rejected
    sig_valid_format, rb_valid = compute_provider_watch_signature(
        provider_secret=provider_sec,
        provider_receipt_id="rcpt-123",
        gcp_project="alfaloop-data-project",
        release_sha=valid_sha,
        start_iso=start_dt.isoformat(),
        end_iso=end_dt.isoformat(),
        proof_hash=dummy_proof,
    )
    assert not authenticate_provider_watch_signature(
        provider_secret=provider_sec,
        provider_receipt_id="rcpt-123",
        provider_signature=sig_valid_format,
        provider_readback_identity=arbitrary_suffix_rb,
        gcp_project="alfaloop-data-project",
        release_sha=valid_sha,
        start_iso=start_dt.isoformat(),
        end_iso=end_dt.isoformat(),
        proof_hash=dummy_proof,
    )

    # 5. B2 Negative Mutation 3: Missing provider secret fails closed without fallback
    monkeypatch.delenv("MONITORING_PROVIDER_SECRET", raising=False)
    monkeypatch.delenv("ONCALL_PROVIDER_SECRET", raising=False)

    receipt_file = tmp_path / "round13_watch_receipt.json"

    def valid_watch_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha,
            "provider_receipt_id": "prov-rcpt-123",
            "provider_signature": sig_valid_format,
            "provider_readback_identity": rb_valid,
            "timeSeries": [
                {
                    "metric": {"type": "custom.googleapis.com/api_error_count", "labels": {"release_sha": valid_sha}},
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                    ],
                },
                {
                    "metric": {"type": "custom.googleapis.com/api_latency_ms", "labels": {"release_sha": valid_sha}},
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 10.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 12.0}},
                    ],
                },
            ],
        }

    with pytest.raises(ValueError, match="provider trust root gate enforced"):
        record_deployment_watch_window_status(
            release_sha=valid_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            receipt_path=receipt_file,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=valid_watch_transport,
        )

    # 6. B2 Authentic Watch Proof: Cryptographically authenticated signature passes when secret is set
    monkeypatch.setenv("MONITORING_PROVIDER_SECRET", provider_sec)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", provider_sec)

    def authentic_proof_watch_transport(method: str, url: str, params: dict = None, payload: dict = None) -> tuple[int, dict]:
        prov_rcpt = "prov-rcpt-authentic-r13"
        raw_resp = {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha,
            "timeSeries": [
                {
                    "metric": {"type": "custom.googleapis.com/api_error_count", "labels": {"release_sha": valid_sha}},
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 0.0}},
                    ],
                },
                {
                    "metric": {"type": "custom.googleapis.com/api_latency_ms", "labels": {"release_sha": valid_sha}},
                    "resource": {"type": "global", "labels": {"project_id": "alfaloop-data-project"}},
                    "points": [
                        {"interval": {"endTime": start_dt.isoformat()}, "value": {"doubleValue": 10.0}},
                        {"interval": {"endTime": end_dt.isoformat()}, "value": {"doubleValue": 12.0}},
                    ],
                },
            ],
        }
        proof_hash = hashlib.sha256(json.dumps(raw_resp, sort_keys=True).encode("utf-8")).hexdigest()
        sig_token, rb_token = compute_provider_watch_signature(
            provider_secret=provider_sec,
            provider_receipt_id=prov_rcpt,
            gcp_project="alfaloop-data-project",
            release_sha=valid_sha,
            start_iso=start_dt.isoformat(),
            end_iso=end_dt.isoformat(),
            proof_hash=proof_hash,
        )
        raw_resp["provider_receipt_id"] = prov_rcpt
        raw_resp["provider_signature"] = sig_token
        raw_resp["provider_readback_identity"] = rb_token
        return 200, raw_resp

    rcpt = record_deployment_watch_window_status(
        release_sha=valid_sha,
        status=1,
        start_time=start_dt,
        end_time=end_dt,
        receipt_path=receipt_file,
        gcp_project="alfaloop-data-project",
        provider_route="https://monitoring.googleapis.com/v3",
        query_transport=authentic_proof_watch_transport,
    )
    assert rcpt["status"] == "WATCH_PASSED"
    assert verify_watch_window_receipt(expected_release_sha=valid_sha, receipt_path=receipt_file)["status"] == "WATCH_PASSED"


def test_round8_worker_and_scheduler_export_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.scheduler.oday_scheduler.main import ODayScheduler
    from apps.worker.oday_worker.main import ODayWorker

    valid_sha = "b" * 40
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("GCP_PROJECT", "alfaloop-data-project")

    worker = ODayWorker()
    scheduler = ODayScheduler()

    # Local export returns None or raises/exports cleanly without AttributeError
    assert hasattr(worker, "export_metrics")
    assert hasattr(scheduler, "export_metrics")


def test_round14_remediation_findings_b1_loopback_socket_mutation_verified(monkeypatch: Any) -> None:
    import hashlib
    import json
    import threading
    from datetime import UTC, datetime
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from modules.notifications.infrastructure.adapters import OnCallNotificationAdapter

    valid_sha = "e" * 40
    provider_sec = "r14-secret-proof-2000"
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", provider_sec)

    # 1. Round 14 B1 Negative Mutation: Caller-owned loopback server using default_http_transport + caller-selected ONCALL_PROVIDER_SECRET and RELEASE_SHA produces TEST_ONLY, NEVER DELIVERED
    class CallerOwnedLoopbackHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8")) if body else {}

            prov_rcpt = "prov-receipt-caller-owned-123"
            req_bytes = json.dumps(payload, sort_keys=True).encode()
            req_hash = hashlib.sha256(req_bytes).hexdigest()
            sig_base = f"{provider_sec}:{prov_rcpt}:{req_hash}:{valid_sha}".encode()
            sig_token = f"sig-sha256-{hashlib.sha256(sig_base).hexdigest()}"
            rb_base = f"readback:{req_hash}".encode()
            rb_token = hashlib.sha256(rb_base).hexdigest()

            response_payload = {
                "status": "delivered",
                "route": payload.get("user_id", "ops-lead"),
                "delivery_id": payload.get("delivery_id"),
                "provider_receipt_id": prov_rcpt,
                "provider_signature": sig_token,
                "provider_readback": rb_token,
                "received_at": datetime.now(UTC).isoformat(),
            }
            response_bytes = json.dumps(response_payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), CallerOwnedLoopbackHandler)
    server_port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    adapter_loopback = OnCallNotificationAdapter(
        endpoint_url=f"http://127.0.0.1:{server_port}/attacker",
        http_transport=None,  # Uses default_http_transport
    )
    ok_lb, err_lb = adapter_loopback.send("n_r14_lb", "webhook", "ops-lead", "Title", "Detail")
    assert ok_lb is True
    # MUST be TEST_ONLY, NEVER DELIVERED
    receipt = adapter_loopback.delivery_receipts[-1]
    assert receipt["status"] == "TEST_ONLY"
    assert receipt["status"] != "DELIVERED"


def test_round16_remediation_findings_b1_b2_b3_negative_mutations_and_positive_verification(monkeypatch: Any, tmp_path: Path) -> None:
    import base64
    import hashlib
    import json

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from modules.notifications.infrastructure.adapters import OnCallNotificationAdapter

    valid_sha = "f" * 40
    provider_sec = "r16-secret-proof-4000"
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", provider_sec)

    # Generate ephemeral Ed25519 test keys in memory dynamically (NO private keys stored in repository)
    provider_key = ed25519.Ed25519PrivateKey.generate()
    platform_key = ed25519.Ed25519PrivateKey.generate()

    provider_pub_pem = provider_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    platform_pub_pem = platform_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")    # 1. Round 16 Negative Mutation A (Finding B3): Canonical hostname with unauthorized port 444 and query ?redirect=evil.example, no attestation, caller SHA env values -> TEST_ONLY, NEVER DELIVERED
    def mock_200_transport(url: str, payload: dict) -> tuple[int, dict]:
        return (200, {"status": "ok", "provider_receipt_id": "prov-rcpt-123", "provider_signature": "invalid-sig"})

    adapter_port_query_mutation = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus:444/api/v1/alerts?redirect=evil.example",
        http_transport=mock_200_transport,
        provider_public_key_pem=provider_pub_pem,
        platform_public_key_pem=platform_pub_pem,
    )
    ok_pq, err_pq = adapter_port_query_mutation.send("n_r16_pq", "webhook", "ops-lead", "Title", "Detail")
    assert ok_pq is True
    receipt_pq = adapter_port_query_mutation.delivery_receipts[-1]
    assert receipt_pq["status"] == "TEST_ONLY"
    assert receipt_pq["status"] != "DELIVERED"

    # 2. Round 16 Negative Mutation B (Finding B2): Unsigned deployment attestation file (missing platform signature) -> TEST_ONLY, NEVER DELIVERED
    unsigned_attestation_file = tmp_path / "unsigned_attestation.json"
    unsigned_attestation_file.write_text(json.dumps({"deployed_release_sha": valid_sha}), encoding="utf-8")
    monkeypatch.setenv("DEPLOYMENT_ATTESTATION_PATH", str(unsigned_attestation_file))

    adapter_unsigned_attestation = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=mock_200_transport,
        provider_public_key_pem=provider_pub_pem,
        platform_public_key_pem=platform_pub_pem,
    )
    ok_unatt, err_unatt = adapter_unsigned_attestation.send("n_r16_unatt", "webhook", "ops-lead", "Title", "Detail")
    assert ok_unatt is True
    receipt_unatt = adapter_unsigned_attestation.delivery_receipts[-1]
    assert receipt_unatt["status"] == "TEST_ONLY"
    assert receipt_unatt["status"] != "DELIVERED"

    # 3. Round 16 Negative Mutation C (Finding B3): Caller environment authority override attempt -> TEST_ONLY, NEVER DELIVERED
    monkeypatch.setenv("ONCALL_PRODUCTION_ENDPOINT_AUTHORITY", "https://evil.example/attacker")
    adapter_caller_override = OnCallNotificationAdapter(
        endpoint_url="https://evil.example/attacker",
        http_transport=mock_200_transport,
        provider_public_key_pem=provider_pub_pem,
        platform_public_key_pem=platform_pub_pem,
    )
    ok_ov, err_ov = adapter_caller_override.send("n_r16_ov", "webhook", "ops-lead", "Title", "Detail")
    assert ok_ov is True
    receipt_ov = adapter_caller_override.delivery_receipts[-1]
    assert receipt_ov["status"] == "TEST_ONLY"
    assert receipt_ov["status"] != "DELIVERED"

    # Reset caller authority env
    monkeypatch.delenv("ONCALL_PRODUCTION_ENDPOINT_AUTHORITY", raising=False)

    # 4. Round 17 Negative Mutation (Finding B1): Two-key caller-injection mutation.
    # Caller generates ephemeral Ed25519 keypairs, passes both public keys to constructor,
    # signs platform attestation with caller platform key, and signs provider receipt with caller provider key
    # using canonical endpoint and default transport. Must evaluate to TEST_ONLY, NEVER DELIVERED.
    plat_sig_bytes = platform_key.sign(f"platform_attestation:{valid_sha}".encode())
    plat_sig_b64 = base64.b64encode(plat_sig_bytes).decode("utf-8")

    signed_attestation_file = tmp_path / "signed_attestation.json"
    signed_attestation_file.write_text(
        json.dumps({
            "deployed_release_sha": valid_sha,
            "platform_signature": plat_sig_b64,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("DEPLOYMENT_ATTESTATION_PATH", str(signed_attestation_file))

    def authentic_asymmetric_transport(url: str, payload: dict) -> tuple[int, dict]:
        prov_rcpt = "prov-receipt-authentic-ed25519-002"
        req_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
        req_hash = hashlib.sha256(req_bytes).hexdigest()
        payload_bytes = f"{prov_rcpt}:{req_hash}:{valid_sha}".encode()
        sig_bytes = provider_key.sign(payload_bytes)
        sig_b64 = base64.b64encode(sig_bytes).decode("utf-8")
        return (
            200,
            {
                "status": "delivered",
                "provider_receipt_id": prov_rcpt,
                "provider_signature": sig_b64,
                "provider_readback": hashlib.sha256(f"readback:{req_hash}".encode()).hexdigest(),
            },
        )

    monkeypatch.setattr(OnCallNotificationAdapter, "_default_http_transport", staticmethod(authentic_asymmetric_transport))

    adapter_caller_keys_injected = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=None,  # Uses class default_http_transport
        provider_public_key_pem=provider_pub_pem,
        platform_public_key_pem=platform_pub_pem,
    )
    ok_inj, err_inj = adapter_caller_keys_injected.send("n_r17_inj", "webhook", "ops-lead", "Title", "Detail")
    assert ok_inj is True
    receipt_inj = adapter_caller_keys_injected.delivery_receipts[-1]
    assert receipt_inj["status"] == "TEST_ONLY"
    assert receipt_inj["status"] != "DELIVERED"

    import modules.notifications.infrastructure.adapters as adapters_mod

    # 5. Round 18 Negative Mutation Verification (Finding B1): No-argument adapter instantiation after module global and class default transport mutation.
    # Caller assigns custom key pairs to module globals (PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM / PINNED_PLATFORM_DEPLOYMENT_PUBLIC_KEY_PEM)
    # and mutates class default transport (OnCallNotificationAdapter._default_http_transport).
    # Constructing OnCallNotificationAdapter with NO arguments MUST evaluate to TEST_ONLY, NEVER DELIVERED.
    monkeypatch.setattr(adapters_mod, "PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM", provider_pub_pem, raising=False)
    monkeypatch.setattr(adapters_mod, "PINNED_PLATFORM_DEPLOYMENT_PUBLIC_KEY_PEM", platform_pub_pem, raising=False)

    adapter_mutated = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=None,  # Uses class default_http_transport
    )

    ok_mut, err_mut = adapter_mutated.send("n_r18_mut", "webhook", "ops-lead", "Title", "Detail")
    assert ok_mut is True
    receipt_mut = adapter_mutated.delivery_receipts[-1]
    assert receipt_mut["status"] == "TEST_ONLY"
    assert receipt_mut["status"] != "DELIVERED"

    # 6. Round 19 Negative Mutation Verification (Finding B1): Dual alias & class default transport mutation.
    # Mutating both public key aliases (PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM / PINNED_PLATFORM_DEPLOYMENT_PUBLIC_KEY_PEM)
    # and class default transport (OnCallNotificationAdapter._default_http_transport).
    # Constructing OnCallNotificationAdapter with NO arguments MUST evaluate to TEST_ONLY, NEVER DELIVERED.
    monkeypatch.setattr(adapters_mod, "PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM", provider_pub_pem, raising=False)
    monkeypatch.setattr(adapters_mod, "PINNED_PLATFORM_DEPLOYMENT_PUBLIC_KEY_PEM", platform_pub_pem, raising=False)
    monkeypatch.setattr(OnCallNotificationAdapter, "_default_http_transport", staticmethod(authentic_asymmetric_transport))

    adapter_r19_mutated = OnCallNotificationAdapter()
    ok_r19, err_r19 = adapter_r19_mutated.send("n_r19_mut", "webhook", "ops-lead", "Title", "Detail")
    assert ok_r19 is True
    receipt_r19 = adapter_r19_mutated.delivery_receipts[-1]
    assert receipt_r19["status"] == "TEST_ONLY"
    assert receipt_r19["status"] != "DELIVERED"

    # 7. Round 20 Negative Mutation Verification (Finding B1): Caller-selected verifier URL and unauthenticated verifier response.
    # A caller-selected loopback verifier URL, unauthenticated boolean response, injected provider transport,
    # redirect, field mismatch, or stale timestamp MUST NEVER evaluate to DELIVERED.
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class CallerVerifierHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length:
                self.rfile.read(content_length)
            body = json.dumps({"verified": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    verifier_server = HTTPServer(("127.0.0.1", 0), CallerVerifierHandler)
    v_port = verifier_server.server_port
    v_thread = threading.Thread(target=verifier_server.serve_forever, daemon=True)
    v_thread.start()

    try:
        monkeypatch.setenv("EXTERNAL_ONCALL_VERIFIER_URL", f"http://127.0.0.1:{v_port}/caller-verifier")
        monkeypatch.setenv("REQUIRE_EXTERNAL_VERIFICATION", "true")

        def caller_provider_transport(_url, _payload):
            return 200, {"provider_receipt_id": "caller-provider-receipt-r20"}

        adapter_r20 = OnCallNotificationAdapter(
            endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
            http_transport=caller_provider_transport,
        )
        ok_r20, err_r20 = adapter_r20.send("n_r20_mut", "webhook", "ops-lead", "Round 20", "Caller-selected verifier")
        assert ok_r20 is True
        receipt_r20 = adapter_r20.delivery_receipts[-1]
        assert receipt_r20["status"] == "PENDING_VERIFICATION"
        assert receipt_r20["status"] != "DELIVERED"
    finally:
        verifier_server.shutdown()
        verifier_server.server_close()

    # 8. Round 21 Negative Mutation Verification (Finding B1): Full same-process composition replacement.
    # Replacing canonical verifier URL, pinned verifier key, class default provider transport, and urllib opener
    # in-process MUST NEVER evaluate to DELIVERED.
    attacker_key = ed25519.Ed25519PrivateKey.generate()
    attacker_public_pem = attacker_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    def caller_provider_transport_r21(_url: str, _payload: dict) -> tuple[int, dict]:
        return 200, {"provider_receipt_id": "caller-provider-receipt-r21"}

    class CallerVerifierResponseR21:
        status = 200

        def __init__(self, url: str, body: bytes) -> None:
            self._url = url
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self) -> str:
            return self._url

        def read(self) -> bytes:
            return self._body

    class CallerVerifierOpenerR21:
        def open(self, request, timeout=5):
            request_payload = json.loads(request.data.decode("utf-8"))
            response_timestamp = datetime.now(UTC).isoformat()
            response_status = "VERIFIED"
            signature_payload = (
                "verifier_resp:"
                f"{request_payload['delivery_id']}:"
                f"{request_payload['provider_receipt_id']}:"
                f"{request_payload['request_hash']}:"
                f"{request_payload['release_sha']}:"
                f"{request_payload['nonce']}:"
                f"{response_timestamp}:"
                f"{response_status}"
            ).encode()
            response_payload = {
                "delivery_id": request_payload["delivery_id"],
                "provider_receipt_id": request_payload["provider_receipt_id"],
                "request_hash": request_payload["request_hash"],
                "release_sha": request_payload["release_sha"],
                "nonce": request_payload["nonce"],
                "timestamp": response_timestamp,
                "verifier_status": response_status,
                "verifier_signature": base64.b64encode(
                    attacker_key.sign(signature_payload)
                ).decode("utf-8"),
            }
            return CallerVerifierResponseR21(
                request.full_url,
                json.dumps(response_payload).encode("utf-8"),
            )

    attacker_verifier_url = "https://caller-verifier.evil.example/verify"
    monkeypatch.setattr(adapters_mod, "CANONICAL_PINNED_EXTERNAL_VERIFIER_URL", attacker_verifier_url, raising=False)
    monkeypatch.setattr(adapters_mod, "PINNED_EXTERNAL_VERIFIER_PUBLIC_KEY_PEM", attacker_public_pem, raising=False)
    monkeypatch.setattr(OnCallNotificationAdapter, "_default_http_transport", staticmethod(caller_provider_transport_r21))
    import urllib.request
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_args, **_kwargs: CallerVerifierOpenerR21())
    monkeypatch.setenv("REQUIRE_EXTERNAL_VERIFICATION", "true")

    adapter_r21 = OnCallNotificationAdapter()
    ok_r21, err_r21 = adapter_r21.send("n_r21_mut", "webhook", "ops-lead", "Round 21", "Full composition replacement")
    assert ok_r21 is True
    receipt_r21 = adapter_r21.delivery_receipts[-1]
    assert receipt_r21["status"] == "PENDING_VERIFICATION"
    assert receipt_r21["status"] != "DELIVERED"


def test_delivery_authority_readback_boundary_verification() -> None:
    import base64
    from datetime import timedelta

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityReadback,
        DeliveryAuthorityRecord,
    )

    auth_priv_key = ed25519.Ed25519PrivateKey.generate()
    auth_pub_key = auth_priv_key.public_key()
    pub_pem = auth_pub_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    delivery_id = "del-test-auth-100"
    prov_receipt_id = "prov-rcpt-auth-100"
    req_hash = "a" * 64
    rel_sha = "f" * 40
    oncall_route = "ops-lead"
    ts_str = datetime.now(UTC).isoformat()
    issuer_id = CANONICAL_AUTHORITY_ISSUER_IDENTITY

    sig_payload = (
        f"authority_record:{delivery_id}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{ts_str}:{issuer_id}"
    ).encode()
    sig_b64 = base64.b64encode(auth_priv_key.sign(sig_payload)).decode("utf-8")

    record = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=sig_b64,
    )

    readback = DeliveryAuthorityReadback(
        authority_public_key_pem=pub_pem,
        allowed_issuer_identity=issuer_id,
    )

    # 1. Positive verification
    is_del, status, err = readback.verify_authority_record(record, expected_release_sha=rel_sha, expected_delivery_id=delivery_id)
    assert is_del is True
    assert status == "DELIVERED"
    assert err is None

    # 2. Negative: Invalid payload type / dict
    is_del, status, err = readback.verify_authority_record("not-a-record", expected_release_sha=rel_sha)
    assert is_del is False
    assert status == "PENDING_VERIFICATION"
    assert "Invalid authority record type" in str(err)

    # 3. Negative: Unauthorized issuer identity
    bad_issuer_rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity="urn:unauthorized:issuer",
        issuer_signature=sig_b64,
    )
    is_del, status, err = readback.verify_authority_record(bad_issuer_rec, expected_release_sha=rel_sha)
    assert is_del is False
    assert status == "PENDING_VERIFICATION"
    assert "Unauthorized issuer identity" in str(err)

    # 4. Negative: Delivery ID mismatch
    is_del, status, err = readback.verify_authority_record(record, expected_release_sha=rel_sha, expected_delivery_id="del-wrong-999")
    assert is_del is False
    assert status == "PENDING_VERIFICATION"
    assert "Delivery ID mismatch" in str(err)

    # 5. Negative: Release SHA mismatch
    is_del, status, err = readback.verify_authority_record(record, expected_release_sha="e" * 40)
    assert is_del is False
    assert status == "PENDING_VERIFICATION"
    assert "Release SHA mismatch" in str(err)

    # 6. Negative: Stale timestamp (> 300s)
    stale_ts = (datetime.now(UTC) - timedelta(seconds=400)).isoformat()
    stale_sig_payload = (
        f"authority_record:{delivery_id}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{stale_ts}:{issuer_id}"
    ).encode()
    stale_sig_b64 = base64.b64encode(auth_priv_key.sign(stale_sig_payload)).decode("utf-8")
    stale_rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=stale_ts,
        issuer_identity=issuer_id,
        issuer_signature=stale_sig_b64,
    )
    is_del, status, err = readback.verify_authority_record(stale_rec, expected_release_sha=rel_sha)
    assert is_del is False
    assert status == "PENDING_VERIFICATION"
    assert "freshness window" in str(err)

    # 7. Negative: Forged signature
    forged_rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=base64.b64encode(b"forged-signature-bytes-32-len-x").decode("utf-8"),
    )
    is_del, status, err = readback.verify_authority_record(forged_rec, expected_release_sha=rel_sha)
    assert is_del is False
    assert status == "PENDING_VERIFICATION"
    assert "signature verification failed" in str(err).lower()


def test_application_adapter_never_issues_delivered_status(monkeypatch: Any) -> None:
    from modules.notifications import OnCallNotificationAdapter

    rel_sha = "a" * 40
    monkeypatch.setenv("RELEASE_SHA", rel_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", rel_sha)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", "test-secret-456")

    # Mock HTTP transport returning 200 with arbitrary delivered payloads
    def mock_200_transport(url: str, payload: dict) -> tuple[int, dict]:
        return (200, {"status": "delivered", "provider_receipt_id": "rcpt-1", "delivered": True})

    # Test under default transport mode
    adapter_default = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=mock_200_transport,
    )
    ok_def, err_def = adapter_default.send("nid-app-1", "webhook", "ops-lead", "Test Title", "Test Detail")
    assert ok_def is True
    receipt_def = adapter_default.delivery_receipts[-1]
    assert receipt_def["status"] == "TEST_ONLY"
    assert receipt_def["status"] != "DELIVERED"

    # Test under external verification enabled mode
    monkeypatch.setenv("REQUIRE_EXTERNAL_VERIFICATION", "true")
    adapter_req = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=mock_200_transport,
    )
    ok_req, err_req = adapter_req.send("nid-app-2", "webhook", "ops-lead", "Test Title", "Test Detail")
    assert ok_req is True
    receipt_req = adapter_req.delivery_receipts[-1]
    assert receipt_req["status"] == "PENDING_VERIFICATION"
    assert receipt_req["status"] != "DELIVERED"


def test_local_evidence_and_loopback_rejects_real_delivery_claim(monkeypatch: Any) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from modules.notifications import OnCallNotificationAdapter

    rel_sha = "b" * 40
    monkeypatch.setenv("RELEASE_SHA", rel_sha)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", rel_sha)
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", "test-secret-789")

    class LoopbackHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8")) if body else {}
            resp = {"status": "delivered", "delivered": True, "route": payload.get("user_id")}
            resp_bytes = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), LoopbackHandler)
    server_port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        adapter = OnCallNotificationAdapter(
            endpoint_url=f"http://127.0.0.1:{server_port}/api/v1/alerts",
            http_transport=None,
        )
        ok, err = adapter.send("nid-lb-1", "webhook", "ops-lead", "Loopback Alert", "Test Detail")
        assert ok is True
        receipt = adapter.delivery_receipts[-1]
        assert receipt["status"] in {"TEST_ONLY", "PENDING_VERIFICATION"}
        assert receipt["status"] != "DELIVERED"
    finally:
        server.shutdown()
