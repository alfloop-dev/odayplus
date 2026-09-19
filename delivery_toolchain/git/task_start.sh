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

STATUS_JSON="${ORCH_STATUS_ROOT:-${PANTHEON_STATUS_ROOT:-$ROOT}}/ai-status.json"
if [ ! -f "$STATUS_JSON" ]; then
  STATUS_JSON="$ROOT/ai-status.json"
fi
RECORDED_BRANCH=""
if [ -f "$STATUS_JSON" ]; then
  if command -v jq >/dev/null 2>&1; then
    RECORDED_BRANCH="$(jq -r --arg id "$TASK_ID" '.tasks[]? | select(.id == $id) | .branch // empty' "$STATUS_JSON" 2>/dev/null || true)"
  elif command -v python3 >/dev/null 2>&1; then
    RECORDED_BRANCH="$(python3 -c "import json; data=json.load(open('$STATUS_JSON')); tasks={t.get('id'): t for t in data.get('tasks', []) if isinstance(t, dict)}; t=tasks.get('$TASK_ID', {}); print(t.get('branch') or '')" 2>/dev/null || true)"
  fi
fi

if [ -n "$RECORDED_BRANCH" ]; then
  BRANCH="$RECORDED_BRANCH"
else
  PREFIX="${PANTHEON_TASK_BRANCH_PREFIX:-task/}"
  BRANCH="${PREFIX}${TASK_ID}"
fi
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

if [ "$CURRENT" = "HEAD" ]; then
  HEAD_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
  STATUS_JSON="$ROOT/ai-status.json"
  if [ ! -f "$STATUS_JSON" ]; then
    STATUS_JSON="${ORCH_STATUS_ROOT:-${PANTHEON_STATUS_ROOT:-$ROOT}}/ai-status.json"
  fi
  AUTHORITATIVE_HEAD=""
  if [ -f "$STATUS_JSON" ]; then
    if command -v jq >/dev/null 2>&1; then
      AUTHORITATIVE_HEAD="$(jq -r --arg id "$TASK_ID" '.tasks[]? | select(.id == $id) | (.approved_head // .review_submission.remote_sha // empty)' "$STATUS_JSON" 2>/dev/null || true)"
    elif command -v python3 >/dev/null 2>&1; then
      AUTHORITATIVE_HEAD="$(python3 -c "import json; data=json.load(open('$STATUS_JSON')); tasks={t.get('id'): t for t in data.get('tasks', []) if isinstance(t, dict)}; t=tasks.get('$TASK_ID', {}); print(t.get('approved_head') or (t.get('review_submission') or {}).get('remote_sha') or '')" 2>/dev/null || true)"
    fi
  fi
  AUTHORITATIVE_HEAD="$(echo "$AUTHORITATIVE_HEAD" | tr -d '[:space:]')"

  if [ -n "$AUTHORITATIVE_HEAD" ] && [ -n "$HEAD_SHA" ]; then
    AUTHORITATIVE_FULL="$(git rev-parse "$AUTHORITATIVE_HEAD" 2>/dev/null || true)"
    if [ -n "$AUTHORITATIVE_FULL" ] && [ "$HEAD_SHA" = "$AUTHORITATIVE_FULL" ]; then
      IS_ANCESTOR=0
      for target_ref in "$BRANCH" "origin/$BRANCH" "dev" "origin/dev"; do
        if git merge-base --is-ancestor HEAD "$target_ref" 2>/dev/null; then
          IS_ANCESTOR=1
          break
        fi
      done
      if [ "$IS_ANCESTOR" -eq 1 ]; then
        echo "task_start: already at verified immutable review checkout for $BRANCH (${HEAD_SHA:0:12})"
        exit 0
      fi
    fi
  fi
fi

echo "task_start: refusing to create or switch $BRANCH outside its Worker Manager lease." >&2
echo "task_start: re-dispatch $TASK_ID, then run this command inside the leased worktree." >&2
exit 1
