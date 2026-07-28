# ODP-PRODUCTION-MODEL-REGISTRY-001 runtime evidence

On 2026-07-28 the task-owned Cloud Run inventory job was rebound away from
the forbidden legacy resources. Its deployed generation was verified before
execution to use only:

- Cloud SQL `alfaloop-data-project:asia-east1:oday-dev-sql`
- Secret Manager `oday-plus-dev-api-database-url-pg16:latest`

Execution `oday-production-model-registry-001-inventory-pmb8m` succeeded and
reported exactly 1,303 labeled and eligible ForecastOps rows from
`model_ready.forecast_training_view` contract
`forecast-training-view-v2`. The redacted aggregate result is preserved in
`pg16_forecast_inventory.json`.

Training candidate `2026.07.28.1` was then attempted over the complete
observed half-open range, with a hard cap of 1,303 rows and the same verified
binding. Execution `oday-production-model-registry-001-train-2dzlg` exited 2
without registering a releasable candidate:

> forecastops: daily forecast rows do not contain a complete canonical horizon window

This is the required fail-closed behavior. The canonical ForecastOps contract
trains 1-, 2-, and 4-week targets from complete per-store daily windows
(7/14/28 consecutive dates). The eligible PG16 population currently spans
only four calendar dates, 2026-06-19 through 2026-06-22, so it cannot produce
even the shortest horizon sample.

No fixture, mock, synthetic, auto-seeded, research-only, or legacy-bound data
was substituted. No production alias was created or changed by the failed
training execution. Production release remains blocked until authoritative
eligible daily history contains complete canonical horizon windows.
