# ODP-ORCH-REBASE-HEAD-LIVENESS-001 acceptance packet

- Status: support-only review packet
- Parent task: `ODP-ORCH-REBASE-HEAD-LIVENESS-001`
- Parent owner: Codex3
- Sidecar owner: Codex7
- Assigned sidecar reviewer: Antigravity3
- Snapshot reviewed: `origin/task/ODP-ORCH-REBASE-HEAD-LIVENESS-001` at
  `0b3e9d9d32e2c8dfceb72b5696dedaefb909e243`
- Snapshot base: `475f6d5e9b36f097a1eb4ab3dbe4bd8b1b1d7c2f`

## Scope and authority

This packet is advisory acceptance evidence for the parent owner. It does not
change canonical truth, runtime behavior, registry data, governance policy, or
the parent implementation. The parent owner decides whether to absorb its
recommendations and remains responsible for the parent task's final review and
merge.

The reviewed parent snapshot changes only:

- `.orchestrator/supervisor.py`
- `.orchestrator/test_supervisor.py`

## Intended contract

A leftover `REBASE_HEAD` file by itself is not authoritative evidence that a
rebase is still active. A reusable worker worktree must remain dispatchable
when that is the only operation marker. Active or unverifiable Git operations
must continue to block without resetting, cleaning, rebasing, or otherwise
discarding worker state.

The reviewed implementation narrows `_git_operation_in_progress()` accordingly:

- `REBASE_HEAD` is removed from the direct ref-marker block list.
- `rebase-merge` and `rebase-apply` remain authoritative active-rebase markers.
- `MERGE_HEAD`, `CHERRY_PICK_HEAD`, and `REVERT_HEAD` remain blocking markers.
- Failure to resolve either authoritative rebase marker path remains
  fail-closed.

## Acceptance matrix

| Condition | Required result | Evidence at reviewed snapshot |
| --- | --- | --- |
| Stale `REBASE_HEAD` exists; no active rebase directory exists | Do not classify the worktree as having an unresolved Git operation | New `test_stale_rebase_head_after_completed_rebase_does_not_block` passes and reaches `base_advance_rebase_required:*` |
| `rebase-merge` exists | Block refresh as `unresolved_git_operation` | `test_unresolved_rebase_blocks` passes its `rebase-merge` subtest |
| `rebase-apply` exists | Block refresh as `unresolved_git_operation` | `test_unresolved_rebase_blocks` passes its `rebase-apply` subtest |
| `MERGE_HEAD` resolves | Block refresh as `unresolved_git_operation` | Existing `test_unresolved_git_operation_blocks` passes |
| `CHERRY_PICK_HEAD` or `REVERT_HEAD` resolves | Block refresh as `unresolved_git_operation` | Preserved direct-marker branch; dedicated tests are not present |
| Git cannot resolve an authoritative rebase marker path | Treat the operation state as unresolved | Preserved `returncode != 0 or not raw_path` fail-closed branch |
| Worktree is dirty, on the wrong branch, from the wrong repository, or has unverifiable/mismatched refs | Preserve the pre-existing fail-closed behavior | Full `ReusedWorkerWorktreeBaseAdvanceTests` class passes (13 tests) |

## Dependency and control-flow map

```text
Git metadata in reused worker worktree
  |-- direct refs: MERGE_HEAD / CHERRY_PICK_HEAD / REVERT_HEAD
  |-- active rebase directories: rebase-merge / rebase-apply
  `-- non-authoritative residue: REBASE_HEAD
                         |
                         v
          _git_operation_in_progress()
                         |
             true ------+------ false
              |                   |
              v                   v
  unresolved_git_operation   remaining refresh guards
  (dispatch blocked)          (identity, cleanliness, fetch,
                               branch and ref ancestry checks)
                                      |
                                      v
                         safe refresh/dispatch outcome
```

The slice depends only on Git's per-worktree metadata resolution and the
existing `_refresh_reused_worker_worktree()` guard sequence. It introduces no
new storage, service, status-schema, routing, deployment, or external package
dependency.

## Verification evidence

Verification was run against an isolated archive of the exact parent commit,
not against the sidecar branch working tree:

```text
python3 -m pytest -q \
  <archive>/.orchestrator/test_supervisor.py::ReusedWorkerWorktreeBaseAdvanceTests

Result: 13 passed

ruff check \
  <archive>/.orchestrator/supervisor.py \
  <archive>/.orchestrator/test_supervisor.py

Result: All checks passed

git diff --check \
  origin/dev...0b3e9d9d32e2c8dfceb72b5696dedaefb909e243

Result: clean
```

The reviewed diff is 35 insertions and 5 deletions across the two parent files.
No canonical document or status truth is part of that diff.

At the evidence snapshot, PR #577 targets `dev` from the exact reviewed head.
Its `orchestrator` and `performance-gate` checks are successful,
`product-e2e-gate` is failing, `product` is still in progress, and
`task-review-gate` is pending. Those PR-level gates remain parent-task closeout
inputs; this sidecar packet does not approve or override them.

## Reviewer checklist

- [ ] Confirm the parent review still targets exact commit `0b3e9d9d` or a
  descendant whose relevant diff is equivalent.
- [ ] Confirm stale `REBASE_HEAD` alone produces a non-blocking result.
- [ ] Confirm active `rebase-merge` and `rebase-apply` states remain blocking.
- [ ] Confirm merge, cherry-pick, and revert operation markers remain blocking.
- [ ] Confirm all unverifiable metadata paths remain fail-closed.
- [ ] Confirm the refresh path does not delete `REBASE_HEAD` or mutate owner
  work while deciding liveness.
- [ ] Confirm parent changes remain limited to the supervisor implementation
  and its tests.

## Residual risk and recommendation

The behavior change itself has direct regression coverage, both active rebase
backends are exercised, stale-marker preservation is asserted, and the full
worktree-refresh test class passes. The retained `CHERRY_PICK_HEAD` and
`REVERT_HEAD` branches are verified by inspection rather than dedicated cases
in this snapshot. This is a non-blocking coverage gap for the narrow fix,
because those paths were not modified. The parent owner may add table-driven
coverage for all retained direct markers if stronger future regression
protection is desired.

Acceptance recommendation: the exact reviewed snapshot satisfies the narrow
stale-versus-active rebase behavior contract. Parent acceptance remains subject
to Antigravity3's assigned review and all normal PR/CI gates.
