# ODP-OBS-INSTRUMENTATION-AS-CODE-001 Completion Evidence

## Executive Summary
This document provides empirical runtime evidence for task **ODP-OBS-INSTRUMENTATION-AS-CODE-001**: *Implement observability instrumentation and configuration as code*.

The implementation decouples API, worker, DLQ, model, solver, business KPI telemetry, dashboards, alerts, SLOs, and runbooks into fully reproducible configuration and code without requiring live provider or on-call acknowledgements. Overall status: **PASSED**.

---

## 1. Acceptance Criteria Verification Matrix

| # | Acceptance Criterion | Verification Method | Status |
|---|---|---|---|
| 1 | Required signals have stable names and owners | Verified 100% metric ownership across SRE, Data, Model, Business KPI, Audit | **PASSED** |
| 2 | Sensitive values are excluded | Verified recursive `StructuredLogger` redaction of passwords, tokens, API keys | **PASSED** |
| 3 | Cardinality is bounded | Route labels normalized to 459 registered templates; declared per-metric budgets; overflow shed into a reserved series without failing the emitting caller; undeclared labels rejected fail-closed | **PASSED** |
| 4 | Alerts link to runbooks and release identity | Verified all 11 alert definitions link to valid Markdown runbooks & anchors under `docs/runbooks/` and bind to exact `RELEASE_SHA` | **PASSED** |
| 5 | Configuration and emission tests are reproducible | 84/84 pytest reliability/observability tests passing dynamically in 39.7s | **PASSED** |

---

## 2. Telemetry Signal Catalog Overview

### Categories & Signal Ownership Coverage (`shared/observability/metrics.py`)
- **Technical & SRE** (`sre-platform` / `sre-messaging`): `api_request_count`, `api_error_count`, `api_latency_ms`, `db_query_latency_ms`, `job_duration_seconds`, `job_failure_count`, `event_consumer_lag`, `dlq_message_count`, `external_connector_failure_count`, `deployment_watch_window_status`
- **Data & Freshness** (`data-platform`): `data_freshness_hours`, `data_quality_score`, `feature_null_rate`
- **Model Telemetry** (`ml-platform`): `prediction_count`, `model_error_metric`, `prediction_interval_coverage`, `drift_score`, `model_alias_change_count`
- **Solver & Business KPIs** (`business-analytics`): `heatzone_topk_adoption_rate`, `listing_dedup_accuracy`, `sitescore_realization_rate`, `forecast_alert_precision`, `intervention_recovery_rate`, `price_hard_constraint_violation_count`, `adlift_incremental_gm`, `avm_interval_coverage`, `netplan_plan_adoption_rate`, `model_adoption_rate`
- **Audit Trail & Evidence** (`security-audit`): `audit_event_record_count`, `audit_event_write_failure_count`, `audit_event_pipeline_lag_seconds`, `audit_event_replay_count`, `audit_evidence_export_count`, `audit_completeness_gap_count`

---

## 3. Sensitive Value Redaction Verification

```json
{
  "log_event": "telemetry_auth_check",
  "correlation_id": "corr-obs-001",
  "actor": "ops-worker",
  "resource": "solver/jobs",
  "result": "success",
  "extra": {
    "password": "[REDACTED]",
    "access_token": "[REDACTED]",
    "api_key": "[REDACTED]",
    "safe_param": "solver_v1"
  }
}
```

---

## 4. Alert to Runbook & Release Identity Mapping

All alert definitions in `infra/monitoring/alerts.json` strictly map to valid runbook files and section anchors, and carry exact `release_sha` bindings:

