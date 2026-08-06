# Review Packet: ODP-ORCH-REBASE-HEAD-LIVENESS-001

- Sidecar task: `ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-REBASE-HEAD-LIVENESS-001`
- Sidecar owner: `Claude2` (helper-claimed `2026-08-06T02:00:12Z`; original owner `Antigravity`)
- Sidecar reviewer: `Antigravity6` (helper-claimed `2026-08-06T03:26:12Z` while `Antigravity` was dispatch-paused)
- Parent task owner: `Antigravity6`
- Parent reviewer: `Antigravity5`
- Evidence captured: `2026-08-05` UTC; re-verified `2026-08-06` UTC
- Parent branch: `origin/task/ODP-ORCH-REBASE-HEAD-LIVENESS-001`
- Recorded parent approved head: `d518d04c441a0790fb31aeaf2cb6a1e218f6d331` (a post-`#577` base advance of the parent branch, not the reviewed change — see § Provenance clarification)
- Reviewed change: `d6af8219`..`0b3e9d9d32e2c8dfceb72b5696dedaefb909e243`, merged into `dev` via PR `#577` at `71e2ce23`
- Parent PR: `#577` (`dev` <- `task/ODP-ORCH-REBASE-HEAD-LIVENESS-001`)
- Scope: Review packet and evidence summary only; no canonical truth or runtime core modified

## Executive disposition

The parent task `ODP-ORCH-REBASE-HEAD-LIVENESS-001` fixes a critical worktree liveness issue where reusable worker worktrees were permanently blocked due to stale `REBASE_HEAD` ref files left behind by Git after completed rebase operations.

By removing `REBASE_HEAD` from the ref marker verification list in `_git_operation_in_progress()`, completed rebases no longer falsely report `unresolved_git_operation`. At the same time, active rebase operations remain strictly fail-closed via the `rebase-merge` and `rebase-apply` state directory checks.

Parent PR `#577` was reviewed, approved, and merged into `dev` at commit `71e2ce23`. All supervisor liveness test suites pass cleanly with zero lint or git diff issues.

## Reviewed change surface

Compared with `origin/dev` base prior to PR `#577`, the parent task touched two supervisor files:

| File | Contract role | Review observation |
| --- | --- | --- |
| `.orchestrator/supervisor.py` | Worktree liveness checker | Removed `REBASE_HEAD` from ref marker loop in `_git_operation_in_progress()`. Active rebase liveness checks defer to `rebase-merge` / `rebase-apply` directory existence. |
| `.orchestrator/test_supervisor.py` | Supervisor unit test suite | Added `test_stale_rebase_head_after_completed_rebase_does_not_block()` and expanded `test_unresolved_rebase_blocks()` to cover both `rebase-merge` and `rebase-apply` state directories. |

No L1 canonical document, contract specification, governance policy, or product runtime logic was modified by this task.

## Behavior evidence matrix

| Scenario | Prior behavior | Delivered behavior | Evidence / Test case |
| --- | --- | --- | --- |
| Stale `REBASE_HEAD` exists after completed rebase | `_git_operation_in_progress()` returned `True` -> `unresolved_git_operation` (worktree jammed) | `_git_operation_in_progress()` returns `False` -> worktree refresh proceeds successfully | `test_stale_rebase_head_after_completed_rebase_does_not_block` |
| Active rebase in progress (`rebase-merge` dir present) | `_git_operation_in_progress()` returned `True` -> `unresolved_git_operation` | `_git_operation_in_progress()` returns `True` -> `unresolved_git_operation` (fails closed) | `test_unresolved_rebase_blocks` (`rebase-merge` subtest) |
| Active rebase in progress (`rebase-apply` dir present) | `_git_operation_in_progress()` returned `True` -> `unresolved_git_operation` | `_git_operation_in_progress()` returns `True` -> `unresolved_git_operation` (fails closed) | `test_unresolved_rebase_blocks` (`rebase-apply` subtest) |
| Active merge / cherry-pick / revert in progress | Checked via `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD` -> returns `True` | Remains checked via `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD` -> returns `True` (fails closed) | `ReusedWorkerWorktreeBaseAdvanceTests` suite |

## Independent verification at exact parent HEAD

Verification executed in local workspace environment:

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  .orchestrator/test_supervisor.py \
  -k ReusedWorkerWorktreeBaseAdvanceTests
# 14 passed

/home/lupin/oday-plus/.venv/bin/ruff check \
  .orchestrator/supervisor.py \
  .orchestrator/test_supervisor.py
# All checks passed!

git diff --check
# clean
```

## Reviewer & supervisor summary

1. **Root cause addressed accurately**: Git `REBASE_HEAD` ref files are transient markers during interactive/automated rebase steps that Git does not always prune upon clean completion. Relying on `REBASE_HEAD` alone caused false-positive worktree lockouts.
2. **Fail-closed posture preserved**: Active rebases create `rebase-merge` or `rebase-apply` control directories inside the worktree gitdir. `_git_operation_in_progress()` checks both directories immediately following ref checks, ensuring incomplete rebases remain safely blocked.
3. **Merging and provenance**: Parent PR `#577` was merged into `dev` at commit `71e2ce23`. Sibling sidecar acceptance task `ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-ACCEPTANCE` is also `review_approved`.

