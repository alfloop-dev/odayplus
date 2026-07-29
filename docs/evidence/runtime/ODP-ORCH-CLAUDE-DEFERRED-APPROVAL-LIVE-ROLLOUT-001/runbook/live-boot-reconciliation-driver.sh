#!/usr/bin/env bash
# ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001
#
# Live acceptance driver for the PR #492 boot-reconciliation rollout.
#
# Why a driver script exists at all:
#   The proof needs a *real* Claude `stop_reason=tool_deferred` receipt from a
#   supervisor-dispatched worker, and the Claude CLI exits as soon as a tool is
#   deferred. The only supervisor-dispatched Claude worker that may be used for
#   the test is this task's own worker (Claude3 is running an unrelated task and
#   must not be disturbed), so the worker that triggers the deferral cannot also
#   observe what happens next. This driver runs in its own transient systemd
#   unit - outside the pantheon-supervisor.service cgroup - and performs every
#   step that has to happen after the worker session ends.
#
# Determinism:
#   The supervisor is *stopped* before the deferral is triggered, so no normal
#   poll can consume the flushed receipt. The receipt is therefore guaranteed to
#   be observed for the first time by reconcile_runtime_on_boot() on the next
#   start. That is exactly the ordering race PR #492 fixes.
#
# Revision 2 - changes demanded by the coordinator preflight STOP GATE
# (2026-07-29T03:33:14Z). Revision 1 was never executed; do not resurrect it.
#   1. The KillMode=process drop-in is now *owned* by this driver: installed in
#      phase 2 and removed with a daemon-reload on EVERY exit path, so the unit
#      is left on the shipped KillMode=control-group. Revision 1 installed it
#      out of band and never removed it.
#   2. An empty approval id is now a hard abort. Revision 1 could reach
#      `proof_complete` with no approval at all.
#   3. The acceptance assertion is fail-closed and external
#      (assert-boot-reconciliation.py): a missing correlation event, a generic
#      worker_failed preceding it, or any missing-process boot finalization
#      aborts the run. Revision 1 only dumped events for a human to read.
#   4. Peer survival is *enforced* after stop, after start, and at the end - and
#      the PID is bound to its run id through /proc/<pid>/cmdline, so PID reuse
#      cannot fake survival. Revision 1 only enforced it after stop.
#   5. The dead-man switch is armed by the driver itself and its unit name is
#      recorded in the evidence. Revision 1 relied on the caller arming it.
#   6. Phase 1 actually validates KillMode=process stop/start semantics on a
#      throwaway unit before touching the real one, and fails closed.
#
# Revision 3 - changes demanded by the coordinator STOP GATE 2
# (2026-07-29T03:48:39Z / 03:51:50Z). Revision 2 was never executed either.
#   1. The old test runner process must be *gone* before the supervisor is
#      started again. Revision 2 recorded `runner_process_exited` and then
#      ignored it: with the runner still alive, boot reconciliation sees a live
#      PID, refreshes the lease, and never takes the deferred-receipt path - a
#      pass would have been vacuous. Now `abort_worker_runner_alive`.
#   2. The new MainPID must be non-empty, non-zero and different from the one
#      captured before the window. Revision 2 printed the transition and
#      asserted nothing, so a failed start (MainPID=0) or a unit that never
#      actually stopped would have been read as a successful restart.
#   3. The dead-man delay is *computed* from the driver's own bounded waits
#      instead of being hardcoded. Revision 2 used a flat 15min while its
#      bounded waits summed to ~18min, so on a slow-but-legal run the dead-man
#      would have fired mid-window, pulled the drop-in and restarted the
#      supervisor underneath the driver.
#   4. The dead-man switch is disarmed and reset in restore_all(), i.e. on every
#      exit path. Revision 2 only disarmed it on the clean path, so every abort
#      left an armed timer that later restarted units behind the operator's back.
#   5. The watchdog *service* is stopped and verified inactive, not just its
#      timer, and every phase asserts that no unmanaged supervisor.py exists.
#      The watchdog is a 60s oneshot that `Popen`s a supervisor directly, so an
#      in-flight run - or a stray earlier one - could put a second, unmanaged
#      supervisor into the window and silently consume the receipt.
#   6. The terminal verdict (`proof_complete` or `abort_*`) is preserved. In
#      revision 2 the EXIT trap overwrote $SIGNAL_DIR/state with
#      `driver_finished_rc_N`, so the documented success check could never
#      observe `proof_complete` and no abort reason survived in the state file.
#   7. Stale claims, stale run ids and the obsolete 15min text were removed from
#      CONTINUATION.md, the fleet brief and the evidence README.
#
# Revisions 4 and 5 changed no executable behaviour: revision 4 corrected two
# documentation overclaims found by STOP GATE 3, revision 5 corrected the
# reviewer name in two capture-commit templates after STOP GATE 4.
#
# Revision 6 - the first finding produced by *running* the driver rather than
# reading it (2026-07-29T04:31:15Z, verdict `abort_killmode_probe`, exit 26).
#   1. Phase 1's throwaway probe could never pass, so no revision of this driver
#      could ever have reached phase 2. It restarted the probe through a second
#      `systemd-run` under the same transient unit name, and the leftover child
#      keeps that name loaded, so systemd refuses it ("was already loaded or has
#      a fragment file").
#   2. The probe additionally asserts that the leftover is still alive *after*
#      the restart, not merely that the unit came back.
#   3. Phase 1's waits are now declared constants and are counted in the
#      dead-man budget instead of being spent silently.
# The abort itself is the fail-closed path working: it happened before phase 2,
# so no drop-in, no restart, no deferral and no approval occurred, and the EXIT
# trap left MainPID 1197865 active on the shipped KillMode=control-group.
# Revision 6's *fix* was rejected by STOP GATE 6 and is not what this file does;
# see revision 7.
#
# Revision 7 - coordinator STOP GATE 6 (2026-07-29T04:43:36Z / 04:45:59Z).
# Revision 6 was never executed. Its diagnosis of the abort stands; its remedy
# did not.
#   1. Revision 6 answered "a transient unit name cannot be reused" by writing a
#      *persistent* unit file into /home/lupin/.config/systemd/user. That is out
#      of scope for this task - the driver has no business creating permanent
#      user units - and it was not necessary. Only a second `systemd-run` under
#      an already-loaded name fails; `systemctl --user start` on the *existing*
#      transient .service is proven to work (independent transcript at
#      2026-07-29T04:39:19Z, reproduced in ../preflight/killmode-probe-diagnosis.txt).
#      Phase 1 therefore creates its unit exactly once with `systemd-run`, stops
#      it, and restarts the SAME already-loaded unit with a bounded
#      `systemctl --user start`. `SYSTEMD_USER_DIR` is gone and the driver writes
#      no unit file anywhere.
#   2. The probe asserts all three live processes the real window depends on:
#      the OLD child (survivor of the stop), the NEW MainPID (non-zero and
#      different) and the NEW child. Each one is bound to a probe-owned argv
#      marker, so a recycled pid cannot satisfy any of them.
#   3. Cleanup is ownership- and cgroup-based and runs on every exit path: the
#      recorded old/new pids plus every pid seen in the probe unit's cgroup are
#      SIGTERMed then SIGKILLed, waited for, and the probe then asserts that no
#      marked process, no cgroup and no loaded unit is left (not-found /
#      inactive / dead). The residue assertion is part of the verdict, so a
#      probe that leaks fails closed instead of proceeding.
#   4. Every systemctl/systemd-run call in the probe is bounded by `timeout` and
#      its rc and stderr are captured to a receipt instead of going to
#      /dev/null. Discarded stderr is why five review passes missed the
#      revision-5 defect.
#   5. A clean-attempt gate runs before anything is written: a dirty timeline
#      root or a leftover verdict/state/exit_code in the signal dir aborts
#      (50/51) with the previous attempt's artefacts untouched, so one attempt's
#      receipts can never be read as another's. Attempt 1's artefacts are
#      archived under timeline/attempt-1-abort-killmode-probe/.
#
# Fail-safes:
#   * hard timeouts on every wait;
#   * an EXIT trap that restores the supervisor, the watchdog timer and the
#     shipped KillMode and disarms the dead-man on every exit path, including
#     every abort;
#   * an independent systemd dead-man's switch that performs the same restore
#     even if this driver is SIGKILLed and the trap never runs.
#
# `--selftest` runs the pure-logic gates (pid/run-id binding, unmanaged
# supervisor detection, dead-man budget) against throwaway processes and touches
# nothing live.
set -uo pipefail

TASK_ID="ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001"
LIVE_ROOT="/home/lupin/oday-plus-supervisor-live"
CONTROL_ROOT="/home/lupin/oday-plus"
WORKTREE="/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-orch-claude-deferred-approval-live-rollout-001"
EV="$WORKTREE/docs/evidence/runtime/$TASK_ID"
TL="$EV/timeline"
RUNBOOK="$EV/runbook"
SIGNAL_DIR="/tmp/odp-rollout-driver"

ACTIVITY_LOG="$LIVE_ROOT/ai-activity-log.jsonl"
LIVE_QUEUE="$LIVE_ROOT/.orchestrator/approval-queue.json"
CONTROL_QUEUE="$CONTROL_ROOT/.orchestrator/approval-queue.json"
LIVE_STATE="$LIVE_ROOT/.orchestrator/state.json"
UNIT="pantheon-supervisor.service"
WATCHDOG_TIMER="pantheon-supervisor-watchdog.timer"
WATCHDOG_SERVICE="pantheon-supervisor-watchdog.service"
# Revision 7 (STOP GATE 6): the only file this driver writes under
# /home/lupin/.config/systemd/user is the drop-in it owns and removes. There is
# deliberately no general `SYSTEMD_USER_DIR` variable any more - revision 6 used
# one to drop a *persistent* probe unit there, which is out of scope for this
# task and, per the 04:39:19Z transcript, was never needed.
DROPIN_DIR="/home/lupin/.config/systemd/user/pantheon-supervisor.service.d"
DROPIN="$DROPIN_DIR/10-preserve-workers-on-restart.conf"
DEADMAN_UNIT="odp-rollout-deadman"

