# Disaster Recovery Drill Runbook

Source baseline: `ODP-OPS-05_INCIDENT_BACKUP_AND_RECOVERY_MANUAL` §11, §15, §16,
`ODP-SD-10_EXCEPTION_HANDLING_AND_RELIABILITY_DESIGN` §10.
Owner: SRE / Security Owner / Project Manager.

A DR drill rehearses recovery from a major outage and **measures** the achieved
RPO and RTO against the targets in `infra/monitoring/slo.json`.

## 1. Cadence

- Full DR drill: every 6 months.
- Restore drill: every quarter (see `backup-and-restore.md` §4).
- Targeted drill: after every major architecture change.

## 2. Drill checklist

- [ ] Set the drill objective.
- [ ] Select a scenario (below).
- [ ] Notify participants.
- [ ] Build test data.
- [ ] Execute failover / restore.
- [ ] Measure RPO (data loss window vs target).
- [ ] Measure RTO (time to recover vs target).
- [ ] Run smoke test.
- [ ] Produce a DR report.
- [ ] Create improvement items.

## 3. DR scenarios

### Scenario A — Cloud SQL unavailable

1. Declare P0/P1.
2. Stop high-risk writes.
3. Check Cloud SQL status.
4. Wait and monitor if transient; restore to standby if prolonged.
5. Switch the API DB connection.
6. Smoke test, then resume service.

### Scenario B — BigQuery data contamination

1. Stop affected scoring jobs.
2. Mark data quality failed.
3. Find the contamination start time.
4. Restore / rebuild affected tables.
5. Rerun dbt tests; recompute affected outputs.
6. Do **not** overwrite decided history — produce a diff.

### Scenario C — Audit log write failure

1. Immediately pause all high-risk decisions.
2. Retain API request logs.
3. Repair the audit store.
4. Replay audit events; mark `recovered_audit=true`.
5. Run a completeness check, then resume high-risk operations.

### Scenario D — Model serving outage

1. Switch to the previous champion or baseline mode.
2. Pause high-risk automatic recommendations.
3. Rerun a smoke prediction.
4. Notify the AI owner; schedule a postmortem.

### Scenario E — Pub/Sub backlog

1. Check queue depth and consumer errors.
2. Quarantine poison messages.
3. Scale workers; analyse the DLQ.
4. Confirm idempotency, then resume processing.

## 4. RPO / RTO measurement

For each scenario record:

```yaml
scenario:
started_at:
data_last_consistent_at:     # → measured RPO = restore_point - data_last_consistent_at
service_restored_at:         # → measured RTO = service_restored_at - started_at
target_rpo:                  # from infra/monitoring/slo.json
target_rto:
within_target:               # pass/fail
```

## 5. DR report & follow-up

Every drill produces a report containing: objective, scenario, timeline,
measured RPO/RTO vs target, smoke-test result, gaps found, and improvement items
with owners and due dates. Improvement items are tracked to completion. If the
drill supports subsidy audit, retain the DR report as evidence.

## Acceptance

- Drill cadence, scenarios, and checklist are defined.
- Audit failure, data contamination, and model outage each have a DR procedure.
- RPO/RTO are measured against `infra/monitoring/slo.json` and recorded.