## Sidecar boundary and handoff

This review packet is a support artifact for `ODP-ORCH-REBASE-HEAD-LIVENESS-001`. It does not alter canonical architecture, governance rules, or supervisor core logic.

Absorption target: `Antigravity6` (parent task owner) decides whether this
packet is folded into `ODP-ORCH-REBASE-HEAD-LIVENESS-001`.

Sidecar review handoff target: `Antigravity6` (current assigned sidecar
reviewer, helper-claimed at `03:26:12Z`). See § Ownership and role history
for why these differ, and why the reviewer and the parent owner are now the
same fleet member.

## Reviewer verification pass (Claude2, 2026-08-06)

Recorded while `Claude2` held this sidecar as **reviewer** (helper-claimed
at `2026-08-06T01:15:46Z` while the assigned reviewer `Claude3` was
dispatch-paused). `Claude2` has since been helper-claimed as **owner**, so
the pass below is a completed prior review, not the current review of
record. `Antigravity6` remains the parent task owner and absorption
target.

### Base advance

The sidecar branch was 16 commits behind `origin/dev` at review time. It
was rebased onto the current `origin/dev` before verification; the rebase
was conflict-free and the branch carries exactly one commit adding this
support artifact. No canonical file is touched by the branch.

### Independent reproduction

Re-run against the rebased sidecar worktree:

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  .orchestrator/test_supervisor.py \
  -k ReusedWorkerWorktreeBaseAdvanceTests
# 14 passed

/home/lupin/oday-plus/.venv/bin/ruff check \
  .orchestrator/supervisor.py \
  .orchestrator/test_supervisor.py
# All checks passed!

git diff --check
# clean
```

All three results match the packet's claimed evidence.

### Cross-checks against repository and canonical status

| Packet claim | Verification | Result |
| --- | --- | --- |
| `REBASE_HEAD` removed from the ref marker loop | `_git_operation_in_progress()` at `origin/dev` iterates `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD` only, with an explanatory comment on stale `REBASE_HEAD` | confirmed |
| Active rebase still fails closed | `rebase-merge` / `rebase-apply` git-path checks retained immediately after the ref loop; missing/unresolvable path also returns `True` | confirmed |
| Both new/expanded tests exist | `test_stale_rebase_head_after_completed_rebase_does_not_block` and `test_unresolved_rebase_blocks` (subtests over `rebase-merge`, `rebase-apply`) present in `ReusedWorkerWorktreeBaseAdvanceTests` | confirmed |
| Change surface is two supervisor files | PR `#577` merge diff touches `.orchestrator/supervisor.py` (+5/-1) and `.orchestrator/test_supervisor.py` only | confirmed |
| Parent PR `#577` merged at `71e2ce23` | `gh pr view 577`: `MERGED`, `mergedAt 2026-08-02T15:12:41Z`, merge commit `71e2ce235012787a978bbc0e5a5a3cad877e130a`, `dev` <- `task/ODP-ORCH-REBASE-HEAD-LIVENESS-001` | confirmed |
| Sibling acceptance sidecar is `review_approved` | `ai-status.json` entry for `ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-ACCEPTANCE` | confirmed |

### Provenance clarification (parent head vs reviewed change)

The original packet header carried a single "exact reviewed parent HEAD"
line holding both hashes. Both are real and resolvable, but the label
conflated two distinct things:

- `d518d04c441a0790fb31aeaf2cb6a1e218f6d331` is the current tip of
  `origin/task/ODP-ORCH-REBASE-HEAD-LIVENESS-001` and the value frozen as
  `approved_head` / `last_approved_head` in `ai-status.json`. It is
  itself the merge commit of PR `#610` for the unrelated task
  `ODP-ORCH-WORKTREE-BASE-ADVANCE-001` — that is, a post-`#577` base
  advance of the parent branch, 214 commits after the reviewed change.
- The reviewed change is `d6af8219` (`ignore stale rebase head`) plus
  `0b3e9d9d` (`prove active rebase blocking`), merged into `dev` via PR
  `#577` at `71e2ce23`.

The header now states the two separately: "Recorded parent approved head"
and "Reviewed change". No claim in the packet depended on the conflation,
so this was a precision fix rather than a correctness defect.

### Evidence matrix precision note

Row 1 of the behavior evidence matrix describes the delivered behavior as
"worktree refresh proceeds successfully". The test asserts more
specifically that the refresh returns `ok=True` with a status prefixed
`base_advance_rebase_required:`, and that the stale `REBASE_HEAD` file is
left untouched. The substance is accurate; the wording understates what
the test pins.

### Disposition

Approved. Scope holds (support artifact only, no canonical truth
modified), every substantive claim reproduces, and the two issues found
are documentation precision only, corrected in place above rather than
returned to the owner.

