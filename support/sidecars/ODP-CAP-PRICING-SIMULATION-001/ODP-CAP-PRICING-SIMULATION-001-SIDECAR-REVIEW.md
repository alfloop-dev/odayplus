# Review Packet: ODP-CAP-PRICING-SIMULATION-001

- Sidecar task: `ODP-CAP-PRICING-SIMULATION-001-SIDECAR-REVIEW`
- Parent task: `ODP-CAP-PRICING-SIMULATION-001`
- Sidecar owner: `Codex`
- Assigned sidecar reviewer: `Codex2`
- Final parent owner / reviewer: `Claude2` / `Antigravity`
- Evidence refreshed: `2026-08-10` UTC
- Parent branch: `task/ODP-CAP-PRICING-SIMULATION-001`
- Final approved parent HEAD: `f390c81d9566233a05be99dca55c7bd5595cff9a`
- Parent PR: `#700`, merged into `dev` as `273a7705b7233511679b705b8281d689a2a82758`
- Scope: review and evidence only; no parent implementation or canonical truth changed

## Executive disposition

The parent is already merged and archived as `done`, and all five recorded PR
gates are green. That delivery state is valid, but it does not resolve all of
the acceptance risks found in the earlier sidecar review.

At the final approved parent head:

1. **B1 remains open:** the browser workbench reports a successful audited
   decision writeback without calling either new API;
2. **B2 remains open:** `scenario_selected` accepts and stores an unavailable
   scenario id once the plan has any feasible optimization;
3. **B3 is resolved:** the final parent head is Ruff-clean, its OpenAPI artifact
   and generated client include both new routes, and PR #700's required gates
   are all green; and
4. **M1 remains open:** unknown candidate item ids are ignored while
   `is_baseline_distinct` is hard-coded `True` even when the effective
   candidate equals the baseline.

Because the parent has already closed, the recommended disposition is a
follow-up implementation task rather than reopening or mutating this support
slice. The parent owner should prioritize B1 and B2 before treating the visible
workbench as an end-to-end audited decision surface. M1 needs an explicit
product/contract decision and coverage.

## Final delivery provenance

The live task archive and GitHub agree on the final delivery:

| Field | Durable evidence |
| --- | --- |
| Parent terminal state | `done`, archived `2026-08-10T12:12:37Z` |
| Approved / verified head | `f390c81d9566233a05be99dca55c7bd5595cff9a` |
| PR | `#700`, merged `2026-08-10T12:10:18Z` |
| Merge commit | `273a7705b7233511679b705b8281d689a2a82758` |
| Required checks | `orchestrator`, `product`, `performance-gate`, `product-e2e-gate`, and `task-review-gate`: all success |
| Closeout verification | focused unit/contract: 6 passed; PriceOps integration: 18 passed; API contract: pass; `make node-check`: 350 tests passed |

The final parent PR changes the domain/service, in-memory repository, two API
routes, the operator UI, Python and frontend tests, completion evidence,
OpenAPI artifact, and generated TypeScript client. No L1 canonical document is
part of the PR. This sidecar branch changes only this support artifact relative
to the current `dev` base.

## Acceptance evidence matrix

| Parent acceptance | Final-head evidence | Sidecar assessment |
| --- | --- | --- |
| Invalid scenarios cannot execute | Numeric invalid inputs return domain errors / HTTP 400 and focused tests pass. Unknown keys in `candidate_prices` are silently ignored. | **Partial; M1 open** |
| Baseline and alternatives stay distinguishable | Separate result objects and visible UI bands exist. `is_baseline_distinct` remains hard-coded `True`, including when the effective candidate equals baseline. | **Partial; M1 open** |
| Unavailable results fail closed | Approval without optimization returns HTTP 422. A missing `selected_scenario_id` is still accepted for `scenario_selected` after any feasible optimization. | **Fail; B2 open** |
| Decision writeback is idempotent and audited | The API command guard and audit route exist and CI is green. The visible UI bypasses the route and fabricates a local success record. | **Fail end-to-end; B1 open** |
| Responsive UI and contract tests are delivered | Component and contract tests exist. The writeback component test asserts only local rendering, not a request, response, failure, replay, or idempotency key; the two-column layout has no responsive assertion. | **Partial; B1 open** |

