# ODP-ORCH-AUTONOMOUS-RECOVERY-001 — Completion evidence

**Task:** ODP-ORCH-AUTONOMOUS-RECOVERY-001 (驗證並正式交付 supervisor 自動恢復修復)
**Owner:** Antigravity7 (reassigned from Claude after review round 2) · **Reviewer:** Codex2 · **PR:** #1362 · **Class:** remediation (tracked adoption of an already-running orchestrator fix)

## Purpose

Bring the interrupted-merge recovery fix (quota-fenced worker exits during a Git merge; the
supervisor preserves the exact merge state and lets only the sealed owner or an authorised
successor resume it) through owner validation, independent review and the normal merge path.
No product deployment is included.

## Provenance and adoption

- Implementation commit `43d2fe8ca8e04b35a5049802169a01a5bc958678` was authored by the
  interactive Codex session (`LLM-Agent: Codex`, `Reviewer: Claude`) and opened as draft PR #1362.
  Its commit history, author and trailers are preserved unchanged; nothing was rewritten.
- Claude inspected and probed the change independently; see `independent_assessment.md`.
  The prior green CI on `43d2fe8c` was not used as a substitute for that review, and no reviewer approval is claimed here.
- Independent review round 1 (Codex2, board event `reopen` at `2026-09-24T07:57:35Z`) returned
  two P2 findings against the adopted code (R1, R2). Both were reproduced and fixed.
- Independent review round 2 (Codex2, board event `reopen` at `2026-09-24T08:45:02Z`) returned
  one P2 finding (R3) regarding symlink target and hardlink byte drift. Reassigned to Antigravity7
  at `2026-09-24T08:48:40Z`, reproduced and fixed in `worker_workspace.py` with shipped regression tests.
- The existing clean task worktree discovered by the supervisor was reused. No other task
  worktree, no live supervisor and no worker was touched.

## Base advance (required by the dispatch)

| Item | Value |
|---|---|
| Merge commit | `31dc1a65e3d813d9174852925d6e95feb237cdf7` |
| Parents | `43d2fe8ca8e04b35a5049802169a01a5bc958678` (task) + `c4efabbbeba9e743fab8ee52125932137852c703` (`origin/dev`) |
| Resulting tree | `1f74062138f6d2dbb23db4cb8c63c39feadd37a9` = tree predicted by `git merge-tree --write-tree` before merging |
| Conflicts | none; `origin/dev` added no change under `.orchestrator/` or to the deliverable files |
| History | plain merge, no rebase, no reset; the previously pushed tip `43d2fe8c` remains an ancestor |

At `43d2fe8c` and at `31dc1a65` the two Python deliverable blobs are byte-identical
(`596bd281…`, `905ae956…`). The review-fix commits that follow change both; details in `independent_assessment.md`.

## Review rounds (Codex2) and fixes

