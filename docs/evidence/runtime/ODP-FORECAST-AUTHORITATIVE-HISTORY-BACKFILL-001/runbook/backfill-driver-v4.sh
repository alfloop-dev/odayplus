#!/usr/bin/env bash
# Drives the remaining governed GKE orders-history backfill for
# ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001. Supersedes v3.
#
# What changed from v3, and why.
#
# v3's queue was s3 -> s4 -> s5: close the measured source gap 2026-07-06..07-22
# and accept that the 28-day horizon was unreachable until a human approved the
# section 6 destructive delete. That premise is gone. The upstream Atlas orders
# collection holds 2 170 979 documents back to 2024-06-26; the 2026-05-23 floor
# in fongniao_raw.raw_orders is a window-clamp artifact of render.py's
# "end - 62 days" default, not a property of the source. See README section 7
# and upstream_source_depth_probe.json.
#
# So the attested span does not have to be repaired ACROSS the Defect D split at
# 07-05/07-06. It can simply be grown BELOW it, by ordinary governed ingestion of
# real records. N contiguous attested days yield N-28 eligible dates, so the
# queue below reaches 33 eligible dates at b3 and clears the 28-day horizon with
# no delete, no human-gated action, and no change to the view.
#
# Queue order is deliberate: b1..b3 come before s5 because they are what unblocks
# acceptance criterion 3, while s5 extends the post-split island that can never
# reach 28 days on its own. b4 is margin and runs before s5 too, because the
# arithmetic below is a GLOBAL span and per-store streaks can be shorter -- a
# store idle for a day produces no row and breaks its own streak.
#
#   s4  2026-07-12..07-18  (already running, inherited from v3 -- only waited on)
#   b1  2026-05-17..05-23  -> 21 eligible dates
#   b2  2026-05-11..05-17  -> 27
#   b3  2026-05-05..05-11  -> 33   <-- criterion 3 clears here
#   b4  2026-04-29..05-05  -> 39   (margin for per-store streaks)
#   s5  2026-07-18..07-23  (completes the post-split island)
#
# Everything else is v3 verbatim, including the two invariants that took a
# failure each to learn: slices run SEQUENTIALLY because the private-pool node
# pool has exactly one node and does not scale up, and they wait SUSPENDED rather
# than merely queued because activeDeadlineSeconds counts from the Job's
# startTime rather than from pod scheduling -- a queued-but-unsuspended job would
# burn its whole 4h budget sitting Pending. Kubernetes resets .status.startTime
# on resume, which is what preserves each slice's full budget.
set -uo pipefail

K=/snap/bin/google-cloud-cli.kubectl
NS=oday-dev
export KUBECONFIG=/tmp/odp-kubeconfig.yaml
JOBS=(oday-data-platform-orders-history-93cb9f94-s4
      oday-data-platform-orders-history-93cb9f94-b1
      oday-data-platform-orders-history-93cb9f94-b2
      oday-data-platform-orders-history-93cb9f94-b3
      oday-data-platform-orders-history-93cb9f94-b4
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

log "driver v4 armed (backwards-extension queue; supersedes v3)"
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
