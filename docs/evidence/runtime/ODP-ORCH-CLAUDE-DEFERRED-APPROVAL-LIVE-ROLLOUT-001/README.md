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
throwaway unit: a `KillMode=process` unit is started with a child process,
stopped (the old child must survive), and started again while that leftover is
still in the cgroup. The restart must produce a new, non-zero, *different*
MainPID and a new child, and the old child must still be alive afterwards. Each
pid is bound to the probe's own argv marker, so a recycled pid cannot satisfy an
assertion. If anything fails - including the probe's own cleanup - the driver
aborts without ever stopping the supervisor.

The throwaway unit is created **once**, with `systemd-run`, and restarted with
`systemctl --user start` on that same already-loaded unit. That has been the
shape since revision 7 and is unchanged in revisions 8 and 9; the distinction is not
cosmetic. The first time the driver was actually run
it aborted at phase 1 (2026-07-29T04:31:15Z, `abort_killmode_probe`) because the
probe tried to *re-create* the transient unit under a name its own surviving
child still held loaded, which `systemd-run` refuses. Only re-creation fails:
starting the existing unit is proven to work on this host, with the leftover in
its cgroup, and it is also what the real restart does. Revision 6 answered the
same abort by writing a *persistent* unit file under `~/.config/systemd/user`;
that was out of scope for this driver and unnecessary, and STOP GATE 6 rejected
it. This driver writes no unit file anywhere - the only thing it puts in that
directory is the drop-in it owns and removes. See
`preflight/killmode-probe-diagnosis.txt` for the transcripts and
`runbook/CONTINUATION.md` § STOP GATE 5 / 6.

Phase 1's cleanup is ownership- and cgroup-based and runs on every exit path
(from the probe itself and from `restore_all()`): the recorded old and new pids
plus every pid seen in the probe unit's cgroup are SIGTERMed, then SIGKILLed,
then waited for, and the probe asserts that no marked process, no cgroup and no
loaded unit is left (`not-found` / `inactive` / `dead`).

Since revision 9 every systemd operation in phase 1 - the mutating ones through
`probe_cmd`, the read-only state queries through `probe_query` - is bounded by a
`timeout` and produces one receipt line carrying its label, rc, stdout and
stderr in `timeline/00-killmode-probe.txt`. Discarded stderr is the sole reason
the revision-5 defect survived five review passes, and revisions 7 and 8 claimed
this coverage while four raw calls (`show -p ControlGroup`, `reset-failed` -
itself `>/dev/null 2>&1` - and the two `show -p MainPID` reads) plus six calls
into the unbounded shared readers were still bypassing it (STOP GATE 8).

Two things stop that claim from drifting again. Receipt completeness is part of
the phase-1 verdict: the probe lists the labels a full-path run must produce and
cannot report `pass` if any is missing or if the receipt count exceeds the
declared call budget. And a static scan (`probe_region_scan`) parses the driver
between its `probe-region` sentinels and fails on any raw `systemctl` /
`systemd-run` call or any unbounded-reader call that is not routed through the
helpers; `--selftest` runs it against the driver itself and against a fixture
that deliberately contains one of each, so it cannot pass vacuously.
`preflight/stop-gate-8-raw-probe-call-reproduction.txt` runs the same scanner
over revision 8's probe region (`raw=4 reader=6`) and revision 9's (`raw=0
reader=0`), both regions extracted mechanically rather than retyped.

### One attempt per directory

