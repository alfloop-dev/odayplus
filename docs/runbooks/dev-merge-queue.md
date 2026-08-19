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
| `min_entries_to_merge` | `1` | Do not hold a ready PR waiting for company; latency matters more than batching here. |
| `min_entries_to_merge_wait_minutes` | `5` | Inert while `min_entries_to_merge` is 1; kept explicit so raising the minimum later is a one-value change. |

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
  are gated on the event type.
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

* `.orchestrator/github_bus.py` automatically arms auto-merge with
  `gh pr merge --auto --merge`, which GitHub handles as direct auto-merge when
  no queue is active and as queue enqueue when the queue is active.
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
| Queue merges nothing while PRs sit "ready to merge" | nothing is enqueueing them | `.orchestrator/github_bus.py` auto-merge processing is not running |
| PRs still report `BEHIND` | `strict` is still on for `dev` | re-apply the policy; see "`strict` must be off on `dev`" above |
| Queue must go away now | any of the above, unresolved | run the rollback above |
