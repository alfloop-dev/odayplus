# Review Packet: ODP-ORCH-AGENT-LOAD-BALANCE-001

## Packet Identity

- Sidecar task: `ODP-ORCH-AGENT-LOAD-BALANCE-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-AGENT-LOAD-BALANCE-001`
- Helper kind: `review_packet`
- Sidecar owner / current reviewer: `Codex2` / `Codex`
- Parent owner / reviewer: `Claude` / `Antigravity2`
- Parent PR: `#710`
- Parent branch: `origin/task/ODP-ORCH-AGENT-LOAD-BALANCE-001`
- Exact reviewed parent HEAD: `7d786d752c4fa1b9e2c1232d1457aca8e52161e8`
- Evidence captured: `2026-08-08T13:22:50Z`
- CI repair evidence refreshed: `2026-08-10`
- Scope: support artifact and reviewer handoff only; no canonical truth, supervisor implementation, registry, config, or governance file is changed by this sidecar.

## Executive Disposition

**Sidecar packet: ready for review. Parent change: owner-balancing path is supported by the code and tests, with one role-semantics decision required before approval.**

The parent change replaces fixed first-viable selection with least-open-task selection after all existing viability filters have run. The implementation is narrow: two files, 134 insertions and 2 deletions. The five new focused tests and one pre-existing viability-matrix test pass independently, lint passes, and all four GitHub CI jobs are green.

The main reviewer decision is that the shared selector is also used to reassign reviewers, while the new counter measures only the `owner` field. A review task is dispatched to its `reviewer`, not its `owner`; consequently the reviewer path does not currently select the candidate with the smallest review workload. This does not invalidate the owner-redistribution fix, but it should be explicitly accepted as a coarse global proxy, restricted to owner selection, or made role-aware before the parent task is approved.

## Parent Change Surface

Compared with `origin/dev`, exact parent HEAD `7d786d75` modifies only:

| File | Diff | Purpose |
| --- | ---: | --- |
| `.orchestrator/supervisor.py` | +48 / -2 | Add open-task counts, collect all viable candidates, and choose the least loaded with preference order as the tie-break. |
| `.orchestrator/test_supervisor.py` | +86 | Add five load-balancing regression tests. |

No L1 canonical document or contract is part of the parent diff.

## Evidence Summary

### Root cause and operational evidence

- On `origin/dev`, `first_viable_agent()` returns immediately when the first candidate passes its existing filters.
- The default fallback pool starts with `Antigravity`, then `Antigravity2` through `Antigravity7`, so a viable first entry wins repeatedly.
- The parent commit records the 2026-08-08 historical snapshot as `Antigravity=23` open owned tasks versus `3, 6, 3, 4, 3, 4` for `Antigravity2..7`.
- An independent live-board snapshot at packet time still shows skew: `Antigravity=21`, `Antigravity2=9`, `Antigravity3=3`, `Antigravity4=2`, `Antigravity5=1`, `Antigravity6=3`, `Antigravity7=3`.
- Live config has `max_concurrent_workers=24` and `max_active_workers_per_task=1`. Per-agent serialization is more directly enforced by the worker duplicate/slot guard, which refuses a second live worker in an occupied logical agent slot. The balancing motivation is therefore sound, although `max_active_workers_per_task` alone is not the per-agent serialization mechanism.

### Behavior preserved by inspection

At parent HEAD, candidate processing still applies these checks before a name is appended to the viable list:

1. blank, duplicate, and excluded candidates;
2. human-gate exclusion;
3. disabled agent/provider configuration;
4. provider runtime configuration block;
5. dispatch pause and auto-dispatch block when runtime state is supplied;
6. sidecar-only lane/task compatibility when task context is supplied.

Balancing runs only after that filtering. When zero candidates remain it returns `None`; when exactly one remains it returns that candidate without reading the board. Equal counts retain caller preference order. The two single-candidate diagnostic call sites therefore keep their previous behavior and cost.

### Independent verification

The parent tree was materialized from exact HEAD with `git archive`, without checking parent implementation into this sidecar branch. The following focused verification passed:

```text
PYTHONPATH=.orchestrator python3 -m unittest -v \
  test_supervisor.SupervisorFailureLoopCoverageTests.test_full_agent_matrix_and_negative_viability_coverage \
  test_supervisor.AgentLoadBalancingTests

Ran 6 tests in 0.034s
OK

uv run ruff check .orchestrator/supervisor.py .orchestrator/test_supervisor.py
All checks passed!
```

At packet time, PR `#710` reports:

| Check | Result |
| --- | --- |
| `orchestrator` | PASS |
| `performance-gate` | PASS |
| `product` | PASS |
| `product-e2e-gate` | PASS |
| `task-review-gate` | PENDING — review by `Antigravity2` |

## Reviewer Decision Point: Owner Load vs Reviewer Load

`agent_open_task_counts()` groups every open task by normalized `task.owner`, including tasks in `review`. The same `first_viable_agent()` is called from both owner- and reviewer-reassignment paths.

That produces this reproducible result at the reviewed HEAD:

```text
Input: two review tasks with owner=Claude and reviewer=Antigravity2
Reviewer candidates: [Antigravity2, Antigravity3]
Computed owner counts: {'claude': 2}
Selected reviewer: Antigravity2
```

The dispatcher routes `review` work to `task.reviewer`, so `Antigravity2` has two relevant review assignments and `Antigravity3` has none in this example. The selector nevertheless sees both candidates at owner-count zero and selects `Antigravity2` by preference order.

Reviewer options:

1. Accept owner backlog as the intentionally coarse load proxy for both roles and document that definition.
2. Keep balancing only for owner reassignment (`balance_load=False` for reviewer calls), preserving prior reviewer preference behavior.
3. Make counting role/status-aware so owner reassignment considers owner-routed states and reviewer reassignment considers reviewer-routed review states.

Option 3 most closely matches the phrase “least loaded agent,” but it broadens the parent implementation and needs focused tests. The sidecar makes no canonical choice.

## Secondary Review Notes

- The load set includes `todo`, `in_progress`, `review`, `review_approved`, and `blocked`. This is an open-assignment/backlog metric, not a count of immediately dispatchable work. Dependency-blocked `todo` and explicitly `blocked` tasks can affect the choice.
- The new tests directly cover least-loaded selection, tie-breaking, exclusion, the single-candidate no-board-read path, and omission of finished work.
- Existing tests cover disabled, paused, and human candidates in single-candidate mode. The code structure preserves those filters for multi-candidate mode, but there is no new multi-candidate regression test for pause, provider block, or sidecar-only filtering.
- The new test class is below the file's `if __name__ == "__main__": unittest.main()` block. Pytest and import-based unittest discovery collect it (as independently verified); direct execution of the file will not. This is test-layout hygiene, not a runtime defect.

## Handoff

- Sidecar reviewer: `Codex`
- Parent reviewer: `Antigravity2`
- Requested action: re-verify that this remains a support-only packet after the
  CI repair refresh, while retaining the historical reviewer disposition below.
- Parent owner `Claude` decides whether and how to absorb this support finding into the mainline task.

## CI Repair and Re-review Evidence

The orchestrator cleared the earlier approval and requeued this sidecar on
2026-08-10 because merged PR `#726` had a failed `performance-gate` at approved
head `f97083d7`. That run does not show a sidecar-content failure:

- `orchestrator`, `product`, and `product-e2e-gate` passed.
- The performance artifact contains successful attempt 1 and attempt 2 reports:
  P95 `0.661s` and `0.481s`, respectively, against the unchanged `3.0s` budget,
  with 150/150 successful requests and zero failures in each attempt.
- No attempt 3 report was produced before the job exited. The retained GitHub
  evidence exposes only exit code 1, so this packet does not assert an unproven
  root cause or relabel the incomplete run as a product regression.

The refreshed branch starts at current `origin/dev` head `273a7705`. Both CI
runs attached to that exact commit (`31385506416` and `31386770455`) report a
successful `performance-gate`. Independent local execution of the same marked
test in three separate processes also passed three times, with P95 values
`1.405s`, `1.343s`, and `1.430s`; every run completed 150/150 requests with
zero failures under the `3.0s` budget:

```text
uv run pytest -q -m performance tests/performance  # run three times
3 passed each run; performance report passed=true each run
```

This repair changes only the support packet and reviewer handoff metadata. It
does not modify the performance workflow, load-balancing implementation, tests,
registry, status truth, or any canonical contract.

### Current re-review refresh

Before changing or handing off this packet again, the owner fetched the remote
refs and composed current `origin/dev` head `f9da2955` into the unchanged remote
task head `c47f7550`. Merge commit `c52df597` preserves both histories and leaves
the task diff against `origin/dev` confined to this support packet. No reset,
history rewrite, force-push, or parent implementation change was used.

For PR `#781` at pre-compose head `c47f7550`, GitHub reports successful
`orchestrator`, `performance-gate`, and `product-e2e-gate` jobs. The `product`
job was still running when this evidence was captured. The failed
`task-review-gate` reflects the orchestrator moving the task back to
`in_progress` for CI repair; it is a review-state gate, not evidence of a
sidecar-content, parent-runtime, or performance regression. This refreshed
support-only head therefore requires a new `Codex` review stamp after normal
push.

## Reviewer Disposition and Closeout Record

The previous sidecar reviewer, `Claude`, approved the packet at sidecar HEAD `f74a97ac` after independently re-verifying every packet claim against unchanged parent HEAD `7d786d75`. The reviewer selected Option 1 for the parent decision point: accept owner backlog as a deliberately coarse proxy for PR `#710`, because the reviewer path is not regressed from `origin/dev`, and track role-aware counting (Option 3) as a follow-up. The parent reviewer should require the selector docstring to state explicitly that the metric counts owner-routed tasks only. The detailed decision is recorded in merged sidecar PR `#722`, whose reviewed head is `c33fd2b4` and merge commit is `07167d47`.

For this finalize dispatch, the task owner fetched and composed current base `origin/dev` at `07167d47` through merge commit `dd9a145e`, then pushed normally. This preserves both the approved task history and the newer local task merge instead of resetting, discarding, or force-updating either lineage. Because that required compose advanced the branch beyond the previously approved head, the then-assigned reviewer `Claude2` was required to stamp the new support-only head before owner finalization. PR `#726` subsequently merged at approved head `f97083d7`; the 2026-08-10 CI repair requeue cleared that approval and assigned the refreshed packet to `Codex` for a new review stamp.

At finalization time, GitHub reports parent PR `#710` as closed without merge. This packet remains historical review evidence and does not claim that the parent runtime change entered `dev`.

## Scope Conformance

This sidecar adds only:

`support/sidecars/ODP-ORCH-AGENT-LOAD-BALANCE-001/ODP-ORCH-AGENT-LOAD-BALANCE-001-SIDECAR-REVIEW.md`

It intentionally does not modify `.orchestrator/supervisor.py`, `.orchestrator/test_supervisor.py`, status truth, L1 canonical documents, runtime contracts, registry, config, or governance implementation.
