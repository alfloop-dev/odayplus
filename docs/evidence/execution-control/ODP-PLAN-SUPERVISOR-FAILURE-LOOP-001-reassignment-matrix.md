# Task Evidence: ODP-CONC-001 — Supervisor Failure-Loop Fallback Coverage

## Executive Summary

Task **ODP-CONC-001** (tracked upstream as `ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001`)
audits the supervisor's worker-failure reassignment path so that every
auto-dispatch owner and reviewer lane has a deterministic, fail-closed fallback,
and so that failure loops cannot strand a task on a dead lane.

This branch was opened before `dev` split `.orchestrator/supervisor.py` into
modules. The reassignment policy now lives in
`.orchestrator/worker_failure_policy.py`, and `dev` independently landed the
broader fallback maps, the dynamic candidate expansion
(`get_agent_reassignment_candidates`), and the human-gate / sidecar /
non-dispatchable guards this task had drafted. Those drafts were therefore
resolved to `dev` during the base advance. What this task contributes on top of
`dev` is the regression coverage below plus the one fail-closed gap that
coverage exposed.

---

## 1. Configured Agent Fallback Matrix

Generated from `worker_reassignment_settings({})` in
`.orchestrator/worker_failure_policy.py`. Reviewer fallbacks are identical to
owner fallbacks. `.orchestrator/config.example.json` carries the same map as
bootstrap input; the code defaults are the runtime source of truth, and a
deployment's `worker_reassignment.owner_fallbacks` / `.reviewer_fallbacks`
override them.

| Agent | Provider | Fallback Order (owner and reviewer) |
| :--- | :--- | :--- |
| **Claude** | `claude` | Claude2, Claude3, Codex, Codex2, Codex6, Antigravity, Antigravity2, Antigravity3 |
| **Claude2** | `claude2` | Claude, Claude3, Codex, Codex2, Codex6, Antigravity, Antigravity2, Antigravity3 |
| **Claude3** | `claude3` | Claude2, Claude, Codex, Codex2, Codex6, Antigravity, Antigravity2, Antigravity3 |
| **Antigravity** | `antigravity` | Antigravity2, Antigravity3, Antigravity4, Antigravity5, Antigravity6, Antigravity7, Codex2, Codex, Codex6, Claude2, Claude |
| **Antigravity2** | `antigravity2` | Antigravity, Antigravity3, Antigravity4, Antigravity5, Antigravity6, Antigravity7, Codex2, Codex, Codex6, Claude2, Claude |
| **Antigravity3** | `antigravity3` | Antigravity4, Antigravity5, Antigravity6, Antigravity7, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude |
| **Antigravity4** | `antigravity4` | Antigravity3, Antigravity5, Antigravity6, Antigravity7, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude |
| **Antigravity5** | `antigravity5` | Antigravity6, Antigravity7, Antigravity4, Antigravity3, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude |
| **Antigravity6** | `antigravity6` | Antigravity5, Antigravity7, Antigravity4, Antigravity3, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude |
| **Antigravity7** | `antigravity7` | Antigravity6, Antigravity5, Antigravity4, Antigravity3, Antigravity2, Antigravity, Codex6, Codex2, Codex, Claude2, Claude |
| **Codex** | `codex` | Codex2, Codex6, Codex3, Codex4, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity, Antigravity2 |
| **Codex2** | `codex2` | Codex, Codex6, Codex3, Codex4, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity, Antigravity2 |
| **Codex3** | `codex3` | Codex2, Codex6, Codex, Codex4, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity |
| **Codex4** | `codex4` | Codex2, Codex6, Codex, Codex3, Codex5, Codex7, Codex8, Codex9, Claude, Claude2, Antigravity |
| **Codex5** | `codex5` | Codex6, Codex2, Codex, Codex8, Codex9, Claude, Claude2, Antigravity3, Antigravity4 |
| **Codex6** | `codex6` | Codex2, Codex, Codex8, Codex9, Claude2, Claude, Antigravity3, Antigravity7 |
| **Codex7** | `codex7` | Codex6, Codex2, Codex, Codex8, Codex9, Claude, Claude2, Antigravity |
| **Codex8** | `codex8` | Codex9, Codex6, Codex2, Codex, Claude2, Claude, Antigravity3, Antigravity7 |
| **Codex9** | `codex9` | Codex8, Codex6, Codex2, Codex, Claude2, Claude, Antigravity3, Antigravity7 |
| **CodexCoordinator** | `codexcoordinator` | Codex6, Codex2, Codex, Codex8, Codex9, Claude2, Claude, Antigravity7 |
| **Gemini** | `gemini` | Gemini2, Codex, Codex2, Claude, Claude2, Antigravity, Antigravity2 |
| **Gemini2** | `gemini2` | Gemini, Codex, Codex2, Claude, Claude2, Antigravity, Antigravity2 |
| **Copilot** | `copilot` | Codex, Codex2, Claude, Claude2, Antigravity, Antigravity2 |
| **Grok** | `grok` | Codex, Codex2, Claude |

