# Acceptance Checklist: ODP-CAP-NETPLAN-SCENARIO-UX-001

| Requirement | Status | Evidence / Notes |
|---|---|---|
| Hard constraints are never auto-relaxed | PASSED | `solve_network_plan` strictly enforces budget/margin/risk/action bounds. Infeasible states generate structured diagnostics without altering limits. |
| Results bind to scenario version and hash | PASSED | `ScenarioSolveRecord` stores `model_version` and `problem_hash` (which incorporates `model_version`). Version drift (e.g. changing model_version from v1 to v2) makes `is_stale()` return `True` and blocks submit/decide approval. |
| All structured diagnostic fields render | PASSED | All 5 fields (`violated_constraint`, `affected_stores`, `required_relaxation`, `business_impact`, `suggested_action`) are rendered in `RebalancePanel.tsx` and validated by React component DOM render assertions in `netplanDiagnosticsUx.test.tsx`. |
| Stale results cannot be approved | PASSED | `submit_for_approval` and `decide` verify `solve.is_stale(scenario)` and raise `NetPlanApprovalError` when problem hash mismatches. `update_scenario` resets `SOLVED`/`INFEASIBLE` scenarios to `DRAFT` so re-solving is enabled. API exposes `PUT /netplan/scenarios/{id}` and `is_stale`. |
| Feasible, infeasible, and failure tests delivered | PASSED | Pytest suite (108 tests) in `tests/integration/test_netplan_solver.py` and Vitest suite in `apps/web/src/app/__tests__/netplanDiagnosticsUx.test.tsx` cover optimal plans, infeasible diagnoses, stale protection, lifecycle re-solving, option preservation, and component DOM rendering. |
