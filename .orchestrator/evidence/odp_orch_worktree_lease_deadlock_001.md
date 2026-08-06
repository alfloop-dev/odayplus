# ODP-ORCH-WORKTREE-LEASE-DEADLOCK-001 Evidence

Owner: Claude · Reviewer: Antigravity4 · Approved head: `59fd888d` ·
Merged to `dev` via PR #660 (`7dbe45e9`).

## Incident

On 2026-08-05 the fleet ran no work for roughly eight hours. `active_workers`
sat at 0 against a non-empty queue while ten tasks were refused a worker
worktree lease 1713 times. Every refusal appended one activity record and
returned, so nothing ever escalated: `active_workers=0` with a non-empty queue
is not itself an alarm condition, and the fleet read as healthy throughout.

Eight of the ten shared one shape. A worker had committed an anchor to its
`task/<id>` branch and exited before pushing. The fail-closed refresh policy
calls a branch dispatchable only when its local HEAD exactly matches the remote
task HEAD, so it correctly refused to reuse that worktree — but the state could
never resolve on its own, because leasing is what runs the worker that would
push, and leasing is what was being refused. Each task re-reported ~300 times.

## Scope

- Owned: worker worktree **leasing** — the unpublished-commit deadlock and the
  visibility of repeated lease blocks.
- Not changed: the fail-closed verification policy itself
  (`_refresh_reused_worker_worktree` is untouched), dispatch scheduling, and
  the review/PR flow.
- Composed onto `origin/dev` before final verification (`564e3688`,
  `59fd888d`).

## Delivered behaviour

`_publish_unpublished_task_branch(worktree_path, expected_branch)` —
fast-forward-publishes a clean task branch whose commits were never pushed.
This does not relax the policy, it *satisfies* it: publishing converts an
unverifiable local-only state into the exact `local == remote` state the policy
already accepts.

| Worktree / branch state | Result |
| --- | --- |
| Clean worktree, remote task branch missing | Push creates it; lease re-verifies and proceeds. |
| Clean worktree, remote HEAD is an ancestor of local HEAD | Fast-forward push; lease re-verifies and proceeds. |
| Clean worktree, remote HEAD already equals local HEAD | No push (`already published`); falls through unchanged. |
| Remote holds commits the local branch does not (genuine divergence) | Never published; falls through to escalation for an owner rebase decision. |
| Dirty tracked/staged worktree | Never published; the pre-existing quarantine/recovery path owns `skipped_dirty_worktree`. |
| `git status` unreadable, missing local HEAD, or push failure | Not published; reason recorded in `publish_detail`. |

On a successful publish the caller re-runs `_refresh_reused_worker_worktree`
and emits `worker_worktree_branch_published` with `publish_detail`,
`refresh_ok`, and `refresh_status`.

`_record_worktree_lease_block(...)` — counts consecutive *identical* blocks per
task and escalates once past `worker_runtime.lease_block_escalate_after`
(default 5, floor 2) with a console line and a
`dispatch_blocked_worktree_lease_escalated` activity record. It escalates
exactly once per streak, restarts the count when the `refresh_status` reason
changes, and `_clear_worktree_lease_block` clears the streak on a successful
lease. This is the part that generalises: the two genuinely diverged tasks
still cannot be auto-resolved, but they are now visible instead of silently
retrying forever.

## Regression topology

`WorktreeLeaseBlockEscalationTests` builds real temporary Git repositories and
linked worktrees:

- `test_publishing_an_unpublished_commit_makes_the_lease_verifiable`
- `test_publishing_creates_a_task_branch_that_was_never_pushed_at_all`
- `test_dirty_worktree_is_never_published`
- `test_genuinely_diverged_branch_is_never_published`
- `test_repeated_identical_blocks_escalate_exactly_once`
- `test_blocks_below_the_threshold_stay_quiet`
- `test_a_different_block_reason_restarts_the_count`
- `test_a_successful_lease_clears_the_streak`

