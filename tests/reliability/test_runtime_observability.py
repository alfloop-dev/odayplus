"""Reliability / observability acceptance tests for ODP-R7-001.

Maps to the task acceptance criteria and ODP-SD-11 §12 / ODP-SD-10 §12:

- AC1  logs include timestamp/service/actor/correlation_id/resource/result/error_code
- AC2  metrics include latency/error/job/data/model/business KPIs
- AC3  at least one E2E trace links API/Event/Worker/Data/Model/Decision/Report
- AC4  backup/restore and DR drill runbooks exist
- plus: monitoring config is consistent with the metric catalog
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from shared.audit import AuditEvent, InMemoryAuditLog


def _generate_test_authority_key():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.generate()


def _get_pub_pem(priv_key):
    from cryptography.hazmat.primitives import serialization

    return priv_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

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
    scheduler = ODayScheduler(persistence=persistence, telemetry=telemetry, tenant_id="tenant-test")

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
    nid2 = authentic_router.trigger_alert(
        "audit-write-failure", "Durable storage write timeout on DB query"
    )

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


def test_unconfigured_route_fails_closed(tmp_path: Path, monkeypatch: Any) -> None:
    from modules.notifications import (
        InMemoryNotificationRepository,
        NotificationService,
        OnCallNotificationAdapter,
    )
    from shared.observability.alerts import AlertRouter

    monkeypatch.setenv("RELEASE_SHA", "a" * 40)
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


def test_alert_release_identity_contract_and_propagation(monkeypatch: Any) -> None:
    from modules.notifications import (
        InMemoryNotificationRepository,
        NotificationService,
        OnCallNotificationAdapter,
    )
    from shared.observability.alerts import AlertRouter

    valid_sha = "f" * 40
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    repo = InMemoryNotificationRepository()
    adapter = OnCallNotificationAdapter(http_transport=lambda u, p: (200, "ok"))
    service = NotificationService(repository=repo, adapter=adapter)
    service.set_preferences("ops-lead", ["webhook"])

    router = AlertRouter(notification_service=service)
    routed = router.route_alert("audit-write-failure")
    assert routed["release_sha"] == valid_sha
    assert routed["release_identity_bound"] is True

    nid = router.trigger_alert("audit-write-failure", "Test details")
    assert nid is not None
    receipts = [r for r in adapter.delivery_receipts if r.get("notification_id") == nid]
    assert len(receipts) >= 1
    assert f"Release SHA: {valid_sha}" in receipts[0]["detail"]






def test_alert_release_identity_mutation_fails_closed(monkeypatch: Any) -> None:
    from modules.notifications import (
        InMemoryNotificationRepository,
        NotificationService,
        OnCallNotificationAdapter,
    )
    from shared.observability.alerts import AlertRouter

    repo = InMemoryNotificationRepository()
    adapter = OnCallNotificationAdapter(http_transport=lambda u, p: (200, "ok"))
    service = NotificationService(repository=repo, adapter=adapter)

    monkeypatch.delenv("RELEASE_SHA", raising=False)
    monkeypatch.delenv("TRUSTED_DEPLOYED_RELEASE_SHA", raising=False)

    router = AlertRouter(notification_service=service, release_sha=None)

    # 1. Routing without release_sha fails closed
    with pytest.raises(
        ValueError, match="Release identity contract violation: release_sha is unbound or invalid"
    ):
        router.route_alert("audit-write-failure")

    # 2. Mutating config to remove release_identity fails closed
    router.config.pop("release_identity", None)
    with pytest.raises(
        ValueError, match="Alert release identity configuration is missing or unbound"
    ):
        router.validate_routing_config()

    # 3. Mutating bound=False fails closed
    router.config["release_identity"] = {"enabled": True, "bound": False}
    with pytest.raises(
        ValueError, match="Alert release identity configuration is missing or unbound"
    ):
        router.validate_routing_config()


def test_alert_router_strict_sha_validation_and_caller_override_rejection(
    monkeypatch: Any,
) -> None:
    from modules.notifications import (
        InMemoryNotificationRepository,
        NotificationService,
        OnCallNotificationAdapter,
    )
    from shared.observability.alerts import AlertRouter

    repo = InMemoryNotificationRepository()
    adapter = OnCallNotificationAdapter(http_transport=lambda u, p: (200, "ok"))
    service = NotificationService(repository=repo, adapter=adapter)

    trusted_sha = "a" * 40
    monkeypatch.setenv("RELEASE_SHA", trusted_sha)
    router = AlertRouter(notification_service=service)

    # 1. Malformed SHA inputs fail closed with ValueError
    malformed_inputs = ["not-a-sha", "12345", "a" * 39, "a" * 41, "g" * 40, ""]
    for bad_sha in malformed_inputs:
        with pytest.raises(ValueError, match="is not a valid 40-character hex SHA"):
            router.resolve_release_sha(bad_sha)
        with pytest.raises(ValueError, match="is not a valid 40-character hex SHA"):
            router.route_alert("audit-write-failure", release_sha=bad_sha)

    # 2. Caller input overriding router/deployed SHA fails closed with ValueError
    mismatched_sha = "b" * 40
    with pytest.raises(ValueError, match="does not match trusted deployed release SHA"):
        router.resolve_release_sha(mismatched_sha)
    with pytest.raises(ValueError, match="does not match trusted deployed release SHA"):
        router.route_alert("audit-write-failure", release_sha=mismatched_sha)

    # 3. Unbound router rejects caller-supplied SHA override
    monkeypatch.delenv("RELEASE_SHA", raising=False)
    monkeypatch.delenv("TRUSTED_DEPLOYED_RELEASE_SHA", raising=False)
    unbound_router = AlertRouter(notification_service=service, release_sha=None)
    with pytest.raises(ValueError, match="router has no trusted deployed release SHA bound"):
        unbound_router.resolve_release_sha("a" * 40)

    # 4. Valid matching caller-supplied SHA succeeds
    bound_router = AlertRouter(notification_service=service, release_sha=trusted_sha)
    assert bound_router.resolve_release_sha(trusted_sha) == trusted_sha
    routed = bound_router.route_alert("audit-write-failure", release_sha=trusted_sha)
    assert routed["release_sha"] == trusted_sha

    # 5. Config validation for exact_sha_binding and per-alert release_identity_bound
    bound_router.config["release_identity"]["exact_sha_binding"] = "not-a-full-sha"
    with pytest.raises(ValueError, match="is not a valid 40-character hex SHA or placeholder"):
        bound_router.validate_routing_config()

    bound_router.config["release_identity"]["exact_sha_binding"] = "c" * 40
    with pytest.raises(ValueError, match="does not match trusted deployed release SHA"):
        bound_router.validate_routing_config()

    bound_router.config["release_identity"]["exact_sha_binding"] = "${RELEASE_SHA}"
    bound_router.config["alerts"][0]["release_identity_bound"] = False
    with pytest.raises(ValueError, match="release_identity_bound is missing or false"):
        bound_router.validate_routing_config()


def test_validate_routing_config_rejects_malformed_exact_sha_binding(monkeypatch: Any) -> None:
    from modules.notifications import (
        InMemoryNotificationRepository,
        NotificationService,
        OnCallNotificationAdapter,
    )
    from shared.observability.alerts import AlertRouter

    repo = InMemoryNotificationRepository()
    adapter = OnCallNotificationAdapter(http_transport=lambda u, p: (200, "ok"))
    service = NotificationService(repository=repo, adapter=adapter)

    trusted_sha = "a" * 40
    monkeypatch.setenv("RELEASE_SHA", trusted_sha)
    router = AlertRouter(notification_service=service)

    # 1. Malformed non-placeholder exact_sha_binding (e.g. 'not-a-full-sha') fails closed
    router.config["release_identity"]["exact_sha_binding"] = "not-a-full-sha"
    with pytest.raises(ValueError, match="is not a valid 40-character hex SHA or placeholder"):
        router.validate_routing_config()

    # 2. Non-string exact_sha_binding fails closed
    router.config["release_identity"]["exact_sha_binding"] = 12345
    with pytest.raises(ValueError, match="exact_sha_binding must be a string"):
        router.validate_routing_config()

    # 3. Unsupported / malformed placeholders fail closed even when RELEASE_SHA is present
    malformed_placeholders = [
        "$TYPO_RELEASE_SHA",
        "${TYPO_RELEASE_SHA}",
        "$",
        "$$",
        "$RELEASE_SHA",
        "${RELEASE_SHA_TYPO}",
    ]
    for bad_placeholder in malformed_placeholders:
        router.config["release_identity"]["exact_sha_binding"] = bad_placeholder
        with pytest.raises(ValueError, match="is not a valid 40-character hex SHA or placeholder"):
            router.validate_routing_config()


def test_release_sha_dashboard_traceability_and_watch_window_receipt(
    tmp_path: Path, monkeypatch: Any
) -> None:
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
        proof_hash = hashlib.sha256(
            json.dumps(raw_resp, sort_keys=True).encode("utf-8")
        ).hexdigest()
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
        raw_resp = {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha_1,
            "timeSeries": ts,
        }
        proof_hash = hashlib.sha256(
            json.dumps(raw_resp, sort_keys=True).encode("utf-8")
        ).hexdigest()
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
        raw_resp = {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha_1,
            "timeSeries": ts,
        }
        proof_hash = hashlib.sha256(
            json.dumps(raw_resp, sort_keys=True).encode("utf-8")
        ).hexdigest()
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
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 3.0},
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
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 10.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 10.0},
                        },
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
        raw_resp = {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha,
            "timeSeries": ts,
        }
        proof_hash = hashlib.sha256(
            json.dumps(raw_resp, sort_keys=True).encode("utf-8")
        ).hexdigest()
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
                ts_return = (
                    mock_posted_series
                    if mock_posted_series
                    else [
                        {
                            "metric": {
                                "type": "custom.googleapis.com/api_request_count",
                                "labels": {
                                    "release_sha": r_sha,
                                    "service": "api",
                                    "route": "/jobs",
                                    "status": "200",
                                },
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
                )
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
                                "labels": {
                                    "release_sha": test_sha,
                                    "service": "api",
                                    "route": "/jobs",
                                    "status": "200",
                                },
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
    def attacker_type_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
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
    def old_point_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        if method == "POST":
            return 200, {}
        return 200, {
            "gcp_project": gcp_proj,
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_request_count",
                        "labels": {
                            "release_sha": test_sha,
                            "service": "api",
                            "route": "/jobs",
                            "status": "200",
                        },
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
    def single_point_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
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
    def attacker_counter_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/attacker_counter",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
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


def test_verify_watch_window_receipt_rejects_tampered_proof_or_circular_metric(
    tmp_path: Path, monkeypatch: Any
) -> None:
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

    def valid_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
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
        raw_resp = {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": ts,
        }
        proof_hash = hashlib.sha256(
            json.dumps(raw_resp, sort_keys=True).encode("utf-8")
        ).hexdigest()
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
    assert (
        verify_watch_window_receipt(expected_release_sha=test_sha, receipt_path=receipt_file)[
            "status"
        ]
        == "WATCH_PASSED"
    )

    # Attack 3a: Tamper provider metric in stored receipt to circular status metric with value 0
    raw_data = json.loads(receipt_file.read_text(encoding="utf-8"))
    raw_data["monitoring_query_execution"]["provider_query_response"]["timeSeries"][0]["metric"][
        "type"
    ] = "custom.googleapis.com/deployment_watch_window_status"
    raw_data["monitoring_query_execution"]["provider_query_response"]["timeSeries"][0]["points"][0][
        "value"
    ]["doubleValue"] = 0
    receipt_file.write_text(json.dumps(raw_data), encoding="utf-8")

    with pytest.raises(
        ValueError, match="circular deployment_watch_window_status metric|integrity check failed"
    ):
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

    def request_count_only_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
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
        raw_resp = {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": ts,
        }
        proof_hash = hashlib.sha256(
            json.dumps(raw_resp, sort_keys=True).encode("utf-8")
        ).hexdigest()
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

    with pytest.raises(
        ValueError,
        match="requires an explicit independent error/failure signal AND latency/health signal",
    ):
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

    def valid_full_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
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
        raw_resp = {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": ts,
        }
        proof_hash = hashlib.sha256(
            json.dumps(raw_resp, sort_keys=True).encode("utf-8")
        ).hexdigest()
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
    assert (
        verify_watch_window_receipt(expected_release_sha=test_sha, receipt_path=receipt_file)[
            "status"
        ]
        == "WATCH_PASSED"
    )

    # Tamper 2a: Change provider project_id inside provider_query_response
    raw = json.loads(receipt_file.read_text(encoding="utf-8"))
    raw["monitoring_query_execution"]["provider_query_response"]["timeSeries"][0]["resource"][
        "labels"
    ]["project_id"] = "wrong-project"
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
    raw["monitoring_query_execution"]["provider_query_response"]["timeSeries"][1]["points"][1][
        "value"
    ]["doubleValue"] = 9999.0
    receipt_file.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="Provider signature mismatch|Stored provider proof point_values mismatch|point_values mismatch|failed independent cryptographic authentication",
    ):
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
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
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
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
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
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
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
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 12.5},
                        },
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
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": -5.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 10.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 10.0},
                        },
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
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": -100.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 10.0},
                        },
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
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": test_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": float("nan")},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 10.0},
                        },
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

    record_data_signal(
        "pg16", "forecast_view", freshness_hours=1.5, quality_score=0.98, feature_null_rate=0.01
    )
    record_model_signal(
        "forecast_ops",
        "learninghub",
        prediction_count=100,
        model_error=12.5,
        interval_coverage=0.95,
        drift_score=0.02,
    )
    record_business_kpi_signal("heatzone_topk_adoption_rate", 0.92)
    record_business_kpi_signal("price_hard_constraint_violation_count", 0.0)

    snap = reg.snapshot()
    assert "data_freshness_hours" in snap
    assert "prediction_count" in snap
    assert "heatzone_topk_adoption_rate" in snap

    test_sha = "a" * 40

    def mock_transport(
        method: str, url: str, params: dict = None, payload: dict = None, headers: dict = None
    ) -> tuple[int, dict]:
        now_iso = datetime.now(UTC).isoformat()
        if method == "POST":
            return 200, {"status": "ok"}
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": test_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": f"custom.googleapis.com/{name}",
                        "labels": {**dict(items[0].get("labels", {})), "release_sha": test_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
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

        reg.register(
            MetricDefinition(
                "adlift_incremental_gm", MetricType.GAUGE, MetricCategory.BUSINESS, "AdLift GM"
            )
        )
        reg.set("adlift_incremental_gm", val)

        def mock_tp(
            method: str, url: str, params: dict = None, payload: dict = None, headers: dict = None
        ) -> tuple[int, dict]:
            now_iso = datetime.now(UTC).isoformat()
            if method == "POST":
                return 200, {"status": "ok"}
            return 200, {
                "gcp_project": "alfaloop-data-project",
                "release_sha": test_sha,
                "timeSeries": [
                    {
                        "metric": {
                            "type": "custom.googleapis.com/adlift_incremental_gm",
                            "labels": {"release_sha": test_sha},
                        },
                        "resource": {
                            "type": "global",
                            "labels": {"project_id": "alfaloop-data-project"},
                        },
                        "points": [
                            {"interval": {"endTime": now_iso}, "value": {"doubleValue": val}}
                        ],
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

    receipt_path = (
        Path(__file__).resolve().parents[2] / "docs" / "evidence" / "watch_window_receipt.json"
    )
    assert receipt_path.exists()

    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    sha = receipt_data["release_sha"]

    verified_status = receipt_data.get("status")
    assert verified_status in {"LOCAL_TEST_ONLY", "WATCH_PASSED"}
    assert receipt_data["release_sha"] == sha
    with pytest.raises(ValueError, match="Invalid watch-window status|Watch-window verification failed"):
        verify_watch_window_receipt(expected_release_sha=sha, receipt_path=receipt_path)


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
    adapter1 = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts"
    )
    ok, err = adapter1.send("n1", "webhook", "ops-lead", "Title", "Detail")
    assert ok is False
    assert err is not None and (
        "authentic 40-character release_sha" in err or "missing or invalid" in err
    )
    assert adapter1.delivery_receipts[-1]["status"] == "FAILED"

    monkeypatch.setenv("RELEASE_SHA", "0" * 40)
    ok_zero, err_zero = adapter1.send("n1_zero", "webhook", "ops-lead", "Title", "Detail")
    assert ok_zero is False
    assert err_zero is not None and (
        "unauthenticated release" in err_zero or "missing or invalid" in err_zero
    )
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

    def valid_watch_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        prov_sec = "test-secret-456"
        prov_rcpt = "prov-rcpt-aaaaaaaa"
        raw_resp = {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_error_count",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 10.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 12.0},
                        },
                    ],
                },
            ],
        }
        proof_hash = hashlib.sha256(
            json.dumps(raw_resp, sort_keys=True).encode("utf-8")
        ).hexdigest()
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
    assert (
        verify_watch_window_receipt(expected_release_sha=valid_sha, receipt_path=receipt_file)[
            "status"
        ]
        == "WATCH_PASSED"
    )

    # Tamper provider signature in stored receipt
    raw_rcpt = json.loads(receipt_file.read_text(encoding="utf-8"))
    raw_rcpt["monitoring_query_execution"]["provider_signature"] = "tampered-provider-sig-123"
    receipt_file.write_text(json.dumps(raw_rcpt, indent=2), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="Provider signature mismatch|integrity check failed|failed independent cryptographic authentication",
    ):
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

    adapter_no_secret = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts"
    )
    ok1, err1 = adapter_no_secret.send("n_r11_1", "webhook", "ops-lead", "Title", "Detail")
    assert ok1 is False
    assert err1 is not None and "ONCALL_PROVIDER_SECRET" in err1
    assert adapter_no_secret.delivery_receipts[-1]["status"] == "FAILED"

    # 2. OnCall delivery without TRUSTED_DEPLOYED_RELEASE_SHA fails closed immediately
    monkeypatch.setenv("ONCALL_PROVIDER_SECRET", "provider-secret-xyz")
    monkeypatch.delenv("TRUSTED_DEPLOYED_RELEASE_SHA", raising=False)
    monkeypatch.delenv("EXPECTED_RELEASE_SHA", raising=False)

    adapter_no_trusted = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts"
    )
    ok2, err2 = adapter_no_trusted.send("n_r11_2", "webhook", "ops-lead", "Title", "Detail")
    assert ok2 is False
    assert err2 is not None and "trusted deployed release binding" in err2
    assert adapter_no_trusted.delivery_receipts[-1]["status"] == "FAILED"

    # 3. Watch window query response missing provider-issued receipt/signature/readback identity fails closed (no local fallbacks)
    monkeypatch.setenv("TRUSTED_DEPLOYED_RELEASE_SHA", valid_sha)
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    def missing_provider_fields_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_error_count",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 10.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 12.0},
                        },
                    ],
                },
            ],
        }

    with pytest.raises(
        ValueError, match="missing authentic provider-issued provider_receipt_id|strictly forbidden"
    ):
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

    def valid_watch_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        return 200, {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha,
            "provider_receipt_id": "prov-rcpt-123",
            "provider_signature": sig_valid_format,
            "provider_readback_identity": rb_valid,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_error_count",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 10.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 12.0},
                        },
                    ],
                },
            ],
        }

    with pytest.raises(ValueError, match="Fail-closed|provider trust root"):
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

    def authentic_proof_watch_transport(
        method: str, url: str, params: dict = None, payload: dict = None
    ) -> tuple[int, dict]:
        prov_rcpt = "prov-rcpt-authentic-r13"
        raw_resp = {
            "gcp_project": "alfaloop-data-project",
            "release_sha": valid_sha,
            "timeSeries": [
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_error_count",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 0.0},
                        },
                    ],
                },
                {
                    "metric": {
                        "type": "custom.googleapis.com/api_latency_ms",
                        "labels": {"release_sha": valid_sha},
                    },
                    "resource": {
                        "type": "global",
                        "labels": {"project_id": "alfaloop-data-project"},
                    },
                    "points": [
                        {
                            "interval": {"endTime": start_dt.isoformat()},
                            "value": {"doubleValue": 10.0},
                        },
                        {
                            "interval": {"endTime": end_dt.isoformat()},
                            "value": {"doubleValue": 12.0},
                        },
                    ],
                },
            ],
        }
        proof_hash = hashlib.sha256(
            json.dumps(raw_resp, sort_keys=True).encode("utf-8")
        ).hexdigest()
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
    assert (
        verify_watch_window_receipt(expected_release_sha=valid_sha, receipt_path=receipt_file)[
            "status"
        ]
        == "WATCH_PASSED"
    )


def test_round8_worker_and_scheduler_export_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.scheduler.oday_scheduler.main import ODayScheduler
    from apps.worker.oday_worker.main import ODayWorker

    valid_sha = "b" * 40
    monkeypatch.setenv("RELEASE_SHA", valid_sha)
    monkeypatch.setenv("GCP_PROJECT", "alfaloop-data-project")

    worker = ODayWorker()
    scheduler = ODayScheduler(tenant_id="tenant-test")

    # Local export returns None or raises/exports cleanly without AttributeError
    assert hasattr(worker, "export_metrics")
    assert hasattr(scheduler, "export_metrics")


def test_round14_remediation_findings_b1_loopback_socket_mutation_verified(
    monkeypatch: Any,
) -> None:
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


def test_round16_remediation_findings_b1_b2_b3_negative_mutations_and_positive_verification(
    monkeypatch: Any, tmp_path: Path
) -> None:
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

    provider_pub_pem = (
        provider_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

    platform_pub_pem = (
        platform_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )  # 1. Round 16 Negative Mutation A (Finding B3): Canonical hostname with unauthorized port 444 and query ?redirect=evil.example, no attestation, caller SHA env values -> TEST_ONLY, NEVER DELIVERED

    def mock_200_transport(url: str, payload: dict) -> tuple[int, dict]:
        return (
            200,
            {
                "status": "ok",
                "provider_receipt_id": "prov-rcpt-123",
                "provider_signature": "invalid-sig",
            },
        )

    adapter_port_query_mutation = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus:444/api/v1/alerts?redirect=evil.example",
        http_transport=mock_200_transport,
        provider_public_key_pem=provider_pub_pem,
        platform_public_key_pem=platform_pub_pem,
    )
    ok_pq, err_pq = adapter_port_query_mutation.send(
        "n_r16_pq", "webhook", "ops-lead", "Title", "Detail"
    )
    assert ok_pq is True
    receipt_pq = adapter_port_query_mutation.delivery_receipts[-1]
    assert receipt_pq["status"] == "TEST_ONLY"
    assert receipt_pq["status"] != "DELIVERED"

    # 2. Round 16 Negative Mutation B (Finding B2): Unsigned deployment attestation file (missing platform signature) -> TEST_ONLY, NEVER DELIVERED
    unsigned_attestation_file = tmp_path / "unsigned_attestation.json"
    unsigned_attestation_file.write_text(
        json.dumps({"deployed_release_sha": valid_sha}), encoding="utf-8"
    )
    monkeypatch.setenv("DEPLOYMENT_ATTESTATION_PATH", str(unsigned_attestation_file))

    adapter_unsigned_attestation = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=mock_200_transport,
        provider_public_key_pem=provider_pub_pem,
        platform_public_key_pem=platform_pub_pem,
    )
    ok_unatt, err_unatt = adapter_unsigned_attestation.send(
        "n_r16_unatt", "webhook", "ops-lead", "Title", "Detail"
    )
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
    ok_ov, err_ov = adapter_caller_override.send(
        "n_r16_ov", "webhook", "ops-lead", "Title", "Detail"
    )
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
        json.dumps(
            {
                "deployed_release_sha": valid_sha,
                "platform_signature": plat_sig_b64,
            }
        ),
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

    monkeypatch.setattr(
        OnCallNotificationAdapter,
        "_default_http_transport",
        staticmethod(authentic_asymmetric_transport),
    )

    adapter_caller_keys_injected = OnCallNotificationAdapter(
        endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
        http_transport=None,  # Uses class default_http_transport
        provider_public_key_pem=provider_pub_pem,
        platform_public_key_pem=platform_pub_pem,
    )
    ok_inj, err_inj = adapter_caller_keys_injected.send(
        "n_r17_inj", "webhook", "ops-lead", "Title", "Detail"
    )
    assert ok_inj is True
    receipt_inj = adapter_caller_keys_injected.delivery_receipts[-1]
    assert receipt_inj["status"] == "TEST_ONLY"
    assert receipt_inj["status"] != "DELIVERED"

    import modules.notifications.infrastructure.adapters as adapters_mod

    # 5. Round 18 Negative Mutation Verification (Finding B1): No-argument adapter instantiation after module global and class default transport mutation.
    # Caller assigns custom key pairs to module globals (PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM / PINNED_PLATFORM_DEPLOYMENT_PUBLIC_KEY_PEM)
    # and mutates class default transport (OnCallNotificationAdapter._default_http_transport).
    # Constructing OnCallNotificationAdapter with NO arguments MUST evaluate to TEST_ONLY, NEVER DELIVERED.
    monkeypatch.setattr(
        adapters_mod, "PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM", provider_pub_pem, raising=False
    )
    monkeypatch.setattr(
        adapters_mod, "PINNED_PLATFORM_DEPLOYMENT_PUBLIC_KEY_PEM", platform_pub_pem, raising=False
    )

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
    monkeypatch.setattr(
        adapters_mod, "PINNED_ONCALL_PROVIDER_PUBLIC_KEY_PEM", provider_pub_pem, raising=False
    )
    monkeypatch.setattr(
        adapters_mod, "PINNED_PLATFORM_DEPLOYMENT_PUBLIC_KEY_PEM", platform_pub_pem, raising=False
    )
    monkeypatch.setattr(
        OnCallNotificationAdapter,
        "_default_http_transport",
        staticmethod(authentic_asymmetric_transport),
    )

    adapter_r19_mutated = OnCallNotificationAdapter()
    ok_r19, err_r19 = adapter_r19_mutated.send(
        "n_r19_mut", "webhook", "ops-lead", "Title", "Detail"
    )
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
        monkeypatch.setenv(
            "EXTERNAL_ONCALL_VERIFIER_URL", f"http://127.0.0.1:{v_port}/caller-verifier"
        )
        monkeypatch.setenv("REQUIRE_EXTERNAL_VERIFICATION", "true")

        def caller_provider_transport(_url, _payload):
            return 200, {"provider_receipt_id": "caller-provider-receipt-r20"}

        adapter_r20 = OnCallNotificationAdapter(
            endpoint_url="https://oncall-router.oday.plus/api/v1/alerts",
            http_transport=caller_provider_transport,
        )
        ok_r20, err_r20 = adapter_r20.send(
            "n_r20_mut", "webhook", "ops-lead", "Round 20", "Caller-selected verifier"
        )
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
    attacker_public_pem = (
        attacker_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )

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
                "verifier_signature": base64.b64encode(attacker_key.sign(signature_payload)).decode(
                    "utf-8"
                ),
            }
            return CallerVerifierResponseR21(
                request.full_url,
                json.dumps(response_payload).encode("utf-8"),
            )

    attacker_verifier_url = "https://caller-verifier.evil.example/verify"
    monkeypatch.setattr(
        adapters_mod, "CANONICAL_PINNED_EXTERNAL_VERIFIER_URL", attacker_verifier_url, raising=False
    )
    monkeypatch.setattr(
        adapters_mod, "PINNED_EXTERNAL_VERIFIER_PUBLIC_KEY_PEM", attacker_public_pem, raising=False
    )
    monkeypatch.setattr(
        OnCallNotificationAdapter,
        "_default_http_transport",
        staticmethod(caller_provider_transport_r21),
    )
    import urllib.request

    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *_args, **_kwargs: CallerVerifierOpenerR21()
    )
    monkeypatch.setenv("REQUIRE_EXTERNAL_VERIFICATION", "true")

    adapter_r21 = OnCallNotificationAdapter()
    ok_r21, err_r21 = adapter_r21.send(
        "n_r21_mut", "webhook", "ops-lead", "Round 21", "Full composition replacement"
    )
    assert ok_r21 is True
    receipt_r21 = adapter_r21.delivery_receipts[-1]
    assert receipt_r21["status"] == "PENDING_VERIFICATION"
    assert receipt_r21["status"] != "DELIVERED"


def test_delivery_authority_readback_boundary_verification(tmp_path: Any) -> None:
    import concurrent.futures
    from datetime import timedelta

    import pytest
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityReadback,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
        verify_durable_delivery_authority,
    )

    # 1. B1 Remediation Test: Standard constructor rejects caller-supplied trust roots
    with pytest.raises(TypeError, match="does not accept caller-supplied"):
        DeliveryAuthorityReadback(
            authority_public_key_pem="some-pem", allowed_issuer_identity="custom-issuer"
        )

    # 2. B1 Remediation Test: Missing or unconfigured durable authority store fails closed
    default_readback = DeliveryAuthorityReadback()
    is_del_def, status_def, err_def = default_readback.read_by_delivery_id(
        expected_delivery_id="del-unconfig-1",
        expected_provider_receipt_id="rcpt-1",
        expected_request_hash="a" * 64,
        expected_release_sha="f" * 40,
        expected_oncall_route="ops-lead",
    )
    assert is_del_def is False
    assert status_def == "PENDING_VERIFICATION"
    assert "Durable authority store is missing or unconfigured" in str(err_def)

    # Test helper subclass strictly inside test suite for custom test key testing
    class TestDeliveryAuthorityReadback(DeliveryAuthorityReadback):
        def __init__(
            self, authority_public_key_pem: str, allowed_issuer_identity: str, authority_store: Any
        ):
            self.authority_public_key_pem = authority_public_key_pem
            self.allowed_issuer_identity = allowed_issuer_identity
            self.authority_store = authority_store

    auth_priv_key = _generate_test_authority_key()
    pub_pem = _get_pub_pem(auth_priv_key)

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

    store_file = tmp_path / "authority_store.json"
    store = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    store._store_authority_record_for_testing(record)

    readback = TestDeliveryAuthorityReadback(
        authority_public_key_pem=pub_pem,
        allowed_issuer_identity=issuer_id,
        authority_store=store,
    )

    # 3. Positive durable readback by delivery_id with full mandatory bindings
    is_del, status, err = readback.read_by_delivery_id(
        expected_delivery_id=delivery_id,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=oncall_route,
    )
    assert is_del is True
    assert status == "DELIVERED"
    assert err is None

    # 4. B3 Replay Protection: Second attempt to read/consume same record is rejected
    is_del, status, err = readback.read_by_delivery_id(
        expected_delivery_id=delivery_id,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=oncall_route,
    )
    assert is_del is False
    assert status == "PENDING_VERIFICATION"
    assert "already been consumed" in str(err)

    # 5. B3 Restart-Safe Persistence: Reload store from file and verify replay is STILL rejected
    reloaded_store = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    reloaded_readback = TestDeliveryAuthorityReadback(
        authority_public_key_pem=pub_pem,
        allowed_issuer_identity=issuer_id,
        authority_store=reloaded_store,
    )
    is_del_re, status_re, err_re = reloaded_readback.read_by_delivery_id(
        expected_delivery_id=delivery_id,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=oncall_route,
    )
    assert is_del_re is False
    assert status_re == "PENDING_VERIFICATION"
    assert "already been consumed" in str(err_re)

    # 6. B3 Concurrent Reader Mutation: 10 parallel threads attempt atomic consume on same record
    concat_del_id = "del-test-auth-concurrent-1"
    sig_payload_conc = (
        f"authority_record:{concat_del_id}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{ts_str}:{issuer_id}"
    ).encode()
    rec_conc = DeliveryAuthorityRecord(
        delivery_id=concat_del_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=base64.b64encode(auth_priv_key.sign(sig_payload_conc)).decode("utf-8"),
    )
    store._store_authority_record_for_testing(rec_conc)

    def _attempt_read():
        return readback.read_by_delivery_id(
            expected_delivery_id=concat_del_id,
            expected_provider_receipt_id=prov_receipt_id,
            expected_request_hash=req_hash,
            expected_release_sha=rel_sha,
            expected_oncall_route=oncall_route,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_attempt_read) for _ in range(10)]
        results = [f.result() for f in futures]

    delivered_count = sum(1 for is_del, st, _ in results if is_del and st == "DELIVERED")
    rejected_count = sum(
        1 for is_del, st, _ in results if not is_del and st == "PENDING_VERIFICATION"
    )
    assert delivered_count == 1
    assert rejected_count == 9

    # 7. B4 Strict Canonical Format Validation Mutations
    del_2 = "del-test-auth-200"
    sig_payload_2 = (
        f"authority_record:{del_2}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{ts_str}:{issuer_id}"
    ).encode()
    rec_2 = DeliveryAuthorityRecord(
        delivery_id=del_2,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=base64.b64encode(auth_priv_key.sign(sig_payload_2)).decode("utf-8"),
    )
    store._store_authority_record_for_testing(rec_2)

    # (a) Non-hex / wrong length request_hash
    is_del, status, err = readback.verify_authority_record(
        rec_2,
        expected_delivery_id=del_2,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash="non-hex-hash-12345",
        expected_release_sha=rel_sha,
        expected_oncall_route=oncall_route,
    )
    assert is_del is False
    assert "must be exactly 64 hexadecimal characters" in str(err)

    # (b) Non-hex / wrong length release_sha
    is_del, status, err = readback.verify_authority_record(
        rec_2,
        expected_delivery_id=del_2,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash=req_hash,
        expected_release_sha="not-a-valid-sha",
        expected_oncall_route=oncall_route,
    )
    assert is_del is False
    assert "must be exactly 40 hexadecimal characters" in str(err)

    # (c) All-zero request_hash
    is_del, status, err = readback.verify_authority_record(
        rec_2,
        expected_delivery_id=del_2,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash="0" * 64,
        expected_release_sha=rel_sha,
        expected_oncall_route=oncall_route,
    )
    assert is_del is False
    assert "Invalid expected_request_hash" in str(err)

    # (d) Mismatched delivery ID
    is_del, status, err = readback.verify_authority_record(
        rec_2,
        expected_delivery_id="del-mismatch-888",
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=oncall_route,
    )
    assert is_del is False
    assert "Delivery ID mismatch" in str(err)

    # (e) Mismatched provider receipt ID
    is_del, status, err = readback.verify_authority_record(
        rec_2,
        expected_delivery_id=del_2,
        expected_provider_receipt_id="receipt-wrong",
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=oncall_route,
    )
    assert is_del is False
    assert "Provider receipt ID mismatch" in str(err)

    # (f) Mismatched on-call route
    is_del, status, err = readback.verify_authority_record(
        rec_2,
        expected_delivery_id=del_2,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route="attacker-route",
    )
    assert is_del is False
    assert "On-call route mismatch" in str(err)

    # (g) Mismatched release SHA
    is_del, status, err = readback.verify_authority_record(
        rec_2,
        expected_delivery_id=del_2,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash=req_hash,
        expected_release_sha="e" * 40,
        expected_oncall_route=oncall_route,
    )
    assert is_del is False
    assert "Release SHA mismatch" in str(err)

    # (h) Unauthorized issuer identity
    bad_issuer_rec = DeliveryAuthorityRecord(
        delivery_id=del_2,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity="urn:unauthorized:issuer",
        issuer_signature=sig_b64,
    )
    is_del, status, err = readback.verify_authority_record(
        bad_issuer_rec,
        expected_delivery_id=del_2,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=oncall_route,
    )
    assert is_del is False
    assert "Unauthorized issuer identity" in str(err)

    # (i) Stale timestamp (> 300s)
    stale_ts = (datetime.now(UTC) - timedelta(seconds=400)).isoformat()
    stale_sig_payload = (
        f"authority_record:{del_2}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{stale_ts}:{issuer_id}"
    ).encode()
    stale_sig_b64 = base64.b64encode(auth_priv_key.sign(stale_sig_payload)).decode("utf-8")
    stale_rec = DeliveryAuthorityRecord(
        delivery_id=del_2,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=stale_ts,
        issuer_identity=issuer_id,
        issuer_signature=stale_sig_b64,
    )
    is_del, status, err = readback.verify_authority_record(
        stale_rec,
        expected_delivery_id=del_2,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=oncall_route,
    )
    assert is_del is False
    assert "freshness window" in str(err)

    # (j) Forged signature
    forged_rec = DeliveryAuthorityRecord(
        delivery_id=del_2,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=base64.b64encode(b"forged-signature-bytes-32-len-x").decode("utf-8"),
    )
    is_del, status, err = readback.verify_authority_record(
        forged_rec,
        expected_delivery_id=del_2,
        expected_provider_receipt_id=prov_receipt_id,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=oncall_route,
    )
    assert is_del is False
    assert "signature verification failed" in str(err).lower()

    # Store an unauthentic record signed with a test key (not pinned key)
    unauth_priv_key = ed25519.Ed25519PrivateKey.generate()
    del_unauth = "del-unauth-888"
    unauth_sig_payload = (
        f"authority_record:{del_unauth}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{ts_str}:{issuer_id}"
    ).encode()
    unauth_sig_b64 = base64.b64encode(unauth_priv_key.sign(unauth_sig_payload)).decode("utf-8")
    unauth_rec = DeliveryAuthorityRecord(
        delivery_id=del_unauth,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=unauth_sig_b64,
    )
    store._store_authority_record_for_testing(unauth_rec)

    os.environ["ONCALL_AUTHORITY_STORE_PATH"] = str(store_file)
    try:
        is_del_h, st_h, err_h = verify_durable_delivery_authority(
            expected_delivery_id=del_unauth,
            expected_provider_receipt_id=prov_receipt_id,
            expected_request_hash=req_hash,
            expected_release_sha=rel_sha,
            expected_oncall_route=oncall_route,
        )
        assert (
            is_del_h is False
        )  # Fails signature check because store_file record is signed with test key, not pinned production key
        assert st_h == "PENDING_VERIFICATION"
    finally:
        os.environ.pop("ONCALL_AUTHORITY_STORE_PATH", None)


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
    ok_def, err_def = adapter_default.send(
        "nid-app-1", "webhook", "ops-lead", "Test Title", "Test Detail"
    )
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
    ok_req, err_req = adapter_req.send(
        "nid-app-2", "webhook", "ops-lead", "Test Title", "Test Detail"
    )
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


def _mp_store_worker(store_file_path: str, delivery_id: str, authority_public_key_pem: str | None = None) -> tuple[bool, str, str | None]:
    """Top-level worker for multi-process authority store concurrency test."""
    from modules.notifications.domain.authority import FileDeliveryAuthorityStore

    store = FileDeliveryAuthorityStore(store_file_path, authority_public_key_pem=authority_public_key_pem)

    def _validator(record):
        return True, "DELIVERED", None

    return store.atomic_consume_if_valid(delivery_id, _validator)


def test_file_authority_store_two_instances_concurrency(tmp_path):
    """B26 Remediation Test: Two independent FileDeliveryAuthorityStore instances
    consuming one record concurrently return exactly one DELIVERED, deterministic replay
    rejection, valid JSON, no exception, and persistent replay state on reload.
    """


    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = _generate_test_authority_key()
    pub_pem = _get_pub_pem(auth_priv_key)
    delivery_id = "del-test-two-inst-1"
    prov_receipt_id = "prov-rcpt-two-inst-1"
    req_hash = "b" * 64
    rel_sha = "e" * 40
    oncall_route = "ops-lead"
    ts_str = datetime.now(UTC).isoformat()
    issuer_id = CANONICAL_AUTHORITY_ISSUER_IDENTITY

    sig_payload = (
        f"authority_record:{delivery_id}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{ts_str}:{issuer_id}"
    ).encode()
    sig_b64 = base64.b64encode(auth_priv_key.sign(sig_payload)).decode("utf-8")

    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=sig_b64,
    )

    store_file = tmp_path / "authority_two_inst.json"

    # Create two independent store instances pointing to the same file path
    store1 = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    store2 = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    store1._store_authority_record_for_testing(rec)

    def _val(r):
        return True, "DELIVERED", None

    def _consume_1():
        return store1.atomic_consume_if_valid(delivery_id, _val)

    def _consume_2():
        return store2.atomic_consume_if_valid(delivery_id, _val)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_consume_1)
        f2 = executor.submit(_consume_2)
        r1 = f1.result()
        r2 = f2.result()

    results = [r1, r2]
    delivered_count = sum(1 for is_del, st, _ in results if is_del and st == "DELIVERED")
    rejected_count = sum(
        1 for is_del, st, _ in results if not is_del and st == "PENDING_VERIFICATION"
    )
    assert delivered_count == 1
    assert rejected_count == 1

    # Verify JSON valid in store file
    with open(store_file, encoding="utf-8") as f:
        data = json.load(f)
    assert delivery_id in data.get("consumed", [])

    # Persistence verification from fresh 3rd store instance
    store3 = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    is_del, st, err = store3.atomic_consume_if_valid(delivery_id, _val)
    assert is_del is False
    assert st == "PENDING_VERIFICATION"
    assert "already been consumed" in str(err)


def test_file_authority_store_multiprocess_concurrency(tmp_path):
    """B26 Remediation Test: Multi-process race across independent store instances
    guarantees exactly one DELIVERED and zero unhandled exceptions.
    """


    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = _generate_test_authority_key()
    pub_pem = _get_pub_pem(auth_priv_key)
    delivery_id = "del-test-mp-1"
    prov_receipt_id = "prov-rcpt-mp-1"
    req_hash = "c" * 64
    rel_sha = "f" * 40
    oncall_route = "ops-lead"
    ts_str = datetime.now(UTC).isoformat()
    issuer_id = CANONICAL_AUTHORITY_ISSUER_IDENTITY

    sig_payload = (
        f"authority_record:{delivery_id}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{ts_str}:{issuer_id}"
    ).encode()
    sig_b64 = base64.b64encode(auth_priv_key.sign(sig_payload)).decode("utf-8")

    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=sig_b64,
    )

    store_file = tmp_path / "authority_mp.json"
    setup_store = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    setup_store._store_authority_record_for_testing(rec)

    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(_mp_store_worker, str(store_file), delivery_id, pub_pem) for _ in range(4)
        ]
        results = [f.result() for f in futures]

    delivered_count = sum(1 for is_del, st, _ in results if is_del and st == "DELIVERED")
    rejected_count = sum(
        1 for is_del, st, _ in results if not is_del and st == "PENDING_VERIFICATION"
    )
    assert delivered_count == 1
    assert rejected_count == 3


def test_file_authority_store_corrupt_file_handling(tmp_path):
    """B26 Remediation Test: Corrupt or unreadable store file produces explicit
    fail-closed error without silently replacing or overwriting store contents.
    """
    from modules.notifications.domain.authority import FileDeliveryAuthorityStore

    store_file = tmp_path / "authority_corrupt.json"
    store_file.write_text("{this is corrupt json text...", encoding="utf-8")

    store = FileDeliveryAuthorityStore(store_file)

    def _val(r):
        return True, "DELIVERED", None

    is_del, st, err = store.atomic_consume_if_valid("del-corrupt-1", _val)
    assert is_del is False
    assert st == "PENDING_VERIFICATION"
    assert "Authority store read failed" in str(err)
    # Prove corrupt store file was NOT overwritten with an empty store dict
    assert store_file.read_text(encoding="utf-8") == "{this is corrupt json text..."


def test_file_authority_store_b27_schema_validation_mutations(tmp_path):
    """B27 Remediation Test: Valid JSON files with corrupt/malformed store shapes
    or incompatible schemas produce explicit fail-closed results without raising
    unhandled exceptions (such as AttributeError) or self-healing/overwriting.
    """

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = _generate_test_authority_key()
    delivery_id = "del-b27-valid-1"
    prov_receipt_id = "prov-rcpt-b27-1"
    req_hash = "a" * 64
    rel_sha = "d" * 40
    oncall_route = "ops-lead"
    ts_str = datetime.now(UTC).isoformat()
    issuer_id = CANONICAL_AUTHORITY_ISSUER_IDENTITY

    sig_payload = (
        f"authority_record:{delivery_id}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{ts_str}:{issuer_id}"
    ).encode()
    sig_b64 = base64.b64encode(auth_priv_key.sign(sig_payload)).decode("utf-8")

    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=sig_b64,
    )

    def _val(r):
        return True, "DELIVERED", None

    malformed_shapes = [
        # (1) Reviewer's exact reproducer: records is list, consumed is list
        '{"records": [], "consumed": []}',
        # (2) Top-level is non-dict
        "[1, 2, 3]",
        '"just a string"',
        "123.45",
        # (3) Records is string
        '{"records": "not a dict", "consumed": []}',
        # (4) Consumed is string/dict instead of list
        '{"records": {}, "consumed": "not a list"}',
        '{"records": {}, "consumed": {"d1": true}}',
        # (5) Duplicate items in consumed
        '{"records": {}, "consumed": ["del-1", "del-1"]}',
        # (6) Non-string items in consumed
        '{"records": {}, "consumed": [123]}',
        # (7) Unrecognized top-level key
        '{"records": {}, "consumed": [], "unrecognized_key": 99}',
        # (8) Invalid schema version
        '{"version": 99, "records": {}, "consumed": []}',
        # (9) Record object is not a dict
        '{"records": {"del-1": "not a dict"}, "consumed": []}',
        # (10) Record object missing required fields
        '{"records": {"del-1": {"delivery_id": "del-1"}}, "consumed": []}',
        # (11) Record key mismatch
        json.dumps({"records": {"del-mismatch": rec.to_dict()}, "consumed": []}),
    ]

    for idx, shape in enumerate(malformed_shapes):
        file_path = tmp_path / f"corrupt_shape_{idx}.json"
        file_path.write_text(shape, encoding="utf-8")

        store = FileDeliveryAuthorityStore(file_path)

        # 1. get_authority_record does not raise AttributeError or unhandled exception
        rec_out = store.get_authority_record("del-1")
        assert rec_out is None

        # 2. atomic_consume_if_valid returns fail-closed PENDING_VERIFICATION
        is_del, st, err = store.atomic_consume_if_valid("del-1", _val)
        assert is_del is False
        assert st == "PENDING_VERIFICATION"
        assert "Authority store read failed" in str(err)

        # 3. _store_authority_record_for_testing fails closed and does not overwrite
        with pytest.raises(ValueError, match="Authority store data is corrupt or unreadable"):
            store._store_authority_record_for_testing(rec)

        assert file_path.read_text(encoding="utf-8") == shape


def test_file_authority_store_b28_fsync_and_durability_failure_mutations(tmp_path, monkeypatch):
    """B28 Remediation Test: Failures at any point during file write, temp creation,
    file fsync, atomic replace, parent directory open, directory fsync, or close
    must be propagated out of _write_store_data_atomic, must cause atomic_consume_if_valid
    to return PENDING_VERIFICATION (never DELIVERED), and must enforce safe recovery
    on subsequent replay without double delivery.
    """
    import os

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = _generate_test_authority_key()
    pub_pem = _get_pub_pem(auth_priv_key)
    delivery_id = "del-b28-durability-1"
    prov_receipt_id = "prov-rcpt-b28-1"
    req_hash = "b" * 64
    rel_sha = "e" * 40
    oncall_route = "ops-lead"
    ts_str = datetime.now(UTC).isoformat()
    issuer_id = CANONICAL_AUTHORITY_ISSUER_IDENTITY

    sig_payload = (
        f"authority_record:{delivery_id}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{ts_str}:{issuer_id}"
    ).encode()
    sig_b64 = base64.b64encode(auth_priv_key.sign(sig_payload)).decode("utf-8")

    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=sig_b64,
    )

    def _val(r):
        return True, "DELIVERED", None

    # Test 1: Injected failure on parent directory fsync (the exact B28 reproducer)
    store_file = tmp_path / "authority_b28_dir_fsync.json"
    store = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    store._store_authority_record_for_testing(rec)

    real_os_fsync = os.fsync
    fsync_count = 0

    def mock_fsync_fail_on_fourth(fd):
        nonlocal fsync_count
        fsync_count += 1
        if fsync_count == 4:  # 1st: journal file, 2nd: journal dir, 3rd: store file, 4th: store dir
            raise OSError(5, "Injected parent directory fsync EIO error")
        return real_os_fsync(fd)

    monkeypatch.setattr(os, "fsync", mock_fsync_fail_on_fourth)

    is_del, st, err = store.atomic_consume_if_valid(delivery_id, _val)
    assert is_del is False
    assert st == "PENDING_VERIFICATION"
    assert "Durable store write or fsync failed" in str(err)
    assert "Injected parent directory fsync EIO error" in str(err)

    # Recovery check on subsequent call (replay prevention / clean state readback):
    # Since replace succeeded before dir fsync failed, the delivery_id was committed to disk.
    # A retry call must recognize it was already consumed and reject replay safely (no double delivery, no DELIVERED claim).
    monkeypatch.setattr(os, "fsync", real_os_fsync)
    is_del_retry, st_retry, err_retry = store.atomic_consume_if_valid(delivery_id, _val)
    assert is_del_retry is False
    assert st_retry == "PENDING_VERIFICATION"
    assert "already been consumed" in str(err_retry)

    # Test 2: Injected failure on file fsync (1st fsync call)
    store_file2 = tmp_path / "authority_b28_file_fsync.json"
    store2 = FileDeliveryAuthorityStore(store_file2, authority_public_key_pem=pub_pem)
    store2._store_authority_record_for_testing(rec)

    fsync_count2 = 0

    def mock_fsync_fail_on_first(fd):
        nonlocal fsync_count2
        fsync_count2 += 1
        if fsync_count2 == 1:
            raise OSError(5, "Injected file fsync EIO error")
        return real_os_fsync(fd)

    monkeypatch.setattr(os, "fsync", mock_fsync_fail_on_first)

    is_del2, st2, err2 = store2.atomic_consume_if_valid(delivery_id, _val)
    assert is_del2 is False
    assert st2 == "PENDING_VERIFICATION"
    assert "Injected file fsync EIO error" in str(err2)

    # Test 3: Injected failure on os.replace
    monkeypatch.setattr(os, "fsync", real_os_fsync)
    store_file3 = tmp_path / "authority_b28_replace.json"
    store3 = FileDeliveryAuthorityStore(store_file3, authority_public_key_pem=pub_pem)
    store3._store_authority_record_for_testing(rec)

    def mock_replace_fail(src, dst):
        raise OSError(16, "Injected os.replace EBUSY error")

    monkeypatch.setattr(os, "replace", mock_replace_fail)

    is_del3, st3, err3 = store3.atomic_consume_if_valid(delivery_id, _val)
    assert is_del3 is False
    assert st3 == "PENDING_VERIFICATION"
    assert "Injected os.replace EBUSY error" in str(err3)


def test_file_authority_store_b29_strict_schema_and_canonical_id_mutations(tmp_path):
    """B29 Remediation Test: Proves strict schema enforcement rejects missing/boolean/float versions,
    unknown record fields, non-canonical whitespace/unstripped IDs, and whitespace consumed queries.
    """

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = _generate_test_authority_key()
    delivery_id = "del-b29-1"
    prov_receipt_id = "prov-rcpt-b29-1"
    req_hash = "a" * 64
    rel_sha = "d" * 40
    oncall_route = "ops-lead"
    ts_str = datetime.now(UTC).isoformat()
    issuer_id = CANONICAL_AUTHORITY_ISSUER_IDENTITY

    sig_payload = (
        f"authority_record:{delivery_id}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{ts_str}:{issuer_id}"
    ).encode()
    sig_b64 = base64.b64encode(auth_priv_key.sign(sig_payload)).decode("utf-8")

    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=sig_b64,
    )

    def _val(r):
        return True, "DELIVERED", None

    # 1. Missing version in store json
    f1 = tmp_path / "missing_version.json"
    f1.write_text(json.dumps({"records": {}, "consumed": []}), encoding="utf-8")
    s1 = FileDeliveryAuthorityStore(f1)
    is_del, st, err = s1.atomic_consume_if_valid("d", _val)
    assert is_del is False and st == "PENDING_VERIFICATION" and "Missing required top-level key 'version'" in str(err)

    # 2. Boolean version True in store json (isinstance(True, int) is True, but type(True) is bool)
    f2 = tmp_path / "bool_version.json"
    f2.write_text(json.dumps({"version": True, "records": {}, "consumed": []}), encoding="utf-8")
    s2 = FileDeliveryAuthorityStore(f2)
    is_del, st, err = s2.atomic_consume_if_valid("d", _val)
    assert is_del is False and st == "PENDING_VERIFICATION" and "Unsupported or invalid store schema version" in str(err)

    # 3. Float version 1.0 in store json
    f3 = tmp_path / "float_version.json"
    f3.write_text(json.dumps({"version": 1.0, "records": {}, "consumed": []}), encoding="utf-8")
    s3 = FileDeliveryAuthorityStore(f3)
    is_del, st, err = s3.atomic_consume_if_valid("d", _val)
    assert is_del is False and st == "PENDING_VERIFICATION" and "Unsupported or invalid store schema version" in str(err)

    # 4. Unknown field in record object
    raw_rec_extra = rec.to_dict()
    raw_rec_extra["extra_unrecognized_field"] = "bad"
    f4 = tmp_path / "extra_field_rec.json"
    f4.write_text(json.dumps({"version": 1, "records": {delivery_id: raw_rec_extra}, "consumed": []}), encoding="utf-8")
    s4 = FileDeliveryAuthorityStore(f4)
    is_del, st, err = s4.atomic_consume_if_valid(delivery_id, _val)
    assert is_del is False and st == "PENDING_VERIFICATION" and "unrecognized field(s)" in str(err)

    # 5. Non-canonical whitespace delivery ID key in records
    f5 = tmp_path / "whitespace_rec_key.json"
    f5.write_text(json.dumps({"version": 1, "records": {f" {delivery_id} ": rec.to_dict()}, "consumed": []}), encoding="utf-8")
    s5 = FileDeliveryAuthorityStore(f5)
    is_del, st, err = s5.atomic_consume_if_valid(delivery_id, _val)
    assert is_del is False and st == "PENDING_VERIFICATION" and "Invalid non-canonical delivery_id key" in str(err)

    # 6. Non-canonical whitespace entry in consumed list
    f6 = tmp_path / "whitespace_consumed_item.json"
    f6.write_text(json.dumps({"version": 1, "records": {}, "consumed": [f" {delivery_id} "]}), encoding="utf-8")
    s6 = FileDeliveryAuthorityStore(f6)
    is_del, st, err = s6.atomic_consume_if_valid(delivery_id, _val)
    assert is_del is False and st == "PENDING_VERIFICATION" and "Invalid non-canonical entry in consumed list" in str(err)

    # 7. Non-canonical whitespace query delivery ID
    f7 = tmp_path / "whitespace_query.json"
    s7 = FileDeliveryAuthorityStore(f7)
    s7._store_authority_record_for_testing(rec)
    is_del, st, err = s7.atomic_consume_if_valid(f" {delivery_id} ", _val)
    assert is_del is False and st == "PENDING_VERIFICATION" and "Non-canonical delivery ID" in str(err)


def test_file_authority_store_b30_crash_outcome_rollback_and_intent_journal_recovery_mutations(tmp_path, monkeypatch):
    """B30 Remediation Test: Proves that when post-replace directory fsync fails and disk rollback occurs
    (store file reverts to pre-transition bytes), opening a fresh store process reconciles the intent journal
    and rejects replay instead of issuing a second DELIVERED result.
    """
    import os

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = _generate_test_authority_key()
    pub_pem = _get_pub_pem(auth_priv_key)
    delivery_id = "del-b30-crash-recovery-1"
    prov_receipt_id = "prov-rcpt-b30-1"
    req_hash = "c" * 64
    rel_sha = "f" * 40
    oncall_route = "ops-lead"
    ts_str = datetime.now(UTC).isoformat()
    issuer_id = CANONICAL_AUTHORITY_ISSUER_IDENTITY

    sig_payload = (
        f"authority_record:{delivery_id}:{prov_receipt_id}:{req_hash}:{rel_sha}:{oncall_route}:{ts_str}:{issuer_id}"
    ).encode()
    sig_b64 = base64.b64encode(auth_priv_key.sign(sig_payload)).decode("utf-8")

    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt_id,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=oncall_route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=sig_b64,
    )

    def _val(r):
        return True, "DELIVERED", None

    store_file = tmp_path / "authority_b30_crash.json"
    store = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    store._store_authority_record_for_testing(rec)

    pre_transition_bytes = store_file.read_bytes()

    real_os_fsync = os.fsync
    fsync_call_count = 0

    def mock_fsync_fail_on_store_dir(fd):
        nonlocal fsync_call_count
        fsync_call_count += 1
        # 1st fsync: journal intent file, 2nd fsync: journal dir, 3rd fsync: store file, 4th fsync: store dir
        if fsync_call_count == 4:
            raise OSError(5, "Injected store directory fsync failure after replace")
        return real_os_fsync(fd)

    monkeypatch.setattr(os, "fsync", mock_fsync_fail_on_store_dir)

    is_del, st, err = store.atomic_consume_if_valid(delivery_id, _val)
    assert is_del is False
    assert st == "PENDING_VERIFICATION"
    assert "Durable store write or fsync failed" in str(err)
    assert "store directory fsync failure" in str(err)

    # Now model the crash outcome: restore pre-transition bytes to store_file (representing store directory entry rollback)
    monkeypatch.setattr(os, "fsync", real_os_fsync)
    store_file.write_bytes(pre_transition_bytes)

    # Open a fresh store process / object on the rolled-back store file
    fresh_store = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)

    # Query atomic_consume_if_valid on the fresh store instance
    is_del_fresh, st_fresh, err_fresh = fresh_store.atomic_consume_if_valid(delivery_id, _val)

    # CRITICAL B30 ASSERTION: Fresh store MUST reconcile the intent journal, recognize delivery_id was consumed,
    # and REJECT replay. It MUST NOT return DELIVERED!
    assert is_del_fresh is False
    assert st_fresh == "PENDING_VERIFICATION"
    assert "already been consumed" in str(err_fresh)


def test_file_authority_store_b31_path_traversal_and_safe_filename_mutations(tmp_path):
    """B31 Remediation Test: Proves raw delivery_id cannot become filesystem path or escape journal_dir.
    Tests traversal, separator, dot segment, absolute path, Unicode-equivalent, and overlong ID mutations.
    """

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = _generate_test_authority_key()
    pub_pem = _get_pub_pem(auth_priv_key)

    def _make_rec(del_id: str) -> DeliveryAuthorityRecord:
        prov_rcpt = "prov-b31"
        req_hash = "a" * 64
        rel_sha = "b" * 40
        route = "ops-lead"
        ts = datetime.now(UTC).isoformat()
        issuer = CANONICAL_AUTHORITY_ISSUER_IDENTITY
        payload = f"authority_record:{del_id}:{prov_rcpt}:{req_hash}:{rel_sha}:{route}:{ts}:{issuer}".encode()
        sig = base64.b64encode(auth_priv_key.sign(payload)).decode("utf-8")
        return DeliveryAuthorityRecord(
            delivery_id=del_id,
            provider_receipt_id=prov_rcpt,
            request_hash=req_hash,
            release_sha=rel_sha,
            oncall_route=route,
            timestamp=ts,
            issuer_identity=issuer,
            issuer_signature=sig,
        )

    def _val(r):
        return True, "DELIVERED", None

    store_file = tmp_path / "authority_b31.json"
    store = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)

    unsafe_ids = [
        "../escaped",
        "../../foo",
        "foo/bar",
        "foo\\bar",
        ".",
        "..",
        "./foo",
        "../foo",
        "/etc/passwd",
        "C:\\Windows",
        "del\x00null",
        "del\nnewline",
        "\u002e\u002e",
        "del\uff0ftraversal",
        "a" * 2000,
    ]

    for bad_id in unsafe_ids:
        try:
            _make_rec(bad_id)
            is_del, st, err = store.atomic_consume_if_valid(bad_id, _val)
            assert is_del is False
            assert st == "PENDING_VERIFICATION"
            assert "rejected" in str(err) or "Invalid" in str(err) or "unsafe" in str(err)
        except ValueError as val_err:
            assert "Invalid" in str(val_err) or "unsafe" in str(val_err) or "rejected" in str(val_err)

    # Verify no files were created outside the journal directory
    outside_files = [f for f in tmp_path.glob("*") if f != store_file and f != store.lock_path and f != store.journal_dir]
    assert len(outside_files) == 0

    # Test valid ID uses safe SHA-256 digest filename stem
    valid_id = "del-b31-valid-1"
    valid_rec = _make_rec(valid_id)
    store._store_authority_record_for_testing(valid_rec)
    is_del_ok, st_ok, err_ok = store.atomic_consume_if_valid(valid_id, _val)
    assert is_del_ok is True
    assert st_ok == "DELIVERED"
    assert err_ok is None

    # Check journal directory contains sha256 stem .intent file
    expected_stem = hashlib.sha256(valid_id.encode("utf-8")).hexdigest()
    intent_file = store.journal_dir / f"{expected_stem}.intent"
    assert intent_file.exists()
    assert intent_file.parent.resolve() == store.journal_dir.resolve()


def test_file_authority_store_b32_store_tenant_isolation_and_namespace_collision_mutations(tmp_path):
    """B32 Remediation Test: Proves separate authority stores isolate lock and journal namespaces
    and bind journal records to exact store identity. Tests same-stem json/yaml, cross-directory, and cross-tenant collisions.
    """

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = _generate_test_authority_key()
    pub_pem = _get_pub_pem(auth_priv_key)
    delivery_id = "shared-id-b32"
    prov_rcpt = "prov-b32"
    req_hash = "b" * 64
    rel_sha = "c" * 40
    route = "ops-lead"
    ts = datetime.now(UTC).isoformat()
    issuer = CANONICAL_AUTHORITY_ISSUER_IDENTITY
    payload = f"authority_record:{delivery_id}:{prov_rcpt}:{req_hash}:{rel_sha}:{route}:{ts}:{issuer}".encode()
    sig = base64.b64encode(auth_priv_key.sign(payload)).decode("utf-8")
    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_rcpt,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=route,
        timestamp=ts,
        issuer_identity=issuer,
        issuer_signature=sig,
    )

    def _val(r):
        return True, "DELIVERED", None

    # 1. Same stem json vs yaml in same directory
    store_json = tmp_path / "authority.json"
    store_yaml = tmp_path / "authority.yaml"
    s_json = FileDeliveryAuthorityStore(store_json, authority_public_key_pem=pub_pem)
    s_yaml = FileDeliveryAuthorityStore(store_yaml, authority_public_key_pem=pub_pem)

    assert s_json.journal_dir != s_yaml.journal_dir
    assert s_json.lock_path != s_yaml.lock_path

    s_json._store_authority_record_for_testing(rec)
    s_yaml._store_authority_record_for_testing(rec)

    is_del1, st1, err1 = s_json.atomic_consume_if_valid(delivery_id, _val)
    assert is_del1 is True and st1 == "DELIVERED"

    # Consuming shared-id in s_json MUST NOT cause s_yaml to reject its record or collide namespaces
    is_del2, st2, err2 = s_yaml.atomic_consume_if_valid(delivery_id, _val)
    assert is_del2 is True and st2 == "DELIVERED"

    # 2. Cross-directory collision test
    dirA = tmp_path / "dirA"
    dirB = tmp_path / "dirB"
    s_dirA = FileDeliveryAuthorityStore(dirA / "authority.json", authority_public_key_pem=pub_pem)
    s_dirB = FileDeliveryAuthorityStore(dirB / "authority.json", authority_public_key_pem=pub_pem)

    assert s_dirA.store_identity != s_dirB.store_identity
    s_dirA._store_authority_record_for_testing(rec)
    s_dirB._store_authority_record_for_testing(rec)

    is_delA, stA, _ = s_dirA.atomic_consume_if_valid(delivery_id, _val)
    is_delB, stB, _ = s_dirB.atomic_consume_if_valid(delivery_id, _val)
    assert is_delA is True and stA == "DELIVERED"
    assert is_delB is True and stB == "DELIVERED"


def test_file_authority_store_b33_corrupt_journal_intent_fail_closed(tmp_path):
    """B33 Remediation Test: Proves corrupt, malformed, invalid schema, or conflicting intent files fail closed
    and surface explicit indeterminate state rather than being silently skipped or overwritten.
    """

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = _generate_test_authority_key()
    pub_pem = _get_pub_pem(auth_priv_key)
    delivery_id = "del-b33-corrupt"
    prov_rcpt = "prov-b33"
    req_hash = "c" * 64
    rel_sha = "d" * 40
    route = "ops-lead"
    ts = datetime.now(UTC).isoformat()
    issuer = CANONICAL_AUTHORITY_ISSUER_IDENTITY
    payload = f"authority_record:{delivery_id}:{prov_rcpt}:{req_hash}:{rel_sha}:{route}:{ts}:{issuer}".encode()
    sig = base64.b64encode(auth_priv_key.sign(payload)).decode("utf-8")
    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_rcpt,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=route,
        timestamp=ts,
        issuer_identity=issuer,
        issuer_signature=sig,
    )

    def _val(r):
        return True, "DELIVERED", None

    store_file = tmp_path / "authority_b33.json"
    store = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    store._store_authority_record_for_testing(rec)

    # Inject corrupt non-JSON intent file into journal dir
    store.journal_dir.mkdir(parents=True, exist_ok=True)
    corrupt_file = store.journal_dir / "corrupt.intent"
    corrupt_file.write_text("{invalid json content ...", encoding="utf-8")

    is_del, st, err = store.atomic_consume_if_valid(delivery_id, _val)
    assert is_del is False
    assert st == "PENDING_VERIFICATION"
    assert "corrupt" in str(err) or "Unreadable" in str(err) or "Authority store read failed" in str(err)


def test_file_authority_store_b34_forged_local_intent_rejection(tmp_path):
    """B34 Remediation Test: Proves caller-writable unauthenticated local JSON files cannot fabricate authority
    or alter state; signature and store identity verification fail closed.
    """
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = ed25519.Ed25519PrivateKey.generate()
    pub_pem = _get_pub_pem(auth_priv_key)
    delivery_id = "del-b34-forged"
    prov_rcpt = "prov-b34"
    req_hash = "e" * 64
    rel_sha = "f" * 40
    route = "ops-lead"
    ts = datetime.now(UTC).isoformat()
    issuer = CANONICAL_AUTHORITY_ISSUER_IDENTITY
    payload = f"authority_record:{delivery_id}:{prov_rcpt}:{req_hash}:{rel_sha}:{route}:{ts}:{issuer}".encode()
    sig = base64.b64encode(auth_priv_key.sign(payload)).decode("utf-8")
    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_rcpt,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=route,
        timestamp=ts,
        issuer_identity=issuer,
        issuer_signature=sig,
    )

    def _val(r):
        return True, "DELIVERED", None

    store_file = tmp_path / "authority_b34.json"
    store = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    store._store_authority_record_for_testing(rec)

    # Drop forged local JSON intent file with invalid signature
    store.journal_dir.mkdir(parents=True, exist_ok=True)
    forged_file = store.journal_dir / "forged.intent"
    forged_data = {
        "version": 1,
        "delivery_id": delivery_id,
        "provider_receipt_id": prov_rcpt,
        "request_hash": req_hash,
        "release_sha": rel_sha,
        "oncall_route": route,
        "issuer_identity": issuer,
        "issuer_signature": "aW52YWxpZCBzaWduYXR1cmUgYnl0ZXM=",  # base64 "invalid signature bytes"
        "store_identity": store.store_identity,
        "timestamp": ts,
        "transition": "CONSUMED",
    }
    forged_file.write_text(json.dumps(forged_data), encoding="utf-8")

    is_del, st, err = store.atomic_consume_if_valid(delivery_id, _val)
    assert is_del is False
    assert st == "PENDING_VERIFICATION"
    assert "signature verification failed" in str(err) or "Authority store read failed" in str(err)


def test_file_authority_store_b35_reconciliation_persistence_failure_propagation(tmp_path, monkeypatch):
    """B35 Remediation Test: Proves journal and primary-store write/fsync failures propagate upward,
    leaving the authority store fail-closed without claiming unproven transitions.
    """

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    auth_priv_key = _generate_test_authority_key()
    pub_pem = _get_pub_pem(auth_priv_key)
    delivery_id = "del-b35-write-fail"
    prov_rcpt = "prov-b35"
    req_hash = "f" * 64
    rel_sha = "a" * 40
    route = "ops-lead"
    ts = datetime.now(UTC).isoformat()
    issuer = CANONICAL_AUTHORITY_ISSUER_IDENTITY
    payload = f"authority_record:{delivery_id}:{prov_rcpt}:{req_hash}:{rel_sha}:{route}:{ts}:{issuer}".encode()
    sig = base64.b64encode(auth_priv_key.sign(payload)).decode("utf-8")
    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_rcpt,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=route,
        timestamp=ts,
        issuer_identity=issuer,
        issuer_signature=sig,
    )

    def _val(r):
        return True, "DELIVERED", None

    store_file = tmp_path / "authority_b35.json"
    store = FileDeliveryAuthorityStore(store_file, authority_public_key_pem=pub_pem)
    store._store_authority_record_for_testing(rec)

    # Write a valid intent file into journal dir
    store._write_journal_intent(rec)

    # Monkeypatch _write_store_data_atomic to fail during reconciliation in _read_store_data
    def mock_write_fail(data):
        raise OSError(5, "Injected store write EIO during reconciliation")

    monkeypatch.setattr(store, "_write_store_data_atomic", mock_write_fail)

    is_del, st, err = store.atomic_consume_if_valid(delivery_id, _val)
    assert is_del is False
    assert st == "PENDING_VERIFICATION"
    assert "Injected store write EIO" in str(err)


def test_b36_copied_local_record_rejection_and_canonical_intents(tmp_path):
    """B36 Remediation Test: Proves that copying an unauthentic stored authority record (signed with a fake/non-pinned key)
    into a canonical or noncanonical journal intent file cannot alter durable consumed state or persist consumed.
    Also proves non-canonical intent filenames and duplicate intents are strictly rejected.
    """
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityReadback,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    # 1. Generate an unauthentic record signed with a fresh non-pinned key
    fake_key = ed25519.Ed25519PrivateKey.generate()
    delivery_id = "copied-local-intent"
    prov_rcpt = "prov-b36-fake"
    req_hash = "d" * 64
    rel_sha = "e" * 40
    route = "ops-lead"
    ts = datetime.now(UTC).isoformat()
    issuer = CANONICAL_AUTHORITY_ISSUER_IDENTITY

    payload = f"authority_record:{delivery_id}:{prov_rcpt}:{req_hash}:{rel_sha}:{route}:{ts}:{issuer}".encode()
    sig_b64 = base64.b64encode(fake_key.sign(payload)).decode("utf-8")

    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_rcpt,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=route,
        timestamp=ts,
        issuer_identity=issuer,
        issuer_signature=sig_b64,
    )

    store_file = tmp_path / "authority_b36.json"
    store = FileDeliveryAuthorityStore(store_file)
    store._store_authority_record_for_testing(rec)

    # Verify normal readback correctly rejects unauthentic record
    readback = DeliveryAuthorityReadback(authority_store=store)
    is_del, st, err = readback.read_by_delivery_id(
        expected_delivery_id=delivery_id,
        expected_provider_receipt_id=prov_rcpt,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=route,
    )
    assert is_del is False
    assert st == "PENDING_VERIFICATION"
    assert "Cryptographic signature verification failed" in str(err)

    # 2. Local caller manufactures journal intent file copying record fields
    expected_stem = hashlib.sha256(delivery_id.encode("utf-8")).hexdigest()
    intent_file = store.journal_dir / f"{expected_stem}.intent"
    intent_data = {
        "version": 1,
        "delivery_id": rec.delivery_id,
        "provider_receipt_id": rec.provider_receipt_id,
        "request_hash": rec.request_hash,
        "release_sha": rec.release_sha,
        "oncall_route": rec.oncall_route,
        "issuer_identity": rec.issuer_identity,
        "issuer_signature": rec.issuer_signature,
        "store_identity": store.store_identity,
        "timestamp": rec.timestamp,
        "transition": "CONSUMED",
    }
    intent_file.write_text(json.dumps(intent_data, indent=2), encoding="utf-8")

    # 3. Open a fresh store instance on the same file path
    fresh_store = FileDeliveryAuthorityStore(store_file)
    fresh_readback = DeliveryAuthorityReadback(authority_store=fresh_store)

    # Fresh read MUST fail closed and MUST NOT return "already been consumed" or persist consumed
    is_del_fresh, st_fresh, err_fresh = fresh_readback.read_by_delivery_id(
        expected_delivery_id=delivery_id,
        expected_provider_receipt_id=prov_rcpt,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=route,
    )
    assert is_del_fresh is False
    assert st_fresh == "PENDING_VERIFICATION"
    assert "already been consumed" not in str(err_fresh)
    assert "Authority store read failed" in str(err_fresh) or "signature verification failed" in str(err_fresh)

    # Confirm consumed array in store file on disk is still empty
    with open(store_file, encoding="utf-8") as f:
        store_disk_data = json.load(f)
    assert delivery_id not in store_disk_data.get("consumed", [])

    # 4. Test non-canonical intent filename rejection
    intent_file.unlink()
    noncanonical_file = store.journal_dir / "arbitrary_noncanonical_name.intent"
    noncanonical_file.write_text(json.dumps(intent_data, indent=2), encoding="utf-8")

    store_nc = FileDeliveryAuthorityStore(store_file)
    is_del_nc, st_nc, err_nc = store_nc.atomic_consume_if_valid(delivery_id, lambda r: (True, "DELIVERED", None))
    assert is_del_nc is False
    assert st_nc == "PENDING_VERIFICATION"
    assert "Non-canonical journal intent filename" in str(err_nc) or "Authority store read failed" in str(err_nc)

    # 5. Test duplicate intent rejection
    noncanonical_file.unlink()
    intent_file.write_text(json.dumps(intent_data, indent=2), encoding="utf-8")
    dup_file = store.journal_dir / "duplicate_intent.intent"
    dup_file.write_text(json.dumps(intent_data, indent=2), encoding="utf-8")

    store_dup = FileDeliveryAuthorityStore(store_file)
    is_del_dup, st_dup, err_dup = store_dup.atomic_consume_if_valid(delivery_id, lambda r: (True, "DELIVERED", None))
    assert is_del_dup is False
    assert st_dup == "PENDING_VERIFICATION"
    assert "Duplicate journal intent" in str(err_dup) or "Non-canonical" in str(err_dup) or "Authority store read failed" in str(err_dup)


def test_b37_test_secret_cannot_mint_live_provider_readback():
    """B37 Remediation Test: Proves repository-visible test secrets cannot be used to record
    or verify live WATCH_PASSED watch-window claims.
    """
    from datetime import UTC, datetime, timedelta

    from shared.observability.watch_window import (
        record_deployment_watch_window_status,
        verify_watch_window_receipt,
    )

    test_sha = "7e23469e77411a6c4d139beb210d5eee1d02c809"
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    # 1. Attempting to record WATCH_PASSED with test-provider-secret-key MUST be rejected
    with pytest.raises(ValueError, match="repository-visible test trust root|authentic external provider trust root|Fail-closed gate enforced"):
        os.environ["MONITORING_PROVIDER_SECRET"] = "test-provider-secret-key"
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
        )

    # 2. Attempting to verify receipt with evidence-provider-trust-root-secret MUST be rejected
    with pytest.raises(ValueError, match="Invalid watch-window status|repository-visible test trust root|authentic external provider trust root|Fail-closed gate enforced"):
        os.environ["MONITORING_PROVIDER_SECRET"] = "evidence-provider-trust-root-secret"
        verify_watch_window_receipt(expected_release_sha=test_sha)


def test_b38_caller_minted_records_cannot_become_delivered(tmp_path):
    """B38 Remediation Test: Proves no caller-selected key seed, derived test helper,
    or local intent write can make production DeliveryAuthorityReadback return DELIVERED.
    """
    import base64
    from datetime import UTC, datetime

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from modules.notifications.domain.authority import (
        CANONICAL_AUTHORITY_ISSUER_IDENTITY,
        DeliveryAuthorityReadback,
        DeliveryAuthorityRecord,
        FileDeliveryAuthorityStore,
    )

    priv_key = Ed25519PrivateKey.generate()
    delivery_id = "del-b38-caller-mint-1"
    prov_receipt = "prov-rcpt-b38-1"
    req_hash = "1" * 64
    rel_sha = "a" * 40
    route = "ops-lead"
    ts_str = datetime.now(UTC).isoformat()
    issuer_id = CANONICAL_AUTHORITY_ISSUER_IDENTITY

    sig_payload = f"authority_record:{delivery_id}:{prov_receipt}:{req_hash}:{rel_sha}:{route}:{ts_str}:{issuer_id}".encode()
    sig_b64 = base64.b64encode(priv_key.sign(sig_payload)).decode("utf-8")

    rec = DeliveryAuthorityRecord(
        delivery_id=delivery_id,
        provider_receipt_id=prov_receipt,
        request_hash=req_hash,
        release_sha=rel_sha,
        oncall_route=route,
        timestamp=ts_str,
        issuer_identity=issuer_id,
        issuer_signature=sig_b64,
    )

    store_file = tmp_path / "authority_b38.json"
    store = FileDeliveryAuthorityStore(store_file)
    store._store_authority_record_for_testing(rec)

    readback = DeliveryAuthorityReadback(authority_store=store)
    is_del, status, err = readback.read_by_delivery_id(
        expected_delivery_id=delivery_id,
        expected_provider_receipt_id=prov_receipt,
        expected_request_hash=req_hash,
        expected_release_sha=rel_sha,
        expected_oncall_route=route,
    )

    assert is_del is False
    assert status == "PENDING_VERIFICATION"
    assert "signature verification failed" in str(err).lower()


def test_b39_arbitrary_secret_and_mock_transport_cannot_mint_watch_passed(tmp_path):
    """B39 Remediation Test: Proves arbitrary secrets, caller-owned mock transports,
    and renamed secrets cannot mint WATCH_PASSED or overwrite committed evidence.
    """
    from datetime import UTC, datetime, timedelta

    from shared.observability.watch_window import (
        record_deployment_watch_window_status,
    )

    test_sha = "7e23469e77411a6c4d139beb210d5eee1d02c809"
    start_dt = datetime.now(UTC) - timedelta(minutes=20)
    end_dt = datetime.now(UTC)

    # 1. Arbitrary caller-supplied secret MUST be rejected for WATCH_PASSED
    os.environ["MONITORING_PROVIDER_SECRET"] = "arbitrary-caller-controlled-secret-not-blocklisted"
    with pytest.raises(ValueError, match="test/mock trust root|authentic external provider trust root|Fail-closed gate enforced"):
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
        )

    # 2. Caller-supplied mock query_transport MUST be rejected for WATCH_PASSED
    os.environ["MONITORING_PROVIDER_SECRET"] = "authentic-prod-secret"
    with pytest.raises(ValueError, match="Caller-supplied mock query_transport cannot mint authentic live WATCH_PASSED status|Fail-closed gate enforced"):
        record_deployment_watch_window_status(
            release_sha=test_sha,
            status=1,
            start_time=start_dt,
            end_time=end_dt,
            gcp_project="alfaloop-data-project",
            provider_route="https://monitoring.googleapis.com/v3",
            query_transport=lambda m, u, p=None, pay=None: (200, {"timeSeries": []}),
        )

    # Clean environment
    os.environ.pop("MONITORING_PROVIDER_SECRET", None)


def test_b40_metrics_registry_enforces_undeclared_labels_and_cardinality_bounds() -> None:
    """B40 Test: Verify fail-closed enforcement of declared label names and max series cardinality."""
    from shared.observability.metrics import CardinalityPolicy

    reg = MetricsRegistry(max_series_per_metric=5, cardinality_policy=CardinalityPolicy.REJECT)
    for m in PLATFORM_METRICS:
        reg.register(m)

    # 1. Undeclared label key must raise ValueError. This stays fail-closed under
    #    every policy: an undeclared key is a coding defect, not a data value.
    with pytest.raises(ValueError, match="undeclared label key"):
        reg.increment(
            "api_request_count",
            labels={"service": "api", "route": "/jobs", "status": "200", "unbounded_user_id": "user-123"},
        )

    # 2. Under REJECT, exceeding max series per metric must raise ValueError.
    #    The production default is SHED (see test_c1_registry_sheds_overflow_*),
    #    which bounds cardinality identically but degrades the metric instead of
    #    the live request that emitted it.
    for i in range(5):
        reg.set("dlq_message_count", float(i), labels={"topic": f"topic-{i}"})

    with pytest.raises(ValueError, match="exceeded maximum allowed series cardinality threshold"):
        reg.set("dlq_message_count", 99.0, labels={"topic": "topic-overflow"})


def test_b41_metric_definitions_require_auditable_ownership() -> None:
    """B41 Test: Verify all platform metrics have valid non-empty owner fields."""
    for m in PLATFORM_METRICS:
        assert m.owner and isinstance(m.owner, str) and len(m.owner.strip()) > 0, f"Metric {m.name} missing owner"

    # Attempting to register a metric with no owner must raise ValueError
    from shared.observability.metrics import MetricCategory, MetricDefinition, MetricType
    invalid_metric = MetricDefinition(
        "unowned_metric", MetricType.COUNTER, MetricCategory.TRAFFIC, "Unowned test metric", owner=""
    )
    reg = MetricsRegistry()
    with pytest.raises(ValueError, match="must have a valid non-empty owner"):
        reg.register(invalid_metric)


def test_b42_alerts_runbook_anchors_and_headings_are_valid() -> None:
    """B42 Test: Verify all alerts in alerts.json point to valid runbook files and valid markdown section anchors."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    alerts_path = root / "infra" / "monitoring" / "alerts.json"
    alerts_data = json.loads(alerts_path.read_text(encoding="utf-8"))

    def header_to_slug(header_text: str) -> str:
        clean = re.sub(r"[^\w\s-]", "", header_text.lower().strip())
        return re.sub(r"[\s_]+", "-", clean)

    for alert in alerts_data.get("alerts", []):
        runbook = alert.get("runbook")
        assert runbook and isinstance(runbook, str), f"Alert {alert['id']} missing runbook"
        file_part, anchor = runbook.split("#") if "#" in runbook else (runbook, None)
        runbook_file = root / file_part
        assert runbook_file.exists(), f"Alert {alert['id']} runbook file '{file_part}' does not exist"

        if anchor:
            content = runbook_file.read_text(encoding="utf-8")
            header_lines = [line.lstrip("#").strip() for line in content.splitlines() if line.startswith("#")]
            slugs = {header_to_slug(h) for h in header_lines if h}
            assert anchor in slugs, f"Alert {alert['id']} anchor #{anchor} not found in {file_part}. Found anchors: {sorted(slugs)}"


