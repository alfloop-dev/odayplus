# Execution Control Evidence: ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001

- **Task ID**: `ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001`
- **Title**: 補齊 Supervisor failure-loop 自動轉派覆蓋
- **Owner**: `CodexCoordinator`
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
# Result: Ran 267 tests in 1.565s - OK

python3 -m unittest scripts.test_ai_status
# Result: Ran 101 tests in 0.632s - OK

python3 .orchestrator/doctor.py
# Result: Exit 0 (Workspace & Providers verified clean)

python3 -m ruff check .orchestrator/common.py .orchestrator/runtime_state.py \
  .orchestrator/supervisor.py .orchestrator/test_supervisor.py \
  scripts/ai_status.py scripts/test_ai_status.py
# Result: All checks passed

git diff --check "$(git merge-base origin/dev HEAD)"..HEAD
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

---

## 5. Coordinator completion addendum — R6–R8

### R6: configured `dev` target and squash-merge closeout

`scripts/ai_status.py` now falls back to tracked
`.orchestrator/config.example.json` when a task worktree has no gitignored local
config. Ordinary merge ancestry remains accepted. A squash/rebase merge requires
GitHub `MERGED`, the immutable PR `headRefOid` equal to exact task HEAD,
`baseRefName` equal to configured `dev`, non-empty merge time and commit, and
that merge commit be an ancestor of fetched `origin/dev`. Open PRs, wrong heads,
wrong bases, and forged/off-target merge commits remain NO-GO.

### R7: test-root isolation

Supervisor and `ai_status` test modules set a temporary
`PANTHEON_STATUS_ROOT` before importing `ai_status`, then restore the caller
environment. This exposed and removed hidden reliance on live config and live
PID scans. The focused run emitted no new `FREEZE-TEST-*` events to the live
activity log; the last pre-fix fixture event remains at
`2026-07-31T08:12:37Z`. Concurrent legitimate watchdog and task-handoff events
can change the whole-file hash while live Supervisor runs, so absence of fixture
events plus isolated paths is the authoritative assertion.

The pre-existing polluted audit log is retained and is not rewritten or deleted.
Generated task-worktree dashboard files are excluded from the task commit.

### R8: stable eligible wake keys

Pure `last_update` changes no longer alter dispatch authority keys. Notes,
status-check retries, and generated-view synchronization cannot make an otherwise
eligible queued wake reject itself. Status, owner, reviewer, dependencies,
dependency states, task id, and dispatch reason remain bound into the key, so
real authority or eligibility changes still invalidate an event.

### Focused verification

```text
pytest -q .orchestrator/test_supervisor.py::SupervisorFailureLoopCoverageTests \
  scripts/test_ai_status.py::DeliveryMetadataValidationTests
................... [100%]

ruff check .orchestrator/runtime_state.py .orchestrator/supervisor.py \
  .orchestrator/test_supervisor.py scripts/ai_status.py scripts/test_ai_status.py
All checks passed!

git diff --check
clean
```

No live Supervisor restart or deployment was performed.

---

## 6. Coordinator completion addendum — R9–R13

Round-2 independent review at owner anchor `ab842fb590f1a981b1cc3d020117e3676fb92c8c`
found five additional control gaps. The findings are preserved in
`ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001-review-codex6-round2.md`; the task was
formally reopened before this batch remediation.

### R9: monotonic terminal worker and queue state

Runtime-state reconciliation now treats terminal worker states as monotonic.
A stale in-memory `running` record cannot resurrect a worker already persisted
as `completed` or `failed`, and a terminal in-memory transition wins over an
older active disk record. Queue records use the same terminal-preserving rule
and retain the higher-ranked durable transition.

The regression tests cross the real persistence boundary for
`running -> completed` and `running -> failed`, then prove a stale writer cannot
move either worker or queue event back into an active state.

### R10: locked read-merge-write transactions

`save_runtime_state` now acquires an advisory lock on the state-specific
`.lock` file around the complete read, merge, and atomic-write transaction.
It preserves worker and event records that exist only on disk, including
terminal records, rather than preserving only records that still look active.

