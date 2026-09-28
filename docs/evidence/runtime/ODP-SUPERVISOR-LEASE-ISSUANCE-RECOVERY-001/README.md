# ODP-SUPERVISOR-LEASE-ISSUANCE-RECOVERY-001: Supervisor Lease Issuance Stale-CAS Safe Recovery

- **Task ID**: `ODP-SUPERVISOR-LEASE-ISSUANCE-RECOVERY-001`
- **Owner**: `Antigravity4`
- **Reviewer**: `Codex2`
- **Phase**: Control Plane Lease Recovery
- **Deliverable**: Stale-CAS safe recovery path in `.orchestrator/release_lease_integration.py` and `delivery_toolchain/release/release_lease.py`
- **Evidence Directory**: `docs/evidence/runtime/ODP-SUPERVISOR-LEASE-ISSUANCE-RECOVERY-001/`

---

## 1. Executive Summary & Incident Analysis (a31 Transaction)

During the `a31` candidate admission and deployment sequence (`candidate_sha`: `a31e02ae391811a4c323ec4d834b70e200953366`, `manifest_run_id`: `36333397898`, `approval_id`: `HUMANOPS-DEV-MIGRATION-20260927T225545Z`), the Supervisor release lease bridge encountered an execution edge case:

1. The Supervisor successfully validated all gate and dependency preconditions.
2. The Supervisor retrieved the private Ed25519 signing key in-memory from Secret Manager.
3. The Supervisor successfully minted the signed release lease and persisted it into the GCS durable compare-and-set store (`state_store.record_issued(lease)`).
4. When the Supervisor attempted to commit the transition from `state="issuing"` to `state="issued"` on `ai-status.json` (`_commit_result`), the commit failed because a concurrent task/status writer advanced `_status_write_revision` during the external network calls.
5. The original bridge implementation did not reconcile in-flight `state="issuing"` reservations and skipped them unconditionally on subsequent cycles (`if previous.get("state") == "issuing": continue`), abandoning the validly minted GCS lease and stalling automated dispatch.

### Root Cause Disposition

- **Root Cause**: A stale Compare-And-Set (CAS) write conflict on `ai-status.json` occurring after GCS lease persistence, combined with the lack of an automated safe reconciliation and orphan-lease handling handler for tasks in `state="issuing"`.
- **Non-Causes (Definitively Ruled Out)**:
  - **GCP IAM**: Service account permissions and Workload Identity Federation performed normally.
  - **GCS CAS State Store**: GCS object creation (`if_generation_match=0`) succeeded without error.
  - **Google Secret Manager**: Secret version access and private key parsing succeeded in memory without error.

---

## 2. Safe Recovery Architecture & Implementation

To prevent control plane stall while strictly preventing lease replay or unauthorized dispatch, the following mechanisms were implemented:

### A. Durable State Store Query & Reconciliation (`LeaseStateStore`)
- Added `list_records()` and `find_leases_for_task(task_id)` to both `_GCSLeaseStateStore` and local `LeaseStateStore`.
- When Supervisor evaluates a task in `state="issuing"` (or during issuance), it queries `state_store` for any existing unconsumed (`state="issued"`) leases for that exact `task_id`.

### B. Exact Request Fingerprint & Precondition Binding
- When an unconsumed lease is found in durable state:
  1. The bridge verifies that the lease matches the exact current approval and request payload:
     - `task_id` == `request.task_id`
     - `candidate_sha` == `request.candidate_sha` == `manifest.candidate_sha` == `registry.release.candidate_sha`
     - `manifest_digest` == `request.manifest_digest` == `manifest.manifest_digest`
     - `target_environment` == `request.target_environment`
     - `allowed_action` == `request.action` ("deploy")
     - `release_id` == `manifest.release_id`
  2. The bridge verifies that neither the lease nor the human approval request has expired (`expires_at > now`).
  3. The bridge re-checks all gate, dependency, dispatch reference, and ancestry preconditions (`request_errors`, `_exact_binding_errors`, `_build_run_binding_errors`, `check_dispatch_ref_errors`, `issuance_errors`).
  4. If all validations succeed, the bridge reconciles the existing GCS lease without re-requesting the signing key from Secret Manager.

### C. Two-Phase Dispatch Guarantee
- **Mandatory Order**: The bridge persists the secret-free `issued_record` receipt to `ai-status.json` *before* attempting `dispatch_runtime_release`.
- If the task CAS commit fails or cannot be confirmed, dispatch is strictly prevented.

