#!/usr/bin/env bash
# Open (or re-enter) the per-task branch for a Pantheon task.
#
#   ./delivery_toolchain/git/task_start.sh "ODP-EXAMPLE-001"
#
# Pantheon's branch model is per-task ephemeral branches cut from the tip of
# the workflow target (`dev`), merged back by PR. Permanent `worker/<name>`
# branches are retired. Every worker wakeup prompt and
# `.orchestrator/skills/*` point here rather than at hand-written branch
# rules, so that the prefix, the base, and the dirty-worktree refusal are
# decided in exactly one place.
#
# Idempotent: if you are already on the task branch it verifies and exits 0,
# which is the normal case inside a supervisor-created per-task worktree.
#
# Exit codes: 0 = on the task branch, 1 = refused (dirty tree, branch busy
# elsewhere), 2 = usage error.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: delivery_toolchain/git/task_start.sh <TASK-ID> [--allow-dirty] [--base <branch>]

  <TASK-ID>       e.g. ODP-EXAMPLE-001 (branch becomes task/ODP-EXAMPLE-001)
  --allow-dirty   do not refuse when tracked files are already modified
  --base <branch> base to cut a new branch from (default: $PANTHEON_TASK_PR_BASE or dev)
EOF
}

TASK_ID=""
ALLOW_DIRTY=0
BASE_BRANCH="${PANTHEON_TASK_PR_BASE:-dev}"

while [ $# -gt 0 ]; do
  case "$1" in
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --base) BASE_BRANCH="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "task_start: unknown option $1" >&2; usage; exit 2 ;;
    *)
      if [ -n "$TASK_ID" ]; then echo "task_start: unexpected argument $1" >&2; usage; exit 2; fi
      TASK_ID="$1"; shift ;;
  esac
done

if [ -z "$TASK_ID" ] || [ -z "$BASE_BRANCH" ]; then usage; exit 2; fi

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

PREFIX="${PANTHEON_TASK_BRANCH_PREFIX:-task/}"
BRANCH="${PREFIX}${TASK_ID}"
CURRENT="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"

# Tracked modifications only: per-task worktrees are seeded with gitignored
# state mirrors (ai-status.json, current-work.md, ai-activity-log.jsonl) and
# those must not be read as "the previous task left work behind".
if [ "$ALLOW_DIRTY" -eq 0 ] && [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "task_start: refusing to switch branch, tracked files are modified:" >&2
  git status --short --untracked-files=no >&2
  cat >&2 <<EOF

If these belong to $TASK_ID, commit them first:
  python3 delivery_toolchain/git/worker_commit.py --task-id "$TASK_ID" --message-file <msg> --scope <paths>
If they belong to another task, this is an uncleaned handoff: record a blocker
and stop. Do not stash and continue
(see .orchestrator/skills/worker-anchor-commit.md).
EOF
  exit 1
fi

if [ "$CURRENT" = "$BRANCH" ]; then
  echo "task_start: already on $BRANCH"
  exit 0
fi

# Non-fatal: an offline worker should still be able to resume an existing
# local task branch. Only cutting a *new* branch truly needs the fetch.
FETCHED=1
git fetch --quiet origin "$BASE_BRANCH" 2>/dev/null || FETCHED=0
if [ "$FETCHED" -eq 0 ]; then
  echo "task_start: warning: could not fetch origin/$BASE_BRANCH; using local refs" >&2
fi

# Refuse early when another worktree holds the branch; `git switch` would fail
# with a message that reads like a git bug rather than a fleet collision.
BUSY_WORKTREE="$(git worktree list --porcelain \
  | awk -v b="refs/heads/$BRANCH" '/^worktree /{w=$2} /^branch /{if ($2==b) print w}' \
  | head -1)"
if [ -n "$BUSY_WORKTREE" ]; then
  echo "task_start: $BRANCH is checked out in another worktree: $BUSY_WORKTREE" >&2
  echo "task_start: work there, or ask the supervisor to re-dispatch this task." >&2
  exit 1
fi

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git switch --quiet "$BRANCH"
  echo "task_start: resumed existing $BRANCH"
else
  if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    git switch --quiet --create "$BRANCH" --track "origin/$BRANCH"
    echo "task_start: created $BRANCH tracking origin/$BRANCH"
  else
    if git show-ref --verify --quiet "refs/remotes/origin/$BASE_BRANCH"; then
      START="origin/$BASE_BRANCH"
    elif git show-ref --verify --quiet "refs/heads/$BASE_BRANCH"; then
      START="$BASE_BRANCH"
    else
      echo "task_start: base branch '$BASE_BRANCH' not found locally or on origin" >&2
      exit 1
    fi
    git switch --quiet --create "$BRANCH" "$START"
    echo "task_start: created $BRANCH from $START"
  fi
fi

# Deliberately does NOT run `git config core.hooksPath` here. That config is
# shared by every linked worktree of the clone, so switching it on behalf of a
# worker would change how *other* lanes' in-flight commits are validated.
# Enabling the hook stays an explicit operator step (delivery_toolchain/git/install_hooks.sh);
# worker_commit.py validates the same rules in-process regardless.
if [ -d "$ROOT/.githooks" ] && [ "$(git config --get core.hooksPath || true)" != ".githooks" ]; then
  echo "task_start: note: commit-msg hook is not installed in this clone (./delivery_toolchain/git/install_hooks.sh)"
fi

echo "task_start: HEAD $(git rev-parse --short HEAD) on $BRANCH"
echo "task_start: next -> edit, then delivery_toolchain/git/worker_commit.py, then delivery_toolchain/git/task_finalize.sh \"$TASK_ID\""
