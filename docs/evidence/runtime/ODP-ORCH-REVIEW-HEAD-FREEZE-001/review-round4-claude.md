# Review Round 4 — ODP-ORCH-REVIEW-HEAD-FREEZE-001

Reviewer: Claude · Owner: Antigravity4 · Date: 2026-07-29
Exact head reviewed: `854be862e761b470d5bd9563d75c62f960025f8b`
Base: `1e256103` (dev tip at branch point)
Remote head at review time: `e5a6f403` · Open PR: **none**

Verdict: **REQUEST CHANGES — delivery gate only, no code rework required.**

All seven round-3 blockers (B6–B12) are independently verified fixed and no new
code blocker was found. The task cannot be approved because the reviewed head
does not exist on `origin`, has no PR into `dev`, and therefore has no
exact-head CI. Approving a local-only head is precisely the failure mode this
task exists to eliminate (AC4), so approval is withheld on that ground alone.

---

## D1 (blocking) — Reviewed head is local-only; no PR, no exact-head CI

```
$ git rev-parse HEAD
854be862e761b470d5bd9563d75c62f960025f8b
$ git ls-remote origin refs/heads/task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
e5a6f4031340dc7d06a977d751e121c97b46fbcd
$ gh pr list --state all --head task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
[]
```

Consequences if this were approved as-is:

1. `command_approve` → `resolve_task_sha` finds no PR, falls through to the
   local branch, and freezes `approved_head = 854be862` — a commit GitHub has
   never seen.
2. `emit_task_review_status_check` POSTs to `repos/.../statuses/854be862`,
   GitHub answers 422, and (correctly, per the B12 fix) the emission is skipped
   with a warning. **No `task-review-gate` status is ever stamped**, so the
   eventual PR sits `BLOCKED` on a missing required check with no reviewer
   action left to trigger it.
3. `scripts/ai-status.sh done` would then fail its ancestor check, re-parking
   the task and resuming the finalize-dispatch churn this task removes.

Required before re-review:

1. Push the branch tip to `origin/task/ODP-ORCH-REVIEW-HEAD-FREEZE-001`.
2. Open the PR into `dev` **as the owner** (reviewer must not author it — AC4
   keeps the identities separate).
3. Let required checks run on the exact PR head and report that head + CI
   result in the handoff.
4. Then hand back to Claude; re-review is expected to be a confirmation pass on
   the same code plus the CI result.

Note that this record adds a commit on top of `854be862`, so the head to push
is the current branch tip, not `854be862` literally. The code under review is
unchanged by this commit (evidence doc only).

---

## Blockers verified fixed

### B6 — `re_review` CLI registration (AC2) — FIXED

`re_review` and `re-review` are both in `MUTATING_COMMANDS`
(`scripts/ai_status.py:5014`) and in the `target_task_id` command set of
`emit_status_checks_for_changed_tasks` (`scripts/ai_status.py:4991`). Live
probe against this head:

```
$ AI_NAME=Claude python3 scripts/ai_status.py re_review NO-SUCH-TASK-XYZ "probe"
Unknown task: NO-SUCH-TASK-XYZ
$ AI_NAME=Claude python3 scripts/ai_status.py re-review NO-SUCH-TASK-XYZ "probe"
Unknown task: NO-SUCH-TASK-XYZ
$ AI_NAME=Claude ./scripts/ai-status.sh re-review NO-SUCH-TASK-XYZ "probe"
Unknown task: NO-SUCH-TASK-XYZ
```

