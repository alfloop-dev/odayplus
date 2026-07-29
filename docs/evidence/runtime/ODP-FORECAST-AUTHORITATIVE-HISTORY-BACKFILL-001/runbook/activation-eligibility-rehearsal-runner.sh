#!/usr/bin/env bash
# Detached runner for the activation-eligibility rehearsal.
#
# Three full scans of model_ready.forecast_training_view plus two activation
# chains -- far longer than a worker turn. Section 9 already paid for the lesson:
# run as an ordinary child of the worker, the process dies with the worker and
# PostgreSQL does not notice, leaving a backend still spilling to disk and
# competing with its own replacement. setsid + nohup + disown is the pattern the
# four keepers use, so the measurement survives a worker exit.
#
# NOT read-only: it writes the PG16 target inside transactions it rolls back.
# Nothing is committed and no job is touched, but this is the reason it must not
# be run concurrently with the finisher's real `activate` -- both take the same
# advisory lock, which serialises them rather than corrupting anything.
#
# Usage:
#   setsid nohup <this script> >>/tmp/odp-activation-eligibility-rehearsal.log 2>&1 </dev/null & disown
set -uo pipefail

WORKTREE=${WORKTREE:-/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live-20260726/odp-forecast-authoritative-history-backfill-001}
EVIDENCE_REL=docs/evidence/runtime/ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001
PROBE="$WORKTREE/$EVIDENCE_REL/runbook/activation-eligibility-rehearsal.py"

# Staged outside the worktree, like the finisher's evidence, because the
# supervisor hard-resets a dirty worktree.
export PROBE_OUT=${PROBE_OUT:-/tmp/odp-forecast-evidence-stage/activation_eligibility_rehearsal.json}

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

mkdir -p "$(dirname "$PROBE_OUT")"
# shellcheck disable=SC1091
source /tmp/odp-forecast-dsn.env

log "activation-eligibility rehearsal starting (out=$PROBE_OUT)"
cd "$WORKTREE" || { log "worktree missing: $WORKTREE"; exit 1; }
python3 "$PROBE"
rc=$?
log "activation-eligibility rehearsal exited rc=$rc"
exit "$rc"
