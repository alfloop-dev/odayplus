# ODP-FLOW-003 · Verification

- Task: ODP-FLOW-003 · Owner: Claude · Reviewer: Claude2
- Worktree: `task/ODP-FLOW-003` (branched from `dev`)

## Commands run

```bash
# Backend integration (versioning, acknowledge, executable handoff, API, audit)
.venv/bin/pytest tests/integration/test_forecastops_alerts.py
# → 8 passed  (5 pre-existing + 3 new acknowledge/execute/API-audit tests)

# API app still builds and mounts the forecastops router
.venv/bin/pytest tests/smoke \
  --deselect tests/smoke/test_foundation_smoke.py::test_production_dependency_stack_imports
# → 2 passed

# Lint
.venv/bin/ruff check modules/forecastops apps/api/app/routes/forecastops.py \
  tests/integration/test_forecastops_alerts.py
# → All checks passed!
```

## New/updated tests and what they prove

| Test | Proves |
| --- | --- |
| `test_acknowledge_alert_persists_and_rejects_double_ack` | `open → acknowledged` persists actor/time/note; double-ack, empty actor, and unknown id are all rejected (invalid-transition + not-found) |
| `test_execute_handoff_links_intervention_and_rejects_reexecute` | `proposed → dispatched` persists actor/time + `intervention_id`; double-dispatch and unknown id are rejected |
| `test_api_acknowledge_alert_and_execute_handoff_with_audit` | `POST .../acknowledge` re-reads `acknowledged` from `GET /forecastops/alerts`; double-ack → 422; unknown id → 404; `POST .../execute` returns `dispatched` + linked `intervention_id`; both `forecastops.alert.acknowledged.v1` and `forecastops.handoff.executed.v1` audit events recorded under the correlation id |
| `e2e-ops-intervention-price-ad-product.spec.ts` (updated) | product loop drives forecast → **acknowledge red alert** → open intervention → **execute handoff (linked)**; asserts the two new audit event types + `acknowledge` action, and that the `ops-live-alerts` region renders `data-source="api"` with the acknowledged row |

## Not run in this worktree (documented)

- **Web typecheck / Playwright E2E**: `node_modules`, `tsc`, and the Playwright
  browsers are not installed in the worker worktree (no JS toolchain), so
  `npm run typecheck` and `npm run test:e2e` were not executed here. The TS
  changes reuse the existing, typed `ApiBinding` / `DataSourceBadge` /
  `getServerApiClient` / `loadApiBinding` contracts and the new
  `listForecastAlerts()` client method; the executable proof of the new
  `/acknowledge` and `/execute` behavior is the Python integration suite above
  (TestClient drives the same API surface the E2E hits). Reviewer/CI with the JS
  toolchain runs the typecheck + Playwright gate.
- **`tests/smoke/test_foundation_smoke.py::test_production_dependency_stack_imports`**
  fails in this worktree because the production ML stack (`duckdb`, `h3`,
  `ortools`, `sklearn`, `statsmodels`, `numpy`) is not installed in the worker
  environment. This test is untouched by ODP-FLOW-003 (no `tests/smoke/` diff)
  and the failure is purely environmental — hence the targeted `--deselect`.

## Scope hygiene

- Task-scoped changes only touch `modules/forecastops`, the forecastops API
  route, the `@oday-plus/openapi-client` reader, the operations web feature + its
  two server routes, the forecastops integration test, the ops product E2E, and
  the ODP-FLOW-003 evidence + matrix row. No canonical architecture docs and no
  RBAC grants were broadened (acknowledge uses `forecastops:create`, execute uses
  `forecastops:execute` — both already held by `OPERATIONS_MANAGER`).
