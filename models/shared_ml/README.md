# Shared ML

Shared feature, validation, model-card, and registry utilities.

Implemented primitives:

- `ModelVersion`, `ModelStage`, and `ModelAlias` for MLflow-style registry state.
- `ValidationRun`, metric thresholds, segment metrics, and failed-rule output.
- `ModelCard` with approval, risk, intended-use, rollback-condition, and review fields.

These objects are intentionally storage-neutral so Learning Hub can adapt them to
MLflow, Vertex AI, Cloud Storage artifacts, or in-memory tests without changing
domain contracts.
