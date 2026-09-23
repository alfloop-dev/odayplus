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
