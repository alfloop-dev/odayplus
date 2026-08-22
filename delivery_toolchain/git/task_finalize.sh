#!/usr/bin/env bash
# Push the per-task branch, open (or re-use) its PR against dev, and submit it
# for review.
#
#   ./delivery_toolchain/git/task_finalize.sh "ODP-EXAMPLE-001"
#
# This keeps PR publication and the base branch out of the agent's hands:
# `.orchestrator/permission_broker.py` denies bare `gh pr create` to
# agents precisely because an agent-chosen `--base` can route work straight at
# the promotion target and bypass dev CI. This script always bases task PRs on
# the branch-workflow target.
#
# Auto-merge is deliberately not armed here. GitHubBus is the single lifecycle
# owner for that mutation and only arms a task PR after canonical reviewer
# approval. Publishing and merging must not race through two independent
# implementations.
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
Usage: delivery_toolchain/git/task_finalize.sh <TASK-ID> [--dry-run] [--base <branch>] [--no-status-submit]

  <TASK-ID>        e.g. ODP-EXAMPLE-001 (branch task/ODP-EXAMPLE-001)
  --dry-run        print what would run; touch neither origin nor GitHub
  --base <branch>  PR target (default: $PANTHEON_TASK_PR_BASE or dev)
  --no-status-submit  do not atomically move a tracked task to review (only for
                      supervisor housekeeping PRs which have no board task)
EOF
}

TASK_ID=""
DRY_RUN=0
STATUS_SUBMIT=1
BASE_BRANCH="${PANTHEON_TASK_PR_BASE:-dev}"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
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
  echo "task_finalize: run ./delivery_toolchain/git/task_start.sh \"$TASK_ID\" first." >&2
  exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "task_finalize: refusing to open a PR with uncommitted tracked changes:" >&2
  git status --short --untracked-files=no >&2
  echo "task_finalize: commit them via delivery_toolchain/git/worker_commit.py first." >&2
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/check_task_delivery_identity.py" \
  --repo "$ROOT" \
  --task-id "$TASK_ID" \
  --base "$BASE_REF" \
  --head HEAD \
  --expected-branch "$BRANCH" \
  --actual-branch "$CURRENT"

# Preflight the two CI failures that are decidable here in seconds but otherwise
# surface ~20 minutes later, after a review round has already been spent on them.
#
# `orchestrator` reruns the boundary inventory and `product` reruns ruff. A task
# that fails either cannot merge, so publishing the PR first does not buy
# anything -- it just moves the same refusal somewhere slower and more expensive.
# Both degrade to a warning when the tooling is unavailable: a missing linter is
# not evidence of clean code, but it must not wedge delivery either.

# Resolve an interpreter that can import ruff. Prefer the toolchain checkout's
# environment: these checks ship with this script, and the repository being
# finalized is not required to carry a virtualenv of its own. Ruff still reads
# the target repo's own config, because it is invoked with cwd = $ROOT.
TOOLCHAIN_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
resolve_py() {
  if [ -x "$TOOLCHAIN_ROOT/.venv/bin/python" ]; then echo "$TOOLCHAIN_ROOT/.venv/bin/python"; return 0; fi
  if [ -x "$ROOT/.venv/bin/python" ]; then echo "$ROOT/.venv/bin/python"; return 0; fi
  if command -v uv >/dev/null 2>&1; then echo "uv run --quiet python"; return 0; fi
  echo "python3"
}
PY_RUN="$(resolve_py)"