Both spellings now reach the command body ("Unknown task", not "Unknown
command"), through the shell wrapper as well. `test_explicit_re_review_command`
now drives `ai_status.main([...])` for both spellings, and
`ActorCommandMutationGuardTests` covers them.

### B7 — Review-gate bypass on rejected/blocked tasks (AC4) — FIXED

`emit_task_review_status_check` fallback restored to `failure`, with an
explicit `done` → `success` arm. Re-derived the full mapping against this head
with the `gh` call stubbed:

| task status                    | emitted `task-review-gate` |
| ------------------------------ | -------------------------- |
| `review`                       | `pending`                  |
| `review_approved` (head match) | `success`                  |
| `review_approved` (mismatch)   | `pending`                  |
| `in_progress`                  | `failure`                  |
| `blocked`                      | `failure`                  |
| `done`                         | `success`                  |

A reviewer rejection can no longer flip the merge gate green on the head it
just rejected.

### B8 — `scripts/test_ai_status.py` regressions — FIXED

`resolve_task_sha` priority restored to gh-PR-head-first, and
`StatusCheckEmissionTests.setUp` now calls `clear_ai_status_caches()`, so the
5 s TTL cache no longer leaks between tests sharing task id `ODP-001`.

```
$ pytest scripts/test_ai_status.py -p no:randomly
98 passed, 45 subtests passed
$ pytest .orchestrator -m "not requires_live_env"
441 passed, 10 deselected, 10 subtests passed
```

Both counts match the owner's claim exactly; the previously-uncovered
`scripts/` suite is now green and recorded in the evidence doc.

### B9 — duplicate `_TASK_SHA_CACHE` — FIXED

One declaration remains, at `scripts/ai_status.py:1584`, adjacent to
`clear_ai_status_caches`.

### B10 — `command_handoff` audit regression — FIXED

`f"Handoff to {to_agent}: {message}"` restored at `scripts/ai_status.py:4254`.

### B11 — `git diff --check` — FIXED

```
$ git diff --check 1e256103 HEAD
$ echo $?
0
```

### B12 — unpushed head → 422 → whole command rolled back — FIXED

`emit_task_review_status_check` now returns on `422` / `No commit found for
SHA` / auth failures, and its outer `except` returns instead of re-raising, so
`main()`'s `save_state(state_before)` rollback path can no longer be reached by
a control-plane emission failure. Verified live: this round's `reopen` ran
against an unpushed head **without** `ALLOW_EMISSION_FAILURE=1` and persisted.

Approve/finalize/supervisor all resolve the head through the same
`resolve_task_sha`, so `approved_head` and both mismatch checks
(`scripts/ai_status.py:4576`, `.orchestrator/supervisor.py:8532` and `:9079`)
compare like against like.

---

## Non-blocking notes (carried forward + new)

- **N1** (round 3, unaddressed) — head-mismatch and CI gating remain duplicated
  in `dispatch_priority_for_task` and `dispatch_ready_tasks` with divergent
  failure behavior (`except Exception: pass` vs `console_log`).
- **N2** (round 3, unaddressed) — the CI branches in `dispatch_ready_tasks`
  call `write_json` without `sync_status_pipeline`, so `current-work.md` and
  the docs-site mirrors drift; they also overwrite `task["next"]`, discarding
  the reviewer's approval note. `ci_pending_since_ts` (raw epoch float) is
  cleared on terminal CI but is not cleaned up on done/archive.
- **N5** (new) — when a `review_approved` task has no PR at all,
  `task_pr_ci_status` yields `unknown`, which falls through to
  `owned_finalize_dispatch`. AC3 is scoped to "CI pending", so this is not a
  blocker, but it leaves the no-PR finalize loop intact — which is exactly the
  state this task is in right now.
- **N6** (new) — `emit_task_review_status_check` now swallows *every* emission
  error, including transient ones. Correct trade against the round-3 rollback
  bug, but a failed `failure` emission will silently leave a stale `success`
  gate on the head. Worth a retry or an explicit `emission_failed` activity-log
  entry in a follow-up.

## Commands run

```bash
git rev-parse HEAD
git ls-remote origin refs/heads/task/ODP-ORCH-REVIEW-HEAD-FREEZE-001 refs/heads/dev
gh pr list --state all --head task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
git diff --stat 1e256103 HEAD
git diff --check 1e256103 HEAD
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest .orchestrator -m "not requires_live_env"   # 441 passed
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest scripts/test_ai_status.py -p no:randomly   # 98 passed
AI_NAME=Claude python3 scripts/ai_status.py re_review NO-SUCH-TASK-XYZ "probe"                    # Unknown task (B6 fixed)
AI_NAME=Claude ./scripts/ai-status.sh re-review NO-SUCH-TASK-XYZ "probe"                          # Unknown task (B6 fixed)
# emit_task_review_status_check state-mapping matrix with subprocess.run stubbed (B7 fixed)
```
