# ODP-FLOW-008 · Verification

- Task: ODP-FLOW-008 · Owner: Claude · Reviewer: Antigravity3
- Worktree: `task/ODP-FLOW-008` (fast-forwarded to `dev` tip before implementation)

## Commands run

```bash
# Backend integration — solver, hard constraints, alternatives, infeasibility
# diagnosis, and the approval/execution/outcome lifecycle
python3 -m pytest tests/integration/test_netplan_solver.py -q
# → 5 passed

# Web typecheck (changed workspaces)
npm run typecheck --workspace=@oday-plus/web            # → tsc --noEmit, clean
npm run typecheck --workspace=@oday-plus/openapi-client # → tsc --noEmit, clean

# Web lint
npm run lint --workspace=@oday-plus/web
# → ✔ No ESLint warnings or errors

# Full deterministic product E2E (Playwright auto-starts uvicorn API + Next.js
# web; ortools installed so the SCIP solve actually runs)
npx playwright test tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts --project=chromium
# → 1 passed (16.3s)
```

## What the E2E proves (acceptance #4)

The `E2E-PV-007` product loop drives the NetPlan flow over the live API and then
navigates the browser to the API-backed comparison UI:

| Step | Assertion |
| --- | --- |
| `POST /netplan/scenarios` | scenario persists in `draft` with frozen constraints/versions |
| `POST …/solve` | `solver_status` optimal/feasible, non-empty `selected_actions` |
| alternatives | `alternative_plan_available === true`, `alternatives.length > 0` (comparison basis) |
| submit → decide → execute → outcome | approval `approved`, execution actions > 0, outcome `variance > 0`, label `netplan_realized_gross_margin` |
| `GET /netplan/scenarios` | the created scenario is listed with `status === "outcome_observed"` and a truthy `solver_version` (the endpoint the UI binds to) |
| audit | `netplan.solved.v1` / `netplan.executed.v1` / `netplan.outcome_observed.v1` present under the correlation id |
| browser `/netplan` | `netplan-live-scenarios` region + `netplan-data-source` badge visible (API-backed comparison UI renders) |

## Solver spot-check (this scenario)

```
status: optimal | alt_available: True | n_alts: 2
selected: [('pv007-candidate-east','OPEN'), ('pv007-store-north','IMPROVE')] | exp_gm: 396000.0
```

Confirms the E2E's `alternative_plan_available`/`alternatives` assertions are
deterministic for the fixture scenario (2 alternatives, feasible under budget
155,000 / min GM 340,000 / max risk 0.58 / OPEN≥1 / EXIT=0).

## Scope hygiene

- Tracked changes are exactly: `packages/openapi-client/src/index.ts`,
  `apps/web/features/netplan/NetPlanWorkspace.tsx`,
  `apps/web/src/app/netplan/page.tsx`,
  `tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`, and this
  ODP-FLOW-008 evidence. No NetPlan domain/solver/API or canonical architecture
  docs were changed; other FLOW rows in the shared matrix were left untouched.
- `node_modules/` (npm install) and `ortools` (pip `--user`) are environment
  setup for running the gate; both are outside the repo / gitignored and are not
  part of the commit.