# ------------------------------------------------------------ wait budget ----
# STOP GATE 2 finding #3: the dead-man delay must exceed everything this driver
# can legally wait for, otherwise a slow-but-valid run trips its own safety net,
# which would pull the drop-in and restart the supervisor mid-window. Every
# bounded wait is declared here and the delay is derived from their sum, so the
# two can no longer drift apart.
STOP_TIMEOUT_S=90                 # `timeout N systemctl stop`
WATCHDOG_QUIESCE_TRIES=20         # x1s  - waiting for the oneshot watchdog to finish
RECEIPT_TRIES=140                 # x3s  - waiting for stop_reason=tool_deferred
RUNNER_EXIT_TRIES=40              # x3s  - waiting for the runner process to exit
START_PID_TRIES=20                # x3s  - waiting for a non-zero MainPID
APPROVAL_TRIES=100                # x3s  - waiting for the approval to be recorded
RESOLVE_SETTLE_S=90               # fixed settle after the deny
PROBE_CHILD_TRIES=20              # x1s  - phase 1: waiting for a probe child pid
PROBE_CMD_TIMEOUT_S=30            # phase 1: bound on each systemd-run/systemctl call
PROBE_REAP_TRIES=10               # x1s  - phase 1: waiting for probe pids to die
PROBE_FIXED_S=8                   # phase 1: the two `sleep 2` settles, rounded up
FIXED_SLEEPS_S=30                 # the scattered `sleep 1..4` calls, rounded up
DEADMAN_MARGIN_S=900              # 15 min of headroom on top of the budget

# Revision 6: phase 1's waits are declared here too. They were always spent -
# revisions 3-5 just never counted them - and the point of finding #3 is that
# the dead-man delay is derived from *every* declared wait, not from a subset.
# Revision 7 adds phase 1's new bounded waits: the `timeout` on each probe
# command (systemd-run, stop, start, and the two cleanup stops = 5 calls), the
# child-pid wait which is now spent twice (once per start), and the reap wait,
# which is spent three times per probe_cleanup() call (SIGTERM, SIGKILL, unit
# garbage collection) and probe_cleanup() itself runs at most twice - once in the
# probe, once from restore_all().
TOTAL_BOUNDED_WAIT_S=$(( STOP_TIMEOUT_S + WATCHDOG_QUIESCE_TRIES \
  + (RECEIPT_TRIES * 3) + (RUNNER_EXIT_TRIES * 3) + (START_PID_TRIES * 3) \
  + (APPROVAL_TRIES * 3) + RESOLVE_SETTLE_S \
  + (PROBE_CHILD_TRIES * 2) + (PROBE_CMD_TIMEOUT_S * 5) + (PROBE_REAP_TRIES * 6) \
  + PROBE_FIXED_S + FIXED_SLEEPS_S ))
DEADMAN_DELAY_S=$(( TOTAL_BOUNDED_WAIT_S + DEADMAN_MARGIN_S ))

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
say() { printf '[%s] %s\n' "$(now)" "$*"; }
alive() { kill -0 "$1" 2>/dev/null; }
main_pid() { systemctl --user show "$UNIT" -p MainPID --value; }
kill_mode() { systemctl --user show "$UNIT" -p KillMode --value; }
active_state() { systemctl --user show "$1" -p ActiveState --value; }
load_state() { systemctl --user show "$1" -p LoadState --value; }
sub_state() { systemctl --user show "$1" -p SubState --value; }

# Bind a pid to a run id. `kill -0` alone would accept a recycled pid, which
# would let the driver claim a worker survived when it did not.
pid_owned_by_run() {
  local pid="$1" run_id="$2"
  [[ -n "$pid" && -r "/proc/$pid/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qF -- "$run_id"
}

# STOP GATE 2 finding #5: print every pid running supervisor.py that is NOT the
# systemd-managed MainPID. The watchdog `Popen`s a supervisor directly, so an
# unmanaged one can exist outside the unit entirely.
#
# Matching is on an exact argv *token*, never a substring: every Claude worker
# carries a wake-up prompt that mentions `supervisor.py` inside one long -p
# argument, and a substring match would report all of them as supervisors.
unmanaged_supervisors() {
  python3 - "$@" <<'PY'
import os, sys
exclude = {token for token in sys.argv[1:] if token and token != "0"}
for pid in sorted((p for p in os.listdir("/proc") if p.isdigit()), key=int):
    if pid in exclude:
        continue
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            raw = handle.read().decode("utf-8", "ignore")
    except OSError:
        continue
    tokens = [token for token in raw.split("\0") if token]
    if any(
        token == ".orchestrator/supervisor.py"
        or token.endswith("/.orchestrator/supervisor.py")
        for token in tokens
    ):
        print(pid)
PY
}

worker_field() {
  python3 - "$LIVE_STATE" "$1" "$2" <<'PY' 2>/dev/null
import json, sys
try:
    state = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
print(str((state.get("workers", {}).get(sys.argv[2]) or {}).get(sys.argv[3]) or ""))
PY
}

signal() { printf '%s\n' "$1" > "$SIGNAL_DIR/state"; say "signal: $1"; }

# STOP GATE 2 finding #6: the terminal verdict must survive the EXIT trap.
# Revision 2 signalled `proof_complete` and then let the trap overwrite the
# state file with `driver_finished_rc_N`, so the success the runbook told
# operators to look for was unobservable and abort reasons were erased too.
TERMINAL_SIGNAL=""
terminal() {
  TERMINAL_SIGNAL="$1"
  printf '%s\n' "$1" > "$SIGNAL_DIR/verdict"
  signal "$1"
}

# The EXIT trap's state-file decision, factored out so it can be regression
# tested directly (see --selftest) instead of only being asserted in prose.
# A recorded terminal verdict wins; only an unexplained exit gets the generic
# `driver_finished_rc_N`.
publish_final_state() {
  local rc="$1"
  printf '%s\n' "$rc" > "$SIGNAL_DIR/exit_code"
  if [[ -n "$TERMINAL_SIGNAL" ]]; then
    say "preserving terminal verdict: $TERMINAL_SIGNAL (rc=$rc)"
  else
    signal "driver_finished_rc_$rc"
  fi
}

# ------------------------------------------------- clean-attempt gate (rev 7) -
# STOP GATE 6: one attempt's receipts must never be readable as another's. The
# 04:31Z attempt left `abort_killmode_probe`/26 in the signal dir and its
# snapshots in the timeline root; those are now archived under
# timeline/attempt-1-abort-killmode-probe/, and a new run refuses to start while
# anything comparable is present.
#
# Prints the first offending entry (`timeline:<name>` / `signal:<name>`) and
# returns 0 when dirty; prints nothing and returns 1 when clean. `README.md` and
# archived `attempt-*/` directories are not dirt.
attempt_state_dirty() {
  local tl="$1" sig="$2" name
  if [[ -d "$tl" ]]; then
    while IFS= read -r name; do
      [[ "$name" == "README.md" ]] && continue
      printf 'timeline:%s\n' "$name"
      return 0
    done < <(find "$tl" -maxdepth 1 -mindepth 1 -type f -printf '%f\n' 2>/dev/null | sort)
  fi
  if [[ -d "$sig" ]]; then
    for name in verdict state exit_code driver.log; do
      if [[ -e "$sig/$name" ]]; then
        printf 'signal:%s\n' "$name"
        return 0
      fi
    done
  fi
  return 1
}

# ------------------------------------------------------------ phase 1 probe --
# Revision 7 (STOP GATE 6). Every probe command is bounded and its rc/stderr are
# recorded. Revisions 3-6 sent both halves of the probe to /dev/null, which is
# the only reason the revision-5 defect survived five review passes: the message
# systemd printed ("Unit ... was already loaded or has a fragment file") was
# thrown away.
probe_cmd() {
  local label="$1"; shift
  local rc=0 err
  err="$(timeout "$PROBE_CMD_TIMEOUT_S" "$@" 2>&1 >/dev/null)" || rc=$?
  # Bounded, single-line and trailing-whitespace-free: this receipt is committed.
  err="$(printf '%s' "$err" | head -c 400 | tr '\n\t' '  ' | sed 's/[[:space:]]*$//')"
  printf '%s rc=%s stderr=%s\n' "$label" "$rc" "${err:-<empty>}" >> "$PROBE_CMD_LOG"
  return "$rc"
}

# Every pid this probe has ever owned: the two MainPIDs, the two children, and
# whatever the unit's cgroup held while it was up. Cleanup works from this list,
# never from a pattern alone.
probe_record_pid() {
  local pid="$1"
  [[ -n "$pid" && "$pid" != "0" ]] || return 0
  case " ${PROBE_OWNED_PIDS:-} " in *" $pid "*) return 0 ;; esac
  PROBE_OWNED_PIDS="${PROBE_OWNED_PIDS:-}${PROBE_OWNED_PIDS:+ }$pid"
}

# Read the pids systemd currently has in the probe unit's cgroup and record them.
probe_record_cgroup_pids() {
  local cg pid
  cg="$(systemctl --user show "$PROBE_UNIT.service" -p ControlGroup --value 2>/dev/null)"
  [[ -n "$cg" ]] && PROBE_CGROUP="$cg"
  [[ -n "${PROBE_CGROUP:-}" && -r "/sys/fs/cgroup${PROBE_CGROUP}/cgroup.procs" ]] || return 0
  while read -r pid; do probe_record_pid "$pid"; done \
    < "/sys/fs/cgroup${PROBE_CGROUP}/cgroup.procs"
}

# A pid is the probe's to kill only if it is still carrying the probe's own argv
# marker. `kill -0` alone would let a recycled pid be killed instead - the same
# pid-recycling hazard pid_owned_by_run() exists for.
probe_owns_pid() { pid_owned_by_run "$1" "$PROBE_MARKER"; }

probe_live_pids() {
  local pid
  for pid in ${PROBE_OWNED_PIDS:-}; do probe_owns_pid "$pid" && printf '%s\n' "$pid"; done
  # Anything the recorded list missed but that still carries the marker.
  pgrep -f "$PROBE_MARKER" 2>/dev/null | while read -r pid; do
    case " ${PROBE_OWNED_PIDS:-} " in *" $pid "*) ;; *) printf '%s\n' "$pid" ;; esac
  done
}

