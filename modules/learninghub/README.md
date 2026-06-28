# Learning Hub Module

Learning Hub and MLOps lifecycle module.

Implemented surfaces:

- Dataset snapshot registration from model-ready rows with point-in-time checks.
- MLflow-style registry adapter for model versions, stages, and aliases.
- Release controller for shadow, canary, full production promotion, and rollback.
- In-memory repository and worker entry point for integration tests and early workflows.

Release gates enforced by `LearningHubService`:

- Dataset snapshot must exist and remain reproducible by ID.
- Validation run must pass configured metric thresholds.
- Model card must be complete and approved.
- Full/canary releases require a rollback target.
- Release and rollback actions emit audit events and update registry aliases.

Focused evidence lives in `tests/integration/test_learninghub_release.py`.
