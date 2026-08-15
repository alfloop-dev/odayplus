# infra/monitoring

Machine-readable monitoring baseline for ODay Plus (ODP-R7-001).

Source baseline:
- `ODP-SD-11_OBSERVABILITY_AND_AUDIT_DESIGN` §5 (metrics), §8 (dashboards), §9 (alerts)
- `ODP-SD-10_EXCEPTION_HANDLING_AND_RELIABILITY_DESIGN` §10 (RPO/RTO), §11 (incident levels)
- `ODP-OPS-05_INCIDENT_BACKUP_AND_RECOVERY_MANUAL` §9 (RPO/RTO)

## Files

| File | Purpose |
|---|---|
| `dashboards.json` | Six dashboards (SRE / Data / Model / Business / Audit / External Source) with panels that reference the shared metric catalog, including audit pipeline metrics. |
| `alerts.json` | Alert policies covering P1/P2/P3 scenarios; each names the metric it watches and the runbook that owns the response. Audit write failures are P1 because high-risk operations must not continue without an append-only audit trail. |
| `slo.json` | SLO/SLI objectives plus the RPO/RTO recovery targets that drive the backup and DR runbooks. |

## Contract with code

Every `metric` referenced in `dashboards.json` and `alerts.json` is a name in
`shared.observability.metrics.PLATFORM_METRICS`. The reliability test
`tests/reliability/test_runtime_observability.py` enforces this so a renamed or
deleted metric can never silently orphan a dashboard panel or alert policy.

`shared.observability.audit.AuditPipeline` emits the audit metrics used by the
Audit & Compliance dashboard:

- `audit_event_record_count`
- `audit_event_write_failure_count`
- `audit_event_pipeline_lag_seconds`
- `audit_event_replay_count`
- `audit_evidence_export_count`
- `audit_completeness_gap_count`

These JSON definitions are the source of truth that a Terraform / `gcloud`
exporter renders into Cloud Monitoring dashboards and alert policies. They are
intentionally dependency-free (stdlib `json`) so they validate in CI without a
live GCP project.
