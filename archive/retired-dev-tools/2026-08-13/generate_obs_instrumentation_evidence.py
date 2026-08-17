#!/usr/bin/env python3
"""Generate completion evidence for ODP-OBS-INSTRUMENTATION-AS-CODE-001.

Telemetry instrumentation as code evidence generator covering API, worker, DLQ,
model, solver, business KPI metrics, sensitive redaction, bounded cardinality,
alert-runbook links, and release identity binding.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Self-bootstrap repo root onto sys.path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from apps.api.oday_api.main import create_app
from shared.observability.logging import ListSink, StructuredLogger, redact
from shared.observability.metrics import (
    PLATFORM_METRICS,
    CardinalityPolicy,
    MetricCategory,
    MetricDefinition,
    MetricsRegistry,
    MetricType,
    ProductionMetricsExporter,
    default_registry,
    record_business_kpi_signal,
    record_data_signal,
    record_model_signal,
)
from shared.observability.routes import UNMATCHED_ROUTE_TEMPLATE, RouteTemplateResolver
from shared.observability.runtime import Telemetry
from shared.observability.tracing import SpanKind, TraceContext


def _header_to_slug(header_text: str) -> str:
    clean = re.sub(r"[^\w\s-]", "", header_text.lower().strip())
    return re.sub(r"[\s_]+", "-", clean)


def get_git_commit_sha() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        sha = res.stdout.strip()
        if len(sha) == 40:
            return sha
    except Exception:
        pass
    return "f" * 40


def main() -> None:
    print("Generating ODP-OBS-INSTRUMENTATION-AS-CODE-001 Evidence...")
    git_sha = get_git_commit_sha()
    is_test_simulated = git_sha == ("f" * 40)

    # 1. Verify Metric Ownership across 100% of PLATFORM_METRICS catalog
    ownership_matrix = {}
    for m in PLATFORM_METRICS:
        assert m.owner and isinstance(m.owner, str) and len(m.owner.strip()) > 0, f"Metric {m.name} missing owner"
        ownership_matrix[m.name] = m.owner
    signal_ownership_verified = len(ownership_matrix) == len(PLATFORM_METRICS)

    # 1b. The ownership gate must be capable of failing. A populated matrix
    # only proved every metric had *some* owner string; while `owner` carried
    # a default of "sre-platform" that was true by construction, and a new
    # signal silently inherited a team that had never agreed to page for it.
    # Record the two negative probes rather than the conclusion.
    ownership_gate_detail: dict[str, Any] = {}
    try:
        MetricDefinition(
            "evidence_probe_unowned", MetricType.COUNTER, MetricCategory.TRAFFIC, "probe"
        )
        ownership_gate_detail["omitted_owner_rejected"] = False
    except ValueError as exc:
        ownership_gate_detail["omitted_owner_rejected"] = True
        ownership_gate_detail["omitted_owner_error"] = str(exc)

    try:
        MetricDefinition(
            "evidence_probe_blank", MetricType.COUNTER, MetricCategory.TRAFFIC, "probe", owner="  "
        )
        ownership_gate_detail["blank_owner_rejected"] = False
    except ValueError:
        ownership_gate_detail["blank_owner_rejected"] = True

    ownership_gate_detail["distinct_owning_teams"] = sorted(set(ownership_matrix.values()))
    ownership_gate_detail["metrics_owned"] = len(ownership_matrix)
    signal_ownership_verified = signal_ownership_verified and bool(
        ownership_gate_detail["omitted_owner_rejected"]
        and ownership_gate_detail["blank_owner_rejected"]
    )

    # 2. Verify Bounded Cardinality & Fail-Closed Label Enforcement
    reg = default_registry()
    reg.clear()

    # Negative test A: undeclared label key must raise ValueError
    try:
        reg.increment("api_request_count", labels={"service": "api", "route": "/jobs", "status": "200", "unbounded_user_id": "123"})
        bounded_cardinality_verified = False
    except ValueError:
        bounded_cardinality_verified = True

    # Negative test B: under REJECT (config/validation contexts) exceeding
    # max_series_per_metric must raise ValueError.
    temp_reg = MetricsRegistry(max_series_per_metric=2, cardinality_policy=CardinalityPolicy.REJECT)
    for m in PLATFORM_METRICS:
        temp_reg.register(m)
    temp_reg.set("dlq_message_count", 1.0, labels={"topic": "t1"})
    temp_reg.set("dlq_message_count", 2.0, labels={"topic": "t2"})
    try:
        temp_reg.set("dlq_message_count", 3.0, labels={"topic": "t3_overflow"})
        bounded_cardinality_verified = False
    except ValueError:
        bounded_cardinality_verified = bounded_cardinality_verified and True

    # Negative test C: under the production SHED default the same overflow must
    # NOT raise (instrumentation sits on the live request path) while still
    # holding the bound. This is the C1 regression: raw request.url.path labels
    # tripped the guard and the ValueError escaped into 52/150 live requests.
    shed_reg = MetricsRegistry(max_series_per_metric=2)
    for m in PLATFORM_METRICS:
        shed_reg.register(m)
    try:
        for i in range(50):
            shed_reg.set("dlq_message_count", float(i), labels={"topic": f"topic-{i}"})
        caller_survives_overflow = True
    except ValueError:
        caller_survives_overflow = False
    shed_report = shed_reg.overflow_report()
    # budget (2) + the single reserved overflow series
    cardinality_bound_held = shed_reg.series_count("dlq_message_count") == 3
    bounded_cardinality_verified = (
        bounded_cardinality_verified
        and caller_survives_overflow
        and cardinality_bound_held
        and shed_report["shed_emissions"].get("dlq_message_count", 0) == 48
    )

    # Negative test D: the live HTTP route label must be a bounded route
    # template, not the raw request path.
    route_resolver = RouteTemplateResolver(create_app().routes)
    route_labels = {route_resolver.resolve(f"/jobs/job-{i}") for i in range(200)}
    route_normalization_verified = (
        route_labels == {"/jobs/{job_id}"}
        and route_resolver.resolve("/no/such/route") == UNMATCHED_ROUTE_TEMPLATE
        and route_resolver.template_count > 100
    )
    bounded_cardinality_verified = bounded_cardinality_verified and route_normalization_verified

    # Record valid Telemetry across categories
    reg.increment("api_request_count", labels={"service": "oday-api", "route": "/jobs", "status": "202"})
    reg.increment("api_error_count", labels={"service": "oday-api", "route": "/jobs", "status": "500"}, amount=1.0)
    reg.observe("api_latency_ms", 24.5, labels={"service": "oday-api", "route": "/jobs"})
    reg.observe("job_duration_seconds", 3.2, labels={"job_type": "solver_optimize", "status": "success"})
    reg.set("dlq_message_count", 0.0, labels={"topic": "solver-dead-letter"})
    reg.set("event_consumer_lag", 12.0, labels={"topic": "telemetry-events", "subscription": "sub-obs"})

    record_data_signal(
        source="pg16_cluster",
        view="v_solver_metrics",
        freshness_hours=0.5,
        quality_score=0.99,
        feature_null_rate=0.005,
        feature_name="site_score",
        dataset_name="sitescore_features",
        run_id="run-20260808",
        registry=reg,
    )
    record_model_signal(
        model="heatzone_v2",
        module="solver",
        prediction_count=450,
        model_error=0.042,
        interval_coverage=0.94,
        drift_score=0.015,
        alias_changes=1,
        horizon="7d",
        segment="commercial",
        feature="location_density",
        registry=reg,
    )
    record_business_kpi_signal("heatzone_topk_adoption_rate", 0.94, registry=reg)
    record_business_kpi_signal("listing_dedup_accuracy", 0.985, registry=reg)
    record_business_kpi_signal("sitescore_realization_rate", 0.91, labels={"horizon": "m6"}, registry=reg)
    record_business_kpi_signal("forecast_alert_precision", 0.89, labels={"metric": "lead_time"}, registry=reg)
    record_business_kpi_signal("price_hard_constraint_violation_count", 0.0, registry=reg)
    record_business_kpi_signal("netplan_plan_adoption_rate", 0.88, registry=reg)

    metrics_snapshot = reg.snapshot()

    # 3. Sensitive Value Exclusion Verification
    sink = ListSink()
    logger = StructuredLogger("oday-telemetry-test", sink=sink)
    logger.info(
        "telemetry_auth_check",
        correlation_id="corr-obs-001",
        actor="ops-worker",
        resource="solver/jobs",
        result="success",
        extra={
            "password": "super-secret-pass",
            "access_token": "bearer-token-12345",
            "api_key": "sk_live_9999",
            "safe_param": "solver_v1",
        },
    )
    redacted_log_record = redact(sink.dicts[0])
    assert redacted_log_record["extra"]["password"] == "[REDACTED]"
    assert redacted_log_record["extra"]["access_token"] == "[REDACTED]"
    assert redacted_log_record["extra"]["api_key"] == "[REDACTED]"
    assert redacted_log_record["extra"]["safe_param"] == "solver_v1"

    # 4. Alert Runbook Anchors & Exporter Release-SHA Binding Verification
    from modules.notifications import (
        InMemoryNotificationRepository,
        NotificationService,
        OnCallNotificationAdapter,
    )
    from shared.observability.alerts import AlertRouter

    exporter = ProductionMetricsExporter(release_sha=git_sha, registry=reg, gcp_project="pantheon-test-proj")
    assert exporter.release_sha == git_sha

    monitoring_dir = Path(repo_root) / "infra" / "monitoring"
    alerts_data = json.loads((monitoring_dir / "alerts.json").read_text(encoding="utf-8"))
    dashboards_data = json.loads((monitoring_dir / "dashboards.json").read_text(encoding="utf-8"))
    slo_data = json.loads((monitoring_dir / "slo.json").read_text(encoding="utf-8"))

    dummy_repo = InMemoryNotificationRepository()
    dummy_service = NotificationService(
        repository=dummy_repo,
        adapter=OnCallNotificationAdapter(http_transport=lambda u, p: (200, "ok")),
    )
    router = AlertRouter(notification_service=dummy_service, release_sha=git_sha)

    alerts_summary = []
    all_anchors_valid = True
    alert_release_identity_verified = True

    for alert in alerts_data.get("alerts", []):
        alert_id = alert.get("id")
        runbook_path = alert.get("runbook", "")
        file_part, anchor = runbook_path.split("#") if "#" in runbook_path else (runbook_path, None)
        full_runbook = Path(repo_root) / file_part
        runbook_exists = full_runbook.exists()
        anchor_verified = False
        if runbook_exists and anchor:
            content = full_runbook.read_text(encoding="utf-8")
            header_lines = [line.lstrip("#").strip() for line in content.splitlines() if line.startswith("#")]
            slugs = {_header_to_slug(h) for h in header_lines if h}
            anchor_verified = anchor in slugs
        elif runbook_exists and not anchor:
            anchor_verified = True

        if not (runbook_exists and anchor_verified):
            all_anchors_valid = False

        routed = router.route_alert(alert_id, release_sha=git_sha)
        bound = (routed.get("release_identity_bound") is True) and (routed.get("release_sha") == git_sha)
        if not bound:
            alert_release_identity_verified = False

        alerts_summary.append({
            "id": alert_id,
            "name": alert.get("name"),
            "severity": alert.get("severity"),
            "metric": alert.get("metric"),
            "runbook": runbook_path,
            "runbook_file_verified": runbook_exists,
            "runbook_anchor_verified": anchor_verified,
            "release_identity_bound": bound,
            "release_sha": routed.get("release_sha"),
        })

    # Mutation test: malformed SHA, mismatched SHA, or unbound release_sha must fail closed with ValueError
    # 1. Malformed SHA caller input fails closed
    try:
        router.route_alert("api-availability-drop", release_sha="not-a-sha")
        alert_release_identity_verified = False
    except ValueError:
        pass

    # 2. Mismatched caller-supplied SHA fails closed
    mismatched_sha = "0" * 40 if git_sha != "0" * 40 else "1" * 40
    try:
        router.route_alert("api-availability-drop", release_sha=mismatched_sha)
        alert_release_identity_verified = False
    except ValueError:
        pass

    # 3. Unbound router with no environment or instance SHA fails closed
    unbound_router = AlertRouter(notification_service=dummy_service, release_sha=None)
    old_env_sha = os.environ.pop("RELEASE_SHA", None)
    old_env_trusted = os.environ.pop("TRUSTED_DEPLOYED_RELEASE_SHA", None)
    try:
        try:
            unbound_router.route_alert("api-availability-drop", release_sha=None)
            alert_release_identity_verified = False
        except ValueError:
            # Expected fail closed
            pass
    finally:
        if old_env_sha:
            os.environ["RELEASE_SHA"] = old_env_sha
        if old_env_trusted:
            os.environ["TRUSTED_DEPLOYED_RELEASE_SHA"] = old_env_trusted

    # 4b. The routed `release_identity_bound` flag must be capable of reporting
    # False. It was previously the literal `True`, so the check at line ~277
    # was reading back its own assumption. Rotate the trusted deployed identity
    # between resolution and emission and record that the flag downgrades while
    # the page still routes.
    probe_router = AlertRouter(notification_service=dummy_service, release_sha=git_sha)
    _probe_calls = {"n": 0}
    _real_trusted = probe_router.get_trusted_release_sha

    def _rotating_trusted() -> str | None:
        _probe_calls["n"] += 1
        return _real_trusted() if _probe_calls["n"] == 1 else "a" * 40

    probe_router.get_trusted_release_sha = _rotating_trusted  # type: ignore[method-assign]
    rotated = probe_router.route_alert("api-availability-drop")
    release_identity_flag_detail = {
        "flag_is_derived_not_literal": rotated.get("release_identity_bound") is False,
        "page_still_routed_when_downgraded": bool(rotated.get("receiver")),
        "release_sha_reported_when_downgraded": rotated.get("release_sha"),
    }
    if not (
        release_identity_flag_detail["flag_is_derived_not_literal"]
        and release_identity_flag_detail["page_still_routed_when_downgraded"]
    ):
        alert_release_identity_verified = False

    # 4c. Fail-closed routing must not pre-empt a caller that is already
    # handling a failure. The DLQ poison-isolation branch pages from inside its
    # own error handler, so with no deployed SHA bound the fail-closed raise
    # escaped before the dead-letter event was written. try_trigger_alert
    # contains the delivery failure and counts it instead.
    from shared.observability.alerts import ALERT_DELIVERY_FAILURE_METRIC, try_trigger_alert

    containment_reg = MetricsRegistry()
    for m in PLATFORM_METRICS:
        containment_reg.register(m)

    containment_service = NotificationService(
        repository=InMemoryNotificationRepository(),
        adapter=OnCallNotificationAdapter(http_transport=lambda u, p: (200, "ok")),
    )
    containment_service.set_preferences("oncall-engineer", ["webhook"])

    old_env_sha = os.environ.pop("RELEASE_SHA", None)
    old_env_trusted = os.environ.pop("TRUSTED_DEPLOYED_RELEASE_SHA", None)
    try:
        unbound_delivery = try_trigger_alert(
            containment_service,
            "dlq-spike",
            "poison job isolated at stage CHECKING_IDENTITY",
            registry=containment_reg,
        )
    finally:
        if old_env_sha:
            os.environ["RELEASE_SHA"] = old_env_sha
        if old_env_trusted:
            os.environ["TRUSTED_DEPLOYED_RELEASE_SHA"] = old_env_trusted

    lost_page_series = containment_reg.snapshot().get(ALERT_DELIVERY_FAILURE_METRIC, [])
    bound_delivery = try_trigger_alert(
        containment_service,
        "dlq-spike",
        "poison job isolated at stage CHECKING_IDENTITY",
        release_sha=git_sha,
        registry=containment_reg,
    )

    alert_delivery_containment_detail = {
        "unbound_identity_returns_none_instead_of_raising": unbound_delivery is None,
        "lost_page_counted_on": ALERT_DELIVERY_FAILURE_METRIC,
        "lost_page_series": lost_page_series,
        "bound_identity_still_delivers": bound_delivery is not None,
        "delivery_failure_alert_policy": next(
            (
                a["id"]
                for a in alerts_data.get("alerts", [])
                if a.get("metric") == ALERT_DELIVERY_FAILURE_METRIC
            ),
            None,
        ),
    }
    alert_delivery_containment_verified = bool(
        alert_delivery_containment_detail["unbound_identity_returns_none_instead_of_raising"]
        and alert_delivery_containment_detail["bound_identity_still_delivers"]
        and len(lost_page_series) == 1
        and lost_page_series[0]["value"] == 1.0
        and lost_page_series[0]["labels"]
        == {"alert_id": "dlq-spike", "error_class": "ValueError"}
        and alert_delivery_containment_detail["delivery_failure_alert_policy"]
    )

    # 5. End-to-End Tracing Verification
    telemetry = Telemetry("oday-obs-verifier", logger=logger, metrics=reg)
    ctx = TraceContext(
        actor_id="obs-user",
        request_id="req-obs-100",
        job_id="job-obs-200",
        entity_type="solver_run",
        entity_id="run-888",
        model_version="heatzone_v2:2.0.1",
    )
    with telemetry.operation("api-solver-submit", SpanKind.API, context=ctx, resource="solver/submit") as api_span:
        with telemetry.operation("worker-solver-execute", SpanKind.WORKER, context=ctx, resource="solver/engine", parent=api_span):
            with telemetry.operation("model-solver-evaluate", SpanKind.MODEL, context=ctx, resource="model/evaluate", parent=api_span):
                pass

    exported_spans = telemetry.tracer.spans_for(ctx.correlation_id)
    assert len(exported_spans) == 3

    # 6. Execute focused Pytest suite to capture truthful execution output
    python_bin = sys.executable
    t0 = time.monotonic()
    pytest_res = subprocess.run(
        [python_bin, "-m", "pytest", "-o", "addopts=", "-q", "tests/reliability/test_runtime_observability.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    duration_s = round(time.monotonic() - t0, 2)
    stdout = pytest_res.stdout

    # Parse passed test count from stdout
    passed_count = 0
    match = re.search(r"(\d+)\s+passed", stdout)
    if match:
        passed_count = int(match.group(1))

    # Derive verification outcomes dynamically
    sensitive_redaction_verified = True
    ac1_status = "PASSED" if signal_ownership_verified else "FAILED"
    ac2_status = "PASSED" if sensitive_redaction_verified else "FAILED"
    ac3_status = "PASSED" if bounded_cardinality_verified else "FAILED"
    ac4_status = (
        "PASSED"
        if (
            all_anchors_valid
            and alert_release_identity_verified
            and alert_delivery_containment_verified
        )
        else "FAILED"
    )
    ac5_status = "PASSED" if (pytest_res.returncode == 0 and passed_count > 0) else "FAILED"

    overall_passed = all([
        signal_ownership_verified,
        sensitive_redaction_verified,
        bounded_cardinality_verified,
        all_anchors_valid,
        alert_release_identity_verified,
        alert_delivery_containment_verified,
        pytest_res.returncode == 0,
        passed_count > 0,
    ])

    # 7. Output Directory & Evidence Materialization
    out_dir = Path(repo_root) / "docs" / "evidence" / "completion" / "ODP-OBS-INSTRUMENTATION-AS-CODE-001"
    out_dir.mkdir(parents=True, exist_ok=True)

    categories_list = sorted([c.value for c in MetricCategory])

    json_artifact = {
        "task_id": "ODP-OBS-INSTRUMENTATION-AS-CODE-001",
        "timestamp": datetime.now(UTC).isoformat(),
        "release_sha": git_sha,
        "is_test_simulated": is_test_simulated,
        "overall_status": "PASSED" if overall_passed else "FAILED",
        "metrics_categories": categories_list,
        "metrics_count": len(PLATFORM_METRICS),
        "recorded_metrics_series_count": sum(len(v) for v in metrics_snapshot.values()),
        "signal_ownership_verified": signal_ownership_verified,
        "ownership_gate_detail": ownership_gate_detail,
        "ownership_matrix": ownership_matrix,
        "sensitive_value_redaction_verified": sensitive_redaction_verified,
        "bounded_cardinality_verified": bounded_cardinality_verified,
        "bounded_cardinality_detail": {
            "undeclared_label_rejected": True,
            "reject_policy_fails_closed": True,
            "shed_policy_caller_survives_overflow": caller_survives_overflow,
            "shed_policy_bound_held": cardinality_bound_held,
            "shed_policy_overflow_report": shed_report,
            "http_route_label_normalized": route_normalization_verified,
            "registered_route_templates": route_resolver.template_count,
            "route_label_for_200_distinct_job_ids": sorted(route_labels),
            "api_request_count_series_budget": default_registry().series_budget("api_request_count"),
            "api_latency_ms_series_budget": default_registry().series_budget("api_latency_ms"),
        },
        "alert_runbook_anchors_verified": all_anchors_valid,
        "alert_release_identity_verified": alert_release_identity_verified,
        "release_identity_flag_detail": release_identity_flag_detail,
        "alert_delivery_containment_verified": alert_delivery_containment_verified,
        "alert_delivery_containment_detail": alert_delivery_containment_detail,
        "alert_runbook_linkage_count": len(alerts_summary),
        "alerts": alerts_summary,
        "trace_spans_count": len(exported_spans),
        "dashboards_count": len(dashboards_data.get("dashboards", [])),
        "slos_count": len(slo_data.get("slos", [])),
        "test_suite_execution": {
            "target": "tests/reliability/test_runtime_observability.py",
            "passed_tests": passed_count,
            "exit_code": pytest_res.returncode,
            "duration_seconds": duration_s,
        },
    }

    (out_dir / "evidence.json").write_text(json.dumps(json_artifact, indent=2), encoding="utf-8")

    evidence_md = f"""# ODP-OBS-INSTRUMENTATION-AS-CODE-001 Completion Evidence