# Idempotent; a no-op before killmode_probe() has named a probe unit. Called by
# the probe itself and by restore_all(), i.e. on every exit path.
probe_cleanup() {
  [[ -n "${PROBE_UNIT:-}" ]] || return 0
  probe_record_cgroup_pids
  probe_cmd "cleanup_stop" systemctl --user stop "$PROBE_UNIT.service" || true

  local signal pid tries
  for signal in TERM KILL; do
    for pid in $(probe_live_pids); do kill -"$signal" "$pid" 2>/dev/null; done
    for tries in $(seq 1 "$PROBE_REAP_TRIES"); do
      [[ -z "$(probe_live_pids)" ]] && break
      sleep 1
    done
  done

  systemctl --user reset-failed "$PROBE_UNIT.service" >/dev/null 2>&1
  # A transient unit with no processes left is garbage-collected by systemd, so
  # the resting state asserted below is not-found / inactive / dead. Collection
  # is not instantaneous, so give it the same bounded wait rather than reading
  # the state in the middle of it.
  for tries in $(seq 1 "$PROBE_REAP_TRIES"); do
    [[ "$(load_state "$PROBE_UNIT.service")" == not-found ]] && break
    sleep 1
  done
  PROBE_RESIDUE_PIDS="$(probe_live_pids | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
  PROBE_RESIDUE_CGROUP=no
  [[ -n "${PROBE_CGROUP:-}" && -d "/sys/fs/cgroup${PROBE_CGROUP}" ]] && PROBE_RESIDUE_CGROUP=yes
  PROBE_UNIT_LOAD_AFTER="$(load_state "$PROBE_UNIT.service")"
  PROBE_UNIT_ACTIVE_AFTER="$(active_state "$PROBE_UNIT.service")"
  PROBE_UNIT_SUB_AFTER="$(sub_state "$PROBE_UNIT.service")"
  PROBE_RESIDUE_CLEAN=no
  [[ -z "$PROBE_RESIDUE_PIDS" && "$PROBE_RESIDUE_CGROUP" == no \
     && "$PROBE_UNIT_LOAD_AFTER" == not-found \
     && "$PROBE_UNIT_ACTIVE_AFTER" == inactive \
     && "$PROBE_UNIT_SUB_AFTER" == dead ]] && PROBE_RESIDUE_CLEAN=yes
  rm -f "${PROBE_EXEC:-}" "${PROBE_CHILD_FILE:-}" 2>/dev/null
  return 0
}

# Revision 6 (STOP GATE 5 - self-caught by executing the driver rather than
# reading it) diagnosed why revisions 3-5 could never pass this gate: they ran
# the probe as two `systemd-run` calls under one transient unit name. The
# leftover child keeps the first unit *loaded* after it goes inactive, and
# `systemd-run` then refuses the name outright:
#
#   Failed to start transient service unit: Unit odp-km-diag-1351971.service
#   was already loaded or has a fragment file.
#
# `reset-failed` does not help - it clears failed state, it does not unload an
# inactive unit. The failure was therefore an artefact of *recreating* a
# transient unit, not the KillMode property under test; the first half of the
# probe (`probe_child_survived_stop: yes`) had already passed against the live
# host.
#
# Revision 7 (STOP GATE 6) fixes it the in-scope way, which is also the way the
# real restart works: `systemctl start` on a unit that already exists. Only a
# second `systemd-run` under a loaded name fails - starting the SAME
# already-loaded transient .service is proven to work on this host
# (2026-07-29T04:39:19Z transcript, ../preflight/killmode-probe-diagnosis.txt):
# `systemctl --user start` returned rc=0 with the leftover still in the cgroup,
# gave a new MainPID and a new child, and the old child stayed alive. Revision 6
# instead wrote a *persistent* unit file under ~/.config/systemd/user, which is
# out of scope for this driver and unnecessary; that is reverted.
#
# The unit is therefore created exactly once, and no unit file is ever written.
#
# Assertions (all four are required, and revisions 3-5 had only the first two):
#   * the OLD child survives `systemctl stop` - the property the window needs;
#   * the SAME unit starts again while that leftover is in its cgroup;
#   * the restart produced a NEW, non-zero, different MainPID and a NEW child -
#     otherwise "active" could mean the unit never really went down;
#   * the OLD child is still alive AFTER the restart.
# Every pid is bound to the probe's own argv marker, so a recycled pid cannot
# satisfy any of them.
#
# Factored into a function so `--selftest` can prove phase 1 passes without
# opening the window. It touches only its own throwaway transient unit.
killmode_probe() {
  PROBE_UNIT="odp-killmode-probe-$$"
  PROBE_MARKER="odp-killmode-probe-marker-$$-${RANDOM}"
  PROBE_CHILD_FILE="$SIGNAL_DIR/probe-child.pid"
  PROBE_EXEC="$SIGNAL_DIR/probe-child.sh"
  PROBE_CMD_LOG="$SIGNAL_DIR/probe-commands.txt"
  PROBE_CREATE_RC=1
  PROBE_OWNED_PIDS=""
  PROBE_CGROUP=""
  PROBE_MAIN_1=""
  PROBE_MAIN_2=""
  PROBE_CHILD=""
  PROBE_CHILD2=""
  PROBE_CHILD_SURVIVED=no
  PROBE_RESTART_OK=no
  PROBE_NEW_CHILD_ALIVE=no
  PROBE_CHILD_SURVIVED_RESTART=no
  PROBE_STATE_AFTER_STOP=""
  PROBE_START_RC=""
  PROBE_RESIDUE_CLEAN=no
  PROBE_VERDICT=fail

  mkdir -p "$SIGNAL_DIR"
  rm -f "$PROBE_CHILD_FILE" "$PROBE_CMD_LOG"
  # The payload's two processes both carry $PROBE_MARKER in their argv, which is
  # what makes ownership-based cleanup safe. `exec` at the end means the unit's
  # MainPID is exactly one marked process, so the only survivor of a
  # KillMode=process stop is the marked child.
  cat > "$PROBE_EXEC" <<EOF
#!/usr/bin/env bash
# Throwaway probe payload for $TASK_ID (driver-owned; no unit file is written).
python3 -c 'import sys, time; time.sleep(900)' "$PROBE_MARKER-child" &
echo \$! > "\$1"
exec python3 -c 'import sys, time; time.sleep(900)' "$PROBE_MARKER-main"
EOF
  chmod +x "$PROBE_EXEC"

  # ONE systemd-run, ever. The unit is never recreated.
  probe_cmd "systemd_run_create" systemd-run --user --unit="$PROBE_UNIT" \
    --property=KillMode=process --property=Type=simple \
    "$PROBE_EXEC" "$PROBE_CHILD_FILE"
  PROBE_CREATE_RC=$?

  for _ in $(seq 1 "$PROBE_CHILD_TRIES"); do [[ -s "$PROBE_CHILD_FILE" ]] && break; sleep 1; done
  PROBE_CHILD="$(cat "$PROBE_CHILD_FILE" 2>/dev/null)"
  PROBE_MAIN_1="$(systemctl --user show "$PROBE_UNIT.service" -p MainPID --value 2>/dev/null)"
  probe_record_pid "$PROBE_CHILD"
  probe_record_pid "$PROBE_MAIN_1"
  probe_record_cgroup_pids

  if [[ "$PROBE_CREATE_RC" -eq 0 && -n "$PROBE_CHILD" ]] && probe_owns_pid "$PROBE_CHILD"; then
    probe_cmd "stop" systemctl --user stop "$PROBE_UNIT.service" || true
    sleep 2
    probe_owns_pid "$PROBE_CHILD" && PROBE_CHILD_SURVIVED=yes
    PROBE_STATE_AFTER_STOP="$(active_state "$PROBE_UNIT.service")/$(sub_state "$PROBE_UNIT.service")/$(load_state "$PROBE_UNIT.service")"

    # Start the SAME already-loaded transient unit while the leftover child is
    # still in its cgroup - the exact condition the real restart will be in.
    rm -f "$PROBE_CHILD_FILE"
    probe_cmd "start_existing_unit" systemctl --user start "$PROBE_UNIT.service"
    PROBE_START_RC=$?
    for _ in $(seq 1 "$PROBE_CHILD_TRIES"); do [[ -s "$PROBE_CHILD_FILE" ]] && break; sleep 1; done
    sleep 2
    PROBE_CHILD2="$(cat "$PROBE_CHILD_FILE" 2>/dev/null)"
    PROBE_MAIN_2="$(systemctl --user show "$PROBE_UNIT.service" -p MainPID --value 2>/dev/null)"
    probe_record_pid "$PROBE_CHILD2"
    probe_record_pid "$PROBE_MAIN_2"
    probe_record_cgroup_pids

    [[ "$PROBE_START_RC" -eq 0 && "$(active_state "$PROBE_UNIT.service")" == active \
       && -n "$PROBE_MAIN_2" && "$PROBE_MAIN_2" != "0" && "$PROBE_MAIN_2" != "$PROBE_MAIN_1" ]] \
       && probe_owns_pid "$PROBE_MAIN_2" && PROBE_RESTART_OK=yes
    [[ -n "$PROBE_CHILD2" && "$PROBE_CHILD2" != "$PROBE_CHILD" ]] \
       && probe_owns_pid "$PROBE_CHILD2" && PROBE_NEW_CHILD_ALIVE=yes
    probe_owns_pid "$PROBE_CHILD" && PROBE_CHILD_SURVIVED_RESTART=yes
  fi

  # Cleanup is part of the gate, not an afterthought: a probe that leaks a
  # process, a cgroup or a loaded unit fails closed.
  probe_cleanup
  [[ "$PROBE_CHILD_SURVIVED" == yes && "$PROBE_RESTART_OK" == yes \
     && "$PROBE_NEW_CHILD_ALIVE" == yes && "$PROBE_CHILD_SURVIVED_RESTART" == yes \
     && "$PROBE_RESIDUE_CLEAN" == yes ]] && PROBE_VERDICT=pass
  [[ "$PROBE_VERDICT" == pass ]]
}