The concurrency regression uses two forked child processes that load the same
snapshot, synchronize at a barrier, and then save distinct worker records.
Both records must remain in the final persisted state; this verifies the actual
cross-process transaction boundary rather than a sequential mock.

### R11: hermetic concurrent-claim test boundary

The concurrent-claim regression redirects status, runtime, worker, watchdog,
workspace, and activity-log paths into a temporary root and patches the process
launch boundary. It exercises the real locked save path and proves no duplicate
worker is started, while leaving the live Supervisor activity log and worker
registry untouched by test fixtures.

During the full verification run the live activity log received one legitimate
`watchdog_probe` from live Supervisor PID `3100560`; no `ODP-CONC-*` worker,
event, or process was created by the test.

### R12: authority-only dispatch eligibility keys

Eligible wake keys exclude observational timestamps such as `last_update` but
remain bound to dispatch authority and eligibility: task id, status, owner,
reviewer, dependency list, dependency states, and dispatch reason.

The negative matrix independently mutates owner, reviewer, status,
dependencies, and dependency state and proves each mutation invalidates a
persisted wake. A pure `last_update` change remains the sole positive case.

### R13: reconciled transactional status-check outbox

GitHub status delivery now uses one exact payload for the immediate attempt and
durable outbox record. Any failed post is deduplicated into
`status_check_outbox` with repository, SHA, context, state, description,
attempt count, and last error. Each authoritative CLI run reconciles pending
records: successful delivery removes the item and appends a delivery-history
receipt; repeated failure remains durable for a later retry.

The isolated end-to-end CLI regression injects HTTP 422 on formal reopen,
proves the task remains `in_progress` and the exact payload is durable, then
runs a second authoritative sync that succeeds and clears the outbox. Runtime
state, backup patches, and the live activity log are not mutated.

### Round-2 verification receipts

```text
PYTHONPATH=.orchestrator python3 -m unittest test_supervisor test_model_rotation
Ran 267 tests in 1.565s
OK

python3 -m unittest scripts.test_ai_status
Ran 101 tests in 0.632s
OK

python3 .orchestrator/doctor.py
exit 0

python3 -m ruff check .orchestrator/common.py .orchestrator/runtime_state.py \
  .orchestrator/supervisor.py .orchestrator/test_supervisor.py \
  scripts/ai_status.py scripts/test_ai_status.py
All checks passed

git diff --check "$(git merge-base origin/dev HEAD)"..HEAD
clean
```

This closes the owner-side R9–R13 remediation only. Exact-head independent
review, PR/CI, merge, worker drain, and safe live Supervisor rollout remain
separate required gates. No live Supervisor restart or deployment was
performed.

---

## 7. Coordinator completion addendum — R14–R15

Round-3 independent review at owner head
`3d290fd430124c46e0e39da61dc1dc7d30a7a92e` found two remaining test-boundary
gaps. The findings are preserved in
`ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001-review-codex6-round3.md`; the task was
formally reopened before this batch remediation.

### R14: complete test-config path isolation

`load_test_config()` no longer returns repository-relative coordination paths.
Every entry in the test config `paths` table is rewritten to an absolute path
below the module-scoped temporary status root before any Supervisor helper can
persist. Watchdog state/metrics, worker-worktree root, and permission-broker
workspace roots are isolated there as well.

The pending-CI freeze regression now snapshots the task worktree's
`ai-status.json` bytes around the real `dispatch_ready_tasks()` persistence
path and requires exact equality. A separate config regression proves every
coordination path is absolute and inside the temporary root.

The Round-3-corrupted task-context status file was deliberately not restored by
hand. The full verification used its existing digest as a sentinel and proved
that neither full suite changed it or the task-context activity log:

```text
status before/after:
a8390bd74bbebab824a10c819eaa3dc28872e52cc0e36e7d50f9f388d3f16ba1

activity before/after:
bf7052d41dfe6207b8f7c834e6bbb9a1b0cbb48ca2f37a8c415fc0e023d06382
```

The pre-existing `ODP-CONC-*` path-set digest was also unchanged, and no
`pantheon-supervisor-tests-*` temporary root remained after either suite.

