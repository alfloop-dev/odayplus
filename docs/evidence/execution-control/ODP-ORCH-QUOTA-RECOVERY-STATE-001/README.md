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

## 5. Review Round 3 Remediation & Regression Handling

Addressed Codex2 review findings on PR #1305 head `0ffb100b602509cda898cc9d507dc76390ad1bec`:
1. **P1 — Multi-Epoch Clearance Coverage (`runtime_state.py:645-655`, `worker_failure_policy.py:930-975, 1290-1320`)**:
   - Retain all tombstones across providers and auth/run epochs using `_record_clearance_tombstone()`.
   - `_merge_provider_guardrails()` checks candidate pauses against all retained clearances in `merged_cleared.values()`.
   - Handles multi-clear disk interleavings: earlier clears survive later clears and delayed clears of older epochs do not replace newer clearance records.
2. **P1 — Durable Shared-Account Canary Admission Fencing (`runtime_state.py:740-775`, `worker_failure_policy.py:1470-1510`, `supervisor.py:1570-1600`)**:
   - Advance generation on canary recovery so disk merges preserve the fence.
   - At equal generation in `_merge_account_pool_runtime()`, `recovering` takes precedence over `healthy` unless the healthy entry possesses a newer `last_recovered_at`.
   - Dynamically fence uninitialized sibling pools sharing the authenticated identity in both `clear_provider_dispatch_pause()` and `account_pool_runtime_state()`.
3. **P1 — Failure-Kind & Failure-Epoch Guard Preservation (`worker_failure_policy.py:820-840, 1485-1510`)**:
   - Shared-auth fan-out preserves cooldowns for newer failure epochs and non-quota failures (e.g. `failure_kind="auth"`).
   - Canary success fanout in `record_account_pool_canary_success()` verifies that sibling pools do not carry non-quota failure kinds before restoring capacity.
4. **P2 — Structured Reopen Reason Assertions (`test_supervisor.py:21904-21920`)**:
   - Restored assertions verifying `--reason=review_finding` and message forwarding in `test_run_ai_status_forwards_extra_args`.

---

## 6. Verification Receipts

### Red Verification Receipts (Pre-Fix Baseline on Head `0ffb100b602509cda898cc9d507dc76390ad1bec`)
- **Reviewer Scratch Suite 1 (Clear History)**:
  - Command: `uv run pytest -q /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T031635Z-6abc7d82/test_clear_history_review.py`
  - Exit Code: `1` (3 failures)
  - Failure Reasons: Single provider clearance tombstone replaced by subsequent clear; stale writer resurrected cleared pause.
- **Reviewer Scratch Suite 2 (Pool Canary & Cooldown)**:
  - Command: `uv run pytest -q /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T031635Z-6abc7d82/review_pool/test_quota_pool_review.py`
  - Exit Code: `1` (4 failures)
  - Failure Reasons: Uninitialized sibling pool bypassed canary fence; newer quota epoch cooldown rewritten; auth failure promoted to healthy; disk save regressed recovering/0 to healthy/2.

### Green Verification Receipts (Post-Fix)

#### Command 1: Code Formatting & Whitespace Check
```bash
git diff --check
```
- **Exit Code**: `0`
- **Duration**: `0.055s`
- **Result**: Clean; no trailing whitespace or format issues.

#### Command 2: Runtime State Unit & Interleaved Concurrency Tests
```bash
uv run pytest -q .orchestrator/test_runtime_state.py
```
- **Exit Code**: `0`
- **Duration**: `2.310s`
- **Result**: `53 passed` (including `test_multi_clear_earlier_clear_survives_later_clear_and_stale_writer`, `test_delayed_old_clear_does_not_replace_newer_failure_clear`, `test_disk_clear_preserves_shared_auth_canary_fence`).

#### Command 3: Supervisor & Failure Policy Unit Tests
```bash
uv run pytest -q .orchestrator/test_supervisor.py -k "quota or pause or account_pool or config"
```
- **Exit Code**: `0`
- **Duration**: `3.410s`
- **Result**: `90 passed, 184 deselected` (including `QuotaClearAndCooldownRecoveryReviewTests`, `test_shared_auth_pool_without_runtime_entry_respects_canary_budget`, `test_shared_auth_newer_quota_epoch_keeps_cooldown`, `test_shared_auth_auth_failure_keeps_cooldown_after_other_pool_success`).

#### Command 4: Orchestrator Common Tests
```bash
uv run pytest -q .orchestrator/test_common.py
```
- **Exit Code**: `0`
- **Duration**: `2.111s`
- **Result**: `44 passed`.

#### Command 5: Reviewer Multi-Clear Interleaving Suite
```bash
uv run pytest -q /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T031635Z-6abc7d82/test_clear_history_review.py
```
- **Exit Code**: `0`
- **Duration**: `2.787s`
- **Result**: `3 passed`.

#### Command 6: Reviewer Pool Canary & Cooldown Suite
```bash
uv run pytest -q /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T031635Z-6abc7d82/review_pool/test_quota_pool_review.py
```
- **Exit Code**: `0`
- **Duration**: `2.625s`
- **Result**: `4 passed`.