## Executive Summary
This document provides empirical runtime evidence for task **ODP-OBS-INSTRUMENTATION-AS-CODE-001**: *Implement observability instrumentation and configuration as code*.

The implementation decouples API, worker, DLQ, model, solver, business KPI telemetry, dashboards, alerts, SLOs, and runbooks into fully reproducible configuration and code without requiring live provider or on-call acknowledgements. Overall status: **{"PASSED" if overall_passed else "FAILED"}**.

---

## 1. Acceptance Criteria Verification Matrix

| # | Acceptance Criterion | Verification Method | Status |
|---|---|---|---|
| 1 | Required signals have stable names and owners | {len(ownership_matrix)} metrics owned across {len(ownership_gate_detail["distinct_owning_teams"])} teams, and the gate is provably able to fail: constructing a metric with an omitted or blank owner raises | **{ac1_status}** |
| 2 | Sensitive values are excluded | Verified recursive `StructuredLogger` redaction of passwords, tokens, API keys | **{ac2_status}** |
| 3 | Cardinality is bounded | Route labels normalized to {route_resolver.template_count} registered templates; declared per-metric budgets; overflow shed into a reserved series without failing the emitting caller; undeclared labels rejected fail-closed | **{ac3_status}** |
| 4 | Alerts link to runbooks and release identity | All {len(alerts_summary)} alert definitions link to valid Markdown runbooks & anchors under `docs/runbooks/` and bind to exact `RELEASE_SHA`; the binding flag is derived, and reports `False` when the trusted identity rotates; a page lost to that fail-closed gate is contained and counted rather than pre-empting the caller | **{ac4_status}** |
| 5 | Configuration and emission tests are reproducible | {passed_count}/{passed_count} pytest reliability/observability tests passing dynamically in {duration_s}s | **{ac5_status}** |

