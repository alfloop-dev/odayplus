# Backup & Restore Runbook

Source baseline: `ODP-OPS-05_INCIDENT_BACKUP_AND_RECOVERY_MANUAL` §8–§12,
`ODP-SD-10_EXCEPTION_HANDLING_AND_RELIABILITY_DESIGN` §10.
Owner: SRE / Security Owner.

This runbook defines what is backed up, how to restore each resource, and how
backups are verified. RPO/RTO targets are the machine-readable source in
`infra/monitoring/slo.json` (`recovery_objectives`).

## 1. Backup scope

| Resource | Method | Frequency |
|---|---|---|
| Cloud SQL / PostGIS | automated backup + PITR | daily + PITR |
| BigQuery canonical | table snapshots / exports | daily |
| BigQuery model_ready | reproducible via dbt + snapshots | daily / on release |
| Cloud Storage raw | object versioning | continuous |
| Cloud Storage reports | versioning + lifecycle | continuous |
| Model artifacts | immutable storage | on model release |
| MLflow metadata | DB backup | daily |
| Audit logs | append-only + export | continuous / daily |
| Terraform state | remote backend with versioning | continuous |
| Secret Manager | versioning | on change |
| Git repository | remote + tags | every commit |

### Backup naming

```
backup-{resource}-{env}-{yyyymmddhhmm}-{version}
```

### Backup metadata

```yaml
backup_id:
resource:
environment:
created_at:
created_by:
source_version:
retention_until:
encryption:
location:
validation_status:
restore_tested_at:
```

## 2. RPO / RTO

Targets are in `infra/monitoring/slo.json`. Summary:

| System | RPO | RTO |
|---|---|---|
| Core API / OpsBoard | 1h | 4h |
| Cloud SQL | 1h | 4h |
| BigQuery canonical | 24h | 8h |
| Audit logs | ~0 (append-only) | 4h |
| Model artifacts | 0 (immutable) | 2h |
| Reports | 24h | 8h |
| Feature marts | rebuildable | 8h |

## 3. Restore procedures

### 3.1 Cloud SQL restore

1. Declare maintenance / incident.
2. Freeze writes.
3. Confirm the restore target time.
4. Create the restored instance.
5. Run integrity check.
6. Switch the connection string.
7. Run smoke test.
8. Resume service.
9. Record the restore report.

**Validation:** row counts, critical tables exist, foreign-key consistency,
audit table continuity, latest decision visible, API smoke pass.

### 3.2 BigQuery restore

1. Identify affected dataset/table.
2. Locate snapshot / export.
3. Restore to a temporary table.
4. Compare row count / hash.
5. Repoint the view.
6. Rerun dbt tests.
7. Rerun affected model-ready views.
8. Record restore evidence.

### 3.3 Cloud Storage restore

Applies to reports, model artifacts, raw files, evidence packages.

1. Find the object version.
2. Restore the object version.
3. Verify hash.
4. Verify permissions.
5. Update metadata.
6. Notify the owner.

### 3.4 Model artifact restore

1. Find the previous production model in the registry.
2. Confirm artifact URI and model card.
3. Switch the serving alias.
4. Run a prediction smoke test.
5. Confirm the Decision Log shows the restored `model_version`.
6. Notify the AI owner. (Detail: ODP-OPS-06.)

## 4. Backup verification

**Daily automated checks:** backup job success, backup object existence, backup
encryption, backup size anomaly, audit export success, model artifact integrity.

**Quarterly drills** (see `disaster-recovery-drill.md`): Cloud SQL restore,
BigQuery snapshot restore, Cloud Storage object restore, model artifact restore,
audit evidence restore.

## Acceptance

- Backup scope and restore procedure are explicit for every critical resource.
- RPO/RTO are verifiable against `infra/monitoring/slo.json`.
- Each restore procedure has an explicit validation step.
