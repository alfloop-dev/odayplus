# ODP-ORCH-AUTONOMOUS-RECOVERY-001 — Completion evidence

**Task:** ODP-ORCH-AUTONOMOUS-RECOVERY-001 (驗證並正式交付 supervisor 自動恢復修復)
**Owner:** Claude2 (auto-reassigned from Antigravity7 after four reviewer reopens; earlier Claude,
then Antigravity7) · **Reviewer:** Codex2 · **PR:** #1362 · **Class:** remediation (tracked
adoption of an already-running orchestrator fix)

## Purpose

Bring the interrupted-merge recovery fix (quota-fenced worker exits during a Git merge; the
supervisor preserves the exact merge state and lets only the sealed owner or an authorised
successor resume it) through owner validation, independent review and the normal merge path.
No product deployment is included.

## Provenance and adoption

- Implementation commit `43d2fe8ca8e04b35a5049802169a01a5bc958678` was authored by the
  interactive Codex session (`LLM-Agent: Codex`, `Reviewer: Claude`) and opened as draft PR #1362.
  Its commit history, author and trailers are preserved unchanged; nothing was rewritten.
- Each owner inspected and probed the change independently; see `independent_assessment.md`.
  The prior green CI on `43d2fe8c` was not used as a substitute for that review, and no reviewer
  approval is claimed here.
- Ownership on the board: Claude (rounds 1–2), Antigravity7 from `2026-09-24T08:48:40Z`
  (rounds 3–4), Claude2 from the board event `review_churn_reassigned` at
  `2026-09-24T10:14:32Z`, which returned the task to `todo` for a fresh implementation run after
  the fourth reopen. This run reused the existing clean task worktree discovered by the
  supervisor. No other task worktree, no live supervisor and no worker was touched.

## Base advance (round 1, required by that dispatch)

| Item | Value |
|---|---|
| Merge commit | `31dc1a65e3d813d9174852925d6e95feb237cdf7` |
| Parents | `43d2fe8ca8e04b35a5049802169a01a5bc958678` (task) + `c4efabbbeba9e743fab8ee52125932137852c703` (`origin/dev`) |
| Resulting tree | `1f74062138f6d2dbb23db4cb8c63c39feadd37a9` = tree predicted by `git merge-tree --write-tree` before merging |
| Conflicts | none; `origin/dev` added no change under `.orchestrator/` or to the deliverable files |
| History | plain merge, no rebase, no reset; the previously pushed tip `43d2fe8c` remains an ancestor |

At `43d2fe8c` and at `31dc1a65` the two Python deliverable blobs were byte-identical
(`596bd281…`, `905ae956…`). Every later commit on the branch changes both. `origin/dev` is still
`c4efabbb` at the time of this run, so no further base advance was needed.

## Review rounds (Codex2) and fixes

