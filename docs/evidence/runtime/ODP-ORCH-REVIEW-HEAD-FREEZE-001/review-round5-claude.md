# Review Round 5 — ODP-ORCH-REVIEW-HEAD-FREEZE-001

Reviewer: Claude · Owner: Antigravity4 · Date: 2026-07-29
Exact head reviewed: `0b0998bac9c1ba7105a43b5aeb499f140a9e9f12`
Remote head: `0b0998ba` (matches) · PR: **#505 → dev, OPEN, MERGEABLE**

Verdict: **REQUEST CHANGES — 3 blockers (B13, B14, B15).**

Round 4's delivery gate (D1) is **resolved**: the branch is pushed, PR #505 is
open against `dev` authored by the owner, and required checks ran on the exact
head. Reviewer/owner identities stay separate (AC4 delivery side).

The code delta since the round-4 head `854be862` is one f-string lint fix plus a
clean `origin/dev` merge (`a84174a1` touches only dev-side files — no conflict
resolution silently altered this task's code). So rounds 1–4's verified fixes
(B1–B12) still stand.

Approval is withheld because CI is **red on the exact reviewed head**, and the
failure is in this task's own AC1 regression test.

---

## B13 (blocking) — CI red on the reviewed head; AC1's regression test depends on a gitignored local config

Required check `orchestrator` is `COMPLETED / FAILURE` on `0b0998ba`
(run 30448699915, job 90565269347):

```
FAILED .orchestrator/test_supervisor.py::ReviewHeadFreezeTests::
       test_supervisor_reverts_mutated_approved_head_to_review_on_disk
       AssertionError: 'review_approved' != 'review'
1 failed, 544 passed, 10 deselected, 55 subtests passed
```

The evidence doc §3 claims `545 passed`. That number is reproducible **only on
the operator's machine**. I reproduced both outcomes locally:

```bash
# with the live gitignored .orchestrator/config.json present
$ pytest -m "not requires_live_env" .orchestrator scripts     # 545 passed

# with CI's config (make bootstrap -> config.example.json)
$ pytest -m "not requires_live_env" .orchestrator scripts
FAILED ...::test_supervisor_reverts_mutated_approved_head_to_review_on_disk
```

### Root cause (verified, and *not* the one recorded in `next`)

`load_test_config()` (`.orchestrator/test_supervisor.py:26`) reads the
**gitignored** `.orchestrator/config.json`. CI's `make bootstrap` derives that
file from `config.example.json`, which does not define agent `antigravity4` and
has no `Antigravity4` entry in `ready_dispatcher.max_tasks_per_agent_by_agent`.
The test drives `dispatch_ready_tasks(..., agent_ids_override=["antigravity4"])`,
so under the CI config the per-agent loop `continue`s on capacity before it ever
reaches the head-mismatch branch at `supervisor.py:9084`.

Bisected the config dimensions against `config.example.json`, instrumenting
whether the finalize branch is entered at all (`ai_status.task_pr_ci_status`
called):

| config                                     | finalize branch entered |
| ------------------------------------------ | ----------------------- |
| `config.example.json` (= CI)               | **False**               |
| + `agents.antigravity4`                    | **False**               |
| + `max_tasks_per_agent_by_agent.Antigravity4` | **True**             |
| live `config.json`                         | **True**                |

The `next` note on the task attributes this to
`agent_has_dispatchable_primary_work` → `dispatch_priority_for_task` returning
`None` and skipping the agent. That hypothesis is **disproven** by row 3: adding
only a capacity entry makes the branch execute while `dispatch_priority_for_task`
still returns `None` for the mutated task. The differentiator is the config
shape, not the priority hook. Please do not rework against the wrong diagnosis.

### Required

Make `ReviewHeadFreezeTests` build an explicit minimal config fixture (agent
registration + capacity + quota group) instead of calling `load_test_config()`.
The suite must not depend on a gitignored, operator-specific file — AC5 asks for
*deterministic* regression tests, and a test that only passes on one machine is
the same class of defect this task exists to remove.

---

## B14 (blocking) — AC3's only regression test passes vacuously in CI

`test_supervisor_suppresses_finalize_dispatch_on_pending_ci`
(`.orchestrator/test_supervisor.py:8610`) asserts `mock_spawn.assert_not_called()`
and that status stays `review_approved`. Both are **negative** assertions, and
they hold under the CI config for the wrong reason: the dispatcher never reaches
the CI-gating code at all, so nothing could have spawned regardless of the CI
result. Instrumented proof (same probe as B13):

```
example config (= CI):  CI-gating path entered = False   -> test passes vacuously
live config:            CI-gating path entered = True    -> test is meaningful
```

So the green `orchestrator` check on any future head would still not prove AC3.
This test passed in CI today while proving nothing.

### Required

Fix the config fixture as per B13, and add a **positive control** to the AC3
test so a silently-skipped dispatcher cannot make it pass — e.g. assert the same
setup with `ci_status = "success"` *does* dispatch, alongside the `pending` case
asserting it does not.

---

## B15 (blocking) — AC1's authoritative gate fails open when the head cannot be resolved

`command_done` (`scripts/ai_status.py:4576`) guards with:

```python
current_sha = resolve_task_sha(task_id)
if current_sha and current_sha != approved_head:
    raise SystemExit(...)
```

When `resolve_task_sha` returns `None`/empty — no PR, network failure,
unauthenticated `gh`, detached branch — the guard is skipped and finalization
proceeds. Verified live against this head:

```
$ # resolve_task_sha patched to return None, approved_head set, heads unverifiable
RESULT: done SUCCEEDED -> done (gate failed OPEN)
```

AC1 requires rejecting owner finalize when HEAD differs. An unresolvable head
means we *cannot establish* that it matches, so the integrity gate must fail
closed, not wave the task through to `done`.

The same fail-open shape repeats in the supervisor and is worth fixing in one
pass:

- `dispatch_priority_for_task` (`supervisor.py:8534-8547`) — `except Exception:
  pass` around both `resolve_task_sha` and `task_pr_ci_status`, then returns
  finalize priority `1`. An unprovable head or unreadable CI dispatches finalize.
- `dispatch_ready_tasks` (`supervisor.py:9084`) — the mismatch check is
  `if approved_head and current_head and ...`, so an unresolved `current_head`
  skips the re-review transition entirely.
- `dispatch_ready_tasks` (`supervisor.py:9154`) — `ci_status` defaults to
  `"unknown"` and an exception leaves it there; the `else` arm then dispatches
  finalize. Only `pending` and `failure` suppress.

### Required

Fail closed on unresolved head and on `unknown`/errored CI in all four places,
and add regression cases for the resolver returning `None`, the resolver
raising, and `task_pr_ci_status` raising. (This is the same family the
coordinator flagged as B1/B2/B3; I confirmed each one against this head.)

---

## Verified good at this head

- **Delivery (round-4 D1)** — pushed, PR #505 open into `dev`, owner-authored,
  exact-head checks ran. `performance-gate` SUCCESS; `product` and
  `product-e2e-gate` still IN_PROGRESS at review time.
- **B1–B12 (rounds 1–3)** — unchanged since the round-4 verification; the only
  code delta is the `f"Task completed"` → `"Task completed"` lint fix.
- **dev merge integrity** — `a84174a1` touches only dev-side files; no silent
  edit to this task's code under cover of conflict resolution.
- **Ruff** — `All checks passed!` in CI on the exact head.
- `task-review-gate` is absent from PR #505's rollup, which is correct: no
  approval has been emitted for this head.

## Non-blocking notes (carried forward)

- **N1/N2** (rounds 3–4, still unaddressed) — head-mismatch and CI gating stay
  duplicated across `dispatch_priority_for_task` and `dispatch_ready_tasks` with
  divergent failure handling; the CI branches call `write_json` without
  `sync_status_pipeline`, and they overwrite `task["next"]`, discarding the
  reviewer's note. `ci_pending_since_ts` is not cleaned up on done/archive.
  Fixing B15 is a natural moment to collapse the duplication.
- **N5** (round 4) — a `review_approved` task with no PR yields `unknown` CI and
  still reaches `owned_finalize_dispatch`; folded into B15.
- **N6** (round 4) — `emit_task_review_status_check` swallows every emission
  error, so a failed `failure` emission can leave a stale `success` gate.
- **N7** (new, design) — the supervisor's revert-to-`review` on a mutated
  approved head only runs when the owner has free dispatch capacity (it lives
  inside the per-agent candidate loop, past the capacity `continue`). A
  saturated owner means a mutated head is never reconciled. `command_done`
  still blocks finalization, so this is defence-in-depth rather than a hole —
  but an unconditional pre-dispatch sweep would be more robust.

## Commands run

```bash
git rev-parse HEAD origin/task/ODP-ORCH-REVIEW-HEAD-FREEZE-001 origin/dev
gh pr list --state all --head task/ODP-ORCH-REVIEW-HEAD-FREEZE-001
gh api repos/alfloop-dev/odayplus/actions/jobs/90565269347            # orchestrator FAILURE
gh api repos/alfloop-dev/odayplus/actions/jobs/90565269347/logs       # 1 failed, 544 passed
git diff 854be862..HEAD -- .orchestrator scripts                      # lint fix only
git diff a84174a1^1 a84174a1 --stat                                   # dev-side files only
pytest -m "not requires_live_env" .orchestrator scripts               # 545 passed (live config)
# same suite with config.example.json in place                        # 1 failed (CI config)
# config-dimension bisect + finalize-branch entry instrumentation     # B13, B14
# command_done with resolve_task_sha -> None                          # B15
```
