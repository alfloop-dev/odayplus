#!/usr/bin/env bash
# Point this clone's git hooks at the tracked .githooks/ directory.
#
#   ./scripts/git/install_hooks.sh
#
# core.hooksPath is per-clone local config, so it cannot be committed; every
# clone (and every `git worktree add`, which shares the config) needs this run
# once. task_start.sh calls it implicitly for workers.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if [ ! -d "$ROOT/.githooks" ]; then
  echo "install_hooks: $ROOT/.githooks does not exist" >&2
  exit 1
fi

chmod +x "$ROOT/.githooks/"* 2>/dev/null || true
git config core.hooksPath .githooks
echo "install_hooks: core.hooksPath -> .githooks"
ls -1 "$ROOT/.githooks" | sed 's/^/  - /'
