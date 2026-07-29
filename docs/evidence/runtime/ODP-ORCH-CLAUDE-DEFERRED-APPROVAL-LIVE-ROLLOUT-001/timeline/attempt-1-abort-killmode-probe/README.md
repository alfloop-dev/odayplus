# attempt 1 - 2026-07-29T04:31:10Z - `abort_killmode_probe` (exit 26)

Everything in this directory belongs to **one** attempt, which failed closed at
phase 1. It is archived here, out of `timeline/`, so that no later attempt's
receipts can be read together with it (coordinator STOP GATE 6). Since revision
7 the driver enforces that separation itself: it refuses to start while
`timeline/` or `/tmp/odp-rollout-driver` still holds a previous attempt's
artefacts (exit 50 / 51).

**This is not a proof run.** It stopped before the drop-in was installed, so
there was no restart, no deferral and no approval:

| file | what it shows |
| --- | --- |
| `00-before.txt` | phase 0 snapshot: `MainPID=1197865`, `KillMode=control-group`, no drop-in |
| `00-cgroup-before.txt` | the supervisor cgroup as it was at 04:31Z |
| `00-killmode-probe.txt` | the phase-1 receipt: `probe_child_survived_stop: yes`, `probe_unit_restarted_with_leftover: no`, `probe_verdict: fail` |
| `98-abort-reason.txt` | the abort message |
| `99-restore.txt` | the EXIT trap's restore: supervisor active on the shipped `KillMode`, no drop-in, dead-man inactive |
| `tmp-verdict.txt`, `tmp-state.txt`, `tmp-exit_code.txt`, `tmp-driver.log.txt` | the run's `/tmp/odp-rollout-driver` state, archived verbatim (trailing whitespace stripped) before that directory was cleared |

The `/tmp` original was moved to
`/tmp/odp-rollout-driver-attempt-1-archived-20260729`, not deleted.

## Why it aborted, and what that does and does not prove

The probe was trying to re-create its transient unit with a **second
`systemd-run` under the same name**. A `KillMode=process` stop leaves the child
alive, the surviving child keeps the unit `loaded`, and `systemd-run` refuses a
loaded name (`Unit ... was already loaded or has a fragment file`);
`reset-failed` clears failed state but does not unload an inactive unit. Both
halves of the probe discarded stderr, which is why five review passes never saw
it.

So `probe_unit_restarted_with_leftover: no` in `00-killmode-probe.txt` records a
defect **in the probe**, not a property of the host. Only *recreation* fails.
`systemctl --user start` on the existing, already-loaded transient unit works:
independently proven at 04:39:19Z and re-proven by the revision-7 driver's own
`--selftest`, which runs the real phase-1 gate. See
`../../preflight/killmode-probe-diagnosis.txt`.

`probe_child_survived_stop: yes` in this receipt is real, and is the first half
of the property the live window depends on.

## Two hand edits, recorded for honesty

1. The driver's phase-0 bulk snapshots (`supervisor-state-before.json` and both
   approval-queue copies) were removed by hand rather than committed: ~780 KB of
   other lanes' live runtime state, including 124 pending approvals belonging to
   unrelated tasks, proving nothing about an abort that happened before the
   window opened. The real run regenerates them.
2. `99-restore.txt` line 11 was written by the pre-revision-6 generator as
   `unmanaged_supervisors: ` with a trailing space (an empty list). It now reads
   `<none>`, which is what the fixed generator emits. The value is unchanged: no
   unmanaged supervisor was found.
