# ODP-SUPERVISOR-LEASE-ISSUANCE-RECOVERY-001: Supervisor Lease Issuance Stale-CAS Safe Recovery

- **Task ID**: `ODP-SUPERVISOR-LEASE-ISSUANCE-RECOVERY-001`
- **Owner**: `Antigravity4`
- **Reviewer**: `Codex2`
- **Phase**: Control Plane Lease Recovery
- **Deliverable**: Stale-CAS safe recovery path in `.orchestrator/release_lease_integration.py` and `delivery_toolchain/release/release_lease.py`
- **Evidence Directory**: `docs/evidence/runtime/ODP-SUPERVISOR-LEASE-ISSUANCE-RECOVERY-001/`

---

## 1. Incident Context & Epistemic Boundaries (a31 Transaction)

During the investigation of the 2026-09-28 `a31` release candidate admission and deployment sequence (`candidate_sha`: `a31e02ae391811a4c323ec4d834b70e200953366`, `manifest_run_id`: `36333397898`, `approval_id`: `HUMANOPS-DEV-MIGRATION-20260927T225545Z`):

### Available Evidence & Explicit Caveats
- **Observed Artifacts**:
  1. GCS object metadata indicating an object existed in the durable state bucket prefix.
  2. A generic activity log entry `stale_status_write_rejected` recorded during the cycle.
- **Epistemic Discipline & Unproven Assumptions**:
  - The generic `stale_status_write_rejected` event carries **no `task_id`**. Without task-linked evidence, causality between that generic stale-write event and the `a31` issuance transaction cannot be asserted as proven fact.
  - The exact plaintext and signature contents of any historical GCS object were not verified in live telemetry.
  - Specific IAM, provider network, or environment root causes are neither asserted nor ruled out without direct evidence.
  - No live GCP infrastructure was accessed, no live private keys were loaded, and no live deployment dispatches were executed during this investigation.

### Control Plane Recovery Objective
Rather than relying on unverified assumptions about past events, the control plane architecture must guarantee safe, fail-closed reconciliation under any possible combination of concurrent status writes, interrupted transactions, or delayed storage operations.

---

## 2. Safe Recovery Architecture & Protocol

To eliminate control plane stalls while strictly preventing unauthorized dispatch, lease replay, or duplicate signing, the following mechanisms are implemented:

### A. Durable State Store Query (`LeaseStateStore`)
- Added `list_records()` and `find_leases_for_task(task_id)` to both `_GCSLeaseStateStore` and local `LeaseStateStore`.
- When the Supervisor encounters a task in `state="issuing"` (or evaluates fresh issuance), it queries durable state for existing leases for that `task_id`.

### B. Formal Lease Verification & Exact Ownership Binding
- Any durable lease found in storage must satisfy formal cryptographic and policy verification:
  1. **`verify_lease` Execution**: Validates schema version, required fields, cryptographic Ed25519 signature against the authorized verification public key, and validity window (`issued_at` to `expires_at` vs current time).
  2. **Exact Request Fingerprint & Precondition Binding**: Validates exact equality for:
     - `task_id` == `request.task_id`
     - `candidate_sha` == `request.candidate_sha` == `manifest.candidate_sha` == `registry.release.candidate_sha`
     - `manifest_digest` == `request.manifest_digest` == `manifest.manifest_digest`
     - `target_environment` == `request.target_environment`
     - `allowed_action` == `request.action` ("deploy")
     - `release_id` == `manifest.release_id`
  3. **Signed Nonce & Request Validity**: Validates that neither the lease nor the human approval request has expired.
  4. **Post-Storage Recheck**: Gate registry, dependency completions, dispatch reference ancestry, and expiry are re-verified after storage reads and before any status commit.

### C. Fail-Closed Terminal Resolution for Unproven Issuing Reservations
- If a task is already in `state="issuing"`, it represents a prior attempt that was interrupted.
- If storage contains:
  - **Zero records** (absent lease),
  - **Non-`issued` records** (`consumed` or `revoked`),
  - **Invalid signatures or tampered documents**,
  - **Mismatched request parameters or expired TTLs**,
  the task terminates non-dispatchably into `state="blocked"`.
- **Strict No Re-Sign Rule**: Under no circumstances does an interrupted `issuing` task re-acquire the Secret Manager signing key or mint a new lease under the same approval request. A fresh Human/Ops approval request with a new nonce is required.

### D. Exact Ownership Revocation Rule
- To prevent accidental invalidation of unrelated operations, the bridge **never revokes task-wide or mismatched leases**.
- Only leases that provably match the exact `task_id`, `candidate_sha`, `manifest_digest`, `target_environment`, and `action` of the active request are revoked when precondition or verification failures occur.

### E. Two-Phase Dispatch Guarantee
- **Mandatory Sequence**: The secret-free `issued_record` receipt must successfully commit to `ai-status.json` *before* attempting `dispatch_runtime_release`.
- If the CAS commit fails or is unconfirmed, dispatch is strictly prevented, allowing the subsequent Supervisor cycle to safely reconcile the unexpired GCS lease.

