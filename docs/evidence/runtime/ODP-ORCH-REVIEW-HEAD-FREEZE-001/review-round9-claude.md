# ODP-ORCH-REVIEW-HEAD-FREEZE-001 — Round 9 Review (Claude)

- Reviewer: Claude
- Owner: Claude3
- Date: 2026-07-29
- Exact reviewed head: `c2db28643512ad6f287eb0aebec812a145caef9c`
- PR: #505 (OPEN, MERGEABLE, head == reviewed head)
- Verdict: **REQUEST CHANGES** — 2 blockers (B23, B24), 1 non-blocking note (N3)

## 1. What round 9 got right

Both round-8 blockers are genuinely fixed at the sites they were raised against,
and I re-verified each one rather than reading the diff only.

- **B21 (`command_restore_approved` produced `review_approved` with no
  `approved_head`)** — fixed in the direction I asked for: `command_approve` now
  writes a durable `last_approved_head` (`scripts/ai_status.py:4870`) that no
  return-to-review transition pops (verified: the only `pop("approved_head")`
  sites are `reopen`/`handoff`/`re_review` at 4205/4241/4274 and
  `supervisor.py:9126`; none touch `last_approved_head`), and
  `command_restore_approved` re-freezes exactly that value and never resolves the
  current head itself (4557-4590).
- **B22 (missing `approved_head` fails open)** — all four consumers named in the
  round-8 record now fail closed, and each branch raises/returns rather than
  falling through: `dispatch_priority_for_task` (`supervisor.py:8533-8537`),
  `dispatch_ready_tasks` (`supervisor.py:9096-9122`, emits `approved_head_missing`
  once), `command_done` (`ai_status.py:4703-4712`, before
  `collect_done_delivery_metadata`), `emit_task_review_status_check`
  (`ai_status.py:5173-5178`, `pending` not `success`).
- Requirement 3 is real: `task_pr_head_and_merge_commit` reads both commits,
  `command_done` refuses when the resolved head *is* the merge commit, and
  delivery records `approved_head` / `verified_head` / `pr_head_ref_oid` /
  `pr_merge_commit` as separate facts.
- Suite green and lint clean, reproduced by me at the reviewed head:
  - `python3 -m pytest .orchestrator/test_supervisor.py scripts/test_ai_status.py -q` → exit 0
  - `python3 -m ruff check .orchestrator/supervisor.py scripts/ai_status.py .orchestrator/test_supervisor.py scripts/test_ai_status.py` → `All checks passed!`

## 2. Why this is still a rejection

Round 8's lesson was that mutation testing has a blind spot: a mutant only dies
inside a path some test reaches. Round 9 answered that with a **state-shape
census** over the `approved_head` field (absent/None/empty/match/drift/
unresolvable), which is the right instrument — for the *field*.

The two blockers below are not field-shape defects. They live in

1. a **state transition** that was never enumerated (`reopen` → `restore_approved`), and
2. a **consumer of `review_approved` that was never enumerated**
   (`higher_priority_ready_task_exists`, which does not dispatch — it *terminates*).

So the census was run over the wrong axis. The axis that finds these is: *every
producer of `review_approved`, and every consumer that reads
`status == review_approved` and takes an action.* Grepping `'"review_approved"'`
across the repo returns the full consumer list; the round-9 note treats "four
consumers" as complete, and it is not.

---

## B23 (blocker) — the owner can undo the reviewer's `reopen` rejection and re-arm the freeze

**Where:** `scripts/ai_status.py:4524-4599` (`command_restore_approved`),
interacting with `command_reopen` (`4182-4216`) and the new
`last_approved_head` written by `command_approve` (`4870`).

**Claim:** after this change ships, a reviewer's rejection is no longer durable.
`command_restore_approved` accepts `status == in_progress` + `review_notes_zh`
present + owner identity, and the *only* transition in the system that puts a
`review_approved` task into `in_progress` is `command_reopen` — i.e. the
canonical reviewer rejection. The supervisor never writes `in_progress`: its
drift path writes `review` (`supervisor.py:9120`), its preempt path writes `todo`
(`supervisor.py:5403`), and reassignment writes `todo` (`5447`+, callers pass
`new_status="todo"`). So the command's guard set now matches the rejection case
and essentially nothing else.