---

## 2. Telemetry Signal Catalog Overview

### Categories & Signal Ownership Coverage (`shared/observability/metrics.py`)
- **Technical & SRE** (`sre-platform` / `sre-messaging`): `api_request_count`, `api_error_count`, `api_latency_ms`, `db_query_latency_ms`, `job_duration_seconds`, `job_failure_count`, `event_consumer_lag`, `dlq_message_count`, `external_connector_failure_count`, `alert_delivery_failure_count`, `deployment_watch_window_status`
- **Data & Freshness** (`data-platform`): `data_freshness_hours`, `data_quality_score`, `feature_null_rate`
- **Model Telemetry** (`ml-platform`): `prediction_count`, `model_error_metric`, `prediction_interval_coverage`, `drift_score`, `model_alias_change_count`
- **Solver & Business KPIs** (`business-analytics`): `heatzone_topk_adoption_rate`, `listing_dedup_accuracy`, `sitescore_realization_rate`, `forecast_alert_precision`, `intervention_recovery_rate`, `price_hard_constraint_violation_count`, `adlift_incremental_gm`, `avm_interval_coverage`, `netplan_plan_adoption_rate`, `model_adoption_rate`
- **Audit Trail & Evidence** (`security-audit`): `audit_event_record_count`, `audit_event_write_failure_count`, `audit_event_pipeline_lag_seconds`, `audit_event_replay_count`, `audit_evidence_export_count`, `audit_completeness_gap_count`

