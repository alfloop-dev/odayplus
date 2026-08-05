# Review Packet: ODP-ORCH-REBASE-HEAD-LIVENESS-001

- Sidecar task: `ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-REBASE-HEAD-LIVENESS-001`
- Sidecar owner: `Antigravity`
- Assigned sidecar reviewer / parent owner: `Antigravity6`
- Parent reviewer: `Antigravity5`
- Evidence captured: `2026-08-05` UTC
- Parent branch: `origin/task/ODP-ORCH-REBASE-HEAD-LIVENESS-001`
- Exact reviewed parent HEAD: `d518d04c441a0790fb31aeaf2cb6a1e218f6d331` (commit `0b3e9d9d32e2c8dfceb72b5696dedaefb909e243`, merged via PR `#577`)
- Parent PR: `#577` (`dev` <- `task/ODP-ORCH-REBASE-HEAD-LIVENESS-001`)
- Scope: Review packet and evidence summary only; no canonical truth or runtime core modified

## Executive disposition

The parent task `ODP-ORCH-REBASE-HEAD-LIVENESS-001` fixes a critical worktree liveness issue where reusable worker worktrees were permanently blocked due to stale `REBASE_HEAD` ref files left behind by Git after completed rebase operations.

By removing `REBASE_HEAD` from the ref marker verification list in `_git_operation_in_progress()`, completed rebases no longer falsely report `unresolved_git_operation`. At the same time, active rebase operations remain strictly fail-closed via the `rebase-merge` and `rebase-apply` state directory checks.

Parent PR `#577` was reviewed, approved, and merged into `dev` at commit `71e2ce23`. All supervisor liveness test suites pass cleanly with zero lint or git diff issues.

## Reviewed change surface

Compared with `origin/dev` base prior to PR `#577`, the parent task touched two supervisor files:

| File | Contract role | Review observation |
| --- | --- | --- |
| `.orchestrator/supervisor.py` | Worktree liveness checker | Removed `REBASE_HEAD` from ref marker loop in `_git_operation_in_progress()`. Active rebase liveness checks defer to `rebase-merge` / `rebase-apply` directory existence. |
| `.orchestrator/test_supervisor.py` | Supervisor unit test suite | Added `test_stale_rebase_head_after_completed_rebase_does_not_block()` and expanded `test_unresolved_rebase_blocks()` to cover both `rebase-merge` and `rebase-apply` state directories. |

No L1 canonical document, contract specification, governance policy, or product runtime logic was modified by this task.

## Behavior evidence matrix

| Scenario | Prior behavior | Delivered behavior | Evidence / Test case |
| --- | --- | --- | --- |
| Stale `REBASE_HEAD` exists after completed rebase | `_git_operation_in_progress()` returned `True` -> `unresolved_git_operation` (worktree jammed) | `_git_operation_in_progress()` returns `False` -> worktree refresh proceeds successfully | `test_stale_rebase_head_after_completed_rebase_does_not_block` |
| Active rebase in progress (`rebase-merge` dir present) | `_git_operation_in_progress()` returned `True` -> `unresolved_git_operation` | `_git_operation_in_progress()` returns `True` -> `unresolved_git_operation` (fails closed) | `test_unresolved_rebase_blocks` (`rebase-merge` subtest) |
| Active rebase in progress (`rebase-apply` dir present) | `_git_operation_in_progress()` returned `True` -> `unresolved_git_operation` | `_git_operation_in_progress()` returns `True` -> `unresolved_git_operation` (fails closed) | `test_unresolved_rebase_blocks` (`rebase-apply` subtest) |
| Active merge / cherry-pick / revert in progress | Checked via `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD` -> returns `True` | Remains checked via `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD` -> returns `True` (fails closed) | `ReusedWorkerWorktreeBaseAdvanceTests` suite |

## Independent verification at exact parent HEAD

Verification executed in local workspace environment:

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  .orchestrator/test_supervisor.py \
  -k ReusedWorkerWorktreeBaseAdvanceTests
# 14 passed

/home/lupin/oday-plus/.venv/bin/ruff check \
  .orchestrator/supervisor.py \
  .orchestrator/test_supervisor.py
# All checks passed!

git diff --check
# clean
```

## Reviewer & supervisor summary

1. **Root cause addressed accurately**: Git `REBASE_HEAD` ref files are transient markers during interactive/automated rebase steps that Git does not always prune upon clean completion. Relying on `REBASE_HEAD` alone caused false-positive worktree lockouts.
2. **Fail-closed posture preserved**: Active rebases create `rebase-merge` or `rebase-apply` control directories inside the worktree gitdir. `_git_operation_in_progress()` checks both directories immediately following ref checks, ensuring incomplete rebases remain safely blocked.
3. **Merging and provenance**: Parent PR `#577` was merged into `dev` at commit `71e2ce23`. Sibling sidecar acceptance task `ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-ACCEPTANCE` is also `review_approved`.

## Sidecar boundary and handoff

This review packet is a support artifact for `ODP-ORCH-REBASE-HEAD-LIVENESS-001`. It does not alter canonical architecture, governance rules, or supervisor core logic.

Handoff target: `Antigravity6` (parent task owner and assigned sidecar reviewer).
