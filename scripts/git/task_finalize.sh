#!/usr/bin/env bash
# Push the per-task branch, open (or re-use) its PR against dev, and turn on
# auto-merge.
#
#   ./scripts/git/task_finalize.sh "ODP-EXAMPLE-001"
#
# This is the step the fleet was missing. Without it each worker improvised
# its own closeout, so whether a green PR ever merged depended on which worker
# happened to remember `gh pr merge --auto` -- #650/#660 had it, #661/#664 did
# not. Centralising it here also keeps the base branch out of the agent's
# hands: `.orchestrator/permission_broker.py` denies bare `gh pr create` to
# agents precisely because an agent-chosen `--base` can route work straight at
# the promotion target and bypass dev CI. This script always bases task PRs on
# the branch-workflow target.
#
# Enabling auto-merge does NOT bypass review: `task-review-gate` is a required
# check that only the assigned reviewer stamps (scripts/ai_status.py), and
# .github/workflows/merge-queue-review-gate.yml re-asserts it for queued PRs.
# Auto-merge simply stops an approved, green PR from sitting there forever.
#
# Closeout is still not complete here. Wait for GitHub to report the PR merged
# into dev, then run:
#   AI_NAME=<Owner> ./scripts/ai-status.sh done "<TASK-ID>" "<checkpoint>"
#
# Exit codes: 0 = PR open (or already merged), 1 = refused / gh failure,
# 2 = usage error.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: scripts/git/task_finalize.sh <TASK-ID> [--dry-run] [--base <branch>] [--no-auto-merge] [--no-status-submit]

  <TASK-ID>        e.g. ODP-EXAMPLE-001 (branch task/ODP-EXAMPLE-001)
  --dry-run        print what would run; touch neither origin nor GitHub
  --base <branch>  PR target (default: $PANTHEON_TASK_PR_BASE or dev)
  --no-auto-merge  push and open the PR, but leave auto-merge off
  --no-status-submit  do not atomically move a tracked task to review (only for
                      supervisor housekeeping PRs which have no board task)
EOF
}

TASK_ID=""
DRY_RUN=0
AUTO_MERGE=1
STATUS_SUBMIT=1
BASE_BRANCH="${PANTHEON_TASK_PR_BASE:-dev}"
MERGE_METHOD="${PANTHEON_TASK_PR_MERGE_METHOD:-merge}"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-auto-merge) AUTO_MERGE=0; shift ;;
    --no-status-submit) STATUS_SUBMIT=0; shift ;;
    --base) BASE_BRANCH="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "task_finalize: unknown option $1" >&2; usage; exit 2 ;;
    *)
      if [ -n "$TASK_ID" ]; then echo "task_finalize: unexpected argument $1" >&2; usage; exit 2; fi
      TASK_ID="$1"; shift ;;
  esac
done

if [ -z "$TASK_ID" ] || [ -z "$BASE_BRANCH" ]; then usage; exit 2; fi

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

PREFIX="${PANTHEON_TASK_BRANCH_PREFIX:-task/}"
BRANCH="${PREFIX}${TASK_ID}"
CURRENT="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"

if [ "$CURRENT" != "$BRANCH" ]; then
  echo "task_finalize: on '$CURRENT' but expected '$BRANCH'." >&2
  echo "task_finalize: run ./scripts/git/task_start.sh \"$TASK_ID\" first." >&2
  exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "task_finalize: refusing to open a PR with uncommitted tracked changes:" >&2
  git status --short --untracked-files=no >&2
  echo "task_finalize: commit them via scripts/git/worker_commit.py first." >&2
  exit 1
fi

git fetch --quiet origin "$BASE_BRANCH" 2>/dev/null || \
  echo "task_finalize: warning: could not fetch origin/$BASE_BRANCH" >&2

BASE_REF="origin/$BASE_BRANCH"
git show-ref --verify --quiet "refs/remotes/$BASE_REF" || BASE_REF="$BASE_BRANCH"

