# ODP-MODELREADY-A4-REMEDIATION-001 Completion Evidence

## Task Summary
- **Task ID**: `ODP-MODELREADY-A4-REMEDIATION-001`
- **Title**: 補齊 ModelReady 缺值 production entry 與持久化驗證
- **Owner**: Antigravity4
- **Reviewer**: Codex
- **Target Branch**: `task/ODP-MODELREADY-A4-REMEDIATION-001`
- **Base Branch**: `dev`

## Purpose & Scope
This task provides forward remediation for the A4 acceptance requirement originally declared in `ODP-MODELREADY-QUALITY-NULLABLE-001`. It verifies that when `ModelReadyRecord` datasets missing `data_quality_score` or `confidence` (or carrying explicit `None`/`null` values) enter via real production entrypoints, they are rejected with field- and record-identifying diagnostic messages, and leave no durable or partial snapshot records in storage.

### Synthetic Data Scope
All test fixtures use synthetic domain datasets representing ModelReady store and timeseries feature tables. No cloud-deployed services or real external provider credentials are required.

## Delivered Verifications

### 1. Production Runtime Service (`runtime_mode="production"`)
In `modules/learninghub/tests/test_learninghub_production_runtime.py`:
- `test_production_dataset_snapshot_rejects_missing_or_null_quality_fields_and_leaves_no_durable_traces`:
  - Uses `LearningHubService` with `runtime_mode="production"`, injected durable `SqliteEngine` / `DurableLearningHubRepository`, `DurableArtifactStore`, `DurableAuditLog`, and remote registry adapter.
  - Injects missing `data_quality_score` -> rejected with `DatasetQualityAdmissionError`, naming entity `store-missing-quality` and field `data_quality_score`.
  - Injects missing `confidence` -> rejected with `DatasetQualityAdmissionError`, naming entity `store-missing-confidence` and field `confidence`.
  - Injects explicit `None`/`null` -> rejected with `DatasetQualityAdmissionError`, naming entity `store-explicit-none` and both fields.
  - Injects mixed batch (valid + missing quality + missing confidence) -> rejected with `DatasetQualityAdmissionError` naming each invalid record and field.
  - Verifies durable repository: no snapshot records or partial writes exist immediately or after SQLite engine close/reopen.
- `test_production_dataset_snapshot_preserves_explicit_zero_and_persists_durable_receipt`:
  - Registers snapshot containing explicit `0.0` values (`data_quality_score=0.0`, `confidence=0.0`) alongside complete values (`1.0`, `0.95`).
  - Asserts that `0.0` is preserved faithfully (not treated as missing, not replaced with `1.0` or `None`).
  - Confirms durable snapshot receipt is saved and readable after SQLite engine close/reopen.

### 2. HTTP Router Entrypoint (`POST /api/v1/learninghub/dataset-snapshots`)
In `tests/integration/test_learninghub_dataset_snapshot_api.py`:
- `test_http_dataset_snapshot_rejects_missing_data_quality_score`: Returns HTTP 422 with entity ID and `data_quality_score` in detail; repository has no snapshot.
- `test_http_dataset_snapshot_rejects_missing_confidence`: Returns HTTP 422 with entity ID and `confidence` in detail; repository has no snapshot.
- `test_http_dataset_snapshot_rejects_explicit_null_quality_fields`: Returns HTTP 422 with entity ID and missing fields in detail; repository has no snapshot.
- `test_http_dataset_snapshot_rejects_mixed_valid_and_missing_rows_without_partial_write`: Returns HTTP 422 identifying both invalid rows; no partial write.
- `test_http_dataset_snapshot_preserves_explicit_zero_values`: Returns HTTP 201; preserves `0.0` values in repository.
- `test_http_dataset_snapshot_durable_rejection_leaves_no_persistence`: Real HTTP router with durable SQLite repository; rejected request leaves no persistence trace across database restart.

## Verification Run & Test Receipts

### 1. Diff Whitespace Check
```bash
git diff --check
```
- **Exit Code**: `0`
- **Output**: clean (no whitespace errors)

### 2. Pytest Focused Test Suite
```bash
uv run pytest modules/learninghub/tests/test_learninghub_production_runtime.py tests/integration/test_learninghub_dataset_snapshot_api.py tests/data/test_pit_snapshot.py -v
```
- **Exit Code**: `0`
- **Duration**: ~74s
- **Result**: `31 passed, 7 warnings in 73.63s`

### 3. Pytest Quiet Verification
```bash
uv run pytest modules/learninghub/tests/test_learninghub_production_runtime.py tests/integration/test_learninghub_dataset_snapshot_api.py tests/data/test_pit_snapshot.py -q
```
- **Exit Code**: `0`
- **Result**: `............................... [100%]` (31 passed)

### 4. Code Quality & Linting
```bash
uv run ruff check modules/learninghub/tests/test_learninghub_production_runtime.py tests/integration/test_learninghub_dataset_snapshot_api.py
```
- **Exit Code**: `0`
- **Output**: `All checks passed!`