# ---------------------------------------------------------------- selftest ---
# Exercises the gates that STOP GATE 2 added, plus (revision 6) phase 1's
# KillMode probe. It never touches pantheon-supervisor.service, the drop-in, the
# dead-man or any queue: the only unit it creates is its own throwaway probe,
# which it removes again. Safe to run at any time.
if [[ "${1:-}" == "--selftest" ]]; then
  selftest_failures=0
  check() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
      printf 'ok    %-58s %s\n' "$label" "$actual"
    else
      printf 'FAIL  %-58s expected=%s actual=%s\n' "$label" "$expected" "$actual"
      selftest_failures=$((selftest_failures + 1))
    fi
  }

  echo "# driver gate selftest ($(now))"
  echo

  echo "## finding 3: dead-man delay must exceed the bounded wait budget"
  echo "total_bounded_wait_s: $TOTAL_BOUNDED_WAIT_S"
  echo "deadman_delay_s:      $DEADMAN_DELAY_S"
  check "deadman_delay > total_bounded_wait" yes \
    "$( [[ "$DEADMAN_DELAY_S" -gt "$TOTAL_BOUNDED_WAIT_S" ]] && echo yes || echo no )"
  check "deadman_margin >= 600s" yes \
    "$( [[ "$DEADMAN_MARGIN_S" -ge 600 ]] && echo yes || echo no )"
  echo

  echo "## finding 1/4: pid<->run-id binding rejects recycled and foreign pids"
  # python3, not `bash -c`: bash exec-optimizes a single simple command and the
  # marker would vanish from /proc/<pid>/cmdline, making the fixture - not the
  # gate - the thing under test.
  selftest_marker="odp-selftest-run-$$-$(date +%s)"
  python3 -c 'import sys,time; time.sleep(120)' "$selftest_marker" &
  selftest_pid=$!
  sleep 1
  check "own marker matches" yes \
    "$( pid_owned_by_run "$selftest_pid" "$selftest_marker" && echo yes || echo no )"
  check "foreign run id rejected" no \
    "$( pid_owned_by_run "$selftest_pid" "odp-selftest-other-run" && echo yes || echo no )"
  check "empty pid rejected" no \
    "$( pid_owned_by_run "" "$selftest_marker" && echo yes || echo no )"
  kill "$selftest_pid" 2>/dev/null
  wait "$selftest_pid" 2>/dev/null
  check "dead pid rejected" no \
    "$( pid_owned_by_run "$selftest_pid" "$selftest_marker" && echo yes || echo no )"
  echo

  echo "## finding 5: unmanaged supervisor detection is token-exact"
  # A decoy whose argv *contains* the string inside a longer argument, exactly
  # like a Claude worker's wake-up prompt. It must NOT be reported.
  python3 -c 'import sys,time; time.sleep(120)' \
    "reminder: do not edit .orchestrator/supervisor.py by hand" &
  selftest_decoy=$!
  # A genuine unmanaged supervisor: the path is its own argv token.
  python3 -c 'import sys,time; time.sleep(120)' \
    "/tmp/odp-selftest/.orchestrator/supervisor.py" &
  selftest_fake=$!
  sleep 1
  selftest_found="$(unmanaged_supervisors)"
  check "prompt-substring decoy not reported" no \
    "$( grep -qx "$selftest_decoy" <<< "$selftest_found" && echo yes || echo no )"
  check "exact-token supervisor reported" yes \
    "$( grep -qx "$selftest_fake" <<< "$selftest_found" && echo yes || echo no )"
  check "exclusion list drops the reported pid" no \
    "$( unmanaged_supervisors "$selftest_fake" | grep -qx "$selftest_fake" && echo yes || echo no )"
  kill "$selftest_decoy" "$selftest_fake" 2>/dev/null
  wait "$selftest_decoy" "$selftest_fake" 2>/dev/null
  sleep 1
  check "live supervisor is managed (excluding MainPID leaves none)" 0 \
    "$( unmanaged_supervisors "$(main_pid)" | grep -c . )"
  echo

  echo "## revision 6/7: phase 1's KillMode=process probe actually passes"
  # The revision-5 probe reported probe_verdict: fail on this host at
  # 04:31:15Z, so the driver aborted before phase 2 and no revision of it could
  # ever have opened the window. This runs the real phase-1 gate - the same
  # function the driver calls - against its own throwaway transient unit, and
  # asserts every property the real restart depends on plus its own cleanup.
  selftest_probe_signal="$SIGNAL_DIR"
  SIGNAL_DIR="$(mktemp -d)"
  selftest_units_before="$(ls /home/lupin/.config/systemd/user 2>/dev/null | grep -c .)"
  killmode_probe
  check "probe verdict" pass "$PROBE_VERDICT"
  check "unit created once, by systemd-run" 0 "$PROBE_CREATE_RC"
  check "leftover child survives the stop" yes "$PROBE_CHILD_SURVIVED"
  # After a KillMode=process stop the unit is inactive but still LOADED - that
  # is precisely why a second `systemd-run` under the same name fails, and
  # precisely why `systemctl start` on it works.
  check "unit is inactive but still loaded after the stop" "inactive/dead/loaded" \
    "$PROBE_STATE_AFTER_STOP"
  check "systemctl start of the existing transient unit succeeds" 0 "$PROBE_START_RC"
  check "restart produced a new, different, non-zero MainPID" yes "$PROBE_RESTART_OK"
  check "restarted unit spawned a new child" yes "$PROBE_NEW_CHILD_ALIVE"
  check "old child is still alive after the restart" yes "$PROBE_CHILD_SURVIVED_RESTART"
  check "old and new MainPID differ" no \
    "$( [[ "$PROBE_MAIN_1" == "$PROBE_MAIN_2" ]] && echo yes || echo no )"
  # STOP GATE 6: revision 6 wrote a persistent unit file here. Nothing may be
  # added to the user unit directory by this driver except the drop-in, which
  # phase 2 owns and which the probe never touches.
  check "no probe unit file left in the user unit dir" 0 \
    "$( ls /home/lupin/.config/systemd/user 2>/dev/null | grep -c 'odp-killmode-probe' )"
  check "user unit dir entry count unchanged" "$selftest_units_before" \
    "$( ls /home/lupin/.config/systemd/user 2>/dev/null | grep -c . )"
  # Residue, from the probe's own ownership/cgroup-based cleanup.
  check "cleanup left no marked probe process" yes \
    "$( [[ -z "$PROBE_RESIDUE_PIDS" ]] && echo yes || echo no )"
  check "cleanup left no probe cgroup" no "$PROBE_RESIDUE_CGROUP"
  check "probe unit load state" not-found "$PROBE_UNIT_LOAD_AFTER"
  check "probe unit active state" inactive "$PROBE_UNIT_ACTIVE_AFTER"
  check "probe unit sub state" dead "$PROBE_UNIT_SUB_AFTER"
  check "residue verdict" yes "$PROBE_RESIDUE_CLEAN"
  # `pgrep -c` prints 0 *and* exits 1 when there is no match, so a `|| echo 0`
  # fallback would append a second line; count the pids instead.
  check "no process still carries the probe marker" 0 \
    "$( pgrep -f "$PROBE_MARKER" 2>/dev/null | grep -c . )"
  check "probe payload script removed" no \
    "$( [[ -e "$PROBE_EXEC" ]] && echo yes || echo no )"
  # Every probe command's rc and stderr were captured, not discarded.
  check "probe command receipts captured" yes \
    "$( [[ -s "$PROBE_CMD_LOG" ]] && echo yes || echo no )"
  rm -rf "$SIGNAL_DIR"
  SIGNAL_DIR="$selftest_probe_signal"
  echo

  echo "## revision 7: the clean-attempt gate refuses dirty state"
  # A previous attempt's receipts must never be mistaken for this one's.
  selftest_gate_dir="$(mktemp -d)"
  check "empty timeline+signal dir is clean" clean \
    "$( attempt_state_dirty "$selftest_gate_dir/tl" "$selftest_gate_dir/sig" || echo clean )"
  mkdir -p "$selftest_gate_dir/tl" "$selftest_gate_dir/sig"
  : > "$selftest_gate_dir/tl/README.md"
  mkdir -p "$selftest_gate_dir/tl/attempt-1-abort-killmode-probe"
  : > "$selftest_gate_dir/tl/attempt-1-abort-killmode-probe/00-before.txt"
  check "README + archived attempt dir are not dirt" clean \
    "$( attempt_state_dirty "$selftest_gate_dir/tl" "$selftest_gate_dir/sig" || echo clean )"
  : > "$selftest_gate_dir/tl/98-abort-reason.txt"
  check "a receipt in the timeline root is dirt" "timeline:98-abort-reason.txt" \
    "$( attempt_state_dirty "$selftest_gate_dir/tl" "$selftest_gate_dir/sig" )"
  rm -f "$selftest_gate_dir/tl/98-abort-reason.txt"
  : > "$selftest_gate_dir/sig/verdict"
  check "a leftover verdict is dirt" "signal:verdict" \
    "$( attempt_state_dirty "$selftest_gate_dir/tl" "$selftest_gate_dir/sig" )"
  rm -rf "$selftest_gate_dir"
  echo

  echo "## finding 6: terminal verdict is preserved, not overwritten by EXIT"
  # Replays the exact revision-2 regression against a throwaway signal dir:
  # proof_complete followed by the trap's state write must still read
  # proof_complete, and an abort reason must survive a non-zero exit code.
  selftest_signal_real="$SIGNAL_DIR"
  SIGNAL_DIR="$(mktemp -d)"

  terminal "proof_complete" >/dev/null
  publish_final_state 0 >/dev/null
  check "proof_complete survives the EXIT trap" "proof_complete" \
    "$(cat "$SIGNAL_DIR/state")"
  check "exit code recorded alongside the verdict" "0" "$(cat "$SIGNAL_DIR/exit_code")"

  TERMINAL_SIGNAL=""
  terminal "abort_no_approval_recorded" >/dev/null
  publish_final_state 18 >/dev/null
  check "abort reason survives a non-zero exit" "abort_no_approval_recorded" \
    "$(cat "$SIGNAL_DIR/state")"
  check "abort exit code recorded" "18" "$(cat "$SIGNAL_DIR/exit_code")"

  # No verdict recorded (e.g. an unexpected kill) must still leave a trace.
  TERMINAL_SIGNAL=""
  publish_final_state 143 >/dev/null
  check "unexplained exit falls back to rc marker" "driver_finished_rc_143" \
    "$(cat "$SIGNAL_DIR/state")"

  rm -rf "$SIGNAL_DIR"
  SIGNAL_DIR="$selftest_signal_real"
  TERMINAL_SIGNAL=""
  echo

  if [[ "$selftest_failures" -eq 0 ]]; then
    echo "SELFTEST PASS"
    exit 0
  fi
  echo "SELFTEST FAIL ($selftest_failures)"
  exit 1
