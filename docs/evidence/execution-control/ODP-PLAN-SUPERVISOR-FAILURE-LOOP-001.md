# Execution Control Evidence: ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001

- **Task ID**: `ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001`
- **Title**: 補齊 Supervisor failure-loop 自動轉派覆蓋
- **Owner**: `Antigravity4`
- **Reviewer**: `Codex6`
- **Phase**: `P0 Execution Control`
- **Date**: `2026-07-31`

---

## 1. Executive Summary & Root Cause Context

### Background & Round-1 Remediation
During live supervision, task `ODP-PLAN-SITESCORE-OUTCOME-001` reached terminal failure threshold=2 on `Antigravity4`. Because `.orchestrator/config.json` lacked explicit `owner_fallbacks` and `reviewer_fallbacks` entries for `Antigravity3-7` and several Codex/Claude/Gemini aliases, the supervisor found no viable fallback target, causing a permanent failure-loop deadlock.

During Round 1 review by `Codex6`, five blocking control findings (R1–R5) were identified and remediated in batch on HEAD `8664a115`:

1. **R1 (P0: Concurrent claim / main-loop state preservation)**: Fixed `save_runtime_state` in `.orchestrator/runtime_state.py` to safely merge `disk_state` with in-memory state before writing. Preserves live worker records and active queue events added by concurrent claims (`--claim-agent`). Updated event key eligibility so `owned_ready_dispatch` events remain valid during `todo -> in_progress` status transitions for the same owner.
2. **R2 (P0: Fail-closed Human/Ops and non-dispatchable gates)**: Added explicit checks `task_is_human_gate(task)` and `bool(task.get("non_dispatchable"))` to `maybe_reassign_task_after_worker_failure` in `.orchestrator/supervisor.py`. Tasks with human-gate metadata (`task_class == "human_gate"`, `human_required_roles`, `gate_status.startswith("pending_human")`) or `non_dispatchable == true` return `None` immediately without persisting reassignment.
3. **R3 (P1: Enabled & available fallback matrix viability)**: Enhanced `agent_dispatch_disabled` to check `enabled: false`, `disabled: true`, and `status: disabled/unavailable` in agent and provider configs. Enhanced `first_viable_agent` to enforce full viability checks including Human/Ops exclusion, provider runtime config block reasons, dispatch pauses, quota group limits, and task sidecar compatibility.
4. **R4 (P1: Safe post-drain rollout record)**: Preserved the no-restart boundary during active worker execution. Documented the safe post-drain rollout sequence and drain receipts.
5. **R5 (P0: Status check 422 transactional outbox & backup preservation)**: Updated `scripts/ai_status.py` so GitHub status check emission failures (such as HTTP 422 for unpushed SHAs) do not roll task status back to `todo`. Enqueued failed status check emissions into a retryable `status_check_outbox` for reconciliation after remote commit visibility. Preserved dirty worktree backups (`.orchestrator/worktree-dirt-backups/*.patch`).

---

## 2. Complete Agent Fallback Matrix

