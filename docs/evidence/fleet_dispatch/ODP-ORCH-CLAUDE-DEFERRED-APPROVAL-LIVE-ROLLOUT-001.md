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

Not executed. The rollout window has never been opened. The driver ran for the
first time at 2026-07-29T04:31:10Z, on the head the coordinator had lifted the
gate for, and aborted fail-closed at phase 1 after five seconds
(`abort_killmode_probe`, exit 26) - before the drop-in, the restart, the
deferral and any approval. As of 04:56Z, after that abort and after its
artefacts were archived under `timeline/attempt-1-abort-killmode-probe/`, the
supervisor is still on its pre-task `MainPID=1197865`, `KillMode` is still the
shipped `control-group`, the drop-in directory is still empty, the dead-man was
never armed, `~/.config/systemd/user` is unchanged and no approval exists for
any test run in either queue.

The driver is at revision 7 after six STOP GATEs: six coordinator findings, then
seven, then two, then an assignment conflict, then one found by execution, and
now one on the fix for that. STOP GATE 5's finding is that phase 1's `KillMode`
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
assertion that is part of the verdict, captures every probe command's rc and
stderr, and refuses to start on a dirty timeline or signal directory so one
attempt's receipts can never be read as another's (`--selftest` 23 → 39 checks).
Everything after phase 1 is byte-identical to revision 3, as were revisions 4
and 5 (a documentation correction and a reviewer/ownership correction). Revision
7 needs a fresh coordinator exact-head recheck; the previous lift covered one
exact head only. See `runbook/CONTINUATION.md` for the finding-by-finding
mapping and `preflight/killmode-probe-diagnosis.txt` for the probe transcripts.

## Fleet impact

- Supervisor downtime is bounded by the driver's own named wait constants
  (1388 s in total) and by an independent dead-man's switch armed at
  1388 s + 900 s = 2288 s, i.e. deliberately longer than the driver's longest
  legal run so it cannot fire mid-window.
- The unrelated peer worker must survive the restart; the driver enforces this
  after the stop, after the start and at the end, binding the pid to its run id.
  At the time of writing the peer is Claude3 on
  `ODP-FORECAST-AUTHORITATIVE-HISTORY-BACKFILL-001`, but run ids change on every
  dispatch and are resolved from live supervisor state at execution time - never
  copied from a note.
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
- `.../timeline/` - stop/deferral/start timeline, receipt, activity-log events, journal, approval queue snapshots
- `.../runbook/` - the driver script and continuation notes

## Downstream

`ODP-DEPLOY-WORKER-JOB-EXECUTION-001` is unblocked by the live proof: its worker
was the run whose deferred `docker build` approval was auto-pruned when the
pre-fix boot reconciliation failed the run generically.
