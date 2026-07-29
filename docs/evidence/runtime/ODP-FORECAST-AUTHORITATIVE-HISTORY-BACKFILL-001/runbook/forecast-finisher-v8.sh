#!/usr/bin/env bash
# Runs the post-backfill activation for ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001.
# Supersedes v7.
#
# What changed from v7, and why.
#
# Defect F: the activation copied every core relation with ON CONFLICT DO
# NOTHING while apps/data_platform/store.py updates those same rows in place on
# the same key, so an already-present target row could never be corrected.
# Measured before the fix: 16 953 stale target rows, one of them a transaction
# the source records as refunded/0.00 that the target still reported as
# succeeded/230.00 and forecast_training_view still counted as revenue. Fixed by
# giving each core relation a refresh_key equal to its primary key, and
# rehearsed on the live pair inside a rolled-back transaction -- drift 1 847 -> 0.
#
# Both of those measurements are PRE-activation. That is the same gap v7 closed
# for criterion 5: an acceptance package whose only evidence about content
# parity was taken against the target that the acceptance activation then
# replaced. It matters more here than it looks, because the entire before/after
# pair -- inventory_before, verify_after, inventory_after -- compares COVERAGE.
# Not one of those receipts compares the target's content to the source's, which
# is exactly why Defect F survived undetected until something asked directly.
#
# So v8 adds one stage after inventory: the same audit, unmodified, pointed at
# the freshly activated target and written as
# canonical_row_drift_audit_after_activation.json. It is read-only and measured
# at ~4 minutes, so it runs BEFORE the criterion-5 probe rather than after --
# a 45+ minute probe should not stand between activation and the receipt that
# says the activation converged. It inherits verify_after.json's rule (never
# written unless activate SUCCEEDED, because an "after activation" receipt must
# never describe a target that failed to activate) and it fails soft: a
# measurement is not a gate, so a failure is logged and the finisher still
# reports FORECAST-FINISHER-DONE with the activation receipts intact.
#
# What to look for in it: injected rows must stay 0, and drifted rows should now
# be 0 as well. A non-zero drift count after activation is not a Defect F
# regression -- the source keeps ingesting, so a row written between the copy and
# the audit reads as drift. Compare it against the pre-activation receipt's
# 16 953 and against the refresh counts in activation_receipt.json before
# concluding anything.
#
# What changed from v6, and why (v7, retained verbatim below).
#
# v6 ended at activate + verify + inventory. Those three answer criteria 3 and 4
# -- the view's own horizon windows and a before/after pair. They do not answer
# criterion 5, "ODP-PRODUCTION-MODEL-REGISTRY-001 can resume training", because
# the registry's contract is not the view's: FORECASTOPS_HORIZON_WEEKS starts at
# 4 weeks, so the shortest window the training path builds is 28 days, and the
# registry reaches the view through PostgresModelReadySource.inventory/load and
# prepare_model_rows rather than through forecast_horizon_windows. Section 9
# measured that contract and it FAILED, blocked at horizon_expansion_and_row_gates
# on a 13-day eligible span.
#
# That receipt is a PRE-activation measurement. Leaving it as the only criterion-5
# evidence would mean the acceptance package's answer to criterion 5 is a failure
# taken against a target that the acceptance activation then replaced. Re-running
# it by hand is not a plan either: the probe takes 45+ minutes and activation
# happens whenever the queue drains, which is hours away and very unlikely to
# coincide with a worker being awake. A criterion that only passes if someone
# happens to be watching is not evidence.
#
# So v7 adds one stage after inventory: the same criterion-5 probe, unmodified,
# pointed at the freshly activated target and written as
# training_contract_readiness_after_activation.json. It runs LAST so it cannot
# delay the acceptance receipts, it inherits verify_after.json's rule -- never
# written unless activate SUCCEEDED, because an "after activation" receipt must
# never describe a target that failed to activate -- and it fails soft: the probe
# is a measurement, not a gate, so a probe failure is logged and the finisher
# still reports FORECAST-FINISHER-DONE with the activation receipts intact.
#
# Budgets are its own, not run_stage's. run_stage escalates from 3600s, which is
# shorter than the probe's own measured 45-48 minute pre-activation runtime, and
# the activated target carries roughly two more months of history -- so a 3600s
# first attempt would reliably be killed just short of writing the receipt, and
# each retry would pay the full cost again. It escalates 3h -> 6h instead.
#
# What changed from v5, and why (v6, retained verbatim below).
#
# v5 removed clause (b) of the settling gate ("no non-terminal run still OWNS
# canonical_lineage rows") after establishing it was a permanent deadlock: run
# 069b0984 is dead, keeps its 4 752 lineage rows forever, and nothing will ever
# transition it. v5 kept clause (a) -- "every partition with a non-terminal run
# also has a SUCCEEDED run" -- on the reasoning that a killed partition that was
# never re-run is real incompleteness and must still block.
#
# Clause (a) is the SAME deadlock shape, and it is reachable rather than
# hypothetical. Driver v4 does not abort on a failed slice; it records the
# failure and moves to the next job, then always logs BACKFILL-DRIVER-DONE. So if
# any slice dies mid-partition -- exactly what Defect C did to -s1, and exactly
# what the raised activeDeadlineSeconds is defending against -- that partition
# keeps a non-terminal run with no SUCCEEDED sibling forever. The driver would
# finish, and the finisher would then spin on unfinished_partitions=1 until the
# host is rebooted, producing NO evidence at all about the 40+ days that landed
# correctly. That is precisely the outcome v5's own header condemns: blocking
# activation does not make the bad days eligible, it only prevents ever producing
# evidence about the days that are good.
#
# The honest reading, applied consistently this time: what makes activation
# unsafe is a partition still MID-FLIGHT, because refresh_key would mirror a
# genuinely live RUNNING status into the target and reproduce Defect B from the
# other direction. v5 already replaced clause (b) with a direct test of exactly
# that -- no orders-history Job Active in the cluster, measured against
# Kubernetes. That test subsumes clause (a): a partition whose run is
# non-terminal while no Job is Active is not in flight, it is abandoned. It is
# settled, just settled badly, and forecast_training_view already excludes its
# day fail-closed as SOURCE_RUN_NOT_COMPLETE.
#
# So v6 demotes clause (a) from a gate to a REPORTED FACT, the same treatment v5
# gave clause (b). The gate is now exactly one condition: driver done AND zero
# Active orders-history Jobs. Both remaining measurements fail closed on an
# unreachable cluster or database.
#
# Demoting a gate is only defensible if the cost it was guarding becomes visible
# instead of silent, so v6 also stops throwing that measurement away. v5 logged
# it to /tmp/odp-forecast-finisher.log, which is not committed and is not
# evidence. v6 writes settle_state.json into the staged evidence directory and
# mirrors it into the worktree alongside the activation receipt: which partitions
# were incomplete at activation time, which runs still own lineage, and the
# calendar dates that costs. An exact-head reviewer can then see what was true
# when the receipt was taken rather than having to trust that nothing was.
#
# IMPORTANT CAVEAT THAT MUST STAY IN THE EVIDENCE, not just here: transaction_daily
# and mature_daily apply no lineage_complete / source_run_complete filter, so a
# PARTIALLY ingested day still contributes a (short) daily row to later dates'
# prior_day_count_28. An abandoned partition therefore costs more than its own
# eligibility. That is why settle_state.json names the dates rather than only
# counting them: if a reported incomplete partition falls inside the span that
# carries the h28 windows, the correct response is to re-run that slice and
# re-activate, NOT to accept the receipt.
#
# Everything else is v5 verbatim: escalating wall-clock budgets, because a fixed
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

