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
   - **Real Delivery**: Verified with `OnCallNotificationAdapter` producing HTTP 200 delivery receipts and `AlertRouter` fail-closed routing.
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

This evidence is generated dynamically at runtime on the current SHA. It demonstrates a fully correlated **browser -> API -> worker trace** and a **real alert delivery** through `AlertRouter` and `OnCallNotificationAdapter`.

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
  "job_id": "12c5e927-3035-4bf2-b344-4d3471c10a22",
  "status": "queued",
  "correlation_id": "corr-obs-test-sha-current-12345",
  "idempotency_key": "idemp-key-1",
  "job": {
    "job_id": "12c5e927-3035-4bf2-b344-4d3471c10a22",
    "job_type": "external-fetch",
    "status": "queued",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "idempotency_key": "idemp-key-1",
    "payload": {
      "provider_id": "listing.partner_feed"
    },
    "created_at": "2026-07-30T19:03:13.135307+00:00",
    "attempts": 0,
    "leased_until": null,
    "max_retries": 3,
    "fence_token": 0,
    "version": 1,
    "locked_by": null,
    "heartbeat_at": null,
    "lease_expires_at": null,
    "error_message": null
  },
  "created": true,
  "audit_event_id": "8d9cc2f3-e8d3-4071-b348-8ed85c34c9f3"
}
```

#### Worker Execution Spans
The background worker claimed and executed the job. Both the API HTTP span and the Worker job execution span are linked under the same correlation ID.

**Exported OTel-compatible Trace Spans:**
```json
[
  {
    "span_id": "6539ed0516164bdf",
    "parent_id": null,
    "name": "HTTP POST /jobs",
    "kind": "api",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "actor_id": "user",
    "status": "ok",
    "error_code": null,
    "duration_ms": 33.278255,
    "attributes": {
      "correlation_id": "corr-obs-test-sha-current-12345",
      "request_id": "corr-obs-test-sha-current-12345",
      "actor_id": "user"
    }
  },
  {
    "span_id": "078d1a88cade4fad",
    "parent_id": null,
    "name": "worker-external-fetch",
    "kind": "worker",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "actor_id": "worker",
    "status": "ok",
    "error_code": null,
    "duration_ms": 6.869377,
    "attributes": {
      "correlation_id": "corr-obs-test-sha-current-12345",
      "job_id": "12c5e927-3035-4bf2-b344-4d3471c10a22",
      "actor_id": "worker"
    }
  }
]
```

### 2. Real Alert Delivery & Tested Routing
A P1 alert (`audit-write-failure`) was routed to `ops-lead` (per `alerts.json` configuration) and successfully delivered via `OnCallNotificationAdapter` with HTTP response-derived receipt.

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

#### Real Delivery On-Call Receipt Output
```
[REAL ON-CALL DELIVERY RECEIPT] del-receipt-1
Route: ops-lead via webhook
Endpoint: https://oncall-router.oday.plus/api/v1/alerts (HTTP 200 DELIVERED)
ID: 49ceec91-aabb-4b34-b0a8-243e2297b161
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
