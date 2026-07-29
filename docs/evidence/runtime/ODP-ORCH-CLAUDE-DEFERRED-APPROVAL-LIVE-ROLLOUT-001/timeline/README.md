# timeline/

Receipts written by `runbook/live-boot-reconciliation-driver.sh`.

**The rollout window was opened once, on 2026-07-29 between 06:08:16Z and
06:10:39Z, and it completed: terminal verdict `proof_complete`, exit code 0.**
The receipts in this directory root are that run's. The earlier attempt - the
revision-5 run of 2026-07-29T04:31Z, which aborted at phase 1 with
`abort_killmode_probe` (exit 26), before the drop-in, the restart, the deferral
and any approval - is archived whole in `attempt-1-abort-killmode-probe/`,
together with its `/tmp/odp-rollout-driver` state and an account of the two hand
edits made to it. Nothing in this root belongs to that attempt.

## What each receipt is

| File | Written at | What it holds |
| --- | --- | --- |
| `00-before.txt` | 06:08:16Z | pre-window baseline: `MainPID=1197865`, shipped `KillMode=control-group`, no drop-in, the test worker run id and pid, the **peer** worker (`claude-20260729T055610Z-75ecff74`, task ODP-ORCH-ACTOR-REF-VALIDATION-001) with its pid bound to that run id, activity-log length, and the two bounded-wait budgets |
| `00-cgroup-before.txt` | 06:08:16Z | the unit cgroup before anything was touched |
| `00-deadman.txt` | 06:08:16Z | the dead-man unit, its +2708 s delay against the 1808 s of bounded waits, and its armed state |
| `00-killmode-probe.txt` | 06:08:16Z | phase 1: `KillMode=process` semantics proven on a throwaway transient unit before the real supervisor is touched |
| `01-after-stop.txt` | 06:08:26Z | the supervisor stopped (`inactive`, `MainPID=0`) with **both** the test worker and the peer worker still alive, watchdog timer and service down, and the surviving cgroup listing |
| `02-deferred-receipt.json` | 06:08:56Z | the real Claude `stop_reason=tool_deferred` receipt, with the deferred `Bash` tool-use id |
| `02-receipt-window.txt` | 06:08:56Z | the ordering fact the whole task turns on: the receipt landed while `supervisor_active_state=inactive`, so it can only be observed for the first time by boot reconciliation |
| `03-after-start.txt` | 06:09:01Z | restart `1197865 -> 1487837`, peer worker still alive, no unmanaged supervisor, post-restart cgroup |
| `04-supervisor-journal.txt`, `04-activity-log-after-restart.jsonl`, `04-events-for-run.txt` | 06:10Z | the journal across the window, the activity log after the restart, and the 5 events scoped to the test run id |
| `05-approval-record.json` | 06:09Z | the approval as boot reconciliation recorded it: `correlation_source: supervisor_deferred_tool_receipt`, not a generic failure |
| `05-assertion-post-boot.json` | 06:09Z | fail-closed ordering assertion, `PASS`: correlation at index 8, `first_generic_worker_failed_index: null`, `missing_process_finalizations: 0` |
| `05-approval-queue-live-after-boot.json`, `05-supervisor-state-after-boot.json` | 06:09Z | live queue and supervisor state at that moment |
| `06-resolve-live.txt`, `06-resolve-control.txt` | 06:09:04Z | both test approvals explicitly **denied** - the live one and the `PreToolUse` hook's orphan in the control queue - so the deferred command never ran and nothing was left pending |
| `07-assertion-final.json` | 06:10:34Z | the same assertion re-run after resolution, `PASS`: correlation still index 8, the first generic `worker_failed` at index 11 - *after* it, and it is the denial finalization |
| `07-approval-queue-live-final.json`, `07-supervisor-state-final.json` | 06:10:34Z | final queue and supervisor state |
| `08-final.txt` | 06:10:34Z | the run's closing summary: approval id, both assertions `PASS`, pid before/after, peer alive, 0 pending for the run, 0 pending in the live queue, watchdog and `KillMode` restored |
| `09-signal.txt`, `09-driver-log.txt`, `09-probe-commands.txt` | 06:10:38Z | the driver's own terminal signal (`proof_complete` / exit 0), its full log, and the phase-1 probe's per-command rc/stdout/stderr receipts, copied out of the volatile `/tmp/odp-rollout-driver` at closeout |
| `99-restore.txt` | 06:10:39Z | the EXIT trap's restoration receipt - the last thing the run wrote, and the only proof that the host was put back |