### R15: worker-identity-independent ai-status tests

`ArchiveWorkflowTests.test_archive_migrate_moves_terminal_tasks_out_of_active_state`
now supplies the registered fixture actor `Codex` explicitly. It no longer
inherits the dispatched reviewer's `AI_NAME`.

Both complete suites were rerun without clearing the real worker identity:

```text
AI_NAME=Codex6 PYTHONPATH=.orchestrator \
  python3 -m unittest test_supervisor test_model_rotation
Ran 268 tests in 0.867s
OK

AI_NAME=Codex6 python3 -m unittest scripts.test_ai_status
Ran 101 tests in 0.372s
OK

AI_NAME=Codex6 python3 .orchestrator/doctor.py
exit 0

python3 -m ruff check .orchestrator/common.py .orchestrator/runtime_state.py \
  .orchestrator/supervisor.py .orchestrator/test_supervisor.py \
  scripts/ai_status.py scripts/test_ai_status.py
All checks passed

git diff --check
clean
```

This closes the owner-side R14–R15 remediation together. Exact-head independent
re-review, PR/CI, merge, worker drain, and safe live Supervisor rollout remain
separate required gates. No live Supervisor restart or deployment was
performed.

---

## 8. Coordinator completion addendum — R16

After the R14–R15 handoff, live execution exposed a dispatch-authority drift on
`ODP-PLAN-ACCEPTANCE-REAL-EXEC-001`. The live Supervisor authorized and
dispatched the legacy owner `Codex`, and the wake prompt required
`AI_NAME=Codex`. After the task merged current `origin/dev`, however, its
tracked config plus the status-root local overlay declared only
`Codex3`–`Codex9`, `CodexCoordinator`, and non-worker actors. The exact official
handoff command therefore failed before mutation:

```text
Unknown AI_NAME: 'Codex' is not a registered agent.
registered: Codex3, Codex4, Codex5, Codex6, Codex7, Codex8, Codex9,
            CodexCoordinator, Human/Ops, Orchestrator
```

The worker recovered by using the documented `AI_STATUS_EXTRA_AGENTS=Codex`
escape hatch, but requiring a worker to infer that repair violates the
Supervisor contract: the authorized dispatch target, prompt identity, runtime
identity, and official status authority must be identical by construction.

### R16: dispatch-bound actor authority

`build_request()` now materializes `target_display_name` for both slotted and
ordinary dispatches. `delivery_runtime_env()` carries that already-authorized
display name into every adapter through the common runtime environment as both
`AI_NAME` and `AI_STATUS_EXTRA_AGENTS`. Existing explicitly supplied extra
actors are preserved and deduplicated.

This is not an ambient actor bypass. The value originates from the live
Supervisor's merged fleet config and selected dispatch event; it merely
preserves that authority across a task branch whose tracked config revision may
differ from the live checkout. All adapters already consume the common runtime
environment, so Codex, Claude, Antigravity, Gemini, Qwen, and Copilot paths share
the same invariant.

The regression covers an ordinary non-slotted alias and an isolated worker
runtime with a pre-existing extra-actor list. It proves exact `AI_NAME`,
deduplicated actor authority, logical identity, configured display name,
workspace root, and status root.

### R16 verification receipts

```text
AI_NAME=Codex6 PYTHONPATH=.orchestrator \
  python3 -m unittest test_supervisor test_model_rotation \
    test_adapter_fallback_policy
Ran 278 tests in 0.757s
OK

AI_NAME=Codex6 python3 -m unittest scripts.test_ai_status
Ran 101 tests in 0.337s
OK

python3 .orchestrator/doctor.py
exit 0

uv run ruff check .orchestrator/common.py .orchestrator/supervisor.py \
  .orchestrator/test_adapter_fallback_policy.py \
  .orchestrator/test_supervisor.py
All checks passed

git diff --check
clean
```

This closes the owner-side R16 remediation. Exact-head independent re-review,
PR/CI, merge, worker drain, and safe live Supervisor rollout remain separate
required gates. No live Supervisor restart or deployment was performed.
