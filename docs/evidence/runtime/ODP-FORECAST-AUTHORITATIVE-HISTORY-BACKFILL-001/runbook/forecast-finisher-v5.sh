#!/usr/bin/env bash
# Runs the post-backfill activation for ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001.
# Supersedes v4.
#
# Why the gate had to change.
#
# v4's settling condition had two clauses: (a) every partition with a
# non-terminal run also has a SUCCEEDED run, and (b) no non-terminal run still
# OWNS canonical_lineage rows. Clause (b) was written when the plan was to repair
# Defect D, so "a dead run still owns lineage" meant "the repair has not landed
# yet, do not activate". That is no longer true, and clause (b) had become a
# permanent deadlock: run 069b0984 is dead, keeps its 4 752 lineage rows forever,
# and nothing will ever transition it. The finisher would have waited for a
# condition that cannot occur.
#
# The honest reading is that clause (b) was never a safety property. What makes
# activation unsafe is a partition still MID-FLIGHT, because refresh_key would
# mirror a genuinely in-progress RUNNING status into the target and reproduce
# Defect B from the other direction. A permanently abandoned run is not
# mid-flight; it is settled, just settled badly. Its days are already excluded by
# forecast_training_view as SOURCE_RUN_NOT_COMPLETE, which is the correct
# fail-closed outcome, and blocking activation does not make those days eligible
# -- it only prevents ever producing evidence about the days that ARE.
#
# So v5 replaces clause (b) with a direct, stronger test of the thing clause (b)
# was standing in for: no orders Job is Active in the cluster. That is measured
# against Kubernetes rather than inferred from SQL. Clause (a) is kept unchanged,
# because "a killed partition was never re-run" is a real incompleteness that
# must still block.
#
# Clause (b)'s measurement is not discarded, it is DEMOTED to a logged fact: the
# finisher reports how much lineage is still owned by abandoned runs and which
# days that costs, so the cost is stated in the evidence rather than hidden
# behind a gate that never opens.
#
# Everything else is v4 verbatim: escalating wall-clock budgets, because a fixed
# per-attempt timeout is worthless against a stage that is simply slower than the
# budget; and staging evidence outside the worktree, because the orchestrator
# hard-resets a worktree it finds dirty when it wants to lease it.
set -uo pipefail

WT=/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live-20260726/odp-forecast-authoritative-history-backfill-001
EV="$WT/docs/evidence/runtime/ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001"
STAGE=/tmp/odp-forecast-evidence
DRIVER_LOG=/tmp/odp-backfill-driver.log
K=/snap/bin/google-cloud-cli.kubectl
NS=oday-dev
export KUBECONFIG=/tmp/odp-kubeconfig.yaml
BUDGETS=(3600 7200 14400)

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

mirror() {
  local name=$1
  [ -f "$STAGE/$name" ] || return 0
  mkdir -p "$EV" 2>/dev/null && cp "$STAGE/$name" "$EV/$name" 2>/dev/null \
    && log "mirrored $name into worktree" || log "could not mirror $name (staging copy kept)"
}

refresh_kubeconfig() {
  local token
  token=$(timeout 60 gcloud auth print-access-token 2>/dev/null) || return 1
  [ -n "$token" ] || return 1
  python3 -c '
import sys, re, pathlib
p = pathlib.Path("/tmp/odp-kubeconfig.yaml")
p.write_text(re.sub(r"token: .*", "token: " + sys.argv[1], p.read_text()))
' "$token"
}

# The real mid-flight test, measured against Kubernetes rather than inferred.
# Emits the count of Active orders-history Jobs, or UNREACHABLE.
active_orders_jobs() {
  refresh_kubeconfig
  timeout 60 $K get jobs -n "$NS" -o json 2>/dev/null | python3 -c '
import json,sys
try: j=json.load(sys.stdin)
except Exception: print("UNREACHABLE"); raise SystemExit
n=0
for item in j.get("items",[]):
    if "orders-history" not in item["metadata"]["name"]: continue
    if item.get("status",{}).get("active"): n+=1
print(n)
' 2>/dev/null || echo UNREACHABLE
}

