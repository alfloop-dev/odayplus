# timeline/

Receipts written by `runbook/live-boot-reconciliation-driver.sh`.

What is here now is from the **aborted** revision-5 run of 2026-07-29T04:31Z
(`abort_killmode_probe`, exit 26), which stopped at phase 1 - before the drop-in,
the restart, the deferral and any approval. It is not a proof run.

The driver's phase-0 snapshots of the live supervisor state and of both approval
queues were removed from this directory by hand: they are bulk copies of other
lanes' runtime state (~780 KB, including 124 pending approvals belonging to
unrelated tasks), they prove nothing about an abort that happened before the
window opened, and the real run regenerates them.

One hand edit is recorded here for honesty: `99-restore.txt` line 11 was written
by the pre-revision-6 generator as `unmanaged_supervisors: ` with a trailing
space (an empty list). It now reads `<none>`, which is what the fixed generator
emits, so the committed receipts stay free of the trailing-whitespace defect
STOP GATE 3 caught. The value itself is unchanged: no unmanaged supervisor was
found.
