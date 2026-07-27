# Learning Hub Module

Learning Hub and MLOps lifecycle module.

Implemented surfaces:

- Dataset snapshot registration from model-ready rows with point-in-time checks.
- MLflow-style registry adapter for model versions, stages, and aliases.
- Release controller for shadow, canary, full production promotion, rollback,
  and governed alias reconciliation.
- Durable release saga with restart recovery plus in-memory, SQLite, and
  PostgreSQL repository implementations.

Release gates enforced by `LearningHubService`:

- Dataset snapshot must exist and remain reproducible by ID.
- Validation run must pass configured metric thresholds.
- Model card must be complete and approved.
- Full/canary releases require a rollback target.
- Every command binds an expected release revision and idempotency key.
- Release intent is committed before any remote MLflow mutation.
- Release and rollback actions emit audit events and update registry aliases.
- Rollback commands must name the current production version and resolve an
  approved prior target.

## Production Scope

The canonical production model registry is global. Model names, aliases,
release revisions, advisory locks, idempotency records, and saga intents are
all keyed by global `model_name`; they are not tenant-isolated namespaces.
Callers must send `release_scope=global` and must not send a tenant ID.
Tenant-scoped release commands fail closed.

Startup and operator recovery use
`LearningHubService.recover_incomplete_releases()` (or the worker recovery
entry point). A saga with a committed receipt is finalized; every earlier
orphaned phase is deterministically compensated from its persisted snapshots.

Focused evidence lives in `tests/integration/test_learninghub_release.py`.
