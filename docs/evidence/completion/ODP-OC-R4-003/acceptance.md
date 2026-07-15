# ODP-OC-R4-003 Acceptance

## Acceptance Criteria

1. The four-light chips deterministically change the API query and visible queue.
   - Covered by `tests/e2e/operator-store-ops.spec.ts`.
   - API query proof: `light=operations&lightStatus=red`.
   - Visible queue proof: only `ISS-1024` remains for Operations Red.

2. `ISS-1024` can complete a valid lifecycle and reload shows persisted state.
   - Covered by `tests/contract/test_operator_api.py`.
   - Durable sequence: camera purpose, triage, assign, actions, field-report, outcome.
   - Restart proof: a fresh app backed by the same SQLite file reloads `ISS-1024` as `closed`.

3. Invalid transitions return 409 and duplicate idempotency keys do not duplicate audit rows.
   - Covered by `test_store_ops_invalid_transition_409_and_idempotency_dedupes_audit`.
   - `outcome` from `new` returns 409.
   - Replaying the same triage idempotency key returns `idempotentReplay=true` and does not increase Store Ops audit row count.

4. Camera evidence remains locked until a permitted purpose is recorded and audited.
   - Covered by contract and e2e tests.
   - Invalid purpose returns 422.
   - Valid purpose removes `lockedReason`, records `evidence.camera_purpose.recorded`, and survives durable restart.

## Residual Risk

- The Store Ops workspace keeps fixture fallback for local demos when the API is unavailable.
- `OperatorConsole.tsx` still has legacy generic workflow callbacks, but Store Ops dialogs now write directly to the task-scoped Store Ops API and refresh the Store Ops workspace via event.
