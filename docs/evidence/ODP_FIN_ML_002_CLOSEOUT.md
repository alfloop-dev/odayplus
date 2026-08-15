# ODP-FIN-ML-002 Closeout Evidence

## Scope

ODP-FIN-ML-002 replaced hand-written statistics in the ForecastOps,
PriceOps, and AdLift domains with the scientific libraries already
declared in `pyproject.toml` (numpy / scikit-learn / statsmodels),
applied only where they add rigour and without regressing any existing
point estimate or test.

- **forecastops**: prediction-interval width is now derived from the
  series' residual volatility via `numpy.polyfit` (previously a fixed
  spread constant); P10/P90 use the standard-normal quantile.
- **adlift**: the matched-pair difference-in-differences effect interval
  is now a `statsmodels` OLS t-confidence-interval carrying a real
  standard error; the pre-trend slope is estimated with `numpy.polyfit`.
  All aggregate point estimates are unchanged.
- **priceops / solver**: a new scikit-learn log-log OLS elasticity
  estimator (`solver.pricing.demand.estimate_elasticity` and
  `PriceElasticityEstimate.from_observations`) with R²-derived
  confidence.

## Delivery

- Runtime deliverable commit `6d9a9b98`
  (`ODP-FIN-ML-002: back stats methods with numpy/sklearn/statsmodels`).
- PR **#264** (`task/ODP-FIN-ML-002` → `dev`) merged on 2026-07-13
  (merge commit `ed65fc31`). The library-backed statistics are durable on
  `dev`.

## Owned / Not-Changing Layers

- **Owned**: statistical internals of the three FIN-ML domains, the new
  solver elasticity estimator, and a focused test module.
- **Not changing**: public API routes, workflow / state machines, and
  output contracts.
- **Composes with**: the existing ForecastOps / PriceOps / AdLift
  services and their integration tests.

## Review Approval

Task status `review_approved`; reviewer of record: **Codex**
(review recorded 2026-07-13T10:07:33Z — "FIN-ML library-backed changes
verified"). Acceptance: existing point estimates preserved and all prior
tests remain green while the declared libraries back the rigour-bearing
statistics.

## Verification

Commands run against the merged `dev` tip on 2026-07-13:

```bash
.venv/bin/python -m pytest \
  tests/integration/test_priceops_constraints.py \
  tests/integration/test_adlift_incrementality.py \
  tests/integration/test_forecastops_alerts.py -q
```

Result: **37 passed**. The original deliverable commit additionally
recorded a full focused run of `tests/integration tests/contract
tests/smoke` (291 passed) with `ruff check` clean.

## Closeout Notes

- This corrective evidence commit carries the finalize trailers matching
  the current task owner (Claude) and reviewer of record (Codex); the
  merged runtime commit `6d9a9b98` carried a stale `Reviewer: Antigravity`
  trailer from before the reviewer was reassigned to Codex.
- The only task-owned change in this commit is this evidence artifact; the
  runtime deliverable already lives on `dev` via PR #264.
