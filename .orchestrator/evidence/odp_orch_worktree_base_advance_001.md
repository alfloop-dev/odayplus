# ODP-ORCH-WORKTREE-BASE-ADVANCE-001 Evidence

## Scope

- Owned: reusable worker-worktree refresh/lease policy, base-advance dispatch prompt, and regression coverage.
- Not changed: provider failure classification, product modules, deployment, model, or release gates.
- Composed onto `origin/dev` at `ddded7ed` before final verification.

## Delivered policy

| Verified worktree state | Result |
| --- | --- |
| Clean task HEAD is behind fetched base | Fast-forward to the fetched base. |
| Clean task HEAD already contains fetched base | Dispatch without changing task history. |
| Clean local task HEAD equals freshly fetched `origin/task`, and task/base diverge | Dispatch with an explicit owner-controlled rebase/compose-required prompt. |
| Dirty tracked/staged state, local/remote task mismatch, wrong branch/repository worktree, fetch failure, unresolved merge/rebase, or unverifiable refs | Block the lease without resetting, cleaning, rebasing, or discarding task state. |

Failed queue finalization no longer automatically hard-resets/cleans the worker worktree before the next lease check. The explicit recovery helper remains available but is not invoked by dispatch or queue finalization.

## Regression topology

`ReusedWorkerWorktreeBaseAdvanceTests` creates real temporary Git repositories and linked worktrees matching the PR #562 incident topology: local tracked task HEAD equals the remote task HEAD while `origin/dev` advances independently. The positive case proves dispatch preserves the diverged task HEAD and reports `base_advance_rebase_required`; negative cases cover dirty product and orchestrator scratch, task-head mismatch, wrong branch, wrong repository, fetch failure, unresolved merge, unresolved rebase, and unverifiable refs.

## Overlap audit

PR #472 (`fix/claude-session-limit-marker`, head `14389e600f96423dd30aabaec340ac09ec5ace7f`) changes provider session-limit classification around the failure classifier. This task changes worktree refresh/lease handling and queue-finalization cleanup. No overlapping hunk was found, and the PR #472 provider diff was neither merged nor rewritten.

## Verification

- `python3 -m unittest ...ReusedWorkerWorktreeBaseAdvanceTests ...ProcessQueueDispatchGuardTests ...RuntimeConfigTests`: 27 passed after composing current `origin/dev`.
- `ruff check .orchestrator/supervisor.py .orchestrator/test_supervisor.py`: passed.
- `git diff --check`: passed.
- `python3 -m unittest discover -s .orchestrator -p 'test_*.py'`: 489 tests executed; four review-head assertions and one concurrent-state assertion failed only in suite order, then all five passed when rerun directly. One provider-permission assertion failed directly and also failed unchanged on a clean detached `origin/dev` worktree, so it is recorded as pre-existing baseline debt rather than changed out of scope.

No live supervisor rollout is claimed by this task. A separate reviewed rollout is required before operators rely on the new dispatch behavior.
