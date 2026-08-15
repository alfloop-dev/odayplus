# Task Brief: ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001

## Task
- Title: Roll out reviewed worktree base-advance policy to live Supervisor
- Status: in_progress
- Owner: Antigravity3
- Reviewer: Antigravity5
- Next: Roll out reviewed worktree base-advance policy (#569, commit 475f6d5e) to live Supervisor runtime, perform systemd restart (pantheon-supervisor.service), verify process PID update, advancing heartbeat, zero loop error, create canonical task brief, record real clean-diverged worktree reproduction and fail-closed edge cases, update PR #575 exact HEAD, and re-review to Antigravity5.

## Summary
- Roll out reviewed worktree base-advance policy (PR #569, commit `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2`) to live Supervisor runtime across target roots `/home/lupin/oday-plus`, `/home/lupin/oday-plus-supervisor-live`, and `/home/lupin/oday-plus-supervisor-runtime-945a8366`.
- Perform systemd service restart (`systemctl --user restart pantheon-supervisor.service`) to ensure in-memory supervisor process executes the reviewed source code.
- Verify new process PID and start timestamp, fresh post-restart heartbeat, advancing loops, 0 loop errors, and module checksum/import.
- Reproduce clean-diverged task worktree refresh scenario returning `base_advance_rebase_required` owner prompt and verify dirty worktree, ref mismatch, and fetch failure fail-closed behavior.

## Source Documents
- PR #569 (commit `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2`)
- PR #575 (rollout PR for `task/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001`)

## Acceptance
- All target roots published with reviewed PR #569 `.orchestrator/supervisor.py` code (sha256 `35b932b6b29dd1ca2e2c228065abd4df5160f177eee17a2bb01ac5d167828a6f`).
- `pantheon-supervisor.service` restarted via systemd with verified new PID and fresh ExecMainStartTimestamp.
- Post-restart heartbeat fresh, lifecycle `running`, and `last_loop_error` is null.
- Real clean-diverged task worktree reproduction recorded showing `base_advance_rebase_required` prompt generation.
- Dirty worktree, ref mismatch, and fetch failure edge cases fail closed.
- Canonical task brief tracked in git repository.
- PR #575 updated with exact HEAD, green CI, and re-reviewed to Antigravity5.

## Verification
- `systemctl --user show pantheon-supervisor.service` (PID & start timestamp verification)
- `python3 scripts/supervisor_runtime_health.py` (healthy=true, last_loop_error=null)
- `/home/lupin/oday-plus/.venv/bin/pytest .orchestrator/test_*.py`
- `ruff check .orchestrator`
- `git diff --check`

