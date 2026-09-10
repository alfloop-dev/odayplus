# ODP-MODELREADY-A4-REMEDIATION-001 Completion Evidence

## Task Summary
- **Task ID**: `ODP-MODELREADY-A4-REMEDIATION-001`
- **Title**: 補齊 ModelReady 缺值 production entry 與持久化驗證
- **Owner**: Antigravity4
- **Reviewer**: Codex
- **Target Branch**: `task/ODP-MODELREADY-A4-REMEDIATION-001`
- **Base Branch**: `dev` (`1260d977345f18f4c8db05821e238be87614f4b2`)

## Purpose & Scope
This task provides forward remediation for the A4 acceptance requirement originally declared in `ODP-MODELREADY-QUALITY-NULLABLE-001`. It verifies that when `ModelReadyRecord` datasets missing `data_quality_score` or `confidence` (or carrying explicit `None`/`null` values) enter via real production entrypoints (both service and HTTP router), they are rejected with field- and record-identifying diagnostic messages, and leave no durable or partial snapshot records in storage.

### Synthetic Data Scope
All test fixtures use synthetic domain datasets representing ModelReady store and timeseries feature tables. No cloud-deployed services or real external provider credentials are required.

### Forward Remediation Boundary
This deliverable represents new forward verification for ModelReady nullable quality and confidence enforcement. In accordance with governance policy, historical archive and correction documents are not retroactively modified.

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
- `test_http_dataset_snapshot_reaches_blocked_feature_gate`: Confirms blocked feature gating returns HTTP 422 with diagnostic message.
- `test_http_dataset_snapshot_returns_registry_bindings`: Confirms registered snapshots return bound `feature_set_id` and `label_set_id`.
- `test_http_dataset_snapshot_rejects_missing_data_quality_score`:
  - Production router (`runtime_mode="production"`, `DurableLearningHubRepository`, `DurableArtifactStore`, `DurableAuditLog`, `RecordingRemoteRegistry`).
  - Injected missing `data_quality_score` -> HTTP 422 with `store-no-quality` and `data_quality_score` in detail.
  - Verifies durable repository has no snapshot immediately and across SQLite engine restart.
- `test_http_dataset_snapshot_rejects_missing_confidence`:
  - Production router (`runtime_mode="production"`).
  - Injected missing `confidence` -> HTTP 422 with `store-no-confidence` and `confidence` in detail.
  - Verifies durable repository has no snapshot immediately and across SQLite engine restart.
- `test_http_dataset_snapshot_rejects_explicit_null_quality_fields`:
  - Production router (`runtime_mode="production"`).
  - Injected explicit `None`/`null` -> HTTP 422 with `store-explicit-null`, `data_quality_score`, and `confidence` in detail.
  - Verifies durable repository has no snapshot immediately and across SQLite engine restart.
- `test_http_dataset_snapshot_rejects_mixed_valid_and_missing_rows_without_partial_write`:
  - Production router (`runtime_mode="production"`).
  - Injected mixed valid and invalid rows -> HTTP 422 naming all invalid entity IDs and missing fields; no partial snapshot write immediately or after SQLite restart.
- `test_http_dataset_snapshot_preserves_explicit_zero_and_persists_durable_receipt`:
  - Production router (`runtime_mode="production"`).
  - Injected rows with explicit `0.0` and complete values (`1.0`, `0.95`) -> HTTP 201.
  - Confirms values preserved faithfully (`0.0` preserved, not coalesced) immediately and across SQLite restart.

## Verification Receipts & Execution Records

### Retry Reason
Re-running focused verification suite on new implementation head after resolving Codex independent review findings [P2][R1] (adding real `runtime_mode="production"` router and durable/registry bindings for HTTP cases) and [P2][R2] (durable SQLite restart assertions across all negative and positive HTTP cases).

### 1. Diff Whitespace Check
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Output Receipt**: `docs/evidence/completion/ODP-MODELREADY-A4-REMEDIATION-001/git-diff-check.txt`
- **Status**: Passed (0 whitespace errors)

### 2. Pytest Focused Test Suite (Quiet Mode)
- **Command**: `uv run pytest modules/learninghub/tests/test_learninghub_production_runtime.py tests/integration/test_learninghub_dataset_snapshot_api.py tests/data/test_pit_snapshot.py -q`
- **Start Time**: `2026-09-10T00:21:58Z`
- **End Time**: `2026-09-10T00:22:32Z`
- **Duration**: `34s`
- **Exit Code**: `0`
- **Output Receipt**: `docs/evidence/completion/ODP-MODELREADY-A4-REMEDIATION-001/pytest-focused-q.txt`
- **Result**: `30 passed, 6 warnings in 22.89s` (exit code `0`)

### 3. Pytest Focused Test Suite (Verbose Mode)
- **Command**: `uv run pytest modules/learninghub/tests/test_learninghub_production_runtime.py tests/integration/test_learninghub_dataset_snapshot_api.py tests/data/test_pit_snapshot.py -v`
- **Start Time**: `2026-09-10T00:22:35Z`
- **End Time**: `2026-09-10T00:23:09Z`
- **Duration**: `34s`
- **Exit Code**: `0`
- **Output Receipt**: `docs/evidence/completion/ODP-MODELREADY-A4-REMEDIATION-001/pytest-focused-v.txt`
- **Result**: `30 passed, 6 warnings in 22.98s` (exit code `0`)

### 4. Code Quality & Linting
- **Command**: `uv run ruff check modules/learninghub/tests/test_learninghub_production_runtime.py tests/integration/test_learninghub_dataset_snapshot_api.py`
- **Exit Code**: `0`
- **Result**: `All checks passed!`

## Artifact & Output Checksums (SHA256)

| File | SHA256 Checksum |
|---|---|
| `docs/evidence/completion/ODP-MODELREADY-A4-REMEDIATION-001/pytest-focused-q.txt` | `cd8fcef3c645a8830453cb61e5ec5348f33b0ae1655949ab17c272c8e5e359af` |
| `docs/evidence/completion/ODP-MODELREADY-A4-REMEDIATION-001/pytest-focused-v.txt` | `9bb06a00ee6a10dcf569c8bdf7c6181f14cf47bc7ab9e826e73445b2bf3232ba` |
| `docs/evidence/completion/ODP-MODELREADY-A4-REMEDIATION-001/git-diff-check.txt` | `1ca4a13146b2891c0e462123ac0cb848f92e6221f18b870a1b78c232d1956c35` |
| `tests/integration/test_learninghub_dataset_snapshot_api.py` | `abd45edc4cadff6c4505f2d9f36abaa0ac66e443f055f130f7c5abee814f6527` |
| `modules/learninghub/tests/test_learninghub_production_runtime.py` | `831531c79f32146571821154c12d268f637e8d93c9d9bd58cd6924069f0059ec` |
| `tests/data/test_pit_snapshot.py` | `40704f71cc1df1f9cbffbc5c19f5d614b01238eb45dca7f496ad41f87a043b0f` |
