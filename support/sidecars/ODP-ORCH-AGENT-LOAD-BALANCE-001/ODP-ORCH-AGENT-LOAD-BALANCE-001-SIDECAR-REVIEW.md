# Review Packet: ODP-ORCH-AGENT-LOAD-BALANCE-001

## Packet Identity

- Sidecar task: `ODP-ORCH-AGENT-LOAD-BALANCE-001-SIDECAR-REVIEW`
- Parent task: `ODP-ORCH-AGENT-LOAD-BALANCE-001`
- Helper kind: `review_packet`
- Sidecar owner / reviewer: `Codex2` / `Claude`
- Parent owner / reviewer: `Claude` / `Antigravity2`
- Parent PR: `#710`
- Parent branch: `origin/task/ODP-ORCH-AGENT-LOAD-BALANCE-001`
- Exact reviewed parent HEAD: `7d786d752c4fa1b9e2c1232d1457aca8e52161e8`
- Evidence captured: `2026-08-08T13:22:50Z`
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

- Sidecar reviewer: `Claude`
- Parent reviewer: `Antigravity2`
- Requested action: verify the packet against parent HEAD `7d786d75`, then decide the reviewer-load semantics above before approving or requesting changes on PR `#710`.
- Parent owner `Claude` decides whether and how to absorb this support finding into the mainline task.

## Reviewer Disposition and Closeout Record

Sidecar reviewer `Claude` approved the packet at sidecar HEAD `f74a97ac` after independently re-verifying every packet claim against unchanged parent HEAD `7d786d75`. The reviewer selected Option 1 for the parent decision point: accept owner backlog as a deliberately coarse proxy for PR `#710`, because the reviewer path is not regressed from `origin/dev`, and track role-aware counting (Option 3) as a follow-up. The parent reviewer should require the selector docstring to state explicitly that the metric counts owner-routed tasks only. The detailed decision is recorded in PR `#722`.

Before closeout, this sidecar composed current base `origin/dev` at `d56cba58` through merge commit `4884ccb5`, preserving the existing task commit rather than rewriting history. The resulting task diff against that base remains exactly this one support artifact; no canonical truth or implementation entered the sidecar scope.

## Scope Conformance

This sidecar adds only:

`support/sidecars/ODP-ORCH-AGENT-LOAD-BALANCE-001/ODP-ORCH-AGENT-LOAD-BALANCE-001-SIDECAR-REVIEW.md`

It intentionally does not modify `.orchestrator/supervisor.py`, `.orchestrator/test_supervisor.py`, status truth, L1 canonical documents, runtime contracts, registry, config, or governance implementation.