| Agent Alias | Provider | Owner Fallbacks | Reviewer Fallbacks |
| :--- | :--- | :--- | :--- |
| **Antigravity** | `antigravity` | Antigravity2, Antigravity3, Antigravity4, Antigravity5, Antigravity6, Antigravity7, Codex2, Codex, Codex6, Claude2, Claude | Same as Owner |
| **Antigravity2** | `antigravity2` | Antigravity, Antigravity3, Antigravity4, Antigravity5, Antigravity6, Antigravity7, Codex2, Codex, Codex6, Claude2, Claude | Same as Owner |
| **Antigravity3** | `antigravity` | Antigravity4, Antigravity5, Antigravity6, Antigravity7, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude | Same as Owner |
| **Antigravity4** | `antigravity` | Antigravity3, Antigravity5, Antigravity6, Antigravity7, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude | Same as Owner |
| **Antigravity5** | `antigravity` | Antigravity6, Antigravity7, Antigravity4, Antigravity3, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude | Same as Owner |
| **Antigravity6** | `antigravity` | Antigravity5, Antigravity7, Antigravity4, Antigravity3, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude | Same as Owner |
| **Antigravity7** | `antigravity` | Antigravity6, Antigravity5, Antigravity4, Antigravity3, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude | Same as Owner |
| **Claude** | `claude` | Claude2, Claude3, Codex, Codex2, Codex6, Antigravity, Antigravity2, Antigravity3 | Same as Owner |
| **Claude2** | `claude` | Claude, Claude3, Codex, Codex2, Codex6, Antigravity, Antigravity2, Antigravity3 | Same as Owner |
| **Claude3** | `claude` | Claude2, Claude, Codex, Codex2, Codex6, Antigravity, Antigravity2, Antigravity3 | Same as Owner |
| **Codex** | `codex` | Codex2, Codex6, Codex3, Codex4, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity, Antigravity2 | Same as Owner |
| **Codex2** | `codex` | Codex, Codex6, Codex3, Codex4, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity, Antigravity2 | Same as Owner |
| **Codex3** | `codex` | Codex2, Codex6, Codex, Codex4, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity | Same as Owner |
| **Codex4** | `codex` | Codex2, Codex6, Codex, Codex3, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity | Same as Owner |
| **Codex5** | `codex` | Codex6, Codex2, Codex, Codex8, Codex9, Claude, Claude2, Antigravity3, Antigravity4 | Same as Owner |
| **Codex6** | `codex` | Codex2, Codex, Codex8, Codex9, Claude2, Claude, Antigravity3, Antigravity7 | Same as Owner |
| **Codex7** | `codex` | Codex6, Codex2, Codex, Codex8, Codex9, Claude, Claude2, Antigravity | Same as Owner |
| **Codex8** | `codex` | Codex9, Codex6, Codex2, Codex, Claude2, Claude, Antigravity3, Antigravity7 | Same as Owner |
| **Codex9** | `codex` | Codex8, Codex6, Codex2, Codex, Claude2, Claude, Antigravity3, Antigravity7 | Same as Owner |
| **CodexCoordinator** | `codex` | Codex6, Codex2, Codex, Codex8, Codex9, Claude2, Claude, Antigravity7 | Same as Owner |
| **Gemini** | `gemini` | Gemini2, Codex, Codex2, Claude, Claude2, Antigravity, Antigravity2 | Same as Owner |
| **Gemini2** | `gemini` | Gemini, Codex, Codex2, Claude, Claude2, Antigravity, Antigravity2 | Same as Owner |
| **Copilot** | `copilot` | Codex, Codex2, Claude, Claude2, Antigravity, Antigravity2 | Same as Owner |
| **Human/Ops** | `human` | *N/A (Fail-Closed Human Gate)* | *N/A (Fail-Closed Human Gate)* |

---

## 3. Failure-Loop Drill & Test Verification Receipts

### Verification Commands & Results
```bash
PYTHONPATH=.orchestrator python3 -m unittest test_supervisor test_model_rotation
# Result: Ran 263 tests in 8.976s - OK

python3 .orchestrator/doctor.py
# Result: Exit 0 (Workspace & Providers verified clean)

git diff --check
# Result: Exit 0 (Clean, no whitespace issues)
```

### Verified R1–R5 Control Assertions

1. `test_concurrent_claim_main_loop_state_preservation` (R1):
   - Proves matching `owned_ready_dispatch` event and live worker PID (`os.getpid()`) survive `todo -> in_progress` status sync and main-loop save.
   - Proves queue event is reconciled to active worker with status `started` / `manual_pending` (not `wake_skipped`).
   - Proves a subsequent dispatch pass does not start a second worker for the task.

2. `test_fail_closed_human_gate_task_metadata_and_non_dispatchable` (R2):
   - Proves tasks with `task_class == "human_gate"`, `human_required_roles`, `gate_status == "pending_human..."`, or `non_dispatchable == True` fail closed.
   - `maybe_reassign_task_after_worker_failure` returns `None` without invoking `persist_task_reassignment`.

3. `test_full_agent_matrix_and_negative_viability_coverage` (R3):
   - Proves fallback list derivation for all 23 enabled auto-dispatch agents in both `owner` and `reviewer` roles.
   - Proves `first_viable_agent` rejects disabled agents (`enabled: false`), paused agents, Human/Ops targets, and sidecar-only incompatibilities.

4. `test_status_check_http_422_failure_injection_outbox_transactional` (R5):
   - Injects HTTP 422 failure response from GitHub status API (unpushed commit SHA).
   - Proves `ai_status.py` updates task status to `in_progress` without rolling back to `todo`.
   - Proves status check emission failure is enqueued into `status_check_outbox`.
   - Proves worktree dirt backup `.orchestrator/worktree-dirt-backups/*.patch` is preserved.

---

## 4. Post-Drain Supervisor Rollout & Restart Protocol

> [!IMPORTANT]
> Do NOT restart the live Supervisor while active workers are running. Prepare code, tests, and exact-head PR handoff first.

### Post-Drain Runtime Rollout Sequence
1. **Worker Drain Check**: Verify all active worker tasks have completed or reached safe checkpoints.
2. **Merge & Pull**: Merge task PR into `dev` tip.
3. **Deploy Config Update**: Copy updated `owner_fallbacks` and `reviewer_fallbacks` from `.orchestrator/config.example.json` into `/home/lupin/oday-plus-supervisor-live/.orchestrator/config.json`.
4. **Restart Supervisor**:
   ```bash
   sudo systemctl restart pantheon-supervisor
   ```
5. **Verify Live PID & Health**:
   ```bash
   systemctl status pantheon-supervisor
   python3 scripts/supervisor_runtime_health.py
   ```
