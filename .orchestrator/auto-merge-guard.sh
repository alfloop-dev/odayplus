#!/usr/bin/env bash
# Durable auto-merge loop: merges green task PRs into dev every INTERVAL seconds.
# Single-instance via flock; started/kept-alive by crontab. Env set so /usr/bin/gh
# finds the user's gh auth (ajoe734).
set -uo pipefail
export HOME="${HOME:-/home/lupin}"
export GH_CONFIG_DIR="${GH_CONFIG_DIR:-$HOME/.config/gh}"
REPO_DIR=/home/lupin/oday-plus
LOG="$REPO_DIR/.orchestrator/logs/auto-merge.log"
LOCK="$REPO_DIR/.orchestrator/auto-merge.lock"
INTERVAL=180

exec 9>"$LOCK"
flock -n 9 || { exit 0; }   # another instance already running

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] auto-merge guard started pid=$$" >> "$LOG"
while true; do
  python3 "$REPO_DIR/.orchestrator/auto_merge_green_prs.py" --max 5 >> "$LOG" 2>&1
  sleep "$INTERVAL"
done