### F. Secrecy & Bearer Protection
- Private key material is parsed strictly in memory and zeroed immediately after use.
- Bearer lease payloads (base64 JSON) and raw signature hex values are never logged or stored in `ai-status.json`.
- Only cryptographic digests (`approval_nonce_digest`, `nonce_digest`, `signature_digest`, `signature_key_id`) are published in receipts and activity logs.

---

## 3. Verification & Regression Coverage

The test suite in `.orchestrator/test_release_lease_integration.py` provides comprehensive, reproducible verification using local state doubles without touching live GCP:

1. `test_stale_cas_issuing_recovery_reconciles_gcs_lease_and_dispatches`:
   - Simulates GCS lease persistence followed by a stale CAS status write conflict.
   - Subsequent cycle safely reconciles the unexpired GCS lease via `verify_lease` without re-loading Secret Manager private key, commits `issued` receipt to status, and dispatches.
2. `test_stale_cas_issuing_recovery_with_expired_lease_revokes_and_blocks`:
   - Expired orphan lease in GCS is revoked and marks the task `blocked` without dispatch.
3. `test_stale_cas_issuing_recovery_with_mismatched_payload_leaves_unrelated_lease_and_blocks`:
   - Mismatched orphan lease is preserved (not revoked) and marks the current task `blocked`.
4. `test_stale_cas_issuing_recovery_with_multiple_issued_leases_revokes_matching_and_blocks`:
   - Multiple conflicting unconsumed leases in GCS are revoked and marked `blocked`.
5. `test_stale_cas_issuing_recovery_with_failed_preconditions_revokes_and_blocks`:
   - Precondition regression (e.g. dependency reverted to `in_progress`) revokes matching lease and blocks.
6. `test_issuing_without_gcs_lease_terminates_blocked_without_loading_key`:
   - `issuing` reservation with 0 GCS records terminates `blocked` without loading key or re-signing.
7. `test_issuing_with_consumed_or_revoked_gcs_lease_terminates_blocked_without_loading_key`:
   - `issuing` reservation with consumed/revoked lease terminates `blocked` without loading key.
8. `test_issuing_recovery_with_invalid_signature_blocks_and_does_not_dispatch`:
   - Durable lease with corrupt signature fails `verify_lease` and transitions task to `blocked`.
9. `test_issuing_recovery_with_changed_approval_blocks_and_does_not_dispatch`:
   - Request parameter change after reservation prevents dispatch of mismatched prior lease.
10. `test_issuing_recovery_with_ttl_delay_blocks_and_does_not_dispatch`:
    - Rechecking expiry after delay catches expired lease/request and transitions to `blocked`.
11. `test_canonical_writer_race_preserves_newer_status_revision`:
    - Confirms stale status snapshot does not overwrite newer writer revisions.
12. `test_no_duplicate_sign_or_dispatch_on_recovery`:
    - Confirms that recovery cycle makes exactly 0 signing key calls and dispatches exactly once.
13. `test_no_secret_or_bearer_material_in_logs_or_status`:
    - Validates absence of private key material, raw signature values, and raw nonces in receipts and logs.

---

## 4. Acceptance Criteria Verification Matrix

| Acceptance Criterion | Implementation / Evidence | Status |
|---|---|---|
| Use fake storage and canonical writer doubles to reproduce the reserved-state then stale status write race without touching live GCP | `.orchestrator/test_release_lease_integration.py::test_stale_cas_issuing_recovery_reconciles_gcs_lease_and_dispatches` | **PASSED** |
| Use the existing verifier and exact request fingerprint to prove a lease payload matches the current approval without exposing bearer data | `.orchestrator/release_lease_integration.py` (`verify_lease`, `_has_exact_lease_ownership`, `_exact_binding_errors`) | **PASSED** |
| Revalidate current status candidate manifest target action approval and request before persisting issued receipt; dispatch only after that receipt commits | `_status_still_reserved`, post-read expiry rechecks, and `_commit_result` ordering before `dispatch()` | **PASSED** |
| If exact lease binding cannot be proven or TTL has elapsed record a terminal non-dispatchable outcome and require a fresh Human/Ops request without reusing a nonce | `test_issuing_without_gcs_lease_terminates_blocked_without_loading_key`, `test_issuing_with_consumed_or_revoked_gcs_lease_terminates_blocked_without_loading_key`, `test_issuing_recovery_with_ttl_delay_blocks_and_does_not_dispatch` | **PASSED** |
| Do not attribute a stale status event with no task_id to this issuance; require task-linked evidence for root-cause claims | Section 1 of this document; explicit epistemic boundaries and caveat declarations | **PASSED** |
| Regression tests prove stale CAS does not cause duplicate signing dispatch or overwrite newer status and logs contain no secret material | `test_no_duplicate_sign_or_dispatch_on_recovery`, `test_canonical_writer_race_preserves_newer_status_revision`, `test_no_secret_or_bearer_material_in_logs_or_status` | **PASSED** |
| Only Supervisor signs or dispatches; submit a separate reviewed PR | In-memory signing key lifetime, single supervisor bridge entrypoint, separate per-task PR workflow | **PASSED** |
