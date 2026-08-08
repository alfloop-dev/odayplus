# ODP-ORCH-REVIEWBUS-AUTOMERGE-001 Closeout Evidence

Owner: Claude3 · Reviewer: Antigravity2 · Approved head: `bab1034e` ·
PR [#676](https://github.com/alfloop-dev/odayplus/pull/676) into `dev`.

## Problem

`scripts/ai_status.py` has read and displayed `autoMergeRequest` since the
delivery gate was written, but no code path ever set it. A task reaching
`review_approved` therefore produced a green PR that simply sat there waiting
for a human to press the button. Because `branches/dev/protection` sets
`required_status_checks.strict = true`, every unrelated merge into `dev` put
that parked PR into `BEHIND`, which re-ran CI and — once the head moved to
compose the new base — sent the task back through `re_review`. The reviewer was
asked to look again at work that had already passed.

ReviewBus is the right owner: it already manages these PRs, already holds `gh`
credentials, and already learns the moment a task turns `review_approved`.

## Scope

- Owned: `.orchestrator/github_bus.py` outbound sync and its tests, plus the
  `.orchestrator/config_wiring_allowlist.json` entry for
  `branch_workflow.task_pr.auto_merge`.
- Not changed: `.orchestrator/auto_merge_green_prs.py` and its cron guard,
  the `scripts/ai_status.py` delivery gate, and
  `.github/branch-protection/policy.json`.

## Delivered behaviour

`enable_review_pr_auto_merge(config, bus_state, repo, task)` arms GitHub
auto-merge for each task in a finalize status. It runs immediately after the
PR upsert pass in `sync_outbound`, so a task approved during this cycle is armed
against the PR that `upsert_review_pr` just created or adopted. ReviewBus opens
its PRs as drafts and GitHub refuses auto-merge on a draft, so the PR is marked
ready first.

This wires `branch_workflow.task_pr.auto_merge`, which `config.example.json` had
declared `true` while the wiring allowlist recorded it as an unkept promise;
that allowlist entry is removed.

**Arming is not merging.** Branch protection on `dev` still holds the PR. The
freeze survives because `task-review-gate` is a *required* check that
`ai_status.py` posts per commit and stamps `success` only on the
reviewer-approved head — so a commit pushed after approval carries no gate at
all, and an armed PR waits instead of merging unreviewed work.

| PR state | Outcome |
| --- | --- |
| Draft, otherwise armable | `pr ready`, then armed |
| Ready, not yet armed | Armed without being touched first |
| GitHub already reports `autoMergeRequest` | Recorded as `enabled`, not re-armed |
| `BLOCKED` on required checks, or `BEHIND` its base | Still armed — this is what auto-merge is for |
| `DIRTY` / `CONFLICTING` | `skipped_conflicting`; left for a rebase |
| Targets the repo default instead of the task-PR base | `skipped_wrong_base` |
| Head branch does not name this task | `skipped_branch_mismatch` |
| Not `OPEN` (merged or closed) | Recorded quietly, no activity line |
| No PR number on the bus entry | `skipped_no_pr` |
| `gh` failure | `failed`, recorded once |

Two guards fail closed on stale bus state: the PR must target the task-PR base
branch (never the repository default, so an old ReviewBus PR aimed at `main` can
never be armed), and its head branch must still name this task.

`auto_merge_statuses` reads the same `ready_dispatcher.finalize_statuses` source
as the finalize dispatcher, so approval means one thing across the system: a
deployment that renames `review_approved` moves the dispatch gate and the merge
gate together instead of silently keeping PRs parked.

`_record_auto_merge` stores the outcome on the bus entry and writes an activity
line **only when the outcome changed**. The bus re-reads each approved PR every
poll, so an unchanged outcome would otherwise append a log line every 30
seconds — the shape of the 2674-failure incident that `find_existing_pr`
documents, which buries the one line that matters.

## Regression topology

`ApprovedTaskAutoMergeTests` in `.orchestrator/test_github_bus.py`:

- `test_draft_pr_is_undrafted_then_armed`
- `test_ready_pr_is_armed_without_being_touched_first`
- `test_arming_is_not_repeated_once_github_reports_it`
- `test_pr_against_the_default_branch_is_never_armed`
- `test_pr_from_another_task_branch_is_never_armed`
- `test_conflicting_pr_is_left_for_a_rebase`
- `test_blocked_and_behind_prs_are_still_armed`
- `test_merged_pr_is_recorded_without_noise`
- `test_missing_pr_number_is_skipped_quietly`
- `test_gh_failure_is_recorded_once_not_every_poll`
- `test_offline_propagates_for_bus_backoff`
- `test_sync_outbound_arms_approved_tasks_only`
- `test_sync_outbound_respects_the_config_switch`
- `test_auto_merge_statuses_track_the_finalize_gate`
- `test_config_switch_reads_branch_workflow_task_pr`

## Verification

- Implementation commit `bab1034e`:
  `python3 -m unittest discover -s .orchestrator -p 'test_*.py' -t .orchestrator`
  → 641 passed, exit 0; `python3 scripts/check_config_wiring.py` → all 245
  config keys read by code or allowlisted.
- Closeout re-verification in the task worktree at `bab1034e`, with the branch
  exactly current with `origin/dev` (`git rev-list --count HEAD..origin/dev` →
  0): both commands re-run, 641 passed and 245 keys clean.

## Closeout blocker observed on this task's own PR: a wedged CI run

Recorded because it is an infrastructure failure mode the finalize lane has no
handling for, and it is adjacent to — but distinct from — what this task fixes.

The `pull_request` CI run for `bab1034e`
([run 31119489342](https://github.com/alfloop-dev/odayplus/actions/runs/31119489342))
was created at 2026-08-06T16:20:44Z and never dispatched a single job. Over six
hours later it still reported `status = queued` with `total_count = 0` jobs and
`conclusion = null`, so the three Actions-backed required checks —
`orchestrator`, `product`, `product-e2e-gate` — never reported at all. Only
`task-review-gate` was present and green, leaving PR #676 permanently
`BLOCKED` with `mergeable = MERGEABLE`.

This was not a capacity or billing problem. A CI run created 39 seconds later on
another branch (`31119529670`, `ODP-ORCH-TASK-GIT-SCRIPTS-RESTORE-001`) started
its first job at 16:21:47 and ran to completion, and no other run in the
repository was incomplete. The wedge was bound to this one run record.

Every documented recovery path was refused by GitHub, each with an error that
contradicts the other two:

| Action | GitHub response |
| --- | --- |
| `POST .../runs/31119489342/cancel` | `409` — "Cannot cancel a workflow run that is completed" |
| `POST .../runs/31119489342/force-cancel` | `409` — "Cannot cancel a workflow re-run that has not yet queued" |
| `POST .../runs/31119489342/rerun` | `403` — "This workflow is already running" |

Closing and reopening PR #676 — which fires `pull_request: reopened`, a default
trigger type for `.github/workflows/ci.yml` — did not create a new run either.
The run's `updated_at` had advanced to 22:22:17Z against `run_attempt = 1`,
consistent with a re-run request that GitHub accepted and then never queued.

The wedge is keyed to the head SHA, so the only remaining way to obtain the
required checks is a new commit. That is normally the expensive move, because
moving the head invalidates `approved_head` and costs a full `re_review` round
trip. It is free here: `is_evidence_only_advance` carries approval forward when
the advance is a fast-forward and every changed path sits under
`APPROVAL_EVIDENCE_PATH_PREFIXES` (`docs/evidence/`), and
`review_gate_head_drifted` re-posts `task-review-gate` at the new head. This
file is that commit — it is the closeout evidence the task owes regardless, and
recording it under `docs/evidence/` is what lets it double as the CI trigger
without spending a review cycle.

Worth handing to the finalize-lane doctor as a follow-up: for this task
`scripts/orchestrator/finalize_lane_doctor.py` reports
`MISSING_REQUIRED_CHECK` and prescribes `ai_status.py assign` to "register on
the board so the required check fires". That remediation only addresses a
missing `task-review-gate`, which is a board-driven commit status. Here
`task-review-gate` was already green and the absent checks were the three
Actions-backed ones, which no board command can produce — so the emitted command
would not have moved this task. Distinguishing "the board never stamped the
gate" from "Actions never reported" would make that classification actionable.
Recorded as a follow-up candidate; not implemented under this task's scope.
