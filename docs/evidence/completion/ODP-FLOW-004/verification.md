# ODP-FLOW-004 · Verification

- Task: ODP-FLOW-004 · Owner: Claude · Reviewer: Codex2
- Worktree: `task/ODP-FLOW-004` (branched from `dev`)

## Commands run

```bash
# Backend integration (state machine, close/follow-up, API, audit, idempotency)
.venv/bin/pytest tests/integration/test_intervention_workflow.py
# → 15 passed  (11 pre-existing + 4 new close/follow-up tests)

# Foundation smoke (API app still builds and mounts the router)
.venv/bin/pytest tests/integration/test_intervention_workflow.py tests/smoke
# → 18 passed

# Lint
.venv/bin/ruff check modules/intervention apps/api/app/routes/interventions.py \
  tests/integration/test_intervention_workflow.py
# → All checks passed!
```

## New/updated tests and what they prove

| Test | Proves |
| --- | --- |
| `test_close_completed_case_records_disposition_and_is_terminal` | `COMPLETED` is not terminal; `close_case` → `CLOSED` (terminal); CloseRecord captures disposition + snapshotted effect recommendation |
| `test_close_requires_reason_and_completed_state` | close rejected from non-`COMPLETED` states, empty reason rejected, and a `CLOSED` case cannot be closed again (invalid-transition rejection) |
| `test_close_with_follow_up_opens_linked_candidate_after_maturity` | `follow_up=True` opens a same-store CANDIDATE linked via `trigger_ref`, scheduled at the original window's maturity time (no contamination) |
| `test_api_close_case_with_follow_up_and_audit` | `POST /interventions/{id}/close` returns `CLOSED`; unknown disposition → 422; follow-up CANDIDATE reachable via `GET`; `close` audit event recorded under the correlation id |
| `e2e-ops-intervention-price-ad-product.spec.ts` (updated) | product loop drives forecast → intervention → **close (+follow-up)** → price → adlift; asserts `CLOSED`, linked follow-up, and `close` in the audit action set |

## Not run in this worktree (documented)

- **Web typecheck / Playwright E2E**: `node_modules` and `tsc`/Playwright browsers
  are not installed in the worker worktree (no JS toolchain), so
  `npm run typecheck` and `npm run test:e2e` were not executed here. The TS
  changes reuse the existing, typed `ApiBinding` / `DataSourceBadge` /
  `openapi-client.listInterventions()` contracts; the executable proof of the
  new `/close` behavior is the Python integration suite above (TestClient drives
  the same API surface the E2E hits). Reviewer/CI with the JS toolchain runs the
  typecheck + Playwright gate.

## Scope hygiene

- Task-scoped commits only touch `modules/intervention`, the interventions API
  route, the intervention web feature + route, the intervention tests, and the
  ODP-FLOW-004 evidence. No canonical architecture docs were broadened. Incidental
  `uv.lock` churn from running the venv was reverted, not committed.
