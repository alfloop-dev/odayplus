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
- `approved_by` must name an approver recorded on the model card in a
  `model-review-board` or `model-risk-owner` role, and must differ from
  `requested_by`; self-review is rejected before the CAS boundary.
- The release target's own model card and validation run are re-checked at
  execution time, so a rollback target is held to the same gate as a promotion.

## Release Actor Identity

`requested_by` is the authenticated principal, never a body-supplied string.
`POST /learninghub/releases` binds it from `request.state.operator_principal`
(established by the auth boundary) and rejects a request that carries a
different `requested_by` (`UNTRUSTED_RELEASE_ACTOR`) or that names the caller as
its own approver (`MODEL_RELEASE_SELF_REVIEW`). The release worker refuses a
queued payload that omits either actor rather than defaulting one.

## Governed MLflow Projection

Each released model version carries its governance facts in
`ModelVersion.monitoring_config`, projected onto MLflow model-version tags under
`oday.model_version.*`: `release_id`, `release_revision`, `approval_id`,
`release_scope`, `requested_by`, `release_approved_by`, `model_card_checksum`,
`validation_run_id`, and `validation_status`. A release verifies the
runtime-visible projection after writing it and after the alias moves; a missing
or disagreeing tag fails the release closed.

Compensation restores those tags from the pre-release snapshot along with stages
and aliases, so a failed release never leaves MLflow advertising an approval
that was never committed. A metadata restore that cannot be verified is recorded
as `COMPENSATION_FAILED` for operator repair rather than being ignored.

## HTTP Contract

`POST /learninghub/releases` answers:

| Status | Condition |
|---|---|
| 201 | release committed (an idempotent replay returns the same receipt) |
| 403 | actor is unauthenticated, spoofed, or self-approving |
| 409 | stale `expected_release_revision`, another active release, idempotency key reused with a different command, or a non-terminal saga awaiting recovery |
| 428 | `expected_release_revision` or `idempotency_key` missing |
| 422 | domain rejection (unknown version, failed gate, tenant-scoped command) |

## Production Scope

The canonical production model registry is global. Model names, aliases,
release revisions, advisory locks, idempotency records, and saga intents are
all keyed by global `model_name`; they are not tenant-isolated namespaces.
Callers must send `release_scope=global` and must not send a tenant ID.
Tenant-scoped release commands fail closed.

## Recovery Lease and Fencing

Startup and operator recovery use
`LearningHubService.recover_incomplete_releases()` (or the worker recovery
entry point). A saga with a committed receipt is finalized; every earlier
orphaned phase is deterministically compensated from its persisted snapshots.

Recovery is lease-gated. The worker driving a release holds an execution lease
(`lease_owner` / `lease_expires_at`, renewed on every saga write and released at
a terminal state), so a peer's startup recovery skips a release that is still
live and records a `learninghub.model_release_recovery.v1` audit event instead of
compensating it. Taking over an expired lease bumps the saga's `fence_token`;
every repository rejects a later write carrying a stale token
(`LearningHubReleaseFenced`), so a resumed worker fails closed instead of racing
the recovery owner. `take_over_live_leases=True` is the explicit operator
override for a worker known to be dead before its lease expires.

Focused evidence lives in `tests/integration/test_learninghub_release.py` and,
for durable PostgreSQL 16 release plus lease-fenced recovery, in
`tests/integration/test_learninghub_postgresql_release.py`
(`INTAKE_TEST_DATABASE_URL`).
