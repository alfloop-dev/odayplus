# Continuation notes for ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001

Read this first if you are a re-dispatched Claude worker on this task.

Owner: Claude3 (was `Claude2`) · Reviewer: Codex2 (was `Codex4` until STOP GATE 4;
see below)

## Status: the live proof HAS been run, and passed

**Do not run the driver again.** Driver revision 10 opened the window once, on
2026-07-29 from 06:08:16Z to 06:10:39Z, and completed: terminal verdict
`proof_complete`, exit code 0. Running it a second time would restart the live
supervisor for nothing and invalidate the receipts already committed and
reviewed. Verified against the live host at closeout, 2026-07-29T06:16Z:

| checked | actual state |
| --- | --- |
| `systemctl --user show pantheon-supervisor.service` | `ActiveState=active`, `MainPID=1487837` - the post-restart pid recorded in `timeline/03-after-start.txt` and `99-restore.txt` |
| `KillMode` | back to the shipped `control-group` |
| `DropInPaths` | empty - the transient `KillMode=process` drop-in was removed on exit |
| `pantheon-supervisor-watchdog` | `.timer` `active`, `.service` `inactive` - both restored |
| `pantheon-supervisor-deadman.timer` | `not-found` - the dead-man was disarmed and collected |
| unmanaged `supervisor.py` | none, on an independent re-run of the driver's own scan excluding `1487837` |
| `/tmp/odp-rollout-driver/` | present, holding this run's signal files; `verdict`/`state`/`exit_code`/`driver.log`/`probe-commands.txt` are copied into `timeline/09-*.txt` |
| `timeline/` root | this run's receipts; attempt 1 remains under `timeline/attempt-1-abort-killmode-probe/` |
| approval queues | 0 pending for the test run and 0 pending in the live queue; both test approvals explicitly denied |

What the run proved is written up in `../README.md` section 6, receipt by
receipt in `../timeline/README.md`, and the approval resolution in section 7.
Read those rather than reconstructing it from here.

A file under `timeline/` is still **not** by itself evidence that a window ran -
attempt 1's receipts sit in the same tree and prove an abort. Read the receipts.

What *is* real and already finished:

1. **Deployment - done and verified.** `.orchestrator/supervisor.py` from merge
   commit `647970dae975f4008633a484cde1e63187035544` is deployed to both
   `/home/lupin/oday-plus` and `/home/lupin/oday-plus-supervisor-live`; both
   hash `f0b419cb3fbdff8a3dfbd5fcc9ee7dfd06b005f258a8f13d556e922d06995ee8`,
   equal to the blob at the merge commit. Backups of the replaced files are at
   `/tmp/odp-rollout-backup/{live,control}-supervisor.py.bak`.
2. **Preflight - done.** Test suites, Ruff, `py_compile` and
   `git diff --check 647970dae975f4008633a484cde1e63187035544` all pass; raw
   output in `preflight/`. The against-merge-SHA form is the one that counts -
   see STOP GATE 3 finding 1 below for why the earlier bare `git diff --check`
   receipt was clean vacuously.
3. **Assertion self-test - done.** `runbook/selftest-assertion.sh` replays 11
   synthetic activity-log slices through the acceptance assertion, including the
   exact pre-fix regression shape from RACE-001. Output:
   `preflight/assertion-selftest.txt`.
4. **Driver gate self-test - done.** `live-boot-reconciliation-driver.sh
   --selftest` exercises the STOP GATE 2 gates, phase 1's KillMode probe (since
   revision 6), the clean-attempt gate (since revision 7, hardened in
   revision 8) and, since revision 9, phase 1's receipt completeness plus the
   static probe-bypass scan and its negative controls - whose *return codes* are
   asserted since revision 10: 71 checks. Output:
   `preflight/driver-gate-selftest.txt`.

Both self-tests touch nothing live and are safe to re-run at any time.

## Why the window stayed closed for so long

The driver was blocked nine times before it ran - six by the coordinator, three
by reviewer Codex2 - reaching **revision 10**, which is the revision that
executed the successful window. Revision 5 was the only other revision ever
executed, and only as far as phase 1; do not resurrect any earlier revision.

