# ODP-SUPERVISOR-LEASE-ISSUANCE-RECOVERY-001: Supervisor Lease Issuance Stale-CAS Safe Recovery

- **Task ID**: `ODP-SUPERVISOR-LEASE-ISSUANCE-RECOVERY-001`
- **Owner**: `Antigravity3`
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
- Robust error handling: all GCS lookups and reads are wrapped to catch `LeaseError` / `Exception`, safely transitioning the task to a non-dispatchable `blocked` state with sanitized error messages.
- When the Supervisor encounters a task in `state="issuing"` (or evaluates fresh issuance), it queries durable state for existing leases for that `task_id`.

### B. Formal Lease Verification & Exact Request-to-Lease Binding
- Every minted lease signs and stores immutable request binding metadata:
  1. `request_fingerprint`: SHA-256 digest binding `task_id`, `approval_id`, `approval_nonce_digest`, `candidate_sha`, `manifest_digest`, `target_environment`, `action`, and `manifest_run_id`.
  2. `approval_id`: Exact human approval identifier.
  3. `approval_nonce_digest`: SHA-256 hash of the human approval nonce.
- Any durable lease found in storage must satisfy formal cryptographic and policy verification via `verify_lease()`:
  1. **Cryptographic Ed25519 Signature**: Attested signature against authorized public key.
  2. **Exact Request Binding Enforcement**: Lease `request_fingerprint`, `approval_id`, and `approval_nonce_digest` must match the current request.
  3. **Target & Artifact Binding**: Validates exact equality for `task_id`, `candidate_sha`, `manifest_digest`, `target_environment`, `allowed_action`, and `release_id`.
  4. **Strict Validity Window**: `issued_at` and `expires_at` checked against current UTC time (failing closed if expired or missing).

### C. Post-Storage Precondition & Eligibility Revalidation
- After reading or writing durable storage and before attempting the status CAS commit, the supervisor revalidates all admission preconditions:
  - `request_errors` (task class, status, approval state).
  - Manifest and registry digests and candidate SHA bindings.
  - Build run successful `workflow_dispatch` verification.
  - Remote dispatch ref ancestry (`check_dispatch_ref_errors`).
  - Request and lease expiry timestamps.
- If any precondition fails post-storage, the lease is revoked (if owned), and the task transitions to `blocked` without committing an `issued` receipt or dispatching.

### D. Fail-Closed Terminal Resolution for Unproven Issuing Reservations
- If a task is already in `state="issuing"`, it represents a prior attempt that was interrupted.
- If storage contains:
  - **Zero records** (absent lease),
  - **Non-`issued` records** (`consumed` or `revoked`),
  - **Unprovable or legacy leases** lacking exact request binding,
  - **Invalid signatures or tampered documents**,
  - **Mismatched request parameters or expired TTLs**,
  the task terminates non-dispatchably into `state="blocked"`.
- **Strict No Re-Sign Rule**: Under no circumstances does an interrupted `issuing` task re-acquire the Secret Manager signing key or mint a new lease under the same approval request. A fresh Human/Ops approval request with a new nonce is required.

### E. Exact Ownership Revocation Rule
- To prevent accidental invalidation of unrelated operations, the bridge **never revokes unprovable, legacy, or mismatched leases**.
- Only leases that provably match exact ownership (`task_id`, `candidate_sha`, `manifest_digest`, `target_environment`, `action`, `request_fingerprint`, `approval_id`, `approval_nonce_digest`) are revoked when precondition or verification failures occur.
- Unowned or legacy leases remain unmodified in GCS while the active task safely blocks.

### F. Two-Phase Dispatch Guarantee
- **Mandatory Sequence**: The secret-free `issued_record` receipt must successfully commit to `ai-status.json` *before* attempting `dispatch_runtime_release`.
- If the CAS commit fails, is rejected, or is unconfirmed, dispatch is strictly prevented, allowing the subsequent Supervisor cycle to safely reconcile the unexpired GCS lease.

### G. Secrecy & Bearer Protection
- Private key material is parsed strictly in memory and zeroed immediately after use.
- Bearer lease payloads (base64 JSON), raw signatures, and raw nonces are never logged or stored in `ai-status.json`.
- Verifier and storage error messages are strictly sanitized: raw nonce strings, algorithm names, and key ID strings are never reflected into receipts or activity logs.
- Only cryptographic digests (`approval_nonce_digest`, `nonce_digest`, `signature_digest`, `signature_key_id`) are published in receipts and activity logs.