Because `_refresh_reused_worker_worktree` is unchanged,
`test_local_and_remote_task_head_mismatch_blocks` and the rest of the
ODP-ORCH-WORKTREE-BASE-ADVANCE-001 verification matrix keep asserting exactly
what they asserted before.

## Verification

- Implementation commit `2f2444fd`: full `.orchestrator` suite, 386 tests,
  including the 8 new tests above.
- Reviewer re-review at approved head `59fd888d`: 357 supervisor tests passing.
- Closeout re-verification in the task worktree at `7dbe45e9`
  (`origin/dev` tip, contains the approved head):
  `python3 -m pytest .orchestrator/test_supervisor.py -q` → 357 passed, exit 0.
- Second closeout base advance: `origin/dev` advanced to `0391ca8c` (PR #657,
  an unrelated sidecar acceptance packet) while PR #662 sat approved, leaving
  the PR `BEHIND`. Composed as `00d73f5a`; the merge brought in one docs file
  and touched no runtime surface. Re-verified at `00d73f5a`:
  `python3 -m pytest .orchestrator/test_supervisor.py` → 357 passed, 129
  subtests passed, exit 0.
- Third closeout base advance: `origin/dev` advanced to `42e3b207` (PR #656,
  another unrelated sidecar acceptance packet) while PR #662 sat approved with
  all five checks green, again leaving the PR `BEHIND`. Composed as `d1e9cb23`;
  the merge brought in one docs file
  (`support/sidecars/ODP-PLAN-ENGINEERING-HARDENING-001/…-SIDECAR-ACCEPTANCE.md`)
  and touched no runtime surface. Re-verified at `d1e9cb23`:
  `python3 -m pytest .orchestrator/test_supervisor.py` → 357 passed, 129
  subtests passed, exit 0.
- Fourth closeout base advance: `origin/dev` advanced to `e301e274` (PR #650,
  ODP-ORCH-PROVIDER-LANE-LIVENESS-001) while PR #662 sat approved with all five
  checks green, again leaving the PR `BEHIND`. Unlike the previous three, this
  base advance lands on the *same runtime surface* this task changed —
  `.orchestrator/supervisor.py`, `common.py`, `provider_permissions.py`, and
  `test_supervisor.py`. Composed as `3400fad5`; the merge applied cleanly with
  no conflicts and this task's own diff against `origin/dev` is still exactly
  one added file. Re-verified at `3400fad5` over both touched suites:
  `python3 -m pytest .orchestrator/test_supervisor.py
  .orchestrator/test_provider_permissions.py` → 435 passed, 145 subtests
  passed, exit 0.

No live supervisor rollout is claimed by this task; the change ships with `dev`
through the normal PR path.

## Closeout loop observed on this task's own PR

This task's own closeout has now been blocked four times by the same
mechanism, which is worth recording because it is adjacent to — but distinct
from — the deadlock the task fixed.

The delivered fix covers *worker worktree leasing*: a task branch whose commits
were never pushed. The closeout blocker here is the *review gate*: branch
protection requires the PR to be up to date with `dev`, so every unrelated
merge into `dev` puts an approved PR into `BEHIND`. Composing the new base
moves the task HEAD, which invalidates the recorded `approved_head`, which
sends the task back through `re_review` — and `dev` can advance again during
that round trip. Approval throughput on `dev` is the loop's clock, so a task
whose only remaining content is a docs file can be starved indefinitely by
merges it has nothing to do with.

The first three round trips were clean auto-merges of unrelated docs files with
a re-verified test suite. The fourth composed a real runtime change onto the
same files this task touched, still without conflict, and the re-verification
widened to cover it — so nothing here is unsound. The cost is latency and
reviewer cycles, not correctness, but the fourth round trip is the point where
the starvation stops being free: each additional lap now has a genuine chance
of landing a conflicting runtime change. A durable fix belongs to the
review-gate lane rather than this task: either re-approve at the new head
automatically when the base advance is a fast-forward compose that leaves the
task's own diff byte-identical, or let auto-merge update the branch without
resetting `approved_head`. Recorded here as a follow-up candidate; not
implemented under this task's scope.
