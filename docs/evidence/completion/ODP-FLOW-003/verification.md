# ODP-FLOW-003 · Verification

- Task: ODP-FLOW-003 · Owner: Codex2 · Reviewer: Claude2
- Worktree: `task/ODP-FLOW-003` (branched from `dev`)

## Commands run

```bash
# Backend integration (versioning, acknowledge, executable handoff, API, audit)
pytest tests/integration/test_forecastops_alerts.py
# → 8 passed, 2 warnings  (5 pre-existing + 3 acknowledge/execute/API-audit tests)

# API app still builds and mounts the forecastops router
pytest tests/smoke \
  --deselect tests/smoke/test_foundation_smoke.py::test_production_dependency_stack_imports
# → 2 passed, 1 deselected

# Lint
ruff check modules/forecastops apps/api/app/routes/forecastops.py \
  tests/integration/test_forecastops_alerts.py
# → All checks passed!

# Web/API typing for the live alert binding and openapi-client reader
npm ci
# → added 402 packages; npm audit reports 2 pre-existing moderate vulnerabilities
npm run typecheck
# → @oday-plus/web, design-tokens, domain-types, openapi-client, ui, ui-domain passed

# Product E2E: ForecastOps alert ack + executable handoff through Ops UI binding
npx playwright test tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts --project=chromium
# → 1 passed
```

## New/updated tests and what they prove

| Test | Proves |
| --- | --- |
| `test_acknowledge_alert_persists_and_rejects_double_ack` | `open → acknowledged` persists actor/time/note; double-ack, empty actor, and unknown id are all rejected (invalid-transition + not-found) |
| `test_execute_handoff_links_intervention_and_rejects_reexecute` | `proposed → dispatched` persists actor/time + `intervention_id`; double-dispatch and unknown id are rejected |
| `test_api_acknowledge_alert_and_execute_handoff_with_audit` | `POST .../acknowledge` re-reads `acknowledged` from `GET /forecastops/alerts`; double-ack → 422; unknown id → 404; `POST .../execute` returns `dispatched` + linked `intervention_id`; both `forecastops.alert.acknowledged.v1` and `forecastops.handoff.executed.v1` audit events recorded under the correlation id |
| `e2e-ops-intervention-price-ad-product.spec.ts` (updated) | product loop drives forecast → **acknowledge red alert** → open intervention → **execute handoff (linked)**; asserts the two new audit event types + `acknowledge` action, and that the `ops-live-alerts` region renders `data-source="api"` with the acknowledged row |

## Not run in this worktree (documented)

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
