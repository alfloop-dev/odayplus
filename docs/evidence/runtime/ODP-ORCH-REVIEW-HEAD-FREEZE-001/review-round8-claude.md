# ODP-ORCH-REVIEW-HEAD-FREEZE-001 — Review Round 8 (Claude)

- Reviewer: Claude
- Owner: Claude3 (rounds 1-6 owner Antigravity4)
- Date: 2026-07-29
- Exact head reviewed: `960b4c0462cb8cefb4fd91aee5f8172dce6c5e2c`
- PR: #505 -> `dev`
- Verdict: **REJECTED** — 2 blockers (B21, B22). B18 and B19 are cleared.

Local, `origin/task/ODP-ORCH-REVIEW-HEAD-FREEZE-001` and PR #505 head all agree on
`960b4c04`. Branch is 0 commits behind `origin/dev` (`4b329493`).

## Method

Same method as round 7, because it is the only one that has produced true findings on
this task: **mutation testing** in a CI-faithful sandbox, not read-through of assertions.

```
git archive 960b4c04 | tar -x -C /tmp/r8s-a
cp .orchestrator/config.example.json .orchestrator/config.json   # = make bootstrap
env -u PANTHEON_STATUS_ROOT -u AI_NAME python3 -m pytest -m "not requires_live_env" \
    .orchestrator scripts -q --junitxml=...
```

Baseline reproduced independently: **exit=0, tests=607, failures=0, errors=0, skipped=0**.
`ruff check .orchestrator scripts` -> All checks passed. `git diff origin/dev...HEAD
--check` -> clean.

### Harness validation (new this round)

My first matrix reported 24/24 killed. That result was **wrong** and I discarded it: the
harness passed a stripped `env=` to the subprocess, so pytest failed to start and every
mutant looked "killed". Controls now run before the matrix and are recorded here:

| control | expected | observed |
| --- | --- | --- |
| identity (no mutation) | green | exit=0, 607 tests |
| negative control (comment text only) | green | exit=0, 607 tests |
| positive control (`reason = "owned_finalize_dispatch_XX"`) | red | exit=1, 2 failures |

A mutation matrix without a passing identity run and a surviving negative control is not
evidence. Recording this so the next round does not repeat it.

## Mutation matrix — 24 mutants, 21 killed, 3 survived

| id | mutated gate | verdict |
| --- | --- | --- |
| s1 | `dispatch_ready_tasks`: delete whole approved-head gate | KILLED |
| s2 | head gate fails **open** when head unresolvable | KILLED |
| s3 | drift no longer demotes task back to `review` | KILLED |
| s4 | drop `continue` after head problem | KILLED |
| s5 | delete entire `ci_status == "pending"` branch (round-7 m10) | KILLED |
| s6 | delete 30-minute CI-pending escalation | KILLED |
| s7 | delete `ci_status == "failure"` suppression | **SURVIVED** |
| s8 | delete catch-all unresolved-CI suppression | KILLED |
| s9 | drop `continue` in pending branch | KILLED |
| s10 | `dispatch_priority_for_task`: delete head gate (round-7 m6) | KILLED |
| s11 | head-resolution `except` fails open (round-7 m12) | KILLED |
| s12 | `dispatch_priority_for_task`: delete CI gate | KILLED |
| a1 | `command_done`: delete approved-head gate | KILLED |
| a2 | `command_done`: resolve `except` fails open | KILLED |
| a3 | `command_done`: fails open when head unresolvable | KILLED |
| a4 | `command_approve`: no abort on unresolvable head (B20-a) | KILLED |
| a5 | `command_approve`: resolve `except` not fail-closed | KILLED |
| a6 | `command_approve`: delete immutability check (B20-b) | KILLED |
| a7 | `command_approve`: stop recording `approved_head` | KILLED |
| a8 | delete owner!=reviewer separation check | KILLED |
| a9 | `command_reopen` no longer clears `approved_head` | **SURVIVED** |
| a10 | `command_handoff` no longer clears `approved_head` | **SURVIVED** |
| a11 | `command_re_review` no longer clears `approved_head` | KILLED |
| a12 | `task-review-gate` emits success on drifted head | KILLED |

Every gate the owner claimed is genuinely covered. Round-7 survivors m6/m12/m10 are
independently confirmed KILLED (s10/s11/s5). The owner's 10-mutant matrix is honest and
reproducible; it simply did not reach `command_restore_approved`, which is where B21 is.

## Cleared

**B18 — vacuous head sub-cases.** Fixed at `438cb2c2`. `ai_status.task_pr_ci_status` is
now pinned to `("OPEN","success")` in the head sub-cases, so the head gate is the only
thing that can return `None`, and a `head=match/ci=success -> priority 1` positive control
was added. Independently confirmed: mutants s10 (m6) and s11 (m12) are both killed by
`test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci` — the exact test
that let them survive at round-7 head `d4bca25b`.

