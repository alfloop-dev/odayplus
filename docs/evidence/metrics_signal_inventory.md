# Production Metrics Signal Inventory & Wiring Specification

This document provides the authoritative inventory mapping every platform metric definition in `PLATFORM_METRICS` (`shared/observability/metrics.py`) to its production writer, shared export path, Cloud Monitoring provider metric identity, domain/unit constraints, and verification tests.

## Signal Inventory Matrix

| Metric Name | Type | Category | Unit | Production Writer / Call Site | Shared Export Path | Provider Metric Identity | Verification Test |
|---|---|---|---|---|---|---|---|
| `api_request_count` | Counter | traffic | - | `apps/api/oday_api/main.py::TelemetryMiddleware` | `ProductionMetricsExporter` | `custom.googleapis.com/api_request_count` | `test_metrics_registry_snapshot_and_exporter` |
| `api_error_count` | Counter | error | - | `apps/api/oday_api/main.py::TelemetryMiddleware` | `ProductionMetricsExporter` | `custom.googleapis.com/api_error_count` | `test_metrics_registry_snapshot_and_exporter` |
| `api_latency_ms` | Histogram | latency | ms | `apps/api/oday_api/main.py::TelemetryMiddleware` | `ProductionMetricsExporter` | `custom.googleapis.com/api_latency_ms` | `test_metrics_registry_snapshot_and_exporter` |
| `db_query_latency_ms` | Histogram | latency | ms | `shared/infrastructure/persistence/` & DB callers | `ProductionMetricsExporter` | `custom.googleapis.com/db_query_latency_ms` | `test_metrics_registry_snapshot_and_exporter` |
| `job_duration_seconds` | Histogram | job | s | `apps/worker/oday_worker/main.py::ODayWorker` | `ProductionMetricsExporter` / `ODayWorker.export_metrics` | `custom.googleapis.com/job_duration_seconds` | `test_telemetry_facade_operation_context` |
| `job_failure_count` | Counter | job | - | `apps/worker/oday_worker/main.py::ODayWorker` | `ProductionMetricsExporter` / `ODayWorker.export_metrics` | `custom.googleapis.com/job_failure_count` | `test_telemetry_facade_operation_context` |
| `event_consumer_lag` | Gauge | queue | - | Event / queue workers & handlers | `ProductionMetricsExporter` / `ODayWorker.export_metrics` | `custom.googleapis.com/event_consumer_lag` | `test_concurrency_recovery` |
| `dlq_message_count` | Gauge | queue | - | `apps/worker/assisted_listing_intake/worker.py` | `ProductionMetricsExporter` / `ODayWorker.export_metrics` | `custom.googleapis.com/dlq_message_count` | `test_assisted_listing_intake_jobs` |
| `external_connector_failure_count` | Counter | error | - | `modules/external_data/providers/live.py` | `ProductionMetricsExporter` | `custom.googleapis.com/external_connector_failure_count` | `test_assisted_listing_intake_jobs` |
| `data_freshness_hours` | Gauge | data | h | `modules/external_data/application/ingestion_service.py` | `ProductionMetricsExporter` | `custom.googleapis.com/data_freshness_hours` | `test_production_metric_recorders_all_categories` |
| `data_quality_score` | Gauge | data | - | `modules/external_data/application/ingestion_service.py` | `ProductionMetricsExporter` | `custom.googleapis.com/data_quality_score` | `test_production_metric_recorders_all_categories` |
| `feature_null_rate` | Gauge | data | - | `modules/external_data/application/ingestion_service.py` | `ProductionMetricsExporter` | `custom.googleapis.com/feature_null_rate` | `test_production_metric_recorders_all_categories` |
| `prediction_count` | Counter | model | - | `apps/api/oday_api/routes/heatzone.py` | `ProductionMetricsExporter` | `custom.googleapis.com/prediction_count` | `test_production_metric_recorders_all_categories` |
| `model_error_metric` | Gauge | model | - | Model evaluation pipeline call sites | `ProductionMetricsExporter` | `custom.googleapis.com/model_error_metric` | `test_production_metric_recorders_all_categories` |
| `prediction_interval_coverage` | Gauge | model | - | Model evaluation pipeline call sites | `ProductionMetricsExporter` | `custom.googleapis.com/prediction_interval_coverage` | `test_production_metric_recorders_all_categories` |
| `drift_score` | Gauge | model | - | Model monitoring pipeline call sites | `ProductionMetricsExporter` | `custom.googleapis.com/drift_score` | `test_production_metric_recorders_all_categories` |
| `model_alias_change_count` | Counter | model | - | Model registry release call sites | `ProductionMetricsExporter` | `custom.googleapis.com/model_alias_change_count` | `test_production_metric_recorders_all_categories` |
| `heatzone_topk_adoption_rate` | Gauge | business | - | `apps/api/oday_api/routes/heatzone.py` | `ProductionMetricsExporter` | `custom.googleapis.com/heatzone_topk_adoption_rate` | `test_production_metric_recorders_all_categories` |
| `listing_dedup_accuracy` | Gauge | business | - | Deduplication service call sites | `ProductionMetricsExporter` | `custom.googleapis.com/listing_dedup_accuracy` | `test_production_metric_recorders_all_categories` |
| `sitescore_realization_rate` | Gauge | business | - | SiteScore outcome evaluation call sites | `ProductionMetricsExporter` | `custom.googleapis.com/sitescore_realization_rate` | `test_production_metric_recorders_all_categories` |
| `forecast_alert_precision` | Gauge | business | - | Forecast alert precision evaluation call sites | `ProductionMetricsExporter` | `custom.googleapis.com/forecast_alert_precision` | `test_production_metric_recorders_all_categories` |
| `intervention_recovery_rate` | Gauge | business | - | Intervention workflow call sites | `ProductionMetricsExporter` | `custom.googleapis.com/intervention_recovery_rate` | `test_production_metric_recorders_all_categories` |
| `price_hard_constraint_violation_count` | Counter | business | - | PriceOps hard-constraint solver call sites | `ProductionMetricsExporter` | `custom.googleapis.com/price_hard_constraint_violation_count` | `test_production_metric_recorders_all_categories` |
| `adlift_incremental_gm` | Gauge | business | - | AdLift outcome evaluation call sites | `ProductionMetricsExporter` | `custom.googleapis.com/adlift_incremental_gm` | `test_production_metric_recorders_all_categories` |
| `avm_interval_coverage` | Gauge | business | - | AVM evaluation call sites | `ProductionMetricsExporter` | `custom.googleapis.com/avm_interval_coverage` | `test_production_metric_recorders_all_categories` |
| `netplan_plan_adoption_rate` | Gauge | business | - | NetPlan acceptance workflow call sites | `ProductionMetricsExporter` | `custom.googleapis.com/netplan_plan_adoption_rate` | `test_production_metric_recorders_all_categories` |
| `model_adoption_rate` | Gauge | business | - | Model override workflow call sites | `ProductionMetricsExporter` | `custom.googleapis.com/model_adoption_rate` | `test_production_metric_recorders_all_categories` |
| `audit_event_record_count` | Counter | audit | - | `shared/observability/audit.py::AuditPipeline` | `ProductionMetricsExporter` | `custom.googleapis.com/audit_event_record_count` | `test_audit_pipeline_metrics` |
| `audit_event_write_failure_count` | Counter | error | - | `shared/observability/audit.py::AuditPipeline` | `ProductionMetricsExporter` | `custom.googleapis.com/audit_event_write_failure_count` | `test_audit_pipeline_metrics` |
| `audit_event_pipeline_lag_seconds` | Histogram | audit | s | `shared/observability/audit.py::AuditPipeline` | `ProductionMetricsExporter` | `custom.googleapis.com/audit_event_pipeline_lag_seconds` | `test_audit_pipeline_metrics` |
| `audit_event_replay_count` | Counter | audit | - | `shared/observability/audit.py::AuditPipeline` | `ProductionMetricsExporter` | `custom.googleapis.com/audit_event_replay_count` | `test_audit_pipeline_metrics` |
| `audit_evidence_export_count` | Counter | audit | - | `shared/observability/audit.py::AuditPipeline` | `ProductionMetricsExporter` | `custom.googleapis.com/audit_evidence_export_count` | `test_audit_pipeline_metrics` |
| `audit_completeness_gap_count` | Counter | audit | - | `shared/observability/audit.py::AuditPipeline` | `ProductionMetricsExporter` | `custom.googleapis.com/audit_completeness_gap_count` | `test_audit_pipeline_metrics` |
| `deployment_watch_window_status` | Gauge | job | - | `shared/observability/watch_window.py` | `ProductionMetricsExporter` / Watch verifier | `custom.googleapis.com/deployment_watch_window_status` | `test_watch_window_receipt_verification` |

