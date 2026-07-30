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
   - **Real Delivery**: Verified with `OnCallNotificationAdapter` producing HTTP 200 delivery receipts over real loopback network socket and `AlertRouter` fail-closed routing.
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

This evidence is generated dynamically at runtime on the current SHA. It demonstrates a fully correlated **browser -> API -> worker trace** and a **real alert delivery** through `AlertRouter` and `OnCallNotificationAdapter` over an actual HTTP network socket.

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
  "job_id": "1a8b1172-309b-4f95-a8ee-330a2e707733",
  "status": "queued",
  "correlation_id": "corr-obs-test-sha-current-12345",
  "idempotency_key": "idemp-key-1",
  "job": {
    "job_id": "1a8b1172-309b-4f95-a8ee-330a2e707733",
    "job_type": "external-fetch",
    "status": "queued",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "idempotency_key": "idemp-key-1",
    "payload": {
      "provider_id": "listing.partner_feed"
    },
    "created_at": "2026-07-30T19:45:17.437869+00:00",
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
  "audit_event_id": "78be437b-f974-4718-a846-c137082333da"
}
```

#### Worker Execution Spans
The background worker claimed and executed the job. Both the API HTTP span and the Worker job execution span are linked under the same correlation ID.

**Exported OTel-compatible Trace Spans:**
```json
[
  {
    "span_id": "295744fb93844962",
    "parent_id": null,
    "name": "HTTP POST /jobs",
    "kind": "api",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "actor_id": "user",
    "status": "ok",
    "error_code": null,
    "duration_ms": 22.688169,
    "attributes": {
      "correlation_id": "corr-obs-test-sha-current-12345",
      "request_id": "corr-obs-test-sha-current-12345",
      "actor_id": "user"
    }
  },
  {
    "span_id": "6f6f9b4913e94524",
    "parent_id": null,
    "name": "worker-external-fetch",
    "kind": "worker",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "actor_id": "worker",
    "status": "ok",
    "error_code": null,
    "duration_ms": 2.930498,
    "attributes": {
      "correlation_id": "corr-obs-test-sha-current-12345",
      "job_id": "1a8b1172-309b-4f95-a8ee-330a2e707733",
      "actor_id": "worker"
    }
  }
]
```

### 2. Real Alert Delivery & Tested Routing
A P1 alert (`audit-write-failure`) was routed to `ops-lead` (per `alerts.json` configuration) and successfully delivered via `OnCallNotificationAdapter` with real HTTP response-derived receipt.

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

#### Real Delivery On-Call Receipt Output (Captured directly from OnCallNotificationAdapter)
```json
{
  "delivery_id": "del-7e6535904283",
  "notification_id": "1b1ed265-532a-4519-87a9-ac28a060bc19",
  "oncall_route": "ops-lead",
  "channel": "webhook",
  "endpoint": "http://127.0.0.1:33569/api/v1/alerts",
  "title": "ALERT: [P1] Audit write failure",
  "detail": "Alert ID: audit-write-failure\nCondition: any audit_event_write_failure_count for high-risk action or export in production\nRunbook: docs/runbooks/observability-and-runbook.md#audit-write-failure\nDetails: Durable storage write timeout on DB query",
  "http_status": 200,
  "status": "DELIVERED",
  "delivered_at": "2026-07-30T19:45:17.447089+00:00",
  "response": {
    "status": "delivered",
    "route": "ops-lead",
    "delivery_id": "del-7e6535904283",
    "received_at": "2026-07-30T19:45:17.485568+00:00"
  },
  "error": null
}
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

- **Notifications Domain Models**: `modules/notifications/domain/models.py`
- **Notifications Repository**: `modules/notifications/infrastructure/repositories.py`
- **Notifications Service**: `modules/notifications/application/service.py`
- **Durable DB Migrations**: `infra/db/migrations/000005_durable_notifications.sql`
- **Detailed Health Endpoints**: `apps/api/oday_api/main.py`
- **Worker Observability**: `apps/worker/oday_worker/main.py`
- **Scheduler Observability**: `apps/scheduler/oday_scheduler/main.py`
- **Notifications Unit Tests**: `tests/reliability/test_notifications.py`
- **Health Endpoint Tests**: `tests/reliability/test_health_endpoints.py`