fi

# Only the two run ids are taken from the caller. Everything else (pid, log
# path) is resolved from the live supervisor state, so an operator cannot pass a
# stale or mismatched pid.
WORKER_RUN_ID="${1:?test worker run id required}"
PEER_RUN_ID="${2:?unrelated peer worker run id required}"

# STOP GATE 6 clean-attempt gate. This runs before the log redirect, before the
# EXIT trap and before a single byte is written, precisely so that a refusal
# leaves the previous attempt's artefacts exactly as they were - and so that the
# refusal itself cannot become the next run's dirt. Archive them and retry:
#
#   mkdir -p "$TL/attempt-<n>-<verdict>"
#   git -C "$WORKTREE" mv "$TL"/*.txt "$TL"/*.json "$TL/attempt-<n>-<verdict>/"
#   cp /tmp/odp-rollout-driver/{verdict,state,exit_code,driver.log} \
#      "$TL/attempt-<n>-<verdict>/"
#   rm -rf /tmp/odp-rollout-driver
DIRTY="$(attempt_state_dirty "$TL" "$SIGNAL_DIR" || true)"
if [[ -n "$DIRTY" ]]; then
  printf '[%s] FATAL: refusing to start: a previous attempt is still live at %s\n' \
    "$(now)" "$DIRTY" >&2
  printf '[%s] archive it under %s/attempt-<n>-<verdict>/ and clear %s first\n' \
    "$(now)" "$TL" "$SIGNAL_DIR" >&2
  case "$DIRTY" in
    timeline:*) exit 50 ;;
    *)          exit 51 ;;
  esac
fi

mkdir -p "$TL" "$SIGNAL_DIR"
exec > >(tee -a "$SIGNAL_DIR/driver.log") 2>&1

# ---------------------------------------------------------------- restore ----
# Idempotent. Runs from the EXIT trap and, independently, from the dead-man
# switch. Restoring the shipped KillMode is part of the contract: the drop-in is
# a temporary widening of the restart window and must not outlive it.
restore_all() {
  probe_cleanup
  if [[ -e "$DROPIN" ]]; then
    rm -f "$DROPIN"
    systemctl --user daemon-reload >/dev/null 2>&1
    say "removed KillMode drop-in and reloaded; KillMode now: $(kill_mode)"
  fi
  systemctl --user start "$UNIT" >/dev/null 2>&1
  systemctl --user start "$WATCHDOG_TIMER" >/dev/null 2>&1
  # STOP GATE 2 finding #4: disarm the dead-man here, so it happens on EVERY
  # exit path. Revision 2 disarmed it only on the clean path, so every abort
  # left an armed timer that would later restart units behind the operator.
  systemctl --user stop "$DEADMAN_UNIT.timer" >/dev/null 2>&1
  systemctl --user reset-failed "$DEADMAN_UNIT.service" "$DEADMAN_UNIT.timer" >/dev/null 2>&1
}

restore_report() {
  local rc="$1"
  {
    echo "restored_at: $(now)"
    echo "exit_code: $rc"
    echo "terminal_verdict: ${TERMINAL_SIGNAL:-<none>}"
    echo "supervisor_active_state: $(active_state "$UNIT")"
    echo "supervisor_main_pid: $(main_pid)"
    echo "supervisor_kill_mode: $(kill_mode)"
    echo "dropin_present: $([[ -e "$DROPIN" ]] && echo yes || echo no)"
    echo "watchdog_timer_state: $(active_state "$WATCHDOG_TIMER")"
    echo "watchdog_service_state: $(active_state "$WATCHDOG_SERVICE")"
    echo "deadman_timer_state: $(active_state "$DEADMAN_UNIT.timer")"
    # `<none>` rather than an empty tail: the empty case wrote a line ending in
    # a space into a committed receipt, which is exactly the defect class
    # STOP GATE 3 caught in preflight/preflight.md.
    echo "unmanaged_supervisors: $(unmanaged_supervisors "$(main_pid)" | tr '\n' ' ' | sed 's/[[:space:]]*$//' | grep . || echo '<none>')"
  } > "$TL/99-restore.txt"
}

# The trap restores state but must NOT invent a verdict. If a terminal verdict
# was already recorded (proof_complete or abort_*), it is preserved and only the
# exit code is added alongside it.
trap 'rc=$?
say "driver exiting rc=$rc; restoring supervisor, watchdog, shipped KillMode and disarming the dead-man"
restore_all
restore_report "$rc"
publish_final_state "$rc"' EXIT

fail() {
  local sig="$1" code="$2"; shift 2
  say "FATAL: $*"
  printf '%s\n' "$*" > "$TL/98-abort-reason.txt"
  terminal "$sig"
  exit "$code"
}

# ---------------------------------------------------------------- phase 0 ----
signal "starting"
T0="$(now)"

[[ -r "$ACTIVITY_LOG" ]] || fail abort_no_activity_log 20 "cannot read $ACTIVITY_LOG"
ACTIVITY_LINES_BEFORE="$(wc -l < "$ACTIVITY_LOG")"

WORKER_PID="$(worker_field "$WORKER_RUN_ID" pid)"
WORKER_LOG="$(worker_field "$WORKER_RUN_ID" log_path)"
PEER_PID="$(worker_field "$PEER_RUN_ID" pid)"
PEER_TASK="$(worker_field "$PEER_RUN_ID" task_id)"

[[ -n "$WORKER_PID" && -n "$WORKER_LOG" ]] || fail abort_unknown_worker 21 \
  "run $WORKER_RUN_ID has no pid/log_path in $LIVE_STATE"
[[ -n "$PEER_PID" ]] || fail abort_unknown_peer 22 \
  "peer run $PEER_RUN_ID has no pid in $LIVE_STATE"
pid_owned_by_run "$WORKER_PID" "$WORKER_RUN_ID" || fail abort_worker_identity 23 \
  "pid $WORKER_PID does not belong to $WORKER_RUN_ID"
pid_owned_by_run "$PEER_PID" "$PEER_RUN_ID" || fail abort_peer_identity 24 \
  "pid $PEER_PID does not belong to peer $PEER_RUN_ID"
[[ -x "$RUNBOOK/assert-boot-reconciliation.py" || -r "$RUNBOOK/assert-boot-reconciliation.py" ]] \
  || fail abort_no_assertion 25 "missing $RUNBOOK/assert-boot-reconciliation.py"

OLD_MAIN_PID="$(main_pid)"
KILL_MODE_BEFORE="$(kill_mode)"

# STOP GATE 2 finding #5, baseline: if a supervisor is already running outside
# the unit, the window is not exclusive before it even opens - that second
# process would keep polling and could consume the receipt normally, making the
# whole proof meaningless.
UNMANAGED_BEFORE="$(unmanaged_supervisors "$OLD_MAIN_PID" | tr '\n' ' ')"
[[ -z "${UNMANAGED_BEFORE// /}" ]] || fail abort_unmanaged_supervisor_before 40 \
  "unmanaged supervisor.py process(es) outside $UNIT before the window: $UNMANAGED_BEFORE"
[[ -n "$OLD_MAIN_PID" && "$OLD_MAIN_PID" != "0" ]] || fail abort_supervisor_not_running 41 \
  "$UNIT reports MainPID=$OLD_MAIN_PID before the window; there is no managed supervisor to restart"

cp "$LIVE_QUEUE" "$TL/approval-queue-live-before.json" 2>/dev/null
cp "$CONTROL_QUEUE" "$TL/approval-queue-control-before.json" 2>/dev/null
cp "$LIVE_STATE" "$TL/supervisor-state-before.json" 2>/dev/null

{
  echo "t0: $T0"
  echo "supervisor_main_pid_before: $OLD_MAIN_PID"
  echo "supervisor_kill_mode_before: $KILL_MODE_BEFORE"
  echo "dropin_present_before: $([[ -e "$DROPIN" ]] && echo yes || echo no)"
  echo "test_worker_run_id: $WORKER_RUN_ID"
  echo "test_worker_pid: $WORKER_PID"
  echo "test_worker_log: $WORKER_LOG"
  echo "peer_worker_run_id: $PEER_RUN_ID"
  echo "peer_worker_pid: $PEER_PID"
  echo "peer_worker_task: $PEER_TASK"
  echo "peer_pid_bound_to_run_id: yes"
  echo "activity_log_lines_before: $ACTIVITY_LINES_BEFORE"
  echo "unmanaged_supervisors_before: <none>"
  echo "watchdog_timer_state_before: $(active_state "$WATCHDOG_TIMER")"
  echo "watchdog_service_state_before: $(active_state "$WATCHDOG_SERVICE")"
  echo "total_bounded_wait_s: $TOTAL_BOUNDED_WAIT_S"
  echo "deadman_delay_s: $DEADMAN_DELAY_S"
} > "$TL/00-before.txt"

systemd-cgls --user-unit "$UNIT" --no-pager 2>/dev/null | sed 's/[[:space:]]*$//' > "$TL/00-cgroup-before.txt"

