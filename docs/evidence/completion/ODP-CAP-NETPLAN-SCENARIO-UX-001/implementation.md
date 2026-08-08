# Implementation Report: ODP-CAP-NETPLAN-SCENARIO-UX-001

## Task Summary
- **Task ID**: ODP-CAP-NETPLAN-SCENARIO-UX-001
- **Title**: Complete NetPlan scenario and infeasibility UX
- **Owner**: Antigravity7
- **Reviewer**: Claude2

## Changes Made

### 1. Hard Constraints & Infeasibility
- Solver strictly enforces hard constraints (`max_budget`, `min_expected_gross_margin`, `min_capacity_delta`, `max_average_risk`, `min_action_counts`, `max_action_counts`).
- Hard constraints are **never auto-relaxed**.
- When no candidate combination satisfies constraints, solver returns `STATUS_INFEASIBLE` with structured `diagnostics`.

### 2. Scenario Version and Hash Binding
- Updated `ScenarioSolveRecord` in `modules/netplan/domain/planning.py` to include `problem_hash` binding (computed via `compute_solver_problem_hash`).
- Added `is_stale(scenario)` method to `ScenarioSolveRecord` to check if solver problem hash matches the current scenario's parameters.
- Added `update_scenario` method to `NetPlanService` in `modules/netplan/application/planning.py` to allow parameter/constraint updates on draft scenarios.

### 3. Structured Diagnostic Fields
- Rendered and validated all 5 structured diagnostic fields in `InfeasibilityDiagnosis`:
  1. `violated_constraint`
  2. `affected_stores`
  3. `required_relaxation`
  4. `business_impact`
  5. `suggested_action`

### 4. Stale-Result Protection
- Enhanced `NetPlanService.submit_for_approval` and `NetPlanService.decide` to check `solve.is_stale(scenario)`.
- If scenario parameters or constraints change after solve execution, any attempt to submit or approve the stale result is rejected with `NetPlanApprovalError`.
- Updated API detail endpoint in `apps/api/app/routes/netplan.py` to include `is_stale` flag in solve detail payload for UI rendering.

### 5. Frontend & Rebalance UX Integration
- `NetworkFindAreasWorkspace.tsx` and `RebalancePanel.tsx` bind NetPlan solver results, alternatives, and infeasibility details cleanly without auto-relaxing constraints or accepting stale results.