The executable changes since revision 3 are confined to the phase-1 probe
(revisions 6, 7 and 9), the clean-attempt gate that runs before it (revisions
7 and 8) and the `--selftest`-only static scan (revisions 9 and 10), plus the
wait-budget constants those probe changes are counted in.
Phases 2-9 carry three deltas from revision 3, all non-executable, and
they are enumerated - not asserted - in
`../preflight/rev3-phase-2-9-delta.txt`: one comment block (the STOP GATE 3
correction) and two `Reviewer: Codex4` → `Codex2` trailer lines inside the
commit-message heredocs. With comment-only and blank lines removed from both
sides, the phase-2-to-EOF slice is 389 lines on each side and exactly those two
lines differ, so every gate, threshold, exit code, wait constant and phase
ordering in phases 2-9 is unchanged. Earlier revisions of this file said
"byte-identical"; that was wrong (STOP GATE 7 finding 2) and is withdrawn.

### STOP GATE 1 (2026-07-29T03:33:14Z) - six findings, answered in revision 2

The drop-in became driver-owned and is removed on every exit path; an empty
approval id became a hard abort and the approval must carry
`correlation_source=supervisor_deferred_tool_receipt`; ordering became an
external fail-closed assertion run twice; peer survival became enforced with the
pid bound to its run id; the dead-man switch became driver-armed; and phase 1
gained a throwaway-unit probe of `KillMode=process` semantics.

### STOP GATE 2 (2026-07-29T03:48:39Z / 03:51:50Z) - seven findings, answered in revision 3