## Domain & Unit Validation Constraints

1. **Non-Finite Value Protection**: Any metric value passed to `increment()`, `set()`, `observe()`, `export_metrics()`, or `verify_watch_window_receipt()` that evaluates to `NaN`, `Inf`, or `-Inf` is immediately rejected with a fail-closed exception.
2. **Counter Non-Negativity**: Counters (`api_request_count`, `api_error_count`, `job_failure_count`, `external_connector_failure_count`, `prediction_count`, `model_alias_change_count`, `price_hard_constraint_violation_count`, `audit_event_record_count`, `audit_event_write_failure_count`, `audit_event_replay_count`, `audit_evidence_export_count`, `audit_completeness_gap_count`) must have strictly positive increments (> 0) and non-negative accumulated values (>= 0).
3. **Latency & Duration Non-Negativity**: Latency and duration metrics (`api_latency_ms`, `db_query_latency_ms`, `job_duration_seconds`, `audit_event_pipeline_lag_seconds`, `data_freshness_hours`) require non-negative values (>= 0).
4. **Canonical Receipt Binding**: The export receipt ID (`export_receipt_id`) and watch receipt hash (`canonical_receipt_hash`) canonically compute a SHA-256 digest over GCP project, release SHA, provider route identity, metric names, categories, units, labels, point values, point timestamps, and provider response hashes. Any value, timestamp, or label mutation alters the receipt hash deterministically.
