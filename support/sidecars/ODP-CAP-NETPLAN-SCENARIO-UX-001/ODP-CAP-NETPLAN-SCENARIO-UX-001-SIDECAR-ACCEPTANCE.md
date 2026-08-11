# Acceptance Packet: ODP-CAP-NETPLAN-SCENARIO-UX-001

- Sidecar task: `ODP-CAP-NETPLAN-SCENARIO-UX-001-SIDECAR-ACCEPTANCE`
- Helper kind: `acceptance_packet`
- Parent task: `ODP-CAP-NETPLAN-SCENARIO-UX-001` — Complete NetPlan scenario and infeasibility UX
- Sidecar owner: `Claude2`
- Sidecar reviewer: `Antigravity7`
- Parent owner: `Antigravity7` · Parent reviewer: `Claude`
- Evidence captured: `2026-08-10` UTC
- Parent branch: `task/ODP-CAP-NETPLAN-SCENARIO-UX-001`
- Exact pinned parent HEAD: `ffea80eb0dbe113b18763a7683ec78a79888ca9d`
- Parent PR: `#703` (OPEN, `BLOCKED`, base `dev`)
- Scope: support-only acceptance checklist, dependency map, and remediation
  evidence. No parent implementation, L1 canonical truth, contract truth,
  runtime, registry, or governance code is changed by this sidecar.

Companion packet: `ODP-CAP-NETPLAN-SCENARIO-UX-001-SIDECAR-REVIEW.md`, pinned
at the older parent head `17349991`. This packet re-verifies that packet's
findings at the *current* head `ffea80eb`, proves the fix for the blocking one,
and sharpens the UX finding from "fixture-backed" to a named producer and a
named set of missing keys.

## Executive disposition

At `ffea80eb` the parent's NetPlan behaviour is sound and the focused suite is
green (108 tests, 0 failures, independently rerun). One CI failure blocks the
merge, and it is fully mechanical: the checked-in OpenAPI artifact was never
regenerated after the task added `PUT /netplan/scenarios/{scenario_id}`.

**The blocking failure now has a proven, measured fix.** Running the two
repository generators in a scratch checkout of `ffea80eb` produces a 192-line
additive-only delta across exactly two files, turns the contract suite from
16/17 to 17/17, and makes the full API contract gate report `PASS` with the new
route classified as *additive* — so no `approved_breaking_changes.json` entry is
needed. Exact commands and measured results are in § B1.

Two acceptance points remain open and need a parent-owner decision rather than a
mechanical fix:

- **A1** — acceptance says results bind to scenario *version and hash*; only
  `problem_hash` exists. No revision field, increment rule, or version binding.
- **A2** — the five diagnostic fields and the stale badge can render, but no
  server response ever carries them. The producer is
  `modules/opsboard/application/network_rebalance.py`, and the three keys the UI
  reads (`isStale`, `isInfeasible`, `diagnostics`) are absent from every row it
  emits. Today those keys exist only in `apps/web/features/operator/fixtures.ts`.

Recommended sequence for the parent: apply B1 (mechanical, unblocks the PR),
then dispose of A1 and A2 either by implementing them or by narrowing the
acceptance and evidence language to what is actually delivered. The parent's own
`docs/evidence/completion/.../acceptance.md` currently marks all five criteria
`PASSED`; § "Evidence-doc corrections" lists the two rows that overstate.

## Acceptance criterion matrix at `ffea80eb`

| # | Parent acceptance criterion | State at pinned head | Evidence |
| --- | --- | --- | --- |
| 1 | Hard constraints are never auto-relaxed | **Verified** | Focused suite covers feasible and infeasible hard-constraint paths; infeasible solves return structured diagnoses without moving any limit. 108/108 pass. |
| 2 | Results bind to scenario version and hash | **Partial** — hash yes, version absent | `ScenarioSolveRecord.problem_hash` (`modules/netplan/domain/planning.py:247`) plus `is_stale()` recompute (`:249`). No scenario revision/version field anywhere on the NetPlan surface. See § A1. |
| 3 | All structured diagnostic fields render | **Partial** — renderer yes, data path no | `RebalancePanel.tsx` renders all five fields; `NetPlanDiagnostic` in `types.ts` matches `InfeasibilityDiagnosis.to_dict()` key-for-key. But no API producer emits them. See § A2. |
| 4 | Stale results cannot be approved | **Verified server-side**; UI surface unreachable | `submit_for_approval` / `decide` reject a stale solve; `update_scenario` resets `SOLVED`/`INFEASIBLE` to `DRAFT`. `GET /netplan/scenarios/{id}` exposes `solve.is_stale`. The operator UI reads a *different* endpoint that never sets `isStale`. See § A2. |
| 5 | Feasible, infeasible, and failure tests are delivered | **Verified for backend**; frontend test is shape-only | Backend: 108 passed. Frontend: `netplanDiagnosticsUx.test.ts` uses `readFileSync` + `toContain` on three source files — it asserts that identifiers exist in source text, never mounts a component. See § A3. |
| 6 | Branch is merge-ready | **Not satisfied** — mechanical, fix proven | `product` job FAILURE on the artifact parity test; 1 failed, 2822 passed. See § B1. |

