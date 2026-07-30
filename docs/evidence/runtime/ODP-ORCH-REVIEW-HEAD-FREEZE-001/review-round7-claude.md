# ODP-ORCH-REVIEW-HEAD-FREEZE-001 — Review Round 7 (Claude)

- Reviewer: Claude
- Owner: Antigravity4
- Date: 2026-07-29
- Reviewed head: `d4bca25ba0432a129a5ed84ba502c0da6f63fdeb`
  (`ODP-ORCH-REVIEW-HEAD-FREEZE-001: fix B17 vacuous test integrity`)
- PR: #505 → `dev`
- **Verdict: REJECT (returned to owner)** — B16 and B17 are verified fixed. One new
  blocking test-integrity defect (B18) in the sibling fail-closed test, plus a blocking
  delivery-sequencing defect (B19) that is specific to this task's own freeze semantics.

## 1. Delivery state — B16 FIXED

The head under review is the head on the PR, verified at review time:

```
$ git fetch origin --quiet
$ git rev-parse HEAD
d4bca25ba0432a129a5ed84ba502c0da6f63fdeb
$ git rev-parse origin/task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
d4bca25ba0432a129a5ed84ba502c0da6f63fdeb
$ gh pr view 505 --json headRefOid
{"headRefOid":"d4bca25ba0432a129a5ed84ba502c0da6f63fdeb"}
```

CI on that exact SHA is green for the check that was red in round 5/6
(`gh api .../actions/runs/30451687467 --jq .head_sha` → `d4bca25b…`):

```
orchestrator        pass    34s
performance-gate    pass    1m2s
product-e2e-gate    pass    7m44s
product             pending
task-review-gate    pending  (Pending review by Claude)
```

The reviewer commit `619f30a8` is preserved on the branch. B16 is cleared.

## 2. Verification method

Round 6's CI-faithful sandbox was reused and then extended with **mutation testing**,
because the last three rounds all turned on tests that passed while the gate under test
was inert. Passing assertions are no longer accepted as evidence; each gate was deleted
and the suite was required to go red.

```
$ mkdir -p /tmp/freeze-r7-base
$ git archive d4bca25b | tar -x -C /tmp/freeze-r7-base
$ cp .orchestrator/config.example.json .orchestrator/config.json   # what `make bootstrap` does in CI
$ env -u PANTHEON_STATUS_ROOT -u AI_NAME python3 -m pytest -m "not requires_live_env" \
      .orchestrator scripts -q --junitxml=/tmp/r7-base.xml
exit=0   <testsuite tests="603" failures="0" errors="0" skipped="0" time="2.482">
$ env -u PANTHEON_STATUS_ROOT python3 -m ruff check .orchestrator scripts
All checks passed!
```

Baseline is green and lint-clean under the CI config, not merely under the operator's
gitignored `config.json`.

### 2.1 Mutation results

Each mutant deletes exactly one fail-closed gate; the full suite was then run.
"KILLED" = at least one test failed, i.e. the gate is genuinely covered.

| # | Mutation | Result |
|---|---|---|
| m1 | `dispatch_ready_tasks`: `elif ci_status not in {"success","none"}: continue` → `elif False:` | **KILLED** by `…finalize_dispatch_on_unresolved_head_or_unknown_ci` |
| m2 | `dispatch_ready_tasks`: `if not current_head or current_head != approved_head` → `if current_head and …` | **KILLED** by the same test |
| m3 | `command_done`: `if not current_sha or current_sha != approved_head` → `if current_sha and …` | **KILLED** by `test_command_done_fails_closed_when_sha_unresolved_or_raises` |
| m5 | `dispatch_priority_for_task`: CI gate → `if False:` | **KILLED** by `test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci` |
| m7 | `dispatch_priority_for_task`: CI `except Exception: return None` → `pass` | **KILLED** by the same test |
| m8 | `emit_task_review_status_check`: head-mismatch branch → `if False:` | **KILLED** by `test_task_review_gate_status_check_pending_on_head_mismatch` |
| m9 | `command_approve`: owner==reviewer rejection removed | **KILLED** by `test_approve_saves_approved_head_and_rejects_same_owner_reviewer` |
| m11 | `command_re_review`: stop popping `approved_head` | **KILLED** by `test_explicit_re_review_command` |
| m6 | `dispatch_priority_for_task`: head gate → `if curr_head and curr_head != approved_head` (fail **open** when head unresolvable) | **SURVIVED** → B18 |
| m12 | `dispatch_priority_for_task`: head-resolution `except Exception: return None` → `pass` | **SURVIVED** → B18 |
| m10 | `dispatch_ready_tasks`: entire `if ci_status == "pending":` branch → `if False:` | **SURVIVED** → §4 observation |

### B17 — FIXED (verified by mutation, not by assertion)

`test_supervisor_suppresses_finalize_dispatch_on_unresolved_head_or_unknown_ci` now
asserts on `queue_delivery_event` (the real dispatch path), patches the same three
helpers the positive control needs, and carries a matching-head/`ci="success"` positive
control per sub-case. Both m1 and m2 kill it, so it fails when the gate is removed. The
round-6 defect is genuinely gone.

## 3. Blockers

### B18 (blocking, test integrity) — `test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci` is vacuous in its two head sub-cases

The B17 fix was applied to the `dispatch_ready_tasks` test only. Its sibling at
`.orchestrator/test_supervisor.py:8631` still has the exact shape B14 and B17 were
raised for: four all-negative `assertIsNone` calls, **no positive control**, and — for
sub-cases 1 and 2 — no patch on `ai_status.task_pr_ci_status`.

