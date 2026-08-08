# Verification Report: ODP-CAP-NETPLAN-SCENARIO-UX-001

## Verification Suite Executed

### 1. Python Unit and Integration Tests
```bash
/home/lupin/oday-plus/.venv/bin/pytest modules/netplan tests/integration/test_netplan_solver.py -q
```
- Total test cases passed: 108 passed.
- Key scenarios verified:
  - `test_stale_solve_result_cannot_be_submitted_or_approved`: verified that modifying scenario constraints after solve marks the solve stale and blocks submit/decide with `NetPlanApprovalError`.
  - `test_all_structured_diagnostic_fields_rendered`: verified all 5 diagnostic fields (`violated_constraint`, `affected_stores`, `required_relaxation`, `business_impact`, `suggested_action`) are populated and non-empty.
  - `test_update_scenario_lifecycle_restrictions`: verified `update_scenario` restricts updates on `pending_approval` scenarios.
  - `test_update_scenario_resets_solved_and_infeasible_to_draft_and_allows_resolving`: verified that updating a `SOLVED` or `INFEASIBLE` scenario resets its state to `DRAFT` and permits re-solving.
  - `test_partial_update_scenario_preserves_omitted_stores`: verified that partial updates to `candidate_sites` preserve existing store options in `options_by_entity`.
  - `test_infeasible_scenario_reports_structured_diagnosis_without_relaxing`: verified hard constraints are strictly preserved.
  - `test_decision_lifecycle_recomputes_every_persisted_solve_field`: verified tamper-proofing and authoritative verification.

### 2. Frontend Vitest Diagnostic Suite
```bash
npx vitest run apps/web/src/app/__tests__/netplanDiagnosticsUx.test.ts
```
- Total test cases passed: 3 passed.
- Key scenarios verified:
  - `verifies RebalancePanel references and renders all 5 structured diagnostic fields and stale state`: verified `RebalancePanel.tsx` renders `violated_constraint`, `affected_stores`, `required_relaxation`, `business_impact`, `suggested_action`, and `isStale`.
  - `verifies NetPlan types include NetPlanDiagnostic and NetPlanScenarioDetail`: verified type safety.
  - `verifies fixtures contain sample diagnostic and stale scenario data`: verified fixture data completeness.

### 3. Status & Verification Summary
- Feasible solve binding: VERIFIED
- Infeasible diagnosis & no auto-relaxation: VERIFIED
- Structured diagnostic fields (5 fields) in backend & frontend: VERIFIED
- Stale result approval rejection & reset to draft on update: VERIFIED
- Delivered tests & API routes: VERIFIED
