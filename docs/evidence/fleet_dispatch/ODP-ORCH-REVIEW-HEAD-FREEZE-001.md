# ODP-ORCH-REVIEW-HEAD-FREEZE-001: Freeze exact review head and stop finalize dispatch churn

Owner: Antigravity4 · Reviewer: Claude · Phase: Orchestrator Control Plane

Depends on ODP-ORCH-APPROVAL-RESUME-ROOT-001 (done).

This task changes the control plane review and finalize dispatch behavior. It touches no Package 10 UI, no design API, no worker business logic, and no cloud resources.

## 1. Summary of Control Plane Defects Addressed & Rework Fixes

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

6. **CLI Command Registration & Re-Review Flow (B6 Fix & AC2)**:
   - Registered `re_review` and `re-review` in `MUTATING_COMMANDS` and `target_task_id` in `emit_status_checks_for_changed_tasks`.
   - Updated unit tests in `test_supervisor.py` to drive execution via `ai_status.main(["ai_status.py", "re_review", ...])` and `re-review`. Added `re_review`/`re-review` to `AI_NAME_CASES` in `test_ai_status.py`.

7. **Review Gate Status Check Alignment (B7 Fix & AC4)**:
   - Corrected fallback state mapping in `emit_task_review_status_check`. Non-terminal/non-approved states (`in_progress`, `blocked`) emit `state = "failure"`, preventing unapproved or rejected tasks from passing `task-review-gate`. Only `review_approved` (with matching head) and `done` emit `success`.

8. **SHA Resolution Caching & Test Suite Green (B8 & B9 Fix)**:
   - Removed duplicate `_TASK_SHA_CACHE` declaration in `scripts/ai_status.py` and added `setUp` cache clearing to `StatusCheckEmissionTests` in `scripts/test_ai_status.py`.
   - Restored `resolve_task_sha` priority order (trying `gh pr view` for remote PR head SHA first). Both `.orchestrator` (441 tests) and `scripts/test_ai_status.py` (98 tests) suites pass cleanly.

9. **Handoff Log Message Integrity (B10 Fix)**:
   - Restored target agent in `command_handoff` log message (`f"Handoff to {to_agent}: {message}"`).

10. **Git Diff Check & Whitespace Formatting (B11 Fix)**:
    - Cleaned up EOF blank lines and stray blank line runs across `.orchestrator/test_supervisor.py` and evidence docs. `git diff --check 1e256103` is 100% clean.

11. **Unpushed Branch & Control-Plane Emission Resiliency (B12 Fix)**:
    - Updated `emit_task_review_status_check` error handling to gracefully handle HTTP 422 ("No commit found for SHA"), unauthenticated, and network errors by warning and returning cleanly without raising `RuntimeError`, ensuring local/control-plane state mutations never roll back on status emission failure.

## 2. Verification

```bash
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest .orchestrator -q -m "not requires_live_env"
# 441 passed
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest scripts/test_ai_status.py -q -p no:randomly
# 98 passed
python3 -m py_compile .orchestrator/supervisor.py .orchestrator/test_supervisor.py scripts/ai_status.py
# Clean!
git diff --check 1e256103 HEAD
# Clean!
```

### Deterministic Unit & On-Disk Regression Tests (`ReviewHeadFreezeTests`)
- `test_approve_saves_approved_head_and_rejects_same_owner_reviewer`
- `test_command_done_rejects_mutated_head`
- `test_supervisor_reverts_mutated_approved_head_to_review_on_disk` (asserts against real on-disk status file)
- `test_task_pr_ci_status_handles_checkrun_completed_failure` (verifies B4 CheckRun parsing)
- `test_dispatch_priority_for_task_and_agent_primary_work` (verifies B3 runtime integration)
- `test_explicit_re_review_command` (verifies AC2 CLI transition via main)
- `test_supervisor_suppresses_finalize_dispatch_on_pending_ci` (verifies AC3 / B5)
- `test_task_review_gate_status_check_pending_on_head_mismatch` (verifies AC4)

## 3. Composed Head Merge & Re-Review Verification

```bash
# Merged current origin/dev (ad4a066e) into task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
git merge origin/dev -m "ODP-ORCH-REVIEW-HEAD-FREEZE-001: merge origin/dev"

# Combined CI suite verification:
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest -m "not requires_live_env" .orchestrator scripts
# 545 passed, 10 deselected in 12.37s

/home/lupin/oday-plus-supervisor-live/.venv/bin/ruff check .orchestrator scripts
# All checks passed!
```