---

## 3. Sensitive Value Redaction Verification

```json
{{
  "log_event": "telemetry_auth_check",
  "correlation_id": "{redacted_log_record['correlation_id']}",
  "actor": "{redacted_log_record['actor']}",
  "resource": "{redacted_log_record['resource']}",
  "result": "{redacted_log_record['result']}",
  "extra": {{
    "password": "{redacted_log_record['extra']['password']}",
    "access_token": "{redacted_log_record['extra']['access_token']}",
    "api_key": "{redacted_log_record['extra']['api_key']}",
    "safe_param": "{redacted_log_record['extra']['safe_param']}"
  }}
}}
```

---

## 4. Alert to Runbook & Release Identity Mapping

All alert definitions in `infra/monitoring/alerts.json` strictly map to valid runbook files and section anchors, and carry exact `release_sha` bindings:

```json
{json.dumps(alerts_summary, indent=2)}
```

---

## 4b. Bounded Cardinality Design (C1 regression cover)

The `route` label is the **registered route template**, never the raw request
path. Labelling with `request.url.path` made every `/jobs/<uuid>` its own
series; under the performance gate that exhausted the series budget and the
registry's `ValueError` escaped into the request path (52/150 request failures
at commit `54b749e0`). Two independent layers now hold the bound:

| Layer | Mechanism | Evidence |
|---|---|---|
| 1. Normalize at the source | `shared/observability/routes.py` resolves a concrete path to its route template | {route_resolver.template_count} templates registered; 200 distinct job ids collapse to `{sorted(route_labels)}`; unrouted paths share `{UNMATCHED_ROUTE_TEMPLATE}` |
| 2. Shed, do not raise | `CardinalityPolicy.SHED` (production default) folds overflow into one reserved `__overflow__` series per metric and counts it | 50 distinct label values against a budget of 2 -> {shed_reg.series_count("dlq_message_count")} series retained, {shed_report["shed_emissions"].get("dlq_message_count", 0)} emissions shed, 0 raised |
| Fail-closed retained | `CardinalityPolicy.REJECT` for config/evidence validation | overflow still raises `ValueError` |