`timeline/` holds the receipts of the attempt in progress and nothing else. A
finished attempt is archived under `timeline/attempt-<n>-<verdict>/` together
with its `/tmp/odp-rollout-driver` state, and the driver refuses to start
(exit 50 / 51) while the timeline root or the signal directory still holds a
previous attempt's artefacts. The check runs before the log redirect and before
the EXIT trap, so a refusal writes nothing and leaves the older attempt exactly
as it was. Attempt 1 is archived at
`timeline/attempt-1-abort-killmode-probe/`.

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
  is derived from the driver's own named wait constants - 1808 s of bounded
  waiting plus 900 s of margin, so 2708 s - and the driver aborts if that
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
with 63 checks against throwaway processes and a throwaway signal directory: the
dead-man budget inequality, pid/run-id binding (foreign, empty, dead pids all
rejected), token-exact unmanaged-supervisor detection (a decoy carrying
`supervisor.py` inside a longer argument must not be reported), a replay of the
revision-2 verdict-overwrite regression, the clean-attempt gate (15 checks since
revision 8, covering every shape revision 7 let through), and -
since revision 6 - phase 1's `KillMode` probe itself, run through the same
`killmode_probe()` the driver calls, so that gate can be proven green without
opening the window. Since revision 7 that section also asserts what the probe
must *not* do: no unit file in the user unit directory, that directory's entry
count unchanged, and no residual pid, cgroup or loaded unit afterwards. The only
unit it creates is its own throwaway transient probe, which it removes again; it
never touches `pantheon-supervisor.service`, the drop-in, the dead-man or any
queue. Since revision 9 it also asserts that phase 1's receipts are complete
(every required label present, count within the declared budget, every line
carrying rc, stdout and stderr) and runs the static probe-bypass scan described
above, together with its negative control. Output:
`preflight/driver-gate-selftest.txt`.

The live pre-fix behaviour of the same host is already on record in
`docs/evidence/runtime/ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-RACE-001/README.md`:
run `claude-20260729T024031Z-b69e0aff` deferred
`toolu_01Tz33HVmkHyCxhyP149Q96m`, and at `2026-07-29T02:41:37Z` boot
reconciliation reported `missing_process_workers_failed=1` and finalized the run
with the generic worker-exit reason. That is the counterfactual this rollout has
to eliminate.

## 6. Live proof result

**Not yet run. The rollout window has never been opened.**

Re-verified against the live host at 2026-07-29T04:56Z, i.e. after the one
attempt so far and after its artefacts were archived: the `timeline/` root holds
no receipts (attempt 1 is in `timeline/attempt-1-abort-killmode-probe/`),
`/tmp/odp-rollout-driver/` does not exist (moved aside to
`/tmp/odp-rollout-driver-attempt-1-archived-20260729`, not deleted), the
supervisor is still on its pre-task `MainPID=1197865` (started 02:37:14Z),
`KillMode` is still `control-group`, the drop-in directory is empty, there is no
transient rollout or dead-man unit, no process carries a probe marker, and no
approval exists for any test run in either queue.

Execution has been blocked by seven STOP GATEs - six from the coordinator, the
seventh from reviewer Codex2. The first
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
mapping table in `runbook/CONTINUATION.md`.

The fourth (2026-07-29T04:17:38Z) was an assignment conflict rather than a
driver defect: ownership had been auto-reassigned to a quota-exhausted lane, and
the reviewer recorded in this evidence set - `Codex4` - is not present in either
Supervisor `agents` config, so it could never have been dispatched. Owner is
Claude2 and reviewer is Codex2, now consistently across `ai-status.json`, the
fleet brief, `runbook/CONTINUATION.md` and the driver's two capture-commit
trailer templates. Revision 5 is that text change plus one residual overclaim
found while re-running the checks (this section previously said the gate
self-test had 16 checks; it had 15). No executable behaviour differs from
revision 3.

The coordinator lifted the exact-head gate on `05c3f59d` at 04:26:08Z and the
driver was launched at 04:31:10Z - the first time any revision of it actually
ran. It aborted 5 s later at phase 1 (`abort_killmode_probe`, exit 26), before
the drop-in and before any restart, deferral or approval, and the EXIT trap left
`MainPID=1197865` active on the shipped `KillMode=control-group`. The finding is
that phase 1's probe could never have passed in any revision: it restarted a
`systemd-run` transient unit under a name its own surviving child still held
loaded, and both halves discarded stderr, so five review passes never saw
systemd's refusal. The property itself was never in doubt - the abort receipt
records `probe_child_survived_stop: yes`, and it holds when the probe is done the
way the real restart is done. Revision 6 fixed the probe, added the
after-restart assertion, and made phase 1 provable from `--selftest`, but it did
so by writing a persistent unit file under `~/.config/systemd/user`.

