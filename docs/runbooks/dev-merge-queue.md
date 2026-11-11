# Runbook: the `dev` merge queue

Use when the merge queue on `dev` needs to be enabled, tuned, inspected, or
switched off.

Owning task: ODP-ORCH-MERGE-QUEUE-ACTIVATION-001. Prerequisite workflows landed
in ODP-ORCH-MERGE-QUEUE-ENABLEMENT-001 (PR #664, `02c847dd`).

## Why the queue exists

Every task lane branches from `dev` and merges back into it. With
"require branches to be up to date" (`strict`) on, a PR that starts its
~25 minute CI run is usually behind `dev` again before the run finishes, so it
re-runs, and re-runs again. The measured cost before the queue: 157 worker
dispatches over 8.4h to close 13 tasks, most of them re-chasing `dev` rather
than doing task work.

A merge queue removes that race. Instead of each PR proving itself against a
`dev` that keeps moving, the queue builds a temporary ref from `dev` plus the
queued PRs and runs the required checks once against that ref.

## What is configured, and where

The queue is **not** expressible in classic branch protection: GraphQL's
`updateBranchProtectionRule` mutation has no `requiresMergeQueue` input and the
REST branch-protection payload has no merge queue field. It is therefore a
repository **ruleset** named `dev-merge-queue`, while the four required
contexts stay on the classic rule. Rulesets and classic protection are
additive, so both apply.

Both surfaces are declared in `.github/branch-protection/policy.json` and
applied by `delivery_toolchain/github/apply_branch_protection.py`.

| Setting | Value | Why |
|---|---|---|
| `merge_method` | `MERGE` | Matches the existing closeout flow (`gh pr merge --merge`) and `dev`'s merge-commit history. Squash would rewrite the task commit and drop the `LLM-Agent` / `Task-ID` / `Reviewer` trailers the hooks enforce. |
| `grouping_strategy` | `ALLGREEN` | Every entry's own merge commit must be green, not just the group head. At this fleet's throughput (~1.5 merges/hour) the queue is rarely deeper than two, so the extra CI is cheap and a bad entry is ejected without bisecting the group. |
| `check_response_timeout_minutes` | `60` | CI measures ~25 minutes. 60 leaves 2x headroom, so a slow runner does not eject an otherwise good PR. |
| `max_entries_to_build` | `5` | Cap on concurrent speculative CI runs. |
| `max_entries_to_merge` | `5` | Cap on how many PRs land in one group. |
| `min_entries_to_merge` | `2` | Minimum PRs to accumulate before merging group when available. Enables batching for concurrent PRs to reduce merge commit churn and base branch rebase races on `dev`. |
| `min_entries_to_merge_wait_minutes` | `10` | Bounded wait ceiling: holds a solo PR for up to 10 minutes to accumulate companion PRs before merging solo. Official GitHub documentation notes merge limits affect merges after build checks pass; offline configuration tests cannot verify GitHub timer start points or zero-added-latency claims, so real solo-PR timeline behavior is listed as a WP-35C live verification item. |

D21 confirmed the batch requirement. The `min_entries_to_merge = 2` and `min_entries_to_merge_wait_minutes = 10` parameters were first adopted by Codex as an adjustable engineering default; on 2026-10-02 they received itemized H08 sign-off (Option B) and live activation approval, and were applied to the `dev-merge-queue` ruleset and read back. Before that date the live ruleset still ran `1` / `5` minutes. See [`docs/evidence/human-decisions/ODP-MERGE-QUEUE-H08-ACTIVATION-001/README.md`](../evidence/human-decisions/ODP-MERGE-QUEUE-H08-ACTIVATION-001/README.md); live batch formation and real solo-PR hold time remain WP-35C observation items.
They transition repository policy from single-entry baseline (`min_entries_to_merge = 1`) to conservative batching.
Note that repository policy updates in `policy.json` (WP-35B) are distinct from live GitHub ruleset activation (WP-35C), which requires explicit operator authorization via `apply_branch_protection.py` and live solo-PR timeline receipt verification.
See [`docs/evidence/human-decisions/ODP-MERGE-QUEUE-BATCH-DESIGN-001/implementation-handoff.md`](../evidence/human-decisions/ODP-MERGE-QUEUE-BATCH-DESIGN-001/implementation-handoff.md) and [`docs/evidence/human-decisions/ODP-MERGE-QUEUE-BATCH-IMPLEMENTATION-001/README.md`](../evidence/human-decisions/ODP-MERGE-QUEUE-BATCH-IMPLEMENTATION-001/README.md).

### `strict` must be off on `dev`

`strict` ("require branches to be up to date before merging") is mutually
exclusive with a merge queue in practice: it forces every PR to be rebased onto
the current `dev` *before* it is even allowed to enter the queue, which is the
exact race the queue was installed to remove. `policy.json` therefore carries
`branches.dev.strict = false`; `main` has no queue and keeps
`strict = true`.

**A queue with `strict` still on is a half-applied state**: the queue is live,
direct merges are refused, and PRs still have to chase `dev` by hand. If
`mergeQueue(branch:"dev")` is non-null while
`branches/dev/protection/required_status_checks.strict` is `true`, either
finish the apply or roll the queue back — do not leave it there.

### Required checks on the merge group

A merge group is a different SHA from the PR head, so all four required
contexts have to report against it:

* `orchestrator`, `product`, `product-e2e-gate` — `.github/workflows/ci.yml`
  carries a `merge_group: [checks_requested]` trigger and none of these jobs
  are gated on the event type, so all three are eligible on a merge group.
  `product` and `product-e2e-gate` are, however, skipped when `change-scope`
  classifies the whole change as `development_tooling` (`ci.yml:145`,
  `ci.yml:337`); GitHub counts a skipped required job as satisfied. That skip
  predates the queue and is bounded by `config/change-review-scopes.json` —
  note that `.github/workflows/` is itself a tooling prefix, so a
  workflow-only change is covered by `orchestrator` but not by
  `tests/contract`. `orchestrator` and `change-scope` carry no condition and
  always run.
* `task-review-gate` — normally stamped by the assigned reviewer onto the PR
  head. `.github/workflows/merge-queue-review-gate.yml` re-asserts it on the
  group SHA, and stamps *failure* when the admitted PR does not already carry a
  successful gate on its own head, so an unreviewed PR is ejected rather than
  merged.

If any of those stop reporting on `merge_group`, every queued PR times out and
is ejected — a queue that blocks all merges instead of speeding them up.

## Apply

```bash
python3 delivery_toolchain/github/apply_branch_protection.py
```

Applies classic protection to `dev` and `main` from the policy, then creates or
updates the `dev-merge-queue` ruleset, then reads back
`repository.mergeQueue(branch:"dev")` and fails if the state does not match.

Read-only inspection:

```bash
python3 delivery_toolchain/github/apply_branch_protection.py --verify-only
```

Requires an admin token (`viewerPermission: ADMIN`); a non-admin run prints the
HUMAN/OPS ACTION REQUIRED block with the settings to apply by hand.

## Rollback

Disabling the queue restores direct PR auto-merge:

```bash
python3 delivery_toolchain/github/apply_branch_protection.py --disable-merge-queue
```

This deletes the `dev-merge-queue` ruleset and re-applies classic protection
with `strict` forced back to `true` on every branch, which is the pre-queue
configuration. Nothing else has to be reverted:

* The supervisor's single admission path (see "Who admits a PR" below)
  issues `gh pr merge --auto --merge`, which GitHub handles as protected
  direct auto-merge when no queue is active and as queue enqueue when the
  queue is active. The queue probe is cached per supervisor process, so
  restart the supervisor after a rollback for CI-green PRs to take the
  no-queue route; until then they keep using `--auto --merge`, which is still
  valid on a base without a queue.
* The `merge_group` triggers in `ci.yml` and `merge-queue-review-gate.yml` are
  inert once nothing emits `merge_group` events.

Manual equivalent, if the script cannot be run:

```bash
gh api repos/alfloop-dev/odayplus/rulesets --jq '.[] | select(.name=="dev-merge-queue") | .id'
gh api -X DELETE repos/alfloop-dev/odayplus/rulesets/<id>
gh api -X PUT repos/alfloop-dev/odayplus/branches/dev/protection --input - <<'JSON'
{"required_status_checks":{"strict":true,"contexts":["orchestrator","product","product-e2e-gate","task-review-gate"]},"enforce_admins":true,"required_pull_request_reviews":null,"restrictions":null}
JSON
```

PRs already sitting in the queue are dequeued when the queue is deleted; they
stay open and can be merged directly again.

## Who admits a PR

Approved task PRs have exactly one admission owner:
`dispatch_ready_tasks` -> `advance_approved_prs_to_merge` ->
`route_approved_pr_to_merge` in `.orchestrator/dispatch_engine.py`. The GitHub
bus (`sync_github_bus` -> `sync_outbound`) used to issue its own
`gh pr merge --auto --merge` every poll as well, with no window or attempt
cap; it now only projects the dispatcher's record into bus state
(`auto_merge.owner = dispatch_engine.route_approved_pr_to_merge`) and makes no
GitHub call for task PRs. Archive housekeeping PRs
(`task/OPS-ARCHIVE-AUTO-COMMIT-*`) have no canonical task, so the bus still
arms those through the same `issue_pr_merge_admission` helper.

Each tick, for a `review_approved` task whose CI is green - or still running,
when `branch_workflow.task_pr.auto_merge` is on - the route:

1. **Reads back the PR** in the task's repository and refuses (`blocked`, no
   mutation) unless it is OPEN, targets the task base, comes from a branch
   naming the task, and its head is exactly `approved_head`. A moved head is
   unreviewed work and needs its own review. An unreadable PR or unreadable
   queue enrollment is `unknown`: nothing is issued and it is never treated
   as "not queued". "Not queued" must be stated, not inferred: the GraphQL
   node has to name the same PR number, head, base and source branch as the
   PR read just before it (a PR retargeted between the two reads is not this
   one), and read OPEN,
   `merged: false`, `isInMergeQueue: false` and a null `mergeQueueEntry`. A
   missing field, a contradiction (an entry beside `isInMergeQueue: false`,
   `state` and `merged` disagreeing) or another PR, head, base or branch is
   `unknown`.