Round 8's B21 fix chose to *re-arm* the freeze from `last_approved_head` rather
than route the restore back through `review`. That choice is what converts this
from a fail-closed state into a fully-armed approval: pre-round-9 a restored task
carried no `approved_head`, and the B22 fix in this same commit would have failed
it closed at `done`. Post-round-9 the restored task carries a valid frozen head,
so `command_done`, `dispatch_priority_for_task` and the task-review-gate all say
GO on work the reviewer explicitly rejected.

**Failure scenario (verified, not argued):**

1. Reviewer runs `approve` at head X → `approved_head = X`, `last_approved_head = X`.
2. Reviewer finds a defect and rejects with `reopen` → `status = in_progress`,
   `approved_head` popped, `last_approved_head = X` survives by design.
3. Owner pushes nothing (branch still at X) and runs
   `restore_approved <task> "spurious downgrade"`.
4. Task is `review_approved` again with `approved_head = X`. Owner finalizes.

**Repro:** `/tmp/r9probe/probe_a_reopen_bypass.py`, run against a
`git archive c2db2864` sandbox, patching `resolve_task_sha` only.

```
=== SCENARIO: reviewer rejects with `reopen`, owner then runs restore_approved ===
  reviewer reopen (rejection): OK
  after reopen: status=in_progress approved_head=None last_approved_head=aaaa...
  owner restore_approved: OK
  RESULT: status=review_approved approved_head=aaaaaaaa
  VERDICT: BYPASS - reviewer rejection undone, freeze re-armed

=== C1 NEGATIVE CONTROL: same sequence, but branch head moved ===
  owner restore_approved: REFUSED -> ... the branch has moved to bbbbbbbb, past
  the reviewer-approved head (aaaaaaaa) ...
  RESULT: status=in_progress  (expected in_progress)

=== C2 IDENTITY CONTROL: downgrade with no reopen, head unchanged ===
  owner restore_approved: OK
  RESULT: status=review_approved  (expected review_approved = intended use)
```

C1 proves the new head guard actually executes (so this is not "the gate is
broken"), and C2 proves the intended recovery path still works (so this is not
"the command is dead"). The finding is specifically that the guard set does not
distinguish a spurious downgrade from a reviewer rejection.

**Note on scope:** this is not reachable on *this* task today
(`last_approved_head` is absent because it has never been approved), but it arms
itself on the first `approve` after this ships, for every task in the fleet.

**Acceptance criterion violated:** "Keep reviewer and owner identities separate."

