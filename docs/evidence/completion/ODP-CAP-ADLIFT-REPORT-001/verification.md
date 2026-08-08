# Verification Matrix: ODP-CAP-ADLIFT-REPORT-001

## Verification Overview
- **Task ID**: `ODP-CAP-ADLIFT-REPORT-001`
- **Execution Date**: 2026-08-08
- **Environment**: Linux x86_64, CPython 3.12.3, Node.js 20+, Vitest, Pytest

---

## Acceptance Criteria Verification Matrix

| # | Brief Acceptance Criteria | Status | Verification Evidence |
|---|---|---|---|
| **1** | `invalid controls prevent causal claims` | **PASS** | `test_ac1_pre_trend_failure_prevents_causal_claim`, `test_ac1_contamination_prevents_causal_claim`, `test_ac1_missing_control_group_prevents_causal_claim` pass. Pre-trend failure or contamination forces evidence to `L2` (or `L1`), sets `causal_claim_allowed=False`, and forces `recommendation=INCONCLUSIVE`. |
| **2** | `interval and evidence level are visible` | **PASS** | `test_ac2_effect_interval_and_evidence_level_are_visible` passes. `report.effect_interval` includes `point`, `low`, `high`, `standard_error`, `metric`. `evidence_level` (`L3`, `L2`, `L1`, `L0`) is serialized in REST responses and projected in `AdLiftReportCard`. |
| **3** | `continue-stop writes immutable rationale` | **PASS** | `test_ac3_continue_stop_writes_immutable_rationale` passes. Audit event `adlift.incrementality_evaluated.v1` is immutably logged with `correlation_id`, `idempotency_key`, `recommendation`, `iromi`, `evidence_level`. Intervention writeback and label registry entries store complete decision rationale. |
| **4** | `unavailable data fails closed` | **PASS** | `test_ac4_unavailable_data_fails_closed_in_production` & `test_ac4_api_fails_closed_without_tenant_scope_or_durable_store` pass. Production mode (`ODP_REQUIRE_LIVE_DATA=true`) rejects missing controls, missing lineage, statsmodels failure, and missing tenant scope with `AdLiftProductionExecutionError` / HTTP 503 / 403. |
| **5** | `adverse-state tests and evidence are delivered` | **PASS** | `test_ac5_adverse_state_unprofitable_recommends_stop` & `test_ac5_adverse_state_break_even_recommends_continue` pass. Full test suite (28 Python tests, 35 frontend workspace test files / 265 tests) passes 100%. Completion artifacts delivered under `docs/evidence/completion/ODP-CAP-ADLIFT-REPORT-001/`. |

---

## Command Output Verification Records

### 1. Python Acceptance & AdLift Test Suite Execution
```text
Command: .venv/bin/pytest modules/adlift tests/integration/test_adlift_incrementality.py
Result: 28 passed, 1 warning in 20.71s
```

### 2. Dedicated ODP-CAP-ADLIFT-REPORT-001 Acceptance Suite
```text
Command: .venv/bin/pytest modules/adlift/tests/test_odp_cap_adlift_report_001_acceptance.py
Result: 9 passed, 1 warning in 13.75s
```

### 3. Frontend Workspaces Test Suite Execution
```text
Command: npm test --workspaces --if-present
Result: 35 passed (35 files), 265 passed (265 tests) in 41.30s
```