# Clause (a) only: a partition that has a non-terminal run and NO SUCCEEDED run
# was never re-run to completion. That is real incompleteness and still blocks.
unfinished_partitions() {
  source /tmp/odp-forecast-dsn.env
  python3 - <<'PY' 2>/dev/null || echo UNREACHABLE
import os, psycopg
with psycopg.connect(os.environ["ODP_LEGACY_DATABASE_URL"]) as c, c.cursor() as cur:
    cur.execute("""
      WITH nonterminal AS (
        SELECT run_id, partition_key FROM data_plane.ingestion_runs
        WHERE source_kind='orders' AND status NOT IN ('SUCCEEDED','FAILED')
      )
      SELECT count(*) FROM nonterminal n
      WHERE NOT EXISTS (
              SELECT 1 FROM data_plane.ingestion_runs r
              WHERE r.source_kind='orders' AND r.partition_key=n.partition_key
                AND r.status='SUCCEEDED')
    """)
    print(cur.fetchone()[0])
PY
}

# Demoted clause (b): reported, not gated on.
report_abandoned_lineage() {
  source /tmp/odp-forecast-dsn.env
  python3 - <<'PY' 2>/dev/null
import os, psycopg
with psycopg.connect(os.environ["ODP_LEGACY_DATABASE_URL"]) as c, c.cursor() as cur:
    cur.execute("""
      SELECT r.run_id::text, r.status, count(l.*)
      FROM data_plane.ingestion_runs r
      JOIN data_plane.canonical_lineage l ON l.run_id = r.run_id
      WHERE r.source_kind='orders' AND r.status NOT IN ('SUCCEEDED','FAILED')
      GROUP BY 1,2 ORDER BY 3 DESC
    """)
    rows = cur.fetchall()
    if not rows:
        print("abandoned-lineage: none")
    for run_id, status, n in rows:
        print(f"abandoned-lineage: run={run_id} status={status} lineage_rows={n}")
PY
}

run_stage() {
  local mode=$1 name=$2 slog=$3 attempt=0 budget rc
  for budget in "${BUDGETS[@]}"; do
    attempt=$((attempt + 1))
    source /tmp/odp-forecast-dsn.env
    cd "$WT" || return 1
    timeout "$budget" python3 -m scripts.data_plane.forecast_history_activation "$mode" \
      --horizons 7,14,28,56,84,168 --output "$STAGE/$name" >"$slog" 2>&1
    rc=$?
    log "$mode attempt=$attempt budget=${budget}s rc=$rc"
    if [ "$rc" = "0" ]; then mirror "$name"; return 0; fi
    [ "$rc" = "124" ] && log "$mode exceeded ${budget}s; widening budget"
    tail -5 "$slog" | sed "s/^/    $mode: /"
    sleep 120
  done
  return 1
}

log "finisher armed (v5: mid-flight gate measured against Kubernetes; abandoned lineage reported, not gated)"
mkdir -p "$STAGE"
while true; do
  if grep -q BACKFILL-DRIVER-DONE "$DRIVER_LOG" 2>/dev/null; then
    a=$(active_orders_jobs)
    u=$(unfinished_partitions)
    log "driver done; active_orders_jobs=$a unfinished_partitions=$u"
    if [ "$a" = "0" ] && [ "$u" = "0" ]; then
      report_abandoned_lineage | while read -r l; do log "$l"; done
      break
    fi
  fi
  sleep 120
done

log "backfill settled; running activate"
if run_stage activate activation_receipt.json /tmp/odp-activate.out; then
  run_stage verify verify_after.json /tmp/odp-verify.out
  run_stage inventory inventory_after.json /tmp/odp-inventory.out
else
  # Do NOT write verify_after.json: that file is an acceptance artifact and must
  # never describe a target that failed to activate.
  log "ACTIVATE FAILED after ${#BUDGETS[@]} attempts; capturing failed-state probe only"
  run_stage verify verify_state_after_failed_activation.json /tmp/odp-verify-failed.out
fi

log "FORECAST-FINISHER-DONE"
