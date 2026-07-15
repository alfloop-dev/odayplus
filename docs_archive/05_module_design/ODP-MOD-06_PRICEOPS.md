# ODP-MOD-06 PriceOps

Status: implemented for ODP-FLOW-005
Owner lane: PriceOps simulation, approval, execution, monitoring, outcome, rollback

## Purpose

PriceOps produces constrained price-change plans that can be compared, manually
approved, applied as price treatments, monitored during a pilot window, evaluated
against realised margin, and rolled back when stop conditions fire.

## Lifecycle

`candidate -> simulated -> optimized -> pending_approval -> approved_for_pilot -> active -> observing -> evaluated -> continue|adjust|stop|rollback`

Rollback is reachable from live/evaluated states. Hard pricing constraints block
approval; rejected or revision-requested decisions move to `stop`.

## Runtime Contract

- `PlanSimulation` exposes demand, revenue, and gross-margin P10/P50/P90 bands.
- `PlanOptimization` carries the safe action set, solver status, risk level,
  approval requirement, and hard-constraint safety fields.
- `PricingPlanComparison` is the API/UI snapshot for current-vs-candidate price
  comparison plus approval, rollback readiness, execution, monitoring, and
  outcome status.
- `ApprovalRecord` records manual pricing decisions with policy version.
- `PricingExecution` publishes price treatments only after approval and an
  existing rollback plan.
- `ObservationWindow` records pilot stop conditions.
- `PricingEffectEvaluation` recommends continue, adjust, stop, or rollback.

## Acceptance Mapping

| Acceptance | Implementation |
|---|---|
| AC-06-01 hard-constraint violation rate is zero | `solver.pricing.build_safe_action_set`, `count_hard_violations`, and approval gate |
| AC-06-02 every plan compares demand/margin/risk intervals | `PricingPlanComparison.items[*].baseline_simulation` and `candidate_simulation` |
| AC-06-03 pilot has observation window and stop conditions | `PriceOpsService.start_observation` |
| AC-06-04 negative impact recommends rollback | `evaluate_effect` and `PlanStatus.ROLLBACK` transition |
| AC-06-05 treatment flows to InterventionOps and Label Registry | `ActivationResult.handoff` and `label_entry` |

## Primary Artifacts

- `modules/priceops`
- `apps/api/app/routes/priceops.py`
- `apps/web/features/priceops`
- `tests/integration/test_priceops_constraints.py`
- `tests/integration/test_priceops_api.py`
- `tests/e2e/e2e-intervention-price-ad.spec.ts`
