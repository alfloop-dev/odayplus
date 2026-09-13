# Verification & Remediation Evidence: ODP-ORCH-QUOTA-RECOVERY-STATE-001

## 1. Overview & Context

- **Task ID**: `ODP-ORCH-QUOTA-RECOVERY-STATE-001`
- **Title**: 修正 quota 解除被舊狀態復活及帳號 cooldown 殘留
- **Owner**: Antigravity
- **Reviewer**: Codex2
- **Target Deliverables**:
  - `.orchestrator/runtime_state.py`
  - `.orchestrator/worker_failure_policy.py`
  - `.orchestrator/supervisor.py`
  - `.orchestrator/test_runtime_state.py`
  - `.orchestrator/test_supervisor.py`
  - `docs/evidence/execution-control/ODP-ORCH-QUOTA-RECOVERY-STATE-001/README.md`

---

## 2. Incident Reproduction & Live Audit Analysis

Observational evidence recorded in `/home/lupin/odayplus/support/handoffs/parallel-dispatch-20260911/`:
1. `codex-live-quota-audit.json`:
   - At `2026-09-11T02:02:49Z`, querying `account/rateLimits/read` under the supervisor's active environment confirmed Codex weekly `usedPercent = 2%` and `rateLimitReachedType = null`.
   - Historical worker failure at `2026-09-11T01:37:15Z` was genuine, but the subsequent manual pause clearance was erroneously overridden.
2. `quota-recovery-restart.json`:
   - At `2026-09-11T02:04:50Z`, official `--clear-provider-pause codex` executed successfully.
   - At `2026-09-11T02:06Z`, the running supervisor's stale in-memory snapshot (`paused_at: 01:37:15Z`) was saved back to `state.json`, resurrecting the pause.
   - Supervisor was stopped and cleared again at `02:07:28Z` and restarted by watchdog with new PID `2957832`.
3. `review-quota-recovery-routing.json`:
   - Residual account pool cooldown on `codex_bjoe` remained active until `02:37:00Z` despite provider pause being cleared. Review tasks were temporarily routed to available `Codex2` slots.

---

### 3. Root Cause Analysis

1. **Lost Provider Clear Resurrections**:
   - `merge_runtime_states()` in `runtime_state.py` merged workers, queues, and dispatch cursors, but lacked epoch-aware synchronization for `provider_guardrails["dispatch_pauses"]`.
   - When a single clearance tombstone was stored per provider, clearing a subsequent pause or delayed older clear replaced the existing tombstone, resurrecting previously cleared pauses upon stale saves.
2. **Residual Account Pool Cooldown & Canary Admission Fencing**:
   - `clear_provider_dispatch_pause()` in `worker_failure_policy.py` only popped entries from `provider_guardrails["dispatch_pauses"]`.
   - When account pools on the same auth identity were recovered, uninitialized sibling pools or disk merge precedence (`healthy` over `recovering` at equal generation) allowed full concurrency bypass before canary success.
   - Blanket fan-out inappropriately rewrote newer quota cooldowns and non-quota (e.g. auth) failure records.
3. **Ambiguous Config Resolution Without `PANTHEON_STATUS_ROOT`**:
   - When invoking CLI commands with `--config /abs/path/.orchestrator/config.json` without `PANTHEON_STATUS_ROOT` set, relative paths inside `config["paths"]` resolved against `ROOT` (the runtime installation directory) rather than the targeted repository root.

---

## 4. Remediation Implementation

1. **Multi-Epoch Clearance Records (`cleared_pauses`)**:
   - Durable `cleared_pauses` inside `provider_guardrails` records primary provider keys and distinct composite epoch keys (`{provider}::{auth}::{run_id}::{paused_at}::{cleared_at}`).
   - `_is_pause_entry_cleared()` in `runtime_state.py` and `worker_failure_policy.py`:
     - Checks all retained clearance records.
     - Stale in-memory pauses matching or preceding the clearance epoch are safely discarded.
     - New legitimate quota failures occurring after clearance are preserved.