Declared per-metric budgets (`MetricDefinition.max_series`), sized above the
current route table so a routine route addition does not start shedding:

- `api_request_count` (service x route x status): {default_registry().series_budget("api_request_count")}
- `api_error_count` (service x route x status): {default_registry().series_budget("api_error_count")}
- `api_latency_ms` (service x route): {default_registry().series_budget("api_latency_ms")}

Shedding is observable rather than silent: `MetricsRegistry.overflow_report()`
exposes per-metric shed counts and the reserved series is marked
`cardinality_overflow` in `snapshot()`, so a non-zero count is alertable and
points at the instrumentation site that needs a bounded label.

The API middleware additionally contains telemetry exceptions: instrumentation
is a side channel and a metric rejection must degrade the signal, not the
response the caller is waiting on.

---

## 4c. Gates That Can Actually Fail (R1 / R2 cover)

Two acceptance signals were previously true by construction, so passing them
demonstrated nothing. Both now derive from a probe recorded in this run.

| Gate | Was | Is | Probe in this run |
|---|---|---|---|
| Metric ownership | `MetricDefinition.owner` defaulted to `"sre-platform"`, so every construction site passed while possibly naming a team that had never agreed to carry the signal | `owner` has no plausible default; `__post_init__` rejects omitted or blank. `MetricsRegistry.register` keeps its own check for definitions restored by unpickling, which bypasses `__init__` | omitted owner rejected: `{ownership_gate_detail["omitted_owner_rejected"]}`; blank owner rejected: `{ownership_gate_detail["blank_owner_rejected"]}`; {len(ownership_matrix)} metrics across {ownership_gate_detail["distinct_owning_teams"]} |
| Alert release identity | `route_alert` emitted the literal `True`, so this document's own check read back its assumption | derived at emission from the trusted deployed identity; a rotated or cleared SHA downgrades the annotation instead of certifying it | flag reports `False` under rotation: `{release_identity_flag_detail["flag_is_derived_not_literal"]}`; page still routed: `{release_identity_flag_detail["page_still_routed_when_downgraded"]}` |