**Suggested fix (owner's call):** `restore_approved` should refuse when the
downgrade was a reviewer rejection. `command_reopen` already appends a pending
reviewer→owner handoff when `actor == reviewer`; refusing while that handoff is
pending is one cheap discriminator. Routing the restore to `review` instead of
`review_approved` is the other, and it is the one that cannot be gamed.

---

## B24 (blocker) — a fail-closed (undispatchable) finalize task still terminates the owner's running worker, every cycle

**Where:** `.orchestrator/supervisor.py:8731-8732` inside
`higher_priority_ready_task_exists`, consumed at `supervisor.py:6393-6400`.

**Claim:** `higher_priority_ready_task_exists` assigns `candidate_priority = 1`
to any `review_approved` task owned by the agent, with **no `approved_head`
check and no CI check** — the two gates this task added to
`dispatch_priority_for_task`. When it returns True the supervisor calls
`terminate_worker_pid(...)`, marks the worker `superseded`, and calls
`sync_preempted_task_status` (which drops the killed task to `todo`). Nothing
then takes the freed slot, because `dispatch_ready_tasks` suppresses the
finalize task at `9096-9122`.

Result: kill → free slot → suppress → re-dispatch the lower-priority task →
kill again. The owner of a wedged task cannot make progress on *any* other task.
This is the same "finalize dispatch churn" this task exists to remove, inverted
into a worker-kill churn, and the round-9 handoff note states that this change
puts **three live tasks** into exactly that shape
(DEPLOY-WEB-PROTECTED-REDIRECT-001, ORCH-ANTIGRAVITY-CLAUDE-POOL-CANARY-001,
DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001), so it fires in production on merge.

**Failure scenario:** agent Codex owns `TASK-FINAL` (`review_approved`, no
`approved_head`) and is running `TASK-INPROG` (`in_progress`, dispatched as
`owned_in_progress_dispatch`, priority 2). Capacity 1. `TASK-FINAL` has priority
1 < 2 and is unserved, free slots 0 → preempt True → running worker killed →
`TASK-INPROG` reset to `todo` → next tick `TASK-FINAL` suppressed, `TASK-INPROG`
re-dispatched at priority 3 → killed again.

**Repro:** `/tmp/r9probe/probe_b_preempt.py` (sandbox at `c2db2864`, real
`self.config` fixture from `test_supervisor.DiscussionPlanningDispatchTests`):

```
=== SCENARIO: review_approved with NO approved_head (the B22 fail-closed shape) ===
   worker would be TERMINATED : True
   finalize task dispatchable : None (suppressed)

=== C1 POSITIVE CONTROL: approved_head present and matching ===
   worker would be TERMINATED : True
   finalize task dispatchable : 1 (dispatchable)      <- preemption legitimate here

=== C2 NEGATIVE CONTROL: no finalize task at all ===
   worker would be TERMINATED : False                 <- probe is not trivially True
```

C1 shows preemption is correct when the task can actually be dispatched; C2 shows
the probe discriminates. The defect is the asymmetry between the two functions.

**This is inside this task's own acceptance scope, not a pre-existing unrelated
bug.** The identical asymmetry already exists for the CI gate that criterion 3
delivered in an earlier round of this task
(`/tmp/r9probe/probe_b2_ci.py`):

```
CI PENDING               dispatchable=None  worker_terminated=True
CI SUCCESS (control)     dispatchable=1     worker_terminated=True
```

So criterion 3 ("Prevent owner dispatch loops while required CI is pending and
resume once terminal") is not met on the preemption path either — B22's
fail-closed change simply makes the same hole permanent instead of transient.

**Acceptance criteria violated:** "Prevent owner dispatch loops while required CI
is pending and resume once terminal"; and the task's own summary goal of ending
finalize dispatch churn.

**Suggested fix (owner's call):** factor the finalize eligibility test
(`approved_head` present + matches + CI terminal) out of
`dispatch_priority_for_task` and call it from `higher_priority_ready_task_exists`
before assigning `candidate_priority = 1`, so a task that cannot be dispatched
cannot preempt. A regression test should assert that a `review_approved` task
with no `approved_head` does **not** supersede a running worker, and the same for
CI pending.

---

## N3 (non-blocking) — `restore_approved_head` does not re-emit the task-review-gate

`emit_status_checks_for_changed_tasks` (`ai_status.py:5225-5239`) only emits when
the status *changes*, or when the command is in
`{approve, reopen, handoff, progress, start, re_review}`. `restore_approved_head`
does neither: it leaves `review_approved` → `review_approved` and is not in the
list. So attesting the head does not clear a `task-review-gate` that the new B22
branch has already stamped `pending` on that sha, and `task-review-gate` is a
required check (`.github/branch-protection/policy.json`). For a wedged task whose
PR is **already merged** the escape hatch works end to end; for one whose PR is
still open it un-wedges dispatch and `done` but not the merge, and since `done`
requires the PR merged, the only real exit there is `re_review` + `approve`.

Fail-closed, so not a blocker — but either add `restore_approved_head` to the
emit list or say plainly in the command's help text that it is only for tasks
whose PR has already merged.

## 3. Verification performed

| Check | Result |
| --- | --- |
| `git rev-parse HEAD` / `origin/task/...` | both `c2db2864`, worktree clean |
| PR #505 head | `c2db2864` — review is of the exact PR head |
| `pytest .orchestrator/test_supervisor.py scripts/test_ai_status.py -q` | exit 0 |
| `ruff check` (4 touched files) | All checks passed! |
| `approved_head` pop census | 4 sites, none touch `last_approved_head` — B21 claim holds |
| `review_approved` producer census | `command_approve`, `command_restore_approved` — B23 |
| `review_approved` consumer census | 4 fixed + `higher_priority_ready_task_exists` unfixed — B24 |
| Probe A (+2 controls) | B23 confirmed |
| Probe B (+2 controls) | B24 confirmed |
| Probe B2 (+1 control) | criterion-3 CI variant of B24 confirmed |
