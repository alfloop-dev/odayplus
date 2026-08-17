# Implementation Report: ODP-CAP-LEARNINGHUB-OPERATIONS-001

## Overview
This task makes the LearningHub Data Quality (DQ) triage and Model Registry operator workflow fully operable with role gating and audit capabilities without requiring a production model to exist upfront.

## Delivered Capabilities & Acceptance Verification

1. **DQ actions persist actor time and rationale**
   - Implemented `DqTriageRecord` domain model in `modules/learninghub/domain/dataset_snapshot.py` containing `triage_id`, `dataset_snapshot_id`, `action`, `actor`, `rationale`, `time`, and `audit_event_id`.
   - Added `save_dq_triage` and `list_dq_triages` methods to `LearningHubRepository` protocol, `InMemoryLearningHubRepository`, and `DurableLearningHubRepository` (persisting to SQLite document store across process restarts).
   - Added `record_dq_triage` service method in `LearningHubService` (`modules/learninghub/application/release.py`) which enforces mandatory rationale, binds actor identity, records audit event `learninghub.dq_triage_recorded.v1` exactly once with request correlation ID, and returns the persisted `DqTriageRecord`.
   - Added REST endpoints `POST /learninghub/dataset-snapshots/{dataset_snapshot_id}/triage` and `GET /learninghub/dataset-snapshots/{dataset_snapshot_id}/triage` in `apps/api/app/routes/learninghub.py`.

2. **Model and DQ operations are role gated (B1 fix)**
   - Configured least-privilege `data_quality` permissions in `shared/auth/rbac.py`: granted `("data_quality", Action.UPDATE)` and `("data_quality", Action.VIEW)` to `Role.DATA_OWNER`, and `("data_quality", Action.VIEW)` to `Role.AUDITOR`.
   - Guarded `POST /learninghub/dataset-snapshots/{id}/triage` with `require_permission("data_quality", Action.UPDATE)` and `GET /learninghub/dataset-snapshots/{id}/triage` with `require_permission("data_quality", Action.VIEW)`.
   - Applied `require_permission` auth dependencies across Model Registry endpoints (`model` resource with `Action.CREATE`, `Action.VIEW`, `Action.APPROVE`, `Action.PUBLISH`, `Action.UPDATE`).
   - Verified that `DATA_OWNER` succeeds (201 Created), while `MODEL_OWNER`, `RELEASE_OWNER`, `EXPANSION_USER`, and `AUDITOR` callers fail closed with HTTP 403 Forbidden.

3. **Single audit event provenance (B2 fix)**
   - Passed request correlation ID (`request.state.correlation_id`) from FastAPI route into `service.record_dq_triage(...)`.
   - Removed duplicate audit logging in route handler; audit event `learninghub.dq_triage_recorded.v1` is recorded exactly once in the service layer, binding `audit_event_id` directly on the returned `DqTriageRecord`.

4. **Empty registry never fabricates a model**
   - Verified that querying empty model repositories (`GET /learninghub/models`, `list_all_model_versions`, `build_model_registry_evidence`) returns empty result sets (`count: 0`, `items: []`) and never fabricates mock or default models.

5. **Unsupported promotion fails closed**
   - Enforced strict gate boundaries in `request_release` for unsupported release types, missing preconditions (`expected_release_revision`, `idempotency_key`), missing `rollback_target` on FULL releases, self-review prohibitions (`requested_by` == `approved_by`), and missing model card approvals.

6. **Lifecycle, permission, and durable restart persistence tests delivered**
   - Delivered test suite in `tests/integration/test_learninghub_operations_acceptance.py` verifying direct DQ triage, durable SQLite restart persistence, single-event audit provenance, positive/negative RBAC role gating, empty registry invariants, and fail-closed promotions.

## Code Changes Summary
- `shared/auth/rbac.py`: Granted `data_quality` `UPDATE` & `VIEW` to `DATA_OWNER` and `VIEW` to `AUDITOR`.
- `modules/learninghub/domain/dataset_snapshot.py`: Added `audit_event_id` field to `DqTriageRecord`.
- `modules/learninghub/application/release.py`: Updated `record_dq_triage` to record single audit event, bind `audit_event_id`, and accept request `correlation_id`.
- `apps/api/app/routes/learninghub.py`: Updated triage routes to use `data_quality` resource permissions and pass `request.state.correlation_id`.
- `shared/infrastructure/persistence/repositories.py`: `DurableLearningHubRepository` persists `DqTriageRecord` with `audit_event_id` into SQLite.
- `tests/integration/test_learninghub_operations_acceptance.py`: Acceptance test suite covering all criteria, B1 RBAC role tests, B2 single-event audit provenance, and durable restart persistence.