| # | finding | what revision 2 actually did | revision 3 |
| --- | --- | --- | --- |
| 1 | require the old test runner gone | recorded `runner_process_exited` and ignored it - with the runner alive, boot reconciliation refreshes the lease and never takes the deferred path, so a pass would be vacuous | hard gate `abort_worker_runner_alive` (exit 46), bound to the run id so that only the *original* run-id-bound process blocks the restart (see the STOP GATE 3 correction below) |
| 2 | require a new MainPID, non-zero and changed | printed `pid A -> pid B` and asserted nothing; MainPID=0 or an unchanged pid would have been read as a successful restart | bounded wait for a non-zero MainPID, then `abort_supervisor_pid_invalid` (47) and `abort_supervisor_pid_unchanged` (48) |
| 3 | dead-man delay must exceed the bounded waits | flat `15min` against ~18min of legal waiting - a slow-but-valid run would trip its own safety net, pulling the drop-in and restarting the supervisor mid-window | every wait is a named constant; the delay is derived from their sum (**1130 s budget → 2030 s delay** at the time; revision 7's added phase-1 waits make it 1388 → 2288) and `abort_deadman_budget` (42) enforces the inequality |
| 4 | disarm/reset the dead-man on every exit | disarmed only on the clean path, so every abort left an armed timer that later restarted units behind the operator | disarm + `reset-failed` moved into `restore_all()`, which the EXIT trap and the clean path both call |
| 5 | stop/check the watchdog service and timer; prove no unmanaged supervisor | stopped the timer only. The watchdog is a `Type=oneshot` unit on a 60 s cycle that `Popen`s `.orchestrator/supervisor.py` directly, so an in-flight run survives `stop <timer>` and can put a second, unmanaged supervisor into the window - which would consume the receipt by normal polling and silently void the proof | timer **and** service stopped and both asserted `inactive` (43, 44); `unmanaged_supervisors()` scans `/proc` for an **exact argv token** and is asserted empty before the window (40), during it (45) and after the restart (49) |
| 6 | preserve the final `proof_complete` without EXIT overwrite | `signal proof_complete` was immediately overwritten by the trap's `driver_finished_rc_0`, so the documented success check could never observe it and abort reasons were erased too | `terminal()` records the verdict in `state` + `verdict`; `publish_final_state()` preserves it and writes the exit code separately; regression-tested in `--selftest` |
| 7 | remove stale claims / run ids / 15min text | - | this file, the fleet brief and `../README.md` rewritten; the driver's own commit trailers corrected from `LLM-Agent: Claude` to `Claude2` |

Token-exact matching in finding 5 is not a detail: every Claude worker's wake-up
prompt contains the string `supervisor.py` inside one long `-p` argument, so a
substring scan reports every worker on the box as a stray supervisor.

### STOP GATE 3 (2026-07-29T04:07:50Z) - two findings, answered in revision 4

Both findings are about **this document and the driver's comments overclaiming
what the code does**. No gate logic changed in revision 4; the executable
behaviour of revision 3 is unchanged and the live state is still untouched.

**(1) The `git diff --check` receipt was clean for the wrong reason.**
`preflight/ruff-and-diff.txt` recorded a bare `git diff --check` (exit 0) while
the whole evidence directory was still untracked (`?? docs/evidence/runtime/...`
appears in that same receipt). A bare `git diff --check` inspects unstaged
changes to *tracked* files only, so it never looked at these files. Once anchor
`44e9e62b` made them tracked, the coordinator's
`git diff --check 647970dae975f4008633a484cde1e63187035544` found real trailing
whitespace at `preflight/preflight.md:127` - the captured `ps` line for the
Claude worker, whose `-p` prompt argument ends in a full-width `。` followed by
two spaces. The whitespace is stripped, the receipt now records the *exact*
against-merge-SHA command rather than the narrow one, and the preflight summary
no longer asserts `git_diff_check: clean` on the strength of a command that
could not have failed.

**(2) The recycled-pid narrative was backwards.** The finding-1 row of the STOP
GATE 2 table above, and the comment block above `abort_worker_runner_alive` in
the driver (the coordinator cited them as CONTINUATION.md:63 and driver:625-626,
which are their pre-correction line numbers), claimed that binding the pid to
its run id makes a recycled pid *abort*.
It does the opposite: `pid_owned_by_run` returns false for a recycled foreign
pid, so `RUNNER_GONE=yes` and the gate passes. That is the correct behaviour for
this gate - the property it needs is that the *original* runner is gone - but the
stated reason was false.

The claim that survives: `abort_worker_runner_alive` blocks the restart while,
and only while, the original run-id-bound process is still running. Note that
`pid_owned_by_run` is used in the opposite direction for the peer checks
(`PEER_ALIVE_AFTER_STOP/_START/_FINAL` must be `yes`), and *there* the run-id
binding genuinely does stop a recycled pid from faking survival - that claim in
`../README.md` § Peer-survival enforcement is correct and unchanged.

The residual gap, stated plainly: the supervisor's own `pid_is_alive()`
(`supervisor.py:2331`) is pid-only with no run-id binding, so under pid recycling
the supervisor would refresh the lease while this gate reads "gone". That cannot
produce a false `proof_complete`: the lease-refresh path emits no
`worker_deferred_approval_recorded/_correlated` event, so
`assert-boot-reconciliation.py` fails and the driver aborts at the assertion
instead. With `pid_max=4194304` and the check running seconds after the runner
exits, the window is negligible in any case.

### STOP GATE 4 (2026-07-29T04:17:38Z) - assignment conflict, evidence-only fix

Not a driver finding. After a Claude2 terminal streak the orchestrator
auto-reassigned the task to Antigravity, whose Gemini weekly quota is exhausted;
the coordinator restored the configured owner (Claude2) and reviewer (Codex2)
and ordered the task-scoped evidence corrected before any live execution.

`Codex4` never existed as a dispatchable lane. Verified directly rather than
taken on trust:

```text
/home/lupin/oday-plus-supervisor-live/.orchestrator/config.json agents:
  claude claude2 claude3 antigravity antigravity2 codex codex2
  antigravity3 antigravity4 antigravity5 antigravity6 antigravity7
/home/lupin/oday-plus/.orchestrator/config.json agents:
  (same, without claude3)
```

Neither Supervisor config contains `codex4`, so no `Codex4` worker could ever
have been dispatched to review this task. (The name does appear in the
`agents` list of the live `ai-status.json`, but that list is a registry
accumulated from status writes - it is not the dispatch config, and it also
holds several obviously corrupt entries such as whole blocker sentences.)
Reviewer is `Codex2` in `ai-status.json`, and now also in this file, the fleet
brief and the driver's two capture-commit trailer templates.

One residual overclaim from revision 4 was found while re-running the checks and
is fixed here: `../README.md` § 6 still said "the driver's own 16-check gate
self-test". The self-test is 15 checks; the claim of having corrected 16→15
"everywhere" had missed this line. Corrected.

Nothing executable changed in revision 5: gate logic, thresholds, exit codes and
ordering are byte-identical to revision 4, and the live state is untouched.

### STOP GATE 5 (2026-07-29T04:31:15Z) - the driver was finally RUN, and phase 1 failed

The coordinator lifted the exact-head gate for `05c3f59d` at 04:26:08Z. The
driver was launched on it at 04:31:10Z with the two live run ids and aborted
5 s later at **phase 1**, verdict `abort_killmode_probe`, exit 26 - before the
drop-in, before any restart, before any deferral. The EXIT trap restored
everything and the live state is exactly as it was: `MainPID=1197865`,
`KillMode=control-group`, empty drop-in dir, dead-man inactive, watchdog timer
active.

**The finding: no revision of this driver could ever have passed phase 1.** The
probe re-created a `systemd-run` transient unit under the *same name* while its
leftover child was still alive - and a surviving child keeps that unit `loaded`,
so systemd-run refuses the name: `Unit ... was already loaded or has a fragment
file`. `reset-failed` does not unload an inactive unit. Both halves of the probe
sent stderr to `/dev/null`, so five revisions of review never saw the message.
The property itself was fine: `probe_child_survived_stop: yes` in the abort
receipt. Raw transcripts: `../preflight/killmode-probe-diagnosis.txt`.

Revision 6 answered this with a throwaway unit **file** started twice, plus an
after-restart assertion on the leftover, a `killmode_probe()` function exercised
by `--selftest` (15 → 23 checks), a `probe_cleanup()` for the unit file, and
phase 1's waits declared as constants inside the dead-man budget. STOP GATE 6
rejected the unit-file part; the rest carries into revision 7.

### STOP GATE 6 (2026-07-29T04:43:36Z, restated by reviewer Codex2 at 04:45:59Z) - revision 7

Revision 6 was never executed. Its diagnosis stands; its remedy does not.

The conclusion revision 6 drew - "a transient unit cannot be restarted" - is
wrong, and the fix it justified was out of scope: a driver for this task has no
business writing a persistent unit into `~/.config/systemd/user`.
**"Already loaded" is a refusal to CREATE, not a refusal to START.** Proven
independently at 04:39:19Z on a throwaway transient unit: after a
`KillMode=process` stop the unit is `inactive/dead/loaded` with the old child
alive, and `systemctl --user start` on that same `.service` returns rc=0 with a
new MainPID, a new child, and the old child still alive. That is also exactly
what the real restart does.

| change in revision 7 | why |
| --- | --- |
| the probe unit is created **once** with `systemd-run` and restarted with a bounded `systemctl --user start` on the same `.service` | the in-scope minimal path, proven at 04:39:19Z and re-proven by `--selftest` |
| `SYSTEMD_USER_DIR` and every unit-file write removed; `DROPIN_DIR` is a literal | the only file this driver puts in that directory is the drop-in it owns and removes |
| the probe asserts the **new** MainPID (non-zero, different) and the **new** child as well as the old child's survival | "active" alone cannot distinguish a real restart from a unit that never went down |
| every probe pid is bound to a probe-owned argv marker (`probe_owns_pid`) | a recycled pid must not satisfy an assertion, and must not be killed by cleanup |
| cleanup works from recorded ownership + the unit's cgroup, SIGTERM then SIGKILL, waits for old and new pids, and asserts no residual pid / cgroup and `not-found`/`inactive`/`dead` - as part of the verdict | a probe that leaks must fail closed, not proceed |
| every probe command bounded by `timeout`, rc and stderr captured into `timeline/00-killmode-probe.txt` | discarded stderr is the only reason the revision-5 defect survived five reviews |
| ^ that row overstated what revision 7 did: only the five *mutating* calls were routed through the helper. STOP GATE 8 found the four raw calls and six unbounded reader calls that remained; revision 9 is where "every probe command" becomes true | |
| clean-attempt gate: refuses to start (50 / 51) on a dirty timeline root or signal dir, before the log redirect and before the EXIT trap | one attempt's receipts must never be readable as another's; a refusal must not itself become dirt |
| attempt 1 archived under `timeline/attempt-1-abort-killmode-probe/`, including its `/tmp` verdict/state/exit_code/driver.log | same reason; the `/tmp` original was moved aside, not deleted |
| `PROBE_CMD_TIMEOUT_S` / `PROBE_REAP_TRIES` declared and counted in the dead-man budget (1130 → **1388** s, delay 2030 → **2288** s) | finding #3: the delay is derived from every declared wait |
| docs corrected wherever they said a transient unit cannot be restarted | only re-creation fails; `systemctl start` is proven PASS |

Self-test: 23 → **39** checks, all passing. Nothing else changed: the gates,
thresholds, exit codes and ordering of phases 2-9 are as described above -
unchanged in every executable respect since revision 3.

**The gate is closed again.** Revision 7 is a driver change, so it needs a fresh
coordinator exact-head recheck - the 05c3f59d lift explicitly covered that head
only.

### STOP GATE 7 (reviewer Codex2, 2026-07-29T05:07:52Z / 05:08:41Z) - revision 8

Revision 7 was never executed either. The live state is untouched and the two
findings below are the only changes in revision 8.

**Finding 1 (blocking, executable): `attempt_state_dirty()` was fail-open.**
The gate that revision 7 added to keep one attempt's receipts from being read as
another's did not enforce the allowlist it documented:

| what was left | why revision 7 accepted it |
| --- | --- |
| any unexpected **directory** in the timeline root, e.g. the reviewer's `timeline/unexpected-receipts/` | the scan was `find -maxdepth 1 -type f`, so only stray *regular files* were ever seen |
| any **symlink** in the timeline root, including one named `README.md` or `attempt-*` | same reason: a symlink is not `-type f` |
| `signal/probe-child.pid`, `probe-commands.txt`, `probe-child.sh`, `deadman.log`, `deadman-restore.sh`, `commit-msg-capture.txt`, `control-ids.txt`, dotfiles, subdirectories | the signal scan tested four hardcoded names (`verdict`, `state`, `exit_code`, `driver.log`) and nothing else |

The reviewer reproduced it directly: `timeline/unexpected-receipts` plus
`signal/probe-commands.txt` returned CLEAN. Revision 8 makes the allowlist
exact - the timeline root may hold a *regular file* named `README.md` and
*directories* named `attempt-*`, the signal dir must be absent or completely
empty - and type-checks the two roots themselves, so a symlinked or
non-directory root is dirt too. Entry types come from `find`'s `%y`, which does
not follow symlinks; listing is `-print0` + `LC_ALL=C sort` so the reported
first offender is deterministic. The two new verdicts (`timeline-root:` /
`signal-root:`) route to the existing exit codes 50 / 51.

Both implementations, extracted mechanically from git and from the working tree,
are run side by side over twelve fixtures in
`../preflight/stop-gate-7-fail-open-reproduction.txt`; the same shapes are
asserted by 15 checks in `--selftest` (39 → **50** checks, all passing).

**Finding 2 (evidence correctness): the "byte-identical" claim was false.**
See § Why the window is still closed above and
`../preflight/rev3-phase-2-9-delta.txt`. The claim is replaced by an enumeration
of the three actual deltas plus a normalized diff.

Nothing else changed: phases 1-9, the probe, the wait budget, the dead-man
derivation, the exit codes and the phase ordering are untouched by revision 8.

**The gate is closed.** Revision 8 is a driver change, so it needs its own
exact-head recheck; no earlier lift covers it.

### STOP GATE 8 (reviewer Codex2, 2026-07-29T05:26:49Z) - revision 9

Revision 8 was never executed either. The reviewer confirmed revision 8's
clean-attempt allowlist and evidence-delta corrections independently (worktree
clean and pushed, driver self-test 50/50, assertion self-test 11/11) and raised
one new blocking finding, in the same class as the one that hid the revision-5
defect.

**The finding: "every probe systemd call is bounded and receipted" was false.**
The driver header, `../README.md` and this runbook all said so from revision 7
on. Only the five *mutating* calls went through `probe_cmd`. Revision 9's static
scanner, run over revision 8's own probe region, reports `raw=4 reader=6`:

| what | where | how it ran in revision 8 |
| --- | --- | --- |
| `systemctl show -p ControlGroup` | `probe_record_cgroup_pids` | raw, unbounded, `2>/dev/null` |
| `systemctl reset-failed` | `probe_cleanup` | raw, unbounded, `>/dev/null 2>&1` |
| `systemctl show -p MainPID` x2 | `killmode_probe` | raw, unbounded, `2>/dev/null` |
| `load_state` x2, `active_state` x2, `sub_state` x2 | `probe_cleanup`, `killmode_probe` | via the shared readers: unbounded, unreceipted |

Unbounded matters twice over: such a call is outside the dead-man budget (a hung
`systemctl show` could outlast the derived delay), and a discarded stderr is
exactly how five review passes missed systemd's "already loaded" refusal.

Revision 9:

| change | why |
| --- | --- |
| every probe systemd operation goes through `probe_cmd` (mutating, `PROBE_CMD_TIMEOUT_S=30`) or `probe_query` (read-only, `PROBE_QUERY_TIMEOUT_S=10`); both preserve stdout and append one receipt line with label, rc, stdout and stderr | the finding, applied to read-only queries and `reset-failed` as well |
| the probe no longer calls `main_pid` / `kill_mode` / `active_state` / `load_state` / `sub_state` at all; those remain in use by phases 2-9, unchanged | the readers are unbounded by design and belong outside the probe |
| receipt completeness is an input to `PROBE_VERDICT`: `PROBE_REQUIRED_LABELS` must all be present and the receipt count must be within the declared budget | an unreceipted command is an unobserved command; it must fail closed, not read as a clean pass |
| both timeouts and the exact call counts (7 mutating, 36 read-only) are declared constants folded into the budget: **1388 → 1808 s, delay 2288 → 2708 s** | finding #3 again: the delay is derived from every declared wait |
| `probe_region_scan()` parses this driver between its `probe-region` sentinels and reports any raw `systemctl`/`systemd-run` call or unbounded-reader call not routed through the helpers | the claim had already survived two reviews; it needed to stop depending on review attention |
| `--selftest` runs that scan against the driver itself, against a fixture containing one raw call and one reader call (must report `raw=1 reader=1`) and against a file with no sentinels (must exit non-zero) | a scanner that reported 0 for everything would otherwise "prove" the fix |

Self-test: 50 → **63** checks, all passing.
`../preflight/stop-gate-8-raw-probe-call-reproduction.txt` runs the same scanner
over both revisions' probe regions, each extracted mechanically (revision 8's cut
out of git by line number, revision 9's between its sentinels, the scanner itself
`sed`-extracted from the driver): `raw=4 reader=6` against `raw=0 reader=0`.

