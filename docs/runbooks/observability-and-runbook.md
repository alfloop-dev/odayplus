# Observability & Operations Runbook

Source baseline: `ODP-OPS-04_RUNBOOK`, `ODP-SD-11_OBSERVABILITY_AND_AUDIT_DESIGN`.
Owner: SRE / Engineering Lead / Support Lead.

This runbook tells on-call how to read the platform's signals and respond to the
alerts defined in `infra/monitoring/alerts.json`. Every alert links to a section
here by anchor.

## How to read the signals

| Signal | Where | Carries |
|---|---|---|
| Structured logs | Cloud Logging | `timestamp, service, actor, correlation_id, resource, action, result, error_code` (`shared.observability.logging`) |
| Metrics | Cloud Monitoring | catalog in `shared.observability.metrics.PLATFORM_METRICS`, dashboards in `infra/monitoring/dashboards.json` |
| Traces | OpenTelemetry | span chain API → Event → Worker → Data → Model → Decision → Report under one `correlation_id` (`shared.observability.tracing`) |

Always start from the `correlation_id`: it joins the log line, the metric label,
the trace, and the audit event for a single request/job/workflow.

## Generic incident triage

1. Confirm the alert source, environment, and service/job/module.
2. Pull the `correlation_id` and follow it across logs → trace → audit.
3. Check recent deploy / migration / model release.
4. Check error rate / latency / queue depth on the relevant dashboard.
5. Decide whether a high-risk decision is affected (escalate if so).
6. Open an incident record (see `incident-management.md`).

Escalate to **P0/P1** immediately for: audit write failure, permission error,
high-risk approval error, production data corruption, wrong model version used,
PriceOps hard-constraint bypass, NetPlan hard-constraint violation, PII exposure,
or RPO/RTO risk.

## API anomaly

Symptoms: 5xx rate up, P95 latency over SLO, health check fail, endpoint timeout.

Checks: Cloud Run revision health, recent deploy, error logs, Cloud SQL
connection, Pub/Sub publish errors, auth provider, downstream model endpoint.

Mitigation: rollback the bad revision; enable degraded/previous-model mode if a
downstream model is down; scale DB / reduce concurrency on connection
exhaustion; disable the feature flag for a single bad endpoint; rate-limit or add
a Cloud Armor rule under load.

Recovery: 5xx back to normal, P95 back within SLO, smoke test passes, audit write
healthy.

## Job failure

Applies to ingest, geocode, heatzone, sitescore, forecast, pricing, adlift, avm,
netplan, model-training, data-quality and audit-export jobs.

Checks: `job_id`, `job_type`, status, inputs, error code, retry count, worker
logs, upstream data status, idempotency key. Watch `dlq_message_count` and
`job_failure_count`.

Handling: retry retryable failures; fix data/config for non-retryable; download
failed records for PARTIAL; inspect the poison message for DLQ. **Before rerun**
confirm the job is idempotent, will not overwrite a decided result, has a retry
reason, has approval if high-risk, and that the DLQ message was analysed.

## Data freshness

Symptoms: data status STALE, data quality alert, model scoring blocked, external
connector failed. Watch `data_freshness_hours`, `data_quality_score`,
`external_connector_failure_count`.

Handling: notify data owner if upstream is missing; retry / fix credentials on
connector failure; adjust schedule on rate limit; contract review on schema
change; quarantine and block downstream on a data-quality fail.

Recovery: rerun ingestion → rerun data quality → rebuild model-ready view →
release the model block → send recovery notice.

## ForecastOps

Symptoms: daily forecast missing, mass red lights, empty store charts, alert not
created. Checks: timeseries view, `forecast-batch-score` job, model version, data
freshness, alert policy version.

Handling: degrade to previous-day data (mark stale) on upstream delay; fall back
to baseline on model failure; rollback the alert policy version on a bad policy;
pause automatic task creation but keep the alert on a false-alert flood.

## PriceOps

**Immediate P1**: hard-constraint violation, unapproved price executed, rollback
failure, price-sync error. Watch `price_hard_constraint_violation_count`.

Handling: turn off the PriceOps execution feature flag → stop pending executions
→ verify plan approval → roll back to previous price → create an audit incident →
notify the pricing owner → start a postmortem.

## NetPlan

Symptoms: solver timeout, infeasible, no result, hard-constraint violation,
missing alternative.

Handling: add resources / shrink scenario / partition on timeout; run
infeasibility diagnosis on infeasible (do **not** auto-relax hard constraints);
block the plan on a constraint violation; rerun alternative generation; ask the
OR owner to review suspicious results.

## Model release

Critical: production alias points wrong, rollback fails, model card missing,
drift monitor fails, model published despite a data-quality fail. Watch
`drift_score`, `model_alias_change_count`. Use the model release/rollback manual
(ODP-OPS-06) for the detailed alias switch.

## Audit write failure

Audit write failure affects high-risk decisions and is always **P0/P1**.

Handling: pause high-risk-operation feature flags → check the audit store →
identify operations whose audit was not written from API logs → replay audit
events → mark replayed entries `recovered_audit=true` → run an audit completeness
check before resuming high-risk operations.

## Acceptance

- Covers API, frontend, job, data, model, solver and audit paths.
- High-risk operations have an explicit mitigation.
- Audit failure and PriceOps hard-constraint failure are treated as high severity.
- Every alert in `infra/monitoring/alerts.json` resolves to a section here.