2. **Does not re-issue** when GitHub already reports the PR armed
   (`autoMergeRequest`) or queued (`isInMergeQueue`); that observation is
   recorded with zero attempts.
3. **Bounds retries** on `task.merge_route` for that exact head: no re-issue
   within 30 minutes of the last one, and no more than four admissions
   (`stalled` is reported instead). The counter belongs to the head, not to
   the tree; a new head starts a new record only after a new review.
4. **Reserves the attempt first.** The incremented `merge_route` is committed
   through the canonical revision CAS (`commit_canonical_task_transition`)
   before `gh` is called. A rejected (stale) write reloads status and issues
   nothing that tick, so a concurrent writer cannot make an admission the
   counter does not know about. An offline mutation keeps the attempt
   because its outcome is unknown. Any other failed mutation is read back
   (step 6) before it is called a refusal - a 5xx or a dropped response can
   arrive after GitHub applied it: if GitHub shows the PR armed, queued or
   merged, that is the outcome; only a whole `not_admitted` readback restores
   the previous record (a definite refusal, retried next tick); anything it
   cannot settle is `unconfirmed` and keeps the counted attempt. A failed
   undraft is a refusal too, since no admission was issued after it.
5. **Issues one command**: `gh pr merge --auto --merge` (queue enqueue, or
   armed auto-merge while checks run), or `gh pr merge --merge` only for a
   repository without a queue whose checks are already green. Never
   `--admin`.