# --- C1: bounded route cardinality must not break instrumented callers -------
#
# Regression cover for the reopen at 54b749e0: the API middleware labelled
# api_request_count with the raw request.url.path, so 150 concurrent /jobs/{id}
# reads minted 150 series, tripped the registry cardinality guard, and the
# resulting ValueError escaped into the request path (52/150 request failures in
# tests/performance/test_load_and_soak.py). The fix has two independent layers,
# and each is pinned below: normalize the label at the source, and make overflow
# shed instead of raising at the instrumented caller.


def test_c1_route_label_is_normalized_to_bounded_templates() -> None:
    """Layer 1: dynamic path segments collapse to their registered template."""
    from shared.observability.routes import (
        UNMATCHED_ROUTE_TEMPLATE,
        RouteTemplateResolver,
        compile_route_template,
    )

    resolver = RouteTemplateResolver(
        [
            _FakeRoute("/health"),
            _FakeRoute("/jobs"),
            _FakeRoute("/jobs/{job_id}"),
            _FakeRoute("/files/{rest:path}"),
        ]
    )

    # Distinct ids must not produce distinct label values.
    labels = {resolver.resolve(f"/jobs/{i:08d}-uuid-{i}") for i in range(500)}
    assert labels == {"/jobs/{job_id}"}

    assert resolver.resolve("/jobs") == "/jobs"
    assert resolver.resolve("/health") == "/health"
    assert resolver.resolve("/files/a/b/c.txt") == "/files/{rest:path}"
    # Unrouted paths (scanners, 404 probes) share one reserved bucket.
    assert resolver.resolve("/definitely/not/a/route") == UNMATCHED_ROUTE_TEMPLATE
    assert resolver.resolve("") == UNMATCHED_ROUTE_TEMPLATE

    # The compiled template must not match a longer path by prefix.
    pattern = compile_route_template("/jobs/{job_id}")
    assert pattern.match("/jobs/abc")
    assert not pattern.match("/jobs/abc/extra")