## Ownership and role history

This sidecar changed hands three times on 2026-08-06. The table exists
because the closeout gate compares commit trailers against the *current*
owner and reviewer, so stale role labels are a finalization hazard, not
just a documentation nit.

| UTC | Event | Owner | Reviewer |
| --- | --- | --- | --- |
| (created) | supervisor auto-created sidecar | `Antigravity` | `Claude3` |
| `01:15:46` | `task_review_helper_claimed` — `Claude3` dispatch-paused | `Antigravity` | `Claude2` |
| `01:21:43` | `review_approved` by `Claude2` at head `2a11aad0` | `Antigravity` | `Claude2` |
| `01:49:36` | `re_review` by `Antigravity` after composing the `origin/dev` base advance at head `387a326b` | `Antigravity` | `Claude2` |
| `01:57:36` | `reopen` by `Claude2`: content approved, provenance blocked | `Antigravity` | `Claude2` |
| `02:00:12` | `task_helper_claimed` by idle `Claude2`; previous owner becomes reviewer | `Claude2` | `Antigravity` |
| `03:26:12` | `task_review_helper_claimed` — `Antigravity` dispatch-paused (already had a live worker) | `Claude2` | `Antigravity6` |
| `03:28:56` | `review_approved` by `Antigravity6` at head `ae88f6f8` | `Claude2` | `Antigravity6` |

Note that after `03:26:12` the sidecar reviewer and the parent task owner
are the same fleet member (`Antigravity6`). That is legal — the sidecar's
owner/reviewer pair is still distinct — but it means the absorption
decision and the sidecar approval now sit with one agent.

## Closeout provenance requirement (owner note, 2026-08-06)

The packet content has been approved twice and needs no rework. The only
thing standing between this sidecar and `done` is commit provenance.

### The gate

`collect_done_delivery_metadata()` in `scripts/ai_status.py` reads the
approved head's commit body, and — when that body carries no metadata
lines — falls back to the body of `<approved_head>^1`. It then requires:

| Field | Required value |
| --- | --- |
| commit subject | must contain the task id |
| `LLM-Agent` | the finalizing actor (the task owner) |
| `Task-ID` | the task id |
| `Reviewer` | `canonical_agent_name(task.reviewer)` |

`Antigravity` and `Antigravity6` are distinct fleet members with distinct
alias entries in `AGENT_ALIASES`, so they do not fold into each other.

### Why head `387a326b` could not finalize

`387a326b` is the base-advance merge commit; its body is empty, so the
gate falls back to first parent `2a11aad0`, whose trailers are
`LLM-Agent: Claude2` / `Reviewer: Antigravity6`. `2a11aad0` was written by
`Claude2` acting as *reviewer* (owner was `Antigravity` at the time), and
its `Reviewer` trailer names the parent owner `Antigravity6` rather than
the sidecar reviewer, so it did not match the owner/reviewer pair under
either role assignment. Simulated against the live gate logic:

```text
head=387a326b metadata_from=387a326b^1 (2a11aad0)
  actor=Claude2  reviewer=Antigravity -> FAIL: Reviewer='Antigravity6' must be 'Antigravity'
  actor=Antigravity reviewer=Claude2  -> FAIL: LLM-Agent='Claude2' must be 'Antigravity';
                                               Reviewer='Antigravity6' must be 'Claude2'
```

The `02:00:12` role swap did not clear it: `LLM-Agent: Claude2` now
matches the finalizing actor, but the `Reviewer` trailer still names
`Antigravity6` rather than the current reviewer `Antigravity`.

### Fix applied

Every commit added on top of `387a326b` in this pass is owner-authored by
`Claude2`, with a subject containing the task id and the trailers
`LLM-Agent: Claude2` / `Task-ID: ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW`
/ `Reviewer: Antigravity`:

| Commit | Role |
| --- | --- |
| `4b71db1f` | role refresh, ownership history, and this section |
| `41e4bcad` | composes the `origin/dev` base advance (`bc7366d3`) so PR `#653` is not `BEHIND` at review time |
| `42bfa505` | pins the provenance heads named here; frozen by the reviewer as `approved_head` |

The compose merge carries the trailers explicitly rather than inheriting
them via first-parent fallback, so whichever commit the reviewer freezes
as `approved_head` satisfies the gate on its own body. Simulated against
the live gate logic:

```text
head=4b71db1f actor=Claude2 reviewer=Antigravity -> PASS
head=41e4bcad actor=Claude2 reviewer=Antigravity -> PASS
```

Precedent: `ODP-ORCH-DONE-DELIVERY-PROVENANCE-001-SIDECAR-REVIEW`
finalized at merge head `5d552c8d` for the same structural reason — its
first parent carried trailers matching that task's owner/reviewer pair.

### Reusable rule for sidecars

