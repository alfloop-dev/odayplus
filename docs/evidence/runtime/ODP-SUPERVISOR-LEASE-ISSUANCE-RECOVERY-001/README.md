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
- Robust error handling: all GCS lookups and reads are wrapped to catch `LeaseError` / `Exception`, safely transitioning the task to a non-dispatchable `blocked` state with sanitized safe sentinel error messages (never raw exception text).
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
- Each final admission refresh re-reads the registry and manifest after ref-resolution callbacks, then reloads canonical status after those file reads. CAS retry and post-sync checks use the refreshed inputs, and dispatch receives the exact manifest that passed the final check.
- If a precondition fails before receipt commit, the exact owned lease is revoked and the task blocks without an `issued` receipt. If it fails after the receipt commit but before dispatch, the record transitions to a non-dispatchable `blocked` or `expired_before_dispatch` state and dispatch is prevented.

### D. Dual-Point Expiry Recheck (P1 Fix)
- **Pre-receipt-commit recheck**: After slow validation and ref lookup (which may take seconds), both `lease.expires_at` and `request.expires_at` are rechecked against the current time before committing the issued receipt. If either has elapsed, the lease is revoked and a terminal `blocked` outcome is committed.
- **Pre-dispatch recheck**: After the receipt commit succeeds (which calls `sync_status_pipeline` and may take additional time), both deadlines are rechecked again. If either has elapsed, a terminal `expired_before_dispatch` record is committed with `dispatch: "expired"`, the lease is revoked, and dispatch is prevented. No replay or re-signing occurs.
- This closes the window where: request expiry in 1 second, ref lookup takes 2 seconds → receipt and dispatch would have proceeded after approval expiry.

### E. Fail-Closed Terminal Resolution for Unproven Issuing Reservations
- If a task is already in `state="issuing"`, it represents a prior attempt that was interrupted.
- If storage contains:
  - **Zero records** (absent lease),
  - **Non-`issued` records** (`consumed` or `revoked`),
  - **Unprovable or legacy leases** lacking exact request binding,
  - **Invalid signatures or tampered documents**,
  - **Mismatched request parameters or expired TTLs**,
  the task terminates non-dispatchably into `state="blocked"`.
- **Strict No Re-Sign Rule**: Under no circumstances does an interrupted `issuing` task re-acquire the Secret Manager signing key or mint a new lease under the same approval request. A fresh Human/Ops approval request with a new nonce is required.

### F. Exact Ownership Revocation Rule
- To prevent accidental invalidation of unrelated operations, the bridge **never revokes unprovable, legacy, or mismatched leases**.
- Only leases that provably match exact ownership (`task_id`, `candidate_sha`, `manifest_digest`, `target_environment`, `action`, `request_fingerprint`, `approval_id`, `approval_nonce_digest`) are revoked when precondition or verification failures occur.
- Unowned or legacy leases remain unmodified in GCS while the active task safely blocks.

### G. Two-Phase Dispatch Guarantee
- **Mandatory Sequence**: The secret-free `issued_record` receipt must successfully commit to `ai-status.json` *before* attempting `dispatch_runtime_release`.
- A first issued-receipt CAS rejection may receive one bounded retry only after full status, registry, manifest, build, ref, and expiry revalidation. A second rejection or unconfirmed commit prevents dispatch; no dispatch occurs until an issued receipt successfully commits.

### H. Bounded Safe Error Codes & Bearer Protection
- Private key material is parsed strictly in memory and zeroed immediately after use.
- Bearer lease payloads (base64 JSON), raw signatures, and raw nonces are never logged or stored in `ai-status.json`.
- Verifier and storage diagnostics use bounded safe error codes via positive-match sentinel mapping (not blacklist/truncation):
  - Bearer key material → `"private key error"`
  - Signature algorithm/key_id mismatch → fixed diagnostic sentinels
  - Schema version mismatch → `"lease schema_version does not match expected version"` (raw value not interpolated)
  - State store read errors → `"durable lease state lookup failed"` (raw exceptions not forwarded)
  - State value interpolation → bounded to known states (`issued`, `consumed`, `revoked`, `expired`, `unknown`); unknown values mapped to `"invalid"`
  - Unmatched diagnostic text → fixed `"lease validation failed"`; arbitrary exception text and bearer values are never forwarded
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
    - Recovery runs after lease TTL has elapsed; pre-receipt-commit expiry recheck catches the expired lease and transitions to `blocked`.
11. `test_revision_checking_writer_preserves_newer_status_on_bounded_retry`:
    - Rejects stale candidates by exact `_status_write_revision` comparison, then verifies the retry reloads and preserves concurrent data without repairing or merging a stale snapshot.
12. `test_second_cas_rejection_during_recovery_does_not_dispatch`:
    - Recovery encounters CAS rejection on status commit; dispatch is prevented and durable lease stays `issued` for future retry.
13. `test_eligibility_revoked_during_storage_revokes_lease_and_blocks`:
    - Request eligibility is revoked by a concurrent writer *during* the ref-resolution callback (not before invocation); the post-storage precondition recheck catches the revocation, revokes the matching lease, and blocks the task.
14. `test_no_duplicate_sign_or_dispatch_on_recovery`:
    - Confirms that recovery cycle makes exactly 0 signing key calls and dispatches exactly once.
15. `test_no_secret_or_bearer_material_in_logs_or_status`:
    - Validates absence of private key material, raw signature values, and raw nonces in receipts and logs.
