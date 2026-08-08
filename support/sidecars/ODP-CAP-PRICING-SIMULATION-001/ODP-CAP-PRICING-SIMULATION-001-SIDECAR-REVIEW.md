# Review Packet: ODP-CAP-PRICING-SIMULATION-001

- Sidecar task: `ODP-CAP-PRICING-SIMULATION-001-SIDECAR-REVIEW`
- Parent task: `ODP-CAP-PRICING-SIMULATION-001`
- Sidecar owner: `Codex5`
- Assigned sidecar reviewer / parent owner: `Antigravity6`
- Parent reviewer: `Antigravity`
- Evidence captured: `2026-08-08` UTC
- Parent branch: `task/ODP-CAP-PRICING-SIMULATION-001`
- Exact reviewed parent HEAD: `a4c6a5f63aaa84f11cf6b3ff454037613dc630ca`
- Parent merge-base: `af4650d9c75187eefbacd1a56bd847b42962c9a5`
- Parent PR: `#700` (`OPEN`, `BLOCKED`; product CI failed)
- Scope: review and evidence only; no parent implementation or canonical truth changed

## Executive disposition

**Do not finalize the parent at `a4c6a5f6`; return it to implementation and
re-review after fixes.** The core Python simulation tests are green, but the
approved head has three blocking gaps:

1. the browser workbench reports a successful audited decision writeback
   without calling either new API;
2. `scenario_selected` accepts and stores an unavailable scenario id; and
3. PR #700's product job fails Ruff with 11 findings, while `git diff --check`
   also fails on two files.

The parent is currently `review_approved` at this exact head, but that status
does not make it merge-ready. GitHub reports PR #700 as `BLOCKED`: orchestrator,
performance-gate, and product-e2e-gate succeeded; `product` failed and
`task-review-gate` remains pending.

## Reviewed change surface

Against the exact merge-base `af4650d9`, the parent changes 12 files with 1,149
insertions and 5 deletions:

| Surface | Files | Review observation |
| --- | --- | --- |
| Domain and service | `modules/priceops/domain/pricing.py`, `modules/priceops/application/pricing.py`, exports | Adds scenario validation, baseline/candidate result types, simulation, decision records, and writeback. |
| Repository | `modules/priceops/infrastructure/repositories.py` | Adds in-memory scenario and decision record accessors. |
| API | `apps/api/app/routes/priceops.py` | Adds `POST /plans/{plan_id}/simulate-scenario` and `POST /plans/{plan_id}/decision-writeback`, using the existing command receipt idempotency guard. |
| UI | `apps/web/features/operator/GrowthWorkspace.tsx` | Adds a two-band scenario workbench and decision controls, but they are local-only rather than API-backed (B1). |
| Tests | two Python suites and `GrowthWorkspace.test.tsx` | Positive-path coverage passes; the missing API wiring and unavailable selected-scenario cases are not asserted. |
| Evidence | `docs/evidence/completion/ODP-CAP-PRICING-SIMULATION-001/` | Records implementation and earlier green suite results, but does not record the current CI/lint failures or the negative probes below. |

No L1 canonical document is changed by the parent diff. This sidecar branch adds
only this support artifact.

## Acceptance evidence matrix

| Parent acceptance | Exact-head evidence | Sidecar assessment |
| --- | --- | --- |
| Invalid scenarios cannot execute | Numeric invalid cases return domain errors / HTTP 400 and focused tests pass. Unknown item ids in `candidate_prices` are silently ignored (M1). | **Partial** |
| Baseline and alternatives stay distinguishable | Separate result objects and UI bands exist. `is_baseline_distinct` is nevertheless hard-coded `True`, including when candidate equals baseline (M1). | **Partial** |
| Unavailable results fail closed | Approval without optimization returns HTTP 422. A missing `selected_scenario_id`, however, is accepted once any optimization exists (B2). | **Fail** |
| Decision writeback is idempotent and audited | The API command receipt guard replays identical payloads and rejects key/payload conflicts. The visible UI bypasses it and fabricates an audit-success record (B1). | **Fail end-to-end** |
| Responsive UI and contract tests are delivered | The component and contract tests exist and pass. The new UI test only clicks the local handler; it does not assert an API request, response, error, or idempotency key. The two-column scenario grid has no responsive assertion. | **Partial** |

## Blocking findings

### B1 — UI writeback fabricates success without persistence or audit

At `GrowthWorkspace.tsx:532-540`, `handleWriteback()` only calls
`setWritebackRecord()` and creates a random `pricing-decision-*` id with
`Math.random()`. The entire parent file contains no reference to
`/simulate-scenario` or `/decision-writeback`. The candidate P10/P50/P90 values
at lines 721-749 are also derived from browser-side price multipliers rather
than the new simulation API.

The component then renders `✓ 決策成功寫回與審計` at lines 795-809. That claim
is false for the delivered UI path: no state left the browser, no command
receipt was created, and no audit event was recorded. The component test at
`GrowthWorkspace.test.tsx:229-263` reinforces the false positive by clicking
the button and asserting only that the local success element appeared.

