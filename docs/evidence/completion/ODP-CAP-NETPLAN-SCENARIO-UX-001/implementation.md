# Implementation Report: ODP-CAP-NETPLAN-SCENARIO-UX-001

## Task Summary
- **Task ID**: ODP-CAP-NETPLAN-SCENARIO-UX-001
- **Title**: Complete NetPlan scenario and infeasibility UX
- **Owner**: Claude
- **Reviewer**: Codex

## Changes Made

### 1. Hard Constraints & Infeasibility
- Solver strictly enforces hard constraints (`max_budget`, `min_expected_gross_margin`, `min_capacity_delta`, `max_average_risk`, `min_action_counts`, `max_action_counts`).
- Hard constraints are **never auto-relaxed**.
- When no candidate combination satisfies constraints, solver returns `STATUS_INFEASIBLE` with structured `diagnostics`.

### 2. Scenario Version, Hash Binding & Lifecycle State Machine
- Updated `ScenarioSolveRecord` in `modules/netplan/domain/planning.py` to include `problem_hash` binding (computed via `compute_solver_problem_hash`) and persisted `model_version`.
- `compute_solver_problem_hash` incorporates `model_version` in the hashed payload dictionary.
- Added `is_stale(scenario)` method to `ScenarioSolveRecord` to check if solver problem hash matches the current scenario's parameters or if `model_version` differs (returns `True` on version drift or missing `problem_hash`).
- Bound scenario to model version `netplan-network-baseline-v1` by default; model version drift makes solves stale and approval-blocking.
- Updated `VALID_TRANSITIONS` in `modules/netplan/domain/planning.py` to allow returning to `DRAFT` from `SOLVED` and `INFEASIBLE`.
- Fixed `update_scenario` in `modules/netplan/application/planning.py`:
  - Automatically transitions `SOLVED` and `INFEASIBLE` scenarios back to `DRAFT` upon parameter/constraint modification so they can be re-solved cleanly without getting stuck.
  - Fixed partial option updates: preserved existing stores options when updating `candidate_sites` only and vice versa.

### 3. API Route Exposure & OpenAPI Client Regeneration
- Added `PUT /netplan/scenarios/{scenario_id}` route with `NetPlanUpdateScenarioPayload` in `apps/api/app/routes/netplan.py` so scenario parameter updates and stale-reset trigger paths are fully exposed to clients.
- Regenerated OpenAPI artifacts (`packages/openapi-client/openapi.json` and `packages/openapi-client/src/generated/types.ts`) via `make api-contract-refresh`, resolving OpenAPI contract drift.

### 4. Frontend & Rebalance UX Integration
- Updated `modules/opsboard/application/network_rebalance.py`:
  - `solve_netplan` now calculates `is_stale`, `is_infeasible`, and extracts `diagnostics` from solver output to populate `isStale`, `isInfeasible`, and `diagnostics` on every scenario plan row returned to `RebalancePanel`.
  - Added default `isStale: False`, `isInfeasible: False`, `diagnostics: []` to `_seed_scenarios()`.
- Updated `apps/web/features/operator/types.ts` to define `NetPlanDiagnostic` and `NetPlanScenarioDetail` with `isStale`, `isInfeasible`, and diagnostic array.
- Updated `apps/web/features/operator/network/RebalancePanel.tsx` to render:
  - `isStale` status badge (`過期 / Stale`).
  - `isInfeasible` status badge (`不可行`).
  - All 5 structured diagnostic fields: `violated_constraint`, `affected_stores`, `required_relaxation`, `business_impact`, and `suggested_action`.
- Updated `apps/web/features/operator/networkFindAreas.module.css` with styling for diagnostic boxes, items, code fields, and status badges.
- Updated `apps/web/features/operator/fixtures.ts` with sample diagnostic data for UI demonstration and test suite assertions.

### 5. Verification & Test Suite
- Delivered backend integration tests in `tests/integration/test_netplan_solver.py`:
  - `test_update_scenario_lifecycle_restrictions`
  - `test_update_scenario_resets_solved_and_infeasible_to_draft_and_allows_resolving`
  - `test_partial_update_scenario_preserves_omitted_stores`
- Delivered frontend component Vitest suite in `apps/web/src/app/__tests__/netplanDiagnosticsUx.test.tsx` verifying DOM rendering of all 5 diagnostic fields, stale badge, and infeasible badge via React testing library assertions.