```json
[
  {
    "id": "api-availability-drop",
    "name": "API availability drop",
    "severity": "P1",
    "metric": "api_error_count",
    "runbook": "docs/runbooks/observability-and-runbook.md#api-anomaly",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  },
  {
    "id": "forecast-daily-failed",
    "name": "Forecast daily batch failed",
    "severity": "P2",
    "metric": "job_failure_count",
    "runbook": "docs/runbooks/observability-and-runbook.md#forecastops",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  },
  {
    "id": "data-quality-p0-fail",
    "name": "Data quality P0 fail",
    "severity": "P2",
    "metric": "data_quality_score",
    "runbook": "docs/runbooks/observability-and-runbook.md#data-freshness",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  },
  {
    "id": "dlq-spike",
    "name": "DLQ spike",
    "severity": "P2",
    "metric": "dlq_message_count",
    "runbook": "docs/runbooks/observability-and-runbook.md#job-failure",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  },
  {
    "id": "unauthorized-spike",
    "name": "Unauthorized spike",
    "severity": "P2",
    "metric": "api_error_count",
    "runbook": "docs/runbooks/observability-and-runbook.md#audit-write-failure",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  },
  {
    "id": "audit-write-failure",
    "name": "Audit write failure",
    "severity": "P1",
    "metric": "audit_event_write_failure_count",
    "runbook": "docs/runbooks/observability-and-runbook.md#audit-write-failure",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  },
  {
    "id": "model-drift-high",
    "name": "Model drift high",
    "severity": "P2",
    "metric": "drift_score",
    "runbook": "docs/runbooks/observability-and-runbook.md#model-release",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  },
  {
    "id": "price-constraint-violation",
    "name": "Price hard-constraint violation",
    "severity": "P1",
    "metric": "price_hard_constraint_violation_count",
    "runbook": "docs/runbooks/observability-and-runbook.md#priceops",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  },
  {
    "id": "data-room-abnormal-download",
    "name": "Data Room abnormal download",
    "severity": "P1",
    "metric": "audit_evidence_export_count",
    "runbook": "docs/runbooks/observability-and-runbook.md#audit-write-failure",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  },
  {
    "id": "solver-repeated-infeasible",
    "name": "Solver repeated infeasible",
    "severity": "P3",
    "metric": "job_failure_count",
    "runbook": "docs/runbooks/observability-and-runbook.md#netplan",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  },
  {
    "id": "external-connector-stale",
    "name": "External connector stale",
    "severity": "P3",
    "metric": "data_freshness_hours",
    "runbook": "docs/runbooks/observability-and-runbook.md#data-freshness",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "615b1942653f5e99af1738e07099ac81908116c4"
  }
]
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
| 1. Normalize at the source | `shared/observability/routes.py` resolves a concrete path to its route template | 459 templates registered; 200 distinct job ids collapse to `['/jobs/{job_id}']`; unrouted paths share `__unmatched__` |
| 2. Shed, do not raise | `CardinalityPolicy.SHED` (production default) folds overflow into one reserved `__overflow__` series per metric and counts it | 50 distinct label values against a budget of 2 -> 3 series retained, 48 emissions shed, 0 raised |
| Fail-closed retained | `CardinalityPolicy.REJECT` for config/evidence validation | overflow still raises `ValueError` |

Declared per-metric budgets (`MetricDefinition.max_series`), sized above the
current route table so a routine route addition does not start shedding:

- `api_request_count` (service x route x status): 2000
- `api_error_count` (service x route x status): 2000
- `api_latency_ms` (service x route): 1000

Shedding is observable rather than silent: `MetricsRegistry.overflow_report()`
exposes per-metric shed counts and the reserved series is marked
`cardinality_overflow` in `snapshot()`, so a non-zero count is alertable and
points at the instrumentation site that needs a bounded label.

The API middleware additionally contains telemetry exceptions: instrumentation
is a side channel and a metric rejection must degrade the signal, not the
response the caller is waiting on.

---

## 5. Correlated Trace Flow Simulation

Exported end-to-end trace spans linking API, worker, model, and solver execution:

```json
[
  {
    "span_id": "0c4bcffdf24842e3",
    "parent_id": "946374eacc394eaf",
    "name": "model-solver-evaluate",
    "kind": "model",
    "correlation_id": "c95b96e4-6ca7-431a-aa08-43aad7852848",
    "actor_id": "obs-user",
    "status": "ok",
    "error_code": null,
    "duration_ms": 0.003222,
    "attributes": {
      "correlation_id": "c95b96e4-6ca7-431a-aa08-43aad7852848",
      "request_id": "req-obs-100",
      "job_id": "job-obs-200",
      "actor_id": "obs-user",
      "entity_type": "solver_run",
      "entity_id": "run-888",
      "model_version": "heatzone_v2:2.0.1"
    }
  },
  {
    "span_id": "d696229090c54db9",
    "parent_id": "946374eacc394eaf",
    "name": "worker-solver-execute",
    "kind": "worker",
    "correlation_id": "c95b96e4-6ca7-431a-aa08-43aad7852848",
    "actor_id": "obs-user",
    "status": "ok",
    "error_code": null,
    "duration_ms": 0.150956,
    "attributes": {
      "correlation_id": "c95b96e4-6ca7-431a-aa08-43aad7852848",
      "request_id": "req-obs-100",
      "job_id": "job-obs-200",
      "actor_id": "obs-user",
      "entity_type": "solver_run",
      "entity_id": "run-888",
      "model_version": "heatzone_v2:2.0.1"
    }
  },
  {
    "span_id": "946374eacc394eaf",
    "parent_id": null,
    "name": "api-solver-submit",
    "kind": "api",
    "correlation_id": "c95b96e4-6ca7-431a-aa08-43aad7852848",
    "actor_id": "obs-user",
    "status": "ok",
    "error_code": null,
    "duration_ms": 0.231594,
    "attributes": {
      "correlation_id": "c95b96e4-6ca7-431a-aa08-43aad7852848",
      "request_id": "req-obs-100",
      "job_id": "job-obs-200",
      "actor_id": "obs-user",
      "entity_type": "solver_run",
      "entity_id": "run-888",
      "model_version": "heatzone_v2:2.0.1"
    }
  }
]
```

---

## 6. Test Suite Execution Output

- Source Commit: `615b1942653f5e99af1738e07099ac81908116c4` (is_test_simulated: False)
- Command: `python3 -m pytest tests/reliability/test_runtime_observability.py`
- Result: **84 passed in 39.7s** (Exit Code: 0)

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
