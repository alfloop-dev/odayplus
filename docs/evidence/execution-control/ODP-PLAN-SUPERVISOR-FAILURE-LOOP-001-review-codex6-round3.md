# Independent Review Round 3 — ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001

- Reviewer: `Codex6`
- Reviewed owner head: `3d290fd430124c46e0e39da61dc1dc7d30a7a92e`
- Review time: `2026-07-31T09:32:23Z`
- Disposition: **CHANGES_REQUESTED**
- Runtime rollout: **not performed**

The reviewed head exactly matched
`origin/task/ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001` before verification. The
no-restart and no-deployment boundary remains in force.

## Passing checks

The full Supervisor/model-rotation suite itself reported success:

```text
PYTHONPATH=.orchestrator python3 -m unittest test_supervisor test_model_rotation
Ran 267 tests in 0.880s
OK
```

The live activity-log digest did not change, and no new `ODP-CONC-*` worktree
appeared. With the inherited worker identity removed from the unit-test
environment, `scripts.test_ai_status` also reports 101 passing tests; doctor,
Ruff, and the task-branch diff check pass.

Those green test exit codes are not sufficient because the full Supervisor
suite corrupts the coordination status file while it runs.

## Blocking findings

### R14 — P0: the full suite overwrites the task worktree's real `ai-status.json`

The task worktree began this review with the supervisor-seeded coordination
state: `ai-status.json` had 3,917 lines and contained the active
`ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001` review record.

Running the declared full suite replaced that file at
`2026-07-31T09:27:27.992993393Z` with a 322-byte, 13-line fixture containing
only:

```json
{
  "tasks": [
    {
      "id": "FREEZE-TEST-005",
      "owner": "Antigravity4",
      "reviewer": "Claude",
      "status": "review_approved",
      "approved_head": "1111111122222222333333334444444455555555"
    }
  ]
}
```

The resulting file digest is
`a8390bd74bbebab824a10c819eaa3dc28872e52cc0e36e7d50f9f388d3f16ba1`.
This is exact, deterministic evidence of a test write, not a concurrent live
Supervisor update.

The writer is
`ReviewHeadFreezeTests.test_supervisor_suppresses_finalize_dispatch_on_pending_ci`.
`_build_freeze_test_config()` loads `config.example.json`, whose
`paths.status_file` is the relative value `ai-status.json`.
`common.config_path()` resolves relative paths against the checked-out source
`ROOT`, not the temporary `PANTHEON_STATUS_ROOT`. When the pending-CI branch in
`dispatch_ready_tasks()` persists `ci_pending_since_ts`, it therefore writes
the fixture directly over this task worktree's coordination state.

This disproves the R7/R11 evidence claim that the complete test root is
isolated and directly violates the acceptance requirement to preserve task
state and the activity audit. A passing process exit code currently masks
destructive state loss.

Fix the freeze-test config boundary (or patch the production write boundary)
so every status, activity, runtime, queue, workspace, and process path used by
the full suite is temporary. Add a regression that snapshots the real
task-worktree `ai-status.json` bytes and proves the full freeze dispatch path
cannot alter them.

### R15 — P1: the claimed 101-test receipt is identity-environment dependent

This worker is required to run with `AI_NAME=Codex6`. Under that inherited
environment, the exact claimed command fails:

```text
python3 -m unittest scripts.test_ai_status

ERROR:
test_archive_migrate_moves_terminal_tasks_out_of_active_state

SystemExit: Unknown AI_NAME: 'Codex6' is not a registered agent.
Ran 101 tests
FAILED (errors=1)
```

`ArchiveWorkflowTests.test_archive_migrate_moves_terminal_tasks_out_of_active_state`
does not isolate `AI_NAME`, so it calls `current_actor_validated()` with the
reviewer's real identity. The suite passes only after clearing `AI_NAME`.
Make this test hermetic by supplying an explicitly registered fixture actor,
then rerun the exact declared command in the dispatched worker environment.

## Required next handoff

Return one new pushed head that closes R14 and R15 together. Run the full
Supervisor/model-rotation and ai-status suites while hashing the task
worktree's seeded `ai-status.json` and `ai-activity-log.jsonl` before and after.
The hashes must remain unchanged, and no fixture process, worktree, queue, or
runtime file may escape the temporary test root.

Do not restore the corrupted task-worktree status file by hand. Use the
orchestrator's supported task-context/status seeding path. Do not restart or
deploy the live Supervisor.
