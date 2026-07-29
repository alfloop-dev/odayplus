# ODP-ORCH-REVIEW-HEAD-FREEZE-001: Freeze exact review head and stop finalize dispatch churn

Owner: Antigravity4 · Reviewer: Claude · Phase: Orchestrator Control Plane

Depends on ODP-ORCH-APPROVAL-RESUME-ROOT-001 (done).

This task changes the control plane review and finalize dispatch behavior. It touches no Package 10 UI, no design API, no worker business logic, and no cloud resources.

## 1. Summary of Control Plane Defects Addressed
1. **Post-Review Mutation Bypassing Reviewer**: When a task was in `review_approved`, owner could push new commits or merge `dev` without re-review, and `ai-status.sh done` would accept the unreviewed HEAD.
2. **Supervisor Dispatch Loop During CI**: When required CI status checks were pending on a task branch/PR, supervisor repeatedly issued `owned_finalize_dispatch` every tick, causing dispatch churn.
3. **Identity Separation**: Ensured owner and reviewer identities cannot be the same agent, enforcing strict dual-identity governance.
4. **Task Review Gate Emission**: `task-review-gate` now verifies the exact current commit against the reviewer-approved HEAD commit, emitting `pending` (re-review required) if branch HEAD has moved.

## 2. Implementation Changes

### `.orchestrator/supervisor.py`
- In `dispatch_ready_tasks` and `dispatch_priority_for_task`:
  - Detects if `task["approved_head"]` differs from the current branch HEAD. If mutated, automatically transitions task status back to `review` with a log note so the reviewer is re-dispatched (`review_ready_dispatch`).
  - Queries `task_pr_ci_status(task_id)`. If required CI is `pending`, suppresses `owned_finalize_dispatch` to eliminate dispatch loops until CI reaches terminal state (`success`).

### `scripts/ai_status.py`
- `command_approve`:
  - Enforces `reviewer != owner`.
  - Captures and records `task["approved_head"]` using `resolve_task_sha(task_id)`.
- `command_done`:
  - Verifies current branch HEAD matches `task["approved_head"]`. Rejects finalize attempt if HEAD differs.
- `emit_task_review_status_check`:
  - Emits `task-review-gate` as `success` ONLY when current SHA matches `approved_head`. Emits `pending` if HEAD has moved.
- `task_pr_ci_status`:
  - Helper to query `gh pr view` rollup status checks and classify CI status (`pending`, `success`, `failure`, `none`).

## 3. Verification

```bash
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest .orchestrator -q -m "not requires_live_env"
# 438 passed
/home/lupin/oday-plus-supervisor-live/.venv/bin/ruff check .orchestrator/supervisor.py .orchestrator/test_supervisor.py scripts/ai_status.py
# All checks passed!
```

### Deterministic Unit & Regression Tests (`ReviewHeadFreezeTests`)
- `test_approve_saves_approved_head_and_rejects_same_owner_reviewer`
- `test_command_done_rejects_mutated_head`
- `test_supervisor_reverts_mutated_approved_head_to_review`
- `test_supervisor_suppresses_finalize_dispatch_on_pending_ci`
- `test_task_review_gate_status_check_pending_on_head_mismatch`