Required correction: call the scenario and decision-writeback APIs, supply a
stable idempotency key, render server-returned simulation/audit data, and show
success only after a successful response. Add failure, replay, and request
payload assertions.

### B2 — unavailable selected scenarios do not fail closed

`PriceOpsService.writeback_decision()` checks only that an optimization exists
and is feasible for `scenario_selected`. It never requires
`selected_scenario_id`, loads it with `get_scenario_simulation()`, verifies that
it belongs to the plan, or checks `scenario.is_feasible`.

The following exact-head probe succeeded and stored the invalid decision:

```text
{'decision': 'scenario_selected',
 'selected_scenario_id': 'missing-scenario',
 'stored': 1}
```

Required correction: for `scenario_selected`, require a non-empty id and reject
missing, cross-plan, or infeasible scenario records before writing any decision
or audit event. Add service and HTTP 422 contract cases.

### B3 — the approved head is not CI-clean

The failed `product` job in Actions run `31254114260`, job `93094801150`, runs:

```bash
uv run ruff check tests modules apps shared models solver pipelines infra
```

It reports 11 findings:

- unused `InvalidScenarioError` import and undefined `Mapping` in
  `modules/priceops/application/pricing.py`;
- undefined `DecisionWritebackRecord` and `PlanScenarioSimulation` annotations
  in `modules/priceops/infrastructure/repositories.py` (six occurrences);
- an unsorted import block and two unused datetime imports in
  `tests/unit/test_pricing_simulation.py`.

Independent `git diff --check af4650d9..a4c6a5f6` also reports a new blank line
at EOF in `modules/priceops/application/__init__.py` and
`modules/priceops/infrastructure/repositories.py`.

Required correction: make the complete product Ruff command and the scoped
diff check green before re-review; fixing only the first F401 named in the
parent handoff is insufficient.

## Additional finding

### M1 — candidate item identity and distinctness are not validated

`simulate_candidate_scenario()` defaults every missing item to its current
price and ignores extra mapping keys. It nevertheless hard-codes
`is_baseline_distinct=True` at both item and plan level. An exact-head probe with
`{"typo-item": 110.0}` produced:

```text
{'candidate_price': 100.0,
 'baseline_price': 100.0,
 'is_baseline_distinct': True,
 'extra_key_ignored': True}
```

The parent should either define and test partial scenarios explicitly or reject
unknown/missing item ids. In either case, distinctness must be computed from
the effective candidates rather than asserted unconditionally.

## Independent verification at exact parent HEAD

The parent head was checked in a detached temporary worktree. Results:

```bash
# Focused task plus existing PriceOps regression suites
/home/lupin/oday-plus/.venv/bin/pytest -q \
  tests/unit/test_pricing_simulation.py \
  tests/contract/test_pricing_simulation_contract.py \
  tests/integration/test_priceops_api.py \
  tests/integration/test_priceops_constraints.py
# 24 passed; warnings only

# New GrowthWorkspace component suite
npm run test --workspace=@oday-plus/web -- \
  --run features/operator/__tests__/GrowthWorkspace.test.tsx
# 1 file passed; 10 tests passed

# Parent-touched Python surface
/home/lupin/oday-plus/.venv/bin/ruff check \
  apps/api/app/routes/priceops.py \
  modules/priceops/application/pricing.py \
  modules/priceops/domain/pricing.py \
  modules/priceops/infrastructure/repositories.py \
  tests/unit/test_pricing_simulation.py \
  tests/contract/test_pricing_simulation_contract.py
# FAIL: 11 findings

git diff --check af4650d9c75187eefbacd1a56bd847b42962c9a5..a4c6a5f63aaa84f11cf6b3ff454037613dc630ca
# FAIL: 2 new-blank-line-at-EOF findings
```

The green test results establish that the positive paths described by the
parent work. They do not override B1/B2, because the tests do not exercise
those paths, and they do not override B3, because CI's product lint gate is a
separate required check.

## Recommended parent repair and re-review packet

Before asking `Antigravity` to stamp a new head, the parent owner should provide:

1. API-backed UI simulation and writeback tests, including network failure and
   idempotent replay;
2. missing, cross-plan, and infeasible `scenario_selected` rejection tests;
3. explicit candidate item-id / distinctness behavior and tests;
4. a green full product Ruff command, scoped `git diff --check`, focused Python
   suites, and GrowthWorkspace suite; and
5. refreshed completion evidence pinned to the repaired commit and current PR
   checks.

## Sidecar boundary and handoff

This artifact is the sole repository deliverable of
`ODP-CAP-PRICING-SIMULATION-001-SIDECAR-REVIEW`. It changes no canonical truth,
runtime, registry, governance implementation, or parent code.

Handoff target: `Antigravity6` (parent owner / assigned sidecar reviewer), who
decides which findings to absorb into the parent before requesting re-review
from `Antigravity`.
