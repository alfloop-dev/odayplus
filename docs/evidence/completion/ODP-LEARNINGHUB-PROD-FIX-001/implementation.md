# ODP-LEARNINGHUB-PROD-FIX-001 · LearningHub MLflow release integrity and authorization closure

- Task: ODP-LEARNINGHUB-PROD-FIX-001 (AI Runtime Fix and Review phase)
- Owner: Codex7 · Reviewer: Codex9
- Reviewed source: PR `#384`, exact commit
  `957234d8fbe40586a306c9c3d77388edcb16c899` (branch `task/ODP-LIVE-RUNTIME-002`)
- Lane ledger: `docs/evidence/fleet_dispatch/ODP-LIVE-RUNTIME-002.md`
  (`ODP-LEARNINGHUB-RELEASE-001`, second review returned `CHANGES_REQUESTED`)

This branch is based on `957234d8` and changes only the LearningHub release
surface plus the release-saga fencing invariant it needs from the shared durable
repository. ForecastOps, auth, and the rest of PR #384 are untouched.

## Findings and fixes

### 1. MLflow metadata failure was not compensated

`_execute_release_saga` wrote approval, approval-id, rollback-target, and
monitoring metadata to MLflow through `sync_release_metadata` *before* the alias
phase, but compensation (`_restore_release_state`) only restored stages, durable
model versions, and aliases. A failed release therefore left MLflow advertising
an approval that was never committed, and any runtime resolving the model from
MLflow tags read uncommitted governance state.

Fix: `MlflowRegistryAdapter.restore_release_metadata()` rewrites the durable
pre-release snapshot's full tag set and verifies the restored projection
(approval, approval time, rollback target, monitoring config). Compensation
calls it for every version snapshot and records a verification failure as a
compensation error, so the saga ends `COMPENSATION_FAILED` (fails closed for
operator repair) instead of drifting silently.

Evidence: `test_release_metadata_compensation_restores_remote_governance_tags`
(fails without the fix — verified by removing the restore call).

### 2. Recovery could compensate a live worker's release

`recover_incomplete_releases` compensated every non-terminal saga. Release
execution runs outside the CAS guard, so a peer's startup recovery could
compensate a release another process was actively driving, and the original
worker would then keep writing into compensated state.

Fix: sagas carry an execution lease (`lease_owner`, `lease_expires_at`) renewed
on every state write and released at a terminal state, plus a monotonic
`fence_token`. Recovery skips a saga whose lease is live and owned by another
worker (auditing `learninghub.model_release_recovery.v1` /
`lease_held_by_live_worker`); taking an expired lease over bumps the fence token,
and `assert_release_saga_fence` — enforced by both the in-memory and durable
repositories — rejects any later write from the fenced owner
(`LearningHubReleaseFenced`). `take_over_live_leases=True` is the explicit
operator override for a worker known to be dead.

Evidence: `test_recovery_skips_live_lease_and_fences_the_stale_owner` (fails
without the fix) and, on PostgreSQL,
`test_postgresql16_full_mlflow_release_and_lease_fenced_recovery`.

### 3. Actor and approval identity came from the request body

`POST /learninghub/releases` took `requested_by` and `approved_by` from the JSON
body, so any authorized caller could attribute a release to another identity and
approve their own promotion; the service accepted any `approved_by` string and
never bound it to a recorded approval.

Fix: the route binds `requested_by` (and the monitor's `evaluated_by`, and the
dataset/model registration audit actors) from `request.state.operator_principal`
established by the auth boundary, rejecting a mismatched body value
(`UNTRUSTED_RELEASE_ACTOR`, 403) and a self-approving caller
(`MODEL_RELEASE_SELF_REVIEW`, 403). `LearningHubService._assert_release_authority`
requires an approver that is recorded on the model card in a
`model-review-board` / `model-risk-owner` role and differs from the requester —
the same rule `scripts/models/contracts.require_approval_document` already
applied to the CLI path. The release worker refuses a queued payload that omits
either actor instead of defaulting one.

Evidence: `test_release_requires_an_independent_recorded_approver`,
`test_release_worker_requires_revision_and_idempotency_binding`,
`test_release_api_binds_actors_and_matches_the_409_428_contract`.

### 4. Model card, validation, and approval tags were not enforced

Released versions carried only `release_id` / `release_revision` /
`approval_id` in `monitoring_config`, and nothing verified what MLflow actually
stored. A production alias could resolve to a version whose model card and
validation run were unknown to the runtime.

Fix: `_release_governance_metadata` re-validates the *release target's* own
model card and validation run (so a rollback target is held to the promotion
gate) and stamps `release_id`, `release_revision`, `approval_id`,
`release_scope`, `requested_by`, `release_approved_by`, `model_card_checksum`,
`validation_run_id`, `validation_status`. The adapter projects them as
`oday.model_version.*` tags, `sync_release_metadata` verifies them at write
time, and `assert_release_governance` re-checks tags *and* alias resolution
after the alias phase. A missing or disagreeing tag fails the release closed
into compensation.

Evidence: `test_production_release_publishes_model_card_validation_and_approval_tags`,
`test_release_fails_closed_when_required_governance_tags_are_not_projected`.

