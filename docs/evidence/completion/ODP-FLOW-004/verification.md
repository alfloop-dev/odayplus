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

## Post-review fix (2026-07-12 · Antigravity5) — maturity guard

Codex2 review reopened the task with the following reproduction:

```
after_evaluate_status=COMPLETED, observation_mature=False
after_close_status=CLOSED
```

Root cause: `evaluate_effect` always advanced the case to `COMPLETED` regardless
of whether `window.is_mature` was true. `close_case` only gated on `COMPLETED`,
so an immature evaluation silently reached the terminal `CLOSED` state.

**Fix applied in two complementary layers:**

1. **Primary guard (`evaluate_effect`)** — the case only advances to `COMPLETED`
   when `mature=True`. When `mature=False` the transition stops at `EVALUATING`;
   `close_case`'s status guard (`requires COMPLETED`) then blocks the premature
   close.

2. **Defence-in-depth (`close_case`)** — added an explicit guard:
   `if intervention.effect is not None and not intervention.effect.observation_mature`
   raises `InterventionError("cannot close: observation window has not matured …")`.
   This protects against any future code path that reaches `COMPLETED` without a
   mature effect (e.g. a migration or admin override).

**Regression tests added:**

| Test | What it proves |
| --- | --- |
| `test_immature_window_cannot_claim_effect` (updated) | now also asserts `status is EVALUATING` (not `COMPLETED`) after an immature evaluate |
| `test_immature_evaluate_then_close_is_rejected` | reproduces the Codex2 sequence: immature evaluate → attempted close → `InterventionError("cannot close")` |
| `test_close_defence_in_depth_rejects_immature_effect` | directly tests the second guard by fabricating a `COMPLETED` case with `observation_mature=False` and asserting `InterventionError("observation window has not matured")` |

```bash
# After fix
uv run pytest tests/integration/test_intervention_workflow.py -q
# → 17 passed (was 15 before fix; +2 regression tests)

uv run ruff check modules/intervention apps/api/app/routes/interventions.py \
  tests/integration/test_intervention_workflow.py
# → All checks passed!
```

## Second pass verification (2026-07-12 · Antigravity5) — mature-retry path

Root cause (newly identified): the first fix correctly left immature evaluations
in `EVALUATING`, but `_require_status` for `evaluate_effect` only allowed
`OBSERVING`.  Calling `evaluate_effect(now=MATURE_TIME)` from `EVALUATING` raised
`InterventionError: cannot evaluate effect on intervention in status EVALUATING`,
leaving those cases permanently stuck.

**Fix**: `evaluate_effect` status guard extended to `{OBSERVING, EVALUATING}`.

**New test**:

| Test | What it proves |
| --- | --- |
| `test_immature_evaluate_then_mature_retry_reaches_completed` | exact three-step reproduction: immature eval → `EVALUATING`; mature retry → `COMPLETED`; close → `CLOSED` |

```bash
# After second pass
uv run pytest tests/integration/test_intervention_workflow.py -q
# → 18 passed (+1 mature-retry regression; total now 18)

uv run ruff check modules/intervention apps/api/app/routes/interventions.py \
  tests/integration/test_intervention_workflow.py
# → All checks passed!
```
