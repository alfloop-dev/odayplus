# `delivery_toolchain/git/` — per-task git helpers

Every worker wakeup prompt, `.orchestrator/skills/worker-anchor-commit.md`,
`.orchestrator/skills/task-closeout-finalization.md`,
`.orchestrator/auto_commit_archive.py`, `.orchestrator/watch_events.py` and
`scripts/orchestrator/finalize_lane_doctor.py` all instruct
workers to run the scripts in this directory. They are the single place where
the branch prefix, the PR base, the staging rules and the commit-message
contract are decided.

## The flow

```bash
TASK=ODP-EXAMPLE-001

./delivery_toolchain/git/task_start.sh "$TASK"                 # branch task/$TASK from dev

# edit files, then:
python3 delivery_toolchain/git/worker_commit.py \
  --task-id "$TASK" \
  --message-file /tmp/${TASK}-msg.txt \
  --scope <path1> <path2> ... \
  --index-file /tmp/git-index-task-$TASK

./delivery_toolchain/git/task_finalize.sh "$TASK"              # push + PR + submit review

# wait for GitHub to merge the PR into dev, then:
AI_NAME=<Owner> ./scripts/ai-status.sh done "$TASK" "<checkpoint>"
```

| Script | Purpose |
| --- | --- |
| `task_start.sh` | Create or resume `task/<TASK-ID>` from the `dev` tip. Refuses a tracked-dirty worktree (uncleaned handoff) and a branch held by another worktree. Idempotent. |
| `worker_commit.py` | The only sanctioned way to make a task commit. Private index, explicit scope, leak check, message check, protected-branch guard, no empty commits. |
| `check_commit_scope.py` | Rejects a staged set that leaks outside `--scope`. Importable and standalone. |
| `check_commit_trailers.py` | Validates subject shape/length and the `LLM-Agent` / `Task-ID` / `Reviewer` trailers. Backs both `worker_commit.py` and the hook. |
| `check_task_delivery_identity.py` | Validates delivery range identity (task ID prefix, trailers, reviewer separation, branch binding) without enforcing the subject length format lint on historical commits. |
| `task_finalize.sh` | Push the branch, open (or re-use) the PR against `dev`, undraft it, and atomically submit review evidence. |
| `install_hooks.sh` | Point `core.hooksPath` at `.githooks/` (per-clone local config, so it cannot be committed). |

`install_hooks.sh` is deliberately an explicit operator step: `core.hooksPath`
is shared by every linked worktree of a clone, so `task_start.sh` will not
flip it on behalf of one worker and change how other lanes' in-flight commits
are validated. It only prints a reminder. `worker_commit.py` enforces the same
message rules in-process, so the sanctioned path is covered either way.

## Why each guard exists

**Private index, seeded from HEAD.** Workers can share a worktree and
therefore one `.git/index`. A plain `git commit` after another worker left
files staged silently absorbs them — the 2026-05-16 sweep-in incident
(`e06f5cf2`, 8 unrelated files). `worker_commit.py` *always* stages into its
own `GIT_INDEX_FILE`, seeded with `git read-tree HEAD` rather than copied from
the live index. "Clear stale staging first" is therefore structural instead of
a step a caller can forget; `--index-file` only chooses where that index
lives. After committing it resets **only the scoped paths** in the worktree's
default index, so a concurrent worker's staging survives.

**Explicit scope.** `.`, `-A`, `*` and absolute paths are rejected, and the
staged set is re-read from git and re-checked afterwards — so a directory
scope that expands wider than expected still fails before the commit.

**Base branch out of the agent's hands.** `.orchestrator/permission_broker.py`
denies bare `gh pr create` to agents because an agent-chosen `--base` can
route work straight at the promotion target and bypass dev CI.
`task_finalize.sh` always uses the branch-workflow target (`dev`, override
with `PANTHEON_TASK_PR_BASE`), which is what that policy protects.

**Auto-merge has one owner.** `task_finalize.sh` never arms it. GitHubBus does
so only after canonical reviewer approval; `task-review-gate` and
`.github/workflows/merge-queue-review-gate.yml` still enforce the reviewed
head before GitHub's merge queue performs the merge.

**PR discovery by head branch.** GitHub allows at most one open PR per
`(head, base)` pair; a title search misses worker-opened PRs and causes
duplicate-create storms (ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001). A create that
races another opener still recovers the existing PR number from `gh`'s output.

## Notes

- `.orchestrator/bin/gh` is a broker shim, not the real CLI. `task_finalize.sh`
  resolves `gh` the same way `delivery_toolchain/github/check_pr_merge_eligibility.py` does.
- `check_commit_trailers.py` enforces a **72**-character subject limit on new
  commits (via `worker_commit.py` and `.githooks/commit-msg`), matching the
  format `.orchestrator/auto_commit_archive.py` builds its messages against.
  `task-closeout-finalization.md` recommends ≤ 70; that is stricter guidance,
  not a conflicting rule.
- `check_task_delivery_identity.py` verifies task ID, required trailers, and
  branch binding across the full delivery range without re-checking the
  72-character subject length limit, ensuring pushed commits with long subjects
  can finalize without requiring history rewrites.
- `--dry-run` on `worker_commit.py` and `task_finalize.sh` runs every guard
  without touching the repo, origin, or GitHub.
- Tests: `python3 -m pytest delivery_toolchain/git/test_git_task_scripts.py`. They run
  against throwaway repos and never hit the network.
