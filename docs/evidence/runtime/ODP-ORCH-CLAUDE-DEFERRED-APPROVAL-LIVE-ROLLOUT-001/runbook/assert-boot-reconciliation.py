#!/usr/bin/env python3
"""Fail-closed acceptance assertion for the PR #492 boot-reconciliation rollout.

Usage:
    assert-boot-reconciliation.py <activity-slice.jsonl> <worker-run-id> <mode>

`mode` is `post_boot` (immediately after the restart, before the test approval is
resolved) or `final` (after the approval has been denied and the supervisor has
acted on it).

The assertion is deliberately *positive*: an empty or unreadable slice, a missing
correlation event, or a missing approval id all fail. Nothing here can pass by
default. Exit code 0 means the proof holds; anything else means it does not, and
the driver must abort without signalling `proof_complete`.

What is being proven
--------------------
`reconcile_runtime_on_boot()` reads the flushed Claude result and calls
`correlate_deferred_tool_approval()` *before* the missing-PID branch. So for the
test run the activity log must show, in this order:

  1. `worker_deferred_approval_recorded` (or `_correlated`) carrying an
     `approval_id`;
  2. only then, if at all, any generic `worker_failed`.

The pre-fix behaviour on this same host - recorded in
docs/evidence/runtime/ODP-ORCH-CLAUDE-DEFERRED-APPROVAL-RACE-001/README.md - was
the opposite: boot reconciliation reported `missing_process_workers_failed=1` and
finalized the run with the generic worker-exit reason, and no approval survived.
Any recurrence of that shape is a hard failure here.
"""

from __future__ import annotations

import json
import re
import sys

CORRELATION_TYPES = (
    "worker_deferred_approval_recorded",
    "worker_deferred_approval_correlated",
)
# The generic boot finalization reason emitted by reconcile_runtime_on_boot()
# when it treats a missing PID as an ordinary worker exit. This is the exact
# branch PR #492 must no longer reach for a deferred run.
MISSING_PROCESS_REASON = re.compile(
    r"process missing during supervisor boot reconciliation", re.IGNORECASE
)


def load_events(path: str) -> list[dict]:
    events: list[dict] = []
    with open(path, encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def main() -> int:
    if len(sys.argv) != 4:
        print(json.dumps({"verdict": "FAIL", "reason": "usage"}, indent=2))
        return 2
    slice_path, run_id, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    if mode not in {"post_boot", "final"}:
        print(json.dumps({"verdict": "FAIL", "reason": "bad_mode"}, indent=2))
        return 2

    try:
        events = load_events(slice_path)
    except OSError as error:
        print(json.dumps({"verdict": "FAIL", "reason": f"unreadable_slice: {error}"}, indent=2))
        return 2

    failures: list[str] = []

    correlation_idx = None
    correlation_event = None
    approval_id = ""
    deferred_failed_idx = None
    generic_failed_idx = None
    missing_process_finalizations: list[dict] = []
    boot_metric_failures: list[dict] = []

    for index, event in enumerate(events):
        etype = str(event.get("type") or "")
        for_run = event.get("worker_run_id") == run_id

        if for_run and etype in CORRELATION_TYPES and correlation_idx is None:
            correlation_idx = index
            correlation_event = event
            approval_id = str(event.get("approval_id") or "").strip()

        if for_run and etype == "worker_deferred_approval_failed" and deferred_failed_idx is None:
            deferred_failed_idx = index

        if for_run and etype == "worker_failed":
            if generic_failed_idx is None:
                generic_failed_idx = index
            if MISSING_PROCESS_REASON.search(str(event.get("message") or "")):
                missing_process_finalizations.append(event)

        # `boot_reconciliation` surfaces as a worker_runtime_metrics activity
        # event, and only when a corrective count is non-zero. Its presence with
        # a missing-process failure inside this window is the pre-fix signature.
        if etype == "worker_runtime_metrics" and event.get("measurement") == "boot_reconciliation":
            counts = event.get("counts") or {}
            if int(counts.get("missing_process_workers_failed") or 0) > 0:
                boot_metric_failures.append(event)

    # --- the proof itself ---------------------------------------------------
    if correlation_idx is None:
        failures.append(
            "no worker_deferred_approval_recorded/_correlated event for "
            f"{run_id}: boot reconciliation did not adopt the tool_deferred receipt"
        )
    if not approval_id:
        failures.append("the correlation event carries no approval_id")
    if deferred_failed_idx is not None:
        failures.append(
            f"worker_deferred_approval_failed was emitted for {run_id}: the queue write failed closed"
        )
    if missing_process_finalizations:
        failures.append(
            f"{len(missing_process_finalizations)} generic missing-process boot finalization(s) "
            f"for {run_id}: this is exactly the pre-fix behaviour PR #492 must eliminate"
        )
    if boot_metric_failures:
        failures.append(
            "boot_reconciliation reported missing_process_workers_failed > 0 during the window"
        )
    if generic_failed_idx is not None:
        if correlation_idx is None or generic_failed_idx < correlation_idx:
            failures.append(
                f"a generic worker_failed for {run_id} precedes the correlation event "
                f"(failed@{generic_failed_idx} correlation@{correlation_idx})"
            )
        elif mode == "post_boot":
            failures.append(
                f"a generic worker_failed for {run_id} appeared before the test approval "
                "was resolved; the run should be parked in waiting_approval"
            )

    verdict = "PASS" if not failures else "FAIL"
    print(
        json.dumps(
            {
                "verdict": verdict,
                "mode": mode,
                "worker_run_id": run_id,
                "events_in_slice": len(events),
                "approval_id": approval_id,
                "correlation_event_type": (correlation_event or {}).get("type"),
                "correlation_index": correlation_idx,
                "first_generic_worker_failed_index": generic_failed_idx,
                "missing_process_finalizations": len(missing_process_finalizations),
                "boot_metric_missing_process_failures": len(boot_metric_failures),
                "failures": failures,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
