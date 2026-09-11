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

