# Acceptance Checklist: ODP-CAP-NETPLAN-SCENARIO-UX-001

| Requirement | Status | Evidence / Notes |
|---|---|---|
| Hard constraints are never auto-relaxed | PASSED | `solve_network_plan` strictly enforces budget/margin/risk/action bounds. Infeasible states generate diagnostics without altering limits. |
| Results bind to scenario version and hash | PASSED | `ScenarioSolveRecord` stores `problem_hash` computed from `options_by_entity` and `constraints`. |
| All structured diagnostic fields render | PASSED | All 5 fields (`violated_constraint`, `affected_stores`, `required_relaxation`, `business_impact`, `suggested_action`) are serialized and rendered. |
| Stale results cannot be approved | PASSED | `submit_for_approval` and `decide` verify `solve.is_stale(scenario)` and raise `NetPlanApprovalError` when problem hash mismatches. API exposes `is_stale`. |
| Feasible, infeasible, and failure tests delivered | PASSED | Suite in `tests/integration/test_netplan_solver.py` covers optimal plans, infeasible diagnoses, stale protection, and lifecycle failures. |
