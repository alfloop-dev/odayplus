#!/usr/bin/env bash
# Self-test for assert-boot-reconciliation.py.
#
# The acceptance assertion is the only thing standing between "the rollout
# worked" and "the rollout silently regressed", so it is tested against
# synthetic activity-log slices before the live window is opened. Nothing here
# touches the live system: it is pure fixture replay and is safe to re-run.
#
#   ./selftest-assertion.sh          # expects: all cases pass
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSERT="$HERE/assert-boot-reconciliation.py"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

RUN="claude-20260729T031503Z-5e9cfd48"
FAILURES=0

check() {
  local name="$1" fixture="$2" mode="$3" want="$4"
  python3 "$ASSERT" "$fixture" "$RUN" "$mode" > "$WORK/out.json" 2>&1
  local rc=$?
  local got=PASS
  [[ $rc -ne 0 ]] && got=FAIL
  if [[ "$got" == "$want" ]]; then
    printf 'ok    %-46s want=%s got=%s\n' "$name" "$want" "$got"
  else
    printf 'NOT OK %-45s want=%s got=%s\n' "$name" "$want" "$got"
    sed 's/^/         /' "$WORK/out.json"
    FAILURES=$((FAILURES + 1))
  fi
}

# --- the post-fix shape the rollout must produce -----------------------------
cat > "$WORK/pass.jsonl" <<EOF
{"type":"worker_deferred_approval_recorded","worker_run_id":"$RUN","approval_id":"apr-abc123","tool_name":"Bash","message":"Recorded deferred Bash approval apr-abc123 from the worker's tool_deferred receipt."}
{"type":"approval_resolved","approval_id":"apr-abc123","decision":"deny"}
EOF
check "post-fix: correlation recorded" "$WORK/pass.jsonl" post_boot PASS

# A generic worker_failed AFTER the correlation is legitimate: it is how the run
# finalizes once the test approval has been denied.
cat > "$WORK/pass-final.jsonl" <<EOF
{"type":"worker_deferred_approval_recorded","worker_run_id":"$RUN","approval_id":"apr-abc123","tool_name":"Bash"}
{"type":"approval_resolved","approval_id":"apr-abc123","decision":"deny"}
{"type":"worker_failed","worker_run_id":"$RUN","message":"Worker denied approval apr-abc123."}
EOF
check "post-fix: failure after correlation (final)" "$WORK/pass-final.jsonl" final PASS
check "post-fix: same slice rejected pre-resolution" "$WORK/pass-final.jsonl" post_boot FAIL

# --- the pre-fix shape recorded on this host in RACE-001 ---------------------
# boot reconciliation reported missing_process_workers_failed=1 and finalized
# the run with the generic worker-exit reason; no approval survived.
cat > "$WORK/prefix.jsonl" <<EOF
{"type":"worker_failed","worker_run_id":"$RUN","message":"Worker process missing during supervisor boot reconciliation."}
{"type":"worker_runtime_metrics","measurement":"boot_reconciliation","counts":{"missing_process_workers_failed":1}}
EOF
check "pre-fix regression shape is rejected" "$WORK/prefix.jsonl" post_boot FAIL
check "pre-fix regression shape is rejected (final)" "$WORK/prefix.jsonl" final FAIL

# --- degenerate inputs must never pass by default ----------------------------
: > "$WORK/empty.jsonl"
check "empty slice is rejected" "$WORK/empty.jsonl" post_boot FAIL
check "missing slice is rejected" "$WORK/nope.jsonl" post_boot FAIL

cat > "$WORK/noapproval.jsonl" <<EOF
{"type":"worker_deferred_approval_recorded","worker_run_id":"$RUN","approval_id":"","tool_name":"Bash"}
EOF
check "correlation without approval_id is rejected" "$WORK/noapproval.jsonl" post_boot FAIL

cat > "$WORK/otherrun.jsonl" <<EOF
{"type":"worker_deferred_approval_recorded","worker_run_id":"claude-someone-else","approval_id":"apr-zzz"}
EOF
check "correlation for a different run is rejected" "$WORK/otherrun.jsonl" post_boot FAIL

cat > "$WORK/queuefail.jsonl" <<EOF
{"type":"worker_deferred_approval_recorded","worker_run_id":"$RUN","approval_id":"apr-abc123"}
{"type":"worker_deferred_approval_failed","worker_run_id":"$RUN","message":"Could not record deferred tool approval during boot: disk full"}
EOF
check "queue write failure is rejected" "$WORK/queuefail.jsonl" post_boot FAIL

cat > "$WORK/ordering.jsonl" <<EOF
{"type":"worker_failed","worker_run_id":"$RUN","message":"Worker exited without completing."}
{"type":"worker_deferred_approval_recorded","worker_run_id":"$RUN","approval_id":"apr-abc123"}
EOF
check "failure preceding correlation is rejected" "$WORK/ordering.jsonl" final FAIL

echo
if [[ $FAILURES -eq 0 ]]; then
  echo "all assertion self-tests passed"
else
  echo "$FAILURES assertion self-test(s) failed"
fi
exit $FAILURES