# ---------------------------------------------------------------- phase 1 ----
# Prove KillMode=process stop/start semantics on a throwaway unit BEFORE the
# real unit is touched: a leftover process must survive the stop, and the unit
# must still start again while that leftover is in the cgroup.
say "validating KillMode=process semantics on a throwaway unit"
# Revision 6: the probe body lives in killmode_probe() so `--selftest` can prove
# this gate passes without opening the window. See the comment there for why
# revisions 3-5 could never pass it.
killmode_probe || true   # the verdict is asserted below, after the receipt
{
  echo "probe_unit: $PROBE_UNIT.service (transient; created once by systemd-run)"
  echo "probe_marker: $PROBE_MARKER"
  echo "probe_unit_file_written: no"
  echo "probe_create_rc: ${PROBE_CREATE_RC:-<none>}"
  echo "probe_main_pid_1: ${PROBE_MAIN_1:-<none>}"
  echo "probe_child_pid_1: ${PROBE_CHILD:-<none>}"
  echo "probe_child_survived_stop: ${PROBE_CHILD_SURVIVED:-no}"
  echo "probe_unit_state_after_stop: ${PROBE_STATE_AFTER_STOP:-<none>}"
  echo "probe_start_existing_unit_rc: ${PROBE_START_RC:-<none>}"
  echo "probe_main_pid_2: ${PROBE_MAIN_2:-<none>}"
  echo "probe_child_pid_2: ${PROBE_CHILD2:-<none>}"
  echo "probe_unit_restarted_with_leftover: ${PROBE_RESTART_OK:-no}"
  echo "probe_new_child_alive: ${PROBE_NEW_CHILD_ALIVE:-no}"
  echo "probe_child_survived_restart: ${PROBE_CHILD_SURVIVED_RESTART:-no}"
  echo "probe_cgroup: ${PROBE_CGROUP:-<none>}"
  echo "probe_residual_pids: ${PROBE_RESIDUE_PIDS:-<none>}"
  echo "probe_residual_cgroup: ${PROBE_RESIDUE_CGROUP:-unknown}"
  echo "probe_unit_after_cleanup: ${PROBE_UNIT_LOAD_AFTER:-unknown}/${PROBE_UNIT_ACTIVE_AFTER:-unknown}/${PROBE_UNIT_SUB_AFTER:-unknown}"
  echo "probe_residue_clean: ${PROBE_RESIDUE_CLEAN:-no}"
  echo "probe_verdict: $PROBE_VERDICT"
  echo "--- bounded command receipts (rc + stderr) ---"
  cat "$PROBE_CMD_LOG" 2>/dev/null || echo "<no command receipts>"
} | sed 's/[[:space:]]*$//' > "$TL/00-killmode-probe.txt"
[[ "$PROBE_VERDICT" == pass ]] || fail abort_killmode_probe 26 \
  "KillMode=process probe failed (child_survived_stop=${PROBE_CHILD_SURVIVED:-no} restarted_with_leftover=${PROBE_RESTART_OK:-no} new_child_alive=${PROBE_NEW_CHILD_ALIVE:-no} child_survived_restart=${PROBE_CHILD_SURVIVED_RESTART:-no} residue_clean=${PROBE_RESIDUE_CLEAN:-no}); refusing to stop the real supervisor"

# ---------------------------------------------------------------- phase 2 ----
say "installing the temporary KillMode=process drop-in"
mkdir -p "$DROPIN_DIR"
cat > "$DROPIN" <<'EOF'
[Service]
# TEMPORARY - installed by live-boot-reconciliation-driver.sh for the
# ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001 window only. The driver
# removes this file and runs `systemctl --user daemon-reload` on every exit
# path. If you find it on disk with no driver running, remove it: the shipped
# behaviour is KillMode=control-group.
KillMode=process
EOF
systemctl --user daemon-reload
sleep 1
KILL_MODE_WINDOW="$(kill_mode)"
say "KillMode is now: $KILL_MODE_WINDOW"
[[ "$KILL_MODE_WINDOW" == process ]] || fail abort_killmode_not_applied 27 \
  "drop-in did not take effect (KillMode=$KILL_MODE_WINDOW); stopping the unit would kill every live worker"

# ---------------------------------------------------------------- phase 3 ----
# Dead-man switch: performs the same restore as the EXIT trap even if this
# driver is SIGKILLed. Armed by the driver, not by the caller, so it can never
# be forgotten.
# The delay is derived from the driver's own bounded waits (see the wait budget
# at the top), so the safety net can never fire inside a legal run.
[[ "$DEADMAN_DELAY_S" -gt "$TOTAL_BOUNDED_WAIT_S" ]] || fail abort_deadman_budget 42 \
  "dead-man delay ${DEADMAN_DELAY_S}s does not exceed the bounded wait budget ${TOTAL_BOUNDED_WAIT_S}s"
say "arming the dead-man switch ($DEADMAN_UNIT, +${DEADMAN_DELAY_S}s; bounded waits total ${TOTAL_BOUNDED_WAIT_S}s)"
cat > "$SIGNAL_DIR/deadman-restore.sh" <<EOF
#!/usr/bin/env bash
# Dead-man restore for $TASK_ID. Idempotent.
rm -f "$DROPIN"
systemctl --user daemon-reload
systemctl --user start "$UNIT"
systemctl --user start "$WATCHDOG_TIMER"
printf 'deadman fired at %s\n' "\$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$SIGNAL_DIR/deadman.log"
EOF
chmod +x "$SIGNAL_DIR/deadman-restore.sh"
systemctl --user stop "$DEADMAN_UNIT.timer" >/dev/null 2>&1
systemctl --user reset-failed "$DEADMAN_UNIT.service" "$DEADMAN_UNIT.timer" >/dev/null 2>&1
systemd-run --user --unit="$DEADMAN_UNIT" --on-active="${DEADMAN_DELAY_S}s" \
  "$SIGNAL_DIR/deadman-restore.sh" >/dev/null 2>&1
DEADMAN_STATE="$(active_state "$DEADMAN_UNIT.timer")"
{
  echo "deadman_unit: $DEADMAN_UNIT.timer"
  echo "deadman_delay_s: $DEADMAN_DELAY_S"
  echo "total_bounded_wait_s: $TOTAL_BOUNDED_WAIT_S"
  echo "deadman_margin_s: $DEADMAN_MARGIN_S"
  echo "deadman_script: $SIGNAL_DIR/deadman-restore.sh"
  echo "deadman_timer_state: $DEADMAN_STATE"
  echo "deadman_next_elapse: $(systemctl --user show "$DEADMAN_UNIT.timer" -p NextElapseUSecMonotonic --value 2>/dev/null)"
} > "$TL/00-deadman.txt"
[[ "$DEADMAN_STATE" == active || "$DEADMAN_STATE" == waiting ]] || fail abort_no_deadman 28 \
  "dead-man switch did not arm (state=$DEADMAN_STATE); refusing to open the restart window"

# ---------------------------------------------------------------- phase 4 ----
# STOP GATE 2 finding #5: stopping the timer alone is not enough. The watchdog
# is a Type=oneshot service on a 60s OnUnitActiveSec cycle that runs
# `run-supervisor-watchdog.sh --restart`, which `Popen`s
# `.orchestrator/supervisor.py` directly rather than going through systemd. An
# in-flight run therefore survives `stop <timer>` and can drop a second,
# unmanaged supervisor into the window - which would poll the receipt normally
# and quietly destroy the boot-reconciliation proof.
say "stopping watchdog timer AND service for the duration of the window"
systemctl --user stop "$WATCHDOG_TIMER"
systemctl --user stop "$WATCHDOG_SERVICE"
for _ in $(seq 1 "$WATCHDOG_QUIESCE_TRIES"); do
  [[ "$(active_state "$WATCHDOG_SERVICE")" == inactive ]] && break
  sleep 1
done
WATCHDOG_TIMER_STATE="$(active_state "$WATCHDOG_TIMER")"
WATCHDOG_SERVICE_STATE="$(active_state "$WATCHDOG_SERVICE")"
say "watchdog timer=$WATCHDOG_TIMER_STATE service=$WATCHDOG_SERVICE_STATE"
[[ "$WATCHDOG_TIMER_STATE" == inactive ]] || fail abort_watchdog_timer_active 43 \
  "$WATCHDOG_TIMER is still $WATCHDOG_TIMER_STATE; it would re-spawn an unmanaged supervisor inside the window"
[[ "$WATCHDOG_SERVICE_STATE" == inactive ]] || fail abort_watchdog_service_active 44 \
  "$WATCHDOG_SERVICE is still $WATCHDOG_SERVICE_STATE; an in-flight watchdog run can Popen a second supervisor"

say "stopping $UNIT (KillMode=process: only the supervisor process is signalled)"
timeout "$STOP_TIMEOUT_S" systemctl --user stop "$UNIT"
sleep 3
STOP_AT="$(now)"

# With the unit stopped there must be no supervisor.py anywhere: not the one
# systemd managed (it must have exited) and not one the watchdog left behind.
UNMANAGED_IN_WINDOW="$(unmanaged_supervisors | tr '\n' ' ')"
[[ -z "${UNMANAGED_IN_WINDOW// /}" ]] || fail abort_unmanaged_supervisor_in_window 45 \
  "supervisor.py still running during the window (pids: $UNMANAGED_IN_WINDOW); the receipt could be consumed by a normal poll"

WORKER_ALIVE_AFTER_STOP=no; pid_owned_by_run "$WORKER_PID" "$WORKER_RUN_ID" && WORKER_ALIVE_AFTER_STOP=yes
PEER_ALIVE_AFTER_STOP=no;   pid_owned_by_run "$PEER_PID" "$PEER_RUN_ID"     && PEER_ALIVE_AFTER_STOP=yes
{
  echo "supervisor_stopped_at: $STOP_AT"
  echo "supervisor_active_state: $(active_state "$UNIT")"
  echo "supervisor_main_pid: $(main_pid)"
  echo "test_worker_pid_alive_after_stop: $WORKER_ALIVE_AFTER_STOP"
  echo "peer_worker_pid_alive_after_stop: $PEER_ALIVE_AFTER_STOP"
  echo "watchdog_timer_state: $WATCHDOG_TIMER_STATE"
  echo "watchdog_service_state: $WATCHDOG_SERVICE_STATE"
  echo "unmanaged_supervisors_in_window: <none>"
  echo
  echo "# processes left in the unit cgroup after the stop"
  systemd-cgls --user-unit "$UNIT" --no-pager 2>/dev/null | sed 's/[[:space:]]*$//'
} > "$TL/01-after-stop.txt"
say "after stop: test worker alive=$WORKER_ALIVE_AFTER_STOP peer alive=$PEER_ALIVE_AFTER_STOP"

[[ "$PEER_ALIVE_AFTER_STOP" == yes ]] || fail abort_peer_killed 11 \
  "the unrelated peer worker $PEER_RUN_ID (pid $PEER_PID, task $PEER_TASK) did not survive the stop"
[[ "$WORKER_ALIVE_AFTER_STOP" == yes ]] || fail abort_worker_killed 12 \
  "the test worker session did not survive the stop; no deferral can be produced"

