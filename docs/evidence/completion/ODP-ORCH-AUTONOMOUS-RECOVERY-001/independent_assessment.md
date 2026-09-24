# ODP-ORCH-AUTONOMOUS-RECOVERY-001 — Independent assessment (Claude)

**Task:** ODP-ORCH-AUTONOMOUS-RECOVERY-001 (驗證並正式交付 supervisor 自動恢復修復)
**Owner:** Claude · **Reviewer:** Codex2 · **PR:** #1362 (adopted draft)

## Provenance and binding

- Original implementation: commit `43d2fe8ca8e04b35a5049802169a01a5bc958678`, authored by the
  interactive Codex session (trailers `LLM-Agent: Codex`, `Reviewer: Claude`). That commit and
  its history are preserved unchanged; this adoption adds a base-advance merge and evidence only.
- This assessment is bound to the exact blobs below. They are identical at `43d2fe8c`, after the
  base-advance merge `31dc1a65` (parents `43d2fe8c` + `origin/dev c4efabbb`), and at the head that
  carries this document:

| File | Git blob |
|---|---|
| `.orchestrator/worker_workspace.py` | `596bd281f81bd331293dc1eb1ba1f41ebf446cf5` |
| `.orchestrator/test_worker_failure_policy.py` | `905ae95657fc967ee3316bbfeedbc96566c2aebd` |
| `docs/runbooks/worker-merge-recovery.md` (before the 2026-09-24 addendum) | `4df0a5a1dcd4c61d6dfa46275f1bb3fad239b0a6` |

Prior CI on `43d2fe8c` (all product/orchestrator checks green) was not treated as approval; the
code was read and probed independently as recorded here.

## What the patch does (read-through of the diff against `d87bc0bf`)

1. `_interrupted_merge_snapshot` reads, and never mutates, the state of a merge attached to a
   branch: it requires a symbolic `HEAD` and a resolvable `MERGE_HEAD`; it refuses when
   `index.lock`, `rebase-merge`, `rebase-apply`, `sequencer`, `CHERRY_PICK_HEAD` or `REVERT_HEAD`
   exists (or when any control file is a symlink); it captures the raw `index`, `MERGE_HEAD`,
   `MERGE_MSG`, `MERGE_MODE`, `AUTO_MERGE`, `ORIG_HEAD` and the logical index
   (`git ls-files --stage -z`). Only `rev-parse`, `symbolic-ref` and `ls-files` are invoked.
2. `_interrupted_merge_fingerprint` hashes the control files and the logical index (the raw index
   is deliberately excluded because `git status` may rewrite its stat cache) together with the
   content-aware worktree fingerprint from `worktree_cleanliness._worktree_fingerprint`, which
   hashes porcelain output plus the bytes of every dirty regular file.
3. `_quarantine_and_preserve_dirty_worktree` previously refused any in-progress Git operation.
   Now an attached merge (snapshot available) proceeds; every other operation still returns
   `git_operation_in_progress`. The backup gains `git-state/` (control files, raw index,
   `index-entries`) and byte copies of each dirty regular file under `files/`, because patches
   cannot reproduce unmerged index entries. Each copy is re-read and checksummed, and all of it is
   covered by `backup_checksums.sha256`.
4. `preserve_dead_worker_worktree`: when the owner seal reports `git_operation_in_progress` and a
   merge snapshot exists, an `interrupted_merge` seal (head SHA + merge fingerprint) is recorded
   through the unchanged `record_unsealed_worker_handoff`, so the rejection counter and
   `worker_reassignment.after_attempts` limit apply exactly as for `owner_dirty`.
5. `sealed_owner_continuation_allowed`: for an `interrupted_merge` record the merge state is
   re-snapshotted and compared (`merge_state_changed` on any drift). All pre-existing gates remain
   in front of it, in order: owner-execution reason, handoff block present, rejection limit,
   target equals task owner, record owner equals task owner, workspace path and branch, head SHA.
6. `prepare_worker_workspace`: the reuse refresh's `unresolved_git_operation` status now also
   routes through the sealed continuation check (before, only `skipped_dirty_worktree` did).
   `_refresh_reused_worker_worktree` returns that status before any fetch or fast-forward, so the
   checkout is not touched. On success the request is tagged `worktree_continuation =
   sealed_owner_merge` and the prompt is prefixed with the INTERRUPTED MERGE RECOVERY instruction;
   the existing CLOSEOUT CONTINUATION prefix is still added.

