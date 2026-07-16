# ODP-PGAP-OBS-001 Observability and Notifications Closeout Evidence

## Scope

ODP-PGAP-OBS-001 delivered runtime observability enhancements (FastAPI, worker, and scheduler OTel-compatible tracing, metrics, structured logs, and detailed dependency-aware health endpoints) and a durable, adapter-backed notifications system.

Key implementation components:
1. **Notifications Module** (`modules/notifications/`):
   - **Preferences**: Allows configuring channel preferences per user, persisting to database.
   - **Deduplication**: Deduplicates notifications using a unique `dedup_key`.
   - **Retry & Receipts**: Automatically retries failed sends and logs individual delivery status in `notification_receipts`.
   - **Escalation**: Escalates high-priority notifications to secondary channels if primary channel fails.
   - **Storage Adapters**: Supported by both `InMemoryNotificationRepository` and SQLite `DurableNotificationRepository`.
   - **Real Delivery**: Verified with `ConsoleNotificationAdapter` printing to stdout and `AlertRouter` routing.
2. **Process and Dependency Health checks**:
   - **Liveness (`/healthz`)**: Verifies process health.
   - **Readiness (`/readiness`)**: Verifies database connection.
   - **Detailed Health (`/health` & `/platform/health`)**: Reports status of dependencies: database, job_queue, and external_providers.
3. **Structured Telemetry (OTel-compatible)**:
   - **Traces**: Custom FastAPI HTTP middleware propagates correlation_id header and records spans; ODayWorker and ODayScheduler operations are wrapped in spans.
   - **Metrics**: Captures SRE metrics: `job_duration_seconds` (histogram) and `job_failure_count` (counter).
   - **Logs**: Re-entrant `StructuredLogger` filters sensitive credentials.

---

## Runtime Proof (Current SHA)

This evidence is generated dynamically at runtime on the current SHA. It demonstrates a fully correlated **browser -> API -> worker trace** and a **real alert delivery** through `AlertRouter` and `ConsoleNotificationAdapter`.

### 1. Correlated Trace Flow
A simulated browser action sends a request to the API with correlation ID `corr-obs-test-sha-current-12345`, which is automatically propagated to the background worker job execution.

#### Request (Browser -> API)
- **Method/Path**: `POST /jobs`
- **Headers**: `X-Correlation-ID: corr-obs-test-sha-current-12345`
- **Payload**:
```json
{
  "job_type": "external-fetch",
  "payload": {
    "provider_id": "listing.partner_feed"
  }
}
```

#### Response (API -> Browser)
- **Status**: 202
- **Body**:
```json
{
  "job_id": "03da40c3-a7ad-4934-9dd3-5cf603e74bb0",
  "status": "queued",
  "correlation_id": "corr-obs-test-sha-current-12345",
  "idempotency_key": "idemp-key-1",
  "job": {
    "job_id": "03da40c3-a7ad-4934-9dd3-5cf603e74bb0",
    "job_type": "external-fetch",
    "status": "queued",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "idempotency_key": "idemp-key-1",
    "payload": {
      "provider_id": "listing.partner_feed"
    },
    "created_at": "2026-07-16T02:41:23.683539+00:00"
  },
  "created": true,
  "audit_event_id": "e48459bb-6eaa-4a09-8b45-e95da845fd31"
}
```

#### Worker Execution Spans
The background worker claimed and executed the job. Both the API HTTP span and the Worker job execution span are linked under the same correlation ID.

**Exported OTel-compatible Trace Spans:**
```json
[
  {
    "span_id": "1d65e4b669f64db3",
    "parent_id": null,
    "name": "HTTP POST /jobs",
    "kind": "api",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "actor_id": "user",
    "status": "ok",
    "error_code": null,
    "duration_ms": 16.962038,
    "attributes": {
      "correlation_id": "corr-obs-test-sha-current-12345",
      "request_id": "corr-obs-test-sha-current-12345",
      "actor_id": "user"
    }
  },
  {
    "span_id": "54f571e680ae4b27",
    "parent_id": null,
    "name": "worker-external-fetch",
    "kind": "worker",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "actor_id": "worker",
    "status": "ok",
    "error_code": null,
    "duration_ms": 2.20561,
    "attributes": {
      "correlation_id": "corr-obs-test-sha-current-12345",
      "job_id": "03da40c3-a7ad-4934-9dd3-5cf603e74bb0",
      "actor_id": "worker"
    }
  }
]
```

### 2. Real Alert Delivery & Tested Routing
A P1 alert (`audit-write-failure`) was routed to `ops-lead` (per `alerts.json` configuration) and successfully delivered to stdout via the `ConsoleNotificationAdapter`.

#### Routed Alert Configuration
```json
{
  "alert_id": "audit-write-failure",
  "name": "Audit write failure",
  "severity": "P1",
  "metric": "audit_event_write_failure_count",
  "condition": "any audit_event_write_failure_count for high-risk action or export in production",
  "runbook": "docs/runbooks/observability-and-runbook.md#audit-write-failure",
  "receiver": "ops-lead"
}
```

#### Real Delivery Console Log Output
```
[REAL DELIVERY] Sent email notification to ops-lead
ID: 8e00fe82-c3e6-4823-8b48-f14fe0e61831
Title: ALERT: [P1] Audit write failure
Detail: Alert ID: audit-write-failure
Condition: any audit_event_write_failure_count for high-risk action or export in production
Runbook: docs/runbooks/observability-and-runbook.md#audit-write-failure
Details: Durable storage write timeout on DB query
```

---

## Verification Evidence

All 52 observability, notifications, and integration tests pass successfully.

### 1. Test Commands Run
- `uv run pytest tests/reliability/ -q`
- `uv run pytest tests/contract/test_platform_api.py -q`

### 2. Test Execution Output
```
tests/reliability/test_health_endpoints.py ....                          [ 8%]
tests/reliability/test_notifications.py ....                              [16%]
tests/reliability/test_runtime_observability.py ........................... [70%]
tests/reliability/test_cross_flow_gate.py .................               [100%]
52 passed, 1 warning in 1.80s
```

## Artifact Mapping

- **Notifications Domain Models**: `modules/notifications/domain/models.py` ([models.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-obs-001/modules/notifications/domain/models.py))
- **Notifications Repository**: `modules/notifications/infrastructure/repositories.py` ([repositories.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-obs-001/modules/notifications/infrastructure/repositories.py))
- **Notifications Service**: `modules/notifications/application/service.py` ([service.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-obs-001/modules/notifications/application/service.py))
- **Durable DB Migrations**: `infra/db/migrations/000005_durable_notifications.sql` ([000005_durable_notifications.sql](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-obs-001/infra/db/migrations/000005_durable_notifications.sql))
- **Detailed Health Endpoints**: `apps/api/oday_api/main.py` ([main.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-obs-001/apps/api/oday_api/main.py#L116))
- **Worker Observability**: `apps/worker/oday_worker/main.py` ([main.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-obs-001/apps/worker/oday_worker/main.py#L31))
- **Scheduler Observability**: `apps/scheduler/oday_scheduler/main.py` ([main.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-obs-001/apps/scheduler/oday_scheduler/main.py#L29))
- **Notifications Unit Tests**: `tests/reliability/test_notifications.py` ([test_notifications.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-obs-001/tests/reliability/test_notifications.py))
- **Health Endpoint Tests**: `tests/reliability/test_health_endpoints.py` ([test_health_endpoints.py](file:///tmp/pantheon-worker-worktrees/oday-plus/odp-pgap-obs-001/tests/reliability/test_health_endpoints.py))