Nothing else changed. Phases 2-9 are untouched, and
`../preflight/rev3-phase-2-9-delta.txt` was re-generated against revision 9: both
diffs reproduce byte for byte and the slice is still 389 normalized lines on each
side with the same two Reviewer-trailer lines differing.

Revision 9 said the scan "fails on" a violation. It did not - see STOP GATE 9.

**The gate is closed.** Revision 9 is a driver change, so it needs its own
exact-head recheck; no earlier lift covers it.

### STOP GATE 9 (reviewer Codex2, 2026-07-29T05:45:54Z) - revision 10

Revision 9 was never executed either. The live state is untouched and the single
finding below is the only change in revision 10.

**The finding: the scan added to stop the overclaim was itself an overclaim.**
`probe_region_scan()` printed every violation it found and then exited **0**
whenever the sentinels were present. Only a missing region produced a non-zero
code. Meanwhile the driver header (revision 9 item 5), `../README.md`, this file
and the revision-9 commit message all said the scan *fails* on any raw or
unbounded call - and the reproduction committed with them recorded the
contradiction in plain sight: `raw=4 reader=6` for revision 8's probe region,
immediately followed by `rc=0`.

Why it matters rather than being a wording slip: the whole point of revision 9's
scan was to stop the "every probe call is bounded and receipted" claim depending
on review attention. A caller written to the documented contract -
`probe_region_scan "$f" || fail` - would have accepted revision 8's ten bypasses
as clean, and the revision-9 `--selftest` would not have caught it either,
because it asserted only the printed counts. That is the same class of defect as
the discarded stderr that hid the revision-5 abort for five reviews.

