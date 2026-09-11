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

## 3. Root Cause Analysis

1. **Lost Provider Clear Resurrections**:
   - `merge_runtime_states()` in `runtime_state.py` merged workers, queues, and dispatch cursors, but lacked epoch-aware synchronization for `provider_guardrails["dispatch_pauses"]`.
   - When a CLI writer cleared a pause on disk, a concurrent supervisor with an in-memory copy of the older pause wrote its stale dictionary back during `save_runtime_state()`.
2. **Residual Account Pool Cooldown**:
   - `clear_provider_dispatch_pause()` in `worker_failure_policy.py` only popped entries from `provider_guardrails["dispatch_pauses"]`.
   - `account_pool_runtime` entries (such as `codex_bjoe`) remained locked in `"cooldown"` until `next_probe_at` (e.g., 02:37Z).
3. **Ambiguous Config Resolution Without `PANTHEON_STATUS_ROOT`**:
   - When invoking CLI commands with `--config /abs/path/.orchestrator/config.json` without `PANTHEON_STATUS_ROOT` set, relative paths inside `config["paths"]` resolved against `ROOT` (the runtime installation directory) rather than the targeted repository root.

---

## 4. Remediation Implementation

1. **Epoch-Aware Clearance Records (`cleared_pauses`)**:
   - Added durable `cleared_pauses` tracking inside `provider_guardrails` to record `cleared_at`, `cleared_paused_at`, `auth_identity_hash`, `worker_run_id`, and `task_id`.
   - `_is_pause_entry_cleared()` in `runtime_state.py` and `worker_failure_policy.py`:
     - Compares `paused_at`, `worker_run_id`, and `auth_identity_hash` against clearance records.
     - Stale in-memory pauses matching or preceding the clearance are safely discarded.
     - New legitimate quota failures occurring in the same second or after the clearance with a different run ID / epoch (`paused_at == cleared_at` with distinct `worker_run_id` or `paused_at > cleared_at`) are preserved.
2. **Account Pool Cooldown Recovery into Canary Mode**:
   - Enhanced `clear_provider_dispatch_pause()` in `worker_failure_policy.py`:
     - Identifies matching account pools in `"cooldown"` for the target provider and matching auth identity hash.
     - Verifies failure epoch (`last_worker_run_id` and `last_failure_at` matching the removed pause).
     - Preserves non-quota failures (such as `"auth"` failures), unrelated newer quota cooldowns, and cooldowns for other account profiles (`auth-b` vs `auth-a`).
     - Transitions eligible pools from `"cooldown"` to `"recovering"` with bounded canary concurrency (`effective_concurrency = min(1, configured_limit)`).
     - Upon subsequent zero-exit worker completion, `record_account_pool_canary_success()` automatically scales the pool to full configured concurrency.
     - Preserves disabled, auth-blocked, or unverified pools.
3. **Account Pool State Merging (`_merge_account_pool_runtime`)**:
   - Added epoch/generation-aware merging for `account_pool_runtime` entries in `runtime_state.py`.
   - Newer failure generations take precedence; for equal generations, recovering/healthy states take precedence over stale cooldowns.
4. **Durable Auth-Rotation Pause Expiry**:
   - `expire_provider_dispatch_pauses()` and `current_provider_dispatch_pause()` in `worker_failure_policy.py`:
     - When an account identity change is detected, records a tombstone in `cleared_pauses` before popping the pause, ensuring stale disk saves cannot resurrect the old account's pause.
5. **Preserving Task Failure Streak Resets**:
   - `_merge_provider_guardrails()` preserves in-memory `task_failure_streaks` dict state without resurrecting keys removed by `clear_task_failure_streak()`.
6. **Authoritative Absolute `--config` Anchoring**:
   - Updated `supervisor.py` CLI initialization: when `status_root` is unset and `--config` is passed, relative paths in `config["paths"]` are anchored via `anchor_config_paths()` to the target repository directory.

---

## 5. Verification Receipts

### Command 1: Code Formatting & Whitespace Check
```bash
git diff --check
```
- **Exit Code**: `0`
- **Result**: Passed cleanly with no whitespace or EOF defects.

### Command 2: Runtime State Unit & Interleaved Concurrency Tests
```bash
uv run pytest -q .orchestrator/test_runtime_state.py
```
- **Exit Code**: `0`
- **Result**: `47 passed` (including `QuotaRecoveryRuntimeStateTests`, same-second new failure retention, streak reset persistence).

### Command 3: Supervisor & Failure Policy Unit Tests
```bash
uv run pytest -q .orchestrator/test_supervisor.py -k "quota or pause or account_pool or config"
```
- **Exit Code**: `0`
- **Result**: `83 passed, 187 deselected`.

### Command 4: Orchestrator Common Tests
```bash
uv run pytest -q .orchestrator/test_common.py
```
- **Exit Code**: `0`
- **Result**: `44 passed`.

### Command 5: Isolated Reviewer Regression Fixture
```bash
uv run pytest -v /home/lupin/odayplus/.orchestrator/worker-runtime/scratch/codex-20260911T023504Z-d8c0730f/test_quota_review.py
```
- **Exit Code**: `0`
- **Result**: `7 passed` (all 6 reviewer regression assertions and 1 control passed).