class _FakeRoute:
    """Minimal duck-type of a router route for resolver unit tests."""

    def __init__(self, path: str) -> None:
        self.path_format = path


def test_c1_registry_sheds_overflow_without_failing_the_caller() -> None:
    """Layer 2: past the budget, emission is shed and counted, never raised."""
    from shared.observability.metrics import (
        OVERFLOW_LABEL_VALUE,
        CardinalityPolicy,
        MetricsRegistry,
    )

    reg = MetricsRegistry(max_series_per_metric=5)
    assert reg.cardinality_policy is CardinalityPolicy.SHED
    for m in PLATFORM_METRICS:
        reg.register(m)

    for i in range(5):
        reg.set("dlq_message_count", float(i), labels={"topic": f"topic-{i}"})

    # 200 further distinct label values: none may raise.
    for i in range(200):
        reg.set("dlq_message_count", float(i), labels={"topic": f"overflow-{i}"})

    # Cardinality stays bounded at budget + the single reserved overflow series.
    assert reg.series_count("dlq_message_count") == 6

    report = reg.overflow_report()
    assert report["policy"] == "shed"
    assert report["shed_emissions"]["dlq_message_count"] == 200
    assert report["metrics_with_shedding"] == ["dlq_message_count"]

    overflow_entries = [
        entry
        for entry in reg.snapshot()["dlq_message_count"]
        if entry.get("cardinality_overflow")
    ]
    assert len(overflow_entries) == 1
    assert overflow_entries[0]["labels"] == {"topic": OVERFLOW_LABEL_VALUE}
    assert overflow_entries[0]["shed_emissions"] == 200


