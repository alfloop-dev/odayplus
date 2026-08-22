#!/usr/bin/env bash
# Open (or re-enter) the per-task branch for a orchestrator task.
#
#   ./delivery_toolchain/git/task_start.sh "ODP-EXAMPLE-001"
#
# A task branch/worktree is allocated only by the supervisor Worker Manager.
# This helper is intentionally a verifier: allowing it to create a branch from
# a moving base would recreate a second workspace authority outside the lease.
#
# Idempotent: if you are already on the task branch it verifies and exits 0,
# which is the normal case inside a supervisor-created per-task worktree.
#
# Exit codes: 0 = on the task branch, 1 = refused (dirty tree, branch busy
# elsewhere), 2 = usage error.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: delivery_toolchain/git/task_start.sh <TASK-ID> [--allow-dirty]

  <TASK-ID>       e.g. ODP-EXAMPLE-001 (branch becomes task/ODP-EXAMPLE-001)
  --allow-dirty   do not refuse when tracked files are already modified
EOF
}

TASK_ID=""
ALLOW_DIRTY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "task_start: unknown option $1" >&2; usage; exit 2 ;;
    *)
      if [ -n "$TASK_ID" ]; then echo "task_start: unexpected argument $1" >&2; usage; exit 2; fi
      TASK_ID="$1"; shift ;;
  esac
done

if [ -z "$TASK_ID" ]; then usage; exit 2; fi

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

echo "task_start: refusing to create or switch $BRANCH outside its Worker Manager lease." >&2
echo "task_start: re-dispatch $TASK_ID, then run this command inside the leased worktree." >&2
exit 1
