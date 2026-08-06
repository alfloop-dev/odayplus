# Review Packet: ODP-ORCH-REBASE-HEAD-LIVENESS-001

- Sidecar task: `ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-REBASE-HEAD-LIVENESS-001`
- Sidecar owner: `Claude2` (helper-claimed `2026-08-06T02:00:12Z`; original owner `Antigravity`)
- Sidecar reviewer: `Antigravity`
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

Sidecar review handoff target: `Antigravity` (current assigned sidecar
reviewer). See § Ownership and role history for why these differ.

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

This sidecar changed hands twice on 2026-08-06. The table exists because
the closeout gate compares commit trailers against the *current* owner
and reviewer, so stale role labels are a finalization hazard, not just a
documentation nit.

| UTC | Event | Owner | Reviewer |
| --- | --- | --- | --- |
| (created) | supervisor auto-created sidecar | `Antigravity` | `Claude3` |
| `01:15:46` | `task_review_helper_claimed` — `Claude3` dispatch-paused | `Antigravity` | `Claude2` |
| `01:21:43` | `review_approved` by `Claude2` at head `2a11aad0` | `Antigravity` | `Claude2` |
| `01:49:36` | `re_review` by `Antigravity` after composing the `origin/dev` base advance at head `387a326b` | `Antigravity` | `Claude2` |
| `01:57:36` | `reopen` by `Claude2`: content approved, provenance blocked | `Antigravity` | `Claude2` |
| `02:00:12` | `task_helper_claimed` by idle `Claude2`; previous owner becomes reviewer | `Claude2` | `Antigravity` |

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

The commit that lands this section is owner-authored on top of
`387a326b`, carries a subject containing the task id, and carries
`LLM-Agent: Claude2` / `Task-ID: ODP-ORCH-REBASE-HEAD-LIVENESS-001-SIDECAR-REVIEW`
/ `Reviewer: Antigravity`. It becomes the new branch head, so the gate
reads its body directly and no first-parent fallback is involved.

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
