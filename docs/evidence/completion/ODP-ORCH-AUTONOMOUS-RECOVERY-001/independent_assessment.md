# ODP-ORCH-AUTONOMOUS-RECOVERY-001 — Independent assessment (Claude2)

**Task:** ODP-ORCH-AUTONOMOUS-RECOVERY-001 (驗證並正式交付 supervisor 自動恢復修復)
**Owner:** Claude2 (auto-reassigned from Antigravity7 by the board event `review_churn_reassigned`
at `2026-09-24T10:14:32Z`, after four reviewer reopens; before that Claude, then Antigravity7) ·
**Reviewer:** Codex2 · **PR:** #1362 (adopted draft)

## Provenance and binding

- Original implementation: commit `43d2fe8ca8e04b35a5049802169a01a5bc958678`, authored by the
  interactive Codex session (trailers `LLM-Agent: Codex`, `Reviewer: Claude`). That commit and
  its history are preserved unchanged.
- Adoption history on the same branch, oldest first: base-advance merge `31dc1a65`; evidence
  `c7982ae7` (Claude); fix for R1/R2 `5cc59315`, fix for R3 `e0542a7a`, fix for R4 `0ff911f4`
  (Antigravity7); then this run's commit (Claude2) with the fix for R5, two further defects
  Claude2 found on its own (F10, F11) and the evidence corrections. No commit was rewritten.
- Independence of this run: Claude2 re-read the whole diff against `origin/dev`
  (`c4efabbbeba9e743fab8ee52125932137852c703`) at `0ff911f4`, reproduced R5 on real Git state
  before changing any code, and probed the remaining binding rules of the seal itself, which is
  where F10 and F11 came from. Prior green CI was not treated as approval of anything.

## What the code does (read-through of the diff against `origin/dev`, at the delivered head)

1. `_interrupted_merge_snapshot` reads, and never mutates, the state of a merge attached to a
   branch: it requires a symbolic `HEAD` and a resolvable `MERGE_HEAD`; it refuses when
   `index.lock`, `rebase-merge`, `rebase-apply`, `sequencer`, `CHERRY_PICK_HEAD` or `REVERT_HEAD`
   exists (or when any control file is a symlink); it captures the raw `index`, `MERGE_HEAD`,
   `MERGE_MSG`, `MERGE_MODE`, `AUTO_MERGE`, `ORIG_HEAD`, `MERGE_AUTOSTASH` and the logical index
   (`git ls-files --stage -z`). When `MERGE_AUTOSTASH` is present it must name a commit that
   still exists (`_interrupted_merge_autostash_oid`); otherwise the snapshot is refused. Only
   `rev-parse`, `symbolic-ref` and `ls-files` are invoked.
2. `_interrupted_merge_fingerprint` hashes the control files and the logical index together with
   `_interrupted_merge_worktree_fingerprint`. That function binds every porcelain entry by its
   code and path and then by the state Git itself records for the path: a regular file by its Git
   mode (`100644` or `100755`, derived from the owner execute bit exactly as Git does) and its
   bytes, read through the path so hardlinked inodes are covered; a symlink by its target; a
   nested repository (a submodule gitlink or an untracked checkout, recognised by a `.git`
   entry) by its `HEAD` commit and, recursively, its own dirty entries; a plain directory by type
   only, because `git status --untracked-files=all` lists the files inside it individually. It
   returns `None` when the state cannot be read exactly (`git status` failed, a nested `HEAD`
   does not resolve, or a nested `git status` failed). The raw index, mtime and ctime are
   deliberately excluded so a stat-cache refresh cannot break a legitimate continuation.
3. `_quarantine_and_preserve_dirty_worktree`: an attached merge (snapshot available) proceeds;
   every other Git operation still returns `git_operation_in_progress`. The backup gains
   `git-state/` (control files, raw index, `index-entries`), byte copies of each dirty regular
   file and re-created dirty symlinks under `files/`, and for an autostash merge the parked
   content as `git-state/MERGE_AUTOSTASH-worktree.patch` and `MERGE_AUTOSTASH-index.patch`.
   `backup_checksums.sha256` covers every copied file by content and every symlink by target,
   including a symlink that resolves to a directory inside the backup.
4. `preserve_dead_worker_worktree`: when the owner seal reports `git_operation_in_progress` and a
   merge snapshot exists, the merge fingerprint is computed; only when it is not `None` is an
   `interrupted_merge` seal (head SHA + merge fingerprint) recorded through the unchanged
   `record_unsealed_worker_handoff`, so the rejection counter and
   `worker_reassignment.after_attempts` limit apply exactly as for `owner_dirty`. When the state
   cannot be read, the plain refusal stands and no handoff block is written.
