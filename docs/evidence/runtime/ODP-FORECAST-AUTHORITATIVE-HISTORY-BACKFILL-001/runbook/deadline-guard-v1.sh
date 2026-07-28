#!/usr/bin/env bash
# Deadline guard for the governed orders-history backfill slices.
#
# WHY THIS EXISTS
# ---------------
# Defect D -- the one permanent, unrepairable-without-human-approval damage in
# this whole task -- was created by exactly one event: a slice hit
# activeDeadlineSeconds mid-partition. The resumed run then reconciled only its
# own records, leaving 4752 canonical_lineage rows attested to a dead run that no
# re-run can re-point (the insert is ON CONFLICT DO NOTHING on a content-derived
# key, and nothing in apps/data_platform/ ever DELETEs from canonical_lineage).
# That cost 2026-07-05 and 2026-07-06 permanently.
#
# The 19:05Z raise from 14400 to 28800 was a manual, one-off application of the
# obvious remedy, decided by watching a partition run. The remaining slices
# (-b1..-b4, -s5) run unattended for many hours. Nothing was watching them.
#
# The measurement that makes this non-optional: lineage projection is 87-94% of
# every partition's wall-clock, and its throughput is NOT stable -- it has
# already fallen from ~700 rows/min (early -s1) to 210-270 rows/min (-s3 tail,
# -s4). Six partitions fit the 480 min budget at 250 rows/min and do NOT fit it
# at 100. So whether a slice survives its deadline depends on a drifting rate
# nobody controls. See runbook/lineage-throughput-probe.py and
# lineage_projection_throughput.json.
#
# WHAT IT DOES
# ------------
# Every 5 minutes: find the single Active backfill slice, and if it is within
# EXTEND_WHEN_REMAINING of its deadline, extend the deadline by EXTEND_BY --
# up to a hard cap.
#
# WHY EXTENDING IS SAFE
# ---------------------
# activeDeadlineSeconds is one of the few mutable Job spec fields. The patch was
# measured against a RUNNING Job at 19:05Z with zero pod disruption: same pod,
# RESTARTS 0, age unbroken. It cannot alter, delete or admit a record; it can
# only change how long a slice is allowed to take. It is reversible by patching
# it back.
#
# WHY IT EXTENDS EVEN WHEN LIVENESS CANNOT BE PROVEN
# --------------------------------------------------
# The tempting design is "extend only if provably progressing, otherwise let it
# die". That is wrong here, and the asymmetry is the whole point:
#
#   - A hung job that gets killed still creates a Defect D. It has already
#     committed lineage rows; the kill is what strands them. There is no such
#     thing as a safe deadline kill for this workload.
#   - An over-extended hung job costs node time, blocks the queue, and is
#     trivially recovered by suspending it by hand. HARD_CAP_SECONDS bounds it.
#
# So the bias is: extend, and make the doubt loud. Liveness is still measured on
# every decision and recorded in the log, because a slice that stops projecting
# is worth a human look even though it does not change what the guard does.
# max(projected_at) is the ONLY external liveness signal available: a RUNNING
# ingestion_runs row reports valid_loaded = 0 until it finishes, and
# core.transactions.ingested_at lands in one bulk commit in the first minute.
#
# The guard NEVER suspends, deletes, resumes or otherwise touches a Job. Its one
# and only mutation is raising activeDeadlineSeconds on an Active slice.
#
# Launch (a plain `( ... & )` subshell gets reaped):
#   setsid nohup /tmp/odp-deadline-guard.sh >>/tmp/odp-deadline-guard.log 2>&1 </dev/null & disown
set -uo pipefail

K=/snap/bin/google-cloud-cli.kubectl
NS=oday-dev
export KUBECONFIG=/tmp/odp-kubeconfig.yaml

# Only this task's slices. The namespace also holds 4-day-old orders-history
# Jobs from other work that this guard must never touch.
PREFIX=oday-data-platform-orders-history-93cb9f94-