| Finding | What Codex2 found | Reproduced as | Fix |
|---|---|---|---|
| R1 (P2) | `prepare_worker_workspace` routed `unresolved_git_operation` into `sealed_owner_continuation_allowed` without requiring an `interrupted_merge` seal. An ordinary `owner_dirty` seal only binds porcelain status, dirty bytes and HEAD, so a cherry-pick, revert or rebase started after the seal that leaves all three unchanged was leased as a success. | Real empty `git cherry-pick` on a sealed dirty checkout: `CHERRY_PICK_HEAD` present, HEAD / `ls-files --stage` / porcelain / dirty bytes unchanged; on the adopted code the seal returned `(True, "1 dirty change …")` and the lease succeeded. | `unresolved_git_operation` is a continuation candidate only when the recorded seal reason is `interrupted_merge`; the ordinary dirty path additionally refuses with `git_operation_in_progress` whenever any Git operation is attached. |
| R2 (P2) | The merge-state snapshot whitelist omitted `MERGE_AUTOSTASH`. With `git merge --autostash` / `merge.autoStash=true` the pre-merge dirty work lives only in the stash-like commit that file names: not in the worktree, not in any patch, not in the seal, not in the backup. | Real `git merge --no-commit --autostash dev` with a dirty tracked file: the file is clean afterwards, `MERGE_AUTOSTASH` names a commit, the backup had no `git-state/MERGE_AUTOSTASH`, and adding / changing / deleting the pointer after sealing did not change the seal verdict. `git gc --prune=now` deletes that commit. | `MERGE_AUTOSTASH` joins the snapshot (symlink-refused like the other control files), hence the seal and the `git-state/` backup. The snapshot fails closed when the pointer no longer names a commit. The backup additionally stores the parked content as `MERGE_AUTOSTASH-worktree.patch` and `MERGE_AUTOSTASH-index.patch` so it does not depend on the object store. |
| R3 (P2) | `_interrupted_merge_fingerprint` relied on `inspection.fingerprint` from `worktree_cleanliness._worktree_fingerprint`, which delegates path validation to `is_safe_context_destination`. Because that helper rejects symlinks and hardlinks (`st_nlink != 1`), their content was hashed as static `unsafe-path`. A dirty symlink target change or hardlink content edit after seal did not change the seal fingerprint, allowing drift past continuation. | Dirty tracked symlink target changed from `task.py` to `README.md`, or hardlinked `README.md` content edited after seal: porcelain, HEAD, logical index and merge snapshot unchanged, seal accepted and lease granted. | Added `_interrupted_merge_worktree_fingerprint` in `worker_workspace.py` that directly reads symlink targets (`os.readlink`) and file bytes (including hardlinks with `nlink > 1`), and preserves dirty symlinks under `files/` during backup. |
| non-blocking | Initial README claimed three deliverables were unchanged. | — | Wording corrected; only the two Python blobs were identical up to `31dc1a65`. |

Shipped regression tests (`QuotaSiblingFencingDirtyHandoffTests`):
`test_owner_dirty_seal_keeps_git_operation_started_after_seal_blocked` (sub-cases: empty
cherry-pick, revert, rebase), `test_interrupted_autostash_merge_backs_up_parked_work_and_seals_the_pointer`
(backup content, checksums, four pointer drifts, pruned-commit fail-closed),
`test_interrupted_merge_seal_rejects_autostash_added_after_seal`,
`test_interrupted_merge_seal_rejects_symlink_target_drift` (symlink target drift rejection), and
`test_interrupted_merge_seal_rejects_hardlink_byte_drift` (hardlink byte drift rejection). Two pre-existing mock-only
tests in `AgyBackgroundExitRecoveryTests` that model an ordinary dirty seal on a path that is not
a Git repository now also patch `_git_operation_in_progress` to `False` (both in
`worker_workspace` and `supervisor`, because `_sync_supervisor_scope` rebinds on every call);
on a non-repository path that helper fails closed, which is the behaviour the fix relies on.

## Verification

Environment: project `uv` environment on CPython 3.12.14; pytest 9.1.1; git 2.43.0. All runs in
this section completed before `2026-09-24T08:14:09Z` (UTC, read from `date -u` after the last run).

### Declared verification (receipt-bearing)

The three commands declared on the task board (`git diff --check`, `ruff check` on the two
Python files, and the five-suite pytest selection) are executed once through
`delivery_toolchain/git/task_verification.py run` at the final task head, immediately before
`task_finalize.sh`, so that each receipt binds the exact head SHA, command, exit code, duration
and selection. Because a receipt binds the head that contains this document, its values cannot
be copied into this document without invalidating it; the outcomes are posted to the task board
(`note`) and appear in the PR body. The finalize gate (`task_verification check`) refuses to
publish unless every declared command has a passing receipt at that head. The receipts recorded
at `c7982ae7` (before the review) are superseded by the ones at the new head.

### Owner measurements for the review fix (this task's own validation, not receipt-bearing)