def test_c1_reject_policy_still_fails_closed_for_config_validation() -> None:
    """Config/evidence contexts keep the fail-closed behaviour."""
    from shared.observability.metrics import CardinalityPolicy, MetricsRegistry

    reg = MetricsRegistry(max_series_per_metric=5, cardinality_policy=CardinalityPolicy.REJECT)
    for m in PLATFORM_METRICS:
        reg.register(m)
    for i in range(5):
        reg.set("dlq_message_count", float(i), labels={"topic": f"topic-{i}"})

    with pytest.raises(ValueError, match="exceeded maximum allowed series cardinality threshold"):
        reg.set("dlq_message_count", 99.0, labels={"topic": "topic-overflow"})


def test_c1_http_metrics_declare_a_budget_covering_the_route_table() -> None:
    """The declared budgets must actually fit the app's route table."""
    from shared.observability.metrics import MetricsRegistry
    from shared.observability.routes import RouteTemplateResolver

    pytest.importorskip("fastapi")
    from apps.api.oday_api.main import create_app

    resolver = RouteTemplateResolver(create_app().routes)
    template_count = resolver.template_count
    assert template_count > 100, "route table lookup regressed to a near-empty set"

    reg = MetricsRegistry()
    for m in PLATFORM_METRICS:
        reg.register(m)

    # route-only signal: one series per template plus the unmatched bucket.
    assert reg.series_budget("api_latency_ms") > template_count + 1
    # route x status signals need headroom for the status dimension.
    assert reg.series_budget("api_request_count") > template_count + 1
    assert reg.series_budget("api_error_count") > template_count + 1