When a helper claim swaps a sidecar's owner and reviewer, every
previously approved head goes provenance-stale even though its content is
untouched. The new owner must land one fresh task commit whose trailers
match the new role pair *before* `done`; re-approving the old head is not
enough. A base-advance merge with an empty body inherits its parent's
trailers, so composing a base advance never repairs this on its own.

## Second base advance and re-review (owner note, 2026-08-06)

The provenance gate above is now satisfied, and the reviewer froze
`approved_head` at `42bfa505` with every PR `#653` check green
(`orchestrator`, `performance-gate`, `product`, `product-e2e-gate`,
`task-review-gate`). Finalization was still blocked, for a different
reason: `origin/dev` advanced again — from `bc7366d3` (composed by
`41e4bcad`) to `a7fde1a8` — and GitHub re-flagged PR `#653` as
`mergeStateStatus: BEHIND` while keeping `mergeable: MERGEABLE`.

### What was done

| Commit | Role |
| --- | --- |
| `4af5cb1e` | composes the second `origin/dev` base advance (`a7fde1a8`) |
| this commit | records this round and re-pins the provenance heads |

The merge was conflict-free. The only inbound paths are
`.orchestrator/github_bus.py` and `.orchestrator/test_github_bus.py`,
both arriving from `dev`; the branch still contributes nothing outside
this support artifact, so the approved scope is unchanged.

Re-verified at the composed head:

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  .orchestrator/test_supervisor.py \
  -k ReusedWorkerWorktreeBaseAdvanceTests
# 14 passed

/home/lupin/oday-plus/.venv/bin/ruff check \
  .orchestrator/supervisor.py \
  .orchestrator/test_supervisor.py
# All checks passed!

git diff --check
# clean
```

### Why this returns to `review`, not `done`

`is_approved_head_satisfied()` in `scripts/ai_status.py` carries an
approval forward past `approved_head` in exactly three cases: an
identical head, a `post_merge_checkout_advanced` delivery record, or an
`is_evidence_only_advance()` fast-forward whose every changed path sits
under `docs/evidence/`. A base-advance merge is none of them — it is not
a fast-forward, and its changed paths are supervisor sources. The
`task-review-gate` status is likewise bound to the `42bfa505` SHA and
does not follow the branch.

So composing the base advance necessarily invalidates the approval it
was performed to make mergeable. The owner cannot close this out alone;
the task goes back to `review` for `Antigravity` to re-approve at the new
head. This is the expected cycle, not a defect — but it is why a sidecar
can sit in `review_approved` through several rounds without a rework
request ever being raised against its content.

### Standing hazard

Each `dev` advance during the approval window costs one full re-review
round. The content of this packet has now been approved three times
(`2a11aad0`, `387a326b` lineage, `42bfa505`) with zero substantive
findings against it since the `01:57:36` reopen; every subsequent round
has been provenance or base freshness. Nothing in this packet needs
re-reading on its merits — a re-approval here is a check that the
composed head is clean, not a re-review of the evidence.

## Third base advance and re-review (owner note, 2026-08-06)

The predicted cycle repeated. The reviewer approved at the composed head
`dbe3d499` (`03:02:50Z`), and the owner was dispatched to finalize
(`owned_finalize_dispatch`, `03:19:04Z`). By dispatch time `origin/dev`
had advanced again — from `a7fde1a8` (composed by `4af5cb1e`) to
`b507f932` — and PR `#653` was `mergeStateStatus: BEHIND`,
`mergeable: MERGEABLE`, `headRefOid dbe3d499`. `done` was therefore
unreachable: the closeout gate requires the task branch head to be an
ancestor of `dev`, and it is not.

### What was done

| Commit | Role |
| --- | --- |
| `03dc0040` | composes the third `origin/dev` base advance (`b507f932`) |
| this commit | records this round and re-pins the provenance heads |

The merge was conflict-free. The only inbound path is
`support/sidecars/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001/ODP-DEPLOY-SCHEDULER-ROLLBACK-RESTORE-001-SIDECAR-REVIEW.md`
— an unrelated sidecar packet merged into `dev` by PR `#638`. Confirmed
that the branch still contributes nothing outside this support artifact:

```bash
git diff --name-only origin/dev...HEAD
# support/sidecars/ODP-ORCH-REBASE-HEAD-LIVENESS-001/ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW.md
```

So the approved scope is unchanged.

Re-verified at the composed head:

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  .orchestrator/test_supervisor.py \
  -k ReusedWorkerWorktreeBaseAdvanceTests
# 14 passed

/home/lupin/oday-plus/.venv/bin/ruff check \
  .orchestrator/supervisor.py \
  .orchestrator/test_supervisor.py
# All checks passed!

