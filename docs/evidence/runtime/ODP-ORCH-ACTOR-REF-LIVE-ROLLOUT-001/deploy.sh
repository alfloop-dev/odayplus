#!/bin/bash
# Deploy the exact PR #496 merged scripts/ai_status.py into both live roots.
#
# The source is materialised straight out of the merge commit's blob, not
# copied from a working tree, so nothing local can leak into it. Each target is
# backed up first; every other dirty file in each root is left untouched.
#
# Usage: deploy.sh <merge-commit> <backup-dir>
set -euo pipefail

MERGE="${1:?merge commit required}"
BACKUP_DIR="${2:?backup dir required}"
WORKTREE="/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-orch-actor-ref-live-rollout-001"
ROOTS=(/home/lupin/oday-plus /home/lupin/oday-plus-supervisor-live)
SRC=/tmp/ai_status.merged-${MERGE:0:8}.py

cd "$WORKTREE"
mkdir -p "$BACKUP_DIR"

echo "# Deployment transcript — exact merged scripts/ai_status.py -> both live roots"
echo "# host date : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "# merge     : $MERGE"
echo "# backups   : $BACKUP_DIR"
echo

echo "## source materialised from the merge commit blob (no working-tree copy)"
BLOB=$(git rev-parse "$MERGE:scripts/ai_status.py")
git cat-file blob "$BLOB" > "$SRC"
MERGED_SHA=$(sha256sum "$SRC" | cut -d' ' -f1)
echo "  blob        : $BLOB"
echo "  materialised: $SRC"
echo "  sha256      : $MERGED_SHA"
echo "  bytes       : $(wc -c < "$SRC")"
echo

echo "## supervisor state BEFORE"
SUP_PID_BEFORE=$(systemctl --user show pantheon-supervisor.service -p MainPID --value)
echo "  MainPID              : $SUP_PID_BEFORE"
echo "  ActiveState/SubState : $(systemctl --user show pantheon-supervisor.service -p ActiveState --value)/$(systemctl --user show pantheon-supervisor.service -p SubState --value)"
echo "  ExecMainStartTimestamp: $(systemctl --user show pantheon-supervisor.service -p ExecMainStartTimestamp --value)"
echo "  NRestarts            : $(systemctl --user show pantheon-supervisor.service -p NRestarts --value)"
echo "  process cwd          : $(readlink -f /proc/$SUP_PID_BEFORE/cwd)"
echo

for ROOT in "${ROOTS[@]}"; do
  NAME=$(basename "$ROOT")
  TARGET="$ROOT/scripts/ai_status.py"
  echo "## target: $TARGET"
  echo "  git branch      : $(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
  echo "  git HEAD        : $(git -C "$ROOT" rev-parse HEAD)"
  echo "  dirty files     : $(git -C "$ROOT" status --porcelain | wc -l)"
  git -C "$ROOT" status --porcelain > "$BACKUP_DIR/$NAME.dirty-before.txt"
  echo "  dirty inventory : $BACKUP_DIR/$NAME.dirty-before.txt"
  echo "  sha256 BEFORE   : $(sha256sum "$TARGET" | cut -d' ' -f1)"
  echo "  bytes  BEFORE   : $(wc -c < "$TARGET")"
  echo "  mode   BEFORE   : $(stat -c '%a' "$TARGET")"
  cp -p "$TARGET" "$BACKUP_DIR/$NAME.ai_status.py.bak"
  echo "  backup          : $BACKUP_DIR/$NAME.ai_status.py.bak"
  echo "  backup sha256   : $(sha256sum "$BACKUP_DIR/$NAME.ai_status.py.bak" | cut -d' ' -f1)"

  MODE=$(stat -c '%a' "$TARGET")
  install -m "$MODE" "$SRC" "$TARGET"

  AFTER=$(sha256sum "$TARGET" | cut -d' ' -f1)
  echo "  sha256 AFTER    : $AFTER"
  echo "  bytes  AFTER    : $(wc -c < "$TARGET")"
  echo "  mode   AFTER    : $(stat -c '%a' "$TARGET")"
  echo "  equals merged   : $([ "$AFTER" = "$MERGED_SHA" ] && echo True || echo False)"
  echo "  cmp -s merged   : $(cmp -s "$SRC" "$TARGET" && echo 'identical' || echo 'DIFFERENT')"
  git -C "$ROOT" status --porcelain > "$BACKUP_DIR/$NAME.dirty-after.txt"
  echo "  dirty files after: $(wc -l < "$BACKUP_DIR/$NAME.dirty-after.txt")"
  echo "  unrelated dirty changes preserved (diff of the two inventories, ignoring scripts/ai_status.py):"
  if diff <(grep -v 'scripts/ai_status.py' "$BACKUP_DIR/$NAME.dirty-before.txt") \
          <(grep -v 'scripts/ai_status.py' "$BACKUP_DIR/$NAME.dirty-after.txt"); then
    echo "    identical — no other file changed state"
  else
    echo "    DIFFERENCE ABOVE — investigate"
  fi
  echo
done

echo "## supervisor state AFTER"
SUP_PID_AFTER=$(systemctl --user show pantheon-supervisor.service -p MainPID --value)
echo "  MainPID              : $SUP_PID_AFTER"
echo "  ActiveState/SubState : $(systemctl --user show pantheon-supervisor.service -p ActiveState --value)/$(systemctl --user show pantheon-supervisor.service -p SubState --value)"
echo "  ExecMainStartTimestamp: $(systemctl --user show pantheon-supervisor.service -p ExecMainStartTimestamp --value)"
echo "  NRestarts            : $(systemctl --user show pantheon-supervisor.service -p NRestarts --value)"
echo "  PID unchanged        : $([ "$SUP_PID_BEFORE" = "$SUP_PID_AFTER" ] && echo True || echo False)"
echo "  still running        : $(ps -p "$SUP_PID_AFTER" -o cmd= || echo 'GONE')"
echo
echo "# No systemctl start/stop/restart/reload was issued by this script."