5. `sealed_owner_continuation_allowed`: for an `interrupted_merge` record the merge state is
   re-snapshotted and re-fingerprinted; a failed `git status`, a missing snapshot, a `None`
   fingerprint or any difference answers `merge_state_changed`. For every other record the
   function first refuses with `git_operation_in_progress` when any Git operation is attached,
   then compares the ordinary dirty fingerprint as before. All pre-existing gates remain in front
   of it, in order: owner-execution reason, handoff block present, rejection limit, target equals
   task owner, record owner equals task owner, workspace path and branch, head SHA.
6. `prepare_worker_workspace`: the reuse refresh's `unresolved_git_operation` status routes
   through the sealed continuation check only when the recorded seal reason is
   `interrupted_merge`; for an ordinary dirty seal the refresh verdict stands and the lease is
   refused as before this feature. On success the request is tagged
   `worktree_continuation = sealed_owner_merge` and the prompt is prefixed with the INTERRUPTED
   MERGE RECOVERY instruction; the existing CLOSEOUT CONTINUATION prefix is still added.

## Acceptance item 3, checked point by point

| Requirement | Shipped test (`test_worker_failure_policy.py`, class `QuotaSiblingFencingDirtyHandoffTests`) |
|---|---|
| Preserve index, metadata and files exactly | `test_interrupted_merge_fence_preserves_and_dispatches_successor` (clean `--no-commit` merge; HEAD, `MERGE_HEAD`, `ls-files --stage` unchanged; `git-state/MERGE_HEAD`, `git-state/index`, `files/README.md` present); `test_interrupted_autostash_merge_backs_up_parked_work_and_seals_the_pointer` (pointer bytes, parked content in `MERGE_AUTOSTASH-worktree.patch`, all in `backup_checksums.sha256`); `test_interrupted_merge_backup_checksums_directory_symlink_by_target` (dirty symlink re-created under `files/`, checksummed by target even when it resolves to a directory inside the backup; every checksum entry names an existing copy) |
| Owner or authorised successor may resume | successor `Codex` via `_settle_fenced_sibling_worker` → `maybe_reassign_task_after_worker_failure`, which transfers the handoff block only when source run id, workspace path and branch match and writers are verified stopped; owner lease tagged `sealed_owner_merge` in every drift test after the state is restored |
| Reject reviewer, helper, foreign owner | `test_interrupted_merge_seal_rejects_reviewer_helper_and_foreign_owner` (`review_ready_dispatch`, `helper_claim_dispatch`, foreign owner) — rejected by `not_owner_execution` / `not_same_owner` before the fingerprint is consulted |
| Detect state drift | `test_interrupted_merge_seal_rejects_changed_metadata_or_index` (`MERGE_MSG` bytes; `git add` of a dirty file); the autostash test (pointer changed, deleted, dangling, symlinked; pruned commit); `test_interrupted_merge_seal_rejects_autostash_added_after_seal`; `test_interrupted_merge_seal_rejects_symlink_target_drift`; `test_interrupted_merge_seal_rejects_hardlink_byte_drift`; `test_interrupted_merge_seal_rejects_empty_porcelain_merge_status_failure_and_dirty_drift`; `test_interrupted_merge_seal_rejects_executable_mode_drift` (chmod +x on an already-dirty file, directly and through a hardlink; a mtime touch plus `git status` keeps the seal); `test_interrupted_merge_seal_binds_nested_repository_head_and_dirt` (submodule moved to another commit; file edited inside the submodule; superproject porcelain, HEAD, logical index and merge metadata identical in both); `test_interrupted_merge_seal_fails_closed_when_nested_repository_becomes_unreadable` (nested `HEAD` made unborn after the seal; superproject status still succeeds; lease refused, handoff block untouched, accepted again after restoring) |
| Index lock, rebase, cherry-pick, revert stay blocked | `test_interrupted_merge_with_index_lock_remains_blocked`; `test_owner_dirty_seal_keeps_git_operation_started_after_seal_blocked` (empty cherry-pick, `REVERT_HEAD`, `rebase-merge` on an `owner_dirty` seal, each leaving HEAD, index, porcelain and bytes identical) |
| No automatic discard, resolution, commit, approval or skipped checks | inspection: the added Git calls are read-only (`git diff` between two commits for the autostash patches writes only into the backup directory; `rev-parse` and `status` inside a nested repository read only); backup copies leave the worktree alone; the resumed worker receives an instruction, not an action; a state that cannot be read yields no seal (`test_interrupted_merge_seal_refuses_when_nested_repository_is_unreadable_at_seal_time`, `test_interrupted_merge_seal_refuses_when_status_fails_at_seal_time`); review and CI gates are untouched |

## Findings