if git merge-base --is-ancestor HEAD "$BASE_REF" 2>/dev/null; then
  echo "task_finalize: HEAD is already an ancestor of $BASE_REF -- the work has landed."
  echo "task_finalize: no PR needed. Close the task out with:"
  echo "  AI_NAME=<Owner> ./scripts/ai-status.sh done \"$TASK_ID\" \"<checkpoint>\""
  exit 0
fi

AHEAD="$(git rev-list --count "$BASE_REF..HEAD" 2>/dev/null || echo 0)"
if [ "$AHEAD" -eq 0 ]; then
  echo "task_finalize: no commits between $BASE_REF and $BRANCH; nothing to open a PR for." >&2
  exit 1
fi

# gh resolution mirrors scripts/check_pr_merge_eligibility.get_gh_executable:
# .orchestrator/bin/gh is a broker shim, not the real CLI.
resolve_gh() {
  local found
  found="$(command -v gh 2>/dev/null || true)"
  case "$found" in
    */.orchestrator/bin/gh)
      for candidate in /usr/bin/gh /usr/local/bin/gh; do
        [ -x "$candidate" ] && { echo "$candidate"; return 0; }
      done
      ;;
  esac
  if [ -n "$found" ]; then echo "$found"; return 0; fi
  for candidate in /usr/bin/gh /usr/local/bin/gh; do
    [ -x "$candidate" ] && { echo "$candidate"; return 0; }
  done
  return 1
}

GH="$(resolve_gh || true)"
if [ -z "$GH" ] && [ "$DRY_RUN" -eq 0 ]; then
  echo "task_finalize: GitHub CLI ('gh') not found; cannot open the task PR." >&2
  exit 1
fi
GH="${GH:-gh}"

SUBJECT="$(git log --no-merges --format=%s "$BASE_REF..HEAD" 2>/dev/null | tail -1)"
[ -n "$SUBJECT" ] || SUBJECT="$(git log -1 --format=%s)"
[ -n "$SUBJECT" ] || SUBJECT="$TASK_ID: task branch changes"

BODY_FILE="$(mktemp "${TMPDIR:-/tmp}/task-finalize-${TASK_ID}-body.XXXXXX")"
trap 'rm -f "$BODY_FILE"' EXIT
{
  echo "Task: \`$TASK_ID\`"
  echo
  echo "Branch \`$BRANCH\` -> \`$BASE_BRANCH\` ($AHEAD commit(s))."
  echo
  echo "Commits:"
  git log --no-merges --format='- %h %s' "$BASE_REF..HEAD"
  echo
  echo "Opened by \`scripts/git/task_finalize.sh\`. Merging still requires the"
  echo "assigned reviewer's \`task-review-gate\` status plus required CI."
} > "$BODY_FILE"

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "dry-run: $*"
    return 0
  fi
  "$@"
}

echo "task_finalize: pushing $BRANCH ($AHEAD commit(s) ahead of $BASE_REF)"
run git push --set-upstream origin "refs/heads/$BRANCH:refs/heads/$BRANCH"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry-run: $GH pr list --head $BRANCH --base $BASE_BRANCH --state open --json number,url,isDraft"
  echo "dry-run: $GH pr create --base $BASE_BRANCH --head $BRANCH --title $SUBJECT --body-file $BODY_FILE"
  [ "$AUTO_MERGE" -eq 1 ] && echo "dry-run: $GH pr merge <number> --auto --$MERGE_METHOD"
  echo "task_finalize: dry-run complete"
  exit 0
fi

# Discover by head branch first: GitHub allows at most one open PR per
# (head, base) pair, whereas a title search misses worker-opened PRs and
# produces duplicate-create storms (ODP-ORCH-REVIEWBUS-PR-DISCOVERY-001).
PR_NUMBER="$("$GH" pr list --head "$BRANCH" --base "$BASE_BRANCH" --state open \
  --json number --jq '.[0].number // empty' 2>/dev/null || true)"

