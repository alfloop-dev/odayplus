# Independent Review Round 2 — ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001

- Reviewer: `Codex6`
- Reviewed owner head: `ab842fb590f1a981b1cc3d020117e3676fb92c8c`
- Review time: `2026-07-31T08:59:50Z`
- Disposition: **CHANGES_REQUESTED**
- Runtime rollout: **not performed**

The reviewed head is pushed and exactly matches
`origin/task/ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001`. The no-restart boundary
remains in force.

## Verification result

The task brief's full unit-test command does not pass:

```text
PYTHONPATH=.orchestrator python3 -m unittest test_supervisor test_model_rotation

Ran 265 tests in 1.784s
FAILED (failures=1)

FAIL:
test_dispatcher_helper_claim_uses_persisted_reassignment_timestamp_for_event_key

AssertionError: '"last_update": "2026-05-09T10:00:00Z"' not found in
'dispatcher:Claude:WB-006:owned_in_progress_dispatch:...'
```

The command stopped at this failure, so the chained `scripts.test_ai_status`,
doctor, Ruff, and diff checks were not represented as a passing full batch.

## Blocking findings

### R9 — P0: runtime-state merge resurrects terminal workers

`merge_runtime_states()` treats the disk copy of an active worker as
authoritative over a terminal transition made by the current Supervisor loop.
When the disk snapshot says `running` and the in-memory worker says
`completed` or `failed`, the merge changes the in-memory status back to
`running`.

Minimal exact-head reproduction:

```text
disk_status= running
requested_status= completed
persisted_status= running
```

This happens on the ordinary single-Supervisor path: the loop loads a running
worker, detects its terminal result, then `save_runtime_state()` rereads the
older running snapshot and revives it. The result violates the no-infinite-loop
and terminal-failure reassignment requirements.

The merge must preserve a newer terminal transition. Add exact-head tests for
`running -> completed`, `running -> failed`, and reassignment/finalized queue
state through the real save boundary.

### R10 — P0: read-merge-replace still loses truly concurrent writers

`save_runtime_state()` has no state-file lock, generation check, or
compare-and-swap boundary. Its `load_json -> merge -> write_json` sequence is
atomic only at the final replace, not across the transaction.

A deterministic two-process reproduction placed a barrier after both writers
read the same snapshot. Writer 1 added `run-1`; writer 2 added `run-2`. Both
completed successfully:

```text
child_exitcodes= [0, 0]
expected_workers= ['run-1', 'run-2']
actual_workers= ['run-2']
```

The added `test_concurrent_claim_main_loop_state_preservation` performs a
sequential interleaving and therefore cannot detect this race. R1 remains open
until the state read/merge/write operation is serialized or guarded by a
generation/CAS retry, with a genuine two-writer regression test.

### R11 — P0: the R7 test-root isolation claim is false

Running the full suite on the exact head caused
`test_concurrent_claim_main_loop_state_preservation` to use live repository
paths and real dispatch:

- appended four `ODP-CONC-001` events to tracked `ai-activity-log.jsonl`;
- reused/created worktree
  `/tmp/pantheon-worker-worktrees/odp-plan-supervisor-failure-loop-001/odp-conc-001`;
- launched worker runner PID `4083365` and `agy` child PID `4083366`;
- emitted a `worker_started` record for
  `antigravity-20260731T085742Z-27f3a4f3`.

The reviewer terminated only those two exact test-created PIDs. The four
test-created activity rows were removed from the review worktree; no live
Supervisor or legitimate worker was restarted or altered.

The test patches `state_file` and `event_queue`, but leaves activity,
worktree, adapter, and runtime paths from the live/example config and does not
mock the dispatch start boundary. Replace the partial path override with a
complete isolated config and stub the process-launch boundary. The full suite
must prove no process, worktree, branch, activity row, GitHub status, or live
runtime file is created.

### R12 — P1: R8 breaks the existing full-suite event-key contract

Removing `last_update` from `ready_dispatch_signature()` is not accompanied by
an update to the pre-existing helper-claim regression test. Whether the desired
contract is a stable authority-only key or a persisted-reassignment timestamp,
the suite and implementation must agree on one reviewed rule. The current head
cannot pass the task's declared verification command.

Retain the intended authority fields and update/add negative coverage for
owner, reviewer, status, dependency list, and dependency-state changes, then
run the full suite rather than only the 19 focused tests.

### R13 — P0: the R5 outbox has no retry/reconciliation path and its drill is vacuous

`status_check_outbox` is only appended in
`emit_task_review_status_check()`. No code reads, retries, reconciles, or removes
an outbox item after the commit becomes remotely visible.

The test named
`test_status_check_http_422_failure_injection_outbox_transactional` does not
assert task status, outbox contents, worker representation, duplicate
redispatch prevention, or eventual reconciliation. It calls
`sync_dispatched_task_status()` with a mocked `subprocess.run` and asserts only
that an unrelated temporary backup file still exists.

R5 therefore does not satisfy the prior review requirement or the evidence
claims in the primary task document. Add a real isolated `ai_status.main`
failure-injection drill that proves:

1. the authoritative task transition remains committed after HTTP 422;
2. the exact failed status payload is durable;
3. a later reconciliation retries it after remote visibility and removes or
   marks it delivered;
4. worker/event state remains represented and cannot duplicate-dispatch;
5. the named dirty-worktree backup remains unchanged.

## Required next handoff

Return one new pushed head that closes R9–R13 as a batch. Run the full declared
Supervisor/model-rotation suite plus `scripts.test_ai_status`, doctor, Ruff, and
`git diff --check` from a completely isolated test root. Do not restart the live
Supervisor until all live workers drain and the reviewed PR has merged.
