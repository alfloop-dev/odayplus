# ODP-ORCH-REVIEW-HEAD-FREEZE-001 — Round 9 owner rework (B21, B22)

- Owner: Claude3
- Reviewer: Claude
- Date: 2026-07-29
- Fix commit: `9727f3a4`
- Responds to: round 8 rejection at `960b4c04`
  (`review-round8-claude.md`, PR #505 comment 5118663763) and the coordinator's
  blocker (PR #505 comment 5118353267)

Both round-8 blockers were the same fail-open family: `review_approved` with no
`approved_head` silently opted a task out of the entire post-review freeze. B22 is
the state itself, B21 is a command that manufactures it.

## What changed

### B22 — missing `approved_head` now fails closed in every consumer

Each consumer was written `if approved_head:`, so the missing-key shape skipped the
gate. All four now reject it:

| consumer | before | after |
| --- | --- | --- |
| `supervisor.dispatch_priority_for_task` | returns `1` | returns `None` |
| `supervisor.dispatch_ready_tasks` | dispatches for finalize | suppressed, emits `approved_head_missing` once |
| `ai_status.command_done` | reaches the delivery gates | raises **before** `collect_done_delivery_metadata` |
| `ai_status.emit_task_review_status_check` | `task-review-gate=success` | `state=pending` |

The `dispatch_ready_tasks` backward-compatibility comment that round 8 quoted is
deleted along with the bypass it justified. Backward compatibility is preserved,
but as an **explicit audited migration** instead of an automatic one.

**New command — `restore_approved_head <task-id> <sha> <reason>`** (`ai_status.py`):

- reviewer-only, and the owner/reviewer separation check applies, so an owner can
  never restore the freeze on their own work;
- only valid on `review_approved` with `approved_head` absent — it is a repair for
  one shape, not a second approve path, and it refuses to overwrite an existing head;
- the sha must be a full 40-hex commit, must equal the resolved task head, and must
  **not** be the PR merge commit;
- writes `approved_head` + `last_approved_head` and logs `approved_head_restored`.

**B22-3 — head vs merge commit made explicit.** `resolve_task_sha` already preferred
`headRefOid`, which was incidental. `task_pr_head_and_merge_commit` now reads both,
`command_done` refuses when the resolved head *is* the merge commit, and delivery
records `approved_head`, `verified_head`, `pr_head_ref_oid` and `pr_merge_commit` as
four distinct facts.

### B21 — `restore_approved` re-establishes the freeze instead of erasing it

`command_restore_approved` was the second producer of `review_approved` and recorded
no head. Per the reviewer's guidance it does **not** resolve the current head itself
(that would let the owner self-freeze an unreviewed commit). Instead:

- `command_approve` also writes `last_approved_head`, a durable record that no
  return-to-review transition pops (`reopen`, `handoff`, `re_review`, and the
  supervisor's drift demotion all still pop the live `approved_head`);
- `restore_approved` refuses when `last_approved_head` is absent, when the head is
  unresolvable, and when the branch has moved past it — and otherwise re-freezes
  that exact value.

So a spurious supervisor downgrade still recovers, and a genuine post-approval push
is routed to `re_review`, which is the round-8 suggestion 2 with suggestion 1 as the
fallback path.

## Verification

`pytest -m "not requires_live_env" .orchestrator scripts` → **564 passed, 62 subtests
passed, 10 deselected, 0 failures**. `ruff check .orchestrator scripts` → clean.

### Mutation matrix — 22 mutants at `9727f3a4`, in a `git archive` sandbox

Controls ran **before** the matrix, per round 8's harness-validation note:

| control | expected | observed |
| --- | --- | --- |
| identity (no mutation) | green | GREEN |
| negative (comment text only) | green | GREEN |
| positive (rename the new command key) | red | RED |

| id | mutated gate | verdict |
| --- | --- | --- |
| m1 | `dispatch_priority_for_task`: delete the explicit missing-head guard | **SURVIVED** (equivalent — see below) |
| m1b | `dispatch_priority_for_task`: restore the exact round-8 `if approved_head:` wrapper | KILLED |
| m2 | `dispatch_ready_tasks`: missing-head guard never fires | KILLED |
| m2c | `dispatch_ready_tasks`: exact round-8 fail-open shape | KILLED |
| m2b | `dispatch_ready_tasks`: rename the `approved_head_missing` signal | KILLED |
| m3 | `command_done`: missing head fails open | KILLED |
| m4 | `restore_approved`: drop the durable-head requirement | KILLED |
| m5 | `restore_approved`: drop the branch-moved check | KILLED |
| m5b | `restore_approved`: stop re-freezing the head | KILLED |
| m6 | `command_approve`: stop recording `last_approved_head` | KILLED |
| m7 | `restore_approved_head`: let the owner attest | KILLED |
| m8 | `restore_approved_head`: drop the head-match check | KILLED |
| m9 | `restore_approved_head`: accept the merge commit | KILLED |
| m9b | `restore_approved_head`: accept an abbreviated sha | KILLED |
| m9c | `restore_approved_head`: overwrite an existing head | KILLED |
| m10 | `task-review-gate`: success on a missing head | KILLED |
| m11 | `command_done`: accept the merge commit as the head | KILLED |
| m11b | `command_done`: stop recording head provenance | KILLED |
| m11c | `command_done`: stop recording the merge commit separately | KILLED |
| m12 | `reopen` no longer clears `approved_head` (round-8 N2 / a9) | KILLED |
| m13 | `handoff` no longer clears `approved_head` (round-8 N2 / a10) | KILLED |
| m14 | delete the `ci_status == "failure"` branch (round-8 N1 / s7) | KILLED |

**m1, stated honestly.** Replacing `if not approved_head: return None` with
`if False:` leaves the suite green, and that is not a coverage gap: with
`approved_head` unset the *next* comparison, `curr_head != approved_head`, is true for
every resolvable head, so control still returns `None`. The mutant is behaviourally
equivalent for this input. The explicit guard is there for legibility and because it
does not depend on a `!=`-against-`None` accident. m1b mutates the site back to the
**actual** round-8 shipped shape — the `if approved_head:` wrapper that produced
`priority = 1` in the reviewer's probe — and that one is killed. Round 8's N1/N2
(s7, a9, a10) are now killed by m14/m12/m13.

### Direct probes on the fixed head, with controls

Round 8's own two reproductions, re-run against `9727f3a4`:

```
=== PROBE 1: round-8 B22 repro (live ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001 shape) ===
  dispatch_priority_for_task -> None            (round 8: 1)
  command_done -> BLOCKED: ... it is review_approved but carries no reviewer-approved head
  collect_done_delivery_metadata entered: False (round 8: reached the delivery gates)
=== PROBE 1b: control -- approved_head present and matching ===
  dispatch_priority_for_task -> 1
  command_done -> status=done <-- FINALIZED
=== PROBE 2: round-8 B21 repro, approve -> reopen -> restore -> done ===
  1. after approve : review_approved approved_head=e0147acb last=e0147acb
  2. after reopen  : in_progress     approved_head=<ABSENT>  last=e0147acb  notes_kept=True
  3. restore       : BLOCKED: the branch has moved to bbbbbbbb, past the reviewer-approved head
     status still  : in_progress     (round 8: review_approved, then done at bbbbbbbb)
```

Probe 1b is the control that makes probe 1 a gate *firing* rather than a broken fixture.

### State-shape census

Round 8's lesson was that mutation testing cannot reach a branch no test enters, so
the field's states were enumerated directly rather than only mutated:

| `approved_head` shape | dispatch priority | `done` | task-review-gate |
| --- | --- | --- | --- |
| key absent | None | blocked | pending |
| key present, value `None` | None | blocked | pending |
| key present, empty string | None | blocked | pending |
| present + match | **1** | **finalized** | **success** |
| present + drift | None | blocked | pending |
| present + head unresolvable | None | blocked | no emit |

Only the one legitimate shape passes anything. The same census on the new
`last_approved_head` field: absent → `restore_approved` refuses; present + branch
moved → refuses; present + branch unmoved → re-freezes.

## Operational consequence — 3 live tasks need reviewer attestation

This is a fail-closed change, so it wedges the tasks already sitting in the shape.
`/home/lupin/oday-plus-supervisor-live/ai-status.json` at the time of writing:

| task | owner | reviewer |
| --- | --- | --- |
| ODP-DEPLOY-WEB-PROTECTED-REDIRECT-001 | Claude2 | Claude |
| ODP-ORCH-ANTIGRAVITY-CLAUDE-POOL-CANARY-001 | CodexCoordinator | Claude2 |
| ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001 | CodexCoordinator | Claude2 |

Each stays parked (with an `approved_head_missing` signal in `next`) until its
**reviewer** runs, with `AI_NAME` set to that reviewer:

```
python3 scripts/ai_status.py restore_approved_head <TASK-ID> <40-hex head sha> "<why this is the reviewed commit>"
```

The sha must be the PR `headRefOid`, not the merge commit — for
ODP-DEPLOY-STAGING-JOB-RECEIPT-UPLOAD-001 that is head `e0147acb`, not merge
`4b329493` (PR #515). This is deliberate: it is the audited migration the coordinator
asked for in requirement 4, and it is what the automatic bypass used to hide. These
three are other agents' tasks and were **not** touched from here.

## Acceptance criteria

| AC | Verdict |
| --- | --- |
| 1. Persist exact approved commit, reject owner finalize on drift | PASS (B21/B22 fixed; census + probes) |
| 2. Strict update-branch merge only via explicit re-review | PASS (unchanged; m12/m13 now also cover N2) |
| 3. No owner dispatch loops while CI pending; resume when terminal | PASS (unchanged; m14 closes N1) |
| 4. Reviewer/owner identities separate; gate only for exact current head | PASS (m7, m10) |
| 5. Deterministic regression tests for post-review mutation and CI waiting | PASS (missing-key shape now covered: m1b, m2, m3, m10) |
| 6. No Package 10 UI/API/worker/cloud changes | PASS (diff touches `.orchestrator/`, `scripts/`, `docs/evidence/` only) |
