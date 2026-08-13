# ODP-FORECASTOPS-PROD-REVIEW-001 — ForecastOps production runtime review

- Reviewer / owner: Claude3
- Independent reviewer: Codex2
- Reviewed pull request: [#384](https://github.com/alfloop-dev/odayplus/pull/384)
- Reviewed exact head: `957234d8fbe40586a306c9c3d77388edcb16c899`
- Base used for the diff: `c72804b8dcf6ef8a78554f69dab780420e8efeba`
- Date: 2026-07-27

## Scope

This review covers the ForecastOps lane of PR #384 only:

- `modules/forecastops/**`
- `apps/api/app/routes/forecastops.py`
- `apps/worker/oday_worker/handlers.py`
- `product_ops/modeling/forecast_training.py`, the `forecastops` entry in
  `product_ops/modeling/contracts.py`, the forecast branches of
  `product_ops/modeling/release.py`, and
  `model_ready.forecast_training_view` in `product_ops/modeling/sql/model_ready_views.sql`
- `shared/infrastructure/persistence/repositories.py` (`DurableForecastOpsRepository`)
- the ForecastOps forecast/alert/handoff calls in
  `tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts`

**No LearningHub behaviour is reviewed or approved by this task.** The
LearningHub release saga, the MLflow adapter, and their tests remain outside this
lane and outside this verdict.

## Verdict

The forecast slice is correct on temporal safety, as-of guards, tenant scoping,
zero-transaction labels, and provenance. Three real defects were found and
fixed on the PR branch; each has a regression test that fails on `957234d8` and
passes on the corrected head.

## Re-verification of the seven Forecast findings

| # | Finding | Verdict | Evidence |
|---|---|---|---|
| 1 | Temporal split is leakage-safe | PASS | `expand_forecast_horizon_rows` emits a row only when the whole `D..D+7h-1` window is present and contiguous, and rejects a window whose label matures before the prediction origin. `_temporal_split(..., purge_label_overlap=True)` drops every training row whose `label_maturity_time` reaches the holdout cutoff, so no training label overlaps the validation period. |
| 2 | As-of guard fails closed | PASS | `_feature_row` rejects, before any inference, an observation on or after the prediction origin, fewer than 28 days of history, a latest observation that is not exactly `origin - 1 day` (stale or future), a non-contiguous daily window, a day without source lineage, and a missing tenant. Verified against all seven cases; every one raises `ProductionModelInputError`. |
| 3 | API idempotency survives replay | PASS (with defect C below) | `ForecastOpsJobStore.run` reserves the idempotency key under one lock before the forecast runs, so a replay returns the first job unchanged. Confirmed end to end through the API: a second identical request returns the same `job_id` and creates no second forecast. |
| 4 | Worker idempotency survives replay | **FAIL → fixed (defect A)** | The prediction run id is derived from the job id and the forecast/prediction ids are `uuid5`-stable, so forecasts and predictions dedupe correctly. Alerts and handoffs did not: replay overwrote them with freshly generated `open` / `proposed` records and destroyed the operator acknowledgement and dispatch record. |
| 5 | Zero-transaction labels stay represented | PASS | `complete_daily` left-joins the eligible store/date spine against `transaction_daily` and coalesces a missing day to `daily_net_revenue = 0.0` with `transaction_count = 0`, so a genuine no-transaction day is kept as a real zero label instead of being dropped. |
| 6 | PostgreSQL multi-instance sequence test | PASS in CI, skipped locally | `tests/integration/test_forecastops_postgresql_sequence.py` drives two independent `PostgresEngine` instances through a barrier and asserts the advisory-lock-serialised versions are `[1, 2]`. It is gated on `INTAKE_TEST_DATABASE_URL`, which the CI `product` job sets against its `postgis/postgis:16-3.5` service; this sandbox has no PostgreSQL, so it skips here. |
| 7 | Provenance is complete | PASS (one nit below) | Production inference requires `source_snapshot_ids` on every history day; the forecast output carries the observation lineage, the model version, the engine, and the inference audit metadata; the training view unions the target and the 28-day feature window lineage. |

## Defects found and fixed

### A. Job replay rewound acknowledged alerts and dispatched handoffs — blocker

`ForecastOpsService.forecast` wrote every generated alert and handoff with a
blind `save_alert` / `save_handoff`. Because the alert and handoff ids are
`uuid5`-stable functions of the deduplicated forecast, an at-least-once
redelivery of the same forecast job regenerated them in their initial state and
overwrote the persisted ones.

Reproduced on `957234d8`: after an operator acknowledged a red alert and
dispatched its handoff, replaying the same job returned the alert to
`status=open` with `acknowledged_by`/`acknowledged_at` cleared, and the handoff
to `status=proposed` with `executed_by`, `executed_at`, and `intervention_id`
cleared. The acknowledgement audit trail was lost and the alert became
acknowledgeable a second time. Both the in-memory and the durable repository
were affected.

Fix: `forecast()` now persists a generated alert or handoff only when one is not
already stored, so a replay returns the persisted lifecycle state untouched.
Acknowledgement and execution still write through `save_alert` / `save_handoff`
as before.

Regression test: `test_worker_replay_preserves_alert_acknowledgement_and_handoff_execution`.

### B. A multi-horizon batch silently returned the wrong horizon — blocker

`forecast_output_id` was `uuid5` over `(prediction_run_id, tenant_id, store_id)`
with no horizon component, and `prediction_id` over `(run_id, store_id)`. Two
inputs for the same store at different canonical horizons therefore produced the
same identity, and the idempotent repository write collapsed them.

Reproduced on `957234d8`: a batch asking for `horizon_days=28` and
`horizon_days=168` for one store returned the 28-day band twice, labelled
`horizon_days=28` for both, persisted a single forecast and a single prediction,
while the prediction run recorded `prediction_horizon="w24,w4"` — claiming both
horizons had been produced. The path is reachable from the public API, whose
`inputs` list accepts a per-input `horizon_days`.

Fix: the horizon is now part of the forecast and prediction identity, so each
requested horizon produces its own persisted forecast, alert lineage, and
prediction.

Regression test: `test_batch_scores_every_requested_horizon_for_one_store`.

### C. Exact-head `product-e2e-gate` failure — blocker

CI run `30264811807` failed at
`tests/e2e/e2e-ops-intervention-price-ad-product.spec.ts:26`, expecting `202`
from `POST /forecastops/forecast-jobs` and receiving `403`.

Root cause: the PR makes an authenticated tenant scope mandatory for every
ForecastOps route (`TENANT_SCOPE_REQUIRED`), but this spec — the only E2E spec
that exercises ForecastOps — never sent `x-tenant-id`, unlike every
operator-console spec in the repo. Reproduced directly against the composed
app: the spec's exact headers return `403 TENANT_SCOPE_REQUIRED`, and the same
request with `x-tenant-id` returns `202`.

Fix: the three ForecastOps requests in that spec now carry the repo's standard
`x-tenant-id: tenant-a` operator scope. Authorization is not weakened and the
expected status codes are unchanged; the other product lanes in the spec keep
the shared headers. Verified through the composed app that the whole ForecastOps
loop — job `202 succeeded` with a red alert and an eligible handoff,
acknowledge `200 acknowledged`, execute `200 dispatched` with the linked
intervention, and an idempotent replay returning the same `job_id` — now passes.

## Non-blocking observations

These do not block the ForecastOps lane and are recorded for the owning lane to
schedule.

1. `model_ready.forecast_training_view`: inside the lineage lateral,
   `bool_and(lineage.run_id = partition.run_id)` sits under a `WHERE` clause that
   already forces that equality, so `source_run_complete` from lineage is always
   true when any row matches. The real run-completeness check now lives in
   `authoritative_order_partitions`; the lateral predicate is dead.
2. A partition whose winning `run_id` differs from the run that wrote
   `canonical_lineage` — a resumed or re-run ingestion — makes every store-day
   for that date fail `lineage_complete` and drop out of training. This is a
   data-availability risk, not a leakage risk, but it is silent.
3. `feature_snapshot_time` is `prior_feature_maturity_time`, which is `NULL`
   when a row has no prior history, so `is_training_eligible` evaluates to `NULL`
   rather than `FALSE` for those rows. `exclusion_reason` still reports
   `INSUFFICIENT_28_DAY_HISTORY`.
4. `PredictionRun.prediction_horizon` is a lexically sorted comma-joined list for
   multi-horizon batches (`"w24,w4"`). Consumers that expect a single canonical
   horizon token should be checked before multi-horizon batches are enabled.
5. `tests/integration/test_forecastops_postgresql_sequence.py` skips silently
   without `INTAKE_TEST_DATABASE_URL`. It runs in CI; a local run needs the DSN.

## Verification

Run from a checkout of the corrected PR head:

```bash
python3 -m pytest modules/forecastops/tests \
  tests/integration/test_forecastops_postgresql_sequence.py \
  tests/integration/test_forecastops_train_runtime_contract.py \
  tests/integration/test_forecastops_tenant_runtime_contract.py \
  tests/integration/test_forecastops_alerts.py -q
python3 -m pytest -m "not requires_live_env" -q tests/integration modules
python3 -m ruff check tests modules apps shared models solver pipelines infra
```

Local results (2026-07-27):

- Forecast targeted suite: 44 passed, 8 skipped. The skips are
  `statsforecast`, `mlforecast`, and the PostgreSQL DSN, none of which is
  available in this sandbox; `mlflow` and `lightgbm` were installed so the
  train-to-runtime contract test executes rather than skipping.
- `ruff check tests modules apps shared models solver pipelines infra`: passed.
- Both new regression tests fail on `957234d8` and pass on the corrected head.

Repository CI must be re-confirmed green on the corrected exact head before this
lane merges. `product-e2e-gate` was the failing check on `957234d8`; defect C is
the fix for it.