POLL_SECONDS=300
EXTEND_WHEN_REMAINING=2700   # 45 min: >>1 partition at the worst measured rate
EXTEND_BY=7200               # 2h per extension
HARD_CAP_SECONDS=57600       # 16h; a slice needing more than this is broken
LIVENESS_STALE_SECONDS=900   # 15 min without a new lineage row = report doubt

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

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

# Emits "<name> <deadline> <elapsed_seconds>" for the one Active slice, or nothing.
active_slice() {
  timeout 90 $K get jobs -n "$NS" -o json 2>/dev/null | python3 -c '
import json, sys
from datetime import datetime, timezone
prefix = sys.argv[1]
try:
    jobs = json.load(sys.stdin).get("items", [])
except Exception:
    sys.exit(0)
now = datetime.now(timezone.utc)
for j in jobs:
    name = j["metadata"]["name"]
    if not name.startswith(prefix):
        continue
    st = j.get("status", {})
    if j["spec"].get("suspend") or not st.get("active"):
        continue
    start = st.get("startTime")
    dl = j["spec"].get("activeDeadlineSeconds")
    if not start or not dl:
        continue
    elapsed = (now - datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds()
    print(f"{name} {dl} {int(elapsed)}")
' "$PREFIX"
}

# Seconds since the newest canonical_lineage row, or "UNKNOWN".
lineage_age_seconds() {
  # shellcheck disable=SC1091
  [ -f /tmp/odp-forecast-dsn.env ] && . /tmp/odp-forecast-dsn.env
  timeout 60 python3 -c '
import os, psycopg
try:
    with psycopg.connect(os.environ["ODP_LEGACY_DATABASE_URL"], connect_timeout=15) as c, c.cursor() as cur:
        cur.execute("SELECT extract(epoch FROM now() - max(projected_at)) FROM data_plane.canonical_lineage")
        v = cur.fetchone()[0]
        print("UNKNOWN" if v is None else int(v))
except Exception:
    print("UNKNOWN")
' 2>/dev/null || echo UNKNOWN
}

log "deadline guard v1 armed (poll=${POLL_SECONDS}s extend_when_remaining=${EXTEND_WHEN_REMAINING}s extend_by=${EXTEND_BY}s cap=${HARD_CAP_SECONDS}s)"

while true; do
  refresh_kubeconfig
  read -r name deadline elapsed <<<"$(active_slice)"

  if [ -n "${name:-}" ]; then
    remaining=$(( deadline - elapsed ))
    if [ "$remaining" -le "$EXTEND_WHEN_REMAINING" ]; then
      age=$(lineage_age_seconds)
      if [ "$age" = "UNKNOWN" ]; then
        liveness="liveness=UNKNOWN(db-unreachable)"
      elif [ "$age" -le "$LIVENESS_STALE_SECONDS" ]; then
        liveness="liveness=OK(${age}s)"
      else
        liveness="liveness=STALE(${age}s)"
      fi

      if [ "$deadline" -ge "$HARD_CAP_SECONDS" ]; then
        log "CAP $name already at deadline=${deadline}s (cap=${HARD_CAP_SECONDS}s) remaining=${remaining}s $liveness -- NOT extending; needs a human"
      else
        new=$(( deadline + EXTEND_BY ))
        [ "$new" -gt "$HARD_CAP_SECONDS" ] && new=$HARD_CAP_SECONDS
        if timeout 90 $K patch job -n "$NS" "$name" \
             -p "{\"spec\":{\"activeDeadlineSeconds\":$new}}" >/dev/null 2>&1; then
          log "EXTEND $name ${deadline}s -> ${new}s (elapsed=${elapsed}s remaining_was=${remaining}s) $liveness"
        else
          log "EXTEND-FAILED $name ${deadline}s -> ${new}s (elapsed=${elapsed}s) $liveness"
        fi
      fi
    fi
  fi

  sleep "$POLL_SECONDS"
done
