# Fleet Execution Brief: ODP-GAP-ML-003

- Parent: Product Platform Gap Closure
- Status: implemented
- Scope boundary: causal decision services (InterventionOps / PriceOps / AdLift)
- Owner lane: Claude (ML pipeline)
- Reviewer lane: Codex2
- Suggested branch: `task/ODP-GAP-ML-003`
- Release authority: task-scoped PR into `dev` with green required checks

## Objective

Complete the ML pipeline part 3 causal-decision surface — InterventionOps,
PriceOps, and AdLift — for effect measurement, elasticity, approval, rollback,
and outcome maturity. The three modules (domain / application / infrastructure /
workers / routers) already exist in `dev`; this task closes the remaining ML
gap: the price-elasticity estimator was implemented but orphaned, and the
PriceOps plan API required the caller to hand-feed an `elasticity_value`.

## Delivered This Task

- `models/priceops/elasticity.py`, `models/shared_ml/backtest.py`,
  `models/shared_ml/drift.py` — elasticity estimator, rolling backtest engine,
  and PSI drift monitor (log-log OLS elasticity with safety bounds; per-horizon
  MAPE/RMSE/MAE; PSI PASSED/WARNING/FAILED). Anchored earlier; linted clean.
- `models/priceops/binding.py` — **new** elasticity binding layer. Resolves a
  `PriceElasticityEstimate` for a plan item from, in priority order,
  (1) enough live `(price, demand)` observations to run the estimator
  (`elasticity_source="estimated"`) or (2) a client-supplied value
  (`elasticity_source="client_supplied"`); **fails closed** with
  `ElasticityInputError` when neither is available.
- `apps/api/app/routes/priceops.py` — plan-item payload gains
  `price_demand_observations` and makes `elasticity_value` optional; plan and
  optimizer-job creation resolve elasticity through the binding, surface
  model-binding metadata (`elasticity_bindings`) on the response and the
  `priceops.plan_created.v1` audit event, and return HTTP 422 when the input is
  absent.
- `tests/integration/test_gap_ml_003_elasticity_binding.py` — API-level proof of
  estimated / client-supplied / fail-closed paths (plan + optimizer-job).
- `tests/integration/_authz.py` — `PRICEOPS_HEADERS` (least-privilege
  `PRICING_MANAGER` bundle) for authenticated PriceOps API tests.

## Fail-Closed Behavior (acceptance)

A pricing plan is refused (HTTP 422) rather than priced from a fabricated demand
curve when the live elasticity signal is absent:

- no observations and no `elasticity_value` → 422;
- fewer than `MIN_OBSERVATIONS` (5) usable observations and no `elasticity_value`
  → 422;
- the batch optimizer-job path enforces the same guard.

## Verification Evidence

```bash
uv run pytest tests/integration/test_gap_ml_003.py \
  tests/integration/test_gap_ml_003_elasticity_binding.py \
  tests/integration/test_priceops_constraints.py \
  tests/integration/test_intervention_workflow.py \
  tests/integration/test_adlift_incrementality.py -q
```

```bash
uv run ruff check models apps/api/app/routes/priceops.py tests/integration
```

Observed: 52 integration tests pass; ruff clean over `models`, the PriceOps
router, and `tests/integration`; `tests/contract` green; the FastAPI app builds
and the estimated elasticity flows end-to-end (plan → simulate) with the model
binding captured on the audit trail.

## Acceptance Criteria

- InterventionOps / PriceOps / AdLift services cover effect measurement,
  elasticity, approval, rollback, and outcome maturity.
- Fail-closed when external live inputs (elasticity signal) are absent.
- Scoped task-branch PR with green required checks.
