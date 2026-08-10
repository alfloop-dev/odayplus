# ODP-ORCH-WORKTREE-LEASE-RECOVERY-001 Evidence

Owner: Codex

Reviewer: Antigravity4

Affected task: `ODP-PLAN-OBSERVABILITY-LIVE-001-SIDECAR-ACCEPTANCE`

## Incident baseline

The live supervisor had recorded 297 consecutive lease blocks between
`2026-08-08T16:06:16Z` and `2026-08-09T06:57:11Z`:

```text
task_head_mismatch:
local=29cea9afa6b1a8cb43a5f368a555cf1c2673bd5e
remote=666088fa0d70ee223e9d8dbe1b34a7e7ca269acc
```

Before recovery, the affected worktree was clean, had no active worker, and
contained 3,299 files (123 MiB). Its path-normalized file inventory digest was
`sha256:b15d5e2c58f5ce8a41af63b7fe26c3cd31a04b750da38a02a3ee95c64aaad640`.
The remote task branch and the task's `review_gate_sha` / `last_approved_head`
all resolved to `666088fa0d70ee223e9d8dbe1b34a7e7ca269acc`.

## Recovery

No worktree or commit was deleted.

- The stale worktree was moved intact to
  `/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-plan-observability-live-001-sidecar-acceptance.forensic_20260809T235828Z_29cea9af`
  and detached at `29cea9afa6b1a8cb43a5f368a555cf1c2673bd5e`.
- Its file count, size, and path-normalized inventory digest after the move
  remained 3,299 files, 123 MiB, and
  `sha256:b15d5e2c58f5ce8a41af63b7fe26c3cd31a04b750da38a02a3ee95c64aaad640`.
- The same local tip is independently retained as
  `refs/heads/supervisor-preserved/odp-plan-observability-live-001-sidecar-acceptance-20260809T235828Z-29cea9af`.
- The reusable task branch was pointed to the authoritative remote task head,
  then a clean linked worktree was recreated at the original lease path.

## Verification

The runtime supervisor code at `9c95ecc3` successfully ran
`prepare_worker_workspace` against the rebuilt lease. It recorded:

```text
workspace_path=/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-plan-observability-live-001-sidecar-acceptance
workspace_branch=task/ODP-PLAN-OBSERVABILITY-LIVE-001-SIDECAR-ACCEPTANCE
workspace_head=666088fa0d70ee223e9d8dbe1b34a7e7ca269acc
remote_head=666088fa0d70ee223e9d8dbe1b34a7e7ca269acc
refresh_status=base_advance_rebase_required:local=666088fa0d70ee223e9d8dbe1b34a7e7ca269acc,base=93190a2e6a61b7f13ac0762873d131f425256f21
last_used_at=2026-08-10T00:04:38Z
```

`base_advance_rebase_required` is a successful, dispatchable refresh result;
it only tells the affected task owner that a later base compose is required.
The rebuilt worktree is clean and its local HEAD equals the freshly queried
remote task HEAD.

After that successful preflight, the existing runtime
`_clear_worktree_lease_block` API removed the obsolete streak under the
runtime-state transaction lock. The persisted
`worker_worktree_lease_blocks` mapping no longer contains
`odp_plan_observability_live_001_sidecar_acceptance`, and the lease record now
points to the rebuilt exact-head worktree above.

The first manual clear overlapped a supervisor loop that had loaded its state
before the recovery. That loop completed at `2026-08-10T00:04:34Z` and wrote
its older 297-count snapshot once more. The recovery waited for that loop to
finish, repeated the exact-head preflight, and cleared the streak during the
following 15-second poll interval. This avoided restarting the supervisor or
interrupting its active workers. The persisted post-loop state at
`2026-08-10T00:04:38Z` again had no affected lease-block key.

The next full supervisor loop started at `2026-08-10T00:04:50Z` from that
clean state and completed at `2026-08-10T00:07:20Z`. The block key remained
absent after its save. During this loop, the normal dispatch path reused the
rebuilt worktree for reviewer Codex2 (`evt-20260810T000511Z-d491af5a`,
`last_used_at=2026-08-10T00:06:45Z`). This is the end-to-end lease proof: the
supervisor did not merely lose the old alarm record; it successfully leased
the exact-remote-head worktree on a real queued review dispatch.

## Scope boundary

This operation did not change the affected task's content or human gates. It
remains `review`, owned by Antigravity2 and reviewed by Codex2, with
`review_gate_sha` and `last_approved_head` both pinned to `666088fa...`.
No support packet, canonical truth, reviewer decision, approval note, remote
branch, or human gate was rewritten.

One adjacent runtime behavior was observed but intentionally not changed here:
a pre-existing block is automatically cleared when a single preflight first
fails and then recovers, but not when an operator repairs the worktree before
the next preflight. This task used the existing clear API after exact-head
verification; changing supervisor policy belongs in a separate task.
