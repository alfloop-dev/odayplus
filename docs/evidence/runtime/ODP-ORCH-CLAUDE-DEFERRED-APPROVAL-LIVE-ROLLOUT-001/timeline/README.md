# timeline/

Receipts written by `runbook/live-boot-reconciliation-driver.sh`.

**This directory holds no receipts, and that is the correct resting state: the
rollout window has never been opened.** The one attempt so far - the revision-5
run of 2026-07-29T04:31Z, which aborted at phase 1 with `abort_killmode_probe`
(exit 26), before the drop-in, the restart, the deferral and any approval - is
archived whole in `attempt-1-abort-killmode-probe/`, together with its
`/tmp/odp-rollout-driver` state and an account of the two hand edits made to it.

Since revision 7 the separation is enforced rather than trusted (coordinator
STOP GATE 6): the driver refuses to start (exit 50 / 51) while this directory
holds anything but `README.md` and `attempt-*/` archives, or while
`/tmp/odp-rollout-driver` still holds a previous run's `verdict`, `state`,
`exit_code` or `driver.log`. The check runs before the log redirect and before
the EXIT trap is installed, so a refusal writes nothing and leaves the older
attempt exactly as it was.

To archive an attempt and free the directory for the next one:

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

A file in here is never by itself evidence that a window ran. Read the attempt's
own README for what its receipts do and do not prove.
