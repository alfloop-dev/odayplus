# ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-RACE-001

## Incident receipt

The source receipt is:

`/home/lupin/oday-plus-supervisor-live/.orchestrator/logs/20260728T164254637960Z-claude-claude2-f11d4a.log`

At 2026-07-28 16:48 UTC, Claude2 had completed its focused tests, Ruff
checks, and diff check for `ODP-DEPLOY-JOB-SECRET-BINDING-SELECTION-001`.
The final tool request was:

```text
tool_use_id: toolu_01YZMtMekPkN7JQUG6AdSo1f
tool: Bash
command: git commit -F /tmp/odp-secret-schema-msg.txt 2>&1 | tail -20
hook decision: defer
session_id: 7d919ae4-893a-400b-b13f-b45e5115184b
stop_reason: tool_deferred
terminal_reason: tool_deferred
```

The CLI emitted the deferred tool in its terminal result and exited. The
permission hook's approval was not present in the approval queue read by the
supervisor. On the next poll, the worker therefore had
`status=waiting_approval`, a dead PID, and no pending or resolved approval. The
old reconciliation path classified that state as:

```text
Approval state disappeared before the worker could resume.
```

That failure path could subsequently reset a verified task worktree even though
the worker had stopped only to request approval for its commit.

The preserved dirty-worktree patch is:

`/home/lupin/oday-plus-supervisor-live/.orchestrator/worktree-dirt-backups/odp-deploy-job-secret-binding-selection-001-claude-20260728T164254Z-7dfbcba3.patch`

It contains only the three already-verified task files from the interrupted
deployment task.

## Fix

The Claude terminal receipt is now treated as a durable correlation source
before dead-worker cleanup:

1. `update_from_log` retains the `deferred_tool_use` payload when a result has
   `stop_reason=tool_deferred`.
2. `poll_workers` correlates that receipt with the worker run before checking
   PID liveness or the approval-disappeared branch.
3. The approval queue atomically loads its latest state, searches pending and
   resolved entries by worker/tool-use ID (or tool-input signature fallback),
   and creates a pending approval only when no match exists. This closes the
   stale-snapshot window without duplicating a hook-created approval.
4. A resumable dead Claude/Claude2 process moves to `suspended_approval`; an
   explicit allow resumes its saved session.
5. A broker-denied tool is recorded and immediately denied. A missing receipt,
   missing approval, queue-write failure, or explicit denial still fails closed.

The allowed-tools list for a resumed session is also narrowed. When an approval
contains a scoped rule such as the exact
`Bash(git commit -F /tmp/odp-secret-schema-msg.txt 2>&1 | tail -20)`, the resume
command receives only that scoped rule. It no longer also receives bare `Bash`,
which would allow unrelated shell commands.

## Regression coverage

`DeferredApprovalCorrelationTests` reproduces the Claude2 receipt above and
proves:

- the receipt is persisted before a dead worker can be failed or cleaned up;
- the dead session becomes `suspended_approval`;
- an allowed exact commit resumes the same session with only the scoped Bash
  rule;
- a broker denial resolves the queue item as denied and fails the worker;
- a suspended worker with neither a durable approval nor a usable receipt still
  fails with the approval-disappeared error.

`ApprovalQueuePruneTests` additionally proves that repeated correlation reuses a
single queue entry and that correlation falls back to the sanitized tool-input
signature when a tool-use ID is unavailable.

## Verification

Executed on the task worktree after the fix:

```text
python3 -m unittest discover -s .orchestrator -p 'test_approval_queue.py'
# Ran 9 tests ... OK

python3 -m unittest discover -s .orchestrator -p 'test_supervisor.py'
# Ran 222 tests ... OK

uv run --frozen ruff check \
  .orchestrator/approval_queue.py \
  .orchestrator/supervisor.py \
  .orchestrator/test_approval_queue.py \
  .orchestrator/test_supervisor.py
# All checks passed

git diff --check
# clean
```

`ruff format --check` was diagnostic only and reports that these four legacy
files are not globally Ruff-formatted. No repository-wide formatting rewrite
was retained.

## Scope

Changed paths are limited to the supervisor, approval queue, their focused
tests, and this task evidence directory. No `apps/**`, `modules/**`, deployment
script, Package 10 design archive, or Package 10 runtime evidence path changed.

## Closeout record (2026-07-29)

Reviewer Codex4 approved exact head
`7a7f87d7978eb7a9248699d9dc421d1819dbd670` with no blocking finding after
independently rerunning the focused approval-queue and supervisor suites, Ruff,
and the diff check. PR #490 merged that head into `dev` at
2026-07-29T02:32:22Z as merge commit
`fd3d99558e4fb65f94e0f145a93e7a5e03577d75`; the approved head is verified as
an ancestor of `origin/dev`.

CI run `30416181111` passed all required jobs: `orchestrator`, `product`,
`performance-gate`, and `product-e2e-gate`. Owner closeout revalidation on the
approved tree also passed:

- `python3 -m unittest discover -s .orchestrator -p 'test_approval_queue.py'`
  (9 tests);
- `python3 -m unittest discover -s .orchestrator -p 'test_supervisor.py'`
  (222 tests);
- focused `uv run --frozen ruff check`;
- `git diff --check dev...HEAD`.

This closeout record is evidence-only. It does not change the reviewed runtime
behavior, approval policy, deployment scripts, Package 10 paths, apps, or
modules.