| change in revision 10 | why |
| --- | --- |
| the return code is the verdict: `0` clean, `2` at least one violation (each still printed), `1` the region could not be located | the documented contract, now honoured |
| the two failure codes are deliberately distinct | "there is no probe region in this file" must never be read as "this probe region is bypassed" |
| `--selftest` asserts the **return code**, not only the counts: this driver and a routed-only fixture must exit 0; a raw call alone, an unbounded reader alone and both together must exit 2 and name the offender; a file with no sentinels must exit 1 | the revision-9 checks would all have passed against the revision-9 scanner |
| one summary line (`scan: OK` / `scan: FAIL <path> - N raw, M unbounded-reader`) | a receipt should say what it concluded, not leave the reader to count |

Detection logic is unchanged, and the fixtures make that checkable: the printed
counts are identical under both scanners.
`../preflight/stop-gate-9-scanner-exit-code-reproduction.txt` runs revision 9's
scanner and revision 10's - both `sed`-extracted, revision 9's from git - over
the same three inputs (revision 8's violating region, the current clean region, a
file with no sentinels): counts identical, `rc=0` against `rc=2` on the violating
region. `../preflight/stop-gate-8-raw-probe-call-reproduction.txt` was
regenerated for the same reason and now ends in `rc=2` where it used to end
in `rc=0`.

