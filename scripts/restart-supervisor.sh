#!/usr/bin/env bash
# Restart the supervisor onto the current tree, in the only order that works.
#
# The cron watchdog runs `run-supervisor-watchdog.sh --restart` every minute and
# respawns the supervisor from whatever the tree holds at that moment. So
# stopping first opens a window in which the watchdog restores the *old* code
# and wins the race against a manual restart.
#
# On 2026-08-20 that happened twice in one hour. A manual restart was reported
# as successful while its pid was already dead, and the watchdog's replacement
# loaded its modules 36 seconds before the fast-forward landed - so the process
# started "to pick up the fix" never had it.
#
# Order: advance the tree, then stop, then let the watchdog spawn. Then verify,
# rather than assume, using the provenance the supervisor stamps on itself.

set -euo pipefail

ROOT="${PANTHEON_ROOT:-/home/lupin/odayplus}"
STATE_FILE="$ROOT/.orchestrator/state.json"
WAIT_SECONDS="${RESTART_WAIT_SECONDS:-120}"

cd "$ROOT"

say() { printf '%s\n' "$*" >&2; }

# ---------------------------------------------------------------- 1. the tree
say "==> advancing the tree first"
git fetch origin dev --quiet

before="$(git rev-parse HEAD)"
if ! git merge --ff-only origin/dev >/dev/null 2>&1; then
    say "refusing to restart: the tree cannot fast-forward to origin/dev."
    say "resolve that first - restarting now would spawn from a tree nobody"
    say "has reconciled, which is the failure this script exists to prevent."
    exit 1
fi
target="$(git rev-parse HEAD)"

if [ "$before" = "$target" ]; then
    say "    already at ${target:0:8}"
else
    say "    ${before:0:8} -> ${target:0:8}"
fi

# ------------------------------------------------------------- 2. stop it
old_pid=""
if [ -f "$ROOT/.orchestrator/supervisor.pid" ]; then
    old_pid="$(cat "$ROOT/.orchestrator/supervisor.pid" 2>/dev/null || true)"
fi

if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    say "==> stopping supervisor pid $old_pid"
    kill -TERM "$old_pid" 2>/dev/null || true
else
    say "==> no live supervisor to stop"
fi

# ------------------------------------------- 3. let the watchdog respawn it
say "==> waiting for the watchdog to respawn (up to ${WAIT_SECONDS}s)"
deadline=$((SECONDS + WAIT_SECONDS))
new_pid=""
while [ "$SECONDS" -lt "$deadline" ]; do
    sleep 5
    candidate="$(cat "$ROOT/.orchestrator/supervisor.pid" 2>/dev/null || true)"
    if [ -n "$candidate" ] && [ "$candidate" != "$old_pid" ] && kill -0 "$candidate" 2>/dev/null; then
        new_pid="$candidate"
        break
    fi
done

if [ -z "$new_pid" ]; then
    say "no supervisor came back within ${WAIT_SECONDS}s."
    say "check the cron watchdog: crontab -l | grep supervisor-watchdog"
    exit 1
fi
say "    running as pid $new_pid"

# ------------------------------------------------------- 4. verify, do not assume
say "==> verifying it loaded the tree it was restarted for"
for _ in $(seq 1 12); do
    sleep 5
    loaded="$(python3 - "$STATE_FILE" <<'PY' 2>/dev/null || true
import json, sys
try:
    state = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
print(str((state.get("supervisor") or {}).get("loaded_code_sha") or ""))
PY
)"
    if [ -n "$loaded" ]; then
        break
    fi
done

if [ -z "$loaded" ]; then
    say "supervisor is running but has not yet stamped its provenance."
    say "it may predate OPS-RUNTIME-PROVENANCE-001; verify by hand."
    exit 0
fi

if [ "$loaded" = "$target" ]; then
    say "    loaded ${loaded:0:8} == tree ${target:0:8}"
    exit 0
fi

say "supervisor is running ${loaded:0:8} but the tree is ${target:0:8}."
say "it did not load what you restarted it for - most likely the tree moved"
say "again during the restart. Run this script again."
exit 1
