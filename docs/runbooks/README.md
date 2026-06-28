# Operations Runbooks

Operational runbooks for ODay Plus production readiness (ODP-R7-001).

Source baseline: `ODP-OPS-04_RUNBOOK`, `ODP-OPS-05_INCIDENT_BACKUP_AND_RECOVERY_MANUAL`,
`ODP-SD-10_EXCEPTION_HANDLING_AND_RELIABILITY_DESIGN`,
`ODP-SD-11_OBSERVABILITY_AND_AUDIT_DESIGN`.

| Runbook | Use when |
|---|---|
| [observability-and-runbook.md](observability-and-runbook.md) | Reading logs/metrics/traces and responding to a `infra/monitoring/alerts.json` alert. |
| [incident-management.md](incident-management.md) | Declaring, running, and closing an incident; postmortems. |
| [backup-and-restore.md](backup-and-restore.md) | Backup scope and restoring Cloud SQL / BigQuery / Storage / model artifacts. |
| [disaster-recovery-drill.md](disaster-recovery-drill.md) | Running a DR drill and measuring RPO/RTO. |

Related machine-readable config lives in `infra/monitoring/` (dashboards,
alerts, SLO/RPO/RTO) and the runtime primitives in `shared/observability/`.