- **F1 — documentation discrepancy, addressed by a runbook addendum (round 1).** The runbook's
  "Validation (2026-09-23)" section says the broader selection
  `test_supervisor.py -k 'preserve or handoff or review_churn'` fails with `common.ConfigError`
  on both baseline and the branch. In the adoption environment this did not reproduce: on the
  merged branch `31dc1a65` and on an unmodified `git archive` copy of `origin/dev c4efabbb`,
  the selection reported 55 passed, 6 skipped, 0 failed (exit 0 both). The original text is kept
  as that session's observation; the addendum records the 2026-09-24 result.
- **F2 — observation, superseded in part by F5.** With git 2.43.0 a conflicting
  `git cherry-pick --no-commit` leaves unmerged index entries but no `CHERRY_PICK_HEAD`; a plain
  `git cherry-pick` that turns out empty does leave `CHERRY_PICK_HEAD` while changing nothing
  else, which is the state F5 is about.
- **F3 — design note, no change.** `ORIG_HEAD` and `AUTO_MERGE` are part of the seal. Both are
  rewritten by the next merge, reset or rebase in that checkout, which correctly invalidates the
  continuation; a legitimate successor is leased before acting.
- **F4 — design note, updated.** `files/` holds byte copies of dirty regular files and re-created
  dirty symlinks (by target); the manifest also records each symlink target. After F11 every
  symlink under the backup is checksummed by target, whether it resolves to a file, to a
  directory inside the backup, or to nothing. Paths come from `git status` relative to the
  worktree, so no traversal.
- **F5 — defect (Codex2 R1, P2), fixed in round 1.** `prepare_worker_workspace` accepted
  `unresolved_git_operation` as a continuation candidate for any handoff record, and the ordinary
  `owner_dirty` branch compared only the porcelain fingerprint and HEAD, so a Git operation
  started after the seal that touched nothing else was leased. Fix: the route requires
  `record.reason == "interrupted_merge"`, and the ordinary dirty branch refuses with
  `git_operation_in_progress` whenever an operation is attached.
- **F6 — defect (Codex2 R2, P2), fixed in round 1.** `MERGE_AUTOSTASH` was outside the snapshot
  whitelist, so the parked pre-merge work was outside the seal and the backup. Fix: the pointer
  joins the snapshot (symlink-refused), a dangling pointer refuses the snapshot, and the backup
  stores the parked content as two patches.
- **F7 — defect (Codex2 R3, P2), fixed in round 2.** The worktree part of the seal relied on
  `worktree_cleanliness._worktree_fingerprint`, which hashes symlinks and hardlinks as the
  constant `unsafe-path`, so their drift after the seal was invisible. Fix: the dedicated
  `_interrupted_merge_worktree_fingerprint` reads symlink targets and file bytes directly.
- **F8 — defect (Codex2 R4, P2), fixed in round 3.** That fingerprint hashed a failed
  `git status` (`entries=()`) exactly like a clean empty listing. Fix: `inspection.kind` is part
  of the digest, and a failed status yields no usable fingerprint; seal creation and continuation
  fail closed on it.
- **F9 — defect (Codex2 R5, P2), fixed in this run.** The regular-file branch of the fingerprint
  bound type and bytes but not the Git executable mode. `chmod +x` on an already-dirty file leaves
  the porcelain code, HEAD, logical index, merge metadata and bytes untouched while Git records
  `mode change 100644 => 100755`, so the seal accepted it. Reproduced by Claude2 at `0ff911f4`
  with real Git before changing code (probe in the worker scratch directory, not shipped): after
  the seal, `git diff --summary` reported the mode change, the merge fingerprint was unchanged,
  the direct seal answered `(True, 'Resume the preserved interrupted merge …')`,
  `prepare_worker_workspace` leased with `worktree_continuation = sealed_owner_merge`. Fix: a
  regular file is bound as `regular:100755` or `regular:100644` from `st_mode & S_IXUSR`, the same
  rule Git applies; mtime, ctime and the raw index stay excluded. The same probe on the fixed tree
  answers `(False, 'merge_state_changed')`, refuses the lease without continuation metadata, and
  accepts again once the mode is restored. Shipped regression:
  `test_interrupted_merge_seal_rejects_executable_mode_drift`.
