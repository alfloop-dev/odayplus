# Fleet Execution Brief: ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001

- Parent: ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-RACE-001 (done)
- Owner lane: fleet control plane / supervisor runtime
- Reviewer lane: Codex2 (independent review required before closeout). The
  earlier `Codex4` designation in this brief and in the task acceptance list is
  superseded: `Codex4` is not present in the live Supervisor agents config, so
  it could never have reviewed this task. Corrected under STOP GATE 4
  (2026-07-29T04:17:38Z).
- Branch: `task/ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001`
- Rollout source: merged `dev` commit `647970dae975f4008633a484cde1e63187035544` (PR #492)

## Objective

Deploy the reviewed boot-reconciliation fix into the supervisor launch tree and
the live runtime, restart the live service without disturbing unrelated workers,
and prove on the live fleet that a real Claude `tool_deferred` receipt becomes a
durable approval before any generic missing-process failure can consume the run.

## Deployment surface

| tree | role | file | before | after |
| --- | --- | --- | --- | --- |
| `/home/lupin/oday-plus` | systemd launch tree (`run-supervisor.sh`) | `.orchestrator/supervisor.py` | `f954e4bf…` | `f0b419cb…` |
| `/home/lupin/oday-plus-supervisor-live` | live runtime root (`runtime-root` pointer) | `.orchestrator/supervisor.py` | `f954e4bf…` | `f0b419cb…` |

`f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8` is the sha256
of blob `4c33259cec94f1ecd72f5c0bd318080907be83e4`, i.e. the file exactly as it
exists at the merge commit. Nothing else was deployed; unrelated dirty files in
both trees were preserved.

## Live proof design

The fix is a startup ordering fix, so the window is created explicitly rather
than raced: stop the supervisor (`KillMode=process`, unrelated workers keep
running) → let this task's own Claude worker emit a real
`stop_reason=tool_deferred` receipt for the harmless `wget --version` → start
the supervisor, so `reconcile_runtime_on_boot()` is provably the first code to
see the receipt.

Because the Claude CLI exits on a deferred tool, the post-deferral steps run in
`docs/evidence/runtime/ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001/runbook/live-boot-reconciliation-driver.sh`,
inside its own transient systemd unit outside the supervisor cgroup.

## Status

**Executed and passed.** Driver revision 10 opened the window once, on
2026-07-29 from 06:08:16Z to 06:10:39Z: terminal verdict `proof_complete`, exit
code 0. A real `tool_deferred` receipt landed at 06:08:56Z while the supervisor
was `inactive`; the restart (`MainPID 1197865 -> 1487837`) had boot
reconciliation record approval `apr-20260729T060857Z-5c04969e` with
`correlation_source: supervisor_deferred_tool_receipt`, ahead of any generic
`worker_failed` (correlation index 8, first generic failure index 11), with
`missing_process_finalizations: 0`. The unrelated peer worker survived the whole
window, both test approvals were denied so the probe command never ran, 0
approvals remain pending, and the EXIT trap restored `KillMode=control-group`,
the watchdog timer and a drop-in-free unit. Full narrative and receipts:
`docs/evidence/runtime/.../README.md` section 6 and `timeline/`.

The one earlier attempt, at 2026-07-29T04:31:10Z, aborted fail-closed at phase 1
after five seconds (`abort_killmode_probe`, exit 26) - before the drop-in, the
restart, the deferral and any approval - and is archived whole under
`timeline/attempt-1-abort-killmode-probe/`.

Remaining: independent review by Codex2, then closeout.

The driver reached revision 10 after nine STOP GATEs: six coordinator findings,
then seven, then two, then an assignment conflict, then one found by execution,
then one on the fix for that, then reviewer Codex2's finding that the fix
introduced a fail-open gate of its own, then the reviewer's finding that the
probe's own observability claim was overstated, and finally the reviewer's
finding that the scanner built to catch that exited 0 on violations. A tenth
STOP GATE followed the run itself, scoped to the evidence closeout rather than
to the driver - the untracked EXIT-trap receipt and a trailing space in
`timeline/08-final.txt`, both answered without re-running anything; see the
runtime README's "Post-run evidence corrections". STOP GATE 5's finding is that phase 1's `KillMode`
probe could never have passed in any revision - it re-created a `systemd-run`
transient unit under a name its own surviving child still held loaded, with
stderr discarded on both halves. Revision 6 answered that by writing a
persistent unit file under `~/.config/systemd/user`; STOP GATE 6 rejected it as
out of scope and as resting on a wrong conclusion. Only *re-creating* a
transient unit fails - `systemctl --user start` on the same already-loaded
transient `.service` is proven to work with the leftover child in its cgroup,
which is also what the real restart does. Revision 7 uses that minimal path,
writes no unit file at all, asserts the new MainPID and new child as well as the
old child's survival, cleans up by recorded ownership and cgroup with a residue
assertion that is part of the verdict, captures the rc and stderr of the probe's
mutating commands, and refuses to start on a dirty timeline or signal directory
so one attempt's receipts can never be read as another's (`--selftest` 23 → 39
checks).

STOP GATE 7 is reviewer Codex2's, on that last gate: `attempt_state_dirty()` was
fail-open. It listed the timeline root with `find -type f`, so an unexpected
directory or symlink passed, and probed the signal directory for four hardcoded
names, so the probe's own `probe-child.pid` / `probe-commands.txt`, the
dead-man's files, dotfiles and subdirectories passed - the reviewer's
reproduction returned CLEAN. Revision 8 makes the allowlist exact (a regular
`README.md` plus `attempt-*` directories; an absent or empty signal directory),
type-checks both roots and reports a deterministic first offender
(`--selftest` 39 → 50 checks, 15 on this gate). The same STOP GATE withdrew the
claim that everything after phase 1 was byte-identical to revision 3: phases 2-9
carry three non-executable deltas (one comment block, two `Reviewer:` trailer
lines), enumerated with a normalized diff in
`preflight/rev3-phase-2-9-delta.txt`, which leaves every gate, threshold, exit
code and phase ordering there unchanged since revision 3.

STOP GATE 8 is the reviewer's second, and it is the same class of defect that hid
the revision-5 bug: the driver claimed every `systemctl`/`systemd-run` call in the
probe was timeout-bounded with rc and stderr captured, when only the five
mutating ones were. `show -p ControlGroup`, `reset-failed` (with stderr sent to
`/dev/null`) and the two `show -p MainPID` reads ran raw, and six further state
reads went through the shared unbounded readers - outside the dead-man budget as
well as unreceipted. Revision 9 routes every probe systemd operation through
`probe_cmd` (mutating) or `probe_query` (read-only), both of which preserve
stdout and record label, rc, stdout and stderr; makes receipt completeness part
of the phase-1 verdict; folds the two new timeouts and the exact call counts into
the budget (1388 → 1808 s, dead-man 2288 → 2708 s); and adds a static scan over
the probe region, exercised in `--selftest` against the driver itself and against
a fixture containing one raw call and one unbounded reader (`--selftest`
50 → 63 checks).

STOP GATE 9 is the reviewer's third, and it found that revision 9's scan repeated
the overclaim it was built to end: `probe_region_scan()` printed every violation
and then exited **0** whenever the sentinels were present, while the driver
header, `README.md`, the runbook and the commit message all said it fails on any
violation - and the committed reproduction recorded `raw=4 reader=6` followed by
`rc=0`. A caller written to the documented contract would have read revision 8's
ten bypasses as clean, and the revision-9 self-test asserted only the printed
counts, so it would not have caught that either. Revision 10 makes the return
code the verdict (0 clean, 2 violation, 1 no region located - the two failure
codes deliberately distinct) and asserts the code itself for a clean file, for
each violation alone and together, and for a file with no sentinels
(`--selftest` 63 → 71 checks). Detection is unchanged and the counts are
identical under both scanners. Revision 10 got its own exact-head recheck and is
the revision that ran. See `runbook/CONTINUATION.md` for the
finding-by-finding mapping, `preflight/killmode-probe-diagnosis.txt` for the
probe transcripts, `preflight/stop-gate-7-fail-open-reproduction.txt` for the
old-vs-new gate comparison,
`preflight/stop-gate-8-raw-probe-call-reproduction.txt` for the scanner run over
both revisions' probe regions (`raw=4 reader=6` against `raw=0 reader=0`) and
`preflight/stop-gate-9-scanner-exit-code-reproduction.txt` for the two scanners
run side by side (same counts, `rc=0` against `rc=2`).

## Fleet impact

- Supervisor downtime is bounded by the driver's own named wait constants
  (1808 s in total) and by an independent dead-man's switch armed at
  1808 s + 900 s = 2708 s, i.e. deliberately longer than the driver's longest
  legal run so it cannot fire mid-window.
- The unrelated peer worker must survive the restart; the driver enforces this
  after the stop, after the start and at the end, binding the pid to its run id.
  Run ids change on every dispatch and are resolved from live supervisor state at
  execution time - never copied from a note. In the run that happened, the peer
  resolved to `claude-20260729T055610Z-75ecff74` on
  `ODP-ORCH-ACTOR-REF-VALIDATION-001`, and it was alive at all three checks.
- Both `pantheon-supervisor-watchdog.timer` **and**
  `pantheon-supervisor-watchdog.service` are stopped for the window and asserted
  inactive: the watchdog is a 60 s `Type=oneshot` unit that recovers the
  supervisor with a raw `Popen` rather than via systemd, so stopping only the
  timer leaves an in-flight run able to spawn a second, unmanaged supervisor.
  The driver additionally asserts that no unmanaged `supervisor.py` exists
  before, during and after the window.
- Worker leases are 1800 s and the window is minutes, so no live worker can lose
  its lease across the restart.

## Evidence index

- `docs/evidence/runtime/ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001/README.md` - rollout record and proof narrative
- `.../preflight/` - before/after hashes, dirty-state capture, test, Ruff and diff output
- `.../timeline/` - the completed run's receipts, indexed file by file in `timeline/README.md`: stop/deferral/start timeline, the `tool_deferred` receipt, activity-log events, journal, approval queue snapshots, both ordering assertions, both denials, the driver's signal and log (`09-*`) and the EXIT trap's restoration receipt (`99-restore.txt`)
- `.../runbook/` - the driver script as executed, and continuation notes

## Downstream

`ODP-DEPLOY-WORKER-JOB-EXECUTION-001` is unblocked by the live proof: its worker
was the run whose deferred `docker build` approval was auto-pruned when the
pre-fix boot reconciliation failed the run generically. The notification is held
until Codex2's independent review passes, so that an unblock cannot rest on an
unreviewed proof.