if [ -z "$PR_NUMBER" ]; then
  CREATE_LOG="$(mktemp "${TMPDIR:-/tmp}/task-finalize-${TASK_ID}-create.XXXXXX")"
  if "$GH" pr create --base "$BASE_BRANCH" --head "$BRANCH" \
      --title "$SUBJECT" --body-file "$BODY_FILE" >"$CREATE_LOG" 2>&1; then
    PR_NUMBER="$(sed -n 's#.*/pull/\([0-9][0-9]*\).*#\1#p' "$CREATE_LOG" | head -1)"
  else
    # A racing worker (or ReviewBus) may have opened it between the list and
    # the create; gh puts the existing PR's URL in the failure output.
    PR_NUMBER="$(sed -n 's#.*/pull/\([0-9][0-9]*\).*#\1#p' "$CREATE_LOG" | head -1)"
    if [ -z "$PR_NUMBER" ]; then
      echo "task_finalize: gh pr create failed:" >&2
      cat "$CREATE_LOG" >&2
      rm -f "$CREATE_LOG"
      exit 1
    fi
    echo "task_finalize: PR #$PR_NUMBER already existed for $BRANCH"
  fi
  rm -f "$CREATE_LOG"
else
  echo "task_finalize: re-using open PR #$PR_NUMBER for $BRANCH"
fi

if [ -z "$PR_NUMBER" ]; then
  echo "task_finalize: could not determine the PR number for $BRANCH" >&2
  exit 1
fi

# A draft PR can never auto-merge and is skipped by
# .orchestrator/auto_merge_green_prs.py, which is how approved work stalls.
IS_DRAFT="$("$GH" pr view "$PR_NUMBER" --json isDraft --jq '.isDraft' 2>/dev/null || echo false)"
if [ "$IS_DRAFT" = "true" ]; then
  echo "task_finalize: PR #$PR_NUMBER is a draft; marking ready for review"
  "$GH" pr ready "$PR_NUMBER" || echo "task_finalize: warning: gh pr ready failed" >&2
fi

if [ "$AUTO_MERGE" -eq 1 ]; then
  MERGED_SET=0
  for method in "$MERGE_METHOD" merge squash rebase; do
    if "$GH" pr merge "$PR_NUMBER" --auto "--$method" >/dev/null 2>&1; then
      echo "task_finalize: auto-merge enabled on #$PR_NUMBER (--$method)"
      MERGED_SET=1
      break
    fi
  done
  if [ "$MERGED_SET" -eq 0 ]; then
    # Not fatal: .orchestrator/auto_merge_green_prs.py also sweeps green,
    # reviewer-approved task PRs, so the PR is still on a merge path.
    echo "task_finalize: warning: could not enable auto-merge on #$PR_NUMBER" >&2
    echo "task_finalize: the auto-merge guard will still pick it up once green + approved." >&2
  fi
fi

PR_URL="$("$GH" pr view "$PR_NUMBER" --json url --jq '.url' 2>/dev/null || true)"
echo "task_finalize: PR #$PR_NUMBER ${PR_URL:-} for $TASK_ID"
if [ "$STATUS_SUBMIT" -eq 1 ]; then
  if [ -z "${AI_NAME:-}" ]; then
    echo "task_finalize: PR exists but review was NOT recorded: AI_NAME is required for the atomic status submission." >&2
    echo "task_finalize: re-run with AI_NAME=<task-owner>, or use --no-status-submit only for untracked housekeeping PRs." >&2
    exit 1
  fi
  AI_NAME="$AI_NAME" "$ROOT/scripts/ai-status.sh" submit_review "$TASK_ID" "$PR_NUMBER" \
    "Remote PR #$PR_NUMBER is open against $BASE_BRANCH: ${PR_URL:-GitHub URL unavailable}"
  echo "task_finalize: review submission recorded atomically for $TASK_ID"
fi
echo "task_finalize: wait for the merge into $BASE_BRANCH, then run:"
echo "  AI_NAME=<Owner> ./scripts/ai-status.sh done \"$TASK_ID\" \"<checkpoint>\""
