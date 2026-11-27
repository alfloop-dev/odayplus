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
| Audit pipeline | Cloud SQL / BigQuery audit sink | canonical `AuditEvent` writes plus `audit_event_record_count`, `audit_event_write_failure_count`, `audit_evidence_export_count` (`shared.observability.audit`) |

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

Handling: pause high-risk-operation feature flags → check
`audit_event_write_failure_count` and the audit pipeline dead-letter queue →
check the audit store → identify operations whose audit was not written from API
logs → replay audit events → mark replayed entries `recovered_audit=true` → run
an audit completeness check before resuming high-risk operations.

Recovery checks:

1. `audit_event_write_failure_count` is no longer increasing.
2. Dead-letter replay has a matching `audit_event_replay_count{result="success"}`.
3. Required decision timeline events are complete; any missing required event is
   counted by `audit_completeness_gap_count`.
4. Evidence exports are visible as `audit.evidence_export.v1` events and
   `audit_evidence_export_count` samples under the same `correlation_id`.

## Alert delivery failure

`alert_delivery_failure_count` counts pages that were raised but never
delivered, so every sample means an incident somewhere else went unannounced.
Treat it as **P1**: it degrades every other alert on this page.

Callers that page while handling their own failure (the DLQ poison-isolation
branch is the reference case) route through
`shared.observability.try_trigger_alert`, which contains the delivery error and
counts it rather than letting it pre-empt the caller's error handling. So a
non-zero count means the underlying work *was* handled correctly — only the
notification was lost.

Handling: read the `error_class` label to separate the causes — a
`ValueError` naming `release identity` means the deployed `RELEASE_SHA` is
unbound or does not match `release_identity.exact_sha_binding` in
`infra/monitoring/alerts.json`; one naming routing means a severity has no
receiver; anything else is the notification transport. Fix the binding or the
route, then search the same window for the alerts that were suppressed and
triage them by hand, since they will not be re-sent.

Recovery checks:

1. `alert_delivery_failure_count` is no longer increasing.
2. A test page for each affected severity reaches its receiver and carries the
   deployed `release_sha`.
3. Every incident whose page was lost during the outage has been triaged.

## Release-SHA Dashboard Traceability & Watch-Window Receipt

Production watch-window verification reads real built-in Cloud Run metrics from Cloud Monitoring (`run.googleapis.com/request_count` and `run.googleapis.com/request_latencies`), filtered by `resource.type="cloud_run_revision"`, `resource.labels.service_name`, `resource.labels.revision_name`, and `resource.labels.location`.
Release binding does not rely on custom `metric.labels.release_sha` labels or synthetic provider signatures; instead, `shared/observability/watch_window.py` queries the Cloud Run Admin API v2 to confirm that both the production API and Web services route 100% traffic to a single active revision whose container image digest matches the admitted `RELEASE_MANIFEST.json`.
The minimum observation window and SLO thresholds (`watch_window_minutes=30`, 5xx error rate `< 5%`, p95 latency `< 5s`) are loaded directly from `docs/evidence/runtime/ODP-PROD-OPS-WATCH-PARAMS-001/prod-ops-governance-parameters.json`.

### Watch-Window Receipt Procedure

1. **Observe the Governed Watch Window**: After production traffic switchover for `RELEASE_SHA`, monitor production API and Web services for at least the governed `watch_window_minutes` (30 minutes).
2. **Generate the Receipt with Production Read-Only Identity**: Using an operator identity with Cloud Run and Cloud Monitoring read permissions (Application Default Credentials via `gcloud auth application-default login` or `GOOGLE_OAUTH_ACCESS_TOKEN`; no `MONITORING_PROVIDER_SECRET` is required), run:

   ```bash
   python3 -m shared.observability.watch_window record \
     --project "<PROD_GCP_PROJECT>" \
     --region "<GCP_REGION>" \
     --api-service "<PROD_API_SERVICE>" \
     --web-service "<PROD_WEB_SERVICE>" \
     --release-sha "<RELEASE_SHA>" \
     --manifest "<PATH_TO_ADMITTED_RELEASE_MANIFEST_JSON>" \
     --start "<WINDOW_START_ISO8601_UTC>" \
     --end "<WINDOW_END_ISO8601_UTC>" \
     --output ./production-watch-window-receipt.json
   ```

   If the window is shorter than 30 minutes, either service has no data or insufficient points, 5xx ratio `>= 5%`, p95 latency `>= 5s`, or either service's 100%-traffic revision digest does not match the admitted manifest, the command fails closed (`WATCH_FAILED` or refusal, non-zero exit).
3. **Verify Locally Before Upload**:

   ```bash
   python3 -m shared.observability.watch_window verify \
     --release-sha "<RELEASE_SHA>" \
     --receipt ./production-watch-window-receipt.json
   ```

   This recomputes `canonical_receipt_hash` over all receipt fields and raw `timeSeries` and re-evaluates all metrics against `prod-ops-governance-parameters.json`.
4. **Upload Receipt for Staging Closeout**: With an operator/deploy identity authorized to write to the closeout bucket, upload the verified receipt to `ODP_PRODUCTION_WATCH_CLOSEOUT_URI`:

   ```bash
   gcloud storage cp ./production-watch-window-receipt.json "${ODP_PRODUCTION_WATCH_CLOSEOUT_URI}"
   ```

5. **Close Out Ephemeral Staging**: Re-run the `staging_closeout` job (`Verify production watch and clean up ephemeral staging`) in the production `Runtime Release` workflow run. `staging_closeout` reads `ODP_PRODUCTION_WATCH_CLOSEOUT_URI` with read-only staging WIF credentials and validates it via `verify_watch_window_receipt(release_sha, path)` before cleaning up ephemeral staging.

### Trust Boundary

- **Producer**: Operator identity with production read-only access (`roles/run.viewer` + `roles/monitoring.viewer`) generates the receipt from real Cloud Run Admin API + Cloud Monitoring responses.
- **Storage**: `ODP_PRODUCTION_WATCH_CLOSEOUT_URI` resides in a GCS bucket writable only by authorized deploy/operations identities.
- **Consumer**: `staging_closeout` holds read-only (`storage.objects.get`) access via staging WIF identity and enforces `canonical_receipt_hash`, SHA binding, manifest-digest binding, and raw `timeSeries` re-evaluation fail-closed.

## Acceptance

- Covers API, frontend, job, data, model, solver and audit paths.
- High-risk operations have an explicit mitigation.
- Audit failure and PriceOps hard-constraint failure are treated as high severity.
- Every alert in `infra/monitoring/alerts.json` resolves to a section here.
- Production watch-window receipt is generated from real Cloud Run metrics, bound to admitted manifest digests, and verified against exact `release_sha`.
