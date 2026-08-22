#!/usr/bin/env bash
# Retired mutable-checkout restart path.
#
# This script used to fetch and fast-forward the developer checkout before
# asking the cron watchdog to restart. That mixes deployment authority with a
# checkout that may contain staged, unstaged, or untracked WIP. Runtime rollout
# is now exclusively handled by scripts/orchestrator/rollout_supervisor_runtime.py
# against a clean source and an immutable named runtime worktree.

set -euo pipefail

printf '%s\n' \
  'restart-supervisor.sh is retired: it will not fetch, merge, reset, or restart a mutable developer checkout.' \
  'Use scripts/orchestrator/rollout_supervisor_runtime.py with the deployed runtime-link, runtime-parent, status-root, and service.' >&2
exit 2
