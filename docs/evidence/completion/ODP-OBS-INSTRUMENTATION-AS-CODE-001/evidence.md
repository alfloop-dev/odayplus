# ODP-OBS-INSTRUMENTATION-AS-CODE-001 Completion Evidence

## Executive Summary
This document provides empirical runtime evidence for task **ODP-OBS-INSTRUMENTATION-AS-CODE-001**: *Implement observability instrumentation and configuration as code*.

The implementation decouples API, worker, DLQ, model, solver, business KPI telemetry, dashboards, alerts, SLOs, and runbooks into fully reproducible configuration and code without requiring live provider or on-call acknowledgements. Overall status: **PASSED**.

---

## 1. Acceptance Criteria Verification Matrix

| # | Acceptance Criterion | Verification Method | Status |
|---|---|---|---|
| 1 | Required signals have stable names and owners | 35 metrics owned across 6 teams, and the gate is provably able to fail: constructing a metric with an omitted or blank owner raises | **PASSED** |
| 2 | Sensitive values are excluded | Verified recursive `StructuredLogger` redaction of passwords, tokens, API keys | **PASSED** |
| 3 | Cardinality is bounded | Route labels normalized to 469 registered templates; declared per-metric budgets; overflow shed into a reserved series without failing the emitting caller; undeclared labels rejected fail-closed | **PASSED** |
| 4 | Alerts link to runbooks and release identity | All 12 alert definitions link to valid Markdown runbooks & anchors under `docs/runbooks/` and bind to exact `RELEASE_SHA`; the binding flag is derived, and reports `False` when the trusted identity rotates; a page lost to that fail-closed gate is contained and counted rather than pre-empting the caller | **PASSED** |
| 5 | Configuration and emission tests are reproducible | 96/96 pytest reliability/observability tests passing dynamically in 41.07s | **PASSED** |

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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
  },
  {
    "id": "alert-delivery-failure",
    "name": "Alert delivery failure",
    "severity": "P1",
    "metric": "alert_delivery_failure_count",
    "runbook": "docs/runbooks/observability-and-runbook.md#alert-delivery-failure",
    "runbook_file_verified": true,
    "runbook_anchor_verified": true,
    "release_identity_bound": true,
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
    "release_sha": "a7355d5f3e0f5b8f525d440af5c62f95d25c1659"
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
| 1. Normalize at the source | `shared/observability/routes.py` resolves a concrete path to its route template | 469 templates registered; 200 distinct job ids collapse to `['/jobs/{job_id}']`; unrouted paths share `__unmatched__` |
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

## 4c. Gates That Can Actually Fail (R1 / R2 cover)

Two acceptance signals were previously true by construction, so passing them
demonstrated nothing. Both now derive from a probe recorded in this run.

| Gate | Was | Is | Probe in this run |
|---|---|---|---|
| Metric ownership | `MetricDefinition.owner` defaulted to `"sre-platform"`, so every construction site passed while possibly naming a team that had never agreed to carry the signal | `owner` has no plausible default; `__post_init__` rejects omitted or blank. `MetricsRegistry.register` keeps its own check for definitions restored by unpickling, which bypasses `__init__` | omitted owner rejected: `True`; blank owner rejected: `True`; 35 metrics across ['business-analytics', 'data-platform', 'ml-platform', 'security-audit', 'sre-messaging', 'sre-platform'] |
| Alert release identity | `route_alert` emitted the literal `True`, so this document's own check read back its assumption | derived at emission from the trusted deployed identity; a rotated or cleared SHA downgrades the annotation instead of certifying it | flag reports `False` under rotation: `True`; page still routed: `True` |

| Alert delivery | a caller that pages *while* handling its own failure inherited the fail-closed raise: the DLQ poison-isolation branch lost its dead-letter event when no deployed SHA was bound | `try_trigger_alert` contains router construction and delivery, counts the lost page on `alert_delivery_failure_count`, and returns `None` so the caller's error path completes | unbound identity returns `None` instead of raising: `True`; lost page counted: `[{'alert_id': 'dlq-spike', 'error_class': 'ValueError'}]`; bound identity still delivers: `True`; policy paging on it: `alert-delivery-failure` |

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
[
  {
    "span_id": "e42c746d11f64ffe",
    "parent_id": "47456bf878234328",
    "name": "model-solver-evaluate",
    "kind": "model",
    "correlation_id": "02199a37-316d-4058-9e2d-d755ec6905d1",
    "actor_id": "obs-user",
    "status": "ok",
    "error_code": null,
    "duration_ms": 0.002586,
    "attributes": {
      "correlation_id": "02199a37-316d-4058-9e2d-d755ec6905d1",
      "request_id": "req-obs-100",
      "job_id": "job-obs-200",
      "actor_id": "obs-user",
      "entity_type": "solver_run",
      "entity_id": "run-888",
      "model_version": "heatzone_v2:2.0.1"
    }
  },
  {
    "span_id": "d17fcf99d02943e6",
    "parent_id": "47456bf878234328",
    "name": "worker-solver-execute",
    "kind": "worker",
    "correlation_id": "02199a37-316d-4058-9e2d-d755ec6905d1",
    "actor_id": "obs-user",
    "status": "ok",
    "error_code": null,
    "duration_ms": 0.095795,
    "attributes": {
      "correlation_id": "02199a37-316d-4058-9e2d-d755ec6905d1",
      "request_id": "req-obs-100",
      "job_id": "job-obs-200",
      "actor_id": "obs-user",
      "entity_type": "solver_run",
      "entity_id": "run-888",
      "model_version": "heatzone_v2:2.0.1"
    }
  },
  {
    "span_id": "47456bf878234328",
    "parent_id": null,
    "name": "api-solver-submit",
    "kind": "api",
    "correlation_id": "02199a37-316d-4058-9e2d-d755ec6905d1",
    "actor_id": "obs-user",
    "status": "ok",
    "error_code": null,
    "duration_ms": 0.153467,
    "attributes": {
      "correlation_id": "02199a37-316d-4058-9e2d-d755ec6905d1",
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

- Source Commit: `a7355d5f3e0f5b8f525d440af5c62f95d25c1659` (is_test_simulated: False)
- Command: `python3 -m pytest tests/reliability/test_runtime_observability.py`
- Result: **96 passed in 41.07s** (Exit Code: 0)

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
