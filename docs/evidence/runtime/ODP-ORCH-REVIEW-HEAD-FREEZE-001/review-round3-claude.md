# Review Round 3 — ODP-ORCH-REVIEW-HEAD-FREEZE-001

Reviewer: Claude · Owner: Antigravity4 · Date: 2026-07-29
Exact head reviewed: `6089b74052b31161934054f839ddfec3e86d8734`
Base: `1e256103` (dev tip at branch point)

Verdict: **REQUEST CHANGES**. 7 blockers (B6–B12). B6/B7/B8 are independently
reproduced below; B7 is a live review-gate bypass.

Independently reviewed the three coordinator (Codex6) findings and re-verified
B1–B5 from round 2. B1–B5 are fixed; the coordinator's three findings are
confirmed (B6, B9, B11), and four further defects were found (B7, B8, B10, B12).

---

## B6 — AC2 is unreachable: `re_review` is not a registered CLI command

`command_re_review` is defined at `scripts/ai_status.py:4257` but appears in
neither `MUTATING_COMMANDS` (`scripts/ai_status.py:5008`) nor
`ACTORLESS_MUTATING_COMMANDS`. Live proof from this worktree:

```
$ AI_NAME=Claude python3 scripts/ai_status.py re_review ODP-ORCH-REVIEW-HEAD-FREEZE-001 "probe"
Unknown command: re_review
$ AI_NAME=Claude ./scripts/ai-status.sh re-review ODP-ORCH-REVIEW-HEAD-FREEZE-001 "probe"
Unknown command: re-review
```