### 5. API 409 / 428 contract did not match the implementation

Every domain rejection returned 422, so a lost CAS race, an active release, and
a replayed idempotency key were indistinguishable from a validation error, and a
caller omitting a precondition got a schema 422 with no contract meaning.

Fix: `LearningHubConflictError` (409) and `LearningHubPreconditionRequiredError`
(428) are raised by the service and mapped by the route;
`expected_release_revision` and `idempotency_key` became schema-optional so a
missing precondition answers 428 `RELEASE_PRECONDITION_REQUIRED` rather than a
schema error. The contract is documented in `modules/learninghub/README.md`.

Evidence: `test_release_api_binds_actors_and_matches_the_409_428_contract`.

### 6. PostgreSQL test did not exercise a release

`test_learninghub_postgresql_release.py` only exercised the advisory-lock CAS
boundary and a saga row rewrite; no release, no MLflow, no recovery.

Fix: `test_postgresql16_full_mlflow_release_and_lease_fenced_recovery` drives a
full governed promotion through the MLflow adapter on PostgreSQL 16 durable
state (model versions, cards, aliases, sagas, revisions, WORM audit chain),
asserts the governance tags and alias projection, crashes a second release after
`MODEL_STATE_APPLIED`, proves a second process skips the live lease, then takes
the expired lease over — asserting compensated aliases, stages, MLflow metadata,
a bumped fence token, the fenced original owner, and durable state after a
restart.

## Verification

Run from this branch (`957234d8` + this task's commits):

```
python3 -m pytest tests/integration/test_learninghub_release.py \
  tests/integration/test_learninghub_model_list_api.py \
  tests/integration/test_model_registry_artifacts.py modules/learninghub/tests -q
# 40 passed

INTAKE_TEST_DATABASE_URL=postgresql://…@127.0.0.1:55432/oday_plus \
python3 -m pytest tests/integration/test_learninghub_postgresql_release.py -q
# 2 passed (real PostgreSQL 16 container: SHOW server_version_num asserted 16.x)

python3 -m pytest -m "not requires_live_env" tests modules apps shared models -q
# see § Repository suite below

python3 -m ruff check .
```

Codex6 independently re-ran the task-scoped verification on 2026-07-27:

```
pytest -q tests/integration/test_learninghub_release.py \
  modules/learninghub/tests tests/integration/test_model_registry_artifacts.py
# passed

INTAKE_TEST_DATABASE_URL=postgresql://…@127.0.0.1:55432/oday_plus \
pytest -q tests/integration/test_learninghub_postgresql_release.py
# 2 passed against the PostgreSQL 16 task container

python3 -m ruff check apps/api/app/routes/learninghub.py \
  modules/learninghub tests/integration/test_learninghub_release.py \
  tests/integration/test_learninghub_postgresql_release.py \
  tests/integration/test_model_registry_artifacts.py
# All checks passed
```

Mutation checks (each new regression test was confirmed to fail without its
fix): removing the compensation metadata restore fails
`test_release_metadata_compensation_restores_remote_governance_tags`; removing
the recovery lease check fails
`test_recovery_skips_live_lease_and_fences_the_stale_owner`.

## Repository suite

The repository suite was run at `957234d8` (baseline) and on this branch with
the same command and environment. The local sandbox lacks `cvxpy`, `dagster`,
and `statsmodels`, so those three collection-error modules are excluded in both
runs; CI installs them.

Pre-existing failures at `957234d8` (unchanged by this task): point-in-time
snapshot maturity, model-ready materialization, OSS execution flow (4),
OSS capability API, production model runtime, Evidently monitor (2).

The only suite this task broke and repaired is
`tests/integration/test_model_registry_artifacts.py`, whose release helper now
names an independent recorded approver.

## Latest-dev composition

Codex7 composed `dev` commit `1c7dd935` (including merged dependency PR #436)
into the task branch without conflicts.
The resulting diff against `dev` remains limited to LearningHub, its required
shared auth/durable-repository integration points, and task-scoped tests and
evidence.

Verification after composition:

```
python3 -m ruff check apps/api/app/routes/learninghub.py \
  modules/learninghub shared/auth/rbac.py \
  shared/infrastructure/persistence/repositories.py \
  tests/integration/_learninghub_fixtures.py \
  tests/integration/test_learninghub_release.py \
  tests/integration/test_learninghub_postgresql_release.py \
  tests/integration/test_model_registry_artifacts.py
# All checks passed

python3 -m pytest -q tests/integration/test_learninghub_release.py \
  modules/learninghub/tests tests/integration/test_model_registry_artifacts.py
# passed

INTAKE_TEST_DATABASE_URL=postgresql://…@127.0.0.1:55432/… \
python3 -m pytest -q tests/integration/test_learninghub_postgresql_release.py
# 3 passed, 0 skipped (PostgreSQL 16)

make api-contract
# PASS: 4 additive, 3 approved breaking, 0 unapproved breaking

uv run pytest -q tests/integration/test_learninghub_release.py \
  tests/integration/test_learninghub_postgresql_release.py
# 30 passed, 3 skipped (PostgreSQL URL not configured for this local rerun)
```