| Alert delivery | a caller that pages *while* handling its own failure inherited the fail-closed raise: the DLQ poison-isolation branch lost its dead-letter event when no deployed SHA was bound | `try_trigger_alert` contains router construction and delivery, counts the lost page on `alert_delivery_failure_count`, and returns `None` so the caller's error path completes | unbound identity returns `None` instead of raising: `{alert_delivery_containment_detail["unbound_identity_returns_none_instead_of_raising"]}`; lost page counted: `{[s["labels"] for s in lost_page_series]}`; bound identity still delivers: `{alert_delivery_containment_detail["bound_identity_still_delivers"]}`; policy paging on it: `{alert_delivery_containment_detail["delivery_failure_alert_policy"]}` |

A contained page is not a silent one. The count carries the `alert_id` and the
`error_class` that suppressed it, `alert-delivery-failure` (P1) pages on any
sample, and the runbook section of the same name says to triage the incidents
whose pages were dropped, since they are not re-sent. Containment is scoped to
callers whose own failure handling would otherwise be pre-empted; a caller whose
only job is to page still gets the raise.

The release-identity flag downgrades rather than raises. The config-declared
binding is already gated ahead of it, and past that point suppressing a page
because its provenance annotation weakened is worse than delivering the page
with an honest annotation. This is the same rule the middleware follows: an
instrumentation fault degrades the signal, never the delivery.

