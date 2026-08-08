# Review Packet: ODP-CAP-NETPLAN-SCENARIO-UX-001

- Sidecar task: `ODP-CAP-NETPLAN-SCENARIO-UX-001-SIDECAR-REVIEW`
- Parent task: `ODP-CAP-NETPLAN-SCENARIO-UX-001`
- Parent task title: Complete NetPlan scenario and infeasibility UX
- Sidecar owner: `Codex3`
- Assigned sidecar reviewer / parent owner: `Antigravity7`
- Parent reviewer: `Antigravity6`
- Evidence captured: `2026-08-08` UTC
- Parent branch: `task/ODP-CAP-NETPLAN-SCENARIO-UX-001`
- Exact reviewed parent HEAD: `17349991456823c072e0d5050a22443fe47ddc72`
- Parent PR: `#703`
- Scope: review packet and evidence summary only; no parent implementation, canonical truth, runtime, registry, or governance code changed

## Executive disposition

The parent implementation at `17349991` has strong focused backend evidence:
the independently rerun NetPlan test selection passes all 108 tests, including
hard-constraint enforcement, infeasibility diagnostics, solve hash binding,
stale approval rejection, partial option preservation, and re-solve lifecycle
behavior. The focused frontend Vitest file also passes all 3 tests.

The parent must **not be finalized at the reviewed head**, however. PR `#703`
is blocked by the product CI job because the new update route and
`NetPlanUpdateScenarioPayload` changed the live FastAPI schema without updating
the checked-in OpenAPI artifact. CI reports 1 failed and 2,791 passed tests;
the failing contract test is
`tests/contract/test_openapi_artifact_and_client.py::test_artifact_is_checked_in_and_matches_the_live_app`.

Two acceptance points also need explicit parent-owner disposition:

1. The task says results bind to the scenario **version and hash**, but the
   reviewed domain model adds only `problem_hash`. No scenario revision/version
   field, increment rule, or solve-record revision binding exists on the
   reviewed NetPlan surface.
2. The UI component can render the five diagnostic fields and stale/infeasible
   badges, but the reviewed change supplies those values through fixtures.
   There is no reviewed API adapter/fetch mapping from the API's nested
   `solve.is_stale` and `solve.result.diagnostics` shape to the component's
   camel-case `isStale`, `isInfeasible`, and `diagnostics` fields. The new
   frontend tests inspect source strings rather than rendering API-derived data.

Recommended parent disposition: fix the OpenAPI drift before closeout, then
either deliver or explicitly clarify the scenario-version and live API-to-UI
acceptance boundaries. This recommendation does not reverse the recorded parent
review approval; it gives the parent owner the concrete evidence needed to make
the branch mergeable and the acceptance record precise.

## Reviewed change surface

The five consecutive parent commits from `3aad7121` through `17349991`, compared
with the first commit's parent `8585f2b2`, change 12 files with 558 insertions and
22 deletions:

| Surface | Files | Review observation |
| --- | --- | --- |
| Domain binding and lifecycle | `modules/netplan/domain/planning.py` | Adds solve `problem_hash`, stale comparison, and `SOLVED`/`INFEASIBLE` back-to-`DRAFT` transitions. No scenario revision/version is added. |
| Application service | `modules/netplan/application/planning.py` | Adds scenario updates, partial entity-option preservation, re-solve reset, problem-hash persistence, and stale guards on submit/approve. |
| API | `apps/api/app/routes/netplan.py` | Adds `PUT /netplan/scenarios/{scenario_id}` and `NetPlanUpdateScenarioPayload`; scenario detail exposes staleness at `solve.is_stale`. Checked-in OpenAPI artifacts were not regenerated. |
| Operator UX | `RebalancePanel.tsx`, `types.ts`, `fixtures.ts`, `networkFindAreas.module.css` | Defines and renders the five diagnostic fields plus stale/infeasible badges. Reviewed values originate from fixture-backed `netPlanScenarios`. |
| Frontend verification | `apps/web/src/app/__tests__/netplanDiagnosticsUx.test.ts` | Three source-string assertions confirm field names exist in source/types/fixtures; no component-render or API-mapping assertion. |
| Backend verification | `tests/integration/test_netplan_solver.py` | Adds stale protection, structured diagnostic, lifecycle reset, and partial-update coverage. |
| Parent evidence | `docs/evidence/completion/ODP-CAP-NETPLAN-SCENARIO-UX-001/{acceptance,implementation,verification}.md` | Records the intended delivery and focused test counts; acceptance overstates scenario-version binding and does not disclose fixture-only UI data flow. |

No L1 canonical document was changed by the five parent task commits. This
sidecar changes only the support artifact containing this packet.

## Acceptance and evidence matrix

