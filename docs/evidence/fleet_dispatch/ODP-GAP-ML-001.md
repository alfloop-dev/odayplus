# ODP-GAP-ML-001 ML Governance Foundation Worker Evidence

Recorded: 2026-07-11
Worker lane: Antigravity
Scope: Feature/Label Registries, Model Registry Integration, Content-Addressed Artifact Store, Model Cards, Validation, Release, Rollback, and Durable Persistence.

## Objective

Implement the ML governance foundation to connect the model lifecycle (Learning Hub) to durable artifact and model registry storage with auditable evidence, instead of session-only in-memory state.

## Implementation Details

The implementation spans the following primary components:

### 1. Feature & Label Registry
- **File**: `models/shared_ml/registry.py` and `modules/learninghub/`
- **Classes**: `FeatureDefinition`, `LabelDefinition`, `FeatureSet`, `LabelSet`
- **Validation**: Ensures dataset snapshots correctly bind feature sets and label sets.
- **Fail-Closed**: Accessing or using `BLOCKED` feature definitions throws `LearningHubError` to prevent bad data from reaching downstream modeling processes.

### 2. Model Cards & Validation
- **Files**: `models/shared_ml/model_card.py` and `models/shared_ml/validation.py`
- **Classes**: `ModelCard`, `ModelCardApproval`, `MetricThreshold`, `SegmentMetric`, `ValidationRun`
- **Governance links**: Model cards carry dataset snapshot links, feature/label set IDs, policy/version, owner, approvals, and rollback conditions.
- **Validation evaluation**: Performs warning/failure threshold check on metrics. Releasing without passed validation raises a `LearningHubError`.

### 3. Content-Addressed Artifact Store
- **File**: `models/shared_ml/artifact_store.py`
- **Class**: `DurableArtifactStore` (and `InMemoryArtifactStore`)
- **Address format**: Digests bytes using SHA-256 and returns a content-bound URI of the form `odp-artifact://sha256/<hex>`.
- **Integrity proof**: `verify(artifact_id)` re-hashes the stored bytes to prove the model artifact was not tampered with.

### 4. Durable Registry Persistence
- **File**: `shared/infrastructure/persistence/repositories.py`
- **Classes**: `DurableLearningHubRepository`
- **Seam**: Reuses `SqliteDocumentStore` and the generic aggregate table `durable_documents` (under `infra/db/migrations/000002_durable_e2e_persistence.sql`). Model versions, model cards, validation runs, release decisions, and artifact records survive process restarts.
- **Registry Evidence Builder**: `build_model_registry_evidence` builds a JSON-serializable manifest recording model stages, aliases, metrics, validation status, model cards, and content digests.

### 5. Release & Rollback Workflow
- **File**: `modules/learninghub/application/release.py` and `modules/learninghub/` entrypoint
- **Classes**: `ReleaseType` (SHADOW, FULL, ROLLBACK), `run_learninghub_release`
- **Traceability**: All releases and rollbacks write correlation-indexed audit events via `DurableAuditLog`.

---

## Verification Evidence

Focused verification was successfully performed to assert the durability, validation, and fail-closed characteristics of the registry:

### 1. Integration Tests
```bash
uv run pytest tests/integration/test_feature_label_registry.py \
  tests/integration/test_learninghub_release.py \
  tests/integration/test_model_registry_artifacts.py
```
- **Result**: `11 passed` in `0.56s`
- **Coverage**:
  - `test_feature_registry_lifecycle` & `test_label_registry_lifecycle` prove CRUD and approval transitions.
  - `test_dataset_snapshot_binding_validation` & `test_dataset_snapshot_blocked_status` verify snapshot binding and block rules.
  - `test_full_lifecycle_promote_rollback_survives_restart` proves shadow -> promote -> rollback transitions survive a simulated engine restart.
  - `test_model_card_carries_required_links` checks model card integrity.
  - `test_artifact_content_addressing_and_tamper_evidence` verifies SHA-256 content hashes, idempotency, and tamper detection.
  - `test_registry_evidence_manifest_is_audit_complete` checks JSON-serializable manifest correctness.

### 2. Lint and Format Checks
```bash
uv run ruff check models/shared_ml shared/domain/ shared/infrastructure/persistence/ tests/integration/
```
- **Result**: `All checks passed!`

---

## Fail-Closed Status
- **Validation failures**: Any release requests for candidates failing validation or missing model cards are rejected with `LearningHubError`.
- **Blocked feature definitions**: Registrations of dataset snapshots containing `BLOCKED` features are automatically rejected.
- **Tamper evidence**: Modifying stored artifact weights results in verification failure during integrity checks.