2. **Account Pool Cooldown Recovery into Canary Mode**:
   - Enhanced `clear_provider_dispatch_pause()` in `worker_failure_policy.py`:
     - Identifies matching account pools in `"cooldown"` for the target provider and matching auth identity hash.
     - Verifies failure epoch (`last_worker_run_id` and `last_failure_at` matching the removed pause).
     - Preserves non-quota failures (such as `"auth"` failures), unrelated newer quota cooldowns, and cooldowns for other account profiles (`auth-b` vs `auth-a`).
     - Transitions eligible pools from `"cooldown"` to `"recovering"` with bounded canary concurrency (`effective_concurrency = min(1, configured_limit)`) and increments generation.
     - Upon subsequent zero-exit worker completion, `record_account_pool_canary_success()` automatically scales eligible pools to full configured concurrency while preserving unrelated non-quota failures.
3. **Account Pool State Merging (`_merge_account_pool_runtime`)**:
   - Epoch/generation-aware merging for `account_pool_runtime` entries in `runtime_state.py`.
   - Newer failure generations take precedence; for equal generations, recovering canary fences take precedence over unrecovered healthy snapshots, preventing disk saves from regressing canary fencing.
4. **Durable Auth-Rotation Pause Expiry**:
   - `expire_provider_dispatch_pauses()` and `current_provider_dispatch_pause()` in `worker_failure_policy.py`:
     - When an account identity change is detected, records a tombstone in `cleared_pauses` before popping the pause, ensuring stale disk saves cannot resurrect the old account's pause.
5. **Preserving Task Failure Streak Resets**:
   - `_merge_provider_guardrails()` preserves in-memory `task_failure_streaks` dict state without resurrecting keys removed by `clear_task_failure_streak()`.
6. **Authoritative Absolute `--config` Anchoring**:
   - Updated `supervisor.py` CLI initialization: when `status_root` is unset and `--config` is passed, relative paths in `config["paths"]` are anchored via `anchor_config_paths()` to the target repository directory.

---

## 5. Review Round 3 & 4 Remediation & Base Advance

1. **Base Advance & Divergence Convergence**:
   - Merged current base from `origin/dev` (`21929049e5c1`) into `task/ODP-ORCH-QUOTA-RECOVERY-STATE-001` (merge commit `890ea228`).
2. **Account-Pool Merging Discards Newer Failure (`runtime_state.py`)**:
   - `_merge_account_pool_runtime()` compares `last_failure_at` first so newer failure records survive stale recovery states regardless of disk write interleaving order.
3. **Conflicting Run IDs & Canary Budget Fencing (`worker_failure_policy.py`)**:
   - Conflicting run IDs (`rem_run != pool_run`) never fall back to same-second timestamp matches.
   - Enforced maximum aggregate canary budget of 1 slot across all sibling pools sharing `auth_identity_hash`.
4. **Canary Promotion Provenance & Auth Binding (`worker_failure_policy.py`)**:
   - Enforced `started_at >= last_probe_at` in `record_account_pool_canary_success()`.
   - Sibling promotion strictly checks matching `auth_identity_hash`.
5. **Inherited CODEX_HOME Support (`worker_failure_policy.py`)**:
   - `provider_auth_identity_hash()` checks `os.environ["CODEX_HOME"]` before defaulting to `Path.home() / ".codex"`.
6. **Same-Second Blind Clearance Tombstone (`runtime_state.py`, `worker_failure_policy.py`)**:
   - Updated `_is_pause_entry_cleared()` so clearance records without `cleared_paused_at` or matching run ID do not erase same-second new failures (`p_at == c_at`).
7. **Time Source Mock Binding (`worker_failure_policy.py`)**:
   - Bound `mark_provider_dispatch_paused()` time evaluation to `datetime.now(UTC)` so test patches on `supervisor.datetime` take effect properly.

---

## 6. Review Round 5 Remediation & Base Advance

1. **Base Advance (`origin/dev` `b20118700dd5`)**:
   - Composed origin base `b20118700dd5` cleanly into task branch `task/ODP-ORCH-QUOTA-RECOVERY-STATE-001` via merge commit `4b6cf65c`.
2. **Same-Second Failure Runs Differentiated During Disk Merge (`runtime_state.py:805-840`)**:
   - `_merge_account_pool_runtime()` explicitly checks `last_worker_run_id` and `auth_identity_hash` before treating entries as the same failure epoch.
   - When run IDs or auth hashes differ, distinct same-second failures are recognized and active `cooldown` states survive over older recovery snapshots in both save orders.