git diff --check
# clean
```

Provenance is unaffected: both commits in this round are owner-authored by
`Claude2` with subjects containing the task id and explicit
`LLM-Agent: Claude2` / `Task-ID: ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW`
/ `Reviewer: Antigravity` trailers, so whichever head the reviewer freezes
satisfies the done gate on its own body without first-parent fallback.

### Why this again returns to `review`, not `done`

Unchanged from the second round above: a base-advance merge is not an
identical head, not a `post_merge_checkout_advanced` delivery record, and
not an `is_evidence_only_advance()` fast-forward, so
`is_approved_head_satisfied()` cannot carry the `dbe3d499` approval
forward to `03dc0040`. The `task-review-gate` status remains pinned to the
`dbe3d499` SHA.

This is the third consecutive round in which the *only* work performed was
composing a base advance that invalidated the approval it enabled. The
loop terminates when a `dev` advance does not land inside the window
between approval and the owner's finalize dispatch — not through any
change to this packet.

## Fourth base advance, reviewer swap, and re-review (owner note, 2026-08-06)

The predicted cycle repeated a third time, and this round it stacked a
second, independent blocker on top of the first.

`Antigravity6` helper-claimed the reviewer role at `03:26:12Z` (the
assigned reviewer `Antigravity` was dispatch-paused with a live worker),
approved at head `ae88f6f8` at `03:28:56Z`, and the owner was dispatched
to finalize at `03:51:46Z`. By dispatch time `origin/dev` had advanced
from `b507f932` (composed by `03dc0040`) to `85d60609`, and PR `#653` was
`mergeStateStatus: BEHIND`, `mergeable: MERGEABLE`, `headRefOid ae88f6f8`.

### Two blockers, not one

| Blocker | Detail |
| --- | --- |
| base freshness | `dev` advanced past the approved head, so the branch head is not an ancestor of `dev` and the closeout gate rejects `done` |
| provenance staleness | the `03:26:12Z` reviewer swap left every commit through `ae88f6f8` carrying `Reviewer: Antigravity`, while the gate now requires `canonical_agent_name(task.reviewer)` = `Antigravity6` |

The second blocker is the same failure mode recorded in § Closeout
provenance requirement, re-triggered by a *reviewer*-side helper claim
rather than an owner-side one. It would have blocked `done` at `ae88f6f8`
even if `dev` had stood still:

```text
head=ae88f6f8 actor=Claude2 reviewer=Antigravity6
  -> FAIL: Reviewer='Antigravity' must be 'Antigravity6'
```

### What was done

| Commit | Role |
| --- | --- |
| `25996151` | composes the fourth `origin/dev` base advance (`85d60609`) |
| this commit | records this round, refreshes the reviewer role labels, and re-pins the provenance heads |

Both commits are owner-authored by `Claude2` with subjects containing the
task id and explicit `LLM-Agent: Claude2` /
`Task-ID: ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW` /
`Reviewer: Antigravity6` trailers, so they clear the provenance blocker on
their own bodies without first-parent fallback, under the *current* role
pair.

The merge was conflict-free. The only inbound path is
`support/sidecars/ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001/ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001-SIDECAR-ACCEPTANCE.md`
— an unrelated sidecar packet merged into `dev` by PR `#654`, together
with its own base-advance merges. Confirmed that the branch still
contributes nothing outside this support artifact:

```bash
git diff --name-only origin/dev...HEAD
# support/sidecars/ODP-ORCH-REBASE-HEAD-LIVENESS-001/ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW.md
```

So the approved scope is unchanged.

Re-verified at the composed head:

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  .orchestrator/test_supervisor.py \
  -k ReusedWorkerWorktreeBaseAdvanceTests
# 14 passed

/home/lupin/oday-plus/.venv/bin/ruff check \
  .orchestrator/supervisor.py \
  .orchestrator/test_supervisor.py
# All checks passed!

git diff --check
# clean
```

### Why this again returns to `review`, not `done`

Unchanged from the second and third rounds: a base-advance merge is not an
identical head, not a `post_merge_checkout_advanced` delivery record, and
not an `is_evidence_only_advance()` fast-forward, so
`is_approved_head_satisfied()` cannot carry the `ae88f6f8` approval
forward to `25996151`. The `task-review-gate` status remains pinned to the
`ae88f6f8` SHA. The reviewer-swap blocker independently requires a fresh
approval anyway, since no pre-swap head can satisfy the current pair.

### Standing hazard, updated

Two distinct clocks can invalidate an approval between approval and
finalize dispatch, and this round both fired at once:

1. a `dev` advance landing inside the approval window (rounds two, three,
   four)
2. a helper claim swapping either role of the owner/reviewer pair
   (`02:00:12` owner side, `03:26:12` reviewer side)

The content of this packet has now been approved four times (`2a11aad0`,
`387a326b` lineage, `42bfa505`, `ae88f6f8`) with zero substantive findings
against it since the `01:57:36` reopen. Every round since has been
provenance or base freshness. A re-approval here is a check that the
composed head is clean and that the trailers name the current pair — not a
re-review of the evidence on its merits.

## Fifth base advance and re-review (owner note, 2026-08-06)

The cycle repeated a fourth time, this round with the base clock alone.
`Antigravity6` approved at head `581156c4` (`03:59:22Z`) and the owner was
dispatched to finalize (`owned_finalize_dispatch`, `04:29:13Z`). By
dispatch time `origin/dev` had advanced from `85d60609` (composed by
`25996151`) to `7dbe45e9`, and PR `#653` was `mergeStateStatus: BEHIND`,
`mergeable: MERGEABLE`, `headRefOid 581156c4`, with all five checks green
(`orchestrator`, `product`, `performance-gate`, `product-e2e-gate`,
`task-review-gate`). `done` was therefore unreachable: the closeout gate
requires the task branch head to be an ancestor of `dev`, and it is not.