# ---------------------------------------------------------------- phase 5 ----
signal "supervisor_stopped"
say "waiting for the worker to emit stop_reason=tool_deferred (<=$((RECEIPT_TRIES * 3))s)"
RECEIPT_AT=""
for _ in $(seq 1 "$RECEIPT_TRIES"); do
  if grep -q '"stop_reason":"tool_deferred"' "$WORKER_LOG" 2>/dev/null; then
    RECEIPT_AT="$(now)"
    break
  fi
  sleep 3
done
[[ -n "$RECEIPT_AT" ]] || fail abort_no_receipt 13 \
  "no tool_deferred receipt appeared within $((RECEIPT_TRIES * 3))s; restoring service without a proof run"

SUPERVISOR_STATE_AT_RECEIPT="$(active_state "$UNIT")"
[[ "$SUPERVISOR_STATE_AT_RECEIPT" == active ]] && fail abort_supervisor_alive_in_window 14 \
  "the supervisor was running when the receipt landed; the boot-reconciliation window was not exclusive"

say "receipt observed at $RECEIPT_AT; waiting for the runner process to exit (<=$((RUNNER_EXIT_TRIES * 3))s)"
for _ in $(seq 1 "$RUNNER_EXIT_TRIES"); do
  pid_owned_by_run "$WORKER_PID" "$WORKER_RUN_ID" || break
  sleep 3
done
RUNNER_GONE=no; pid_owned_by_run "$WORKER_PID" "$WORKER_RUN_ID" || RUNNER_GONE=yes

# STOP GATE 2 finding #1: this is a gate, not a note. If the old test runner is
# still alive when the supervisor comes back, reconcile_runtime_on_boot() sees a
# live PID and simply refreshes the lease - the missing-process branch this task
# has to prove is never reached, and `proof_complete` would mean nothing.
#
# STOP GATE 3 correction. What `pid_owned_by_run` buys here is narrow and must
# not be overstated: the gate blocks the restart only while the *original*,
# run-id-bound process is still running. A pid recycled by some unrelated
# process does NOT abort - it reads as "the original runner is gone", which is
# the property this gate actually needs.
#
# That does leave one honest gap, because the supervisor's own `pid_is_alive()`
# is pid-only with no run-id binding: under pid recycling the supervisor would
# see a live pid, refresh the lease and never take the deferred branch, while
# this gate reads "gone". The run cannot pass vacuously anyway - the lease-
# refresh path emits no `worker_deferred_approval_recorded/_correlated` event,
# so `assert-boot-reconciliation.py` fails and the driver aborts there instead.
# The recycling window is also ~0: the check runs seconds after the runner exit,
# under a 4-million pid space.
[[ "$RUNNER_GONE" == yes ]] || fail abort_worker_runner_alive 46 \
  "the test runner (pid $WORKER_PID, run $WORKER_RUN_ID) is still alive after the receipt; boot reconciliation would refresh its lease instead of adopting the deferred receipt"

python3 - "$WORKER_LOG" > "$TL/02-deferred-receipt.json" <<'PY'
import json, sys
receipt = None
with open(sys.argv[1], encoding="utf-8", errors="ignore") as handle:
    for line in handle:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "result" and payload.get("stop_reason") == "tool_deferred":
            deferred = payload.get("deferred_tool_use") or {}
            receipt = {
                "session_id": payload.get("session_id"),
                "stop_reason": payload.get("stop_reason"),
                "terminal_reason": payload.get("terminal_reason"),
                "num_turns": payload.get("num_turns"),
                "deferred_tool_use": {
                    "id": deferred.get("id"),
                    "name": deferred.get("name"),
                    "input": deferred.get("input"),
                },
            }
print(json.dumps(receipt, indent=2, ensure_ascii=False))
PY
grep -q '"deferred_tool_use"' "$TL/02-deferred-receipt.json" || fail abort_unparsable_receipt 15 \
  "the tool_deferred receipt could not be parsed out of $WORKER_LOG"

{
  echo "receipt_observed_at: $RECEIPT_AT"
  echo "runner_process_exited: $RUNNER_GONE"
  echo "supervisor_active_state_while_receipt_landed: $SUPERVISOR_STATE_AT_RECEIPT"
  echo "# The supervisor was NOT running when the receipt was flushed, so the"
  echo "# receipt can only be observed for the first time by boot reconciliation."
} > "$TL/02-receipt-window.txt"

# ---------------------------------------------------------------- phase 6 ----
say "starting $UNIT"
systemctl --user start "$UNIT"
sleep 4
START_AT="$(now)"
NEW_MAIN_PID="$(main_pid)"
for _ in $(seq 1 "$START_PID_TRIES"); do
  [[ -n "$NEW_MAIN_PID" && "$NEW_MAIN_PID" != "0" ]] && break
  sleep 3
  NEW_MAIN_PID="$(main_pid)"
done
say "supervisor restarted: pid $OLD_MAIN_PID -> $NEW_MAIN_PID"

PEER_ALIVE_AFTER_START=no; pid_owned_by_run "$PEER_PID" "$PEER_RUN_ID" && PEER_ALIVE_AFTER_START=yes
UNMANAGED_AFTER_START="$(unmanaged_supervisors "$NEW_MAIN_PID" | tr '\n' ' ')"
{
  echo "supervisor_started_at: $START_AT"
  echo "supervisor_main_pid_before: $OLD_MAIN_PID"
  echo "supervisor_main_pid_after: $NEW_MAIN_PID"
  echo "supervisor_active_state: $(active_state "$UNIT")"
  echo "peer_worker_pid_alive_after_start: $PEER_ALIVE_AFTER_START"
  echo "unmanaged_supervisors_after_start: ${UNMANAGED_AFTER_START:-<none>}"
  echo
  echo "# processes in the unit cgroup after the restart"
  systemd-cgls --user-unit "$UNIT" --no-pager 2>/dev/null | sed 's/[[:space:]]*$//'
} > "$TL/03-after-start.txt"

[[ "$PEER_ALIVE_AFTER_START" == yes ]] || fail abort_peer_lost_on_start 16 \
  "peer worker $PEER_RUN_ID (pid $PEER_PID) did not survive the restart"
[[ "$(active_state "$UNIT")" == active ]] || fail abort_supervisor_not_active 17 \
  "the supervisor did not come back up after the window"

# STOP GATE 2 finding #2: assert the restart, do not just narrate it. MainPID=0
# means the start failed; an unchanged MainPID means the unit never actually
# stopped, so no boot reconciliation ran and any later "pass" would describe a
# process that had been up the whole time.
[[ -n "$NEW_MAIN_PID" && "$NEW_MAIN_PID" != "0" ]] || fail abort_supervisor_pid_invalid 47 \
  "$UNIT reports MainPID=${NEW_MAIN_PID:-<empty>} after the start; the supervisor did not come up"
[[ "$NEW_MAIN_PID" != "$OLD_MAIN_PID" ]] || fail abort_supervisor_pid_unchanged 48 \
  "MainPID is still $OLD_MAIN_PID after the window; the supervisor never restarted, so no boot reconciliation ran"
[[ -z "${UNMANAGED_AFTER_START// /}" ]] || fail abort_unmanaged_supervisor_after_start 49 \
  "a second, unmanaged supervisor.py is running after the restart (pids: $UNMANAGED_AFTER_START)"

say "waiting for boot reconciliation to record the approval (<=$((APPROVAL_TRIES * 3))s)"
APPROVAL_ID=""
for _ in $(seq 1 "$APPROVAL_TRIES"); do
  APPROVAL_ID="$(python3 - "$LIVE_QUEUE" "$WORKER_RUN_ID" <<'PY'
import json, sys
try:
    queue = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
run_id = sys.argv[2]
for item in list(queue.get("pending", [])) + list(queue.get("history", [])):
    if item.get("worker_run_id") == run_id:
        print(item.get("approval_id") or "")
        break
else:
    print("")
PY
)"
  [[ -n "$APPROVAL_ID" ]] && break
  sleep 3
done
say "approval id: ${APPROVAL_ID:-<none>}"

# Coordinator finding #2: an empty approval id is a hard abort. Revision 1 fell
# through to proof_complete here.
[[ -n "$APPROVAL_ID" ]] || fail abort_no_approval_recorded 18 \
  "boot reconciliation created no approval for $WORKER_RUN_ID in $LIVE_QUEUE within $((APPROVAL_TRIES * 3))s; the receipt was not adopted"

# The approval must have been adopted by the *supervisor*, not pre-created by
# the hook: correlation_source is what distinguishes the two.
python3 - "$LIVE_QUEUE" "$APPROVAL_ID" > "$TL/05-approval-record.json" <<'PY'
import json, sys
queue = json.load(open(sys.argv[1], encoding="utf-8"))
for item in list(queue.get("pending", [])) + list(queue.get("history", [])):
    if item.get("approval_id") == sys.argv[2]:
        print(json.dumps({
            "approval_id": item.get("approval_id"),
            "status": item.get("status"),
            "worker_run_id": item.get("worker_run_id"),
            "task_id": item.get("task_id"),
            "tool_name": item.get("tool_name"),
            "tool_use_id": item.get("tool_use_id"),
            "risk_class": item.get("risk_class"),
            "correlation_source": item.get("correlation_source"),
            "created_at": item.get("created_at"),
            "tool_input_preview": item.get("tool_input_preview"),
        }, indent=2, ensure_ascii=False))
        break
PY
CORRELATION_SOURCE="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("correlation_source") or "")' "$TL/05-approval-record.json" 2>/dev/null)"
say "approval correlation_source: ${CORRELATION_SOURCE:-<none>}"
[[ "$CORRELATION_SOURCE" == "supervisor_deferred_tool_receipt" ]] || fail abort_wrong_correlation_source 19 \
  "approval $APPROVAL_ID has correlation_source='${CORRELATION_SOURCE:-<none>}'; it was not adopted by boot reconciliation"