3. **Release/Reassign Shared Canary Lease on Failure (`supervisor.py:1587-1645`, `worker_failure_policy.py:779-795`)**:
   - `account_pool_runtime_state()` and `account_pool_effective_concurrency()` check `int(effective_concurrency) > 0` when checking `is_fenced_by_shared_canary`, distinguishing a waiting fence (`effective_concurrency = 0`) from an active canary holder.
   - When a canary fails on one pool, `mark_account_pool_cooldown()` puts all same-auth recovering/healthy siblings into cooldown with matching `next_probe_at`.
   - Upon cooldown expiry, exactly one pool claims the canary slot (effective concurrency 1) while sibling pools remain properly fenced (0), resolving the zero-slot sibling deadlock.
4. **Dispatch Provenance & Timing/Auth Validation (`supervisor.py:2315-2330`, `worker_failure_policy.py:805-870`)**:
   - `start_worker_for_request()` records `started_at`, `created_at`, `lease_acquired_at`, and dispatch-time `auth_identity_hash` on worker records.
   - `record_account_pool_canary_success()` parses `started_at`/`lease_acquired_at` and verifies `worker_started >= last_probe_at`.
   - Sibling pool promotion strictly validates matching `auth_identity_hash` against the worker's persisted dispatch-time auth provenance, preventing pre-clear sibling success from certifying canary recovery and preventing rotated auth workers from certifying other auth siblings.

---

## 7. Traceable Red Evidence & Reviewer Reproduction Receipts

Review report: `/home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T041551Z-f78b2255/review.md`
Tested PR Head: `cde8be50abf31d871d6bc2a7e64e432ade6736bd`

1. **Runtime Merge Same-Second Collapsing**:
   - **Command**: `uv run pytest -q "$ORCH_SCRATCH_DIR/test_review_runtime_same_second.py" --basetemp="$ORCH_SCRATCH_DIR/runtime-same-second-pytest-temp" --junitxml="$ORCH_SCRATCH_DIR/runtime-same-second-junit.xml"`
   - **Receipt**: `runtime-same-second-receipt.json` (Terminal chunk: `8db016`)
   - **Exit Code**: `1` (Duration: 2.736s)
   - **Result**: 2 failed regressions and 2 passing different-second controls.
2. **Canary Retry Deadlock on Zero-Slot Sibling**:
   - **Command**: `PYTHONPATH=.orchestrator:scripts uv run pytest -q "$ORCH_SCRATCH_DIR/test_review_cli_canary_deadlock.py" --junitxml="$ORCH_SCRATCH_DIR/review-cli-canary-deadlock.junit.xml"`
   - **Receipt**: `review-cli-canary-deadlock.receipt.json` (Terminal chunk: `7b2f6a`)
   - **Exit Code**: `1` (Duration: 2.841s)
   - **Result**: 1 failed regression (`AssertionError: No canary remains eligible after cooldown expiry`).
3. **Production Canary Provenance & Auth Rotation Attribution**:
   - **Command**: `uv run pytest -q "$ORCH_SCRATCH_DIR/test_review_canary_provenance_expected.py" --junitxml="$ORCH_SCRATCH_DIR/review_canary_provenance_expected.junit.xml"`
   - **Receipt**: `review_canary_provenance_expected.receipt.json` (Terminal chunk: `3447e5`)
   - **Exit Code**: `1` (Duration: 2.734s)
   - **Result**: 2 failed regressions (`test_real_dispatched_preclear_sibling_cannot_certify_canary` and `test_real_dispatched_auth_a_worker_cannot_certify_current_auth_b`).

---

## 8. Verification Receipts (Post-Fix)

All verification commands executed on the updated task branch and verified against required acceptance criteria:

### 1. Code Formatting & Whitespace Check
- **Command**: `git diff --check`
- **Exit Code**: `0`
- **Duration**: `0.02s`
- **Result**: Clean; no trailing whitespace or format issues.

### 2. Ruff Linter
- **Command**: `uv run ruff check .orchestrator/runtime_state.py .orchestrator/worker_failure_policy.py .orchestrator/supervisor.py .orchestrator/test_runtime_state.py .orchestrator/test_supervisor.py`
- **Exit Code**: `0`
- **Duration**: `0.15s`
- **Result**: `All checks passed!`

### 3. Runtime State Suite (including same-second failure merge regressions)
- **Command**: `uv run pytest -q .orchestrator/test_runtime_state.py`
- **Exit Code**: `0`
- **Duration**: `2.41s`
- **Result**: `58 passed in 2.41s`

### 4. Supervisor Focused Quota & Account Pool Suite
- **Command**: `uv run pytest -q .orchestrator/test_supervisor.py -k "quota or pause or account_pool or config"`
- **Exit Code**: `0`
- **Duration**: `6.54s`
- **Result**: `96 passed, 608 deselected in 6.54s`