## Dependency map

The parent has no task-level `depends_on`. Its real risk is a *contract chain*:
one route addition propagates through four checked-in artifacts and two CI
gates. That chain is what B1 is about.

### Contract chain (the blocking path)

```
apps/api/app/routes/netplan.py
  ├─ + class NetPlanUpdateScenarioPayload        (route file :39)
  └─ + @router.put("/scenarios/{scenario_id}")   (route file :161)
        │
        ▼  scripts/openapi/export_openapi.py  (build_schema → serialize)
   packages/openapi-client/openapi.json          ← STALE at ffea80eb
        │
        ▼  scripts/openapi/generate_client.py  (render)
   packages/openapi-client/src/generated/types.ts ← consistent with the STALE artifact
        │
        ▼  consumed by 15+ web modules via "@oday-plus/openapi-client"
   apps/web/features/{shell,operator}/**
```

Both CI gates sit on this chain; the first one to run is the one that failed:

| Gate | Where | Behaviour at `ffea80eb` |
| --- | --- | --- |
| `tests/contract/test_openapi_artifact_and_client.py` | `product` job, step *Test product code* | **FAIL** — `test_artifact_is_checked_in_and_matches_the_live_app` |
| `make api-contract` (`scripts/openapi/check_drift.py`) | `product` job, step *Check API contract drift* | **Not reached** — the step is after the failing pytest step |

Note the trap: `test_generated_client_matches_the_artifact` **passes** at
`ffea80eb`, because the client is faithfully generated from the *stale* artifact.
Regenerating only the client would not fix anything. Both generators must run,
artifact first.

### Runtime data chain (the A2 path)

Two independent NetPlan HTTP surfaces exist, and the parent extended the one the
operator UI does not read:

```
surface A — canonical NetPlan API              surface B — operator rebalance API
GET /api/v1/netplan/scenarios/{id}             POST /api/v1/operator/network-rebalance/
  └─ payload["solve"]["is_stale"]      ← NEW        stores/{store_id}/netplan/solve
  └─ solve.result.diagnostics                     └─ modules/opsboard/application/
  └─ solve.result.infeasible                         network_rebalance.py
                                                       └─ store["netPlanScenarios"] = plan_rows
   (no web consumer)                                        │
                                                            ▼
                                        networkFindAreasViewModel.ts:660 (pass-through)
                                                            ▼
                                        RebalancePanel.tsx:204 selected.netPlanScenarios
                                                            ▼
                                        renders diagnostics / stale / infeasible
```

`plan_rows` is built at `modules/opsboard/application/network_rebalance.py:562`
(recommendation row) and `:584` (alternative rows). Both build from
`result_payload = solve.result.to_dict()`, which **already contains**
`infeasible` and `diagnostics` (`solver/netplan/optimizer.py:116-117`) — the
rows simply do not copy them. The seed path at `:636-655` likewise emits none.
A repo-wide grep for `isStale`, `isInfeasible`, or a `diagnostics` key across
`modules/opsboard` and `apps/api` returns zero hits.

### Upstream / downstream summary

| Direction | Surface | Relationship |
| --- | --- | --- |
| Upstream | `solver/netplan/{model,optimizer}.py` | Owns `InfeasibilityDiagnosis`; its five snake_case keys are exactly the UI's `NetPlanDiagnostic` fields. No adapter/renaming needed. |
| Upstream | `modules/netplan/domain/planning.py` | Owns `problem_hash` and `is_stale()`. Sole staleness authority. |
| Sibling | `modules/opsboard/application/network_rebalance.py` | The only server producer of `netPlanScenarios`. Untouched by the parent — this is the A2 gap. |
| Downstream | `packages/openapi-client/**` | Generated, `DO NOT EDIT`. Regenerate; never hand-edit. |
| Downstream | `apps/web/features/operator/{types,fixtures}.ts`, `network/RebalancePanel.tsx` | Renderer is ready and correctly typed. |
| Gate | `tests/contract/test_openapi_artifact_and_client.py`, `make api-contract` | Both guard the contract chain. |

No dependency on `ODP-PLAN-NETPLAN-BASELINE-APPROVAL-001` was found; the parent
summary explicitly states it does not wait for baseline approval, and nothing in
the reviewed delta reads a baseline receipt. That separation holds at `ffea80eb`.