# 1. Boundary inventory. Adding files to a module without regenerating
#    docs/audits/code-boundary-inventory.csv is the single most common
#    `orchestrator` failure, and the fix is one deterministic command.
if [ -f "$ROOT/delivery_toolchain/governance/check_code_boundaries.py" ]; then
  boundary_out=""
  if boundary_out="$($PY_RUN "$ROOT/delivery_toolchain/governance/check_code_boundaries.py" 2>&1)"; then
    :
  else
    boundary_rc=$?
    case "$boundary_out" in
      *ModuleNotFoundError*|*"No module named"*)
        echo "task_finalize: warning: could not run the boundary check ($boundary_rc); skipping preflight." >&2 ;;
      *)
        echo "task_finalize: refusing to publish -- the code boundary check fails:" >&2
        echo "$boundary_out" | sed 's/^/  /' >&2
        echo "task_finalize: this is the CI 'orchestrator' job's check. Fix it here:" >&2
        echo "  $PY_RUN delivery_toolchain/governance/check_code_boundaries.py --write-inventory" >&2
        echo "task_finalize: then commit the regenerated inventory and re-run." >&2
        exit 1 ;;
    esac
  fi
fi

# 2. Lint, restricted to the Python files this branch actually changed. Linting
#    the whole tree would block a task on someone else's pre-existing findings.
changed_py="$(git diff --name-only "$BASE_REF...HEAD" -- '*.py' 2>/dev/null || true)"
lint_targets=""
for f in $changed_py; do
  [ -f "$ROOT/$f" ] && lint_targets="$lint_targets $f"
done
if [ -n "$lint_targets" ]; then
  lint_out=""
  if lint_out="$($PY_RUN -m ruff check $lint_targets 2>&1)"; then
    :
  else
    case "$lint_out" in
      *"No module named"*)
        echo "task_finalize: warning: ruff unavailable; skipping lint preflight." >&2 ;;
      *)
        echo "task_finalize: refusing to publish -- ruff reports errors in this task's own files:" >&2
        echo "$lint_out" | tail -20 | sed 's/^/  /' >&2
        echo "task_finalize: this is the CI 'product' job's lint step. Most are auto-fixable:" >&2
        echo "  $PY_RUN -m ruff check --fix$lint_targets" >&2
        exit 1 ;;
    esac
  fi
fi

# gh resolution mirrors delivery_toolchain/github/check_pr_merge_eligibility.py:
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
  echo "Opened by \`delivery_toolchain/git/task_finalize.sh\`. Merging still requires the"
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

# A draft PR cannot enter the assigned-reviewer flow, so publication makes an
# adopted draft ready before recording the canonical review submission.
IS_DRAFT="$("$GH" pr view "$PR_NUMBER" --json isDraft --jq '.isDraft' 2>/dev/null || echo false)"
if [ "$IS_DRAFT" = "true" ]; then
  echo "task_finalize: PR #$PR_NUMBER is a draft; marking ready for review"
  "$GH" pr ready "$PR_NUMBER" || echo "task_finalize: warning: gh pr ready failed" >&2
fi

PR_URL="$("$GH" pr view "$PR_NUMBER" --json url --jq '.url' 2>/dev/null || true)"
echo "task_finalize: PR #$PR_NUMBER ${PR_URL:-} for $TASK_ID"
if [ "$STATUS_SUBMIT" -eq 1 ]; then
  if [ -z "${AI_NAME:-}" ]; then
    echo "task_finalize: PR exists but review was NOT recorded: AI_NAME is required for the atomic status submission." >&2
    echo "task_finalize: re-run with AI_NAME=<task-owner>, or use --no-status-submit only for untracked housekeeping PRs." >&2
    exit 1
  fi
  AI_NAME="$AI_NAME" "${PANTHEON_STATUS_ROOT:-$ROOT}/scripts/ai-status.sh" submit_review "$TASK_ID" "$PR_NUMBER" \
    "Remote PR #$PR_NUMBER is open against $BASE_BRANCH: ${PR_URL:-GitHub URL unavailable}"
  echo "task_finalize: review submission recorded atomically for $TASK_ID"
fi
echo "task_finalize: wait for the merge into $BASE_BRANCH, then run:"
echo "  AI_NAME=<Owner> ./scripts/ai-status.sh done \"$TASK_ID\" \"<checkpoint>\""
