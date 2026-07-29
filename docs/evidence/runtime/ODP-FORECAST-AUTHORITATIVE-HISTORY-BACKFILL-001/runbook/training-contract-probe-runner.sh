#!/usr/bin/env bash
# Detached runner for the criterion-5 training-contract readiness probe.
#
# Why this exists at all. The probe walks model_ready.forecast_training_view
# twice -- once for a shape count, then again through the registry's own loader
# -- and that takes far longer than a worker turn. Run as an ordinary child of
# the worker it dies with the worker, and the PostgreSQL backend it left behind
# keeps executing: the 2026-07-28T23:47Z attempt did exactly that, and the
# orphan was still spilling to disk (`BufFileRead`) twenty minutes later,
# competing with the replacement run for I/O on the same view. setsid + nohup +
# disown is the pattern the four keepers already use here, so the measurement
# survives a worker exit and the next wake simply collects the receipt.
#
# Read-only: SELECTs only, no writes, no DDL, no job mutation. Safe to run
# while a backfill slice is in flight -- it reads the PG16 activation target
# while the slices write the PG15 source.
#
# Usage:
#   setsid nohup <this script> >>/tmp/odp-training-contract-probe.log 2>&1 </dev/null & disown
set -uo pipefail

WORKTREE=${WORKTREE:-/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live-20260726/odp-forecast-authoritative-history-backfill-001}
EVIDENCE_REL=docs/evidence/runtime/ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001
PROBE="$WORKTREE/$EVIDENCE_REL/runbook/training-contract-readiness-probe.py"

# Staged outside the worktree, like the finisher's evidence, because the
# supervisor hard-resets a dirty worktree.
export PROBE_OUT=${PROBE_OUT:-/tmp/odp-forecast-evidence-stage/training_contract_readiness.json}

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

mkdir -p "$(dirname "$PROBE_OUT")"
# shellcheck disable=SC1091
source /tmp/odp-forecast-dsn.env

log "criterion-5 training-contract probe starting (out=$PROBE_OUT)"
cd "$WORKTREE" || { log "worktree missing: $WORKTREE"; exit 1; }
python3 "$PROBE"
rc=$?
log "criterion-5 training-contract probe exited rc=$rc"
exit "$rc"