6. **Reads back again** and records `queued`, `armed`, `merged`, or
   `unconfirmed`. A zero exit alone is never recorded as queued or merged;
   `unconfirmed` still counts toward the window and the cap. The readback is
   bound to the head, base and branch checked in step 1: if any of them moved
   in between - in the PR view or in the GraphQL queue node - the answer
   belongs to another PR state and is recorded as
   `unconfirmed`, never as the approved head's `queued`/`armed`/`merged`; the
   next tick's step 1 then refuses a moved head or base, and anything still
   passing step 1 stays under the same window and cap. Base and branch are
   compared exactly, because git refs are case-sensitive (`dev` -> `DEV` is
   another base); only the hex head SHA is compared without case. Archive
   housekeeping PRs go through the same readers and the same binding.

Bounded recovery: a task reported `stalled` or `unconfirmed` is not retried
past the cap - look at the PR's queue entry and required checks. Clearing
`merge_route` by hand is not the recovery; it erases the counter that keeps
the retry bounded. A queue ejection (the PR conflicts with `dev` after an
earlier merge) returns the task to its owner to advance the base and be
reviewed again.

## Verify

```bash
gh api graphql -f query='{repository(owner:"alfloop-dev",name:"odayplus"){mergeQueue(branch:"dev"){id configuration{mergeMethod mergingStrategy checkResponseTimeout maximumEntriesToBuild maximumEntriesToMerge minimumEntriesToMerge minimumEntriesToMergeWaitTime}}}}'
gh api repos/alfloop-dev/odayplus/branches/dev/protection/required_status_checks --jq '{strict,contexts}'
```

Queue on and correctly configured means `mergeQueue` is non-null and `strict`
is `false`.

## Symptoms and first moves

| Symptom | Likely cause | First move |
|---|---|---|
| Every queued PR is ejected after ~60 min | a required context is not reporting on `merge_group` | check the PR's `merge_group` workflow runs; confirm the triggers in `ci.yml` and `merge-queue-review-gate.yml` |
| `gh pr merge` fails with a merge-queue error | a caller is still merging directly | that caller needs the `--auto` enqueue path |
| Queue merges nothing while PRs sit "ready to merge" | nothing is enqueueing them | check the task's `merge_route` and `next`: `blocked` / `unknown` / `stalled` say why; the dispatcher stage (`dispatch_ready_tasks`) must be running - it is skipped while watchdog safe mode, discussion planning, or a dirty control plane holds dispatch |
| PRs still report `BEHIND` | `strict` is still on for `dev` | re-apply the policy; see "`strict` must be off on `dev`" above |
| Queue must go away now | any of the above, unresolved | run the rollback above |