The sixth (2026-07-29T04:43:36Z, restated by reviewer Codex2 at 04:45:59Z)
rejected that remedy and the conclusion behind it. A persistent user unit is out
of scope for this driver and was never necessary: only *re-creating* a transient
unit under a loaded name fails, while `systemctl --user start` on the same
already-loaded transient `.service` is proven to work with the leftover child
still in its cgroup. Revision 7 therefore creates the probe unit once with
`systemd-run` and restarts that same unit; removes `SYSTEMD_USER_DIR` and every
unit-file write; asserts the new MainPID and new child as well as the old
child's survival, each bound to a probe-owned argv marker; makes cleanup
ownership- and cgroup-based on every exit path with a residue assertion that is
part of the verdict; captures the rc and stderr of every *mutating* probe
command instead of discarding them (it claimed to cover every probe command;
that claim was false and is answered by revision 9 below); archives attempt 1
under
`timeline/attempt-1-abort-killmode-probe/` and refuses to start on a dirty
timeline root or signal directory (exit 50 / 51). The gate self-test grew from
23 to 39 checks. Revision 7 is a driver change, so it needs a fresh coordinator
exact-head recheck: the `05c3f59d` lift covered that head only.

The seventh (reviewer Codex2, 2026-07-29T05:07:52Z) is the first STOP GATE
raised by the reviewer rather than the coordinator, and it found that revision
7's new clean-attempt gate was itself fail-open. `attempt_state_dirty()` listed
the timeline root with `find -type f`, so an unexpected *directory* or a symlink
passed, and it probed the signal directory for four hardcoded names, so the
probe's own `probe-child.pid` / `probe-commands.txt` / `probe-child.sh`, the
dead-man's `deadman.log` / `deadman-restore.sh`, dotfiles, subdirectories and
anything else passed. The reviewer's own reproduction -
`timeline/unexpected-receipts` plus `signal/probe-commands.txt` - returned
CLEAN. Revision 8 makes the allowlist exact (a *regular file* `README.md` plus
`attempt-*` *directories* in the timeline root; an absent or completely empty
signal directory), type-checks both roots, and reports a deterministic first
offender. The gate self-test grew from 39 to **50** checks, 15 of them on this
gate; the old and new implementations are run side by side over twelve fixtures
in `preflight/stop-gate-7-fail-open-reproduction.txt`.

The same STOP GATE withdrew this document's claim that everything downstream of
phase 1 was "byte-identical to revision 3". It is not: phases 2-9 carry three
deltas, all non-executable - one comment block (the STOP GATE 3 correction) and
two `Reviewer: Codex4` → `Codex2` trailer lines in the commit-message heredocs.
`preflight/rev3-phase-2-9-delta.txt` enumerates all three and shows the
normalized slice (comment-only and blank lines removed) to be 389 lines on each
side with exactly those two differing, so every gate, threshold, exit code, wait
constant and phase ordering in phases 2-9 is unchanged since revision 3.
Revision 8 is a driver change and needs its own exact-head recheck.

The eighth (reviewer Codex2, 2026-07-29T05:26:49Z) accepted revision 8's
allowlist and evidence corrections and found the next overclaim of the same
family: the driver header and this document said every `systemctl` /
`systemd-run` call in the probe was timeout-bounded with rc and stderr captured,
and only the five *mutating* calls were. Still raw in revision 8:
`show -p ControlGroup` in `probe_record_cgroup_pids`; `reset-failed` in
`probe_cleanup`, with its stderr sent to `/dev/null`; and the two
`show -p MainPID` reads in `killmode_probe` - plus six calls into the shared
unbounded readers for the load / active / sub states. Unbounded means outside
the dead-man budget as well as unreceipted. Revision 9 routes every one of them
through `probe_cmd` (mutating) or `probe_query` (read-only), both of which
preserve stdout and record label, rc, stdout and stderr; makes receipt
completeness part of the phase-1 verdict; declares the two timeouts and the exact
call counts and folds them into the budget (1388 → **1808** s, delay 2288 →
**2708** s); and adds the static probe-bypass scan with its negative control. The
gate self-test grew from 50 to **63** checks.
`preflight/stop-gate-8-raw-probe-call-reproduction.txt` shows the same scanner
reporting `raw=4 reader=6` on revision 8 and `raw=0 reader=0` on revision 9.
Revision 9 is a driver change and needs its own exact-head recheck.

What has been completed and is safe to review now: the deployment (section 2),
its verification (section 3), the restart-safety design (section 4), the
determinism argument (section 5), the fail-closed assertion with its 11-case
self-test, and the driver's own 63-check gate self-test.

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