# ---------------------------------------------------------------- phase 7 ----
capture_events() {
  tail -n +"$((ACTIVITY_LINES_BEFORE + 1))" "$ACTIVITY_LOG" > "$TL/04-activity-log-after-restart.jsonl"
  python3 - "$TL/04-activity-log-after-restart.jsonl" "$WORKER_RUN_ID" > "$TL/04-events-for-run.txt" <<'PY'
import json, sys
run_id = sys.argv[2]
keep = (
    "worker_deferred_approval_recorded",
    "worker_deferred_approval_correlated",
    "worker_deferred_approval_failed",
    "worker_failed",
    "worker_suspended",
    "worker_reaped",
    "worker_runtime_metrics",
    "approval",
    "supervisor_boot",
)
with open(sys.argv[1], encoding="utf-8", errors="ignore") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = str(event.get("type") or "")
        if event.get("worker_run_id") == run_id or any(k in etype for k in keep):
            print(json.dumps(event, ensure_ascii=False))
PY
}
capture_events
journalctl --user -u "$UNIT" --since "$T0" --no-pager -o short-iso > "$TL/04-supervisor-journal.txt" 2>&1
cp "$LIVE_QUEUE" "$TL/05-approval-queue-live-after-boot.json" 2>/dev/null
cp "$LIVE_STATE" "$TL/05-supervisor-state-after-boot.json" 2>/dev/null

# Coordinator finding #3: fail closed on the ordering itself, not just record it.
say "asserting boot-reconciliation ordering (post_boot)"
python3 "$RUNBOOK/assert-boot-reconciliation.py" \
  "$TL/04-activity-log-after-restart.jsonl" "$WORKER_RUN_ID" post_boot \
  > "$TL/05-assertion-post-boot.json" 2>&1
POST_BOOT_RC=$?
cat "$TL/05-assertion-post-boot.json"
[[ "$POST_BOOT_RC" -eq 0 ]] || fail abort_assertion_post_boot 30 \
  "boot-reconciliation assertion FAILED after the restart; see timeline/05-assertion-post-boot.json"

say "committing captured evidence before resolving the approval"
git -C "$WORKTREE" add "docs/evidence/runtime/$TASK_ID" >/dev/null 2>&1
cat > "$SIGNAL_DIR/commit-msg-capture.txt" <<EOF
$TASK_ID: capture live boot reconciliation

Owned layer: live acceptance evidence for the PR #492 boot-reconciliation
rollout (deferred receipt, restart timeline, approval queue state, fail-closed
ordering assertion).
Not changing: supervisor behaviour, approval policy, deployment scripts.
Composes with: the deployed supervisor.py in the launch tree and live runtime.

LLM-Agent: Claude2
Task-ID: $TASK_ID
Reviewer: Codex2
Verified: driver capture only; suites recorded in preflight/
EOF
git -C "$WORKTREE" commit -F "$SIGNAL_DIR/commit-msg-capture.txt" >/dev/null 2>&1 \
  && say "capture commit: $(git -C "$WORKTREE" rev-parse --short HEAD)" \
  || say "capture commit skipped (nothing staged or hook refused)"
git -C "$WORKTREE" push -u origin "task/$TASK_ID" >/dev/null 2>&1 \
  && say "pushed task branch" || say "push failed (will retry at the end)"

# ---------------------------------------------------------------- phase 8 ----
say "explicitly denying the test approval $APPROVAL_ID (the deferred command must not run)"
( cd "$LIVE_ROOT" && python3 .orchestrator/approval_queue.py deny "$APPROVAL_ID" \
    --note "ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001 live acceptance probe. The deferred command was a harmless 'wget --version' used only to produce a real tool_deferred receipt. Denied so no privileged action is executed or left pending." \
    ) > "$TL/06-resolve-live.txt" 2>&1
say "live deny rc=$?"

# The PreToolUse hook writes into the control tree's queue (its ROOT is
# /home/lupin/oday-plus), which is a different queue from the one the live
# supervisor reads. Resolve this run's orphan there too, so the probe leaves no
# pending privileged action in either queue. Filtered strictly by worker_run_id:
# the control queue holds unrelated pending approvals that must not be touched.
python3 - "$CONTROL_QUEUE" "$WORKER_RUN_ID" > "$SIGNAL_DIR/control-ids.txt" <<'PY'
import json, sys
try:
    queue = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(0)
for item in queue.get("pending", []):
    if item.get("worker_run_id") == sys.argv[2]:
        print(item.get("approval_id"))
PY
: > "$TL/06-resolve-control.txt"
while read -r cid; do
  [[ -z "$cid" ]] && continue
  ( cd "$CONTROL_ROOT" && python3 .orchestrator/approval_queue.py deny "$cid" \
      --note "ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001 live acceptance probe orphan (hook queue). Denied; the command was never executed." \
      ) >> "$TL/06-resolve-control.txt" 2>&1
done < "$SIGNAL_DIR/control-ids.txt"

say "waiting for the supervisor to act on the resolved approval (${RESOLVE_SETTLE_S}s)"
sleep "$RESOLVE_SETTLE_S"
capture_events
cp "$LIVE_QUEUE" "$TL/07-approval-queue-live-final.json" 2>/dev/null
cp "$LIVE_STATE" "$TL/07-supervisor-state-final.json" 2>/dev/null
journalctl --user -u "$UNIT" --since "$T0" --no-pager -o short-iso > "$TL/04-supervisor-journal.txt" 2>&1

say "asserting boot-reconciliation ordering (final)"
python3 "$RUNBOOK/assert-boot-reconciliation.py" \
  "$TL/04-activity-log-after-restart.jsonl" "$WORKER_RUN_ID" final \
  > "$TL/07-assertion-final.json" 2>&1
FINAL_RC=$?
cat "$TL/07-assertion-final.json"
[[ "$FINAL_RC" -eq 0 ]] || fail abort_assertion_final 31 \
  "boot-reconciliation assertion FAILED after approval resolution; see timeline/07-assertion-final.json"

# Nothing privileged may be left pending for this run in either queue.
LEFTOVER="$(python3 - "$LIVE_QUEUE" "$CONTROL_QUEUE" "$WORKER_RUN_ID" <<'PY'
import json, sys
total = 0
for path in sys.argv[1:3]:
    try:
        queue = json.load(open(path, encoding="utf-8"))
    except Exception:
        continue
    total += sum(1 for i in queue.get("pending", []) if i.get("worker_run_id") == sys.argv[3])
print(total)
PY
)"
say "pending approvals still attached to $WORKER_RUN_ID: $LEFTOVER"
[[ "$LEFTOVER" == "0" ]] || fail abort_approval_left_pending 32 \
  "$LEFTOVER approval(s) for $WORKER_RUN_ID are still pending; the probe must not leave a privileged action open"

# ---------------------------------------------------------------- phase 9 ----
PEER_ALIVE_FINAL=no; pid_owned_by_run "$PEER_PID" "$PEER_RUN_ID" && PEER_ALIVE_FINAL=yes
{
  echo "final_capture_at: $(now)"
  echo "approval_id: $APPROVAL_ID"
  echo "approval_correlation_source: $CORRELATION_SOURCE"
  echo "assertion_post_boot: PASS"
  echo "assertion_final: PASS"
  echo "supervisor_main_pid_before: $OLD_MAIN_PID"
  echo "supervisor_main_pid_after: $(main_pid)"
  echo "supervisor_active_state: $(active_state "$UNIT")"
  echo "peer_worker_run_id: $PEER_RUN_ID"
  echo "peer_worker_pid_alive_final: $PEER_ALIVE_FINAL"
  echo "pending_approvals_for_run: $LEFTOVER"
  echo "live_queue_pending_count: $(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["pending"]))' "$LIVE_QUEUE" 2>/dev/null)"
} > "$TL/08-final.txt"

# Coordinator finding #4: peer survival is enforced at the end too.
[[ "$PEER_ALIVE_FINAL" == yes ]] || fail abort_peer_lost_final 33 \
  "peer worker $PEER_RUN_ID (pid $PEER_PID) is gone at the end of the window"

say "restoring watchdog timer and the shipped KillMode"
systemctl --user start "$WATCHDOG_TIMER"
rm -f "$DROPIN"
systemctl --user daemon-reload
sleep 1
{
  echo "watchdog_timer_state_restored: $(active_state "$WATCHDOG_TIMER")"
  echo "supervisor_kill_mode_restored: $(kill_mode)"
  echo "dropin_present: $([[ -e "$DROPIN" ]] && echo yes || echo no)"
} >> "$TL/08-final.txt"
[[ "$(kill_mode)" == "$KILL_MODE_BEFORE" ]] || fail abort_killmode_not_restored 34 \
  "KillMode is $(kill_mode), expected the shipped $KILL_MODE_BEFORE"

say "disarming the dead-man switch"
systemctl --user stop "$DEADMAN_UNIT.timer" >/dev/null 2>&1
systemctl --user reset-failed "$DEADMAN_UNIT.service" "$DEADMAN_UNIT.timer" >/dev/null 2>&1
{
  echo "deadman_timer_state_final: $(active_state "$DEADMAN_UNIT.timer")"
  echo "watchdog_service_state_final: $(active_state "$WATCHDOG_SERVICE")"
  echo "unmanaged_supervisors_final: $(unmanaged_supervisors "$(main_pid)" | tr '\n' ' ')"
} >> "$TL/08-final.txt"

git -C "$WORKTREE" add "docs/evidence/runtime/$TASK_ID" >/dev/null 2>&1
cat > "$SIGNAL_DIR/commit-msg-final.txt" <<EOF
$TASK_ID: record approval resolution

Owned layer: live acceptance evidence - resolved test approval, final queue and
supervisor state after the boot-reconciliation proof, restored service state.
Not changing: supervisor behaviour, approval policy, deployment scripts.
Composes with: the capture commit from the same live run.

LLM-Agent: Claude2
Task-ID: $TASK_ID
Reviewer: Codex2
Verified: driver capture only; suites recorded in preflight/
EOF
git -C "$WORKTREE" commit -F "$SIGNAL_DIR/commit-msg-final.txt" >/dev/null 2>&1 \
  && say "final commit: $(git -C "$WORKTREE" rev-parse --short HEAD)" \
  || say "final commit skipped (nothing staged)"
git -C "$WORKTREE" push -u origin "task/$TASK_ID" >/dev/null 2>&1 \
  && say "pushed task branch" || say "push failed"

# STOP GATE 2 finding #6: `terminal` (not `signal`) so the EXIT trap preserves
# this verdict instead of replacing it with driver_finished_rc_0.
terminal "proof_complete"
say "done"
