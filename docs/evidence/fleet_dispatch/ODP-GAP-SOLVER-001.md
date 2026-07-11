# Fleet Execution Brief: ODP-GAP-SOLVER-001

- Parent: Product Platform Gap Closure
- Status: delivered (closeout)
- Scope boundary: solver_domain (DealRoom AVM valuation + NetPlan optimization)
- Owner lane: Claude
- Reviewer lane: Codex2
- Delivered branch: `task/ODP-GAP-SOLVER-001`
- Release authority: PR #207 (merge `e3d0101`), follow-ups PR #211 (`f90ab9b`), PR #212 (`a3e2f6b`)

## Objective

Implement the DealRoomAVM valuation and NetPlan optimization services with
infeasibility diagnostics, alternative plans, an audit trail, and API evidence,
failing closed when live external inputs are absent.

## Delivered Surface

Merged into `dev` (all paths verified present at `dev` tip `e9846bb`):

- AVM valuation domain + application + workers
  - `modules/avm/domain/valuation.py` — three-lens valuation, comparable
    multiples, price separation, data-room checklist, export audit.
  - `modules/avm/application/valuation.py`, `modules/avm/workers/valuation_worker.py`
  - `apps/api/app/routes/avm.py` — valuation, data-room export, and finance
    approval endpoints (approval fails closed without a reason).
- NetPlan optimization domain + solver
  - `modules/netplan/domain/planning.py`, `modules/netplan/application/planning.py`
  - `modules/netplan/workers/solver_worker.py`
  - `solver/netplan/optimizer.py` — OR-Tools MILP with a deterministic
    fallback, alternative plans, and structured `InfeasibilityDiagnosis`
    output; `ortools` is lazy-imported so API startup never requires it.
  - `apps/api/app/routes/netplan.py` — scenario/solve/approve endpoints wired
    through an `InMemoryAuditLog` audit trail and an authz engine.

## Acceptance

1. Meets scope in this brief — **met**: valuation, optimization, infeasibility
   diagnostics, alternatives, audit trail, and API evidence are all present and
   exercised by integration tests (below).
2. Fail-closed when external live inputs are absent — **met**: infeasible
   scenarios return a structured diagnosis without relaxing constraints and
   cannot be advanced to approval; the NetPlan solver falls back deterministically
   when OR-Tools is unavailable rather than fabricating a plan.
3. Scoped task-branch PR with green required checks — **met**: PR #207 merged to
   `dev`, with post-merge lint/import corrections in PR #211 and PR #212.

## Verification

Run at `dev` tip `e9846bb`:

```bash
uv run pytest tests/integration/test_avm_valuation.py tests/integration/test_netplan_solver.py -q
# 8 passed
```

Covering tests:

- `test_valuation_view_and_worker_emit_three_lenses_and_price_separation`
- `test_finance_approval_requires_reason_and_updates_report`
- `test_avm_api_runs_e2e_valuation_dataroom_export_and_audit`
- `test_scenario_builder_and_solver_return_optimal_plan_with_alternatives`
- `test_infeasible_scenario_reports_structured_diagnosis_without_relaxing`
- `test_service_lifecycle_tracks_approval_execution_and_outcome`
- `test_infeasible_scenario_cannot_skip_to_approval`
- `test_batch_worker_solves_multiple_scenarios_and_persists_results`

End-to-end product coverage: `tests/e2e/e2e-avm-netplan.spec.ts`,
`tests/e2e/e2e-avm-netplan-learning-audit-product.spec.ts`.

## Closeout Note

The reviewed deliverable is already durable in `dev` via PR #207 (+#211, #212).
This document is the acceptance-evidence packet referenced by the task's
`source_docs`; it carries no product code change beyond recording the delivered
and verified scope so the task can be finalized to `done`.
