# Independent Review — ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001

- Reviewer: `Codex6`
- Reviewed owner head: `3584edf0c8dd4b4742f0fd59681680f83422df1e`
- Disposition: **CHANGES_REQUESTED**
- Runtime rollout: **not performed**; the live Supervisor must not be restarted while workers are active.

## Verification

The submitted checks pass, but do not cover the control failures below:

```text
PYTHONPATH=.orchestrator python3 -m unittest test_supervisor test_model_rotation
Ran 259 tests in 8.560s
OK

python3 .orchestrator/doctor.py
exit 0

git diff --check
exit 0
```

The configured live agent matrix was also inspected against
`/home/lupin/oday-plus-supervisor-live/.orchestrator/config.json`. Every current
enabled lane can derive at least one configured alias when all lanes are assumed
healthy. That does not establish viable fallback selection under disabled or
unavailable lanes.

## Blocking findings

### R1 — P0: concurrent claim/main-loop state loss is still reproducible

The owner patch changes fallback selection, tests, example config, and empty
config loading. It does not change `run_once`, `process_queue`,
`current_dispatch_event_key`, or `runtime_state.save_runtime_state`, and adds no
claim/main-loop reconciliation test.

The live receipt named by the task brief is confirmed:

- queue event: `evt-20260731T074052Z-a35fca24`
- worker run: `antigravity7-20260731T074053Z-07fc2717`
- worker start: `2026-07-31T07:40:53Z`
- coordinator observation: runner PID `3951191` and child `3951193` remained
  alive with a fresh heartbeat after the worker record was removed.

A deterministic isolated reproduction on the reviewed head simulated a
concurrent self-claim after the main loop loaded runtime state. The claim wrote
a running worker and changed the task from `todo` to `in_progress`; the stale
main-loop copy then classified the original `owned_ready_dispatch` event as
stale and overwrote the newer shared state at final save:

```json
{"pid_alive": true, "queue_event_present": false, "wake_skipped": 1, "worker_preserved": false}
```

This leaves a live process unmonitored and permits a later duplicate dispatch.
The task cannot be approved until one exact-head test proves all of the
following together:

1. the matching `owned_ready_dispatch` run/event survives the expected
   `todo -> in_progress` status sync;
2. a main-loop save cannot overwrite a worker added by a concurrent claim;
3. a live PID with a fresh heartbeat remains represented in shared state;
4. the queue event is reconciled to the running worker, not `wake_skipped` or
   pruned;
5. a subsequent dispatch pass does not start a second worker for the task.

The fix must use an explicit serialization, compare-and-swap, or safe
reload/merge boundary around shared runtime-state mutation. Merely accepting
`in_progress` as equivalent for stale-key comparison is insufficient because
the final stale-state write still loses the worker.

### R2 — P0: Human/Ops and non-dispatchable task gates are not fail-closed

`maybe_reassign_task_after_worker_failure` checks whether the failing agent name
looks like `Human/Ops`, but never checks `task_is_human_gate(task)` or
`task["non_dispatchable"]`.

Independent negative probes on the reviewed head both performed a persisted
owner reassignment:

```text
{"marker": {"task_class": "human_gate"}, "reassigned_to": "Antigravity", "persist_called": true}
{"marker": {"non_dispatchable": true}, "reassigned_to": "Antigravity", "persist_called": true}
```

Add owner and reviewer negative tests for human-gate metadata,
`human_required_roles`, pending-human gate states, and `non_dispatchable=true`.
Every case must return without calling `persist_task_reassignment`.

### R3 — P1: matrix tests do not prove enabled and available fallback viability

`test_full_agent_matrix_coverage_all_configured_agents` uses a hard-coded list
and only asserts that returned candidate strings are non-empty. It neither
derives the matrix from configured dispatch lanes nor passes candidates through
the same viability boundary used by reassignment.

`first_viable_agent` treats membership in `known_agent_display_names` as
sufficient and does not reject an agent whose config contains
`"enabled": false`. A direct probe with candidates
`["Disabled", "Unavailable", "Healthy"]` returned `Disabled`. Provider
capability/unavailability is likewise not evaluated by failure reassignment.

Replace the hard-coded/non-empty assertion with a configured-lane matrix that
proves at least one viable owner and reviewer fallback per enabled
auto-dispatch lane and negative coverage for:

- same agent and current owner/reviewer;
- disabled agent/provider;
- dispatch pause and quota pause/group;
- unsupported, unauthenticated, or otherwise unavailable provider capability;
- Human/Ops and sidecar-only/mainline incompatibility.

The chosen replacement, not merely the raw candidate list, must satisfy these
conditions.

### R4 — P1: rollout evidence is a future procedure, not a safe-rollout record

The submitted evidence says to copy config and restart later. It contains no
worker-drain snapshot, deployed commit/config digest, restart receipt, recovered
worker/queue reconciliation result, or post-restart no-double-dispatch drill.
Keep the no-restart boundary in force. After R1–R3 pass on one pushed head,
record the drain decision and perform rollout verification only at the task's
safe post-drain gate.

### R5 — P0: status-check failure is not transactional with worker dispatch

A second live receipt arrived during this review:

- task: `ODP-PLAN-ACCEPTANCE-REAL-EXEC-001`
- event: `evt-20260731T080347Z-fda15eea`
- worker run: `codex-20260731T080348Z-61f2c0b5`
- local task head: `f1078e8b3b62bd5690a55e731ba3b4fdb301f632`
- remote task head at review time:
  `6a003b4a2e650307265c7e81688c2f80ef041041`

The Supervisor reset the task worktree after backing up its dirt to
`.orchestrator/worktree-dirt-backups/odp-plan-acceptance-real-exec-001-lease_blocked.patch`,
started the worker, and then attempted `ai-status start`. GitHub rejected the
status check with HTTP 422 because the local head did not exist remotely.
`ai_status.py` rolled authoritative task state back to `todo`, while activity
and runner state already said the worker had started.

The worker later called progress and moved the task to `in_progress`, but this
does not close the control failure. At the targeted review snapshot the runner
PID `3988419` was alive with a fresh heartbeat and `PPID=1`, while
`state.json` contained no matching worker record. The backup patch existed and
was `4,825,511` bytes; it must not be deleted.

Add failure injection for status-check HTTP 422 and prove one transactional
outcome:

1. resolve and push a remote-verifiable task head before worker launch/status
   emission; or
2. durably commit task mutation, runner/activity state, and a retryable status
   check outbox without rolling task truth behind a live run.

The exact-head tests must show no `todo + live runner` split, no orphan,
no duplicate redispatch, no loss of the dirty-worktree backup, and eventual
reconciliation after the remote commit becomes visible.

## Required next handoff

Return one exact pushed head that closes R1–R5 as a batch. Include the full
configured owner/reviewer and negative matrices, the deterministic concurrent
claim/main-loop and HTTP-422 failure-injection tests, before/after worker and
task state, and the safe post-drain rollout record. Do not restart the live
Supervisor for a partial fix.
