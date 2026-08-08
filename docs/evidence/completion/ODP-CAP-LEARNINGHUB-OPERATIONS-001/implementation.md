# Implementation Report: ODP-CAP-LEARNINGHUB-OPERATIONS-001

## Overview
This task makes the LearningHub Data Quality (DQ) triage and Model Registry operator workflow fully operable with role gating and audit capabilities without requiring a production model to exist upfront.

## Delivered Capabilities & Acceptance Verification

1. **DQ actions persist actor time and rationale**
   - Implemented `DqTriageRecord` domain model in `modules/learninghub/domain/dataset_snapshot.py`.
   - Added `save_dq_triage` and `list_dq_triages` methods to `LearningHubRepository` protocol, `InMemoryLearningHubRepository`, and `DurableLearningHubRepository`.
   - Added `record_dq_triage` service method in `LearningHubService` (`modules/learninghub/application/release.py`) which enforces mandatory rationale, binds actor identity, and records audit event `learninghub.dq_triage_recorded.v1`.
   - Added REST endpoints `POST /learninghub/dataset-snapshots/{dataset_snapshot_id}/triage` and `GET /learninghub/dataset-snapshots/{dataset_snapshot_id}/triage` in `apps/api/app/routes/learninghub.py`.

2. **Model operations are role gated**
   - Applied `require_permission` auth dependencies across all Model Registry endpoints in `apps/api/app/routes/learninghub.py` (`model` resource with `Action.CREATE`, `Action.VIEW`, `Action.APPROVE`, `Action.PUBLISH`, `Action.UPDATE`).
   - Unauthenticated or unauthorized callers fail closed with HTTP 401/403.

3. **Empty registry never fabricates a model**
   - Verified that querying empty model repositories (`GET /learninghub/models`, `list_all_model_versions`, `build_model_registry_evidence`) returns empty result sets (`count: 0`, `items: []`) and never fabricates mock or default models.

4. **Unsupported promotion fails closed**
   - Enforced strict gate boundaries in `request_release` for unsupported release types, missing preconditions (`expected_release_revision`, `idempotency_key`), missing `rollback_target` on FULL releases, self-review prohibitions (`requested_by` == `approved_by`), and missing model card approvals.

5. **Lifecycle and permission tests delivered**
   - Comprehensive test suite in `tests/integration/test_learninghub_operations_acceptance.py` and `tests/integration/test_learninghub_release.py` verifying DQ triage, role gating, empty registry invariants, fail-closed promotions, and full release lifecycle.

## Code Changes Summary
- `modules/learninghub/domain/dataset_snapshot.py`: Added `DqTriageRecord` dataclass.
- `modules/learninghub/domain/__init__.py`: Exported `DqTriageRecord`.
- `modules/learninghub/infrastructure/repositories.py`: Added `save_dq_triage` & `list_dq_triages` to `LearningHubRepository` protocol and `InMemoryLearningHubRepository`.
- `shared/infrastructure/persistence/repositories.py`: Added `_DQ_TRIAGE` store collection and `save_dq_triage` & `list_dq_triages` to `DurableLearningHubRepository`.
- `modules/learninghub/application/release.py`: Added `record_dq_triage` to `LearningHubService` with audit logging.
- `apps/api/app/routes/learninghub.py`: Added `DqTriagePayload`, `POST /learninghub/dataset-snapshots/{dataset_snapshot_id}/triage` and `GET /learninghub/dataset-snapshots/{dataset_snapshot_id}/triage`.
- `tests/integration/test_learninghub_operations_acceptance.py`: Acceptance test suite covering all 5 criteria.