Self-test: 63 → **71** checks, all passing. Nothing else changed: the probe, its
receipts and budget, the clean-attempt allowlist, phases 2-9, the exit codes and
the phase ordering are untouched, and the scan still runs only under
`--selftest`, never in the live path.

**The gate is closed.** Revision 10 is a driver change, so it needs its own
exact-head recheck; no earlier lift covers it.

## Before you execute

> **Historical from here to "After a successful window".** The window has already
> been opened and the proof completed (see Status above). These two sections are
> kept because they document what the successful run was gated on, and because
> they are the procedure a *future*, separately authorised window would follow -
> not an instruction to run anything now. Both start-state preconditions below
> are deliberately unsatisfied today.

```bash
W=/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-orch-claude-deferred-approval-live-rollout-001
EV=$W/docs/evidence/runtime/ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001

# 1. Confirm the gate has been lifted for THIS exact head (revision 10), not for
#    an older one. `git rev-parse HEAD` must equal the head the coordinator
#    named (revision 10), and must equal origin/task/<TASK-ID>.
# 2. Re-run the cheap checks - none of them touch anything live.
bash -n $EV/runbook/live-boot-reconciliation-driver.sh
python3 -m py_compile $EV/runbook/assert-boot-reconciliation.py
$EV/runbook/selftest-assertion.sh                                  # 11 cases
bash $EV/runbook/live-boot-reconciliation-driver.sh --selftest      # 71 checks
cd $W && git diff --check 647970dae975f4008633a484cde1e63187035544  # exit 0

# 3. Confirm the starting state is what the driver expects.
systemctl --user show pantheon-supervisor.service -p KillMode -p ActiveState -p MainPID
ls -la /home/lupin/.config/systemd/user/pantheon-supervisor.service.d/   # expect: empty
ls /home/lupin/.config/systemd/user | grep odp-killmode-probe           # expect: nothing
# Since revision 7 the driver refuses to start on a dirty state, and since
# revision 8 the allowlist is exact, so BOTH of these must hold before launching:
#   - $EV/timeline/ holds a regular file README.md and directories named
#     attempt-* and NOTHING else - no other file, directory or symlink,
#     dotfiles included                                                (else exit 50)
#   - /tmp/odp-rollout-driver does not exist, or is a completely empty
#     directory - any entry at all, including probe-child.pid,
#     probe-commands.txt or deadman.log, is dirt                       (else exit 51)
# If either is dirty, archive the previous attempt first - the recipe is in
# $EV/timeline/README.md - rather than deleting anything.
ls -A $EV/timeline/
ls -A /tmp/odp-rollout-driver 2>/dev/null   # expect: no such directory

# 4. Resolve the CURRENT run ids. They change on every dispatch - never reuse
#    the ids from an older note. The peer is whichever unrelated worker is live.
#    There is often NO live peer: a Claude worker's process exits the moment one
#    of its own tools is deferred, and the fleet only dispatches when a task is
#    ready. With no live unrelated worker the driver aborts in preflight
#    (abort_unknown_peer 22 / abort_peer_identity 24), before phase 1 - that is
#    the intended fail-closed behaviour, not a defect to work around.
python3 - <<'PY'
import json
state = json.load(open('/home/lupin/oday-plus-supervisor-live/.orchestrator/state.json'))
for run_id, worker in state.get('workers', {}).items():
    if worker.get('status') in ('running', 'stalled', 'waiting_approval'):
        print(run_id, worker.get('agent_id'), worker.get('task_id'), worker.get('pid'), worker.get('status'))
PY
```

