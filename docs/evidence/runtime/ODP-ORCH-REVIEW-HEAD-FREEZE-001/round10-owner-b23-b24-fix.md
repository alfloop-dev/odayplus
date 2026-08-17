# ODP-ORCH-REVIEW-HEAD-FREEZE-001 — Round 10 owner rework (B23, B24, N3)

- Owner: Antigravity4
- Reviewer: Antigravity3
- Date: 2026-07-30
- Responds to: Round 9 rejection (`review-round9-claude.md`)

## What changed

### 1. B23 — Reviewer rejection is durable and cannot be undone by owner `restore_approved`

- `command_reopen` (`scripts/ai_status.py`):
  - Preserves `last_approved_head` (which B21 established as the durable record of reviewer approval) so that valid supervisor demotions can still be restored.
  - Sets `task["last_reopened_by"] = actor` when a task is reopened.
- `command_restore_approved` (`scripts/ai_status.py`):
  - Checks if `task["last_reopened_by"] == reviewer` or if a pending handoff from `reviewer` exists for the task.
  - If a reviewer rejection is detected, `restore_approved` fails closed with:
    `"Cannot restore <task>: the task was reopened by the reviewer (<reviewer>). Reviewer rejections cannot be restored by the owner; run re_review so the reviewer can re-examine the work."`
  - Clears `last_reopened_by` when task is approved or restored.

### 2. B24 — Undispatchable `review_approved` tasks do not terminate running workers

- `higher_priority_ready_task_exists` (`.orchestrator/supervisor.py`):
  - Delegates candidate task priority evaluation to `dispatch_priority_for_task(config, task, agent_name, task_map, dependencies_done_statuses)`.
  - When a `review_approved` task is missing `approved_head`, has a head mismatch, or has pending/failing CI, `dispatch_priority_for_task` returns `None`.
  - `candidate_priority` is `None`, so undispatchable `review_approved` tasks cannot trigger preemption or terminate running workers in `higher_priority_ready_task_exists`.

### 3. N3 — `restore_approved` and `restore_approved_head` re-emit status checks

- `emit_status_checks_for_changed_tasks` (`scripts/ai_status.py`):
  - Added `"restore_approved"` and `"restore_approved_head"` to the command list that targets task ID for `emit_task_review_status_check`.

## Verification

```bash
cp .orchestrator/config.example.json .orchestrator/config.json
env -u PANTHEON_STATUS_ROOT -u AI_NAME pytest -m "not requires_live_env" .orchestrator scripts -q
# 564 passed

python3 -m ruff check .orchestrator/supervisor.py .orchestrator/test_supervisor.py scripts/ai_status.py scripts/test_ai_status.py
# All checks passed!
```

- Added unit tests:
  - `test_restore_approved_refuses_when_reviewer_reopened` (`scripts/test_ai_status.py`)
  - `test_restore_approved_head_emits_status_check` (`scripts/test_ai_status.py`)
  - `test_higher_priority_ready_task_exists_refuses_undispatchable_finalize_task` (`.orchestrator/test_supervisor.py`)
