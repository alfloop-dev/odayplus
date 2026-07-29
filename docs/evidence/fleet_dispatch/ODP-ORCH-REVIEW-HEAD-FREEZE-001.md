# ODP-ORCH-REVIEW-HEAD-FREEZE-001: Freeze exact review head and stop finalize dispatch churn

Owner: Antigravity4 · Reviewer: Claude · Phase: Orchestrator Control Plane

Depends on ODP-ORCH-APPROVAL-RESUME-ROOT-001 (done).

This task changes the control plane review and finalize dispatch behavior. It touches no Package 10 UI, no design API, no worker business logic, and no cloud resources.

## 1. Summary of Control Plane Defects Addressed & Rework Round 1 Fixes

1. **Post-Review Mutation Bypassing Reviewer & On-Disk Persistence (B1 Fix)**:
   - When a task in `review_approved` undergoes branch mutation (HEAD mismatch), `supervisor.py` now persists `status: "review"` directly to `ai-status.json` on disk via `write_json` and `sync_status_pipeline(config)`, popping `approved_head`.
   - Prevents in-memory-only status mutations that previously caused infinite activity logging loops and task parking.

2. **Silent Failure & Path Resolution (B2 Fix)**:
   - Added `SCRIPTS_DIR` to `sys.path` at module top in `.orchestrator/supervisor.py` so `from ai_status import resolve_task_sha, task_pr_ci_status` succeeds reliably without raising `ModuleNotFoundError` or failing open.

3. **Dead Code Cleanup & Priority Hook (B3 Fix)**:
   - Wired `dispatch_priority_for_task` into `agent_has_dispatchable_primary_work` and `dispatch_ready_tasks`, making `dispatch_priority_for_task` the single source of truth for task dispatch priority and eligibility.

4. **CI Status Check Classification (B4 Fix)**:
   - Updated `task_pr_ci_status` in `scripts/ai_status.py` to inspect check `conclusion` first before `state` or `status`. Correctly maps `CheckRun` entries with `status: COMPLETED` and `conclusion: FAILURE` to `"failure"`.

5. **CI-Pending Tracking, Timeout Escape Hatch & Resume (B5 Fix)**:
   - Supervisor tracks pending duration (`ci_pending_since_ts`). If CI is pending for > 30 minutes, writes `ci_pending_timeout` activity log entry for operator visibility.
   - Resumes owner finalize dispatch once CI reaches terminal `success`. Handles `failure` state by suppressing finalize and recording `ci_failed`.
   - Performance: Cached `task_pr_ci_status` and `resolve_task_sha` with short TTLs and prioritized fast local `git rev-parse`, eliminating ~0.8s untimed `gh` subprocess calls inside the dispatch hot loop.

6. **Explicit Re-Review Command (AC2)**:
   - Added `ai-status.sh re_review <task-id> <message>` command (registered as `re_review` and `re-review`), allowing owners to explicitly request re-review after branch updates.

7. **Identity Separation & Gate Checks (AC4)**:
   - Enforces `owner != reviewer` in `command_approve`.
   - `emit_task_review_status_check` emits `task-review-gate` as `pending` on head mismatch.

## 2. Verification

```bash
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest .orchestrator -q -m "not requires_live_env"
# 441 passed
python3 -m py_compile .orchestrator/supervisor.py .orchestrator/test_supervisor.py scripts/ai_status.py
# Clean!
```

### Deterministic Unit & On-Disk Regression Tests (`ReviewHeadFreezeTests`)
- `test_approve_saves_approved_head_and_rejects_same_owner_reviewer`
- `test_command_done_rejects_mutated_head`
- `test_supervisor_reverts_mutated_approved_head_to_review_on_disk` (asserts against real on-disk status file)
- `test_task_pr_ci_status_handles_checkrun_completed_failure` (verifies B4 CheckRun parsing)
- `test_dispatch_priority_for_task_and_agent_primary_work` (verifies B3 runtime integration)
- `test_explicit_re_review_command` (verifies AC2 CLI transition)
- `test_supervisor_suppresses_finalize_dispatch_on_pending_ci` (verifies AC3 / B5)
- `test_task_review_gate_status_check_pending_on_head_mismatch` (verifies AC4)

