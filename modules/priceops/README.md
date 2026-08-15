# PriceOps Module

Constrained, auditable price adjustment (ODP-MOD-06): safe action set, demand /
margin simulation, optimization, approval, pilot observation, effect evaluation
and rollback.

## Layers

- `domain/` — `PricingPlan` aggregate, the lifecycle state machine
  (`PlanStatus` + `VALID_TRANSITIONS`), and the audit-bearing records
  (`StatusTransition`, `ApprovalRecord`, `PricingExecution`,
  `PricingEffectEvaluation`, `RollbackRecommendation`, treatment hand-offs).
- `application/` — `PriceOpsService` orchestrates the full lifecycle:
  `create_plan → simulate → optimize → submit_for_approval → approve →
  activate → start_observation → evaluate → (continue/adjust/stop/rollback)`.
  Each state-changing call drives exactly one plan transition so
  `status_history` stays complete.
- `infrastructure/` — `InMemoryPriceOpsRepository` (dependency-free, like the
  other ODay Plus modules).
- `workers/` — `pricing-optimizer` batch entry point
  (`run_priceops_optimizer_batch`) that fails the job if any plan would breach a
  hard constraint.

The numeric engine (hard constraints, demand model, constrained optimizer) lives
in `solver/pricing` and is imported here.

## Acceptance coverage

| AC | Where |
|----|-------|
| AC-06-01 hard-constraint violation rate = 0 | `solver.pricing.build_safe_action_set` filters infeasible prices; `count_hard_violations` re-checks recommendations |
| AC-06-02 demand / margin / risk intervals | `PlanSimulation` / `solver.pricing.simulate_price` P10/P50/P90 bands |
| AC-06-03 pilot observation window + stop conditions | `build_observation_window` / `ObservationWindow.stop_conditions` |
| AC-06-04 negative impact → rollback recommendation | `evaluate_effect` → `RollbackRecommendation` |
| AC-06-05 treatment → InterventionOps + Label Registry | `PriceOpsService.activate` → `InterventionTreatmentHandoff` + `LabelRegistryEntry` |

Tests: `tests/integration/test_priceops_constraints.py`.