---

## 3. Verification & Regression Coverage

The test suite in `.orchestrator/test_release_lease_integration.py` provides comprehensive, reproducible verification using local state doubles without touching live GCP (73 tests passed):

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
    - Confirms stale status snapshot does not overwrite newer writer revisions using a real revision-advancing status writer double.
12. `test_no_duplicate_sign_or_dispatch_on_recovery`:
    - Confirms that recovery cycle makes exactly 0 signing key calls and dispatches exactly once.
13. `test_no_secret_or_bearer_material_in_logs_or_status`:
    - Validates absence of private key material, raw signature values, and raw nonces in receipts and logs.
14. `test_exact_approval_id_and_nonce_digest_mismatch_on_same_deployment_leaves_lease_unrevoked`:
    - Verifies that mismatched approval identity on identical deployment params leaves the durable lease unrevoked in GCS.
15. `test_clock_advance_during_storage_write_blocks_and_revokes_without_dispatch`:
    - Verifies that if the clock advances past expiry while persisting to storage, the lease is revoked and task blocked.
16. `test_recovery_second_cas_rejection_leaves_lease_issued_for_future_retry`:
    - Verifies that a CAS rejection during recovery status commit does not revoke the valid lease, allowing future retry.
17. `test_eligibility_revoked_during_storage_revokes_lease_and_blocks`:
    - Verifies that request revocation during storage access revokes the matching lease and marks the task blocked.
18. `test_state_store_list_or_read_failure_records_blocked_without_crashing_or_signing`:
    - Verifies that GCS `LeaseStateError` during storage lookup transitions the task to blocked with a sanitized receipt error.
19. `test_bearer_sentinels_and_error_sanitization_in_receipts_and_logs`:
    - Verifies that raw sentinels and verifier diagnostic fragments are never leaked into receipts or activity logs.

---

## 4. Acceptance Criteria Verification Matrix

| Acceptance Criterion | Implementation / Evidence | Status |
|---|---|---|
| Use fake storage and canonical writer doubles to reproduce the reserved-state then stale status write race without touching live GCP | `.orchestrator/test_release_lease_integration.py::test_stale_cas_issuing_recovery_reconciles_gcs_lease_and_dispatches` | **PASSED** |
| Use the existing verifier and exact request fingerprint to prove a lease payload matches the current approval without exposing bearer data | `.orchestrator/release_lease_integration.py` (`verify_lease`, `_has_exact_lease_ownership`, `_exact_binding_errors`, `_sanitize_errors`) | **PASSED** |
| Revalidate current status candidate manifest target action approval and request before persisting issued receipt; dispatch only after that receipt commits | `_status_still_reserved`, post-storage precondition rechecks, and `_commit_result` ordering before `dispatch()` | **PASSED** |
| If exact lease binding cannot be proven or TTL has elapsed record a terminal non-dispatchable outcome and require a fresh Human/Ops request without reusing a nonce | `test_issuing_without_gcs_lease_terminates_blocked_without_loading_key`, `test_issuing_with_consumed_or_revoked_gcs_lease_terminates_blocked_without_loading_key`, `test_issuing_recovery_with_ttl_delay_blocks_and_does_not_dispatch` | **PASSED** |
| Do not attribute a stale status event with no task_id to this issuance; require task-linked evidence for root-cause claims | Section 1 of this document; explicit epistemic boundaries and caveat declarations | **PASSED** |
| Regression tests prove stale CAS does not cause duplicate signing dispatch or overwrite newer status and logs contain no secret material | `test_no_duplicate_sign_or_dispatch_on_recovery`, `test_canonical_writer_race_preserves_newer_status_revision`, `test_no_secret_or_bearer_material_in_logs_or_status`, `test_bearer_sentinels_and_error_sanitization_in_receipts_and_logs` | **PASSED** |
| Only Supervisor signs or dispatches; submit a separate reviewed PR | In-memory signing key lifetime, single supervisor bridge entrypoint, separate per-task PR workflow | **PASSED** |
