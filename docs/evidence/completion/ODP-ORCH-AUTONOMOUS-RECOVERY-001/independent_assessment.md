# ODP-ORCH-AUTONOMOUS-RECOVERY-001 — Independent assessment (Claude)

**Task:** ODP-ORCH-AUTONOMOUS-RECOVERY-001 (驗證並正式交付 supervisor 自動恢復修復)
**Owner:** Claude · **Reviewer:** Codex2 · **PR:** #1362 (adopted draft)

## Provenance and binding

- Original implementation: commit `43d2fe8ca8e04b35a5049802169a01a5bc958678`, authored by the
  interactive Codex session (trailers `LLM-Agent: Codex`, `Reviewer: Claude`). That commit and
  its history are preserved unchanged. This adoption added a base-advance merge and evidence,
  and — after review round 1 — one fix commit for findings F5/F6 below.
- Blob identities:

| File | At `43d2fe8c` and `31dc1a65` (adopted) | At the head that carries this document (review fix) |
|---|---|---|
| `.orchestrator/worker_workspace.py` | `596bd281f81bd331293dc1eb1ba1f41ebf446cf5` | `35d19146b111bbdef2507651e826ac50ef1dca73` |
| `.orchestrator/test_worker_failure_policy.py` | `905ae95657fc967ee3316bbfeedbc96566c2aebd` | `9ec97912ed8e1e1e54ed7e65243fbc368518834e` |
| `docs/runbooks/worker-merge-recovery.md` | `4df0a5a1dcd4c61d6dfa46275f1bb3fad239b0a6` (before the 2026-09-24 addenda) | see `git ls-tree HEAD` |

Prior CI on `43d2fe8c` (all product/orchestrator checks green) was not treated as approval; the
code was read and probed independently as recorded here, and the reviewer's findings were
reproduced on real Git state before anything was changed.

## What the code does (read-through of the diff against `origin/dev`, after the review fix)

1. `_interrupted_merge_snapshot` reads, and never mutates, the state of a merge attached to a
   branch: it requires a symbolic `HEAD` and a resolvable `MERGE_HEAD`; it refuses when
   `index.lock`, `rebase-merge`, `rebase-apply`, `sequencer`, `CHERRY_PICK_HEAD` or `REVERT_HEAD`
   exists (or when any control file is a symlink); it captures the raw `index`, `MERGE_HEAD`,
   `MERGE_MSG`, `MERGE_MODE`, `AUTO_MERGE`, `ORIG_HEAD`, **`MERGE_AUTOSTASH`** and the logical
   index (`git ls-files --stage -z`). When `MERGE_AUTOSTASH` is present it must name a commit that
   still exists (`_interrupted_merge_autostash_oid`); otherwise the snapshot is refused, because
   the parked pre-merge work is already unrecoverable and a continuation would finish the merge
   without it. Only `rev-parse`, `symbolic-ref` and `ls-files` are invoked.
2. `_interrupted_merge_fingerprint` hashes the control files (now including `MERGE_AUTOSTASH`)
   and the logical index together with the content-aware worktree fingerprint from
   `worktree_cleanliness._worktree_fingerprint`. The raw index is excluded because `git status`
   may rewrite its stat cache.
3. `_quarantine_and_preserve_dirty_worktree` previously refused any in-progress Git operation.
   An attached merge (snapshot available) proceeds; every other operation still returns
   `git_operation_in_progress`. The backup gains `git-state/` (control files, raw index,
   `index-entries`), byte copies of each dirty regular file under `files/`, and — for an autostash
   merge — `git-state/MERGE_AUTOSTASH-worktree.patch` (`git diff --binary <stash>^1 <stash>`) and
   `git-state/MERGE_AUTOSTASH-index.patch` (`<stash>^1 <stash>^2`), because the stash-like commit
   is reachable from no ref and `git gc` prunes it. Each copy is re-read and checksummed, and all of
   it is covered by `backup_checksums.sha256`.
4. `preserve_dead_worker_worktree`: when the owner seal reports `git_operation_in_progress` and a
   merge snapshot exists, an `interrupted_merge` seal (head SHA + merge fingerprint) is recorded
   through the unchanged `record_unsealed_worker_handoff`, so the rejection counter and
   `worker_reassignment.after_attempts` limit apply exactly as for `owner_dirty`.
5. `sealed_owner_continuation_allowed`: for an `interrupted_merge` record the merge state is
   re-snapshotted and compared (`merge_state_changed` on any drift, including a changed, deleted,
   dangling or symlinked `MERGE_AUTOSTASH`). For every other record the function first refuses with
   `git_operation_in_progress` when any Git operation is attached, then compares the dirty
   fingerprint as before. All pre-existing gates remain in front of it, in order: owner-execution
   reason, handoff block present, rejection limit, target equals task owner, record owner equals
   task owner, workspace path and branch, head SHA.