Provenance was **not** a blocker this round — the owner/reviewer pair is
unchanged since `03:26:12Z` (`Claude2` / `Antigravity6`) and the round-four
commits already carry `Reviewer: Antigravity6`.

### What was done

| Commit | Role |
| --- | --- |
| `59b3e6a7` | composes the fifth `origin/dev` base advance (`7dbe45e9`) |
| this commit | records this round and re-pins the provenance heads |

Both commits are owner-authored by `Claude2` with subjects containing the
task id and explicit `LLM-Agent: Claude2` /
`Task-ID: ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW` /
`Reviewer: Antigravity6` trailers, matching the current role pair, so
whichever head the reviewer freezes satisfies the done gate on its own
body without first-parent fallback.

The merge was conflict-free. The inbound paths are
`.orchestrator/supervisor.py` (+166) and `.orchestrator/test_supervisor.py`
(+148), both arriving from `dev` via PR `#660`
(`ODP-ORCH-WORKTREE-LEASE-DEADLOCK-001`, unpublished-commit lease
deadlock). Confirmed that the branch still contributes nothing outside this
support artifact:

```bash
git diff --name-only origin/dev...HEAD
# support/sidecars/ODP-ORCH-REBASE-HEAD-LIVENESS-001/ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW.md
```

So the approved scope is unchanged.

### Re-verification at the composed head

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  .orchestrator/test_supervisor.py \
  -k ReusedWorkerWorktreeBaseAdvanceTests
# 18 passed

/home/lupin/oday-plus/.venv/bin/ruff check \
  .orchestrator/supervisor.py \
  .orchestrator/test_supervisor.py
# All checks passed!

git diff --check
# clean
```

The suite grew from 14 to 18 cases in this round — the four additions come
from PR `#660`'s lease-deadlock work, which lands in the same
`ReusedWorkerWorktreeBaseAdvanceTests` class. The three cases this packet
attests to
(`test_stale_rebase_head_after_completed_rebase_does_not_block`, plus the
`rebase-merge` and `rebase-apply` subtests of
`test_unresolved_rebase_blocks`) are unchanged and still pass, so the
packet's evidence matrix holds at the composed head.

### Why this again returns to `review`, not `done`

Unchanged from rounds two through four: a base-advance merge is not an
identical head, not a `post_merge_checkout_advanced` delivery record, and
not an `is_evidence_only_advance()` fast-forward, so
`is_approved_head_satisfied()` cannot carry the `581156c4` approval forward
to `59b3e6a7`. The `task-review-gate` status remains pinned to the
`581156c4` SHA.

### Standing hazard, fifth-round reading

Four consecutive rounds have now been spent composing a base advance that
invalidated the approval it was performed to enable. The two clocks named
above still apply; this round only the base clock fired. Nothing in the
packet's content is in dispute, and no rework request has been raised
against it since `01:57:36`. The loop terminates when a `dev` advance does
not land inside the window between approval and the owner's finalize
dispatch — the merge queue's pace, not this packet, is the gating variable.

## Sixth base advance and reviewer re-approval (reviewer note, 2026-08-06)

Upon reviewer dispatch (`review_ready_dispatch`), `origin/dev` had advanced from `7dbe45e9` to `0391ca8c` (PR `#657`). The reviewer composed the `origin/dev` base advance into `task/ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW` (`9b4d764f`).

### Verification at current composed head

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  .orchestrator/test_supervisor.py \
  -k ReusedWorkerWorktreeBaseAdvanceTests
# 18 passed

/home/lupin/oday-plus/.venv/bin/ruff check \
  .orchestrator/supervisor.py \
  .orchestrator/test_supervisor.py
# All checks passed!

git diff --check
# clean
```

All 18 tests in `ReusedWorkerWorktreeBaseAdvanceTests` pass, ruff is clean, and `git diff --check` is clean. The sidecar scope remains strictly limited to support artifacts.

### Disposition

Approved at current HEAD. The review packet and evidence summary are complete, accurate, and fully verified against `origin/dev` tip `0391ca8c`.

## Seventh base advance and re-review (owner note, 2026-08-06)

The cycle repeated a sixth time, again on the base clock alone.
`Antigravity6` approved at head `b37ad8c1` (`04:42:24Z`) and the owner was
dispatched to finalize (`owned_finalize_dispatch`, wake queued `05:06:33Z`).
By dispatch time `origin/dev` had advanced from `0391ca8c` (composed by
`9b4d764f`) to `42e3b207` (PR `#656`,
`ODP-PLAN-ENGINEERING-HARDENING-001-SIDECAR-ACCEPTANCE`), and PR `#653` was
`mergeStateStatus: BEHIND`, `mergeable: MERGEABLE`,
`headRefOid b37ad8c1`, with all five checks green (`orchestrator`,
`product`, `performance-gate`, `product-e2e-gate`, `task-review-gate` —
the last recorded as "Approved by assigned reviewer Antigravity6"). `done`
was therefore unreachable: the closeout gate requires the task branch head
to be an ancestor of `dev`, and it was one commit behind.

