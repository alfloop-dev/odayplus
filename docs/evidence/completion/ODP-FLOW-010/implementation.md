# ODP-FLOW-010 Implementation Evidence

## Scope Delivered

ODP-FLOW-010 completes the OpsBoard/Governance operator flow enough for
product-grade proof:

- Replaced route-local operator state with `OperatorStateStore`.
- Added RBAC-guarded read APIs for bootstrap, Today, issues, approvals,
  notifications, tasks, and search.
- Added idempotent workflow writes for Store Ops issue transitions, approval
  decisions, and evidence purpose recording.
- Wired durable-mode persistence through `SqliteDocumentStore` when the API is
  started with the durable persistence bundle.
- Wired `/operator` React shell to API-backed bootstrap, notifications,
  approval state, search results, governance decision/audit rows, and task
  follow-up counts.
- Added backend contract tests covering RBAC, state transitions, idempotency,
  reason gate, search, and platform audit events.

## Touched Files

- `apps/api/app/routes/operator.py`
- `apps/api/oday_api/main.py`
- `apps/web/features/operator/OperatorConsole.tsx`
- `apps/web/features/operator/GovernanceWorkspace.tsx`
- `apps/web/features/operator/operator.module.css`
- `modules/opsboard/README.md`
- `tests/contract/test_operator_api.py`
- `docs/evidence/PRODUCT_FLOW_IMPLEMENTATION_MATRIX_2026-07-12.md`
- `docs_archive/05_module_design/ODP-MOD-11_OPSBOARD.md`

## Notes

- The task brief mentioned `apps/api/operator_api.py`; the current repo mounts
  the operator API from `apps/api/app/routes/operator.py`, so the implementation
  follows the existing API routing convention.
- The task brief mentioned the module design archive path before that archive
  directory existed in this worktree; this task creates the required
  `docs_archive/05_module_design/ODP-MOD-11_OPSBOARD.md` artifact.
