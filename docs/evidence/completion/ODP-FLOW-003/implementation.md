# ODP-FLOW-003 · Complete ForecastOps alert and handoff flow — Implementation

- Task: ODP-FLOW-003 (Product Flow Implementation phase)
- Owner: Claude · Reviewer: Claude2
- Source design: `docs_archive/05_module_design/ODP-MOD-04_FORECASTOPS.md`
  (four-light policy, alert lifecycle, intervention handoff)

## Scope

The timeseries snapshot → versioned forecast + P10/P50/P90 uncertainty →
four-light alert → intervention-handoff **proposal** path already shipped in
`modules/forecastops` (ODP-R3-001). ODP-FLOW-003 closes the two remaining steps
named in the task summary so the loop is demonstrably closed rather than ending
at a proposed handoff:

1. **Alert acknowledgement** — a persisted, once-only human action that moves an
   alert `open → acknowledged` with actor / time / note.
2. **Executable intervention handoff** — dispatching a `proposed` handoff records
   the actor, time, and the linked InterventionOps case (`proposed → dispatched`).

It also binds the operations workspace (Overview + Alert center) to the live
`GET /forecastops/alerts` API.

## What changed

### Domain (`modules/forecastops/domain/forecasting.py`)
- Added `ForecastOpsError(ValueError)` and `ForecastOpsNotFoundError` for invalid
  transitions and missing ids (the API maps them to 422 / 404).
- `Alert` gained `acknowledged_by` / `acknowledged_at` / `acknowledgement_note`
  and an immutable `acknowledge(actor, note, now)` that rejects an empty actor,
  a double-acknowledge, and acknowledging a `closed` alert. Serialized in
  `to_dict()`.
- `InterventionHandoff` gained `executed_by` / `executed_at` / `intervention_id`
  and an immutable `execute(actor, intervention_id, now)` that rejects an empty
  actor and a double-dispatch (`proposed → dispatched`). Serialized in `to_dict()`.

### Infrastructure (`modules/forecastops/infrastructure/repositories.py`)
- Added `get_alert(alert_id)` and `get_handoff(handoff_id)`; `save_alert` /
  `save_handoff` already upsert by id, so the acknowledged / dispatched copy
  persists in place.

### Application (`modules/forecastops/application/forecasting.py`)
- `ForecastOpsService.acknowledge_alert(alert_id, *, actor, note, now)` and
  `execute_handoff(handoff_id, *, actor, intervention_id, now)` load, validate,
  transition, and persist. Missing ids raise `ForecastOpsNotFoundError`.

### API (`apps/api/app/routes/forecastops.py`)
- `POST /forecastops/alerts/{alert_id}/acknowledge` (RBAC `forecastops:create`)
  and `POST /forecastops/intervention-handoffs/{handoff_id}/execute` (RBAC
  `forecastops:execute`) — both actions are held by `OPERATIONS_MANAGER`, so no
  RBAC grant change was needed. Each emits an audit event
  (`forecastops.alert.acknowledged.v1` / `forecastops.handoff.executed.v1`)
  under the request correlation id and returns the updated resource with the
  `audit_event_id`. `ForecastOpsNotFoundError → 404`, `ForecastOpsError → 422`.

### Client (`packages/openapi-client/src/index.ts`)
- Added the `ForecastAlert` type and `listForecastAlerts({ level })` reader used
  by the server-rendered operations routes.

### Web (`apps/web/features/operations/OperationsWorkspace.tsx`, `apps/web/src/app/operations/page.tsx`, `apps/web/src/app/w/operations/alerts/page.tsx`)
- Both routes are now `force-dynamic` and build an `ApiBinding<ForecastAlert>`
  from `GET /forecastops/alerts` via `getServerApiClient()` + `loadApiBinding`.
- A new `LiveAlertQueue` region renders the live four-light queue (including the
  persisted `acknowledged` status + `acknowledged_by`) with a `DataSourceBadge`
  (`data-testid="ops-alert-data-source"`). The existing fixture queue remains as
  a documented non-product fallback for cold-store / unconfigured / error states
  — matching the established AVM / Audit / Intervention API-binding pattern.

### Tests
- `tests/integration/test_forecastops_alerts.py`:
  `test_acknowledge_alert_persists_and_rejects_double_ack`,
  `test_execute_handoff_links_intervention_and_rejects_reexecute`,
  `test_api_acknowledge_alert_and_execute_handoff_with_audit`.
- `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`: drives the
  acknowledge + execute-handoff steps (linking the opened intervention), asserts
  the two new audit event types + the `acknowledge` action, and asserts the live
  `ops-live-alerts` region renders `data-source="api"` with the acknowledged row.

## Acceptance mapping

| Acceptance criterion | Where satisfied |
| --- | --- |
| versioned forecast and uncertainty persist | `save_forecast` versioning + w4/w8/w12/w24 P10/P50/P90 bands (ODP-R3-001, still covered by `test_forecast_job_emits_four_light_alerts_and_handoffs`) |
| four light alerts and acknowledgement persist | `Alert.acknowledge` + `save_alert`; `test_acknowledge_alert_persists_and_rejects_double_ack`, API `test_api_...` re-reads `acknowledged` from `GET /forecastops/alerts` |
| intervention handoff is executable | `InterventionHandoff.execute` + `POST .../execute` links `intervention_id`; `test_execute_handoff_links_intervention_and_rejects_reexecute` + E2E |
| API backed overview detail and audit E2E pass | Overview/Alert-center bound to `GET /forecastops/alerts` with `DataSourceBadge`; E2E asserts live region + `forecastops.alert.acknowledged.v1` / `forecastops.handoff.executed.v1` audit events |