## Executing the window

The driver takes **only the two run ids**; it resolves pid and log path from the
live supervisor state itself, so a stale pid cannot be passed in by mistake.

```bash
systemd-run --user --unit=odp-rollout-driver --collect \
  $EV/runbook/live-boot-reconciliation-driver.sh \
  "<this task's own worker run id>" "<unrelated peer worker run id>"
```

It must run in its own transient unit, **outside** the supervisor cgroup: the
Claude CLI exits the moment a tool is deferred, so the worker that triggers the
deferral cannot observe what happens next.

Sequence once the driver is running: it opens the window and blocks waiting for
the receipt; this worker session then runs `wget --version`, the `PreToolUse`
broker hook defers it, and the session ends. Everything after that is the
driver's job.

Progress and outcome:

```bash
cat /tmp/odp-rollout-driver/verdict     # proof_complete, or abort_*
cat /tmp/odp-rollout-driver/state       # same verdict; the trap no longer clobbers it
cat /tmp/odp-rollout-driver/exit_code
tail -40 /tmp/odp-rollout-driver/driver.log
cat $EV/timeline/05-assertion-post-boot.json $EV/timeline/07-assertion-final.json
cat $EV/timeline/08-final.txt
```

`proof_complete` is only reachable when the old runner was gone, the supervisor
came back on a new non-zero MainPID, no unmanaged supervisor existed at any
point, both assertions passed, the approval id was non-empty and
supervisor-correlated, the peer survived all three checks, nothing was left
pending, and the shipped `KillMode` was restored. Any `abort_*` means the driver
restored the supervisor, the watchdog and `KillMode` and disarmed the dead-man
without completing the proof; `timeline/98-abort-reason.txt` says why.