## B1 — OpenAPI artifact drift (blocking, fix proven)

State at `ffea80eb`, confirmed by direct inspection of the committed artifact:

```text
packages/openapi-client/openapi.json
  /api/v1/netplan/scenarios/{scenario_id}  ->  ['get']        # 'put' missing
  grep -c NetPlanUpdateScenarioPayload     ->  0
packages/openapi-client/src/generated/types.ts
  grep -c NetPlanUpdateScenarioPayload     ->  0
```

CI at the same head, run `31383436219`, job `93438468322`:

```text
orchestrator      SUCCESS
performance-gate  SUCCESS
product-e2e-gate  SUCCESS
product           FAILURE
task-review-gate  FAILURE   (expected: task status is in_progress after reopen)

1 failed, 2822 passed, 140 warnings in 641.86s
FAILED tests/contract/test_openapi_artifact_and_client.py::
       test_artifact_is_checked_in_and_matches_the_live_app
```

### Proven remediation

Run in a scratch worktree detached at `ffea80eb` (removed afterwards; nothing
was pushed and no parent branch was modified):

```bash
python scripts/openapi/export_openapi.py      # -> Wrote packages/openapi-client/openapi.json (224 paths).
python scripts/openapi/generate_client.py     # -> Wrote packages/openapi-client/src/generated/types.ts.
```

Measured result — the delta is additive-only and confined to the two generated
files:

```text
 packages/openapi-client/openapi.json           | 182 +++++++++++++++++++++++++
 packages/openapi-client/src/generated/types.ts |  11 +-
 2 files changed, 192 insertions(+), 1 deletion(-)
```

Post-regeneration gates, both re-run in that scratch worktree:

```text
pytest tests/contract/test_openapi_artifact_and_client.py
  17 tests, 0 failures        (was 17 tests, 1 failure)

python scripts/openapi/check_drift.py --base-ref origin/dev
  [1/3] artifact freshness   OK
  [2/3] client freshness     OK
  [3/3] breaking-change diff + PUT /api/v1/netplan/scenarios/{scenario_id}: new operation.
                             OK: 1 additive, 0 approved breaking, 0 unapproved breaking.
  API contract gate: PASS
```

Parent-owner action:

1. On `task/ODP-CAP-NETPLAN-SCENARIO-UX-001`, run both generators in that order.
   Do not hand-edit either file — `test_generated_client_is_marked_do_not_edit`
   and the freshness checks exist precisely to catch that.
2. Commit both generated files inside the task scope.
3. No `scripts/openapi/approved_breaking_changes.json` entry is required; the
   gate already classifies the new route as additive.
4. Push and let PR `#703` re-run. Do not call parent `done` until `#703` is
   green and merged.

## A1 — Scenario version binding is absent (owner decision)

`problem_hash` is a content fingerprint over `options_by_entity`, `constraints`,
`risk_penalty`, and `alternative_limit`. It detects change; it does not order
change. Searches across `modules/netplan`, `apps/api/app/routes/netplan.py`, the
operator web surface, and the integration tests find no `scenario_version`,
`scenarioVersion`, or NetPlan revision field.

The parent owner should pick one and record it:

- **Implement** — add a scenario revision, increment it on the updates that
  invalidate a solve, and persist the solved revision beside the hash; or
- **Re-scope** — state in the acceptance and evidence docs that the content hash
  is the sole version surrogate, and why that satisfies the product contract.

Either is defensible. What is not defensible is the current evidence row, which
claims the criterion `PASSED` while citing only `problem_hash`.

## A2 — Diagnostics and staleness never reach the UI (owner decision)

The renderer is ready and the types are correct — the key names line up exactly
between `solver/netplan/model.py:472-479` and `apps/web/features/operator/types.ts`.
The gap is purely that no server response carries them.

| Key the UI reads | Emitted by `network_rebalance.py`? | Available at that call site? |
| --- | --- | --- |
| `diagnostics` | No | Yes — `result_payload["diagnostics"]` |
| `isInfeasible` | No | Yes — `result_payload["infeasible"]` |
| `isStale` | No | Derivable — `solve.is_stale(scenario)` |

Consequence at `ffea80eb`: in a running system every `netPlanScenarios` row
arrives without those three keys, so `RebalancePanel`'s diagnostic block and
stale badge are unreachable. They render only against `fixtures.ts`. Criterion 4
("stale results cannot be approved") is genuinely enforced — but on the server,
in `submit_for_approval` / `decide`, not through anything the operator can see.

Owner options:

- **Wire it** — forward the three keys in both `plan_rows` builders and in the
  seed path, then add a behaviour test that asserts an infeasible solve response
  reaches the panel. All three values are already in scope at those lines.
- **Re-scope** — state that the operator surface intentionally ships
  renderer-only in this task, and record the wiring as follow-up work.

## A3 — Frontend test asserts source text, not behaviour

`apps/web/src/app/__tests__/netplanDiagnosticsUx.test.ts` reads
`RebalancePanel.tsx`, `types.ts`, and `fixtures.ts` with `readFileSync` and
asserts `toContain("violated_constraint")` and similar. It passes, but it would
keep passing if the component stopped rendering, if the props were never
supplied, or if the string appeared only in a comment. It is a spelling check.

This is why criterion 3 reads green while A2 is open: the test cannot detect A2
by construction. Whichever way A2 is disposed of, at least one test that mounts
`RebalancePanel` with a diagnostics-bearing scenario and asserts visible content
should exist before criterion 3 is called satisfied.

## Evidence-doc corrections requested from the parent

`docs/evidence/completion/ODP-CAP-NETPLAN-SCENARIO-UX-001/acceptance.md` marks
all five criteria `PASSED`. Two rows overstate what the pinned head delivers:

| Row | Current claim | Suggested |
| --- | --- | --- |
| Results bind to scenario version and hash | `PASSED` — cites `problem_hash` | `PARTIAL` until A1 is implemented or the contract is re-scoped to hash-only |
| All structured diagnostic fields render | `PASSED` — "rendered in `RebalancePanel.tsx` and validated by Vitest" | `PARTIAL` — renderer and types verified; no API producer emits the fields, and the Vitest assertions are source-text checks |

The remaining three rows are accurate at `ffea80eb`.

## Independent verification log

All commands run against a scratch worktree detached at
`ffea80eb0dbe113b18763a7683ec78a79888ca9d`, then removed.

```bash
# focused NetPlan suite — parent behaviour
python -m pytest modules/netplan tests/integration/test_netplan_solver.py -q
# junitxml: tests=108 failures=0 errors=0 skipped=0  (110.2s)  exit 0

# contract suite — before regeneration
python -m pytest tests/contract/test_openapi_artifact_and_client.py -q
# junitxml: tests=17 failures=1  -> test_artifact_is_checked_in_and_matches_the_live_app  exit 1

# proven remediation
python scripts/openapi/export_openapi.py      # 224 paths
python scripts/openapi/generate_client.py
git diff --stat                                # 2 files, +192 -1

# contract suite — after regeneration
python -m pytest tests/contract/test_openapi_artifact_and_client.py -q
# junitxml: tests=17 failures=0  exit 0

# full contract gate — after regeneration
python scripts/openapi/check_drift.py --base-ref origin/dev
# API contract gate: PASS   (1 additive, 0 breaking)   exit 0
```

Interpreter: `/home/lupin/oday-plus/.venv/bin/python`.

Not re-run in this round: the Vitest file. Its content was read directly and is
analysed in § A3; the remote `product-e2e-gate` is `SUCCESS` at `ffea80eb`, which
remains the available web signal.

Branch position at capture: `git rev-list --left-right --count origin/dev...ffea80eb`
reports `30 6` — the parent is 30 behind and 6 ahead of `origin/dev`. The
regeneration above was measured against that base and the drift gate diffed
cleanly against `origin/dev`, so a base refresh should not change the B1 outcome.

## Handoff checklist

Two distinct roles act on this packet.

**`Antigravity7` as sidecar reviewer** — reviews only item 1:

1. This packet is support-only, pins parent head `ffea80eb`, and its single
   repository delta is this file.

**`Antigravity7` as parent owner** — disposes of items 2-6 on the parent task;
these are not sidecar review gates:

2. Apply B1 with both generators, commit the generated delta, get `#703` green.
3. Record an explicit A1 disposition: implement version binding, or re-scope.
4. Record an explicit A2 disposition: wire the three keys through
   `network_rebalance.py`, or re-scope to renderer-only.
5. If criterion 3 is to stay green, add one render-level test (A3).
6. Correct the two overstated rows in the parent `acceptance.md`.

## Sidecar boundary

This file is the sole repository deliverable of
`ODP-CAP-NETPLAN-SCENARIO-UX-001-SIDECAR-ACCEPTANCE`. It changes no parent
implementation, no L1 canonical document, no contract truth, and no runtime,
registry, or governance behaviour. The scratch worktree used for verification
was detached, never pushed, and has been removed; the parent branch is
byte-identical to what it was before this round.

Sidecar approval is not parent merge approval. Parent PR `#703` remains blocked
at the pinned head until B1 lands.

Handoff target: `Antigravity7`.