Mutants m6 and m12 delete the unresolved-head and head-exception fail-closed paths and
the whole 603-test suite still passes. Mechanism, probed directly with the mutant loaded
and a spy wrapping the real `task_pr_ci_status`:

```
[MUTANT m6 / SUBCASE1] prio=None   real CI probe results=[(None, 'unknown')]
  -> the test asserts assertIsNone(prio); it passes even though the head gate was deleted
```

With the head gate removed, control falls through to the CI gate, the **unmocked real**
`task_pr_ci_status` runs, returns `(None, "unknown")` for a task id that has no PR, and
*that* produces the `None` the assertion is checking. The pass is not attributable to the
behaviour under test. Two further consequences:

- The test shells out to `gh` from a unit test. Confirmed reached under mutation; that is
  an environment-dependent call inside a suite that is supposed to be deterministic (AC5).
- With no positive control, a config regression of the B13 class (agent missing from
  `config.example.json`) would leave all four sub-cases passing vacuously again.

For contrast, the shipped code is correct — pinning `ci="success"` on the unmutated build
still fails closed, and the positive control returns `1`:

```
[POSITIVE control  head=match ci=success]        prio=1
[SUBCASE1 + ci=success  head=None]               prio=None
```

So this is a test defect, not a behaviour defect — but AC5 asks for deterministic
regression tests, and a test that passes with the gate deleted is not one.

**Required fix:** in `test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci`,
(a) patch `ai_status.task_pr_ci_status` to `("OPEN", "success")` in the two head
sub-cases so the head gate is the only thing that can produce `None`, and (b) add the
`head=match` + `ci="success"` → `assertEqual(prio, 1)` positive control to the test, as
the sibling test now does. Re-run m6/m12 (or equivalent) and show the test failing.

### B19 (blocking, delivery sequencing) — the reviewed head cannot be the merged head

```
$ gh pr view 505 --json mergeable,mergeStateStatus,autoMergeRequest
{"autoMergeRequest":null,"mergeable":"MERGEABLE","mergeStateStatus":"BEHIND"}
```

This is not raised as generic PR hygiene. It is specific to what this task ships: once
the reviewer approves, `command_approve` records `approved_head`, and both
`command_done` and `dispatch_ready_tasks` fail closed when branch HEAD moves off it.
Approving `d4bca25b` while the PR is `BEHIND` guarantees the owner must then merge
`origin/dev` to make the PR mergeable, which moves HEAD, which trips this task's own
freeze and forces round 8. The branch must be current **before** approval, not after.

The update is low risk and should be a plain merge, not a rebase (reviewer history
`619f30a8` must survive):

```
$ git rev-list --count ad4a066e..origin/dev
3
$ git log --oneline ad4a066e..origin/dev -- .orchestrator/supervisor.py scripts/ai_status.py \
      .orchestrator/test_supervisor.py scripts/test_ai_status.py
(no output — dev has not touched any file this PR changes)
```

Fold the round-6 whitespace defect into that same push so it costs no extra head:

```
$ git diff origin/dev...HEAD --check
docs/evidence/fleet_dispatch/ODP-ORCH-REVIEW-HEAD-FREEZE-001.md:84: new blank line at EOF.
```

For the record, no CI job runs `git diff --check`, so the blank line is a hygiene defect
rather than the cause of any red check — it is listed here only because it is free to fix
in the push B19 already requires.

## 4. Non-blocking observations

- **The CI-pending branch is entirely untested.** Mutant m10 replaces
  `if ci_status == "pending":` with `if False:` and the suite stays green: dispatch is
  still suppressed by the later generic `ci_status not in {"success","none"}` gate, so
  only the `ci_pending_since_ts` bookkeeping and the 30-minute operator-escalation notice
  are lost — and nothing asserts on them (`grep -n "ci_pending_since\|ci_pending_timeout"`
  over both test files returns nothing). AC3's suppression half is covered; the escalation
  half is not. Worth one test that advances `ci_pending_since_ts` past 1800s and asserts
  the `ci_pending_timeout` activity-log entry.
- **Silent suppression still has no operator signal** (carried from round 6). The
  unresolved-head `continue` and the `elif ci_status not in {"success","none"}: continue`
  write neither `next` nor an activity-log entry, unlike the `pending` and `failure`
  branches, so a task whose head cannot be resolved sits in `review_approved` with no
  explanation.
- **The freeze is skipped when `approved_head` is absent** (carried from round 6). Both
  `command_done` and `dispatch_ready_tasks` gate on `if approved_head:`, so a task
  approved before this change bypasses the integrity check rather than failing closed.
  Defensible for backward compatibility, but it deserves an explicit comment.

## 5. Disposition

Task reopened to owner Antigravity4. Required before round 8:

1. Fix B18 — patch the CI probe in the two head sub-cases and add a positive control;
   demonstrate the test failing when the head gate is removed.
2. Merge current `origin/dev` into the task branch (plain merge, keep `619f30a8`) and
   remove the trailing blank line at
   `docs/evidence/fleet_dispatch/ODP-ORCH-REVIEW-HEAD-FREEZE-001.md:84` (B19).
3. Push one exact head; prove local == `origin/task/…` == `gh pr view 505 headRefOid`;
   confirm `git diff origin/dev...HEAD --check` is clean and `mergeStateStatus` is no
   longer `BEHIND`; obtain a green `orchestrator` check on that exact SHA, then hand off.

No code under `.orchestrator/supervisor.py`, `scripts/ai_status.py`,
`.orchestrator/test_supervisor.py` or `scripts/test_ai_status.py` was modified by this
review; all mutants were applied to a throwaway copy at `/tmp/freeze-r7-base` and the
originals were restored after each run. The only reviewer-owned change is this evidence
file. Package 10 UI, API worker logic and cloud resources were not touched.
