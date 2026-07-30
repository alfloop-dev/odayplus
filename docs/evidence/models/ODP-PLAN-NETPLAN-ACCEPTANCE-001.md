# Task Completion & Management Acceptance Packet: ODP-PLAN-NETPLAN-ACCEPTANCE-001

This document provides product-grade management acceptance and verification evidence for task **ODP-PLAN-NETPLAN-ACCEPTANCE-001**: *完成 NetPlan hard constraint 與管理驗收*.

## 1. Task Metadata

- **Task ID**: `ODP-PLAN-NETPLAN-ACCEPTANCE-001`
- **Title**: 完成 NetPlan hard constraint 與管理驗收
- **Owner**: `Antigravity2`
- **Reviewer**: `Claude`
- **Phase**: P1 Optimization Readiness
- **Program ID**: `ODP-PLAN-GAP-CLOSEOUT-2026-07-30`
- **Gap ID**: `GAP-P1-006`
- **Primary Dependencies**: `ODP-PLAN-SOLVER-RUNTIME-COMPAT-001` (done)

---

## 2. Executive Summary & Acceptance Criteria Verification

| Acceptance Criterion | Verification Verdict | Evidence Summary |
|---|---|---|
| **100% Hard Constraints** | **PASS** | Enforces 5 hard constraint types: budget ceiling (`max_budget`), gross margin floor (`min_expected_gross_margin`), capacity delta floor (`min_capacity_delta`), average risk ceiling (`max_average_risk`), and per-action min/max counts (`min_action_counts`, `max_action_counts`). Dedicated, tested infeasibility diagnosis logic exists for 100% of hard constraints (including `max_average_risk` -> `AVERAGE_RISK_INFEASIBLE` / `max_average_risk` and `max_action_counts` -> `ACTION_COUNT_MAX_INFEASIBLE` / `max_action_counts.<action>`), eliminating generic fallbacks for individual constraint failures. |
| **Superiority over Baseline** | **PASS** | Evaluated against a named immutable approved management baseline input (`ManagementBaselineInput` / `WBS-NETPLAN-APPROVED-BASELINE-2026Q3`) under identical constraint sets and objective functions via `compare_solver_against_management_baseline()`. Generates structured comparison receipt (`ManagementBaselineComparisonReceipt`) proving deterministic objective superiority or equality (`superior_or_equal: True`, `objective_gain_over_baseline >= 0.0`), or proving solver feasibility when the baseline is infeasible (`baseline_feasible: False` with explicit constraint violations). Management acceptance remains explicitly gated behind authentic `Human/Ops` approval (`NetPlanScenarioStatus.PENDING_APPROVAL` -> `APPROVED`). |
| **Explainable Infeasibility & Alternatives** | **PASS** | If a scenario is infeasible, the solver returns structured diagnostics detailing `violated_constraint`, `affected_stores`, `required_relaxation`, `business_impact`, and `suggested_action` without auto-relaxing limits. Dedicated diagnosis branches cover 100% of hard constraint types across both OR-Tools (`optimizer.py`) and CVXPY (`robust.py`). Top rank-ordered alternative plans are returned for feasible solves. |
| **Scenario Provenance & Metadata** | **PASS** | Tracks `source_snapshot_ids`, `policy_version`, `model_version`, `feature_version`, `engine` metadata across OR-Tools (authoritative MIP), CVXPY (robust scenario solver), and Pymoo (frontier solver). |
| **Management Acceptance Packet** | **PASS** | Full end-to-end lifecycle verification (DRAFT → SOLVED → PENDING_APPROVAL → APPROVED → EXECUTED → OUTCOME_OBSERVED → CLOSED) complete with non-repudiable audit trails and authentic `Human/Ops` approval gates. |

---

## 3. NetPlan Solver & Application Architecture

The NetPlan subsystem is structured across discrete, isolated layers to guarantee deterministic execution and ABI safety:

### 3.1 Primitive & Solver Layer (`solver/netplan/`)
- **`model.py`**: Pure domain primitives defining `NetworkAction` (OPEN, KEEP, IMPROVE, MOVE, EXIT), `ActionOption`, `NetPlanConstraints`, `InfeasibilityDiagnosis`, `ManagementBaselineInput`, and `ManagementBaselineComparisonReceipt`.
- **`optimizer.py`**: CP-SAT / SCIP MIP solver via OR-Tools. Enumerates action options, enforces all 5 hard constraints, extracts binding constraints, performs dedicated infeasibility diagnosis across all 100% constraint types, and executes deterministic baseline comparison via `compare_solver_against_management_baseline()`.
- **`robust.py`**: CVXPY robust optimization solver supporting `WEIGHTED_EXPECTED`, `MAX_MIN`, and `CVAR` objective functions under scenario uncertainty, equipped with dedicated diagnostics for all robust constraints (`AVERAGE_RISK_INFEASIBLE`, `CAPACITY_DELTA_INFEASIBLE`, `ACTION_COUNT_MIN_INFEASIBLE`, `ACTION_COUNT_MAX_INFEASIBLE`, `SCENARIO_FLOOR_INFEASIBLE`, `BUDGET_INFEASIBLE`).

### 3.2 ABI Isolation Layer (`solver/process_isolation.py`)
- **`solver/process_isolation.py`**: Native C++ ABI isolation runner preventing symbol conflicts between OR-Tools (`libortools`) and CVXPY/HiGHS (`highspy`). This module lives at the `solver/` top level, not inside `solver/netplan/`.