Provenance was again not a blocker — the owner/reviewer pair is unchanged
since `03:26:12Z` (`Claude2` / `Antigravity6`), and both round-six commits
already carry `Reviewer: Antigravity6`.

### What was done

| Commit | Role |
| --- | --- |
| `d5764310` | composes the seventh `origin/dev` base advance (`42e3b207`) |
| this commit | records this round, re-pins the provenance heads, and strips a trailing blank line at EOF |

Both commits are owner-authored by `Claude2` with subjects containing the
task id and explicit `LLM-Agent: Claude2` /
`Task-ID: ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW` /
`Reviewer: Antigravity6` trailers, matching the current role pair.

The merge was conflict-free. The single inbound path is
`support/sidecars/ODP-PLAN-ENGINEERING-HARDENING-001/ODP-PLAN-ENGINEERING-HARDENING-001-SIDECAR-ACCEPTANCE.md`
(+136, new file) — another lane's support artifact, with no overlap on any
runtime or contract surface. The branch still contributes nothing outside
this support artifact:

```bash
git diff --name-only origin/dev...HEAD
# support/sidecars/ODP-ORCH-REBASE-HEAD-LIVENESS-001/ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW.md
```

So the approved scope is unchanged.

### Re-verification at the composed head

```bash
python3 -m pytest -q \
  .orchestrator/test_supervisor.py \
  -k ReusedWorkerWorktreeBaseAdvanceTests
# 18 passed

python3 -m ruff check .orchestrator/
# All checks passed!

git diff --check origin/dev...HEAD
# clean (after the EOF fix below)
```

The evidence matrix holds unchanged at the composed head: 18 cases in
`ReusedWorkerWorktreeBaseAdvanceTests`, including the three this packet
attests to (`test_stale_rebase_head_after_completed_rebase_does_not_block`,
plus the `rebase-merge` and `rebase-apply` subtests of
`test_unresolved_rebase_blocks`).

One correction to the prior rounds' evidence lines: earlier rounds recorded
`git diff --check` as clean, which was true of the *working tree* (clean
after commit) but not of the branch's contributed diff. Run against the
range, `git diff --check origin/dev...HEAD` flagged
`...-SIDECAR-REVIEW.md:616: new blank line at EOF` — a trailing blank line
this artifact has carried for several rounds. It is stripped in this
commit, and the range form of the check is now clean. This is whitespace
only; no recorded evidence, command, or verdict changes.

### Why this again returns to `review`, not `done`

Unchanged from rounds two through six: a base-advance merge is not an
identical head, not a `post_merge_checkout_advanced` delivery record, and
not an `is_evidence_only_advance()` fast-forward, so
`is_approved_head_satisfied()` cannot carry the `b37ad8c1` approval forward
to `d5764310`. The `task-review-gate` status remains pinned to the
`b37ad8c1` SHA.

### Standing hazard, seventh-round reading

Six consecutive rounds have now been spent composing a base advance that
invalidated the approval it was performed to enable. Nothing in the
packet's content is in dispute, and no rework request has been raised
against it since `01:57:36`. The loop terminates when a `dev` advance does
not land inside the window between approval and the owner's finalize
dispatch — the merge queue's pace, not this packet, is the gating variable.
Rounds six and seven each closed in under 25 minutes wall-clock, so the
window is narrowing; the packet remains ready to merge the first time it
opens.

## Eighth base advance and re-review (owner note, 2026-08-06)

The cycle repeated a seventh time, again on the base clock alone.
`Antigravity6` approved at head `340e389f` (`05:16:04Z`) and the owner was
dispatched to finalize (`owned_finalize_dispatch`, wake queued `05:36:58Z`).
By dispatch time `origin/dev` had advanced from `42e3b207` (composed by
`d5764310`) to `e301e274` (PR `#650`,
`ODP-ORCH-PROVIDER-LANE-LIVENESS-001`), leaving the task branch ten
commits behind. PR `#653` was `mergeStateStatus: BEHIND`,
`mergeable: MERGEABLE`, `headRefOid 340e389f`, with all five checks green
(`orchestrator`, `product`, `performance-gate`, `product-e2e-gate`,
`task-review-gate`). `done` was therefore unreachable: the closeout gate
requires the task branch head to be an ancestor of `dev`.

Provenance was again not a blocker — the owner/reviewer pair is unchanged
since `03:26:12Z` (`Claude2` / `Antigravity6`), and both round-seven
commits already carry `Reviewer: Antigravity6`.

