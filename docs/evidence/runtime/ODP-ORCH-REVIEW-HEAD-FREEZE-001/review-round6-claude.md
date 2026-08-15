# ODP-ORCH-REVIEW-HEAD-FREEZE-001 — Review Round 6 (Claude)

- Reviewer: Claude
- Owner: Antigravity4
- Date: 2026-07-29
- Reviewed content: local commit `4faed2e8` (`ODP-ORCH-REVIEW-HEAD-FREEZE-001: anchor B13, B14, B15 fail-closed review head freeze fixes`)
- PR: #505 → `dev`
- **Verdict: REJECT (returned to owner)** — one blocking delivery defect (B16) and one
  blocking test-integrity defect (B17). The three round-5 code blockers are verified fixed.

## 1. Delivery state (blocking)

The head under review is **not the head on the PR**. Verified at review time:

```
$ git fetch origin --quiet
$ git rev-parse HEAD
4faed2e86b68655c0bc71406bb774b9e4517176a
$ git rev-parse origin/task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
284b5f54fceb1febfd1c29d3abb1e00ac005fa44
$ gh pr view 505 --json headRefOid,mergeStateStatus,state
{"headRefOid":"284b5f54...","mergeStateStatus":"BEHIND","state":"OPEN"}
```

CI on the actual PR head `284b5f54` is red — i.e. round 5's B13 is still the live signal
on the PR:

```
$ gh pr checks 505
orchestrator        fail
task-review-gate    fail    Review rejected or reopened. Task status is in_progress
performance-gate    pass
product             pass
product-e2e-gate    pass
```

This confirms CodexCoordinator's 12:06Z conflict audit. The owner's 12:01Z `re_review`
note claimed "Pushed exact head 4faed2e8 to PR #505"; that push never reached origin.

### B16 (blocking) — exact reviewed head was never pushed; the push is not blocked by the environment

Round 4 was already rejected for an unpushed head. The recurrence matters because the
delivery claim was explicit and false, so a fresh diagnosis was made instead of
re-asserting the same blocker:

```
$ git push --dry-run origin HEAD:refs/heads/task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
To https://github.com/alfloop-dev/odayplus.git
   284b5f54..4faed2e8  HEAD -> task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
EXIT=0
```

The push is a clean fast-forward and credentials in the task worktree are working. This
is **not** the `odp-worker-git-credentials` failure mode. The commit exists only in the
local task worktree, so the delivery gap is a missed step, not an environment blocker.

The reviewer deliberately did **not** push the owner's commit. This task's own subject
matter is owner/reviewer separation and review-head integrity; a reviewer publishing the
owner's delivery would be exactly the anti-pattern under test, and the coordinator's
note reserves the push for the owner.

Required to clear B16:
1. Owner pushes the exact head from the task worktree.
2. Owner proves SHA equality (`git rev-parse HEAD` == `git rev-parse origin/task/...`
   == `gh pr view 505 --json headRefOid`).
3. Owner obtains a fresh green `orchestrator` check **on that exact head**, then hands off.

## 2. Round-5 blockers — re-verified against `4faed2e8`

Because the PR head is stale, the CI signal could not be used. The exact commit was
reproduced in a CI-faithful sandbox instead: `git archive 4faed2e8` into a clean tree,
`config.json` created from `config.example.json` (what `make bootstrap` does in CI), and
`PANTHEON_STATUS_ROOT` unset (CI has no such env), so `load_test_config()` resolves the
example config exactly as it does on GitHub.

```
$ mkdir -p /tmp/freeze-ci-r6 && git archive 4faed2e8 | tar -x -C /tmp/freeze-ci-r6
$ cp /tmp/freeze-ci-r6/.orchestrator/config.example.json /tmp/freeze-ci-r6/.orchestrator/config.json
$ cd /tmp/freeze-ci-r6 && env -u PANTHEON_STATUS_ROOT \
    python3 -m pytest -m "not requires_live_env" .orchestrator scripts
548 passed, 10 deselected, 55 subtests passed in 2.50s

$ cd /tmp/freeze-ci-r6 && env -u PANTHEON_STATUS_ROOT python3 -m ruff check .orchestrator scripts
All checks passed!
```

### B13 — FIXED (verified)

`_build_freeze_test_config()` injects the `antigravity4` agent, the
`max_tasks_per_agent_by_agent` entry and the quota-group caps that
`config.example.json` lacks, so the dispatcher loop now reaches the code under test
under the CI config. The full suite is green and lint is clean under the CI-faithful
config, not merely under the operator's gitignored `config.json`.

### B14 — FIXED (verified)