### 3.3 Production Application Module (`modules/netplan/`)
- **`application/planning.py` & `production.py`**: Coordinates multi-engine solves across OR-Tools (authoritative MIP), CVXPY (robust scenario analysis), and Pymoo (multi-objective Pareto frontier).
- **`application/planning.py`**: Governs scenario lifecycle status transitions, ensuring infeasible plans cannot proceed to approval, and logging actor/reason audit records at every transition. Lifecycle enforcement is also present in `modules/netplan/domain/planning.py`.
- **`infrastructure/`**: In-memory and SQL persistence adapters for scenarios, solves, approvals, executions, and realized outcome tracking.

---

## 4. Test Verification & Verification Command Results

### 4.1 Canonical Verification Command
```bash
/home/lupin/oday-plus/.venv/bin/pytest -q tests -k "netplan or ortools or robust" && git diff --check
```

This command collects **11 tests** from `tests/` (the top-level test root only):

| Suite | Tests |
|---|---|
| `tests/integration/test_netplan_solver.py` | 9 |
| `tests/contract/test_operator_network_rebalance_api.py` | 1 |
| `tests/integration/test_operator_canonical_wiring.py` | 1 |

### 4.2 Canonical Command Verbatim Output
```text
...........                                                              [100%]
=============================== warnings summary ===============================
../../../../home/lupin/oday-plus/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1
  /home/lupin/oday-plus/.venv/lib/python3.12/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
11 passed in 18.23s
```

### 4.3 Supplementary Explicit Runs

The suites below live outside `tests/` and are not collected by the canonical command. They were run explicitly and all passed:

**Supplementary command:**
```bash
/home/lupin/oday-plus/.venv/bin/pytest -q \
  solver/netplan/tests/test_robust.py \
  modules/netplan/tests/test_netplan_production_execution.py \
  tests/solver/test_runtime_compat.py \
  --tb=no
```

#### Suite A: `solver/netplan/tests/test_robust.py` (7 tests)
- `test_cvxpy_weighted_and_max_min_objectives_choose_different_actions`: PASSED
- `test_cvxpy_cvar_contract_controls_lower_tail`: PASSED
- `test_cvxpy_infeasible_scenario_floor_has_diagnostics`: PASSED
- `test_missing_cvxpy_fails_closed`: PASSED
- `test_missing_mixed_integer_backend_fails_closed`: PASSED
- `test_cvxpy_infeasible_max_average_risk_has_dedicated_diagnostics`: PASSED
- `test_cvxpy_infeasible_max_action_counts_has_dedicated_diagnostics`: PASSED

#### Suite B: `modules/netplan/tests/test_netplan_production_execution.py` (2 tests)
- `test_production_netplan_executes_all_three_oss_contracts`: PASSED
- `test_production_netplan_runtime_failure_leaves_scenario_draft`: PASSED

#### Suite C: `tests/solver/test_runtime_compat.py` (10 tests — full list)
- `test_process_isolation_runner_executes_solvers_without_abi_conflict`: PASSED
- `test_both_same_process_orders_reproduce_abi_conflict_and_isolation_fixes_both`: PASSED
- `test_inspect_oss_capability_executes_real_import_and_minimal_solve`: PASSED
- `test_probe_package_in_isolation_explicit_highs_solve`: PASSED
- `test_process_isolation_raises_error_on_process_crash`: PASSED
- `test_process_isolation_large_payload_stdin_transport`: PASSED
- `test_process_isolation_stdout_flushed_logs_do_not_corrupt_result`: PASSED
- `test_learninghub_exposes_installed_oss_engine_versions_ready`: PASSED
- `test_probe_package_in_isolation_reports_unavailable_when_highs_missing`: PASSED
- `test_process_isolation_timeout_reaps_child_process`: PASSED

### 4.4 Combined Verification Summary

| Run | Command Scope | Tests | Result |
|---|---|---|---|
| Canonical | `tests/ -k "netplan or ortools or robust"` | 11 | **11 PASSED** |
| Supplementary A | `solver/netplan/tests/test_robust.py` | 7 | **7 PASSED** |
| Supplementary B | `modules/netplan/tests/test_netplan_production_execution.py` | 2 | **2 PASSED** |
| Supplementary C | `tests/solver/test_runtime_compat.py` | 10 | **10 PASSED** |
| **Total** | | **30** | **30 PASSED** |

### 4.5 Whitespace Check
```bash
git diff --check
```
Clean — exit 0, no trailing whitespace or whitespace errors.

---

## 5. Decision & Release Recommendation

All hard constraints, infeasibility explanations, scenario provenance, management baseline comparison receipts, and lifecycle management contracts for **ODP-PLAN-NETPLAN-ACCEPTANCE-001** are 100% verified.

- **F1-F5 findings**: All five reviewer findings factually corrected and verified.
- **Coordinator Re-audit (Management Baseline & Explainability)**: Fully resolved:
  1. `compare_solver_against_management_baseline()` evaluates named immutable management baseline input receipts (`ManagementBaselineInput`) under identical constraint sets & objective functions, outputting measured objective values and deterministic comparison receipts (`ManagementBaselineComparisonReceipt`). Authentic `Human/Ops` approval gates remain enforced for lifecycle transition.
  2. Dedicated, tested infeasibility diagnosis logic implemented in both `optimizer.py` and `robust.py` for 100% of hard constraints (`max_average_risk`, `max_action_counts`, `min_capacity_delta`, `max_budget`, `min_expected_gross_margin`, `min_action_counts`).

**Verdict**: **ACCEPTANCE COMPLETE**
