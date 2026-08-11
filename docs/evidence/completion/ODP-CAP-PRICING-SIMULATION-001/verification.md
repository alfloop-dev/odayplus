# Verification Report: ODP-CAP-PRICING-SIMULATION-001

## Verification Summary
- **Task ID**: ODP-CAP-PRICING-SIMULATION-001
- **Title**: Complete governed Pricing Simulation interactions
- **Verification Date**: 2026-08-08
- **Result**: ALL PASS

## Executed Verification Commands

### 1. Domain & API Unit/Contract Tests (Python)
```bash
/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-orch-worktree-base-advance-001/.venv/bin/pytest tests/unit/test_pricing_simulation.py tests/contract/test_pricing_simulation_contract.py -v
```
**Output**:
```text
======================== 6 passed, 3 warnings in 15.55s ========================
```
- `test_invalid_scenario_execution_blocked`: PASS
- `test_baseline_and_alternatives_stay_distinguishable`: PASS
- `test_unavailable_results_fail_closed`: PASS
- `test_decision_writeback_idempotent_and_audited`: PASS
- `test_simulate_scenario_contract_valid_and_invalid`: PASS
- `test_decision_writeback_contract_idempotency_and_fail_closed`: PASS

### 2. Full Existing PriceOps Integration Suite (Python)
```bash
/tmp/pantheon-worker-worktrees/oday-plus-supervisor-live/odp-orch-worktree-base-advance-001/.venv/bin/pytest tests/integration/test_priceops_api.py tests/integration/test_priceops_constraints.py -v
```
**Output**:
```text
======================== 18 passed, 3 warnings in 28.57s ========================
```

### 3. Frontend Unit & Component Tests (Vitest / React)
```bash
npm run test --workspace=@oday-plus/web -- --run
```
**Output**:
```text
Test Files 35 passed (35)
     Tests 266 passed (266)
```

## Acceptance Compliance Matrix

| Requirement | Status | Verification Method |
|---|---|---|
| Invalid scenarios cannot execute | PASS | `test_invalid_scenario_execution_blocked`, HTTP 400 contract check |
| Baseline & alternatives stay distinguishable | PASS | `test_baseline_and_alternatives_stay_distinguishable`, UI `priceops-baseline-band` / `priceops-alternative-band` |
| Unavailable results fail closed | PASS | `test_unavailable_results_fail_closed`, HTTP 422 check, UI fail-closed alert |
| Decision writeback idempotent & audited | PASS | `test_decision_writeback_idempotent_and_audited`, `Idempotency-Key` replay contract check |
| Responsive UI and contract tests delivered | PASS | `GrowthWorkspace.tsx` scenario workbench + full vitest & pytest suites |

## CI Repair Round (2026-08-10)

PR #700 was requeued twice with `ci_repair_requeued`. The task branch was
317 commits behind `dev`, so the current base was composed in first
(merge `a4c6a5f6` → `dev` tip), then the two red gates were repaired.

### Failure 1 — `product` job, "Lint product code"

`ruff check tests modules apps shared models solver pipelines infra` reported
11 errors, all in files this task added:

| Rule | Location | Cause |
|---|---|---|
| F821 | `modules/priceops/application/pricing.py:282` | `Mapping` used in the `simulate_scenario` signature but never imported |
| F401 | `modules/priceops/application/pricing.py:30` | `InvalidScenarioError` imported only to be re-exported by `application/__init__.py` |
| F821 ×3 | `modules/priceops/infrastructure/repositories.py:124,125,131` | `DecisionWritebackRecord` annotations without the import |
| F821 ×3 | `modules/priceops/infrastructure/repositories.py:137,138,144` | `PlanScenarioSimulation` annotations without the import |
| I001, F401 ×2 | `tests/unit/test_pricing_simulation.py:1-18` | unused `datetime` / `UTC` imports leaving the block unsorted |

Fixes are import-level only; no domain rule, service semantic, route handler,
or UI behavior changed. `InvalidScenarioError` was added to `pricing.py`'s
`__all__` rather than deleted, because `application/__init__.py` re-exports it
and `tests/unit/test_pricing_simulation.py` imports it from that package.

### Failure 2 — `product` job, "Check API contract drift"

The two new routes never reached the checked-in OpenAPI artifact:

```text
ERROR: packages/openapi-client/openapi.json is stale — the API changed but the artifact was not regenerated.
```

Regenerated both artifact and client:

```bash
uv run python scripts/openapi/export_openapi.py     # Wrote packages/openapi-client/openapi.json (226 paths)
uv run python scripts/openapi/generate_client.py    # Wrote packages/openapi-client/src/generated/types.ts
```

Both new operations are additive, so no breaking-change approval is required:

```text
+ POST /api/v1/priceops/plans/{plan_id}/decision-writeback: new operation.
+ POST /api/v1/priceops/plans/{plan_id}/simulate-scenario: new operation.
OK: 2 additive, 0 approved breaking, 0 unapproved breaking.
```

### Re-verification on the current base

| Command | Result |
|---|---|
| `ruff check tests modules apps shared models solver pipelines infra` | `All checks passed!` |
| `uv run pytest tests/unit/test_pricing_simulation.py tests/contract/test_pricing_simulation_contract.py` | 6 passed |
| `uv run pytest tests/integration/test_priceops_api.py tests/integration/test_priceops_constraints.py` | 18 passed |
| `ODP_API_BASE_REF=origin/dev make api-contract` | `API contract gate: PASS` |
| `make node-check` | exit 0 — 41 test files, 350 tests passed |

The acceptance matrix above is unchanged: no acceptance-bearing code was
touched in this round.