### 5. Common & Failure Policy Suite
- **Command**: `uv run pytest -q .orchestrator/test_common.py`
- **Exit Code**: `0`
- **Duration**: `1.88s`
- **Result**: `44 passed in 1.88s`

### 6. Reviewer Regression Suites (Reviewer Fixtures)
- **Command**: `uv run pytest -q /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T041551Z-f78b2255/test_review_runtime_same_second.py /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T041551Z-f78b2255/test_review_cli_canary_deadlock.py /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T041551Z-f78b2255/test_review_canary_provenance_expected.py`
- **Exit Code**: `0`
- **Duration**: `2.92s`
- **Result**: `7 passed in 2.92s`

### 7. Full Supervisor Test Suite
- **Command**: `uv run pytest -q .orchestrator/test_supervisor.py`
- **Exit Code**: `0`
- **Duration**: `94.61s`
- **Result**: `691 passed in 94.61s`



## 9. Review Round 6 Remediation & Multi-Epoch Canary Fencing (2026-09-12)

### Findings & Root Causes Addressed

1. **P1 — Stale healthy snapshot removes auth cooldown after provider clear (`runtime_state.py:732-747,761-765`)**:
   - *Problem*: `_merge_account_pool_runtime()` treated any matching clearance tombstone as authorization to discard a pool cooldown, even when `failure_kind == "auth"`. A stale save from a pre-failure healthy snapshot restored capacity from 0 to 3.
   - *Fix*: Restricted `cleared_cooldown()` in `_merge_account_pool_runtime()` to quota/capacity failures (`"quota_terminal"`, `"capacity"`, `"capacity_retryable"`). Non-quota cooldowns (e.g. `"auth"`) are preserved through merging.

2. **P1 — Same-second pre-clear work certifying canary recovery (`supervisor.py:2315-2365`, `worker_failure_policy.py:829-865`)**:
   - *Problem*: Subsecond dispatch occurred before same-second failure and clear, and second-truncated timestamps allowed `worker_started == probe_started`, enabling the pre-clear worker to certify canary recovery.
   - *Fix*: Bound canary admission and completion to durable `recovery_generation` and `dispatched_pool_state` in `start_worker_for_request()`. `record_account_pool_canary_success()` verifies that the worker was dispatched in the active recovery generation (`recovery_generation >= generation` and `dispatched_pool_state == "recovering"`).

3. **P1 — Legacy healthy sibling entries bypassing shared canary limit (`worker_failure_policy.py:785-805,829-875`, `supervisor.py:1576-1665`)**:
   - *Problem*: Legacy entries lacking `auth_identity_hash` were not matched during shared cooldown fencing and canary capacity evaluation, allowing same-account capacity 1+2=3.
   - *Fix*: Introduced `configured_account_pool_auth_hash()` to reconcile missing auth identity against configured provider provenance during admission, cooldown fencing, recovery fencing, and canary success.

4. **P2 — Auth rotation stranding successful current-account workers in recovery (`supervisor.py:1576-1635`, `worker_failure_policy.py:830-875`)**:
   - *Problem*: After auth rotation (e.g. A -> B), existing pools retained old auth A without a rebind path during admission. The successful B worker was rejected by canary certification (`worker_auth != pool_auth`).
   - *Fix*: In `account_pool_runtime_state()`, when current auth differs from existing pool auth, rebinds the pool to the new auth with a bounded canary recovery slot (`effective_concurrency = min(1, configured_limit)`). Upon canary success, `record_account_pool_canary_success()` verifies `worker_auth == pool_auth == current_auth == "auth-b"`, restoring configured capacity to pool A while leaving unverified old-auth pools in recovery.

---

### Verification Receipts (Round 6 Exact-Head Verification)

- **Reviewer Invocations (Green Verification)**:
  1. `uv run pytest -q /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260912T142938Z-89b134bf/test_review_auth_pool_stale_clear.py`: `4 passed in 2.38s` (Exit code: 0)
  2. `uv run pytest -q /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260912T142938Z-89b134bf/test_review_auth_canary_lifecycle.py`: `5 passed in 2.51s` (Exit code: 0)
  3. `uv run pytest -q /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260912T142938Z-89b134bf/test_review_auth_canary_lifecycle.py -k "same_second"`: `1 passed, 4 deselected in 1.95s` (Exit code: 0)