| Run | Tree | Result |
|---|---|---|
| A/B: the three new tests against the adopted code | `git archive` copy of `c7982ae7` (`worker_workspace.py` sha256 `34dcd380…`, blob `596bd281`; module origin verified to be the copy) with only the new test file copied in | exit 1: R1 test fails in all three sub-cases (`(True, '1 dirty change (1 unstaged tracked): README.md')` instead of `(False, 'git_operation_in_progress')`); autostash backup test fails with `FileNotFoundError … git-state/MERGE_AUTOSTASH`; added-after-seal test fails with `(True, 'Resume the preserved interrupted merge …')` instead of `(False, 'merge_state_changed')` |
| Same selection (`-k "interrupted_merge or autostash or git_operation_started_after_seal"`) | fixed worktree | exit 0, 7 tests |
| Whole `test_worker_failure_policy.py` | fixed worktree | exit 0 (after the two mock-only tests were extended as described above; before that extension they failed with `git_operation_in_progress`, which is the fail-closed behaviour on a non-repository path) |
| `ruff check` on both Python files; `git diff --check` | fixed worktree | exit 0 |

### Earlier independent probes and the runbook's broader selection (before review round 1)

| Run | Result | Exit | Duration (pytest) |
|---|---|---|---|
| Scratch probes on top of the `QuotaSiblingFencingDirtyHandoffTests` fixture: conflicted merge preserved and sealed (A); same-path content edit after the seal detected (B); stat-cache refresh keeps the seal (C); revert in progress stays blocked (D); fenced-sibling successor dispatch on a conflicted merge (E) | 29 passed, 1 skipped (cherry-pick sub-case of D; see F2/F5 in the assessment for why that skip hid R1), 4 subtests passed | 0 | 20.22 s |
| `test_supervisor.py -k 'preserve or handoff or review_churn'` on the merged branch `31dc1a65` | 55 passed, 6 skipped, 0 failed (74 JUnit cases incl. subtests) | 0 | 5.75 s |
| Same selection on an unmodified `git archive` copy of `origin/dev c4efabbb` (module origin verified to be the copy) | 55 passed, 6 skipped, 0 failed | 0 | 6.98 s |

The probe file lives in the worker scratch directory and is intentionally not shipped; its
scenarios are described in `independent_assessment.md` so a reviewer can reproduce them.

The runbook's 2026-09-23 statement that the broader selection fails with `common.ConfigError`
did not reproduce on either tree (finding F1). The runbook received an addendum recording this;
the original text is kept.

### Genuine regressions

The two review findings are genuine defects in the adopted code, both reproduced with real Git
operations before the fix and covered by shipped tests. No test was re-run for a count.

## Live observation (log level only; no runtime paths, PIDs or dumps published)

- The supervisor's runtime alias resolves to a runtime checkout at `43d2fe8c`; its
  `.orchestrator/worker_workspace.py` is blob `596bd281…`, i.e. the adopted code **without**
  the R1/R2 fix. Until the next controlled rollout the live fleet therefore still has both
  exposures: an `owner_dirty` seal can be leased across a later cherry-pick/revert/rebase, and
  an autostash merge is sealed and backed up without its parked work.
- The canonical activity log shows the sealed-continuation path exercised after that rollout:
  `worker_worktree_preserved` (trigger `sibling_fenced`) at `2026-09-23T14:35:06Z`,
  `task_reassigned` Antigravity4 → Claude2 at `2026-09-23T14:35:15Z`, and
  `worker_worktree_refreshed` with `refresh_status=sealed_owner_continuation` for Claude2 at
  `2026-09-23T14:39:40Z` and again at `2026-09-23T15:27:51Z`, all for
  `DPF-OVERTURE-BOUNDED-RECORD-READER-001`. The activity log does not record whether a seal was
  `owner_dirty` or `interrupted_merge`, and the live state holds no open handoff block at the time
  of writing, so this is evidence that the continuation path ran, not a measurement of the merge
  branch specifically.

## Rollout record

The runtime runs the original patch only. After Codex2 approval, required CI and the normal
merge into `dev`, the merged source for the next controlled runtime rollout is the `dev` merge
commit of PR #1362, which now includes the R1/R2 fix. This task performs no rollout, no
supervisor restart and no product deployment.

## Files in this directory

- `README.md` — this record.
- `independent_assessment.md` — Claude's source-bound code assessment, acceptance-item mapping,
  findings F1–F6 (F5 and F6 are the review findings, fixed).