# The only gate. Measured against Kubernetes rather than inferred from SQL.
# Emits the count of Active orders-history Jobs, or UNREACHABLE (which fails
# closed at the call site, because UNREACHABLE != 0).
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

# Demoted clauses (a) and (b), captured together as a committed receipt instead
# of a log line. Non-gating: this runs once, immediately before activate.
write_settle_state() {
  source /tmp/odp-forecast-dsn.env
  ODP_ACTIVE_JOBS="$1" python3 - <<'PY' >"$STAGE/settle_state.json" 2>/dev/null
import json, os, psycopg
from datetime import datetime, timezone

out = {
    "artifact": "settle_state",
    "task": "ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001",
    "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "captured_by": "forecast-finisher-v6",
    "what_this_is": (
        "The state of the ingestion ledger at the moment activation was allowed to "
        "run. The finisher gates on exactly one condition -- driver done and zero "
        "Active orders-history Jobs in Kubernetes -- because that is the only "
        "condition that means 'no partition is mid-flight'. The two measurements "
        "below used to be gates (v4 clause b, v5 clause a); both were permanent "
        "deadlocks, because an abandoned run never transitions and nothing will "
        "ever clear them. They are reported here so the cost they were guarding is "
        "stated in the evidence rather than hidden behind a gate that never opens."
    ),
    "gate": {
        "active_orders_history_jobs": os.environ.get("ODP_ACTIVE_JOBS"),
        "driver_handshake": "BACKFILL-DRIVER-DONE observed in /tmp/odp-backfill-driver.log",
    },
    "read_only": True,
    "redaction": "run ids, partition keys and counts only; no store, member, order id or amount",
}

with psycopg.connect(os.environ["ODP_LEGACY_DATABASE_URL"]) as c, c.cursor() as cur:
    cur.execute("""
      SELECT n.run_id::text, n.partition_key, n.status, n.started_at
      FROM data_plane.ingestion_runs n
      WHERE n.source_kind='orders' AND n.status NOT IN ('SUCCEEDED','FAILED')
        AND NOT EXISTS (SELECT 1 FROM data_plane.ingestion_runs r
                        WHERE r.source_kind='orders' AND r.partition_key=n.partition_key
                          AND r.status='SUCCEEDED')
      ORDER BY n.started_at
    """)
    incomplete = [
        {"run_id": r[0], "partition_key": r[1], "status": r[2],
         "started_at": r[3].strftime("%Y-%m-%dT%H:%M:%SZ") if r[3] else None}
        for r in cur.fetchall()
    ]
    out["incomplete_partitions"] = {
        "count": len(incomplete),
        "rows": incomplete,
        "meaning": (
            "A partition with a non-terminal run and no SUCCEEDED sibling was never "
            "re-run to completion. Its own day cannot be an eligible target. It also "
            "still contributes a SHORT daily row to later dates' prior_day_count_28, "
            "because transaction_daily and mature_daily filter on neither "
            "lineage_complete nor source_run_complete. If any partition key below "
            "falls inside the span that carries the reported h28 windows, re-run that "
            "slice and re-activate; do not accept the receipt."
        ),
    }

    cur.execute("""
      SELECT r.run_id::text, r.status, r.partition_key, count(l.*)
      FROM data_plane.ingestion_runs r
      JOIN data_plane.canonical_lineage l ON l.run_id = r.run_id
      WHERE r.source_kind='orders' AND r.status NOT IN ('SUCCEEDED','FAILED')
      GROUP BY 1,2,3 ORDER BY 4 DESC
    """)
    out["lineage_owned_by_nonterminal_runs"] = {
        "rows": [{"run_id": r[0], "status": r[1], "partition_key": r[2],
                  "lineage_rows": r[3]} for r in cur.fetchall()],
        "meaning": (
            "canonical_lineage inserts are ON CONFLICT DO NOTHING on a content-derived "
            "source_snapshot_id, and there is no DELETE against canonical_lineage "
            "anywhere in apps/data_platform. So rows committed by a run that later died "
            "can never be re-pointed by re-reading the same records -- the regenerated "
            "key collides and the insert is discarded. This is Defect D. It permanently "
            "costs the affected dates their eligibility as targets, which is the correct "
            "fail-closed outcome and is why the span was grown below the split rather "
            "than bridged across it."
        ),
    }

    cur.execute("""
      SELECT min(partition_key), max(partition_key), count(*)
      FROM data_plane.ingestion_runs WHERE source_kind='orders' AND status='SUCCEEDED'
    """)
    lo, hi, n = cur.fetchone()
    out["succeeded_partitions"] = {"count": n, "min_partition_key": lo, "max_partition_key": hi}

print(json.dumps(out, indent=2, sort_keys=False))
PY
  if [ -s "$STAGE/settle_state.json" ]; then
    mirror settle_state.json
  else
    log "settle_state.json capture FAILED (database unreachable); continuing to activate"
  fi
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

# Defect F, re-measured against the target activation just produced. Read-only,
# ~4 minutes, own budget because run_stage drives a forecast_history_activation
# mode and this is a standalone probe.
run_drift_audit() {
  local name=canonical_row_drift_audit_after_activation.json
  local slog=/tmp/odp-drift-audit-after-activation.out
  local probe="$EV/runbook/canonical-row-drift-audit.py"
  local rc
  if [ ! -f "$probe" ]; then
    log "drift audit missing at $probe; skipping (activation receipts unaffected)"
    return 1
  fi
  source /tmp/odp-forecast-dsn.env
  cd "$WT" || return 1
  ODP_DRIFT_AUDIT_OUT="$STAGE/$name" timeout 3600 python3 "$probe" >"$slog" 2>&1
  rc=$?
  log "drift_audit rc=$rc"
  if [ "$rc" = "0" ]; then mirror "$name"; return 0; fi
  tail -5 "$slog" | sed "s/^/    drift_audit: /"
  log "drift audit did not complete; activation receipts stand, re-run runbook/canonical-row-drift-audit.py by hand"
  return 1
}

# Criterion 5, re-measured against the target activation just produced.
#
# Not run through run_stage: that helper invokes a forecast_history_activation
# mode and starts at a 3600s budget, and this probe's own measured runtime was
# already 45-48 minutes on a 13-day eligible span. Its own escalation, and its
# own failure semantics -- a measurement that fails leaves the activation
# receipts untouched and must not fail the finisher.
run_criterion5_probe() {
  local name=training_contract_readiness_after_activation.json
  local slog=/tmp/odp-training-contract-after-activation.out
  local probe="$EV/runbook/training-contract-readiness-probe.py"
  local attempt=0 budget rc
  if [ ! -f "$probe" ]; then
    log "criterion-5 probe missing at $probe; skipping (activation receipts unaffected)"
    return 1
  fi
  for budget in 10800 21600; do
    attempt=$((attempt + 1))
    source /tmp/odp-forecast-dsn.env
    cd "$WT" || return 1
    PROBE_OUT="$STAGE/$name" timeout "$budget" python3 "$probe" >"$slog" 2>&1
    rc=$?
    log "criterion5 attempt=$attempt budget=${budget}s rc=$rc"
    if [ "$rc" = "0" ]; then mirror "$name"; return 0; fi
    [ "$rc" = "124" ] && log "criterion5 exceeded ${budget}s; widening budget"
    tail -5 "$slog" | sed "s/^/    criterion5: /"
    sleep 120
  done
  log "criterion-5 probe did not complete; activation receipts stand, re-run runbook/training-contract-probe-runner.sh by hand"
  return 1
}

log "finisher armed (v8: v6 gate unchanged; adds post-activation Defect F drift audit before the criterion-5 re-measurement)"
mkdir -p "$STAGE"
active=""
while true; do
  if grep -q BACKFILL-DRIVER-DONE "$DRIVER_LOG" 2>/dev/null; then
    active=$(active_orders_jobs)
    log "driver done; active_orders_jobs=$active"
    [ "$active" = "0" ] && break
  fi
  sleep 120
done

log "no orders-history Job in flight; capturing settle state"
write_settle_state "$active"

log "backfill settled; running activate"
if run_stage activate activation_receipt.json /tmp/odp-activate.out; then
  run_stage verify verify_after.json /tmp/odp-verify.out
  run_stage inventory inventory_after.json /tmp/odp-inventory.out
  # Last, and only on a successful activation: an "after activation" receipt must
  # never describe a target that failed to activate.
  # Cheap and read-only, so it runs first: the receipt that says the activation
  # actually converged should not wait behind a 45-minute probe.
  log "acceptance receipts captured; re-measuring Defect F drift against the activated target"
  run_drift_audit
  log "re-measuring criterion 5 against the activated target"
  run_criterion5_probe
else
  # Do NOT write verify_after.json: that file is an acceptance artifact and must
  # never describe a target that failed to activate.
  log "ACTIVATE FAILED after ${#BUDGETS[@]} attempts; capturing failed-state probe only"
  run_stage verify verify_state_after_failed_activation.json /tmp/odp-verify-failed.out
fi

log "FORECAST-FINISHER-DONE"
