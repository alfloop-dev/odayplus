# Interrupted worker merge recovery

When a quota-fenced owner exits during a merge, the supervisor preserves the
raw Git index, logical index entries, merge control files, staged/unstaged
patches and dirty file contents before settling the dead worker. The backup
remains under the canonical `.orchestrator/worktree-dirt-backups/` directory.

Only the original owner, or a successor authorized through the existing atomic
handoff, may resume the exact checkout. The seal binds the task, owner, branch,
HEAD, working files, logical index and merge metadata. Reviewers and helpers
cannot use the continuation. A changed index, merge state or worktree rejects
the lease. An index lock, rebase, cherry-pick or revert remains blocked.
The supervisor does not resolve conflicts, commit a merge, discard changes or
approve product acceptance; the resumed owner must finish and verify the merge.

For quota and review-churn rotation, owner fallbacks must include a different
account pool. Logical Antigravity aliases share one pool. Add authenticated
Claude owner identities to those fallbacks, and include both `antigravity` and
`claude` in the owner preference so an intentional handoff is retained. Keep
Codex reviewers and existing independent-account review checks.

A task already escalated for repeated review failures requires the existing
single-use, expiring `approve_continuation` flow backed by an actual user
instruction. Preserve prior findings and counts. Real legal receipts, source
permissions and release approvals remain separate dependencies; engineering
subtasks must have their own acceptance rather than repeatedly submitting an
unfulfilled human-gated deliverable.

## Validation (2026-09-23)

Passed with the existing project Python environment:

- `python -m pytest -q .orchestrator/test_worker_failure_policy.py -k interrupted_merge`
- `python -m pytest -q .orchestrator/test_worker_failure_policy.py .orchestrator/test_worktree_authority.py .orchestrator/test_worktree_cleanliness.py .orchestrator/test_runtime_state.py .orchestrator/test_supervisor_scope_injection.py`
- `python -m ruff check .orchestrator/worker_workspace.py .orchestrator/test_worker_failure_policy.py`
- `git diff --check`

The broader selection `test_supervisor.py -k 'preserve or handoff or review_churn'`
has the same pre-existing missing temporary config failures on unmodified
`origin/dev` (`d87bc0bf`) and this branch: one continuation test and the three
subcases of `test_return_to_review_transitions_clear_and_preserve_the_right_heads`.
The failure is `common.ConfigError` for a removed fixture's config.json.
This validation does not claim those broader checks passed.

## Independent validation (2026-09-24, ODP-ORCH-AUTONOMOUS-RECOVERY-001)

Claude re-ran the broader selection
`test_supervisor.py -k 'preserve or handoff or review_churn'` on the adopted branch after its
base advance to `origin/dev` (`c4efabbb`) and on an unmodified copy of that `origin/dev`:
55 passed, 6 skipped, 0 failed on both trees. The `common.ConfigError` failures described in
the 2026-09-23 section did not reproduce, and the three tests it names pass on both trees.
Read that section as an observation from that session's environment, not as a known-failing
baseline. Additional scratch probes (a conflicted merge with unmerged index entries, a same-path
content edit after the seal, a stat-cache refresh, a revert in progress, and fenced-sibling
successor dispatch on a conflicted merge) behaved as this runbook describes. Details:
`docs/evidence/completion/ODP-ORCH-AUTONOMOUS-RECOVERY-001/`.

## Review fixes (2026-09-24, ODP-ORCH-AUTONOMOUS-RECOVERY-001, Codex2 findings R1/R2/R3/R4)

- An ordinary dirty seal (`owner_dirty`) never resumes a checkout that has a Git
  operation attached. A cherry-pick, revert or rebase started after the seal can
  leave HEAD, the index and every dirty byte unchanged (an empty cherry-pick does
  exactly that); the lease is refused with `git_operation_in_progress` regardless,
  and the refresh verdict `unresolved_git_operation` only becomes a continuation
  for a seal that captured the merge itself (`interrupted_merge`).
- `MERGE_AUTOSTASH` is part of the merge seal and of the backup. With
  `git merge --autostash` or `merge.autoStash=true` the pre-merge dirty work lives
  only in the stash-like commit that file names; it is not in the worktree and not
  in any patch. The backup now stores that content as
  `git-state/MERGE_AUTOSTASH-worktree.patch` and `git-state/MERGE_AUTOSTASH-index.patch`
  next to the pointer. A pointer that changes, disappears, dangles (git gc prunes
  the parked commit once the file is gone) or becomes a symlink rejects the lease;
  the resumed owner must not finish the merge without the parked work.
- Working tree state binding in `interrupted_merge` seals binds exact dirty file
  contents and symlink targets without relying on static context-materialization
  fallbacks. Symlink target drift and hardlinked file byte drift after sealing
  are detected and rejected, preserving strict state-drift rejection. Dirty tracked
  symlinks are also preserved and checksummed under `files/` during backup.
- In `_interrupted_merge_worktree_fingerprint`, `inspection.kind` is explicitly bound
  and `status_failed` returns a dedicated failure token rather than an empty digest.
  Interrupted merge continuation and sealing fail closed on git status failure (`status_failed`),
  preventing status read failures on empty porcelain merges from being mistakenly accepted
  as exact clean state. Tracked directory symlinks are recorded in the manifest; file symlinks
  under `files/` are checksummed.
