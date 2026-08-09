# ODP-ORCH-CLOSEOUT-CI-RECONCILIATION-001 Evidence

## What was wrong

Tasks whose work had demonstrably merged into `origin/dev` could not be
finalized. On 2026-08-09 the finalize lane held 21 `review_approved` tasks. In
three of them the obstacle was not missing delivery — it was how delivery was
being read.

### 1. Delivery provenance was chosen by recency, not by base

A task branch routinely carries two pull requests: the ReviewBus one against
`main` and the task-flow one against `dev`. `pull_request_status_for_branch`
returned the first MERGED PR that `gh pr view <branch>` offered, and that call
answers with whichever PR was updated most recently.

`task/ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001` at approved head
`cc560e00`:

| PR | base | state | merged |
| --- | --- | --- | --- |
| #575 | `dev` | MERGED | 2026-08-07T01:26:54Z |
| #617 | `main` | MERGED | 2026-08-07T12:25:14Z |

`gh pr view` returned #617. Because it is MERGED, the merged-list fallback never
ran, so #575 — the PR that actually delivered the approved head to `dev` — was
never examined. The gate then failed on `pr_base != "dev"` and reported that
provenance did not prove delivery to `origin/dev`, for a head that had been on
`origin/dev` for two days.

### 2. Superseded check runs were read as current

`statusCheckRollup` lists *every* check run recorded against the head commit,
including attempts that were later re-run. PR #575's rollup:

```
CheckRun orchestrator      COMPLETED SUCCESS
CheckRun orchestrator      COMPLETED SUCCESS
CheckRun product           COMPLETED FAILURE   <- stale
CheckRun product           COMPLETED SUCCESS
CheckRun performance-gate  COMPLETED SUCCESS
CheckRun performance-gate  COMPLETED SUCCESS
CheckRun product-e2e-gate  COMPLETED SUCCESS
CheckRun product-e2e-gate  COMPLETED SUCCESS
StatusContext task-review-gate       SUCCESS
```

Branch protection reads the newest run per check, which is why GitHub merged it.
`normalized_green_pr_checks` treated each entry as authoritative, so the stale
`product` FAILURE made a merged PR read as permanently red. No rerun could clear
it, because the failing entry is a historical record, not a current verdict.

### 3. A cleaned-up worktree was indistinguishable from work never done

`task_delivery_checkout` required exactly one local checkout on `task/<id>` and
raised `found 0` otherwise. Task worktrees are disposable and the branch is
deleted when its PR merges, so a task finished weeks ago legitimately has none;
`ODP-SEC-NPM-AUDIT-NANOID-001` is in that state, with PR #693 merged into `dev`
at its approved head. Waiting does not bring the worktree back.

## What changed

`scripts/ai_status.py`

- `select_merged_pull_request()` chooses provenance by fact — merged state,
  exact task branch, configured base, reviewer-approved head, recorded merge
  time and merge commit. `pull_request_status_for_branch()` searches the full
  candidate list for that PR before falling back to the historical
  recency-ordered lookup, so callers that only want "some PR for this branch"
  are unaffected.
- `latest_status_check_runs()` collapses a rollup to the newest run per
  `(workflow, check)`, ordered by `status_check_timestamp()` with rollup order as
  tie-break. Superseded runs are still written into `delivery["ci_checks"]`
  marked `superseded: true`, so a recovered run is never mistaken for a clean
  one.
- `status_check_timestamp()` ignores the zero-time sentinel `gh` emits for an
  unset `DateTime` and ranks each entry by its newest *real* timestamp. Ordering
  on the raw `completedAt` inverted the intended rule: a re-run that is still
  running reports `completedAt: "0001-01-01T00:00:00Z"`, which sorts below every
  real timestamp, so the older completed SUCCESS it was started to replace won
  the collapse and the PR read green while its newest run had not concluded.
  That is a fail-open in the direction this reader exists to close, so a
  sentinel now counts as absent rather than as the year 1.
- `resolve_task_delivery_checkout()` reports an absent checkout instead of
  raising. `collect_done_delivery_metadata()` then verifies the approved head
  exists as a commit object and proves delivery from merged-PR provenance alone;
  it reads no local working tree.

`scripts/orchestrator/finalize_lane_doctor.py`

- `find_pr()` lists all PRs for the branch and prefers the one targeting the
  promotion base, so the diagnosis describes the PR that governs the merge.
- `latest_checks_by_name()` applies the same newest-run-wins rule, so a task
  whose checks were re-run green is no longer reported as `CI_FAILED` with a
  remediation telling its owner to rerun them. Its `check_timestamp()` discards
  the same zero-time sentinel; without it the doctor reported `READY` for a PR
  whose newest run was still in progress.

## What still fails closed

Nothing here lets an unmerged or head-mismatched task finalize.

- Two merged PRs matching the same branch, base and head but reporting
  different merge commits are ambiguous provenance and raise.
- A check whose *newest* run is red or unfinished is still red. Collapsing the
  rollup applies GitHub's own rule; it does not forgive a current failure. An
  unfinished run wins the collapse on its `startedAt`, so re-running a check
  turns the PR pending rather than leaving the previous pass standing in.
- With no task checkout, the merged-PR gate is the only remaining evidence, so
  the path refuses to run when that gate is disabled, and refuses when the
  approved head is not present as a commit object. Gates that cannot be
  evaluated are recorded as unevaluated (`git_clean: null`,
  `git_clean_evaluated: false`, `push_status: "no_task_checkout"`) — never as
  passed.
- More than one task-owned checkout is still ambiguous and still raises.

Verified against live state, read-only:

| Task | Approved head | Outcome |
| --- | --- | --- |
| `ODP-ORCH-WORKTREE-BASE-ADVANCE-LIVE-ROLLOUT-001` | `cc560e00` | resolves via PR #575 |
| `ODP-SEC-NPM-AUDIT-NANOID-001` | `e497a465` | resolves via PR #693, no checkout |
| `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001` | `eede5cff` | still blocked — approved head never merged (PR #567 merged `b664a8ea`) |
| `ODP-ORCH-REBASE-HEAD-LIVENESS-001` | `d518d04c` | still blocked — approved head never merged (PR #577 merged `cdc5e5b6`) |
| `ODP-ORCH-AGENT-LOAD-BALANCE-001-SIDECAR-REVIEW` | `f97083d7` | still blocked — newest `performance-gate` run is red |

Of 21 `review_approved` tasks, 2 become finalizable; the other 19 remain blocked
for reasons the gate should block on.

## Verification

```bash
python3 -m pytest scripts/test_ai_status.py \
  scripts/orchestrator/ .orchestrator/test_supervisor.py -q
# 634 passed, 214 subtests passed

python3 -m ruff check scripts/ai_status.py scripts/test_ai_status.py \
  scripts/orchestrator/finalize_lane_doctor.py \
  scripts/orchestrator/test_finalize_lane_doctor.py
# All checks passed!
```

Regression coverage added in `scripts/test_ai_status.py`
(`HistoricalClosemergeProvenanceTests`, 24 cases) and
`scripts/orchestrator/test_finalize_lane_doctor.py` (7 cases), built from the
live PR payloads above: stale rollup in both directions, the zero-`completedAt`
in-progress re-run in the exact `gh` field shape (`conclusion: ""`, Go zero
time) against both readers, absent and duplicated checkouts, ambiguous merge
provenance, and every rejection case for candidate selection.