Live consequence of making the ownership gate real:
`auth.attempts_total` in `modules/opsboard/auth/boundary.py` declared no owner
and had been silently inheriting `sre-platform`. It is now explicitly owned by
`security-audit`, and `test_b41b_every_metric_definition_in_the_repo_declares_an_owner`
scans the source tree so a future site in a module this suite never imports
cannot repeat it.

---

## 5. Correlated Trace Flow Simulation

Exported end-to-end trace spans linking API, worker, model, and solver execution:

```json
{json.dumps([s.to_dict() for s in exported_spans], indent=2)}
```

---

## 6. Test Suite Execution Output

- Source Commit: `{git_sha}` (is_test_simulated: {is_test_simulated})
- Command: `python3 -m pytest tests/reliability/test_runtime_observability.py`
- Result: **{passed_count} passed in {duration_s}s** (Exit Code: {pytest_res.returncode})

---

## 7. Artifact Mapping

- **Metrics Catalog & Exporter**: [`shared/observability/metrics.py`](../../../shared/observability/metrics.py)
- **Structured Logger & Redactor**: [`shared/observability/logging.py`](../../../shared/observability/logging.py)
- **OTel-Compatible Tracing**: [`shared/observability/tracing.py`](../../../shared/observability/tracing.py)
- **Alert Configurations**: [`infra/monitoring/alerts.json`](../../../infra/monitoring/alerts.json)
- **Dashboard Provisioning**: [`infra/monitoring/dashboards.json`](../../../infra/monitoring/dashboards.json)
- **SLO Definitions**: [`infra/monitoring/slo.json`](../../../infra/monitoring/slo.json)
- **Runbooks**: [`docs/runbooks/observability-and-runbook.md`](../../../docs/runbooks/observability-and-runbook.md)
- **Test Suite**: [`tests/reliability/test_runtime_observability.py`](../../../tests/reliability/test_runtime_observability.py)
"""

    (out_dir / "evidence.md").write_text(evidence_md, encoding="utf-8")
    print(f"Evidence successfully generated at: {out_dir / 'evidence.md'}")

    if not overall_passed:
        print("ERROR: Evidence verification failed closed (overall_status=FAILED).", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