Agents absent from this map are not stranded: `get_agent_reassignment_candidates`
appends the remaining configured auto-dispatch agents, so an unmapped lane still
resolves a viable fallback.

---

## 2. Fail-Closed & Dynamic Viability Rules

1. **Human gates and non-dispatchable tasks are never auto-reassigned.**
   `maybe_reassign_task_after_worker_failure` returns early for
   `task_is_human_gate(task)` (`task_class: human_gate`, `human_required_roles`,
   or a `pending_human*` `gate_status`) and for `non_dispatchable: true`.
2. **A `Human/Ops` assignee is never replaced by an agent.** A failing agent that
   *is* the human gate cannot be reassigned, and a `Human/Ops` reviewer is
   carried through unchanged when the owner lane is reassigned — see § 3.
3. **Dynamic fallback resolution.** Unmapped agents derive candidates from the
   configured auto-dispatch agents rather than falling off the end of a
   hardcoded list.
4. **Viability filtering.** `first_viable_agent` rejects the current owner and
   reviewer, human-gate names, agents blocked by
   `agent_auto_dispatch_block_reason` (dispatch paused/disabled, account-pool
   block, quota-group saturation, provider config or auth not ready), agents in
   an excluded account pool, and agents that fail `agent_can_take_task` for the
   task (which itself rejects sidecar-only agents on mainline tasks). Among the
   survivors it picks the least-loaded lane, keeping configured preference as
   the tie-break.

---

## 3. Gap Found and Fixed: `Human/Ops` reviewer stripped on owner reassignment

`first_viable_agent` deliberately skips human-gate names. In the owner-failure
branch that made the *existing* reviewer look unviable, so the fallback search
ran and handed the review gate to whichever automated lane was least loaded.

Reproduced against `dev` before the fix — task
`{"status": "in_progress", "owner": "Antigravity4", "reviewer": "Human/Ops"}`
with a terminal `antigravity4` worker failure persisted
`new_owner=Antigravity3, new_reviewer=Antigravity5`, silently removing the human
approval requirement.

`.orchestrator/worker_failure_policy.py` now short-circuits that search: when the
reviewer is a human gate it is preserved as-is. The owner lane still recovers, so
this is a narrowing of the reassignment, not a new deadlock.

Regression test: `SupervisorFailureLoopCoverageTests.test_owner_reassignment_preserves_human_gate_reviewer`.

---

## 4. Verification Receipts

Run from the repository root on the base-advanced branch.

### Failure-loop coverage tests

```
python -m pytest .orchestrator/test_supervisor.py -k SupervisorFailureLoopCoverage -q
```

Result: **5 passed**

- `test_every_enabled_auto_dispatch_agent_has_fallback_coverage`
- `test_unmapped_agent_falls_back_to_dynamic_known_agents`
- `test_reassign_skips_human_gate_and_non_dispatchable_tasks`
- `test_owner_reassignment_preserves_human_gate_reviewer`
- `test_status_check_emission_422_warning_and_outbox_safety`

### Full supervisor suite

```
python -m pytest .orchestrator/test_supervisor.py -q
```

Result: **all tests passed (0 failures, 0 errors)**

---

## 5. Audit & Metadata

- **Task-ID**: ODP-CONC-001 (upstream: ODP-PLAN-SUPERVISOR-FAILURE-LOOP-001)
- **LLM-Agent**: Claude2
- **Reviewer**: Antigravity2
- **Prior anchors**: `1435d951`, `f7b2b9b4`, `7c1c9bd7`, `b4ada622` (Antigravity4)
- **Base advance**: merge of `dev` into `task/ODP-CONC-001`