| Parent acceptance criterion | Evidence at reviewed head | Assessment |
| --- | --- | --- |
| Hard constraints are never auto-relaxed | Focused pytest passes cases for feasible and infeasible hard constraints; infeasible results retain structured diagnoses. | **Verified** for the exercised solver paths. |
| Results bind to scenario version and hash | `ScenarioSolveRecord.problem_hash` is computed at solve time and `is_stale()` recomputes it from options, constraints, risk penalty, and alternative limit. No scenario revision/version field or binding was found. | **Partial — hash verified; version unimplemented or undefined.** |
| All structured diagnostic fields render | `RebalancePanel.tsx` contains markup for `violated_constraint`, `affected_stores`, `required_relaxation`, `business_impact`, and `suggested_action`; focused Vitest passes. | **Partial — renderer/source presence verified; live API-to-UI delivery not proven.** |
| Stale results cannot be approved | Backend tests prove changed constraints make the old solve stale and block submit and approve. `update_scenario` resets solved/infeasible scenarios to draft. | **Verified** in application-service tests. API detail exposure exists, but UI mapping is not proven. |
| Feasible, infeasible, and failure tests are delivered | Independent focused pytest: 108 passed. Independent focused Vitest: 3 passed. | **Verified** for the delivered focused suites. |
| Branch is merge-ready | PR `#703` product job fails the checked-in OpenAPI artifact parity test. The branch is 102 commits behind `origin/dev` at evidence capture. | **Not satisfied.** |

## Blocking finding

### B1 — OpenAPI artifact drift prevents merge

The parent adds both a Pydantic schema and a route:

- `NetPlanUpdateScenarioPayload`
- `PUT /netplan/scenarios/{scenario_id}`

Neither `packages/openapi-client/openapi.json` nor the generated client surface
is part of the five-commit task delta. In GitHub Actions run `31253134847`, job
`93092469890`, the product test suite fails because the live schema contains
`NetPlanUpdateScenarioPayload` while the committed artifact does not.

Required parent action:

1. Rebase or merge current `origin/dev` according to the task workflow.
2. Regenerate the repository-owned OpenAPI artifact and generated client using
   the normal generator, not a hand edit.
3. Commit the generated task-owned delta and rerun the contract/product checks.
4. Do not call parent `done` until PR `#703` (or its replacement) is green and
   merged.

## Acceptance clarifications for parent owner

### A1 — Scenario version binding is absent

Targeted search across `modules/netplan`, the NetPlan API route, operator web
surface, and integration test found no `scenario_version`, `scenarioVersion`,
or NetPlan revision binding. `problem_hash` is a content fingerprint, not an
explicit monotonically advancing scenario version.

The parent owner should choose one of two explicit outcomes:

- implement scenario revision/version ownership, increment it on relevant
  updates, and persist the solved revision alongside the hash; or
- clarify the acceptance contract and evidence docs that the content hash is
  the sole version surrogate, if that is the intended product contract.

### A2 — UX evidence stops at fixture-backed rendering

Targeted search at the exact parent head finds `netPlanScenarios` diagnostics
and stale data in the fixture, the component, types, and the view-model pass
through. It does not find a NetPlan API adapter mapping:

```text
API: solve.is_stale + solve.result.diagnostics
UI:  isStale + isInfeasible + diagnostics
```

The focused Vitest test reads source files and asserts that strings are present;
it does not mount `RebalancePanel`, supply an API-derived response, or assert
visible diagnostic content. The parent owner should either add the missing
adapter/render test or narrow the acceptance/evidence language to fixture-backed
renderer readiness.

## Independent verification at exact parent HEAD

Commands were executed in the clean parent worktree checked out at
`17349991456823c072e0d5050a22443fe47ddc72`:

```bash
/home/lupin/oday-plus/.venv/bin/pytest \
  modules/netplan tests/integration/test_netplan_solver.py -q
# 108 passed; warnings only

npx vitest run apps/web/src/app/__tests__/netplanDiagnosticsUx.test.ts
# 1 file passed; 3 tests passed
```

Additional evidence was read from GitHub PR `#703` and its CI run:

```text
orchestrator:      success
performance-gate: success
product-e2e-gate: success
task-review-gate: success
product:           failure
product summary:   1 failed, 2791 passed, 70 deselected
failed test:       tests/contract/test_openapi_artifact_and_client.py::
                   test_artifact_is_checked_in_and_matches_the_live_app
```

An attempted local workspace typecheck was not counted as verification because
the parent worktree does not have the `tsc` executable installed locally
(`npm run typecheck --workspace=@oday-plus/web` exits 127). This is an evidence
environment limitation, not a reported product failure; the successful
`product-e2e-gate` remains the available remote web gate signal.

## Reviewer handoff checklist

The assigned sidecar reviewer / parent owner (`Antigravity7`) should verify:

1. This packet stays support-only and accurately pins parent head `17349991`.
2. B1 is fixed with regenerated OpenAPI/client artifacts before parent closeout.
3. A1 receives an explicit version-contract disposition.
4. A2 receives either live data wiring plus behavior-level coverage or precise
   acceptance re-scoping.
5. Focused backend/frontend checks and the relevant CI gates are rerun after the
   parent branch is brought up to date.

## Recommended sidecar disposition

- **Approve this sidecar packet for handoff** once its evidence and boundaries
  are confirmed.
- **Do not treat sidecar approval as parent merge approval.** Parent PR `#703`
  remains blocked at the reviewed head.

## Sidecar boundary and handoff

This file is the sole repository deliverable of
`ODP-CAP-NETPLAN-SCENARIO-UX-001-SIDECAR-REVIEW`. It does not modify the parent
implementation, L1 canonical truth, core contracts, runtime, registry, or
governance behavior. The parent owner decides whether and how to absorb these
findings into the main task.

Handoff target: `Antigravity7` (assigned sidecar reviewer and parent owner).