6. `prepare_worker_workspace`: the reuse refresh's `unresolved_git_operation` status routes
   through the sealed continuation check **only when the recorded seal reason is
   `interrupted_merge`**; for an ordinary dirty seal the refresh verdict stands and the lease is
   refused as it was before this feature. `_refresh_reused_worker_worktree` returns that status
   before any fetch or fast-forward, so the checkout is not touched. On success the request is
   tagged `worktree_continuation = sealed_owner_merge` and the prompt is prefixed with the
   INTERRUPTED MERGE RECOVERY instruction; the existing CLOSEOUT CONTINUATION prefix is still added.

## Acceptance item 3, checked point by point

| Requirement | Shipped test (`test_worker_failure_policy.py`) | Additional probe (scratch, not shipped) |
|---|---|---|
| Preserve index, metadata and files exactly | `test_interrupted_merge_fence_preserves_and_dispatches_successor` (clean `--no-commit` merge; HEAD, `MERGE_HEAD`, `ls-files --stage` unchanged; `git-state/MERGE_HEAD`, `git-state/index`, `files/README.md` present); `test_interrupted_autostash_merge_backs_up_parked_work_and_seals_the_pointer` (`git-state/MERGE_AUTOSTASH` bytes, parked content present in `MERGE_AUTOSTASH-worktree.patch` and absent from `staged.patch`/`unstaged.patch`, all three in `backup_checksums.sha256`) | Probe A: real conflicted merge; `index-entries` contains stage 1/2/3 for the conflicted path, `files/README.md` keeps the conflict markers, manifest status `UU`; logical index unchanged after preservation; owner seal accepted |
| Owner or authorised successor may resume | successor `Codex` via `_settle_fenced_sibling_worker` → `maybe_reassign_task_after_worker_failure`, which transfers the handoff block only when source run id, workspace path and branch match and writers are verified stopped; owner lease on an autostash merge tagged `sealed_owner_merge` | Probe E: same flow on a conflicted merge; `prepare_worker_workspace` succeeds with `sealed_owner_merge`, `MERGE_HEAD`, logical index and conflict markers intact |
| Reject reviewer, helper, foreign owner | `test_interrupted_merge_seal_rejects_reviewer_helper_and_foreign_owner` (`review_ready_dispatch`, `helper_claim_dispatch`, foreign owner) — rejected by `not_owner_execution` / `not_same_owner` before the fingerprint is consulted | — |
| Detect state drift | `test_interrupted_merge_seal_rejects_changed_metadata_or_index` (`MERGE_MSG` bytes; `git add` of a dirty file); autostash test: pointer changed to another commit, deleted, dangling (`0`×40), replaced by a symlink → `merge_state_changed` and lease refused each time, seal accepted again after restoring the bytes; after `git gc --prune=now` removes the parked commit the seal stays refused while the backup patch still holds the content; `test_interrupted_merge_seal_rejects_autostash_added_after_seal` | Probe B: same-path content edit that leaves porcelain status unchanged → `merge_state_changed`. Probe C: mtime touch + `git status` + `update-index --refresh` keeps the seal |
| Index lock, rebase, cherry-pick, revert stay blocked | `test_interrupted_merge_with_index_lock_remains_blocked` (`git_operation_in_progress`, no handoff block); `test_owner_dirty_seal_keeps_git_operation_started_after_seal_blocked`: on an `owner_dirty` seal a real empty `git cherry-pick`, a `REVERT_HEAD` and a `rebase-merge` directory — each verified to leave HEAD, `ls-files --stage`, porcelain and dirty bytes identical to the sealed state — make the seal answer `git_operation_in_progress` and `prepare_worker_workspace` refuse with `unresolved_git_operation` (no `worktree_continuation`, handoff block untouched, worktree untouched); clearing the operation leases again | Probe D: `REVERT_HEAD` on a merge → `git_operation_in_progress`, no handoff block. Rebase is refused by `_git_operation_in_progress` before the snapshot and again inside it |
| No automatic discard, resolution, commit, approval or skipped checks | inspection: the added Git calls are read-only (`git diff` between two commits for the autostash patches writes only into the backup directory); backup copies leave the worktree; the resumed worker receives an instruction, not an action; review and CI gates are untouched | — |

Probe run (before review round 1): 5 probe methods on top of the 24 inherited fixture tests →
29 passed, 1 skipped (the cherry-pick sub-case, see F2 and F5), 4 subtests passed, exit 0, 20.22 s.

## Findings