## Acceptance item 3, checked point by point

| Requirement | Shipped test (`test_worker_failure_policy.py`) | Additional probe (scratch, not shipped) |
|---|---|---|
| Preserve index, metadata and files exactly | `test_interrupted_merge_fence_preserves_and_dispatches_successor` (clean `--no-commit` merge; HEAD, `MERGE_HEAD`, `ls-files --stage` unchanged; `git-state/MERGE_HEAD`, `git-state/index`, `files/README.md` present) | Probe A: real conflicted merge; `index-entries` contains stage 1/2/3 for the conflicted path, `files/README.md` keeps the conflict markers, manifest status `UU`; logical index unchanged after preservation; owner seal accepted |
| Owner or authorised successor may resume | same test: successor `Codex` via `_settle_fenced_sibling_worker` → `maybe_reassign_task_after_worker_failure`, which transfers the handoff block only when source run id, workspace path and branch match and writers are verified stopped | Probe E: same flow on a conflicted merge; `prepare_worker_workspace` succeeds with `sealed_owner_merge`, `MERGE_HEAD`, logical index and conflict markers intact |
| Reject reviewer, helper, foreign owner | `test_interrupted_merge_seal_rejects_reviewer_helper_and_foreign_owner` (`review_ready_dispatch`, `helper_claim_dispatch`, foreign owner) — rejected by `not_owner_execution` / `not_same_owner` before the fingerprint is consulted | — |
| Detect state drift | `test_interrupted_merge_seal_rejects_changed_metadata_or_index` (`MERGE_MSG` bytes; `git add` of a dirty file) | Probe B: same-path content edit that leaves porcelain status unchanged → `merge_state_changed` (content-aware fingerprint). Probe C: mtime touch + `git status` + `update-index --refresh` keeps the seal |
| Index lock, rebase, cherry-pick, revert stay blocked | `test_interrupted_merge_with_index_lock_remains_blocked` (`git_operation_in_progress`, no handoff block) | Probe D: `REVERT_HEAD` present → `git_operation_in_progress`, no handoff block. Rebase is refused by `_git_operation_in_progress` (`rebase-merge` / `rebase-apply`) before the snapshot and again inside it |
| No automatic discard, resolution, commit, approval or skipped checks | inspection: the added Git calls are read-only; backup copies leave the worktree; the resumed worker receives an instruction, not an action; review and CI gates are untouched | — |

Probe run: 5 probe methods on top of the 24 inherited fixture tests → 29 passed, 1 skipped
(the cherry-pick sub-case, see F2), 4 subtests passed, exit 0, 20.22 s reported by pytest.

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
- **F2 — observation outside this patch, no change.** With git 2.43.0 a conflicting
  `git cherry-pick --no-commit` leaves unmerged index entries but no `CHERRY_PICK_HEAD`
  (a plain `git cherry-pick` does). Such a worktree has no `MERGE_HEAD`, so the new merge path
  cannot be entered; it is classified as ordinary `owner_dirty` by the pre-existing
  `_git_operation_in_progress`. The runbook's "cherry-pick remains blocked" holds for the
  sequencer-driven form. Not a defect introduced here.
- **F3 — design note, no change.** `ORIG_HEAD` and `AUTO_MERGE` are part of the seal. Both are
  rewritten by the next merge, reset or rebase in that checkout, which correctly invalidates the
  continuation; a legitimate successor is leased before acting, so this does not block it
  (shipped test 1, probe E).
- **F4 — design note, no change.** `files/` copies only regular files listed by `git status`;
  symlinks are recorded by target in the manifest (untracked ones are recreated under
  `untracked/`); paths come from `git status` relative to the worktree, so no traversal.

**Defects requiring a code change: none found.** The three deliverable blobs are shipped exactly
as authored at `43d2fe8c`.

## Not verified here

- Live behaviour of the runtime that already runs this patch is outside repository verification;
  `README.md` records only what the canonical activity log shows, without runtime paths or PIDs.
- The runbook's account-pool fallback statements describe live configuration policy, not code in
  this PR, and are not exercised by these tests.
