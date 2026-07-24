# Production Model Training and Release

`scripts.models.release` turns canonical PostgreSQL model-ready views into
bounded, immutable GCS dataset snapshots and governed MLflow model versions.
It never creates training rows. Missing views, realized labels, eligible rows,
temporal/segment validation, GCS, MLflow, exact commit lineage, or independent
approval stop the command with exit code `2`.

## Install the model-ready contract

`sql/model_ready_views.sql` is the versioned PostgreSQL artifact that closes
the data-plane-to-training boundary. Run it only after canonical migrations and
the data-plane backfill:

```bash
export ODAY_DATABASE_URL='postgresql://...remote production database...'
python -m scripts.models.install_views inventory
python -m scripts.models.install_views install
```

`inventory` is read-only. `install` validates all required `core.transactions`,
`core.stores`, `data_plane.canonical_lineage`, and `data_plane.ingestion_runs`
columns, takes a transaction-scoped advisory lock, applies the SQL, records its
SHA-256, and verifies the registered Forecast view/version. The database URL is
read from the environment and local, SQLite, file, and placeholder resources
are rejected.

The Forecast view uses only persisted successful TWD transactions. Its daily
label is the actual sum of `net_amount`; lag and rolling features use prior
calendar dates only. Rows require complete source-snapshot lineage, completed
ingestion runs, 28 daily history rows, a mature label, tenant/store scope, and
`feature_snapshot_time < prediction_origin_time`.

## Model bindings

| Key | View | Required realized label | Engine |
|---|---|---|---|
| `forecastops` | `model_ready.forecast_training_view` | `daily_net_revenue` | LightGBM quantile |
| `avm` | `model_ready.valuation_view` | `realized_transaction_price` | LightGBM quantile |
| `sitescore` | `model_ready.candidate_site_view` | `realized_site_success` | CatBoost |
| `avm-liquidity` | `model_ready.avm_liquidity_training_view` | `duration_days` + `sold` | lifelines CoxPH |

AVM, SiteScore, and AVM liquidity are intentionally non-trainable until their
canonical outcome relations exist and can expose mature realized outcomes.
The installer registers each missing contract as `BLOCKED` and does not create
an empty or inferred outcome view. `asset.valuation_runs`, SiteScore
recommendations, fixture constants, and current predictions are not accepted
as labels.

## Runtime inputs

```text
ODAY_DATABASE_URL          remote PostgreSQL canonical + LearningHub state
MLFLOW_TRACKING_URI        HTTPS remote MLflow registry
ODP_MODEL_ARTIFACT_ROOT    dedicated gs:// bucket/prefix
ODP_RELEASE_COMMIT_SHA     exact training source commit
ODP_MODEL_TRAINING_ACTOR   attributable requester identity
```

Credentials are supplied through workload identity and Secret Manager. They
must not be passed as CLI flags or stored in approval JSON.

## Dry-run inventory

This verifies the installed view registry/version, relation metadata, realized
label contract, and aggregate row counts only; it does not create snapshots,
runs, artifacts, or aliases.

```bash
python -m scripts.models.release inventory --model all
python -m scripts.models.release inventory --model forecastops
```

The `all` command remains non-zero while any binding lacks its realized label.

## Bounded training

```bash
python -m scripts.models.release train \
  --model forecastops \
  --version 2026.07.24.1 \
  --start 2025-07-01T00:00:00Z \
  --end 2026-07-01T00:00:00Z \
  --max-rows 100000
```

The command:

1. verifies the allowlisted view and required columns;
2. reads only eligible, labeled rows inside the supplied half-open time range;
3. drops incomplete feature rows and enforces a hard row cap;
4. creates a deterministic PIT-safe dataset snapshot and immutable GCS bytes;
5. runs an out-of-time holdout and minimum-size segment gates;
6. trains the actual configured OSS estimator through the shared training
   pipeline;
7. stores model, feature, validation, temporal-validation, model-card, and
   lineage artifacts with SHA-256 evidence;
8. registers a `DEV` candidate in remote MLflow without assigning a production
   alias.

## Governed promotion

Promotion is a separate command. The approval is version-bound, attributable,
time-stamped, and must come from a different actor.

```json
{
  "approval_id": "MRB-2026-0017",
  "model_name": "forecast_revenue_interval",
  "model_version": "2026.07.24.1",
  "decision": "approved",
  "approver": "reviewer@example.invalid",
  "role": "model-review-board",
  "approved_at": "2026-07-24T12:00:00Z",
  "release_type": "shadow",
  "reason": "Temporal and segment validation accepted"
}
```

```bash
python -m scripts.models.release promote \
  --model forecastops \
  --version 2026.07.24.1 \
  --approval-file /secure-input/model-approval.json
```

`CANARY` and `FULL` also require `--rollback-target` naming an already
registered approved version. A first release can therefore enter `SHADOW`, but
cannot jump directly to production without a rollback candidate.