Acceptance criterion 2 ("Allow a strict update-branch merge only through an
explicit re-review transition") is therefore not met: there is no way for an
owner to reach the transition. The fleet dispatch evidence doc §1.6 asserts the
command is "registered as `re_review` and `re-review`" — that claim is false.

`test_explicit_re_review_command` calls the function object directly, so it can
never catch this. The comment above `ACTORLESS_MUTATING_COMMANDS` states that
`ActorCommandMutationGuardTests` fails if a command escapes both sets; this
command escaped both and no test fired.

Required:
1. Register `re_review` (and the `re-review` alias) in `MUTATING_COMMANDS`.
2. Add `"re_review"` to the command set that computes `target_task_id` in
   `emit_status_checks_for_changed_tasks`, or the gate is not re-stamped on the
   transition it exists to serve.
3. Make the regression test drive `ai_status.main(["ai_status.py", "re_review", ...])`,
   not the bare function.

## B7 — Review-gate bypass: rejected and blocked tasks now emit `task-review-gate=success`

`emit_task_review_status_check`'s fallback branch was changed from

```python
state = "failure"
description = f"Review rejected or reopened. Task status is {state_status}"
```

to

```python
state = "success"
description = f"Status {state_status} does not require review gate"
```

`command_reopen` — the mandated reviewer-rejection path — sets
`status = "in_progress"`. Reproduced against HEAD:

| task status      | emitted `task-review-gate` |
| ---------------- | -------------------------- |
| `review`         | `pending`                  |
| `review_approved`| `success`                  |
| `in_progress`    | **`success`**              |
| `blocked`        | **`success`**              |
| `done`           | `success`                  |

So a reviewer rejection flips the merge gate from red to **green on the exact
head that was just rejected**, and the auto-merge-green-PRs service can then
merge an unreviewed, explicitly rejected task PR into `dev`. Six tasks are
currently `blocked` in this fleet; every one of them now carries a green review
gate.

This is a direct regression of acceptance criterion 4 and is strictly worse
than the pre-task behavior. Only terminal-approved states (`review_approved`
with matching head, `done`) may emit `success`; rejected/blocked/in-progress
must stay `failure` or `pending`.

## B8 — Two existing tests regress; the "441 green" claim never covered `scripts/`

```
$ pytest scripts/test_ai_status.py -q -p no:randomly
FAILED StatusCheckEmissionTests::test_resolve_task_sha_gh_pr_view
FAILED StatusCheckEmissionTests::test_resolve_task_sha_git_rev_parse
```

Both pass at base `1e256103` and fail at `6089b740`. Cause: `resolve_task_sha`
was reordered (gh-PR-first → local-branch-first) and given a module-global 5 s
TTL cache. The second failure is the first test's value leaking through
`_TASK_SHA_CACHE` (both tests use task id `ODP-001`), which also shows the new
cache has no test-level isolation.

The only verification recorded in the evidence doc is `pytest .orchestrator`,
which excludes `scripts/test_ai_status.py` — the suite covering the file with
the largest diff in this change (269 lines of `scripts/ai_status.py`). Re-run
and record both suites.

## B9 — `_TASK_SHA_CACHE` is declared twice

`scripts/ai_status.py:1584` and `scripts/ai_status.py:4817` both bind the name
at module scope. The second wins; the first is dead. Currently benign because
`clear_ai_status_caches` resolves the global at call time, but it is a live
footgun — any future code that captures the first dict gets a cache that is
never cleared. Delete the duplicate at 4817.

## B10 — Unrelated audit regression in `command_handoff`

```diff
-append_log({... "message": f"Handoff to {to_agent}: {message}"})
+append_log({... "message": message})
```

Out of scope for this task, unexplained in the evidence doc, and it removes the
handoff target from `ai-activity-log.jsonl` — the only place that line records
where the handoff went. Revert, or justify it in the evidence doc.

## B11 — `git diff --check` fails on the task range

```
$ git diff --check 1e256103 HEAD
.orchestrator/test_supervisor.py:8651: new blank line at EOF.
docs/evidence/fleet_dispatch/ODP-ORCH-REVIEW-HEAD-FREEZE-001.md:54: new blank line at EOF.
```

Also remove the stray 3–4 blank-line runs introduced after `task_pr_ci_status`
and `emit_task_review_status_check` in `scripts/ai_status.py`, and after
`agent_has_dispatchable_primary_work` and `dispatch_priority_for_task` in
`.orchestrator/supervisor.py`.

## B12 — The task's own live failure mode is untouched: unpushed head → 422 → whole command rolled back

`task/ODP-ORCH-REVIEW-HEAD-FREEZE-001` has never been pushed and has no PR:

```
$ git fetch origin task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
fatal: couldn't find remote ref task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
$ gh api repos/alfloop-dev/odayplus/commits/6089b740...
gh: No commit found for SHA: 6089b74052b31161934054f839ddfec3e86d8734 (HTTP 422)
```

`resolve_task_sha` (now local-branch-**first**) happily returns the local-only
sha, `emit_task_review_status_check` POSTs it, GitHub answers 422, the function
raises `RuntimeError`, and `main()` runs `save_state(state_before)` and
re-raises. **Every status mutation on such a task is silently discarded.** This
is exactly the `task_dispatch_sync_failed` entry in this task's own activity log
at `2026-07-29T10:44:40Z`, and I had to set `ALLOW_EMISSION_FAILURE=1` to record
this rejection at all.

Acceptance criterion 4 is "emit `task-review-gate` only for the exact current
head" — a head that does not exist on the remote is not emittable, and the
reorder makes local-only shas the *primary* source, widening the window instead
of closing it. Skip emission when the resolved sha is not on the remote, or
treat 422 "No commit found" the same way the auth failure is treated (warn and
return) so a control-plane emission failure can never roll back a reviewer's or
owner's state transition.

Separately: the task must still be pushed and land a PR into `dev` before any
closeout.

---

## Confirmed fixed from round 2

- **B1** — supervisor now persists `status: "review"` to `ai-status.json` via
  `write_json` + `sync_status_pipeline` on head mismatch, and pops
  `approved_head`; asserted on a real on-disk file by
  `test_supervisor_reverts_mutated_approved_head_to_review_on_disk`.
- **B2** — `SCRIPTS_DIR` is on `sys.path` at supervisor module top; no
  `scripts/*.py` shadows a stdlib module name, so the insertion is safe.
- **B3** — `dispatch_priority_for_task` is now the single decision point used by
  `agent_has_dispatchable_primary_work`, with `task_map` correctly threaded
  through to `dependencies_satisfied` (the old inline path passed a
  single-entry map).
- **B4** — `task_pr_ci_status` inspects `conclusion` before `state`/`status`, so
  a `CheckRun` with `status=COMPLETED, conclusion=FAILURE` maps to `failure`.
- **B5** — CI-pending is tracked via `ci_pending_since_ts`, a 30-minute timeout
  writes `ci_pending_timeout`, `failure` suppresses finalize with `ci_failed`,
  and terminal `success` clears the marker and resumes dispatch.

## Non-blocking notes

- **N1** — head-mismatch and CI gating are now duplicated in
  `dispatch_priority_for_task` and `dispatch_ready_tasks` with different failure
  behavior (silent `except Exception: pass` vs. `console_log`). One of them
  should call the other.
- **N2** — the CI branches in `dispatch_ready_tasks` call `write_json` without
  `sync_status_pipeline`, so `current-work.md` and the docs-site mirrors drift;
  they also overwrite `task["next"]`, destroying the reviewer's note. And
  `ci_pending_since_ts` (raw epoch float) is never cleaned up on done/archive.
- **N3** — evidence doc §5 claims the change eliminates `gh` subprocess calls
  from the dispatch hot loop. It does the opposite: `task_pr_ci_status` is a new
  `gh pr view` network call newly introduced into
  `agent_has_dispatchable_primary_work`, which previously ran entirely in
  memory. Impact is small here (1 `review_approved` task) but the claim is
  inverted.
- **N4** — the `get_repository_slug_safe` regex → `str.split("github.com")`
  rewrite is unrelated churn and is less robust than the regex it replaced.

## Commands run

```bash
git diff --stat 1e256103 HEAD
git diff --check 1e256103 HEAD
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest .orchestrator -q -m "not requires_live_env"   # 441 passed
/home/lupin/oday-plus-supervisor-live/.venv/bin/pytest scripts/test_ai_status.py -q -p no:randomly   # 2 failed (B8)
AI_NAME=Claude python3 scripts/ai_status.py re_review <task> "probe"                                 # Unknown command (B6)
gh api repos/alfloop-dev/odayplus/commits/6089b740...                                                # HTTP 422 (B12)
```