| Finding | What was found | Reproduced as | Fix (commit) |
|---|---|---|---|
| R1 (P2, round 1) | `prepare_worker_workspace` routed `unresolved_git_operation` into `sealed_owner_continuation_allowed` without requiring an `interrupted_merge` seal; an ordinary `owner_dirty` seal binds only porcelain status, dirty bytes and HEAD, so a cherry-pick, revert or rebase started after the seal was leased. | Real empty `git cherry-pick` on a sealed dirty checkout: `CHERRY_PICK_HEAD` present, HEAD / `ls-files --stage` / porcelain / bytes unchanged, seal accepted. | `unresolved_git_operation` is a continuation candidate only for an `interrupted_merge` seal; the ordinary dirty path refuses with `git_operation_in_progress` whenever any Git operation is attached (`5cc59315`). |
| R2 (P2, round 1) | The merge-state snapshot omitted `MERGE_AUTOSTASH`; with `git merge --autostash` the pre-merge dirty work lives only in the stash-like commit that file names, outside seal and backup. | Real `git merge --no-commit --autostash dev` with a dirty tracked file: backup had no `git-state/MERGE_AUTOSTASH`; pointer changes after sealing did not change the verdict. | `MERGE_AUTOSTASH` joins snapshot, seal and backup; a pointer that no longer names a commit refuses the snapshot; the parked content is stored as `MERGE_AUTOSTASH-worktree.patch` and `MERGE_AUTOSTASH-index.patch` (`5cc59315`). |
| R3 (P2, round 2) | The worktree part of the seal reused `worktree_cleanliness._worktree_fingerprint`, which hashes symlinks and hardlinks as the constant `unsafe-path`, so their drift after the seal was invisible. | Dirty tracked symlink retargeted, or hardlinked `README.md` edited, after the seal: porcelain, HEAD, logical index and merge snapshot unchanged, seal accepted. | Dedicated `_interrupted_merge_worktree_fingerprint` reads symlink targets and file bytes directly; dirty symlinks are re-created under `files/` in the backup (`e0542a7a`). |
| R4 (P2, round 3) | That fingerprint hashed a failed `git status` (`entries=()`) exactly like a clean empty listing, so a status read failure passed as the exact sealed state. | Clean merge from divergent allow-empty commits sealed; invalid `status.showUntrackedFiles` plus a dirty file after the seal: `git status` exit 128, seal still accepted. | `inspection.kind` is part of the digest; seal creation and continuation fail closed on a failed status (`0ff911f4`). |
| R5 (P2, round 4) | The regular-file branch bound type and bytes but not the Git executable mode; `chmod +x` on an already-dirty file leaves porcelain, HEAD, logical index, merge metadata and bytes untouched. | Real Git at `0ff911f4`: after the seal, `git diff --summary` = `mode change 100644 => 100755 README.md`, fingerprint unchanged, direct seal accepted, lease granted with `sealed_owner_merge`. | A regular file is bound as `100644` or `100755` from the owner execute bit, the rule Git applies; mtime, ctime and the raw index stay excluded (this run's commit). |
| Claude2 F10 (own finding, this run) | A directory entry was bound as the constant `dir`; a submodule gitlink whose `HEAD` moved or whose files changed after the seal, or an untracked nested checkout, kept the same porcelain code and was leased. | Real submodule (`git submodule add` in the fixture) moved to another commit and edited inside after the seal; superproject porcelain, HEAD, logical index and merge metadata identical; seal accepted on `0ff911f4`. | A directory with a `.git` entry is bound by its `HEAD` and, recursively, its own dirty entries; unreadable nested state makes the fingerprint `None`: no seal at worker death, `merge_state_changed` at continuation (this run's commit). |
| Claude2 F11 (Codex2 non-blocking note in round 3, confirmed, this run) | The backup checksum walk skipped symlinks that resolve to a directory inside the backup (`os.walk` lists them under directory names). | Dirty symlink `docs_link -> docs` next to a dirty tracked file under `docs/`: `files/docs_link` present, no entry in `backup_checksums.sha256` on `0ff911f4`. | The walk also records symlinks found among the directory names by target (this run's commit). |
| non-blocking wording | Earlier versions of this README carried stale text (three unchanged deliverables; "two findings"; F1–F6; a time-of-day bound; a function name that does not exist). | — | Corrected in this run; timing claims now live only in the verification receipts. |

Shipped regression tests (`QuotaSiblingFencingDirtyHandoffTests`):
`test_owner_dirty_seal_keeps_git_operation_started_after_seal_blocked` (empty cherry-pick,
revert, rebase), `test_interrupted_autostash_merge_backs_up_parked_work_and_seals_the_pointer`,
`test_interrupted_merge_seal_rejects_autostash_added_after_seal`,
`test_interrupted_merge_seal_rejects_symlink_target_drift`,
`test_interrupted_merge_seal_rejects_hardlink_byte_drift`,
`test_interrupted_merge_seal_rejects_empty_porcelain_merge_status_failure_and_dirty_drift`,
`test_interrupted_merge_seal_refuses_when_status_fails_at_seal_time`,
`test_interrupted_merge_seal_rejects_executable_mode_drift` (direct chmod and chmod through a
hardlink; a mtime touch plus `git status` keeps the seal),
`test_interrupted_merge_seal_binds_nested_repository_head_and_dirt`,
`test_interrupted_merge_seal_fails_closed_when_nested_repository_becomes_unreadable`,
`test_interrupted_merge_seal_refuses_when_nested_repository_is_unreadable_at_seal_time`,
`test_interrupted_merge_backup_checksums_directory_symlink_by_target`. Two pre-existing
mock-only tests in `AgyBackgroundExitRecoveryTests` that model an ordinary dirty seal on a path
that is not a Git repository patch `_git_operation_in_progress` to `False` (in both
`worker_workspace` and `supervisor`, because `_sync_supervisor_scope` rebinds on every call).

## Verification

Environment: project `uv` environment on CPython 3.12.14; pytest 9.1.1; git 2.43.0;
`core.fileMode=true`.

### Declared verification (receipt-bearing)

The three commands declared on the task board (`git diff --check`, `ruff check` on the two
Python files, and the five-suite pytest selection) are executed once through
`delivery_toolchain/git/task_verification.py run` at the final task head, immediately before
`task_finalize.sh`, so that each receipt binds the exact head SHA, command, exit code, duration
and selection. Because a receipt binds the head that contains this document, its values cannot
be copied into this document without invalidating it; the outcomes are posted to the task board
(`note`) and appear in the PR body. The finalize gate (`task_verification check`) refuses to
publish unless every declared command has a passing receipt at that head. Receipts recorded at
earlier heads are superseded by the ones at the final head.

### Owner measurements for this run (not receipt-bearing)

| Run | Tree | Result |
|---|---|---|
| R5 probe (scratch, drives the shipped fixture; not shipped) | `0ff911f4` | defect reproduced: mode change visible to Git, fingerprint unchanged, seal and lease granted; 0.87 s |
| Same probe | fixed worktree | `(False, 'merge_state_changed')`, lease refused, accepted again after restoring the mode |
| A/B: the five new tests | `git archive` copy of `0ff911f4` with only the final test file copied in (module origin verified to be the copy) | exit 1, 6.2 s: all five fail, seven failure records; details in `independent_assessment.md` |
| `-k "interrupted_merge or autostash or git_operation_started_after_seal"` | fixed worktree | exit 0, 16 tests |
| `ruff check` on both Python files; `git diff --check` | fixed worktree | exit 0 |

### Earlier rounds (kept for the record)

- Round 1 A/B on a `git archive` copy of `c7982ae7`: the R1/R2 tests failed on the adopted code
  (seal accepted; `FileNotFoundError … git-state/MERGE_AUTOSTASH`) and passed on the fixed tree.
- Rounds 2 and 3: R3 and R4 reproduced by the reviewer and by the owner on real Git state
  before the fix; the shipped tests above cover them.
- Before round 1: scratch probes (conflicted merge preserved and sealed; same-path edit after
  the seal detected; stat-cache refresh keeps the seal; revert in progress stays blocked;
  fenced-sibling successor dispatch on a conflicted merge) 29 passed, 1 skipped, exit 0;
  `test_supervisor.py -k 'preserve or handoff or review_churn'` on `31dc1a65` and on an
  unmodified copy of `origin/dev c4efabbb`: 55 passed, 6 skipped, 0 failed on both (finding F1;
  the runbook received an addendum).

### Genuine regressions

Seven defects (F5–F11) were found in the adopted code across the review rounds: five by the
independent reviewer, two by this run's own inspection. Each was reproduced with real Git
operations before the fix and is covered by a shipped test. No test was re-run for a count.

## Live observation (log level only; no runtime paths, PIDs or dumps published)

- At the time of this run the supervisor's runtime checkout is at `43d2fe8c` and its
  `.orchestrator/worker_workspace.py` is blob `596bd281…`, i.e. the adopted code without any of
  the fixes above. Until the next controlled rollout the live fleet keeps every exposure listed
  in the table: an `owner_dirty` seal can be leased across a later cherry-pick/revert/rebase, an
  autostash merge is sealed without its parked work, and an `interrupted_merge` seal does not see
  symlink, hardlink, status-failure, executable-mode or nested-repository drift.
- The canonical activity log shows the sealed-continuation path exercised after that rollout:
  `worker_worktree_preserved` (trigger `sibling_fenced`), `task_reassigned` Antigravity4 →
  Claude2, and `worker_worktree_refreshed` with `refresh_status=sealed_owner_continuation` for
  Claude2, all for `DPF-OVERTURE-BOUNDED-RECORD-READER-001` on 2026-09-23. The log does not
  record whether a seal was `owner_dirty` or `interrupted_merge`, so this is evidence that the
  continuation path ran, not a measurement of the merge branch specifically.

## Rollout record

The runtime runs the original patch only. After Codex2 approval, required CI and the normal
merge into `dev`, the merged source for the next controlled runtime rollout is the `dev` merge
commit of PR #1362, which includes the fixes for R1–R5 and F10/F11. This task performs no
rollout, no supervisor restart and no product deployment.

## Files in this directory

- `README.md` — this record.
- `independent_assessment.md` — the owner's source-bound code assessment, acceptance-item
  mapping, findings F1–F12 (F5–F11 fixed; F12 recorded for follow-up outside owned paths).