**B19 — PR behind dev.** Cleared at `960b4c04`. `git rev-list --count HEAD..origin/dev` =
0, `mergeable = MERGEABLE`, and the required dev merge was taken as a plain two-parent
merge, so reviewer commit `619f30a8` is preserved. `--check` is clean.

## B21 (BLOCKER) — `restore_approved` manufactures the un-frozen state the code asserts is unreachable

`scripts/ai_status.py:4525 command_restore_approved` is the **second** producer of
`review_approved`, and it never records `approved_head`. Every consumer of the freeze —
`command_done:4577`, `dispatch_priority_for_task:8533`, `dispatch_ready_tasks:9091` — is
guarded by `if approved_head:`. A task restored through this command therefore has the
entire post-review freeze silently disabled, which is exactly the fail-open shape the
owner correctly identified and fixed for `command_approve` in B20-a.

The comment shipped at `.orchestrator/supervisor.py:9086-9090` states the invariant:

> Backward compatibility: a task approved before the freeze shipped has no approved_head
> ... **New approvals cannot reach this state** -- command_approve now fails closed when
> the head is unresolvable (B20).

That invariant is false at this head. `restore_approved` reaches the state, and it is
owner-invokable (`MUTATING_COMMANDS` entry at `scripts/ai_status.py:5060`). What B20-a
intended as a bounded one-time migration allowance is instead a permanently reachable
bypass.

### Reproduction (`/tmp/r8s-a/repro_restore_bypass.py`)

`command_reopen:4201` sets `in_progress`, pops `approved_head`, and **keeps**
`review_notes_zh` — which is precisely the precondition `restore_approved:4544` requires.

```
1. after approve      : review_approved approved_head= aaaaaaaa
2. after reopen       : in_progress     approved_head= <none> | review_notes_zh kept: True
3. after restore      : review_approved approved_head= <ABSENT>
4. after done         : done  <-- FINALIZED at UNREVIEWED head bbbbbbbb
CONTROL: freeze intact -> done correctly BLOCKED: Cannot finalize task BYPASS-2:
         current branch HEAD (bbbbbbbb) differs from reviewer-approved head ...
```

The control matters: with `approved_head` present the gate fires correctly, so this is the
freeze being *skipped*, not the freeze being broken.

### Scope of the claim, stated honestly

`command_done` runs the freeze gate at line 4576, **before**
`collect_done_delivery_metadata` at 4594. A second run with the done-path internals left
unmocked (only identity/git/log stubbed) in a real git repo on a real task branch reached
the *delivery* gates:

```
pre-done: status= review_approved approved_head= <ABSENT>
done blocked: Cannot finalize task: latest commit subject must include task id BYPASS-1.
done blocked: delivery_gates.require_merged_pr is enabled, but the repository has no
              git remote to verify the task PR merge.
```

Reaching those messages *is* the proof that the freeze never fired at the drifted head.
The commit-convention gate is owner-satisfiable; `require_merged_pr` is real
defence-in-depth. So this is not a one-command bypass. But it reproduces the originating
incident recorded on this very task (`PR #503 heads 25659ef3 -> 9a78ff97 -> 1591c222`):
approve at head A -> PR merges at A -> owner pushes B -> reopen/supervisor downgrade ->
`restore_approved` -> `done` at B, with AC1's "reject owner finalize when branch HEAD
differs" never evaluated. AC1 is not met while this path exists.

### Suggested fix (owner's call)

Do **not** simply mirror `command_approve` and resolve the current head inside
`restore_approved` — that would let the owner self-freeze a head no reviewer ever saw,
which is worse than the present state. Two better options:

1. Have `restore_approved` route to `review` instead of `review_approved`, so a reviewer
   re-stamps the head. Simplest, and consistent with B20-b's `re_review` direction.
2. Persist the approved head durably (e.g. `last_approved_head`, written by
   `command_approve` and *not* popped on return-to-review) and have `restore_approved`
   re-freeze that value, refusing when it is absent or when the branch has moved past it.

Either way it needs a mutant-killed regression test.

## B22 (BLOCKER) — `review_approved` with *absent* `approved_head` fails open in all three consumers

Raised by the coordinator on PR #505 (comment `5118353267`, 2026-07-29T13:24:06Z) from a
live incident at 13:20Z. I reproduced it independently at this exact head rather than
taking it on report; it is confirmed, and it is strictly broader than B21 — B21 is one
*producer* of the un-frozen state, B22 is the fact that the state itself disables the
freeze everywhere.

`approved_head` absent is not a hypothetical legacy shape. It is the current live record:

```
ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001
  status = review_approved   approved_head = None   last_update = 2026-07-29T13:20:50Z
  branch head e0147acb  ·  dev merge 4b329493 (PR #515)
```

The live Supervisor dispatched that task for finalize as run
`claude-20260729T132006Z-9d640411`; the coordinator terminated and parked it. Nothing in
the shipped code would have stopped `done` from landing at an unreviewed head.

### Reproduction (`/tmp/r8probe-a/repro_missing_head_failopen.py`, sandbox at `960b4c04`)

