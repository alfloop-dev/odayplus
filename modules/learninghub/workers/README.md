# Workers

Release jobs require:

- `expected_release_revision`
- `idempotency_key`
- `release_scope=global`

`LearningHubReleaseWorker.recover_releases()` is the startup/operator recovery
entry point for durable release sagas left incomplete by process termination.