def test_c1_instrumented_api_caller_survives_past_the_cardinality_cap() -> None:
    """End-to-end: requests keep succeeding past the budget, telemetry degrades.

    Drives the real middleware through a registry whose budget is deliberately
    tiny, so every request after the first few is in overflow territory. Before
    the fix this raised ValueError out of the middleware and returned 500s.
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from shared.observability import Telemetry as _Telemetry
    from shared.observability.metrics import MetricsRegistry

    tiny = MetricsRegistry(max_series_per_metric=2)
    for m in PLATFORM_METRICS:
        tiny.register(m)

    app = create_app(telemetry=_Telemetry("oday-api", metrics=tiny))
    client = TestClient(app)

    statuses = []
    for i in range(60):
        # Distinct dynamic ids + unrouted paths: both would be unbounded labels.
        statuses.append(client.get(f"/jobs/job-{i}-{'x' * 12}").status_code)
        statuses.append(client.get(f"/not-a-route/{i}").status_code)

    # No request may fail because of instrumentation.
    assert 500 not in statuses, f"instrumentation broke the request path: {sorted(set(statuses))}"

    # Cardinality stayed bounded: budget (2) + reserved overflow series.
    for name in ("api_request_count", "api_latency_ms"):
        assert tiny.series_count(name) <= 3, f"{name} exceeded its bound: {tiny.snapshot()[name]}"

    # And the route labels that were recorded are templates, not raw paths.
    recorded_routes = {
        entry["labels"].get("route") for entry in tiny.snapshot()["api_request_count"]
    }
    assert not any(
        route and route.startswith("/jobs/job-") for route in recorded_routes
    ), f"raw path leaked into the route label: {recorded_routes}"


def test_c1_api_latency_is_recorded_exactly_once_per_request() -> None:
    """The middleware must not double-count latency (operation + explicit emit)."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from apps.api.oday_api.main import create_app
    from shared.observability import Telemetry as _Telemetry
    from shared.observability.metrics import MetricsRegistry

    reg = MetricsRegistry()
    for m in PLATFORM_METRICS:
        reg.register(m)

    client = TestClient(create_app(telemetry=_Telemetry("oday-api", metrics=reg)))
    for _ in range(5):
        assert client.get("/health").status_code == 200

    health_latency = [
        entry
        for entry in reg.snapshot()["api_latency_ms"]
        if entry["labels"].get("route") == "/health"
    ]
    assert len(health_latency) == 1
    assert health_latency[0]["count"] == 5

    health_requests = [
        entry
        for entry in reg.snapshot()["api_request_count"]
        if entry["labels"].get("route") == "/health"
    ]
    assert len(health_requests) == 1
    assert health_requests[0]["value"] == 5
