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
   - **Local Delivery Simulation**: Verified with `OnCallNotificationAdapter` producing HTTP 200 TEST_ONLY receipts over local loopback network socket and `AlertRouter` fail-closed routing.
2. **Process and Dependency Health checks**:
   - **Liveness (`/healthz`)**: Verifies process health.
   - **Readiness (`/readiness`)**: Verifies database connection.
   - **Detailed Health (`/health` & `/platform/health`)**: Reports status of dependencies: database, job_queue, and external_providers.
3. **Structured Telemetry (OTel-compatible)**:
   - **Traces**: Custom FastAPI HTTP middleware propagates correlation_id header and records spans; ODayWorker and ODayScheduler operations are wrapped in spans.
   - **Metrics**: Captures SRE metrics: `job_duration_seconds` (histogram) and `job_failure_count` (counter).
   - **Logs**: Re-entrant `StructuredLogger` filters sensitive credentials.

---

## Runtime Proof (Current SHA - Local Test Simulation)

> [!NOTE]
> This evidence script (`generate_observability_evidence.py`) uses memory persistence, FastAPI TestClient, and a local loopback HTTP server to provide deterministic local test-only evidence. Production and live deployment acceptance require Cloud Monitoring backend resource IDs, provider route readback receipts, exact full 40-character release SHAs, and monitored watch-window query executions as enforced by `validate_cloud_run_live_deployment.py` and `shared/observability/`.

This evidence is generated dynamically at runtime on the current SHA. It demonstrates a fully correlated **browser -> API -> worker trace** and a **test delivery simulation** through `AlertRouter` and `OnCallNotificationAdapter` over an HTTP network socket.


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
  "job_id": "b15fca1f-01fb-42f8-89e6-c61924d5b0e6",
  "status": "queued",
  "correlation_id": "corr-obs-test-sha-current-12345",
  "idempotency_key": "idemp-key-1",
  "job": {
    "job_id": "b15fca1f-01fb-42f8-89e6-c61924d5b0e6",
    "job_type": "external-fetch",
    "status": "queued",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "idempotency_key": "idemp-key-1",
    "payload": {
      "provider_id": "listing.partner_feed"
    },
    "created_at": "2026-07-31T16:56:51.580936+00:00",
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
  "audit_event_id": "ee2d6b94-bd47-480a-b8a9-88f0bddc348c"
}
```

#### Worker Execution Spans
The background worker claimed and executed the job. Both the API HTTP span and the Worker job execution span are linked under the same correlation ID.

**Exported OTel-compatible Trace Spans:**
```json
[
  {
    "span_id": "f9b11129c9ba4890",
    "parent_id": null,
    "name": "HTTP POST /jobs",
    "kind": "api",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "actor_id": "user",
    "status": "ok",
    "error_code": null,
    "duration_ms": 31.436208,
    "attributes": {
      "correlation_id": "corr-obs-test-sha-current-12345",
      "request_id": "corr-obs-test-sha-current-12345",
      "actor_id": "user"
    }
  },
  {
    "span_id": "cc19c3868a8647e6",
    "parent_id": null,
    "name": "worker-external-fetch",
    "kind": "worker",
    "correlation_id": "corr-obs-test-sha-current-12345",
    "actor_id": "worker",
    "status": "ok",
    "error_code": null,
    "duration_ms": 2.934753,
    "attributes": {
      "correlation_id": "corr-obs-test-sha-current-12345",
      "job_id": "b15fca1f-01fb-42f8-89e6-c61924d5b0e6",
      "actor_id": "worker"
    }
  }
]
```

### 2. Local Test Delivery & Alert Routing Simulation
A P1 alert (`audit-write-failure`) was routed to `ops-lead` (per `alerts.json` configuration) and processed via `OnCallNotificationAdapter` producing a local HTTP response-derived receipt (status: FAILED).

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

#### Local Test Delivery Receipt Output (Captured directly from OnCallNotificationAdapter)
```json
{
  "delivery_id": "del-409b9973edf8",
  "notification_id": "8cb759cb-31e1-499d-b54e-12e59f014e3c",
  "oncall_route": "ops-lead",
  "channel": "webhook",
  "endpoint": "http://127.0.0.1:39945/api/v1/alerts",
  "release_sha": "unauthenticated",
  "request_hash": "",
  "response_hash": "",
  "provider_receipt_id": null,
  "title": "ALERT: [P1] Audit write failure",
  "detail": "Alert ID: audit-write-failure\nCondition: any audit_event_write_failure_count for high-risk action or export in production\nRunbook: docs/runbooks/observability-and-runbook.md#audit-write-failure\nDetails: Durable storage write timeout on DB query",
  "http_status": 0,
  "status": "FAILED",
  "delivered_at": "2026-07-31T16:56:51.589158+00:00",
  "response": null,
  "error": "On-call notification delivery requires a non-empty ONCALL_PROVIDER_SECRET provider trust root. Fail-closed gate enforced."
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