## After a successful window

The window succeeded at 06:10:39Z. Steps 1 and 2 are **done**; 3 and 4 are what
remains.

```bash
# 1. DONE - nothing privileged pending in either queue. Both test approvals were
#    denied at 06:09:04Z; timeline/08-final.txt records 0 pending for the run and
#    0 pending in the live queue. Receipts: timeline/06-resolve-{live,control}.txt.
cd /home/lupin/oday-plus-supervisor-live && python3 .orchestrator/approval_queue.py list
cd /home/lupin/oday-plus && python3 .orchestrator/approval_queue.py list

# 2. DONE - sections 6 and 7 of ../README.md are written up from timeline/, and
#    timeline/README.md indexes every receipt. Two things the driver itself could
#    not commit were closed out afterwards under the reviewer's tenth STOP GATE:
#    the EXIT trap's 99-restore.txt (written after the driver's own final commit)
#    and the /tmp signal files, now timeline/09-*.txt.
# 3. Open the PR to dev and hand off to reviewer Codex2:
cd $W && /usr/bin/gh pr create --base dev --head task/ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001 ...
AI_NAME=Claude3 python3 scripts/ai_status.py handoff ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-LIVE-ROLLOUT-001 Codex2 "<summary>"
# 4. Notify/unblock ODP-DEPLOY-WORKER-JOB-EXECUTION-001 (acceptance criterion) -
#    only after Codex2's review passes, so the unblock does not rest on an
#    unreviewed proof.
```

## If the driver is killed

The EXIT trap restores the supervisor, the watchdog timer and the shipped
`KillMode`, and disarms the dead-man. If the driver is SIGKILLed the trap does
not run, and the `odp-rollout-deadman.timer` transient unit performs the same
restore once the computed delay (2708 s ≈ 45 min from the moment it was armed,
i.e. 900 s after the driver's longest legal run) elapses. Check:

```bash
systemctl --user status pantheon-supervisor.service --no-pager | head -5
systemctl --user status pantheon-supervisor-watchdog.timer --no-pager | head -5
systemctl --user show pantheon-supervisor.service -p KillMode --value   # expect control-group
ls /home/lupin/.config/systemd/user/pantheon-supervisor.service.d/      # expect empty
cat /tmp/odp-rollout-driver/deadman.log 2>/dev/null
```

If the drop-in is on disk with no driver running, remove it and
`systemctl --user daemon-reload`.

## Rollback of the supervisor deployment

```bash
cp /tmp/odp-rollout-backup/live-supervisor.py.bak    /home/lupin/oday-plus-supervisor-live/.orchestrator/supervisor.py
cp /tmp/odp-rollout-backup/control-supervisor.py.bak /home/lupin/oday-plus/.orchestrator/supervisor.py
systemctl --user restart pantheon-supervisor.service
```

Note that with the drop-in removed (the normal resting state) a restart uses
`KillMode=control-group` and **will** kill every live worker. Reinstall the
drop-in first if the fleet is busy.