16. `test_cas_retry_revalidates_revoked_approval_and_preserves_newer_state`:
    - A canonical revision race that revokes request approval blocks retry, preserves the newer status, and revokes only the exact matching lease.
17. `test_state_store_list_or_read_failure_records_blocked_without_crashing_or_signing`:
    - Verifies that GCS `LeaseStateError` during storage lookup transitions the task to blocked with a sanitized receipt error.
18. `test_malformed_verifier_errors_with_bearer_sentinels_never_leak_secrets`:
    - Verifies that raw sentinels and verifier diagnostic fragments are never leaked into receipts or activity logs.
19. `test_revision_checking_writer_recovers_after_two_issued_cas_rejections` and `test_release_inputs_are_reloaded_after_cas_and_sync_callbacks`:
    - An exact-revision writer rejects two issued-receipt CAS attempts, then proves one-lease recovery dispatch without re-signing while preserving newer task data. Six callback cases change registry decision, manifest, or build binding during CAS rejection and post-sync; each must block dispatch and revoke only the exact owned lease.
20. `test_request_expiry_during_final_ref_validation_blocks_without_dispatch` and `test_request_expiry_during_commit_sync_blocks_dispatch`:
    - Callback-driven clock advancement proves expiry during final ref resolution and commit synchronization blocks before dispatch.
21. `test_schema_version_bearing_signature_value_is_sanitized`:
    - A stored lease with `schema_version` set to a 128-char hex signature value has its error sanitized; the raw hex value never appears in status or activity logs.
22. `test_durable_state_lookup_error_uses_safe_sentinel_not_raw_exception`:
    - `LeaseStateError` with infrastructure details (bucket paths, IAM errors) uses safe sentinel in receipt errors; raw exception text never appears in status or activity.
23. `test_sanitize_errors_unknown_text_maps_to_fixed_message`:
    - Unknown verifier/storage text maps to a fixed safe message; arbitrary text is never truncated and forwarded.
24. `test_sanitize_errors_state_store_sentinel_mapping`:
    - `_sanitize_errors` maps known diagnostic categories to bounded safe sentinel codes.

---

## 4. Acceptance Criteria Verification Matrix

| Acceptance Criterion | Implementation / Evidence | Status |
|---|---|---|
| Use fake storage and canonical writer doubles to reproduce the reserved-state then stale status write race without touching live GCP | `test_stale_cas_issuing_recovery_reconciles_gcs_lease_and_dispatches`, `test_revision_checking_writer_preserves_newer_status_on_bounded_retry`, and `test_revision_checking_writer_recovers_after_two_issued_cas_rejections` (exact-revision CAS doubles) | **LOCAL PASS; exact-head CI pending** |
| Use the existing verifier and exact request fingerprint to prove a lease payload matches the current approval without exposing bearer data | `verify_lease`, `_has_exact_lease_ownership`, `_exact_binding_errors`, `_sanitize_errors` (fixed known sentinels and fixed message for unmatched diagnostics) | **LOCAL PASS; exact-head CI pending** |
| Revalidate current status candidate manifest target action approval and request before persisting issued receipt; dispatch only after that receipt commits | `_status_still_reserved`, refreshed registry/manifest reads at each admission check, P1 dual-point expiry recheck, and `_commit_result` ordering before `dispatch()` | **LOCAL PASS; exact-head CI pending** |
| If exact lease binding cannot be proven or TTL has elapsed record a terminal non-dispatchable outcome and require a fresh Human/Ops request without reusing a nonce | `test_issuing_without_gcs_lease_terminates_blocked_without_loading_key`, `test_issuing_with_consumed_or_revoked_gcs_lease_terminates_blocked_without_loading_key`, `test_issuing_recovery_with_ttl_delay_blocks_and_does_not_dispatch`, `test_request_expiry_during_final_ref_validation_blocks_without_dispatch` and `test_request_expiry_during_commit_sync_blocks_dispatch` | **LOCAL PASS; exact-head CI pending** |
| Do not attribute a stale status event with no task_id to this issuance; require task-linked evidence for root-cause claims | Section 1 of this document; explicit epistemic boundaries and caveat declarations | **CAVEAT RETAINED** |
| Regression tests prove stale CAS does not cause duplicate signing dispatch or overwrite newer status and logs contain no secret material | `test_no_duplicate_sign_or_dispatch_on_recovery`, both exact-revision writer tests, `test_release_inputs_are_reloaded_after_cas_and_sync_callbacks`, `test_no_secret_or_bearer_material_in_logs_or_status`, `test_schema_version_bearing_signature_value_is_sanitized`, `test_durable_state_lookup_error_uses_safe_sentinel_not_raw_exception` | **LOCAL PASS; exact-head CI pending** |
| Only Supervisor signs or dispatches; submit a separate reviewed PR | In-memory signing key lifetime, single supervisor bridge entrypoint; PR #1377 | **REVIEW AND MERGE PENDING** |


## 5. Current Patch Verification

- `uv run pytest -q --tb=no .orchestrator/test_release_lease_integration.py` — exit 0.
- `uv run ruff check .orchestrator/release_lease_integration.py .orchestrator/test_release_lease_integration.py` — exit 0.
- `python3 -m py_compile .orchestrator/release_lease_integration.py .orchestrator/test_release_lease_integration.py` — exit 0.
- The current patch is local on the task branch; exact-head GitHub CI, owner finalization, and reviewer approval are pending. No live GCP mutation, lease signing, or Runtime Release dispatch occurred.