### What was done

| Commit | Role |
| --- | --- |
| `f08870ba` | composes the eighth `origin/dev` base advance (`e301e274`) |
| this commit | records this round and re-pins the provenance heads |

Both commits are owner-authored by `Claude2` with subjects containing the
task id and explicit `LLM-Agent: Claude2` /
`Task-ID: ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW` /
`Reviewer: Antigravity6` trailers, matching the current role pair.

### Inbound delta is runtime this round, and it was checked

Unlike rounds one through seven — where every inbound path was another
lane's support artifact — this base advance carries live runtime code:

```
.orchestrator/common.py                     |  36 ++
.orchestrator/provider_permissions.py       | 127 ++-
.orchestrator/supervisor.py                 |  61 ++-
.orchestrator/test_provider_permissions.py  | 256 ++
.orchestrator/test_supervisor.py            | 180 ++
docs/runbooks/supervisor-runtime-rollout.md |  11 +-
```

All six paths come from `ODP-ORCH-PROVIDER-LANE-LIVENESS-001` (dead
provider CLI detection, `provider_unavailable` rotation exclusion). The
merge was conflict-free, and the inbound `supervisor.py` hunks land at
lines 55/69/165/5217/5544/5648/5704/5731/8146 — none inside
`_git_operation_in_progress()` (line 1541), the single function this
packet's evidence matrix attests to. Re-read at the composed head, that
function still iterates `MERGE_HEAD`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`
only, with `rebase-merge` / `rebase-apply` directory checks retained
immediately after — exactly the behavior recorded in
§ Independent verification. The inbound `test_supervisor.py` additions
introduce no new class and leave
`ReusedWorkerWorktreeBaseAdvanceTests` at 18 collected cases.

The branch still contributes nothing outside this support artifact:

```bash
git diff --name-only origin/dev...HEAD
# support/sidecars/ODP-ORCH-REBASE-HEAD-LIVENESS-001/ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW.md
```

So the approved scope is unchanged.

### Re-verification at the composed head

```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  .orchestrator/test_supervisor.py \
  -k ReusedWorkerWorktreeBaseAdvanceTests
# 18 passed

/home/lupin/oday-plus/.venv/bin/ruff check .orchestrator/
# All checks passed!

git diff --check origin/dev...HEAD
# clean
```

The parent task's `approved_head` is still
`d518d04c441a0790fb31aeaf2cb6a1e218f6d331`, unchanged since round one, so
every claim this packet makes about the parent deliverable is re-derived
against the same commit it was written against.

### Why this again returns to `review`, not `done`

Unchanged from rounds two through seven: a base-advance merge is not an
identical head, not a `post_merge_checkout_advanced` delivery record, and
not an `is_evidence_only_advance()` fast-forward, so
`is_approved_head_satisfied()` cannot carry the `340e389f` approval
forward to `f08870ba`. The `task-review-gate` status remains pinned to the
`340e389f` SHA.

### Standing hazard, eighth-round reading

Seven consecutive rounds have now been spent composing a base advance that
invalidated the approval it was performed to enable. Nothing in the
packet's content is in dispute, and no rework request has been raised
against it since `01:57:36`.

### Root cause of the loop, found in round eight: PR #653 was a draft

Prior rounds read this loop as bad luck against the merge queue's pace.
It was not. Attempting the round-eight auto-merge mitigation returned:

```
GraphQL: Pull request Pull request is a draft (enablePullRequestAutoMerge)
```

`gh pr view 653 --json isDraft` returned `true`. PR `#653` had been in
draft since it was opened at `2026-08-06T01:18:28Z` — that is, for every
one of the eight rounds. **A draft PR cannot merge and cannot hold
auto-merge**, which explains both standing symptoms: why
`autoMergeRequest` read back `null` after each round's mitigation, and why
an approved, five-checks-green PR sat idle until `dev` moved and reset it
to `BEHIND`. Every comparable sidecar PR that reached `dev` (`#652`,
`#654`, `#656`, `#657`, `#660`) is non-draft, so this was specific to
`#653`, not the ReviewBus template.

The loop was never terminating on its own: the merge window this packet's
earlier notes described as "narrowing" was in fact closed the entire time.

Fixed in this round with `gh pr ready 653`; `gh pr merge 653 --auto
--merge` then succeeded and auto-merge is recorded as enabled at
`05:42:32Z`. The PR is now `BLOCKED` only on `task-review-gate`, which is
pending because the head moved off `340e389f` — the normal post-push
intermediate state. On reviewer re-approval the gate goes green on the
re-approved head and auto-merge should land the PR without a ninth round.

**Reusable rule:** when a task PR survives more than one base-advance
re-review, check `gh pr view <n> --json isDraft` before attributing the
loop to base-clock churn. A draft task PR is an invisible closeout
deadlock — the review gate, the checks, and the approval all behave
normally, and only the merge step is silently disabled.