### D. Orphan & Expired Lease Revocation
- If an unconsumed lease in `state_store` is found to be expired, mismatched in payload, or fails any gate precondition:
  1. The bridge transitions the lease in `state_store` to `state="revoked"` with an audit reason (`orphan lease expired or unbound from current request`).
  2. The task board transitions to `state="blocked"` with detailed receipt error reporting.
  3. The approval nonce digest remains recorded in the issuance record and task history, preventing nonce reuse.

### E. Multiple Conflicting Leases Fail-Closed
- If more than one unconsumed `issued` lease is found in `state_store` for a task, the bridge revokes all conflicting leases and transitions the task to `blocked`.

### F. Secrecy & Bearer Protection
- Private key material is never persisted, logged, or exported.
- Lease bearer payloads (base64 documents) and signatures are never logged or stored in receipts/status. Only cryptographic digests (`nonce_digest`, `signature_digest`, `signature_key_id`) are published.

---

## 3. Verification & Regression Coverage

The regression suite in `.orchestrator/test_release_lease_integration.py` was expanded with reproducible, GCP-mocked tests:

1. `test_stale_cas_issuing_recovery_reconciles_gcs_lease_and_dispatches`:
   - Simulates GCS write success followed by a stale CAS status write rejection.
   - Verifies that subsequent Supervisor cycle safely reconciles the unexpired GCS lease without re-loading Secret Manager private key, commits `issued` receipt to status, and dispatches.
2. `test_stale_cas_issuing_recovery_with_expired_lease_revokes_and_blocks`:
   - Verifies that an expired orphan lease in GCS is revoked and marks the task `blocked`.
3. `test_stale_cas_issuing_recovery_with_mismatched_payload_revokes_and_blocks`:
   - Verifies that a mismatched orphan lease is revoked and marks the task `blocked`.
4. `test_stale_cas_issuing_recovery_with_multiple_issued_leases_revokes_all_and_blocks`:
   - Verifies that multiple unconsumed leases in GCS are revoked and marked `blocked`.
5. `test_stale_cas_issuing_recovery_with_failed_preconditions_revokes_and_blocks`:
   - Verifies that precondition failure (e.g. dependency regression) revokes existing GCS lease and blocks.
6. `test_issuing_without_gcs_lease_proceeds_with_fresh_issuance`:
   - Verifies that an `issuing` reservation without a GCS lease proceeds with fresh key acquisition and minting.

### Test Execution Results
- `uv run --python 3.12 pytest .orchestrator/test_release_lease_integration.py`: **62 passed in 2.89s**.
- `uv run --python 3.12 pytest tests/release/ .orchestrator/test_release_lease_integration.py .orchestrator/test_release_lease_issuer.py`: **504 passed in 279.09s**.

---

## 4. Acceptance Criteria Verification Matrix

| Acceptance Criterion | Implementation / Evidence | Status |
|---|---|---|
| 用可重現測試涵蓋 GCS durable write 後 canonical task CAS stale 的競態且測試不觸碰 live GCP | `.orchestrator/test_release_lease_integration.py::test_stale_cas_issuing_recovery_reconciles_gcs_lease_and_dispatches` | **PASSED** |
| 以 exact request fingerprint 與目前 canonical approval 安全 reconciliation 未過期 lease 並先持久化 issued receipt 才允許 Supervisor dispatch | `.orchestrator/release_lease_integration.py` lines 940–1160; exact fingerprint and payload checks, `_commit_result` before `dispatch` | **PASSED** |
| 逾期或無法證明 payload 與 request 完全綁定的 orphan lease 進入明確 no-dispatch terminal state 且不能重用 nonce | `release_lease_integration.py` orphan checks + `state_store.revoke()` + `_record_blocked()` + nonces preserved in history | **PASSED** |
| stale status snapshot 不覆蓋較新 task 狀態並在任何安全前置條件失敗時維持 blocked | `_status_still_reserved` + `_commit_result(..., expected_issuance=...)` + CAS verification in `write_status_snapshot_if_current` | **PASSED** |
| 日誌與活動記錄不輸出私鑰或 lease bearer payload且只有 Supervisor 簽署與 dispatch | In-memory key zeroing, `build_receipt` digests only, stdin-based `gh api` dispatch | **PASSED** |
| 新增 regression tests並提交獨立審查 PR | 6 dedicated regression tests added to `.orchestrator/test_release_lease_integration.py`; full suite passing | **PASSED** |