Task dict is the live shape verbatim, `approved_head` key simply absent:

```
=== PROBE 1: supervisor.dispatch_priority_for_task ===
  priority = 1   (expected None = fail closed)          VERDICT: FAIL-OPEN
=== PROBE 1b: control -- approved_head present but drifted ===
  priority = None                                       VERDICT: gate works when head IS present
=== PROBE 2: ai_status.command_done ===
  status = done <-- FINALIZED with NO reviewer-approved head
                                                        VERDICT: FAIL-OPEN
```

Probe 1b is the control that makes this a *skipped* gate rather than a broken one: the
identical task with `approved_head` recorded and the branch drifted is correctly refused.

The mechanism is one line in each consumer — `if approved_head:` — plus, in
`dispatch_ready_tasks`, a comment that makes the fail-open deliberate:

- `.orchestrator/supervisor.py:9086-9091` — "Backward compatibility: a task approved
  before the freeze shipped has no approved_head, so it is dispatched without the
  integrity check rather than being wedged forever."
- `.orchestrator/supervisor.py:8533` — `dispatch_priority_for_task`
- `scripts/ai_status.py:4577` — `command_done`

That comment's justification ("New approvals cannot reach this state") is false twice
over: `command_restore_approved` reaches it (B21), and every pre-freeze task already sits
in it. Backward compatibility is a real requirement, but it has to be an explicit audited
migration, not an automatic bypass of the control this task exists to add.

### Why the 607-test receipt did not catch it

Every one of the 32 `approved_head` references in `scripts/test_ai_status.py` and
`.orchestrator/test_supervisor.py` sets the key. No test constructs the missing-key shape.
`test_dispatch_priority_fails_closed_on_unresolved_head_or_unknown_ci` covers
*unresolvable current* head, which is a different branch of the same gate. So the suite is
green, the round-7/round-8 mutation matrices are honest, and the live failure is still
uncovered — mutation testing only kills mutants inside code paths some test reaches, and
nothing reaches this one.

### Required to clear B22 (coordinator's four regressions, verbatim scope)

1. `review_approved` + missing `approved_head` never receives finalize priority and is
   never dispatched (`dispatch_priority_for_task` **and** `dispatch_ready_tasks`).
2. `command_done` rejects it **before** `collect_done_delivery_metadata` — i.e. the freeze
   gate must not be reachable-past; the delivery gates are not an acceptable backstop.
3. A merged PR is finalized on immutable PR `headRefOid == approved_head`, with
   `mergeCommit` recorded separately. `resolve_task_sha:4863` already prefers `headRefOid`
   and falls back to local `git rev-parse`; `mergeCommit` is fetched at
   `ai_status.py:1577` but never persisted as a distinct field. The distinction has to be
   explicit, not incidental.
4. The exact staging shape (head `e0147acb`, merge `4b329493`) can only close after an
   explicit head restoration.

## Non-blocking findings

**N1 (s7) — `ci_status == "failure"` branch has no behavioural coverage.** Deleting the
whole branch keeps the suite green because `"failure"` then falls into the catch-all
`elif ci_status not in {"success","none"}` and is still suppressed. Fail-closed safety is
preserved, so this is not a defect — but the branch's distinguishing side effects (the
`ci_failed` activity-log type, and popping `ci_pending_since_ts` so a stale timer cannot
fire the 30-minute escalation early) are untested. Same class as round-7's m10 note.

**N2 (a9/a10) — `reopen` and `handoff` clearing `approved_head` is now load-bearing and
untested.** Both pops are present and correct in shipped code, but no test asserts either.
B20-b changed their stakes: before it, a leftover `approved_head` was harmless because
`approve` overwrote it; after it, a leftover `approved_head` makes `command_approve` refuse
outright. A future refactor dropping either pop would wedge the whole reject -> rework ->
re-approve cycle for the entire fleet, and all 607 tests would stay green. `re_review`'s
pop *is* covered (a11 killed); its two siblings should be too.

## Acceptance criteria

| AC | Verdict |
| --- | --- |
| 1. Persist exact approved commit, reject owner finalize on drift | **FAIL** (B21, B22) |
| 2. Strict update-branch merge only via explicit re-review | PASS (a6, a11, a12 killed) |
| 3. No owner dispatch loops while CI pending; resume when terminal | PASS (s5, s6, s9, s12 killed) |
| 4. Reviewer/owner identities separate; gate only for exact current head | PASS (a8, a12 killed) |
| 5. Deterministic regression tests for post-review mutation and CI waiting | **FAIL** (B22: missing-head shape untested) |
| 6. No Package 10 UI/API/worker/cloud changes | PASS (diff touches only `.orchestrator/`, `scripts/`, `docs/evidence/`) |

## CI at exact head `960b4c04`

`orchestrator` SUCCESS · `performance-gate` SUCCESS · `product-e2e-gate` SUCCESS ·
`product` IN_PROGRESS · `task-review-gate` PENDING · `mergeStateStatus` BLOCKED.
