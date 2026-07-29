# ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001

Roll the merged Claude deferred-approval boot reconciliation (PR #492) into the
Pantheon supervisor launch tree and the live runtime, and prove on the live
system that a real Claude `tool_deferred` receipt is turned into an approval by
boot reconciliation instead of being eaten by the generic missing-process
failure path.

## 1. Rollout source

| item | value |
| --- | --- |
| merged `dev` commit | `647970dae975f4008633a484cde1e63187035544` |
| `.orchestrator/supervisor.py` blob at that commit | `4c33259cec94f1ecd72f5c0bd318080907be83e4` |
| sha256 of that blob | `f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8` |
| reviewed change | boot reconciliation reads the flushed Claude result and correlates a deferred-tool receipt *before* the missing-PID branch (27 added lines) |

The task worktree is checked out at exactly that merge commit, so the deployed
bytes are the reviewed bytes.

## 2. What was deployed, and where

Pantheon runs from two trees:

* `/home/lupin/oday-plus` - the **service launch tree**. The systemd unit
  `pantheon-supervisor.service` runs `scripts/run-supervisor.sh` from here.
* `/home/lupin/oday-plus-supervisor-live` - the **live runtime root**, named by
  `/home/lupin/oday-plus/.orchestrator/runtime-root`. `run-supervisor.sh` `cd`s
  here and executes `python3 -u .orchestrator/supervisor.py --verbose`, so this
  copy is the one actually imported by the running service.

Both copies were pre-fix (`f954e4bf…`, blob `b5d60d0e`, the PR #490 state) and
both were replaced with the exact merge-commit content. Only
`.orchestrator/supervisor.py` was touched in either tree.

```text
sha256  BEFORE (both trees)  f954e4bf19e3ee7fea3a849c033de3b96f920a6280ca7e8ccb4a20013c7c6bfa
sha256  AFTER  (both trees)  f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
sha256  merge-commit blob    f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8
```

Unrelated dirty state in both trees was preserved - notably the live runtime's
uncommitted `.orchestrator/permission_broker.py` change (12 insertions,
`SAFE_TOOLS` expansion) and the launch tree's 70+ modified files. `preflight/`
records the full before/after `git status --porcelain` for both trees.

Backups of the replaced files are kept outside the repository at
`/tmp/odp-rollout-backup/{live,control}-supervisor.py.bak` for a one-command
rollback.

## 3. Verification of the deployed content

Run in the task worktree (identical bytes to both deployed copies):

```text
python3 -m unittest discover -s .orchestrator -p 'test_approval_queue.py'
# Ran 9 tests ... OK

python3 -m unittest discover -s .orchestrator -p 'test_supervisor.py'
# Ran 224 tests ... OK

python3 -m unittest test_supervisor.DeferredApprovalCorrelationTests -v
# Ran 6 tests ... OK  (both boot-reconciliation regressions included)

ruff check .orchestrator/approval_queue.py .orchestrator/supervisor.py \
           .orchestrator/test_approval_queue.py .orchestrator/test_supervisor.py
# All checks passed!   (ruff 0.15.20 from the project venv; `uv` is not on PATH
#                       inside the worker environment, so the pinned venv binary
#                       was used directly)

ruff check /home/lupin/oday-plus-supervisor-live/.orchestrator/supervisor.py \
           /home/lupin/oday-plus/.orchestrator/supervisor.py
# All checks passed!   (the deployed files themselves)

git diff --check 647970dae975f4008633a484cde1e63187035544   # exit 0

python3 -m py_compile <both deployed files>   # OK
```

The whitespace check is quoted against the merge SHA deliberately. The original
receipt used a bare `git diff --check`, which only inspects unstaged changes to
*tracked* files - and at that moment this entire evidence directory was still
untracked, so the clean exit was vacuous and real trailing whitespace survived
in `preflight/preflight.md`. That was STOP GATE 3 finding 1; see
`runbook/CONTINUATION.md`. Both forms are preserved in
`preflight/ruff-and-diff.txt`, the narrow one marked retracted.

Three `test_supervisor.py` cases need the gitignored
`.orchestrator/config.json`; it was copied from the live runtime root into the
worktree before the run. Raw output: `preflight/tests-*.txt`,
`preflight/ruff-and-diff.txt`.

## 4. Restarting the supervisor without killing unrelated workers

`pantheon-supervisor.service` shipped with the systemd default
`KillMode=control-group`. A restart would therefore SIGTERM **every** process in
the unit cgroup: every `worker_runner.py`, every provider CLI, and every
long-running verification process those workers started. At rollout time that
included Claude3's `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001` run and its
Postgres-backed rehearsal processes.

The supervisor is designed to survive a restart - `reconcile_runtime_on_boot()`
refreshes the lease of any worker whose PID is alive and whose heartbeat is
fresh - so the correct approach is to stop only the supervisor process, via a
temporary drop-in at
`/home/lupin/.config/systemd/user/pantheon-supervisor.service.d/10-preserve-workers-on-restart.conf`:

```ini
[Service]
KillMode=process
```

**This drop-in is owned by the driver, not installed out of band.** It is
written in phase 2, its effect is asserted (`KillMode` must actually read
`process`, otherwise the window never opens), and it is removed with a
`systemctl --user daemon-reload` on every exit path: the clean path, the EXIT
trap, and the dead-man switch. The driver also asserts at the end that
`KillMode` is back to the value captured before the window. Widening the kill
scope of a live service is a temporary measure and must not outlive its window;
the resting state of this host is the shipped `KillMode=control-group`.

Before the real unit is touched at all, phase 1 validates the semantics on a
throwaway `systemd-run` unit: a `KillMode=process` unit is started with a child
process, stopped (the child must survive), and started again while the leftover
child is still in the cgroup (the start must succeed). If either half of that
fails, the driver aborts without ever stopping the supervisor.

Two further precautions cover the restart window:

* `pantheon-supervisor-watchdog.timer` **and**
  `pantheon-supervisor-watchdog.service` are stopped for the duration, and both
  are asserted `inactive` before the supervisor is touched. The watchdog does not
  use systemd to recover the supervisor - it `Popen`s
  `python3 -u .orchestrator/supervisor.py` directly - and it is a `Type=oneshot`
  unit on a 60 s cycle, so stopping only the timer leaves an in-flight run free
  to spawn a second, unmanaged supervisor that would consume the receipt by
  normal polling and void the proof. The timer is started again at the end of
  the window.
* An independent systemd dead-man's switch (`systemd-run --user
  --on-active=<computed>s --unit=odp-rollout-deadman`) is armed **by the driver**
  in phase 3, and its armed state is asserted before the window opens. The delay
  is derived from the driver's own named wait constants - 1130 s of bounded
  waiting plus 900 s of margin, so 2030 s - and the driver aborts if that
  inequality does not hold. A flat delay shorter than the legal waits would let
  a slow-but-valid run trip its own safety net, pulling the drop-in and
  restarting the supervisor mid-window. If the driver is SIGKILLed so the EXIT
  trap never runs, the switch still starts the supervisor and the watchdog timer
  and removes the drop-in. It is disarmed and reset in `restore_all()`, i.e. on
  every exit path rather than only the clean one.

### No unmanaged supervisor

The window is only exclusive if *no* supervisor is running in it. The driver
scans `/proc` for processes whose argv contains `.orchestrator/supervisor.py` as
an **exact token** - a substring match would flag every Claude worker, because
each one carries a wake-up prompt that mentions `supervisor.py` - and requires
the set (excluding the unit's own `MainPID`) to be empty before the window,
during it, and after the restart.

### Peer-survival enforcement

The whole point of `KillMode=process` is that unrelated workers keep running.
That is enforced, not merely observed: the unrelated peer worker must be alive
after the stop, after the start, and at the end of the window, and each check
binds the pid to its run id through `/proc/<pid>/cmdline` so a recycled pid
cannot fake survival. Any failure aborts the run.

## 5. How the live proof is made deterministic

The defect PR #492 fixes is a *startup ordering* race: a Claude worker flushes
`stop_reason=tool_deferred` and exits, and the supervisor restarts before its
normal poll parses that result. Reproducing it by luck would mean racing an
80-second poll interval. Instead the window is created explicitly:

1. The supervisor is **stopped first** (`KillMode=process`, so both the test
   worker and the unrelated Claude3 worker keep running).
2. The test worker then requests a deferred tool. The `PreToolUse` broker hook
   classifies it `defer`, the Claude CLI flushes
   `stop_reason=tool_deferred` with its `deferred_tool_use` payload, and the
   session ends. No supervisor is running, so no normal poll can consume the
   receipt.
3. The supervisor is started again. `reconcile_runtime_on_boot()` is therefore
   guaranteed to be the first code to see the receipt.

The deferred tool is `wget --version` - matched by the broker's
`^(curl|wget)(\s|$)` defer rule, and harmless in every outcome, including the
one where an approval is mistakenly allowed.

Because the Claude CLI exits the moment a tool is deferred, the worker that
triggers the deferral cannot observe what happens next. Everything after step 1
is executed by `runbook/live-boot-reconciliation-driver.sh`, launched in its own
transient systemd unit outside the supervisor cgroup, with hard timeouts and a
trap that restores the supervisor, the watchdog and the shipped `KillMode` on
every exit path.

### What counts as a pass

The driver cannot report success by falling through. `proof_complete` is
reachable only when all of the following hold; each one is an explicit abort:

| requirement | abort if violated |
| --- | --- |
| `KillMode=process` semantics validated on a throwaway unit first | `abort_killmode_probe` |
| the drop-in actually took effect | `abort_killmode_not_applied` |
| the dead-man switch armed, with a delay exceeding every bounded wait | `abort_no_deadman`, `abort_deadman_budget` |
| the watchdog timer *and* service are inactive for the window | `abort_watchdog_timer_active`, `abort_watchdog_service_active` |
| no unmanaged `supervisor.py` before / during / after the window | `abort_unmanaged_supervisor_before`, `abort_unmanaged_supervisor_in_window`, `abort_unmanaged_supervisor_after_start` |
| peer worker alive after stop / after start / at the end, pid bound to its run id | `abort_peer_killed`, `abort_peer_lost_on_start`, `abort_peer_lost_final` |
| the supervisor was *not* running when the receipt landed | `abort_supervisor_alive_in_window` |
| a parsable `tool_deferred` receipt exists | `abort_no_receipt`, `abort_unparsable_receipt` |
| the old test runner process is gone before the restart | `abort_worker_runner_alive` |
| the supervisor came back on a new, non-zero MainPID | `abort_supervisor_pid_invalid`, `abort_supervisor_pid_unchanged` |
| a **non-empty** approval id was created by boot reconciliation | `abort_no_approval_recorded` |
| that approval's `correlation_source` is `supervisor_deferred_tool_receipt` | `abort_wrong_correlation_source` |
| the ordering assertion passes after boot and again after resolution | `abort_assertion_post_boot`, `abort_assertion_final` |
| no approval for the test run is left pending in either queue | `abort_approval_left_pending` |
| the shipped `KillMode` was restored | `abort_killmode_not_restored` |

Two of these deserve a note, because without them a green run would have meant
nothing. If the **old runner is still alive** at restart, boot reconciliation
sees a live PID and simply refreshes the lease - the missing-process branch under
test is never reached. If the **MainPID is unchanged**, the unit never actually
stopped, so no boot reconciliation ran at all.

The terminal verdict is written to `/tmp/odp-rollout-driver/verdict` and
`/state`, and is preserved by the EXIT trap rather than overwritten by it; the
exit code goes to `/exit_code` separately.

The ordering assertion is `runbook/assert-boot-reconciliation.py`. It requires a
`worker_deferred_approval_recorded` / `_correlated` event carrying an approval
id for the test run, and rejects a `worker_deferred_approval_failed`, any
generic `worker_failed` that precedes the correlation, any `worker_failed`
carrying the boot "process missing" reason, and any `boot_reconciliation`
measurement reporting `missing_process_workers_failed > 0` inside the window -
that last shape being exactly what this host produced before the fix. An empty
or unreadable slice fails; nothing passes by default.

`runbook/selftest-assertion.sh` replays 11 synthetic slices through the
assertion - including the recorded pre-fix regression shape - and checks every
verdict. It touches nothing live. Output: `preflight/assertion-selftest.txt`.

`live-boot-reconciliation-driver.sh --selftest` covers the driver's own gates
with 15 checks against throwaway processes and a throwaway signal directory: the
dead-man budget inequality, pid/run-id binding (foreign, empty, dead pids all
rejected), token-exact unmanaged-supervisor detection (a decoy carrying
`supervisor.py` inside a longer argument must not be reported), and a replay of
the revision-2 verdict-overwrite regression. It also touches nothing live.
Output: `preflight/driver-gate-selftest.txt`.

The live pre-fix behaviour of the same host is already on record in
`docs/evidence/runtime/ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-RACE-001/README.md`:
run `claude-20260729T024031Z-b69e0aff` deferred
`toolu_01Tz33HVmkHyCxhyP149Q96m`, and at `2026-07-29T02:41:37Z` boot
reconciliation reported `missing_process_workers_failed=1` and finalized the run
with the generic worker-exit reason. That is the counterfactual this rollout has
to eliminate.

## 6. Live proof result

**Not yet run. The rollout window has never been opened.**

Re-verified against the live host at 2026-07-29T04:00Z: `timeline/` is empty,
`/tmp/odp-rollout-driver/` does not exist, the supervisor is still on its
pre-task `MainPID=1197865` (started 02:37:14Z), `KillMode` is still
`control-group`, the drop-in directory is empty, there is no transient rollout
or dead-man unit, and no approval exists for any test run in either queue.

Execution has been blocked by three CodexCoordinator STOP GATEs. The first
(2026-07-29T03:33:14Z) found six defects in driver revision 1; revision 2
answered them. The second (2026-07-29T03:48:39Z) found seven more, several of
which would have let a green run mean nothing - a still-live test runner, an
unasserted MainPID, a dead-man switch that fired inside its own window, a
watchdog able to spawn a second supervisor mid-proof, and a `proof_complete`
verdict the EXIT trap immediately overwrote. Revision 3 answered all seven.

The third (2026-07-29T04:07:50Z) found two *documentation* defects rather than
gate defects: a `git diff --check` receipt that was clean only because it was
run while the evidence files were untracked (real trailing whitespace was
sitting in `preflight/preflight.md:127`), and a backwards explanation of the
runner-gone gate that claimed a recycled pid aborts when in fact it reads as
"gone". Revision 4 corrects both without changing any gate logic; see the
mapping table in `runbook/CONTINUATION.md`. It needs a coordinator/reviewer
recheck before the window is opened.

What has been completed and is safe to review now: the deployment (section 2),
its verification (section 3), the restart-safety design (section 4), the
determinism argument (section 5), the fail-closed assertion with its 11-case
self-test, and the driver's own 16-check gate self-test.

## 7. Approval resolution

*(filled in from `timeline/06-resolve-*.txt` after the proof run)*

No test approval exists yet, and neither queue holds a pending item for this
task. The driver denies the probe approval in both the live and the control
queue - the `PreToolUse` hook writes to the control tree's queue, which is a
different file from the one the live supervisor reads - filtering strictly by
`worker_run_id` so the unrelated pending approvals in the control queue are
untouched, and then asserts that nothing for the test run remains pending.

## 8. Scope

Changed on disk so far: `.orchestrator/supervisor.py` in the two Pantheon trees,
and this evidence directory. The systemd drop-in is *transient* - created and
removed inside the driver's window - so it is not a persistent change to the
host. No `apps/**`, `modules/**`, no deployment script, no Package 10 product
API worker deployment, and no design archive file was touched. The repository
branch for this task carries evidence only - the supervisor fix itself is
already in `dev` via PR #492.