- **F10 — defect (Claude2's own finding), fixed in this run.** A directory entry of the porcelain
  listing was bound as the constant `dir`. A submodule gitlink that is already dirty (` M sub`)
  keeps that same code when its `HEAD` moves to another commit or when a file inside it is
  edited, and an untracked nested checkout (`?? nested/`) keeps its code whatever happens inside;
  neither changed the seal, so both were leased as the exact sealed state. Fix: a directory that
  carries a `.git` entry is bound by its `HEAD` commit and, recursively, by its own dirty
  entries; when that state cannot be read (`HEAD` unresolvable, nested status failed) the whole
  fingerprint is `None`, which means no `interrupted_merge` seal at worker death and
  `merge_state_changed` at continuation. This repository has no `.gitmodules` and no gitlink on
  `origin/dev c4efabbb`, so the gap was latent rather than a live exposure; it is closed because
  the acceptance asks for exact-state drift rejection, and a rule that cannot see a whole class of
  Git-tracked state does not satisfy it. Shipped regressions:
  `test_interrupted_merge_seal_binds_nested_repository_head_and_dirt`,
  `test_interrupted_merge_seal_fails_closed_when_nested_repository_becomes_unreadable`,
  `test_interrupted_merge_seal_refuses_when_nested_repository_is_unreadable_at_seal_time`.
- **F11 — defect (Codex2 non-blocking note in round 3, confirmed and fixed in this run).** The
  checksum walk over the backup iterated `os.walk` file names only. A preserved symlink that
  resolves to a directory inside the backup is listed under the directory names, is never
  descended, and so had no entry in `backup_checksums.sha256`, contradicting the runbook's
  "all symlinks checksummed" statement. Fix: the walk also records every symlink found among the
  directory names by target. Shipped regression:
  `test_interrupted_merge_backup_checksums_directory_symlink_by_target` (a tracked dirty file
  under `docs/` puts `files/docs/` into the backup, so the re-created `files/docs_link` resolves to
  a directory there; the test also asserts that every checksum entry names an existing copy).
- **F12 — observation, not changed (outside owned paths).** The ordinary `owner_dirty` seal
  still uses `worktree_cleanliness._worktree_fingerprint`, which binds neither the executable
  mode nor symlink targets; Codex2 noted in R5 that this gap is baseline behaviour that
  `origin/dev` already has for non-merge continuations. `worktree_cleanliness.py` is not in this
  task's owned paths, so it is left unchanged and recorded here for a follow-up task.

**Defects requiring a code change: seven (F5–F11).** Five were raised by the independent
reviewer, two (F10, F11) by this run's own inspection; all were reproduced on real Git state
before the change and are covered by shipped regression tests.

## Owner measurements for this run (Claude2's own validation, not receipt-bearing)

| Run | Tree | Result |
|---|---|---|
| R5 probe (scratch script driving the `QuotaSiblingFencingDirtyHandoffTests` fixture: seal, `chmod +x` on dirty `README.md`, re-check, restore) | `0ff911f4` (the reviewed head, before any change) | defect reproduced: porcelain, HEAD, `ls-files --stage`, `MERGE_HEAD` and bytes identical; `git diff --summary` = `mode change 100644 => 100755 README.md`; fingerprint unchanged; direct seal accepted; lease granted with `sealed_owner_merge`; exit 0 (the script's exit reports that it ran, its JSON reports `defect_present: true`); 0.87 s |
| Same probe | fixed worktree | `defect_present: false`; fingerprint changed; `(False, 'merge_state_changed')`; lease refused; accepted again after restoring the mode |
| A/B: the five new tests against the reviewed code | `git archive` copy of `0ff911f4` (its `.orchestrator/worker_workspace.py` is blob `c944cf14`; a direct import from the copy resolved to the copy's file) with only the final `test_worker_failure_policy.py` copied in; selection `-k "nested_repository or executable_mode or directory_symlink_by_target"` | exit 1, 6.2 s; all five tests fail, seven failure records (three tests, four subtests): four times the seal answered `(True, 'Resume the preserved interrupted merge …')` where `(False, 'merge_state_changed')` was expected (mode drift direct and via hardlink; nested HEAD moved; nested file edited); the fingerprint of the unreadable nested checkout was a hex digest instead of `None`; an `interrupted_merge` handoff block was recorded although the nested state was unreadable; `KeyError: 'files/docs_link'` in the checksums |
| Selection `-k "interrupted_merge or autostash or git_operation_started_after_seal"` | fixed worktree | exit 0, 16 tests |
| `ruff check` on both Python files; `git diff --check` | fixed worktree | exit 0 |

The declared verification (the three commands on the task board) is executed once at the final
head through `delivery_toolchain/git/task_verification.py run` immediately before
`task_finalize.sh`; the receipts bind the head SHA, command, exit code, duration and selection,
and the outcomes are posted to the task board. No suite was re-run for a count.

## Not verified here

- Live behaviour of the runtime, which runs the adopted patch `43d2fe8c` (none of F5–F11), is
  outside repository verification; `README.md` records only what the canonical activity log
  shows and the exposure that remains until the next controlled rollout.
- The runbook's account-pool fallback statements describe live configuration policy, not code in
  this PR, and are not exercised by these tests.
- The two `AgyBackgroundExitRecoveryTests` cases that model an ordinary dirty seal on a
  non-repository path patch `_git_operation_in_progress` to `False` (in both `worker_workspace`
  and `supervisor`, since `_sync_supervisor_scope` rebinds on every entrypoint call). They test
  the rejection counter, not Git state.