`05-*`/`07-*` state and queue dumps and the `*-before.json` captures are mode
`0600` because they are copies of live runtime state.

## Two post-run evidence corrections

Both were found by reviewer Codex2 after the run and fixed without re-running
anything; no captured timestamp, JSON body or verdict was altered.

1. `99-restore.txt` is written by the EXIT trap, which fires *after* the
   driver's own final commit and push, so the run could not commit it. It was
   left untracked in the worktree and is committed at closeout. Its contents
   were re-verified against the live host before committing (see
   `../README.md` section 6).
2. `08-final.txt` line 18 ended in a space. `unmanaged_supervisors_final` is
   emitted by driver line 1882 as a bare `tr '\n' ' '`, while the same probe at
   the EXIT-trap site (line 1295) is normalized with
   `sed 's/[[:space:]]*$//' | grep . || echo '<none>'` and a comment saying why.
   Only the trap site was fixed when that defect class was first caught. The
   committed receipt now ends at the colon; **an empty value means no unmanaged
   supervisor was found**, which is what `99-restore.txt` records as `<none>`
   for the same probe four seconds later. The driver itself is left byte-exact
   as executed - it is the artefact of record for this run, and editing it would
   break that.

## Starting another attempt

A file in here is never by itself evidence that a window ran; read the receipts.
Since revision 7 the separation between attempts is enforced rather than
trusted (coordinator STOP GATE 6), and since revision 8 the allowlist is exact
(reviewer STOP GATE 7, which found the revision-7 check fail-open). The driver
refuses to start (exit 50 / 51) unless:

* this directory holds a **regular file** named `README.md` and **directories**
  named `attempt-*`, and nothing else - no other file, no other directory, no
  symlink even under an allowed name, dotfiles included; and
* `/tmp/odp-rollout-driver` is absent or a **completely empty** directory - any
  entry at all is dirt, including the probe's `probe-child.pid` /
  `probe-commands.txt` / `probe-child.sh` and the dead-man's `deadman.log` /
  `deadman-restore.sh`, none of which the revision-7 check looked for.

The check runs before the log redirect and before the EXIT trap is installed, so
a refusal writes nothing and leaves the older attempt exactly as it was. Both
conditions are currently **unsatisfied on purpose**: this root holds the
completed run's receipts, and `/tmp/odp-rollout-driver` still holds its signal
directory. Any further attempt must archive them first:

```bash
TL=docs/evidence/runtime/ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001/timeline
A="$TL/attempt-<n>-<verdict>"
mkdir -p "$A"
git mv "$TL"/[0-9]*.txt "$TL"/*.json "$A"/          # whatever that run wrote
for f in verdict state exit_code driver.log; do
  sed 's/[[:space:]]*$//' "/tmp/odp-rollout-driver/$f" > "$A/tmp-$f.txt"
done
mv /tmp/odp-rollout-driver /tmp/odp-rollout-driver-attempt-<n>-archived-<date>
```

The four files above are the ones worth committing; everything else the run left
in the signal directory (`probe-*`, `deadman*`, `commit-msg-*`, `control-ids`)
travels with the moved directory. Move it, never delete it - and note that the
gate is satisfied only once the original path is gone or empty, so an emptied
directory left behind is fine but a half-cleared one is not.