- **F1 — documentation discrepancy, addressed by a runbook addendum.** The runbook's
  "Validation (2026-09-23)" section says the broader selection
  `test_supervisor.py -k 'preserve or handoff or review_churn'` fails with `common.ConfigError`
  (one continuation test and three sub-cases of
  `test_return_to_review_transitions_clear_and_preserve_the_right_heads`) on both baseline and the
  branch. In the adoption environment this did not reproduce: on the merged branch `31dc1a65`
  and on an unmodified `git archive` copy of `origin/dev c4efabbb`, the selection reports
  55 passed, 6 skipped, 0 failed (74 JUnit cases incl. subtests; exit 0 both; 5.75 s and 6.98 s).
  All six skips carry the reason "dirty worktrees now block dispatch; the fresh recovery lease was
  intentionally removed", and the three tests the runbook names pass on both trees. The original
  text is kept as that session's observation; an addendum records the 2026-09-24 result.
- **F2 — observation, superseded in part by F5.** With git 2.43.0 a conflicting
  `git cherry-pick --no-commit` leaves unmerged index entries but no `CHERRY_PICK_HEAD`. That is
  still true, but the conclusion drawn from it before review ("such a worktree is classified as
  ordinary `owner_dirty`; not a defect") stopped short: a plain `git cherry-pick` that turns out
  empty *does* leave `CHERRY_PICK_HEAD` while changing nothing else, and that is exactly the
  state F5 is about. The probe skipped the cherry-pick sub-case instead of trying that form.
- **F3 — design note, no change.** `ORIG_HEAD` and `AUTO_MERGE` are part of the seal. Both are
  rewritten by the next merge, reset or rebase in that checkout, which correctly invalidates the
  continuation; a legitimate successor is leased before acting, so this does not block it
  (shipped test 1, probe E).
- **F4 — design note, no change.** `files/` copies only regular files listed by `git status`;
  symlinks are recorded by target in the manifest (untracked ones are recreated under
  `untracked/`); paths come from `git status` relative to the worktree, so no traversal.
- **F5 — defect (Codex2 R1, P2), fixed.** `prepare_worker_workspace` accepted
  `unresolved_git_operation` as a continuation candidate for *any* handoff record, and the
  ordinary `owner_dirty` branch of `sealed_owner_continuation_allowed` compared only the porcelain
  fingerprint and HEAD. A Git operation that starts after the seal without touching HEAD, the
  index or any dirty byte — a real empty cherry-pick, a `REVERT_HEAD`, a `rebase-merge`
  directory — was therefore leased as `sealed_owner_dirt`, whereas before the adopted change the
  refresh policy refused it outright. Reproduced on the adopted code with the shipped test (seal
  returned `(True, '1 dirty change (1 unstaged tracked): README.md')` in all three sub-cases).
  Fix: the `unresolved_git_operation` route requires `record.reason == "interrupted_merge"`, and
  the ordinary dirty branch refuses with `git_operation_in_progress` whenever an operation is
  attached. `_interrupted_merge_snapshot` already refuses cherry-pick/revert/rebase/lock state for
  merge seals, so both seal kinds now block them.
- **F6 — defect (Codex2 R2, P2), fixed.** `MERGE_AUTOSTASH` was not in the snapshot whitelist, so
  for `git merge --autostash` / `merge.autoStash=true` the pre-merge dirty work (parked in a
  stash-like commit named only by that file) was outside the seal and the backup: the worktree is
  clean of it, `staged.patch`/`unstaged.patch` do not contain it, and creating, changing or
  deleting the pointer after sealing left the verdict unchanged. `git gc --prune=now` deletes the
  parked commit, so the pointer alone would not have been a backup either. Reproduced on the
  adopted code with the shipped tests (`FileNotFoundError … git-state/MERGE_AUTOSTASH`;
  pointer added after seal still accepted). Fix: the pointer joins the snapshot (symlink-refused),
  a pointer that no longer names a commit refuses the snapshot, and the backup stores the parked
  content as two patches. Git behaviour reference: https://git-scm.com/docs/git-merge
  (`--autostash`).

**Defects requiring a code change: two (F5, F6), both raised by the independent reviewer,
both reproduced before the change and covered by shipped regression tests.** The earlier
statement in this file that none were found was wrong in the way F2 describes.

## Not verified here

- Live behaviour of the runtime that already runs the *adopted* patch (without F5/F6) is outside
  repository verification; `README.md` records only what the canonical activity log shows,
  without runtime paths or PIDs, and states the exposure that remains until the next rollout.
- The runbook's account-pool fallback statements describe live configuration policy, not code in
  this PR, and are not exercised by these tests.
- The two `AgyBackgroundExitRecoveryTests` cases that model an ordinary dirty seal on a
  non-repository path were extended to patch `_git_operation_in_progress` to `False` (in both
  `worker_workspace` and `supervisor`, since `_sync_supervisor_scope` rebinds on every entrypoint
  call). They test the rejection counter, not Git state; on a non-repository path the helper fails
  closed, which is intended and is what the shipped R1 test exercises on a real repository.