- **Declared Exact-Head Verification Suites**:
  1. `git diff --check`: Exit code `0` (Clean formatting & whitespace)
  2. `uv run pytest -q .orchestrator/test_runtime_state.py`: Exit code `0` (`69 passed in 2.52s`)
  3. `uv run pytest -q .orchestrator/test_supervisor.py -k "quota or pause or account_pool or config"`: Exit code `0` (`99 passed, 608 deselected in 6.78s`)
  4. `uv run pytest -q .orchestrator/test_common.py`: Exit code `0` (`44 passed in 1.84s`)
  5. `uv run ruff check .orchestrator/runtime_state.py .orchestrator/worker_failure_policy.py .orchestrator/supervisor.py .orchestrator/test_runtime_state.py .orchestrator/test_supervisor.py`: Exit code `0` (`All checks passed!`)


## Admission-window supplement

Capture auth, start time and per-pool recovery epoch snapshots before adapter delivery. A clear or credential rotation while delivery is running must not relabel the worker that was already dispatched. Completion compares the durable epoch of both its own pool and any sibling it would promote; a later same-second sibling recovery remains unverified. An explicit empty snapshot identifies workers dispatched before recovery. Existing triple-auth and legacy checks remain.

Three deterministic real start-worker callback tests cover clear during delivery, auth rotation during delivery and a newer sibling epoch. Final selected suite: 163 passed. Identical production source also passed all 12 reviewer/admission fixtures and 155 previous regressions. Source hashes and original command/exit/duration receipts are in `admission-window-verification.json`; adjacent logs retain the two failing pre-patch cases and subsequent passing runs.


The CI completion-flow fixtures now carry the same pool admission state and epoch snapshots as dispatched canaries. Their Codex identity is mocked, so no host credentials are read. Negative no-progress, rejected-seal and historical-terminal cases also receive valid admission provenance, ensuring they still test completion guards. All 15 SuccessfulWorkerPostconditionTests and 3 admission tests pass; see `postcondition-admission-verification.json`. Production guards are unchanged by this fixture correction.


Missing worker auth is now covered explicitly: an old worker with no admission record and an admitted worker with no saved auth must both remain unverified, while known A/B identities retain negative/positive controls. The completion-time auth fallback experiment fails the two missing-auth cases; this branch passes all 7 admission cases plus 15 completion-flow cases (22 total). The older three-case auth matrix covers known auth identities only; it was not evidence for missing-auth behavior. See `missing-worker-auth-verification.json`.


## Durable recovery continuation

A cleared provider pause alone cannot select an older healthy pool snapshot: the alternative must represent a successor failure/recovery epoch. Auth rotation retains exact superseded pool epochs, so current-auth bounded recovery survives a stale cooldown save without masking newer failure. Real disk tests cover A to B to C rotation, stale writers in both directions, later failure preservation, and expiry with same-second earlier healthy epochs. Focused verification: 97 passed and 26 subtests. Admission baseline full verification after make bootstrap: 2912 passed, 636 subtests. Final combined full verification follows separately; initial failures are retained.


## Final combined verification

Original task-branch head 8562a4951b82 passed the entire CI-equivalent orchestrator suite: 2920 passed, 6 skipped, 10 deselected, 636 subtests, exit 0. The final commit adds only the known-auth/missing-admission parameter (eight admission cases pass) and these receipts. Production sources are byte-identical to that fully tested head. Boundary checks cover 1158 files and ruff passes. See final-continuation-verification.json and original full JUnit/log/receipt. The task remains subject to a new bounded Human/Ops continuation at epoch 8 and independent exact-head review/required CI.


## Legacy sibling admission follow-up

An already-running persisted worker from before `dispatched_recovery_epochs` existed could still attest its own recovery generation but had no evidence for sibling epochs. The old conditional skipped sibling validation entirely when the snapshot was missing or malformed. Such workers could promote even a newer same-second sibling recovery. Sibling promotion now always requires an explicit matching dispatch snapshot; legacy own-pool admission remains unchanged.

The expanded actual-dispatch regression crosses recorded/missing/malformed snapshots with unchanged/newer sibling generations. Red: 4 failures and 2 controls; green: 28 admission/postcondition checks and 8 subtests; baseline: 114 quota/pause/pool/config/postcondition checks and 77 subtests. Original receipts and source fingerprints are in `legacy-sibling-verification.json`.
