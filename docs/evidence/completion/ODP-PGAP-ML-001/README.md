# ODP-PGAP-ML-001 Evidence

Task: Implement production model lifecycle

## Runtime Surfaces

- Feature pipeline: `pipelines/features/model_features.py`
  - Loads a durable Learning Hub dataset snapshot.
  - Emits a stable, content-addressed feature artifact.
  - Re-running the same snapshot and feature schema produces the same artifact version and digest.
- Training pipeline: `pipelines/training/model_training.py`
  - Builds deterministic model bytes from the dataset snapshot and feature artifact digest.
  - Persists model and validation-report artifacts.
  - Produces calibrated validation metrics and segment acceptance gates.
- Learning Hub lifecycle:
  - Release decisions now bind dataset snapshot, feature schema version, label version, model card checksum, artifact URI, approval, rollback target, requester, approver, and audit event.
  - Drift and outcome monitoring persist retraining requests with `auto_promotion=false`.
  - Shadow/canary same-input comparisons persist champion/challenger predictions, deltas, tolerance, and rollback recommendation.
  - Rollback from a failed comparison still requires a governed `ROLLBACK` release decision.

## Verification Run

Verification run locally in this worktree:

```bash
python3 -m ruff check models/shared_ml modules/learninghub pipelines/features pipelines/training shared/infrastructure/persistence/repositories.py tests/integration/test_production_model_lifecycle.py
uv run pytest tests/integration/test_production_model_lifecycle.py -q
python3 -m ruff check models pipelines modules/learninghub apps/worker tests
uv run pytest tests/integration tests/contract -q
npm ci
npm run typecheck --workspace=@oday-plus/web
git diff --check origin/dev...HEAD
```

Result:

- Focused ruff: passed.
- Focused pytest: `3 passed`.
- Acceptance ruff: passed.
- Acceptance integration/contract pytest: passed.
- Web typecheck: passed after `npm ci` installed lockfile dependencies.
- Diff whitespace check: passed.

## Coverage Map

- Restart safety: durable SQLite repository is closed and rebuilt before reading aliases, artifacts, retraining requests, inference comparisons, rollback decisions, and audit events.
- Reproducibility: feature artifact version and digest are identical for repeated runs over the same durable dataset snapshot.
- Rejection: failed segment acceptance gate blocks governed release.
- Drift trigger: drift monitoring breach creates a durable retraining request without promotion.
- Outcome ingestion: outcome monitoring breach creates a durable retraining request without promotion.
- Comparison: canary inference compares challenger and champion on the same input IDs and persists deltas.
- Rollback: comparison-triggered rollback goes through `request_release(..., ROLLBACK, ...)`, restores production alias to the rollback target, and marks the challenger `rolled_back`.