## Finding reconciliation

### B1 — open: UI writeback still fabricates success

At final parent head `f390c81d`, `GrowthWorkspace.tsx:532-540` still implements
`handleWriteback()` solely with `setWritebackRecord()` and a random
`pricing-decision-*` id. The file contains no reference to
`/simulate-scenario` or `/decision-writeback`. Its displayed P10/P50/P90 values
are browser-side price multipliers rather than server simulation output.

The component then renders `✓ 決策成功寫回與審計`. No request, command receipt,
server decision record, or audit event is created by that visible path. The
frontend test clicks the button and checks only that the local
`priceops-audit-status` element appeared.

Required follow-up:

- call the scenario and decision-writeback APIs;
- use a stable idempotency key and render server-returned data;
- show success only after a successful response; and
- assert request payload, failure handling, conflict/replay behavior, and audit
  response in component tests.

### B2 — open: selected scenario availability is not validated

`PriceOpsService.writeback_decision()` checks only whether the plan has a
feasible optimization for `scenario_selected`. It does not require a non-empty
`selected_scenario_id`, call `get_scenario_simulation()`, verify plan ownership,
or reject an infeasible selected simulation.

The final-head negative probe still stores an unavailable scenario:

```text
{'decision': 'scenario_selected',
 'selected_scenario_id': 'missing-scenario',
 'stored': 1}
```

Required follow-up: require a selected id and reject missing, cross-plan, or
infeasible scenario records before writing a decision or audit event. Add
service and HTTP 422 contract cases.

### B3 — resolved: final parent head is CI-clean

The earlier parent head `a4c6a5f6` failed the product job with 11 Ruff findings
and stale OpenAPI output. Commit `dcb42385` repaired the import/lint failures
and regenerated the OpenAPI artifact and TypeScript client; commit `f390c81d`
recorded the repair evidence.

GitHub reports every required PR #700 check as successful. Independent focused
Ruff verification on this sidecar worktree also passes. B3 therefore no longer
blocks the shipped parent head.

### M1 — open: candidate identity and distinctness remain unvalidated

`simulate_candidate_scenario()` still defaults missing plan items to current
price, ignores extra mapping keys, and writes `is_baseline_distinct=True` at
both item and plan level. The final-head probe remains:

```text
{'candidate_price': 100.0,
 'baseline_price': 100.0,
 'is_baseline_distinct': True,
 'extra_key_ignored': True}
```

Required follow-up: define whether partial candidate maps are supported, reject
unknown item ids, and compute distinctness from effective candidate prices.

## Independent verification

Executed from the sidecar worktree after composing current `origin/dev` while
the relevant parent files remained byte-identical to final head `f390c81d`:

```bash
uv run pytest -q \
  tests/unit/test_pricing_simulation.py \
  tests/contract/test_pricing_simulation_contract.py
# 6 passed; warnings only

uv run ruff check \
  apps/api/app/routes/priceops.py \
  modules/priceops/application/pricing.py \
  modules/priceops/domain/pricing.py \
  modules/priceops/infrastructure/repositories.py \
  tests/unit/test_pricing_simulation.py \
  tests/contract/test_pricing_simulation_contract.py
# All checks passed!
```

Two read-only Python probes produced the B2 and M1 outputs quoted above.

The local frontend command could not run because this refreshed worktree has no
installed `vitest` binary (`exit 127`). This is a local dependency limitation,
not a parent CI regression. The durable parent closeout records `make
node-check` at 41 files / 350 tests passed, and GitHub's final `product` check
is successful. Those green results do not cover B1 because the component test
asserts only the local success element.

## Reviewer handoff

Reviewer `Codex2` should verify that this packet:

- is pinned to final parent head `f390c81d` and merged PR #700;
- distinguishes durable merge/CI success from unresolved acceptance risks;
- changes no parent runtime, canonical truth, registry, or governance surface;
  and
- routes remaining product work to a new parent-owned follow-up rather than
  treating this sidecar as a canonical implementation lane.

This review packet is the sole repository deliverable of
`ODP-CAP-PRICING-SIMULATION-001-SIDECAR-REVIEW`.
