#!/usr/bin/env bash
# Drives the remaining governed GKE orders-history backfill for
# ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001. Supersedes v4.
#
# What changed from v4, and why.
#
# v4's queue was sized against acceptance criterion 3: N contiguous attested days
# yield N-28 eligible dates, an h28 window needs 28 consecutive eligible dates,
# so b3 (33 eligible) clears it and b4 (39) is margin. That arithmetic is correct
# and unchanged. It is also the WRONG BOUND for acceptance criterion 5, and every
# span number produced for this task so far was computed against it.
#
# Criterion 5 is "ODP-PRODUCTION-MODEL-REGISTRY-001 can resume training", and the
# registry does not reach the data through forecast_horizon_windows. It goes
# expand_forecast_horizon_rows -> prepare_model_rows -> _temporal_split ->
# _segment_validation. Three facts in that chain compound:
#
#   * _temporal_split splits on DISTINCT origin dates, not on row counts, so
#     adding stores never adds holdout dates;
#   * minimum_segment_rows (7 for forecastops) is applied to the HOLDOUT only,
#     and _segment_validation fails outright if NO store carries that many;
#   * a store contributes at most one row per origin date per qualifying horizon,
#     and the longer horizons' origins sit at the start of the span -- in the
#     training partition, not the holdout.
#
# So the holdout is about a fifth of the eligible origin dates and that fifth
# must be at least 7. Measured by running the registry's own functions over a
# uniform grid (runbook/criterion5-span-requirement-sweep.py, receipt
# criterion5_span_requirement.json): the governing gate is minimum_segment_rows
# and it needs 58 eligible dates = 86 contiguous attested days. v4's queue
# bottoms out at 2026-04-29 = 67 attested days = 39 eligible. It clears
# criterion 3 and cannot clear criterion 5.
#
# That measurement is a FLOOR, not a forecast: the grid is uniform, every store
# trading every date, which is the most favourable shape real data can take. Real
# stores break their islands, so the true requirement is at least this and
# probably more. Hence b8 rather than stopping at the arithmetic minimum, and
# hence b9 exists.
#
#   b2  2026-05-11..05-17  (already running, inherited from v4 -- only waited on)
#   b3  2026-05-05..05-11  -> 33 eligible   <-- criterion 3 clears here
#   b4  2026-04-29..05-05  -> 39
#   b5  2026-04-23..04-29  -> 45
#   b6  2026-04-17..04-23  -> 51
#   b7  2026-04-11..04-17  -> 57            (one short of the 58 floor)
#   b8  2026-04-05..04-11  -> 63            <-- criterion 5 clears here, +5 margin
#   s5  2026-07-18..07-23  (completes the post-split island)
#
# b9 (2026-03-30..04-05, -> 69) is created SUSPENDED and deliberately NOT queued.
# It is the reserve for the one way the floor can be too low: the holdout needs
# some single store trading continuously across the tail, and no projection
# settles that. Resume it only if the measured holdout falls short after b8.
#
# s5 stays last for v4's reason, unchanged: it extends the post-split island,
# which tops out at 21 days and can never reach 28 on its own, so it is coverage
# rather than critical path.
#
# Everything else is v4 verbatim, including the two invariants that took a
# failure each to learn: slices run SEQUENTIALLY because the private-pool node
# pool has exactly one node and does not scale up, and they wait SUSPENDED rather
# than merely queued because activeDeadlineSeconds counts from the Job's
# startTime rather than from pod scheduling. Kubernetes resets .status.startTime
# on resume, which is what preserves each slice's full budget.
#
# HANDOVER NOTE. v4 was killed by PID while b2 was RUNNING and v5 started in its
# place. Killing the driver does not touch the Job -- the driver only polls and
# resumes -- and v4 was stopped BEFORE it could log BACKFILL-DRIVER-DONE, so the
# finisher never saw a false handshake. v5 appends to the same
# /tmp/odp-backfill-driver.log and emits the same BACKFILL-DRIVER-DONE string to
# stderr, which is the exact string the finisher greps in that exact file.
set -uo pipefail

K=/snap/bin/google-cloud-cli.kubectl
NS=oday-dev
export KUBECONFIG=/tmp/odp-kubeconfig.yaml
JOBS=(oday-data-platform-orders-history-93cb9f94-b2
      oday-data-platform-orders-history-93cb9f94-b3
      oday-data-platform-orders-history-93cb9f94-b4
      oday-data-platform-orders-history-93cb9f94-b5
      oday-data-platform-orders-history-93cb9f94-b6
      oday-data-platform-orders-history-93cb9f94-b7
      oday-data-platform-orders-history-93cb9f94-b8
      oday-data-platform-orders-history-93cb9f94-s5)

# Progress goes to stderr, not stdout: wait_for_terminal's terminal status is
# read via command substitution, which would otherwise swallow every progress
# line into the variable instead of the driver log.
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*" >&2; }

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

job_status() {
  local name=$1 json
  json=$(timeout 60 $K get job -n "$NS" "$name" -o json 2>/dev/null) || { echo UNREACHABLE; return; }
  printf '%s' "$json" | python3 -c '
import json,sys
j=json.load(sys.stdin)
conds={c["type"]:c["status"] for c in j.get("status",{}).get("conditions",[])}
if conds.get("Complete")=="True": print("COMPLETE")
elif conds.get("Failed")=="True": print("FAILED")
elif j["spec"].get("suspend"): print("SUSPENDED")
elif j.get("status",{}).get("active"): print("RUNNING")
else: print("PENDING")
'
}

wait_for_terminal() {
  local name=$1 last="" st
  while true; do
    refresh_kubeconfig
    st=$(job_status "$name")
    if [ "$st" != "$last" ]; then log "job=$name status=$st"; last=$st; fi
    case "$st" in COMPLETE|FAILED) echo "$st"; return 0 ;; esac
    sleep 60
  done
}

log "driver v5 armed (criterion-5 span queue b2..b8,s5; supersedes v4 mid-flight at b2)"
summary=""
for name in "${JOBS[@]}"; do
  refresh_kubeconfig
  if [ "$(job_status "$name")" = "SUSPENDED" ]; then
    log "resuming $name"
    timeout 90 $K patch job -n "$NS" "$name" -p '{"spec":{"suspend":false}}' >/dev/null 2>&1 \
      || { log "FAILED to resume $name"; summary="$summary ${name##*-}=RESUME-FAILED"; continue; }
  fi
  st=$(wait_for_terminal "$name")
  summary="$summary ${name##*-}=$st"
done

log "BACKFILL-DRIVER-DONE$summary"