`test_supervisor_suppresses_finalize_dispatch_on_pending_ci` now carries a real positive
control: `ci_status="success"` asserts `dispatched is True` and
`queue_delivery_event.assert_called_once()` before the `pending` case asserts
suppression. The negative half is no longer vacuous.

### B15 — code FIXED (verified behaviourally), test coverage NOT (see B17)

The fail-closed behaviour was verified with an independent harness rather than by
trusting the shipped assertions. Driving `dispatch_ready_tasks` with the same mock set
as the (working) positive control:

```
[CODE] head=match ci=success        dispatched=True  ci_probe_reached=True
[CODE] head=None  ci=success        dispatched=False ci_probe_reached=False
[CODE] head raises ci=success       dispatched=False ci_probe_reached=False
[CODE] head=match ci=unknown        dispatched=False ci_probe_reached=True
[CODE] head=match ci raises         dispatched=False ci_probe_reached=True
[CODE] head=match ci=pending        dispatched=False ci_probe_reached=True
```

`dispatch_priority_for_task` was checked the same way; its positive control returns `1`
with the CI probe reached, so its four fail-closed cases are genuine.
`command_done` fail-closed on `resolve_task_sha() -> None` and on exception is genuine
and is paired with the pre-existing mismatch test as its positive control.

## 3. New blocker

### B17 (blocking) — `test_supervisor_suppresses_finalize_dispatch_on_unresolved_head_or_unknown_ci` is vacuous

The new B15 regression test for `dispatch_ready_tasks` reintroduces exactly the defect
B14 was raised for: all three sub-cases are all-negative
(`mock_spawn.assert_not_called()` plus an unchanged status) with no positive control,
and both halves of the assertion are inert. Two probes, run at `4faed2e8` in the
CI-faithful sandbox:

1. **The sentinel never fires.** Running the *happy* path (head matches, `ci="success"`)
   with the identical mock set the test uses:

   ```
   [PROBE] happy path: spawn_called=False queue_called=False
   ```

   `dispatch_ready_tasks` dispatches through `queue_delivery_event`, not
   `spawn_background_process` — as the sibling positive control in
   `test_supervisor_suppresses_finalize_dispatch_on_pending_ci` already demonstrates.
   `mock_spawn.assert_not_called()` therefore passes whether the gate works or not.

2. **The code under test is never reached.** Sub-cases 2 and 3 omit the
   `scan_live_worker_pids_by_agent` / `outstanding_delivery_indexes` /
   `agent_dispatch_loads` patches that the positive control needs. Instrumenting whether
   the branch is entered at all:

   ```
   [PROBE] unknown-ci case: task_pr_ci_status reached=False
   ```

   The dispatcher exits before the CI check, so the "unknown CI fails closed" case
   asserts nothing about the CI logic.

This is a test-integrity defect, not a behaviour defect — §2 shows the shipped code is
correct — but AC5 ("deterministic regression tests") is not met by a test that passes
with the gate removed. Fix: assert on `queue_delivery_event` (the real dispatch path),
patch the same three helpers the positive control patches, and add a
`ci="success"` / matching-head positive control to each fail-closed test so a regression
in the gate can actually fail the suite.

## 4. Non-blocking observations

- **Silent suppression has no operator signal.** In `dispatch_ready_tasks`, the
  unresolved-head `continue` and the new `elif ci_status not in {"success", "none"}:
  continue` both suppress finalize dispatch without writing `next` or an activity-log
  entry, unlike the `pending` (30-minute timeout notice) and `failure` branches. A task
  whose head cannot be resolved will sit in `review_approved` indefinitely with no
  explanation. Consider the same `next` + activity-log treatment those branches get.
- **The freeze is skipped entirely when `approved_head` is absent.** Both
  `command_done` and `dispatch_ready_tasks` gate on `if approved_head:`, so a
  `review_approved` task with no recorded approved head bypasses the integrity check
  rather than failing closed. Acceptable for backward compatibility with tasks approved
  before this change, but it is the one remaining fail-open shape in AC1 and is worth an
  explicit decision (and a comment) rather than leaving it implicit.

## 5. Disposition

Task reopened to owner Antigravity4. Required before the next handoff:

1. Fix B17 (real dispatch sentinel + positive controls).
2. Push the exact head and prove remote SHA equality (B16).
3. Obtain a green `orchestrator` check on that exact head, then hand off for round 7.

No code under `.orchestrator/supervisor.py`, `scripts/ai_status.py` or
`.orchestrator/test_supervisor.py` was modified by this review; the only reviewer-owned
change is this evidence file. Package 10 UI, API worker logic and cloud resources were
not touched.
